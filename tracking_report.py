"""Build carrier-vs-ActivTrack vessel tracking comparison workbooks."""

from __future__ import annotations
from sea_legs import sea_legs

import csv
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo


REPORTS_DIR = Path.home() / "Documents" / "ActivTrack Reports"
SOURCE_PATTERN = "ActivTrack_Vessel_Booking_Report_*_Source.json"
UNTRACKABLE_VESSEL_NAMES = {"TBA", "TBD", "UNKNOWN", "NOT AVAILABLE", "N A"}


@dataclass(frozen=True)
class TrackingReportStats:
    output_path: Path
    carrier: str
    requested_vessels: int
    rows: int
    delayed_rows: int
    advanced_rows: int
    no_data_rows: int
    source_path: Path


def canonical(value: object) -> str:
    """Return a stable comparison key for vessel names and identifiers."""

    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def parse_vessel_names(text: str) -> list[str]:
    """Read a pasted vessel list and remove repeated names case-insensitively."""

    names: list[str] = []
    seen: set[str] = set()
    for token in re.split(r"[\r\n,;|\t]+", text):
        name = re.sub(r"^\s*(?:[-*\u2022]+|\d+[.)])\s*", "", token).strip()
        key = canonical(name)
        if name and not name.startswith("#") and key and key not in seen:
            seen.add(key)
            names.append(name)
    return names


def latest_booking_source(reports_dir: Path = REPORTS_DIR) -> Path:
    """Return the newest booking-source JSON created by the report app."""

    candidates = [path for path in reports_dir.glob(SOURCE_PATTERN) if path.is_file()]
    if not candidates:
        raise FileNotFoundError(
            f"No {SOURCE_PATTERN} file was found in {reports_dir}. "
            "Generate the booking report first."
        )
    return max(candidates, key=lambda path: path.stat().st_mtime)


def _schedule_key(schedule: dict) -> str:
    return str(schedule.get("id") or schedule.get("reference_number") or "")


def schedules_from_source(data: dict) -> list[dict]:
    """Return unique vessel schedules from either supported source shape."""

    supplied = data.get("schedules")
    if isinstance(supplied, list) and supplied:
        schedules: dict[str, dict] = {}
        for schedule in supplied:
            if isinstance(schedule, dict):
                key = _schedule_key(schedule)
                if key and key not in schedules:
                    schedules[key] = dict(schedule,transshipments=sea_legs(schedule))
        return list(schedules.values())

    schedules = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule")
        if isinstance(schedule, dict):
            key = _schedule_key(schedule)
            if key and key not in schedules:
                schedules[key] = dict(schedule,transshipments=sea_legs(schedule))
    return list(schedules.values())


def _booking_counts(data: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule") or {}
        key = _schedule_key(schedule)
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


def _actual_is_missing(value: object) -> bool:
    return str(value or "").strip().upper() in {"", "TBA", "N/A", "NA", "NONE", "NULL"}


def _in_transit_vessel_keys(data: dict) -> set[str]:
    """Return vessels on schedules and legs whose ATA is still missing."""

    vessels: set[str] = set()
    for schedule in schedules_from_source(data):
        for leg in schedule.get("transshipments") or []:
            if not _actual_is_missing(leg.get("ata")):
                continue
            vessel = canonical((leg.get("vessel") or {}).get("name"))
            if vessel:
                vessels.add(vessel)
    return vessels


def filter_in_transit_vessels(source_path: Path, requested_vessels: Iterable[str]) -> tuple[list[str], list[str]]:
    """Keep only requested vessels having an ActiveTrack leg without ATA."""

    data = json.loads(source_path.read_text(encoding="utf-8"))
    in_transit_keys = _in_transit_vessel_keys(data)
    in_transit: list[str] = []
    skipped: list[str] = []
    for vessel in requested_vessels:
        key = canonical(vessel)
        if key in in_transit_keys and key not in UNTRACKABLE_VESSEL_NAMES:
            in_transit.append(vessel)
        else:
            skipped.append(vessel)
    return in_transit, skipped


def load_active_track_legs(source_path: Path, requested_vessels: Iterable[str]) -> tuple[list[dict], dict]:
    """Load unique ActiveTrack schedule legs for the requested vessels."""

    data = json.loads(source_path.read_text(encoding="utf-8"))
    requested = {canonical(name): index for index, name in enumerate(requested_vessels)}
    counts = _booking_counts(data)
    legs: list[dict] = []

    for schedule in schedules_from_source(data):
        schedule_key = _schedule_key(schedule)
        for leg in schedule.get("transshipments") or []:
            if not _actual_is_missing(leg.get("ata")):
                continue
            vessel = (leg.get("vessel") or {}).get("name")
            vessel_key = canonical(vessel)
            if vessel_key not in requested:
                continue
            legs.append(
                {
                    "requested_order": requested[vessel_key],
                    "vs_id": schedule.get("reference_number") or "",
                    "leg": leg.get("leg_number") or "",
                    "vessel": vessel or "",
                    "voyage": leg.get("voyage") or "",
                    "pol": (leg.get("departure_port") or {}).get("name") or "",
                    "pod": (leg.get("arrival_port") or {}).get("name") or "",
                    "at_etd": leg.get("etd"),
                    "at_eta": leg.get("eta"),
                    "booking_count": counts.get(schedule_key, 0),
                }
            )

    legs.sort(
        key=lambda row: (
            row["requested_order"],
            row.get("at_eta") or "9999-12-31",
            row.get("at_etd") or "9999-12-31",
            str(row.get("vs_id") or ""),
            str(row.get("leg") or ""),
        )
    )
    return legs, data


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _read_bot_status(result_dir: Path) -> dict[str, str]:
    path = result_dir / "results.json"
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return {
        canonical(item.get("vessel")): str(item.get("status") or "NO CARRIER DATA")
        for item in payload
        if isinstance(item, dict) and item.get("vessel")
    }


def _parse_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], pattern).date()
        except ValueError:
            continue
    return None


def _date_change(active: date | None, latest: date | None) -> str:
    if latest is None:
        return "missing"
    if active is None:
        return "new"
    if latest > active:
        return "delayed"
    if latest < active:
        return "advanced"
    return "unchanged"


def build_comparison_rows(
    source_path: Path,
    requested_vessels: list[str],
    result_dir: Path,
) -> list[dict]:
    """Combine ActiveTrack legs with the newest carrier-bot match output."""

    active_legs, _data = load_active_track_legs(source_path, requested_vessels)
    statuses = _read_bot_status(result_dir)
    matched_rows = _read_csv(result_dir / "matched_legs.csv")
    matches: dict[tuple[str, str], dict] = {}
    for row in matched_rows:
        key = (canonical(row.get("vs_id")), str(row.get("leg") or ""))
        current = matches.get(key)
        current_dates = int(bool(current and current.get("carrier_etd"))) + int(bool(current and current.get("carrier_eta")))
        new_dates = int(bool(row.get("carrier_etd"))) + int(bool(row.get("carrier_eta")))
        if current is None or new_dates > current_dates:
            matches[key] = row

    output: list[dict] = []
    active_vessels = {canonical(row.get("vessel")) for row in active_legs}
    for leg in active_legs:
        match = matches.get((canonical(leg.get("vs_id")), str(leg.get("leg") or "")))
        at_etd = _parse_date(leg.get("at_etd"))
        at_eta = _parse_date(leg.get("at_eta"))
        latest_etd = _parse_date((match or {}).get("carrier_etd"))
        latest_eta = _parse_date((match or {}).get("carrier_eta"))
        etd_change = _date_change(at_etd, latest_etd)
        eta_change = _date_change(at_eta, latest_eta)
        no_data = latest_etd is None and latest_eta is None

        changes = {etd_change, eta_change}
        if no_data:
            result = statuses.get(canonical(leg.get("vessel")), "NO CARRIER DATA")
            if result == "OK":
                result = "NO MATCHED CARRIER DATES"
        elif "delayed" in changes and "advanced" in changes:
            result = "MIXED DATE CHANGE"
        elif "delayed" in changes:
            result = "DELAYED"
        elif "advanced" in changes:
            result = "ADVANCED"
        elif "missing" in changes:
            result = "PARTIAL CARRIER DATA"
        elif "new" in changes:
            result = "NEW CARRIER DATE"
        else:
            result = "NO CHANGE"

        output.append(
            {
                **leg,
                "at_etd": at_etd,
                "at_eta": at_eta,
                "latest_etd": latest_etd,
                "latest_eta": latest_eta,
                "etd_change": etd_change,
                "eta_change": eta_change,
                "result": result,
                "source_url": (match or {}).get("source_url") or "",
                "no_data": no_data,
            }
        )

    for order, vessel in enumerate(requested_vessels):
        if canonical(vessel) in active_vessels:
            continue
        bot_status = statuses.get(canonical(vessel), "NO CARRIER DATA")
        output.append(
            {
                "requested_order": order,
                "vs_id": "",
                "leg": "",
                "vessel": vessel,
                "voyage": "",
                "pol": "",
                "pod": "",
                "at_etd": None,
                "at_eta": None,
                "latest_etd": None,
                "latest_eta": None,
                "booking_count": 0,
                "etd_change": "missing",
                "eta_change": "missing",
                "result": f"NO ACTIVTRACK MATCH; {bot_status}",
                "source_url": "",
                "no_data": True,
            }
        )

    output.sort(
        key=lambda row: (
            row["requested_order"],
            row.get("at_eta") or date.max,
            row.get("at_etd") or date.max,
            str(row.get("vs_id") or ""),
        )
    )
    return output


def build_tracking_report(
    carrier: str,
    requested_vessels: list[str],
    source_path: Path,
    result_dir: Path,
    output_path: Path,
) -> TrackingReportStats:
    """Create the final color-coded Excel comparison report."""

    rows = build_comparison_rows(source_path, requested_vessels, result_dir)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "Tracking Comparison"
    ws.sheet_view.showGridLines = False
    ws.freeze_panes = "A7"

    navy = "17365D"
    pale_blue = "DCE6F1"
    pale_red = "FDE9E7"
    red = "C00000"
    green = "008000"
    gray = "666666"
    thin_gray = Side(style="thin", color="D9E1F2")

    ws["A1"] = "Vessel Tracking Comparison"
    ws["A1"].font = Font(name="Aptos Display", size=16, bold=True, color=navy)
    ws["A2"] = "Carrier"
    ws["B2"] = carrier
    ws["D2"] = "Generated"
    ws["E2"] = datetime.now()
    ws["E2"].number_format = "dd-mmm-yyyy hh:mm"
    ws["A3"] = "ActiveTrack source"
    ws["B3"] = source_path.name
    ws["A4"] = "Requested vessels"
    ws["B4"] = len(requested_vessels)
    ws["D4"] = "Comparison rows"
    ws["E4"] = len(rows)
    ws["G4"] = "No carrier data"
    ws["H4"] = sum(1 for row in rows if row["no_data"])
    for cell in ("A2", "D2", "A3", "A4", "D4", "G4"):
        ws[cell].font = Font(name="Aptos", size=10, bold=True, color=gray)
    for cell in ("B2", "E2", "B3", "B4", "E4", "H4"):
        ws[cell].font = Font(name="Aptos", size=10, bold=True, color="1F1F1F")

    headers = [
        "Vessel ID",
        "Leg",
        "Vessel Name",
        "Voyage",
        "POL",
        "POD",
        "ActiveTrack ETD",
        "ActiveTrack ETA",
        "Latest ETD",
        "Latest ETA",
        "Booking Count",
        "Result",
        "Carrier Source",
    ]
    header_row = 6
    for column, header in enumerate(headers, 1):
        cell = ws.cell(header_row, column, header)
        cell.fill = PatternFill("solid", fgColor=navy)
        cell.font = Font(name="Aptos", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = Border(bottom=Side(style="medium", color=navy))

    for row_number, row in enumerate(rows, header_row + 1):
        values = [
            row["vs_id"],
            row["leg"],
            row["vessel"],
            row["voyage"],
            row["pol"],
            row["pod"],
            row["at_etd"],
            row["at_eta"],
            row["latest_etd"],
            row["latest_eta"],
            row["booking_count"],
            row["result"],
            row["source_url"],
        ]
        for column, value in enumerate(values, 1):
            cell = ws.cell(row_number, column, value)
            cell.font = Font(name="Aptos", size=10, color="1F1F1F")
            cell.alignment = Alignment(vertical="center", wrap_text=False)
            cell.border = Border(bottom=thin_gray)
        for column in (7, 8, 9, 10):
            ws.cell(row_number, column).number_format = "dd-mmm-yyyy"
            ws.cell(row_number, column).alignment = Alignment(horizontal="center", vertical="center")
        for column in (2, 11):
            ws.cell(row_number, column).alignment = Alignment(horizontal="center", vertical="center")

        if row["no_data"]:
            for column in range(1, len(headers) + 1):
                cell = ws.cell(row_number, column)
                cell.font = Font(name="Aptos", size=10, bold=True, color=red)
                cell.fill = PatternFill("solid", fgColor=pale_red)
        else:
            for column, change in ((9, row["etd_change"]), (10, row["eta_change"])):
                cell = ws.cell(row_number, column)
                if change == "delayed":
                    cell.font = Font(name="Aptos", size=10, bold=True, color=red)
                elif change == "advanced":
                    cell.font = Font(name="Aptos", size=10, bold=True, color=green)
                elif change == "missing":
                    cell.font = Font(name="Aptos", size=10, bold=True, color=red)
            if "DELAYED" in row["result"]:
                ws.cell(row_number, 12).font = Font(name="Aptos", size=10, bold=True, color=red)
            elif "ADVANCED" in row["result"]:
                ws.cell(row_number, 12).font = Font(name="Aptos", size=10, bold=True, color=green)

    last_row = header_row + len(rows)
    if rows:
        table = Table(displayName="VesselTrackingComparison", ref=f"A{header_row}:M{last_row}")
        table.tableStyleInfo = TableStyleInfo(
            name="TableStyleMedium2",
            showFirstColumn=False,
            showLastColumn=False,
            showRowStripes=True,
            showColumnStripes=False,
        )
        ws.add_table(table)

    widths = {
        "A": 14,
        "B": 8,
        "C": 25,
        "D": 15,
        "E": 24,
        "F": 24,
        "G": 18,
        "H": 18,
        "I": 16,
        "J": 16,
        "K": 14,
        "L": 30,
        "M": 52,
    }
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    ws.row_dimensions[1].height = 25
    ws.row_dimensions[6].height = 24
    for row_number in range(7, last_row + 1):
        ws.row_dimensions[row_number].height = 21

    ws.auto_filter.ref = f"A{header_row}:M{last_row}"
    from console_rules import color_report_rows
    color_report_rows(wb, json.loads(source_path.read_text(encoding="utf-8-sig")))
    wb.save(output_path)

    delayed_rows = sum(1 for row in rows if "delayed" in {row["etd_change"], row["eta_change"]})
    advanced_rows = sum(1 for row in rows if "advanced" in {row["etd_change"], row["eta_change"]})
    no_data_rows = sum(1 for row in rows if row["no_data"])
    return TrackingReportStats(
        output_path=output_path,
        carrier=carrier,
        requested_vessels=len(requested_vessels),
        rows=len(rows),
        delayed_rows=delayed_rows,
        advanced_rows=advanced_rows,
        no_data_rows=no_data_rows,
        source_path=source_path,
    )
