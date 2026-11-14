"""Planning, merging and Excel output for the one-button morning vessel run."""

from __future__ import annotations
from sea_legs import sea_legs

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from itertools import groupby
from tracking_config import AUTO_TRACKING_DISABLED
from pathlib import Path
from console_rules import booking_class, agent_name, CONSOLE_FILL, COLOAD_FILL

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


BOT_ORDER = ("Evergreen", "ONE Line", "Wan Hai", "COSCO", "MSC", "CMA CGM", "Maersk")
PRIORITY_WATCH_VESSELS = {"WAN HAI 507", "WAN HAI 517"}
COLOAD_MARKERS = ("CO-LOAD", "CO LOAD", "COLOAD", "NVOCC")
CONSOLE_MARKERS = ("CONSOLE", "CONSOLIDATION", "CONSOLIDATED")
VESSEL_BOT_OVERRIDES = {
    "FSL KELANG": "ONE Line",
}
UNTRACKABLE = {"", "TBA", "TBD", "UNKNOWN", "NOT AVAILABLE", "N A"}
COLOMBO_TIMEZONE = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class AutomatedTrackingStats:
    output_path: Path
    source_path: Path
    legs: int
    routed_vessels: int
    matched_legs: int
    action_rows: int
    manual_rows: int
    carriers_run: int
    trico_required: int
    trico_matched: int
    priority_watch_rows: int
    console_watch_rows: int
    coload_watch_rows: int


def canonical(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def voyage_key(value: object) -> str:
    """Normalize carrier rotation prefixes while preserving voyage direction."""
    text = canonical(value)
    suffix = re.search(r"(\d+[NSEW])$", text)
    return suffix.group(1) if suffix else text.replace(" ", "")


def actual_missing(value: object) -> bool:
    return str(value or "").strip().upper() in {"", "TBA", "N/A", "NA", "NONE", "NULL"}


def route_bot(carrier: object, vessel: object) -> str | None:
    """Choose the vessel operator site before the ActiveTrack carrier field.

    ActiveTrack's carrier can be the booking/co-load carrier rather than the
    vessel operator.  Branded vessel names are therefore the stronger routing
    signal (for example WAN HAI 621 can sit under COSCO in ActiveTrack).
    """
    carrier_key = canonical(carrier)
    vessel_key = canonical(vessel)
    if vessel_key in VESSEL_BOT_OVERRIDES:
        return VESSEL_BOT_OVERRIDES[vessel_key]
    if vessel_key.startswith("WAN HAI"):
        return "Wan Hai"
    if vessel_key.startswith("EVER"):
        return "Evergreen"
    if vessel_key.startswith("ONE "):
        return "ONE Line"
    if vessel_key.startswith(("COSCO ", "OOCL ", "CSCL ")):
        return "COSCO"
    if vessel_key.startswith("MSC "):
        return "MSC"
    if vessel_key.startswith("CMA CGM "):
        return "CMA CGM"
    if vessel_key.startswith("MAERSK "):
        return "Maersk"
    if "EVERGREEN" in carrier_key:
        return "Evergreen"
    if carrier_key in {"ONE", "ONE LINE", "OCEAN NETWORK EXPRESS"}:
        return "ONE Line"
    if "WAN HAI" in carrier_key:
        return "Wan Hai"
    if "COSCO" in carrier_key or carrier_key == "OOCL":
        return "COSCO"
    if carrier_key == "MSC" or "MEDITERRANEAN SHIPPING" in carrier_key:
        return "MSC"
    if "CMA CGM" in carrier_key:
        return "CMA CGM"
    if "MAERSK" in carrier_key:
        return "Maersk"
    if vessel_key.startswith("XIN "):
        return "COSCO"
    return None


def schedules_from_source(data: dict) -> list[dict]:
    supplied = data.get("schedules")
    if isinstance(supplied, list) and supplied:
        return [dict(schedule,transshipments=sea_legs(schedule)) for schedule in supplied if isinstance(schedule,dict)]
    schedules: dict[str, dict] = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule")
        if not isinstance(schedule, dict):
            continue
        key = str(schedule.get("id") or schedule.get("reference_number") or "")
        if key and key not in schedules:
            schedules[key] = dict(schedule,transshipments=sea_legs(schedule))
    return list(schedules.values())


def booking_counts(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule") or {}
        key = str(schedule.get("id") or schedule.get("reference_number") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _booking_schedule_key(booking: dict) -> str:
    schedule = (booking or {}).get("vessel_schedule") or {}
    return str(
        booking.get("vessel_schedule_id")
        or booking.get("vessel_schedule_reference_number")
        or schedule.get("id")
        or schedule.get("reference_number")
        or ""
    )


def _unique_text(values) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = canonical(text)
        if not text or key in UNTRACKABLE or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def bookings_by_schedule(data: dict) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for booking in data.get("bookings") or []:
        if not isinstance(booking, dict):
            continue
        key = _booking_schedule_key(booking)
        if key:
            grouped.setdefault(key, []).append(booking)
    return grouped


def _shared_references(bookings: list[dict], field: str) -> list[str]:
    grouped: dict[str, list[str]] = {}
    display: dict[str, str] = {}
    for index, booking in enumerate(bookings):
        value = str(booking.get(field) or "").strip()
        key = canonical(value)
        if not value or key in UNTRACKABLE:
            continue
        booking_ref = str(booking.get("reference_number") or booking.get("id") or index)
        grouped.setdefault(key, []).append(booking_ref)
        display.setdefault(key, value)
    return [display[key] for key, refs in grouped.items() if len(set(refs)) >= 2]


def classify_console_bookings(bookings: list[dict]) -> dict[str, object]:
    """Classify co-load/console evidence without relying on one fragile field.

    ActiveTrack's co-loader field is often blank, so a schedule is kept in the
    watch list when any linked booking is LCL/CFS. Stronger structural evidence
    (shared master reference/container or several LCL bookings) is labelled as
    a console; explicit co-load/NVOCC evidence remains separately visible.
    """
    if not bookings:
        return {
            "is_console_priority": False,
            "priority_type": "",
            "priority_basis": "",
            "console_booking_count": 0,
            "booking_refs": "",
            "mbl_numbers": "",
            "container_numbers": "",
        }

    lcl_bookings = [booking for booking in bookings if canonical(booking.get("shipment_type")) == "LCL"]
    cfs_bookings = [booking for booking in bookings if "CFS" in canonical(booking.get("delivery_type"))]
    explicit_coload: list[str] = []
    explicit_console = False
    for booking in bookings:
        if booking_class(booking) == "CONSOLE":
            explicit_console = True
            continue
        co_loader = str(booking.get("liner_co_loader_name") or "").strip()
        if co_loader:
            explicit_coload.append(co_loader)
        marker_text = " ".join(
            str(booking.get(field) or "")
            for field in (
                "shipment_type", "customer_category", "business_type", "shipment_plan",
                "cargo_type", "rate_source", "remarks", "ts_remarks", "warehouse_remarks",
            )
        ).upper()
        if any(marker in marker_text for marker in COLOAD_MARKERS):
            explicit_coload.append("ActiveTrack co-load marker")
        if any(marker in marker_text for marker in CONSOLE_MARKERS):
            explicit_console = True

    shared_mbls = _shared_references(bookings, "mbl_number")
    shared_containers = _shared_references(bookings, "container_number")
    is_lcl_cfs = bool(lcl_bookings or cfs_bookings)
    is_confirmed_console = bool(explicit_console or shared_mbls or shared_containers)
    is_coload = bool(explicit_coload)
    agent_classes = {booking_class(b) for b in lcl_bookings}
    if agent_classes and "UNCLASSIFIED LCL" not in agent_classes:
        is_confirmed_console = "CONSOLE" in agent_classes
        is_coload = "CO-LOAD" in agent_classes
    grouped_lcl_count = len({
        str(booking.get("reference_number") or booking.get("id") or index)
        for index, booking in enumerate(lcl_bookings or cfs_bookings)
    })
    is_console_candidate = bool(
        not is_confirmed_console and not is_coload and grouped_lcl_count >= 2
    )
    is_coload_candidate = bool(
        is_lcl_cfs and not is_confirmed_console and not is_coload and not is_console_candidate
    )
    is_priority = bool(is_lcl_cfs or is_confirmed_console or is_coload)

    types: list[str] = []
    if is_coload:
        types.append("CO-LOAD")
    if is_confirmed_console:
        types.append("CONSOLE")
    elif is_console_candidate:
        types.append("CONSOLE CANDIDATE")
    elif is_coload_candidate:
        types.append("CO-LOAD / LCL CANDIDATE")

    basis: list[str] = []
    if explicit_coload:
        basis.append("Co-loader: " + ", ".join(_unique_text(explicit_coload)))
    if explicit_console:
        basis.append("Explicit console marker")
        agents = _unique_text(agent_name(b) for b in bookings if booking_class(b) == "CONSOLE")
        if agents:
            basis.append("Console agent rule: " + ", ".join(agents))
    if shared_mbls:
        basis.append("Shared MBL: " + ", ".join(shared_mbls))
    if shared_containers:
        basis.append("Shared container: " + ", ".join(shared_containers))
    if len(lcl_bookings) >= 2:
        basis.append(f"{len(lcl_bookings)} LCL bookings on schedule")
    elif lcl_bookings:
        basis.append("LCL shipment")
    if cfs_bookings and not lcl_bookings:
        basis.append("CFS delivery")

    return {
        "is_console_priority": is_priority,
        "priority_type": " + ".join(types),
        "priority_basis": "; ".join(basis),
        "console_booking_count": len({
            str(booking.get("reference_number") or booking.get("id") or index)
            for index, booking in enumerate(lcl_bookings or cfs_bookings or bookings)
        }) if is_priority else 0,
        "booking_refs": ", ".join(_unique_text(
            booking.get("reference_number") for booking in bookings
        )),
        "mbl_numbers": ", ".join(_unique_text(
            booking.get("mbl_number") for booking in bookings
        )),
        "container_numbers": ", ".join(_unique_text(
            booking.get("container_number") for booking in bookings
        )),
    }


def is_priority_watch_row(row: dict) -> bool:
    return bool(
        row.get("is_console_priority")
        or canonical(row.get("vessel")) in PRIORITY_WATCH_VESSELS
    )


def build_tracking_plan(source_path: Path) -> tuple[list[dict], dict[str, list[str]], dict]:
    """Freeze one source and derive ATA-blank legs plus serial bot groups."""
    data = json.loads(source_path.read_text(encoding="utf-8"))
    counts = booking_counts(data)
    linked_bookings = bookings_by_schedule(data)
    rows: list[dict] = []
    groups: dict[str, list[str]] = {name: [] for name in BOT_ORDER}
    seen_by_bot: dict[str, set[str]] = {name: set() for name in BOT_ORDER}

    for schedule in schedules_from_source(data):
        schedule_key = str(schedule.get("id") or schedule.get("reference_number") or "")
        carrier = (schedule.get("carrier") or {}).get("name") or "UNKNOWN"
        console = classify_console_bookings(linked_bookings.get(schedule_key, []))
        for leg in schedule.get("transshipments") or []:
            if not actual_missing(leg.get("ata")):
                continue
            vessel = (leg.get("vessel") or {}).get("name") or ""
            vessel_key = canonical(vessel)
            bot = None if vessel_key in UNTRACKABLE else route_bot(carrier, vessel)
            disabled = bot in AUTO_TRACKING_DISABLED
            if disabled:
                bot = None
            priority_type = str(console.get("priority_type") or "")
            priority_basis = str(console.get("priority_basis") or "")
            if vessel_key in PRIORITY_WATCH_VESSELS:
                priority_type = " + ".join(filter(None, (priority_type, "KEY CONNECTING VESSEL")))
                priority_basis = "; ".join(filter(None, (priority_basis, f"Priority vessel {vessel_key}")))
            row = {
                "vs_id": schedule.get("reference_number") or "",
                "schedule_id": schedule_key,
                "leg": leg.get("leg_number") or "",
                "carrier": carrier,
                "bot": bot or "MANUAL",
                "manual_reason": "CMA CGM AUTOMATIC TRACKING TEMPORARILY DISABLED" if disabled else "",
                "vessel": vessel or "TBA",
                "voyage": leg.get("voyage") or "",
                "pol": (leg.get("departure_port") or {}).get("name") or "",
                "pod": (leg.get("arrival_port") or {}).get("name") or "",
                "at_etd": leg.get("etd"),
                "at_atd": leg.get("atd"),
                "at_eta": leg.get("eta"),
                "at_ata": leg.get("ata"),
                "booking_count": counts.get(schedule_key, 0),
                **console,
                "priority_type": priority_type,
                "priority_basis": priority_basis,
            }
            rows.append(row)
            if bot and vessel_key not in seen_by_bot[bot]:
                seen_by_bot[bot].add(vessel_key)
                groups[bot].append(vessel)

    rows.sort(key=lambda item: (item.get("at_eta") or "9999-12-31", item.get("at_etd") or "9999-12-31", item["vs_id"], str(item["leg"])))
    return rows, groups, data


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _change(label: str, active: object, latest: object) -> str | None:
    active_date, latest_date = _parse_date(active), _parse_date(latest)
    if not active_date or not latest_date or active_date == latest_date:
        return None
    days = abs((latest_date - active_date).days)
    direction = "DELAYED" if latest_date > active_date else "ADVANCED"
    return f"{label} {direction} {days} DAY" + ("S" if days != 1 else "")


def trico_48h_rows(plan_rows: list[dict], now: datetime | None = None) -> list[dict]:
    """Return ATA-blank Colombo arrivals whose ActiveTrack ETA is within 48h.

    ActiveTrack normally supplies date-only values.  For those values, include
    every calendar date touched by the next 48 hours so an early-morning call
    is not missed simply because its time is absent.
    """
    local_now = now or datetime.now(COLOMBO_TIMEZONE)
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=COLOMBO_TIMEZONE)
    deadline = local_now + timedelta(hours=48)
    selected = []
    for row in plan_rows:
        if not actual_missing(row.get("at_ata")) or "COLOMBO" not in canonical(row.get("pod")):
            continue
        raw_eta = str(row.get("at_eta") or "").strip()
        eta_date = _parse_date(raw_eta)
        if not eta_date:
            continue
        exact_time = "T" in raw_eta or bool(re.search(r"\d{1,2}:\d{2}", raw_eta))
        if exact_time:
            try:
                eta_time = datetime.fromisoformat(raw_eta.replace("Z", "+00:00"))
                if eta_time.tzinfo is None:
                    eta_time = eta_time.replace(tzinfo=local_now.tzinfo)
                eta_time = eta_time.astimezone(local_now.tzinfo)
            except ValueError:
                continue
            eligible = local_now <= eta_time <= deadline
        else:
            eligible = local_now.date() <= eta_date <= deadline.date()
        if eligible:
            selected.append(row)
    return selected


def _append_result(current: object, addition: str) -> str:
    text = str(current or "").strip()
    if not text or text == "NO CHANGE":
        return addition
    return f"{text}; {addition}"


def merge_trico_results(rows: list[dict], result_dir: Path | None, now: datetime | None = None) -> list[dict]:
    """Attach one Trico page read to eligible rows without treating ETB as ETA/ATA."""
    eligible = {
        (canonical(row.get("vs_id")), str(row.get("leg") or ""))
        for row in trico_48h_rows(rows, now)
    }
    calls = _read_csv(result_dir / "trico_calls.csv") if result_dir else []
    run_status = "NOT RUN"
    status_path = result_dir / "results.json" if result_dir else None
    if status_path and status_path.exists():
        try:
            statuses = json.loads(status_path.read_text(encoding="utf-8"))
            values = [str(item.get("status") or "") for item in statuses if isinstance(item, dict)]
            if values:
                run_status = values[0]
        except (json.JSONDecodeError, OSError):
            run_status = "RESULT UNREADABLE"

    output = []
    for source_row in rows:
        row = {**source_row}
        key = (canonical(row.get("vs_id")), str(row.get("leg") or ""))
        row.update({
            "trico_required": "YES" if key in eligible else "",
            "trico_etb": "", "trico_etb_time": "", "trico_terminal": "",
            "trico_updated": "", "trico_status": "", "trico_source_url": "",
        })
        if key not in eligible:
            output.append(row)
            continue

        exact = [
            call for call in calls
            if canonical(call.get("vessel")) == canonical(row.get("vessel"))
            and canonical(call.get("voyage")) == canonical(row.get("voyage"))
        ]
        if len(exact) == 1:
            call = exact[0]
            row.update({
                "trico_etb": call.get("etb") or "",
                "trico_etb_time": call.get("etb_time") or "",
                "trico_terminal": call.get("terminal") or "",
                "trico_updated": call.get("source_updated_at") or "",
                "trico_status": "MATCHED",
                "trico_source_url": call.get("source_url") or "",
            })
            active_eta, etb = _parse_date(row.get("at_eta")), _parse_date(row.get("trico_etb"))
            if active_eta and etb and active_eta != etb:
                days = abs((etb - active_eta).days)
                direction = "AFTER" if etb > active_eta else "BEFORE"
                row["result"] = _append_result(
                    row.get("result"),
                    f"TRICO ETB {days} DAY" + ("S" if days != 1 else "") + f" {direction} ACTIVETRACK ETA",
                )
        elif len(exact) > 1:
            row["trico_status"] = "AMBIGUOUS MULTIPLE MATCHES"
            row["result"] = _append_result(row.get("result"), "MANUAL TRICO 48H CHECK - MULTIPLE MATCHES")
        else:
            row["trico_status"] = "NOT LISTED" if run_status == "OK" else run_status
            reason = "NOT LISTED" if run_status == "OK" else run_status
            row["result"] = _append_result(row.get("result"), f"MANUAL TRICO 48H CHECK - {reason}")
        output.append(row)
    return output


def merge_results(plan_rows: list[dict], result_dirs: dict[str, Path]) -> list[dict]:
    matches: dict[tuple[str, str], dict] = {}
    statuses: dict[tuple[str, str], str] = {}
    for bot, result_dir in result_dirs.items():
        for row in _read_csv(result_dir / "matched_legs.csv"):
            key = (canonical(row.get("vs_id")), str(row.get("leg") or ""))
            current = matches.get(key)
            fields = ("carrier_etd", "carrier_atd", "carrier_eta", "carrier_ata")
            if current is None or sum(bool(row.get(field)) for field in fields) > sum(bool(current.get(field)) for field in fields):
                matches[key] = {**row, "matched_bot": bot}
        status_path = result_dir / "results.json"
        if status_path.exists():
            for item in json.loads(status_path.read_text(encoding="utf-8")):
                if isinstance(item, dict) and item.get("vessel"):
                    statuses[(bot, canonical(item["vessel"]))] = str(item.get("status") or "NO CARRIER DATA")

    grouped_departures: dict[tuple[str, str, str], list[dict]] = {}
    grouped_arrivals: dict[tuple[str, str, str], list[dict]] = {}
    for row in matches.values():
        identity = (
            canonical(row.get("vessel")),
            voyage_key(row.get("voyage") or row.get("carrier_voyage")),
        )
        departure_key = (*identity, canonical(row.get("pol")))
        arrival_key = (*identity, canonical(row.get("pod")))
        if all(departure_key):
            grouped_departures.setdefault(departure_key, []).append(row)
        if all(arrival_key):
            grouped_arrivals.setdefault(arrival_key, []).append(row)

    def grouped_date(item: dict, field: str, active_field: str) -> tuple[object, dict | None]:
        """Find the closest verified date from the same physical voyage group."""
        port_field = "pol" if field == "carrier_etd" else "pod"
        groups = grouped_departures if field == "carrier_etd" else grouped_arrivals
        key = (
            canonical(item.get("vessel")),
            voyage_key(item.get("voyage")),
            canonical(item.get(port_field)),
        )
        candidates = [(row, _parse_date(row.get(field))) for row in groups.get(key, [])]
        candidates = [(row, value) for row, value in candidates if value]
        if not candidates:
            return "", None
        active_date = _parse_date(item.get(active_field))
        if active_date:
            row, value = min(candidates, key=lambda pair: abs((pair[1] - active_date).days))
            return value.isoformat(), row
        row, value = candidates[0]
        return value.isoformat(), row

    output = []
    for item in plan_rows:
        exact_match = matches.get((canonical(item["vs_id"]), str(item["leg"])))
        match = dict(exact_match or {})
        grouped_fallback = False
        for carrier_field, active_field in (("carrier_etd", "at_etd"), ("carrier_eta", "at_eta")):
            if match.get(carrier_field):
                continue
            value, source_row = grouped_date(item, carrier_field, active_field)
            if not value:
                continue
            match[carrier_field] = value
            grouped_fallback = True
            if source_row:
                match["carrier_voyage"] = match.get("carrier_voyage") or source_row.get("carrier_voyage") or ""
                match["source_url"] = match.get("source_url") or source_row.get("source_url") or ""
                match["matched_bot"] = match.get("matched_bot") or source_row.get("matched_bot") or ""
        merged = {
            **item,
            # The planned route can differ from the site that actually supplied
            # a verified call.  Keep the routing value in ``bot`` for coverage
            # metrics and display the evidence-producing site separately.
            "tracking_site": (match or {}).get("matched_bot") or item["bot"],
            "carrier_voyage": (match or {}).get("carrier_voyage") or "",
            "carrier_etd": (match or {}).get("carrier_etd") or "",
            "carrier_atd": (match or {}).get("carrier_atd") or "",
            "carrier_eta": (match or {}).get("carrier_eta") or "",
            "carrier_ata": (match or {}).get("carrier_ata") or "",
            "source_url": (match or {}).get("source_url") or "",
            "voyage_grouped": grouped_fallback,
        }
        actions: list[str] = []
        if actual_missing(item.get("at_atd")) and merged["carrier_atd"]:
            actions.append("RECORD ATD")
        if actual_missing(item.get("at_ata")) and merged["carrier_ata"]:
            actions.append("RECORD ATA")
        if actual_missing(item.get("at_atd")):
            etd_change = _change("ETD", item.get("at_etd"), merged["carrier_etd"])
            if etd_change:
                actions.append(etd_change)
        if actual_missing(item.get("at_ata")):
            eta_change = _change("ETA", item.get("at_eta"), merged["carrier_eta"])
            if eta_change:
                actions.append(eta_change)

        if actions:
            result = "; ".join(actions)
        elif match:
            result = "NO CHANGE"
        elif item["bot"] == "MANUAL":
            result = "MANUAL CHECK - " + (item.get("manual_reason") or "UNSUPPORTED CARRIER")
        else:
            status = statuses.get((item["bot"], canonical(item["vessel"])), "NO CARRIER DATA")
            result = "NO MATCHED CARRIER DATES" if status == "OK" else f"MANUAL CHECK - {status}"
        merged["result"] = result
        merged["matched"] = bool(match)
        output.append(merged)
    return output


HEADERS = (
    ("vs_id", "VESSEL ID"), ("vessel", "VESSEL"), ("leg", "LEG"),
    ("carrier", "ACTIVETRACK CARRIER"), ("tracking_site", "TRACKING SITE"),
    ("voyage", "VOYAGE"), ("pol", "POL"), ("pod", "POD"),
    ("at_etd", "ACTIVETRACK ETD"), ("carrier_etd", "LATEST ETD"),
    ("at_atd", "ACTIVETRACK ATD"), ("carrier_atd", "CARRIER ATD"),
    ("at_eta", "ACTIVETRACK ETA"), ("carrier_eta", "LATEST ETA"),
    ("trico_required", "48H TRICO"), ("trico_etb", "TRICO ETB"),
    ("trico_etb_time", "ETB TIME"), ("trico_terminal", "TERMINAL"),
    ("trico_updated", "TRICO LAST UPDATED"), ("trico_status", "TRICO CHECK"),
    ("at_ata", "ACTIVETRACK ATA"), ("carrier_ata", "CARRIER ATA"),
    ("booking_count", "BOOKINGS"), ("priority_type", "CONSOLE / CO-LOAD"),
    ("priority_basis", "IDENTIFICATION BASIS"), ("console_booking_count", "CONSOLE BKS"),
    ("booking_refs", "BOOKING REFERENCES"), ("mbl_numbers", "MBL(S)"),
    ("container_numbers", "CONTAINER(S)"), ("result", "ACTION / RESULT"),
    ("source_url", "CARRIER SOURCE"), ("trico_source_url", "TRICO SOURCE"),
)

DATE_KEYS = {"at_etd", "carrier_etd", "at_atd", "carrier_atd", "at_eta", "carrier_eta", "trico_etb", "at_ata", "carrier_ata"}
COLUMN_WIDTHS = [13, 23, 6, 18, 15, 13, 24, 24, 16, 16, 16, 16, 16, 16, 10, 16, 11, 14, 22, 22, 16, 16, 10, 22, 42, 12, 42, 30, 30, 35, 15, 15]
HEADER_INDEX = {key: index for index, (key, _label) in enumerate(HEADERS, 1)}


def _write_result_row(sheet, row_number: int, item: dict, fill_color: str) -> None:
    red, green, blue = "C00000", "008000", "1F4E78"
    priority = str(item.get("priority_type") or "")
    if "CONSOLE" in priority and "CANDIDATE" not in priority:
        fill_color = CONSOLE_FILL
    elif "CO-LOAD" in priority:
        fill_color = COLOAD_FILL
    fill = PatternFill("solid", fgColor=fill_color)
    for column, (key, _label) in enumerate(HEADERS, 1):
        value = item.get(key)
        if key == "tracking_site" and not value:
            value = item.get("bot")
        if key in DATE_KEYS:
            value = _parse_date(value)
        cell = sheet.cell(row_number, column, value)
        cell.fill = fill
        cell.border = Border(bottom=Side(style="hair", color="D9E1E8"))
        cell.alignment = Alignment(
            vertical="center",
            wrap_text=key in {
                "pol", "pod", "result", "trico_status", "trico_updated",
                "priority_type", "priority_basis", "booking_refs", "mbl_numbers", "container_numbers",
            },
        )
        if key in DATE_KEYS and value:
            cell.number_format = "dd mmm yyyy"
        if key in {"source_url", "trico_source_url"} and value:
            cell.hyperlink = value
            cell.value = "Open source"
            cell.font = Font(color=blue, underline="single")

    result = str(item.get("result") or "")
    result_cell = sheet.cell(row_number, HEADER_INDEX["result"])
    if "DELAYED" in result:
        result_cell.font = Font(color=red, bold=True)
    elif "ADVANCED" in result:
        result_cell.font = Font(color=green, bold=True)
    elif "RECORD" in result:
        result_cell.font = Font(color=blue, bold=True)
    elif "MANUAL" in result or result.startswith("NO MATCHED"):
        result_cell.font = Font(color=red, bold=True)
    elif "TRICO ETB" in result:
        result_cell.font = Font(color="7030A0", bold=True)

    for active_column, latest_column in (
        (HEADER_INDEX["at_etd"], HEADER_INDEX["carrier_etd"]),
        (HEADER_INDEX["at_eta"], HEADER_INDEX["carrier_eta"]),
    ):
        active_value = sheet.cell(row_number, active_column).value
        latest_value = sheet.cell(row_number, latest_column).value
        if isinstance(active_value, date) and isinstance(latest_value, date) and active_value != latest_value:
            sheet.cell(row_number, latest_column).font = Font(color=red if latest_value > active_value else green, bold=True)
    for actual_column in (HEADER_INDEX["carrier_atd"], HEADER_INDEX["carrier_ata"]):
        if sheet.cell(row_number, actual_column).value:
            sheet.cell(row_number, actual_column).font = Font(color=blue, bold=True)
    trico_status = str(item.get("trico_status") or "")
    if trico_status:
        sheet.cell(row_number, HEADER_INDEX["trico_status"]).font = Font(
            color="008000" if trico_status == "MATCHED" else red,
            bold=True,
        )
    if item.get("trico_etb") and _parse_date(item.get("trico_etb")) != _parse_date(item.get("at_eta")):
        sheet.cell(row_number, HEADER_INDEX["trico_etb"]).font = Font(color="7030A0", bold=True)


def _write_sheet(sheet, title: str, rows: list[dict], summary: str) -> None:
    navy, pale, white = "173A5E", "E8F2F4", "FFFFFF"
    thin = Side(style="thin", color="C8D2DA")
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADERS))
    sheet.cell(1, 1, title).fill = PatternFill("solid", fgColor=navy)
    sheet.cell(1, 1).font = Font(color=white, bold=True, size=16)
    sheet.cell(1, 1).alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(HEADERS))
    sheet.cell(2, 1, summary).fill = PatternFill("solid", fgColor=pale)
    sheet.cell(2, 1).font = Font(color=navy, bold=True, size=9)

    header_row = 4
    for column, (_key, label) in enumerate(HEADERS, 1):
        cell = sheet.cell(header_row, column, label)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(color=white, bold=True, size=9)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = Border(bottom=thin)

    for row_number, item in enumerate(rows, header_row + 1):
        _write_result_row(sheet, row_number, item, "F4F8FA" if row_number % 2 else white)

    for column, width in enumerate(COLUMN_WIDTHS, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    last_column = get_column_letter(len(HEADERS))
    sheet.auto_filter.ref = f"A{header_row}:{last_column}{max(header_row, header_row + len(rows))}"
    # Keep the two operational identifiers visible during horizontal review.
    # Vessel ID and Vessel Name are deliberately columns A:B so only a compact
    # identity pane is locked instead of the five wide legacy columns.
    sheet.freeze_panes = f"C{header_row + 1}"
    sheet.sheet_view.showGridLines = False
    sheet.row_dimensions[header_row].height = 32


def _write_manual_tracking_sheet(sheet, rows: list[dict], summary: str) -> None:
    """List every unmatched leg under a visible carrier heading."""
    navy, purple, pale, white = "173A5E", "6D28D9", "F2ECFF", "FFFFFF"
    sheet.merge_cells(start_row=1, start_column=1, end_row=1, end_column=len(HEADERS))
    sheet.cell(1, 1, "Manual Tracking - Grouped by Carrier").fill = PatternFill("solid", fgColor=purple)
    sheet.cell(1, 1).font = Font(color=white, bold=True, size=16)
    sheet.cell(1, 1).alignment = Alignment(vertical="center")
    sheet.row_dimensions[1].height = 28
    sheet.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(HEADERS))
    sheet.cell(2, 1, summary).fill = PatternFill("solid", fgColor=pale)
    sheet.cell(2, 1).font = Font(color=purple, bold=True, size=9)

    ordered = sorted(
        rows,
        key=lambda item: (
            canonical(item.get("carrier")) or "UNKNOWN",
            canonical(item.get("vessel")),
            canonical(item.get("voyage")),
            canonical(item.get("vs_id")),
            str(item.get("leg") or ""),
        ),
    )
    cursor = 4
    if not ordered:
        sheet.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=len(HEADERS))
        sheet.cell(cursor, 1, "All vessel schedules were matched by the available carrier bots.")
        sheet.cell(cursor, 1).font = Font(color="008000", bold=True)
    else:
        for carrier_key, grouped_items in groupby(ordered, key=lambda item: canonical(item.get("carrier")) or "UNKNOWN"):
            carrier_rows = list(grouped_items)
            display_carrier = str(carrier_rows[0].get("carrier") or "UNKNOWN")
            vessel_count = len({canonical(item.get("vessel")) for item in carrier_rows})
            sheet.merge_cells(start_row=cursor, start_column=1, end_row=cursor, end_column=len(HEADERS))
            heading = sheet.cell(
                cursor,
                1,
                f"{display_carrier}  |  {vessel_count} vessel{'s' if vessel_count != 1 else ''}  |  {len(carrier_rows)} schedule leg{'s' if len(carrier_rows) != 1 else ''}",
            )
            heading.fill = PatternFill("solid", fgColor=purple)
            heading.font = Font(color=white, bold=True, size=11)
            heading.alignment = Alignment(vertical="center")
            sheet.row_dimensions[cursor].height = 23
            cursor += 1

            for column, (_key, label) in enumerate(HEADERS, 1):
                cell = sheet.cell(cursor, column, label)
                cell.fill = PatternFill("solid", fgColor=navy)
                cell.font = Font(color=white, bold=True, size=9)
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            sheet.row_dimensions[cursor].height = 30
            cursor += 1

            first_data_row = cursor
            for index, item in enumerate(carrier_rows):
                _write_result_row(sheet, cursor, item, "F8F5FF" if index % 2 == 0 else white)
                cursor += 1
            for row_number in range(first_data_row, cursor):
                sheet.row_dimensions[row_number].outlineLevel = 1
            cursor += 1

    for column, width in enumerate(COLUMN_WIDTHS, 1):
        sheet.column_dimensions[get_column_letter(column)].width = width
    sheet.freeze_panes = "C4"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.outlinePr.summaryBelow = False


def build_automated_report(source_path: Path, rows: list[dict], output_path: Path, result_dirs: dict[str, Path]) -> AutomatedTrackingStats:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    actions = [row for row in rows if row["result"] != "NO CHANGE"]
    priority_watch = [row for row in rows if is_priority_watch_row(row)]
    console_watch = [row for row in priority_watch if row.get("is_console_priority")]
    coload_watch = [row for row in priority_watch if "CO-LOAD" in str(row.get("priority_type") or "")]
    # Any leg without a successful carrier-site match belongs in the manual
    # queue, including unsupported carriers, bot failures and no-date matches.
    manual = [
        row for row in rows
        if not row.get("matched") or (row.get("trico_required") == "YES" and row.get("trico_status") != "MATCHED")
    ]
    unchanged = [row for row in rows if row["result"] == "NO CHANGE"]
    routed = {(row["bot"], canonical(row["vessel"])) for row in rows if row["bot"] != "MANUAL"}
    routed_legs = sum(row["bot"] != "MANUAL" for row in rows)
    matched = sum(bool(row.get("matched")) for row in rows)
    trico_required = sum(row.get("trico_required") == "YES" for row in rows)
    trico_matched = sum(row.get("trico_status") == "MATCHED" for row in rows)
    verified_coverage = (matched / len(rows) * 100) if rows else 0.0
    routed_coverage = (routed_legs / len(rows) * 100) if rows else 0.0
    summary = (
        f"Source locked: {source_path.name}  |  Legs: {len(rows)}  |  "
        f"Verified: {matched} ({verified_coverage:.1f}%)  |  "
        f"Routed: {routed_legs} ({routed_coverage:.1f}%)  |  "
        f"Trico 48h: {trico_matched}/{trico_required}  |  "
        f"Actions: {len(actions)}  |  Priority watch: {len(priority_watch)}  |  "
        f"Console/co-load: {len(console_watch)}  |  "
        f"Manual/no data: {len(manual)}"
    )

    workbook = Workbook()
    action_sheet = workbook.active
    action_sheet.title = "ACTION REQUIRED"
    _write_sheet(action_sheet, "Automated Daily Vessel Tracking - Action Required", actions, summary)
    priority_sheet = workbook.create_sheet("PRIORITY WATCH")
    _write_sheet(
        priority_sheet,
        "Priority Watch - Console, Co-load and Key Connecting Vessels",
        priority_watch,
        summary,
    )
    all_sheet = workbook.create_sheet("ALL RESULTS")
    _write_sheet(all_sheet, "Automated Daily Vessel Tracking - All Results", rows, summary)
    manual_sheet = workbook.create_sheet("MANUAL TRACKING")
    _write_manual_tracking_sheet(manual_sheet, manual, summary)
    unchanged_sheet = workbook.create_sheet("NO CHANGE")
    _write_sheet(unchanged_sheet, "Matched Schedules with No Date Change", unchanged, summary)

    info = workbook.create_sheet("RUN INFO")
    info.append(["AUTOMATED DAILY VESSEL TRACKING", ""])
    info.append(["Source report", str(source_path)])
    info.append(["Generated", datetime.now().isoformat(timespec="seconds")])
    info.append(["ATA-blank legs", len(rows)])
    info.append(["Routed vessel/site pairs", len(routed)])
    info.append(["Routed schedule legs", routed_legs])
    info.append(["Routed coverage", f"{routed_coverage:.1f}%"])
    info.append(["Matched legs", matched])
    info.append(["Verified match coverage", f"{verified_coverage:.1f}%"])
    info.append(["Action rows", len(actions)])
    info.append(["Priority watch rows", len(priority_watch)])
    info.append(["Console/co-load watch rows", len(console_watch)])
    info.append(["Explicit co-load watch rows", len(coload_watch)])
    info.append(["Priority identification", "Explicit co-loader/markers; shared MBL/container; multiple LCL bookings; LCL or CFS shipment; WAN HAI 507/517 key connection."])
    info.append(["Manual/no data rows", len(manual)])
    info.append(["Trico 48h checks required", trico_required])
    info.append(["Trico exact vessel/voyage matches", trico_matched])
    info.append(["Trico rule", "ETB is terminal planning evidence only; it is never recorded as ETA or ATA."])
    info.append(["Carrier sites completed", ", ".join(result_dirs) or "None"])
    info.column_dimensions["A"].width = 30
    info.column_dimensions["B"].width = 105
    for cell in info[1]:
        cell.fill = PatternFill("solid", fgColor="173A5E")
        cell.font = Font(color="FFFFFF", bold=True, size=14)
    for row in info.iter_rows(min_row=2, max_col=1):
        row[0].font = Font(bold=True, color="173A5E")
    info.sheet_view.showGridLines = False

    from fcl_tracking import extract_fcl,load_state,add_fcl_sheet,arrived_before_today
    snapshot=json.loads(source_path.read_text(encoding='utf-8-sig'))
    records=[r for r in extract_fcl(snapshot) if not arrived_before_today(r)]
    saved={r['key']:r for r in load_state().get('containers',[]) if r.get('source_path')==str(source_path) and r.get('source_mtime')==source_path.stat().st_mtime}
    fcl_rows=[dict(r,**{k:v for k,v in saved.get(r['key'],{}).items() if k in ('status','departure','reason','checked_at','checks')}) for r in records]
    for r in fcl_rows:
        if not r.get('status'):r.update(status='N/A',reason='Not checked against this capture')
    add_fcl_sheet(workbook,fcl_rows)
    from container_history import add_history_sheet
    add_history_sheet(workbook,snapshot)
    workbook.save(output_path)
    return AutomatedTrackingStats(
        output_path=output_path,
        source_path=source_path,
        legs=len(rows),
        routed_vessels=len(routed),
        matched_legs=matched,
        action_rows=len(actions),
        manual_rows=len(manual),
        carriers_run=len(result_dirs),
        trico_required=trico_required,
        trico_matched=trico_matched,
        priority_watch_rows=len(priority_watch),
        console_watch_rows=len(console_watch),
        coload_watch_rows=len(coload_watch),
    )
