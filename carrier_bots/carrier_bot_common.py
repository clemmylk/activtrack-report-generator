"""Shared output and ActiveTrack comparison helpers for carrier schedule bots."""

from __future__ import annotations

import csv
import json
import os
import re
import time
from datetime import date, datetime
from pathlib import Path


ACTIVTRACK_REPORTS = (Path.home() / "Documents" / "ActivTrack Reports")

HUMAN_VERIFICATION_SECONDS = {"CMA CGM": 60, "HAPAG-LLOYD": 60, "HAPAG": 60}


def wait_for_human_verification(driver, carrier: str) -> None:
    """Allow a person to verify the visible browser once per browser session."""
    seconds = HUMAN_VERIFICATION_SECONDS.get(carrier.strip().upper(), 0)
    if not seconds or getattr(driver, "_globactiv_human_verification_waited", False):
        return
    print(f"{carrier}: complete human verification in Edge. Waiting {seconds} seconds.", flush=True)
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    driver._globactiv_human_verification_waited = True
    print(f"{carrier}: verification wait finished; continuing tracking.", flush=True)


def normal(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().upper()


def compact(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", normal(value))


def port_key(value: object) -> str:
    """Normalize the spelling differences used by carrier and ActiveTrack ports."""
    text = normal(value)
    aliases = (
        (("PORT KELANG", "PORT KLANG", "WESTPORT"), "PORT KLANG"),
        (("TIANJINXINGANG", "TIANJIN XINGANG", "TIANJIN"), "TIANJIN"),
        (("LONDON GATEWAY",), "LONDON GATEWAY"),
        (("SOUTHAMPTON",), "SOUTHAMPTON"),
        (("FELIXSTOWE",), "FELIXSTOWE"),
        (("LE HAVRE",), "LE HAVRE"),
        (("ANTWERP",), "ANTWERP"),
        (("ALGECIRAS",), "ALGECIRAS"),
        (("JEBEL ALI",), "JEBEL ALI"),
        (("LAEM CHABANG",), "LAEM CHABANG"),
        (("HO CHI MINH", "CAT LAI"), "HO CHI MINH"),
        (("CAI MEP", "VUNG TAU"), "VUNG TAU"),
        (("COLOMBO",), "COLOMBO"),
        (("SINGAPORE",), "SINGAPORE"),
        (("SHANGHAI",), "SHANGHAI"),
        (("NINGBO",), "NINGBO"),
        (("SHEKOU",), "SHEKOU"),
        (("SHENZHEN",), "SHENZHEN"),
        (("QINGDAO",), "QINGDAO"),
        (("DALIAN",), "DALIAN"),
        (("XIAMEN",), "XIAMEN"),
        (("JAKARTA",), "JAKARTA"),
        (("MUNDRA",), "MUNDRA"),
        (("KARACHI",), "KARACHI"),
        (("PIRAEUS",), "PIRAEUS"),
        (("HAMBURG",), "HAMBURG"),
        (("ROTTERDAM",), "ROTTERDAM"),
        (("JEDDAH",), "JEDDAH"),
    )
    for tokens, key in aliases:
        if any(token in text for token in tokens):
            return key
    return re.sub(r",.*$", "", text).strip()


def voyage_aliases(value: object) -> set[str]:
    text = normal(value)
    if not text:
        return set()
    aliases = {compact(text)}
    aliases.update(compact(part) for part in re.split(r"[/,]", text) if compact(part))
    return aliases


def requested_vessels(root: Path) -> list[str]:
    input_path = root / "vessels.txt"
    override = os.environ.get("TRACKING_VESSELS")
    if not override and not input_path.exists():
        raise SystemExit("Missing vessels.txt")
    lines = override.splitlines() if override else input_path.read_text(encoding="utf-8-sig").splitlines()
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        vessel = normal(line)
        if vessel and not vessel.startswith("#") and vessel not in seen:
            seen.add(vessel)
            output.append(vessel)
    if not output:
        raise SystemExit("Add at least one vessel name to vessels.txt")
    return output


def latest_report() -> Path | None:
    override = Path(os.environ["ACTIVTRACK_SOURCE_FILE"]) if os.environ.get("ACTIVTRACK_SOURCE_FILE") else None
    if override and override.is_file():
        return override
    reports = list(ACTIVTRACK_REPORTS.glob("ActivTrack_Vessel_Booking_Report_*_Source.json"))
    if not reports:
        reports = list(ACTIVTRACK_REPORTS.glob("ActivTrack_All_Vessels_*_Source.json"))
    return max(reports, key=lambda item: item.stat().st_mtime) if reports else None


def schedules_from_source(data: dict) -> list[dict]:
    supplied = data.get("schedules")
    if isinstance(supplied, list) and supplied:
        return supplied
    schedules: dict[str, dict] = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule")
        if not schedule:
            continue
        key = str(schedule.get("id") or schedule.get("reference_number") or "")
        if key and key not in schedules:
            schedules[key] = schedule
    return list(schedules.values())


def _nearest(rows: list[dict], target: str | None, fields: tuple[str, ...]) -> tuple[dict, str] | None:
    candidates: list[tuple[dict, str]] = []
    for row in rows:
        value = next((row.get(field) for field in fields if row.get(field)), None)
        if value:
            candidates.append((row, value))
    if not candidates:
        return None
    if not target:
        return candidates[0] if len(candidates) == 1 else None
    target_date = datetime.fromisoformat(target).date()
    row, value = min(candidates, key=lambda item: abs((date.fromisoformat(item[1]) - target_date).days))
    return (row, value) if abs((date.fromisoformat(value) - target_date).days) <= 120 else None


def compare(rows: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    source = latest_report()
    if not source:
        return [], [], None
    source_data = json.loads(source.read_text(encoding="utf-8-sig"))
    matched: list[dict] = []
    updates: list[dict] = []
    for schedule in schedules_from_source(source_data):
        for leg in schedule.get("transshipments") or []:
            vessel = normal((leg.get("vessel") or {}).get("name"))
            voyage = compact(leg.get("voyage"))
            exact = [
                row for row in rows
                if row.get("carrier_vessel") == vessel and voyage in voyage_aliases(row.get("carrier_voyage"))
            ]
            if not exact:
                continue
            departure_rows = [
                row for row in exact
                if port_key(row.get("port")) == port_key((leg.get("departure_port") or {}).get("name"))
            ]
            arrival_rows = [
                row for row in exact
                if port_key(row.get("port")) == port_key((leg.get("arrival_port") or {}).get("name"))
            ]
            departure = _nearest(departure_rows, leg.get("etd"), ("carrier_atd", "carrier_etd"))
            arrival = _nearest(arrival_rows, leg.get("eta"), ("carrier_ata", "carrier_eta"))
            if not departure and not arrival:
                continue
            source_row = (departure or arrival)[0]
            review = {
                "vs_id": schedule.get("reference_number"),
                "leg": leg.get("leg_number"),
                "vessel": vessel,
                "voyage": leg.get("voyage"),
                "carrier_voyage": source_row.get("carrier_voyage"),
                "pol": (leg.get("departure_port") or {}).get("name"),
                "pod": (leg.get("arrival_port") or {}).get("name"),
                "at_etd": leg.get("etd"),
                "carrier_etd": departure[0].get("carrier_etd") if departure else None,
                "at_atd": leg.get("atd"),
                "carrier_atd": departure[0].get("carrier_atd") if departure else None,
                "at_eta": leg.get("eta"),
                "carrier_eta": arrival[0].get("carrier_eta") if arrival else None,
                "at_ata": leg.get("ata"),
                "carrier_ata": arrival[0].get("carrier_ata") if arrival else None,
                "source_url": source_row.get("source_url"),
            }
            matched.append(review)
            if (
                departure and review["carrier_etd"] and not leg.get("atd")
                and review["at_etd"] != review["carrier_etd"]
            ) or (
                arrival and review["carrier_eta"] and not leg.get("ata")
                and review["at_eta"] != review["carrier_eta"]
            ):
                updates.append(review)
    return updates, matched, str(source)


TIMETABLE_COLUMNS = [
    "carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_ata",
    "carrier_etd", "carrier_atd", "source_url",
]
COMPARE_COLUMNS = [
    "vs_id", "leg", "vessel", "voyage", "carrier_voyage", "pol", "pod",
    "at_etd", "carrier_etd", "at_atd", "carrier_atd", "at_eta", "carrier_eta",
    "at_ata", "carrier_ata", "source_url",
]


def _save_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def save_outputs(out: Path, statuses: list[dict], rows: list[dict]) -> tuple[int, int]:
    updates, matched, source = compare(rows)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(statuses, indent=2), encoding="utf-8")
    _save_csv(out / "timetable.csv", rows, TIMETABLE_COLUMNS)
    _save_csv(out / "matched_legs.csv", matched, COMPARE_COLUMNS)
    _save_csv(out / "updates_only.csv", updates, COMPARE_COLUMNS)
    (out / "comparison.json").write_text(
        json.dumps({"activtrack_source": source, "updates_only": updates, "matched_legs": matched}, indent=2),
        encoding="utf-8",
    )
    return len(matched), len(updates)
