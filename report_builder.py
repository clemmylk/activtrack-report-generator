"""Create the ActivTrack vessel booking workbook.

The report uses a normalized JSON structure with two top-level arrays:
``bookings`` and ``schedules``.  Each schedule can contain one or more
``legs``.  One detail row is written per booking leg so transshipments are
never flattened or lost.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
import re
from typing import Any, Iterable
from console_rules import write_console_sheet, color_report_rows

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side, Color
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter


ACTIVTRACK_URL = "https://activtrack.globactiv.com/sea-import/bookings"

NAVY = "17365D"
TEAL = "1F6D7A"
PALE_BLUE = "EAF2F8"
PALE_TEAL = "E8F4F4"
PALE_GOLD = "FFF4CC"
PALE_RED = "FDECEC"
PALE_GREEN = "E7F5E7"
PALE_GRAY = "F6F8FA"
TRANS_FILL = "FCE8D5"
BORDER_COLOR = "CCD6E0"
TEXT = "1F2933"
MUTED = "52606D"
RED = "A61B1B"
MAS_RED = "FF0000"
GREEN = "1E6B37"
AMBER = "8A5A00"
WHITE = "FFFFFF"


class ReportDataError(ValueError):
    """Raised when captured ActivTrack data is incomplete."""


@dataclass(frozen=True)
class ReportStats:
    output_path: Path
    bookings: int
    schedules: int
    vessels: int
    schedule_legs: int
    detail_rows: int
    atd_missing_legs: int
    ata_missing_legs: int
    arriving_48h_legs: int
    missing_schedule_bookings: int


def _pick(data: dict[str, Any], *keys: str, default: Any = "") -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def _as_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:19] if "T" in fmt else text[:10], fmt).date()
        except ValueError:
            continue
    if len(text) >= 10:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            pass
    return None


def _excel_text(value: Any) -> str:
    """Store identifiers/names as text without accidental formula injection."""
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _is_mas_customer(value: Any) -> bool:
    """Return True only when MAS appears as a standalone customer-name token."""
    return bool(re.search(r"\bMAS\b", str(value or ""), flags=re.IGNORECASE))


def _normalize_route(value: Any, leg_count: int) -> str:
    raw = str(value or "").strip().lower()
    if "trans" in raw or leg_count > 1:
        return "transshipment"
    return "direct"


def _display_name(value: Any) -> Any:
    if isinstance(value, dict):
        return _pick(value, "name", "label", "title", "port_name", "vessel_name", default="")
    return value


def _normalize_leg(raw: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    return {
        "vessel_name": _display_name(_pick(raw, "vessel_name", "vessel", "vesselName", default=_pick(fallback, "vessel_name", "vessel", "vesselName"))),
        "voyage": _pick(raw, "voyage", "voyage_number", "voyageNo", default=_pick(fallback, "voyage", "voyage_number")),
        "departure_port": _display_name(_pick(raw, "departure_port", "pol", "origin_port", "origin", default=_pick(fallback, "departure_port", "pol", "origin_port"))),
        "arrival_port": _display_name(_pick(raw, "arrival_port", "pod", "destination_port", "destination", default=_pick(fallback, "arrival_port", "pod", "destination_port"))),
        "bkg_cutoff": _as_date(_pick(raw, "bkg_cutoff", "booking_cutoff", "bookingCutoff", default=_pick(fallback, "bkg_cutoff", "booking_cutoff"))),
        "cfs_cutoff": _as_date(_pick(raw, "cfs_cutoff", "cfsCutoff", default=_pick(fallback, "cfs_cutoff"))),
        "etd": _as_date(_pick(raw, "etd", "estimated_departure", "estimatedDeparture", default=_pick(fallback, "etd"))),
        # Actual dates belong to the individual leg. Never inherit a schedule-level
        # actual date when this leg is blank: for transshipments that would copy the
        # first leg's ATD into later legs and hide them from the ATD Missing report.
        "atd": _as_date(_pick(raw, "atd", "actual_departure", "actualDeparture", default=None)),
        "eta": _as_date(_pick(raw, "eta", "estimated_arrival", "estimatedArrival", default=_pick(fallback, "eta"))),
        # Apply the same rule to ATA so an earlier leg's arrival cannot be reported
        # as the actual arrival of a later leg.
        "ata": _as_date(_pick(raw, "ata", "actual_arrival", "actualArrival", default=None)),
    }


def _normalize_incomplete_booking(raw: dict[str, Any], default_issue: str = "Vessel schedule details are missing.") -> dict[str, Any]:
    status = _pick(raw, "status_label", "status_name", "status")
    if isinstance(status, dict):
        status = _pick(status, "display_name", "label", "name", "value")
    return {
        "reference_number": str(_pick(raw, "reference_number", "booking_reference", "reference")),
        "hbl_number": str(_pick(raw, "hbl_number", "hbl", "house_bl_number", "house_bill_number")),
        "so_number": str(_pick(raw, "so_number", "shipping_order_number", "sales_order_number")),
        "customer_name": _display_name(_pick(raw, "customer_name", "customer", "client_name", "consignee_name", "consignee")),
        "status_label": status,
        "mbl_number": str(_pick(raw, "mbl_number", "mbl", "master_bl_number", "master_bill_number")),
        "container_number": str(_pick(raw, "container_number", "container_numbers")),
        "issue": str(_pick(raw, "capture_issue", "issue", default=default_issue)),
    }


def normalize_source(source: dict[str, Any]) -> dict[str, Any]:
    bookings_raw = source.get("bookings") or []
    schedules_raw = source.get("schedules") or []
    if not isinstance(bookings_raw, list) or not bookings_raw:
        raise ReportDataError("No booking records were captured from ActivTrack.")
    if not isinstance(schedules_raw, list) or not schedules_raw:
        raise ReportDataError("No vessel schedule records were captured from ActivTrack.")

    schedules: list[dict[str, Any]] = []
    schedule_ref_to_id: dict[str, Any] = {}
    for index, raw in enumerate(schedules_raw, start=1):
        if not isinstance(raw, dict):
            continue
        schedule_id = _pick(raw, "id", "vessel_schedule_id", "schedule_id", default=f"schedule-{index}")
        reference = _pick(raw, "reference_number", "reference", "schedule_reference", "vessel_schedule_reference_number", default=str(schedule_id))
        raw_legs = _pick(raw, "legs", "vessel_legs", "schedule_legs", "vessel_schedule_legs", "transshipments", default=[])
        if not isinstance(raw_legs, list) or not raw_legs:
            raw_legs = [raw]
        legs = [_normalize_leg(leg if isinstance(leg, dict) else {}, raw) for leg in raw_legs]
        route_type = _normalize_route(_pick(raw, "route_type", "routeType"), len(legs))
        schedule = {
            "id": schedule_id,
            "reference_number": str(reference),
            "route_type": route_type,
            "departure_port": _display_name(_pick(raw, "departure_port", "pol", "origin_port")),
            "arrival_port": _display_name(_pick(raw, "arrival_port", "pod", "destination_port")),
            "legs": legs,
        }
        schedules.append(schedule)
        schedule_ref_to_id[str(reference)] = schedule_id

    schedules_by_id = {str(item["id"]): item for item in schedules}
    bookings: list[dict[str, Any]] = []
    incomplete_bookings: list[dict[str, Any]] = []
    supplied_incomplete = source.get("incomplete_bookings") or []
    if isinstance(supplied_incomplete, list):
        incomplete_bookings.extend(
            _normalize_incomplete_booking(raw)
            for raw in supplied_incomplete
            if isinstance(raw, dict)
        )
    seen_booking_refs: set[str] = set()
    for index, raw in enumerate(bookings_raw, start=1):
        if not isinstance(raw, dict):
            continue
        reference = str(_pick(raw, "reference_number", "booking_reference", "reference", default=f"booking-{index}"))
        schedule_id = _pick(raw, "vessel_schedule_id", "schedule_id", "vesselScheduleId")
        schedule_reference = str(_pick(raw, "vessel_schedule_reference_number", "vesselScheduleReferenceNumber", "schedule_reference", "vessel_schedule_reference"))
        nested_schedule = raw.get("vessel_schedule") or raw.get("vesselSchedule") or raw.get("schedule")
        if isinstance(nested_schedule, dict):
            if schedule_id in (None, ""):
                schedule_id = _pick(nested_schedule, "id", "schedule_id", "vessel_schedule_id")
            if not schedule_reference:
                schedule_reference = str(_pick(nested_schedule, "reference_number", "reference", "schedule_reference"))
        if schedule_id in (None, "") and schedule_reference:
            schedule_id = schedule_ref_to_id.get(schedule_reference, schedule_reference)
        if str(schedule_id) not in schedules_by_id:
            if schedule_reference and schedule_reference in schedule_ref_to_id:
                schedule_id = schedule_ref_to_id[schedule_reference]
            else:
                incomplete_bookings.append(_normalize_incomplete_booking(
                    raw,
                    "The booking could not be linked to a captured vessel schedule.",
                ))
                continue
        if reference in seen_booking_refs:
            continue
        seen_booking_refs.add(reference)
        bookings.append({
            "reference_number": reference,
            "status_label": _pick(raw, "status_label", "status_name", "status"),
            "customer_name": _display_name(_pick(raw, "customer_name", "customer", "client_name", "consignee_name", "consignee")),
            "shipment_type": _pick(raw, "shipment_type", "container_type", "load_type"),
            "container_number": _pick(raw, "container_number", "container_numbers"),
            "vessel_schedule_id": schedule_id,
            "vessel_schedule_reference_number": schedule_reference,
            "arrival_port": _display_name(_pick(raw, "arrival_port", "pod", "destination_port")),
            "booking_confirmed_date": _as_date(_pick(raw, "booking_confirmed_date", "confirmed_date")),
        })

    if not bookings:
        raise ReportDataError("No usable booking records remained after validation.")

    filter_data = source.get("filter") if isinstance(source.get("filter"), dict) else {}
    totals_data = source.get("totals") if isinstance(source.get("totals"), dict) else {}
    filtered_total = int(totals_data.get("filtered_bookings") or (len(bookings) + len(incomplete_bookings)))
    totals_data = {
        **totals_data,
        "bookings": len(bookings),
        "filtered_bookings": filtered_total,
        "incomplete_bookings": len(incomplete_bookings),
        "schedules": len(schedules),
    }
    return {
        "filter": filter_data,
        "totals": totals_data,
        "bookings": bookings,
        "incomplete_bookings": incomplete_bookings,
        "schedules": schedules,
    }


def _thin_border() -> Border:
    side = Side(style="thin", color=BORDER_COLOR)
    return Border(left=side, right=side, top=side, bottom=side)


def _title(ws, title: str, end_col: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=end_col)
    cell = ws.cell(1, 1, title)
    cell.fill = PatternFill("solid", fgColor=NAVY)
    cell.font = Font(name="Aptos Display", size=18, bold=True, color=WHITE)
    cell.alignment = Alignment(vertical="center")
    ws.row_dimensions[1].height = 34


def _style_header(ws, row: int, start_col: int, end_col: int) -> None:
    for cell in ws.iter_cols(min_col=start_col, max_col=end_col, min_row=row, max_row=row):
        c = cell[0]
        c.fill = PatternFill("solid", fgColor=NAVY)
        c.font = Font(name="Aptos", size=10, bold=True, color=WHITE)
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[row].height = 32


def _add_table(ws, ref: str, name: str) -> None:
    table = Table(displayName=name, ref=ref)
    table.tableStyleInfo = TableStyleInfo(
        name="TableStyleMedium2",
        showFirstColumn=False,
        showLastColumn=False,
        showRowStripes=True,
        showColumnStripes=False,
    )
    ws.add_table(table)


def _set_widths(ws, widths: dict[str, float]) -> None:
    for column, width in widths.items():
        ws.column_dimensions[column].width = width


def _filter_caption(source: dict[str, Any]) -> str:
    filt = source.get("filter") or {}
    freight = str(filt.get("sea_freight") or filt.get("module") or "Sea Import")
    if freight.strip().lower() == "import":
        freight = "Sea Import"
    etd_from = str(filt.get("etd_from") or filt.get("ETD From") or "Current filter")
    etd_to = str(filt.get("etd_to") or filt.get("ETD To") or "Current filter")
    pages = filt.get("ui_pages") or filt.get("pages") or "captured all pages"
    totals = source.get("totals") or {}
    reportable = int(totals.get("bookings") or 0)
    missing = int(totals.get("incomplete_bookings") or 0)
    filtered = int(totals.get("filtered_bookings") or reportable + missing)
    booking_text = f"{filtered} filtered / {reportable} reportable"
    if missing:
        booking_text += f" / {missing} missing schedules"
    return f"Applied ActivTrack filter: {freight} | ETD {etd_from} to {etd_to} | {booking_text} | UI pages: {pages}"


def _date_status(actual: date | None, estimated: date | None, as_of: date) -> str:
    if actual:
        return "UPDATED"
    if estimated and estimated <= as_of:
        return "MISSING"
    return "PENDING"


def _recorded_containers(bookings: list[dict[str, Any]]) -> list[str]:
    """Unique recorded identifiers for this exact VS, excluding empty placeholders."""
    result = set()
    for booking in bookings:
        raw = booking.get("container_number") or ""
        values = raw if isinstance(raw, (list, tuple)) else re.split(r"[,;\n|/]+", re.sub(r"\bN\s*/\s*A\b", "", str(raw), flags=re.IGNORECASE))
        for value in values:
            text = str(value).strip().upper()
            if re.sub(r"[^A-Z0-9]", "", text) in {"", "TBA", "TBD", "NA", "NONE", "NULL", "MISSING", "PENDING", "NOCONTAINER", "NOTAVAILABLE", "NOTRECORDED"}:
                continue
            matches = re.findall(r"(?<![A-Z0-9])[A-Z]{4}\s*\d{6}\s*\d(?!\d)", text)
            result.update(re.sub(r"\s", "", item) for item in matches) if matches else result.add(text)
    return sorted(result)


def _build_records(source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    schedule_by_id = {str(item["id"]): item for item in source["schedules"]}
    bookings_by_schedule: dict[str, list[dict[str, Any]]] = defaultdict(list)
    detail_records: list[dict[str, Any]] = []
    all_legs: list[dict[str, Any]] = []

    for booking in source["bookings"]:
        bookings_by_schedule[str(booking["vessel_schedule_id"])].append(booking)

    mas_schedule_ids = {
        schedule_id
        for schedule_id, schedule_bookings in bookings_by_schedule.items()
        if any(_is_mas_customer(booking.get("customer_name")) for booking in schedule_bookings)
    }

    for booking in source["bookings"]:
        schedule = schedule_by_id[str(booking["vessel_schedule_id"])]
        legs = schedule["legs"]
        for leg_no, leg in enumerate(legs, start=1):
            detail_records.append({
                "booking_ref": booking["reference_number"],
                "status": booking.get("status_label", ""),
                "customer": booking.get("customer_name", ""),
                "shipment_type": booking.get("shipment_type", ""),
                "schedule_ref": schedule["reference_number"],
                "route_type": schedule["route_type"],
                "total_legs": len(legs),
                "leg_no": leg_no,
                "vessel_name": leg.get("vessel_name") or "(Vessel not specified)",
                "voyage": leg.get("voyage", ""),
                "pol": leg.get("departure_port", ""),
                "bkg_cutoff": leg.get("bkg_cutoff"),
                "cfs_cutoff": leg.get("cfs_cutoff"),
                "etd": leg.get("etd"),
                "atd": leg.get("atd"),
                "eta": leg.get("eta"),
                "ata": leg.get("ata"),
                "pod": leg.get("arrival_port", ""),
                "final_pod": schedule.get("arrival_port") or booking.get("arrival_port", ""),
                "booking_confirmed": booking.get("booking_confirmed_date"),
                "has_mas_booking": str(booking["vessel_schedule_id"]) in mas_schedule_ids,
            })

    for schedule in source["schedules"]:
        schedule_bookings = bookings_by_schedule.get(str(schedule["id"]), [])
        booking_refs = sorted({item["reference_number"] for item in schedule_bookings})
        for leg_no, leg in enumerate(schedule["legs"], start=1):
            all_legs.append({
                "vessel_name": leg.get("vessel_name") or "(Vessel not specified)",
                "schedule_ref": schedule["reference_number"],
                "voyage": leg.get("voyage", ""),
                "route_type": schedule["route_type"].upper(),
                "leg_no": leg_no,
                "total_legs": len(schedule["legs"]),
                "pol": leg.get("departure_port", ""),
                "pod": leg.get("arrival_port", ""),
                "bkg_cutoff": leg.get("bkg_cutoff"),
                "cfs_cutoff": leg.get("cfs_cutoff"),
                "etd": leg.get("etd"),
                "atd": leg.get("atd"),
                "eta": leg.get("eta"),
                "ata": leg.get("ata"),
                "booking_refs": booking_refs,
                "container_numbers": _recorded_containers(schedule_bookings),
                "has_fcl_booking": any(str(item.get("shipment_type", "")).strip().upper() == "FCL" for item in schedule_bookings),
                "booking_count": len(booking_refs),
                "has_mas_booking": str(schedule["id"]) in mas_schedule_ids,
            })
    return detail_records, all_legs, bookings_by_schedule


def _write_details(ws, records: list[dict[str, Any]]) -> int:
    headers = [
        "Booking Ref", "Status", "Customer", "Shipment Type", "Schedule Ref", "Route Type",
        "Total Legs", "Leg No.", "Vessel Name", "Voyage", "POL", "BKG Cutoff", "CFS Cutoff",
        "ETD", "ATD", "ETA", "ATA", "POD", "Final POD", "Booking Confirmed",
        "Unique Bookingâ€“Vessel Flag", "Unique Booking Flag", "Unique Schedule Flag", "Unique Vesselâ€“Schedule Flag",
    ]
    ws.append(headers)
    _style_header(ws, 1, 1, len(headers))
    for row_no, record in enumerate(records, start=2):
        ws.append([
            _excel_text(record["booking_ref"]), _excel_text(record["status"]), _excel_text(record["customer"]),
            _excel_text(record["shipment_type"]), _excel_text(record["schedule_ref"]), record["route_type"],
            record["total_legs"], record["leg_no"], _excel_text(record["vessel_name"]), _excel_text(record["voyage"]),
            _excel_text(record["pol"]), record["bkg_cutoff"], record["cfs_cutoff"], record["etd"], record["atd"],
            record["eta"], record["ata"], _excel_text(record["pod"]), _excel_text(record["final_pod"]),
            record["booking_confirmed"],
            f'=IF(COUNTIFS($A$2:A{row_no},A{row_no},$I$2:I{row_no},I{row_no})=1,1,0)',
            f'=IF(COUNTIF($A$2:A{row_no},A{row_no})=1,1,0)',
            f'=IF(COUNTIF($E$2:E{row_no},E{row_no})=1,1,0)',
            f'=IF(COUNTIFS($E$2:E{row_no},E{row_no},$I$2:I{row_no},I{row_no})=1,1,0)',
        ])
    last_row = len(records) + 1
    _add_table(ws, f"A1:X{last_row}", "BookingVesselDetailsTable")
    ws.freeze_panes = "B2"
    ws.sheet_view.showGridLines = False
    for row in ws.iter_rows(min_row=2, max_row=last_row, min_col=1, max_col=24):
        for cell in row:
            cell.font = Font(name="Aptos", size=10, color=TEXT)
            cell.alignment = Alignment(vertical="center")
        ws.row_dimensions[row[0].row].height = 20
    for row_no, record in enumerate(records, start=2):
        if record["has_mas_booking"]:
            ws.cell(row_no, 9).font = Font(name="Aptos", size=10, bold=True, color=MAS_RED)
    for row in range(2, last_row + 1):
        for col in range(12, 21):
            ws.cell(row, col).number_format = "yyyy-mm-dd"
    trans_rule = FormulaRule(formula=['$F2="transshipment"'], fill=PatternFill("solid", fgColor=PALE_BLUE))
    ws.conditional_formatting.add(f"A2:X{last_row}", trans_rule)
    _set_widths(ws, {
        "A": 15, "B": 19, "C": 34, "D": 15, "E": 15, "F": 17, "G": 11, "H": 9,
        "I": 29, "J": 14, "K": 29, "L": 14, "M": 14, "N": 13, "O": 13, "P": 13,
        "Q": 13, "R": 29, "S": 25, "T": 17, "U": 20, "V": 17, "W": 18, "X": 22,
    })
    ws.auto_filter.ref = f"A1:X{last_row}"
    ws.print_title_rows = "1:1"
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    return last_row


def _write_incomplete_bookings_sheet(ws, rows: list[dict[str, Any]]) -> None:
    """Expose bookings that ActiveTrack returned without a usable schedule."""
    headers = [
        "Booking Ref", "HBL", "SO", "Customer / Consignee", "Status",
        "MBL", "Container", "Issue / Required Action",
    ]
    _title(ws, "Bookings Missing Vessel Schedules", len(headers))
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=len(headers))
    ws.cell(2, 1).value = (
        "These filtered bookings were captured, but ActiveTrack did not provide a usable linked vessel schedule. "
        "They are excluded from vessel/date calculations and require correction in ActiveTrack."
    )
    ws.cell(2, 1).fill = PatternFill("solid", fgColor=PALE_RED)
    ws.cell(2, 1).font = Font(name="Aptos", size=10, bold=True, color=RED)
    ws.cell(2, 1).alignment = Alignment(wrap_text=True, vertical="center")
    ws.row_dimensions[2].height = 34
    for column, header in enumerate(headers, 1):
        ws.cell(4, column, header)
    _style_header(ws, 4, 1, len(headers))
    for row_number, item in enumerate(rows, 5):
        values = [
            item.get("reference_number"), item.get("hbl_number"), item.get("so_number"),
            item.get("customer_name"), item.get("status_label"), item.get("mbl_number"),
            item.get("container_number"), item.get("issue"),
        ]
        for column, value in enumerate(values, 1):
            cell = ws.cell(row_number, column, _excel_text(value))
            cell.font = Font(name="Aptos", size=10, color=RED if column in {1, 8} else TEXT, bold=column in {1, 8})
            cell.alignment = Alignment(vertical="center", wrap_text=column in {4, 8})
            cell.border = Border(bottom=Side(style="hair", color=BORDER_COLOR))
        ws.row_dimensions[row_number].height = 30
    last_row = 4 + len(rows)
    if rows:
        _add_table(ws, f"A4:H{last_row}", "MissingVesselSchedulesTable")
    ws.freeze_panes = "A5"
    ws.sheet_view.showGridLines = False
    ws.sheet_properties.tabColor = RED
    ws.auto_filter.ref = f"A4:H{max(4, last_row)}"
    _set_widths(ws, {"A": 16, "B": 24, "C": 22, "D": 34, "E": 20, "F": 22, "G": 24, "H": 56})
    ws.print_title_rows = "1:4"


def _write_kpi(ws, label_cell: str, value_cell: str, label: str, formula: str) -> None:
    ws[label_cell] = label
    ws[value_cell] = formula
    ws[label_cell].fill = PatternFill("solid", fgColor=PALE_TEAL)
    ws[label_cell].font = Font(name="Aptos", size=10, bold=True, color=NAVY)
    ws[label_cell].alignment = Alignment(vertical="center")
    ws[value_cell].fill = PatternFill("solid", fgColor=TEAL)
    ws[value_cell].font = Font(name="Aptos Display", size=13, bold=True, color=WHITE)
    ws[value_cell].alignment = Alignment(horizontal="center", vertical="center")
    ws[value_cell].number_format = "#,##0"


def _status_conditional_formats(ws, ranges: Iterable[str]) -> None:
    for target in ranges:
        start_cell = target.split(":", 1)[0]
        col = "".join(ch for ch in start_cell if ch.isalpha())
        row = "".join(ch for ch in start_cell if ch.isdigit())
        ws.conditional_formatting.add(target, FormulaRule(formula=[f'{col}{row}="MISSING"'], fill=PatternFill("solid", fgColor=PALE_RED), font=Font(bold=True, color=RED)))
        ws.conditional_formatting.add(target, FormulaRule(formula=[f'{col}{row}="PENDING"'], fill=PatternFill("solid", fgColor=PALE_GOLD), font=Font(color=AMBER)))
        ws.conditional_formatting.add(target, FormulaRule(formula=[f'{col}{row}="UPDATED"'], fill=PatternFill("solid", fgColor=PALE_GREEN), font=Font(color=GREEN)))


def _write_missing_sheet(ws, rows: list[dict[str, Any]], kind: str, as_of: date, detail_last_row: int) -> None:
    is_atd = kind == "ATD"
    end_col = 18 if is_atd else 17
    _title(ws, f"{kind} Not Updated â€” Due by {as_of.isoformat()}", end_col)
    ws.merge_cells("A2:O2")
    rule_target = "ETD is on or before the as-of date and ATD is blank" if kind == "ATD" else "ETA is on or before the as-of date and ATA is blank"
    ws["A2"] = f"Rule: {rule_target}. One row represents one vessel-schedule leg."
    ws["A2"].fill = PatternFill("solid", fgColor=PALE_GOLD)
    ws["A2"].font = Font(name="Aptos", size=10, italic=True, color=AMBER)
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws["P2"] = "As of Date"
    ws["Q2"] = as_of
    for cell in (ws["P2"], ws["Q2"]):
        cell.fill = PatternFill("solid", fgColor=PALE_TEAL)
        cell.font = Font(name="Aptos", size=10, bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center")
    ws["Q2"].number_format = "yyyy-mm-dd"

    data_last = max(8, 7 + len(rows))
    for merged_range in ("A4:C4", "E4:G4", "I4:K4", "M4:O4"):
        ws.merge_cells(merged_range)
    _write_kpi(ws, "A4", "D4", "Missing Schedule Legs", f'=COUNTA($A$8:$A${data_last})')
    _write_kpi(ws, "E4", "H4", "Booking-Leg Exposures", f'=SUM($P$8:$P${data_last})')
    _write_kpi(ws, "I4", "L4", "Transshipment Legs", f'=COUNTIF($B$8:$B${data_last},"TRANSSHIPMENT")')
    _write_kpi(ws, "M4", "P4", "Direct Legs", f'=COUNTIF($B$8:$B${data_last},"DIRECT")')
    ws.merge_cells("A5:R5" if is_atd else "A5:Q5")
    ws["A5"] = f"Source: {ACTIVTRACK_URL}"
    ws["A5"].font = Font(name="Aptos", size=9, color="2962A3")

    headers = ["Schedule Ref", "Route Type", "Leg No.", "Total Legs", "Vessel Name", "Voyage", "POL", "POD", "ETD", "ATD", "ATD Status", "ETA", "ATA", "ATA Status", "Days Overdue", "Booking Count", "Booking References"]
    if is_atd:
        headers.append("Container Number")
    for col, header in enumerate(headers, start=1):
        ws.cell(7, col, header)
    _style_header(ws, 7, 1, end_col)
    for index, record in enumerate(rows, start=8):
        ws.cell(index, 1, _excel_text(record["schedule_ref"]))
        ws.cell(index, 2, record["route_type"])
        ws.cell(index, 3, record["leg_no"])
        ws.cell(index, 4, record["total_legs"])
        ws.cell(index, 5, _excel_text(record["vessel_name"]))
        ws.cell(index, 6, _excel_text(record["voyage"]))
        ws.cell(index, 7, _excel_text(record["pol"]))
        ws.cell(index, 8, _excel_text(record["pod"]))
        ws.cell(index, 9, record["etd"])
        ws.cell(index, 10, record["atd"])
        ws.cell(index, 11, f'=IF(J{index}<>"","UPDATED",IF(I{index}<=$Q$2,"MISSING","PENDING"))')
        ws.cell(index, 12, record["eta"])
        ws.cell(index, 13, record["ata"])
        ws.cell(index, 14, f'=IF(M{index}<>"","UPDATED",IF(L{index}<=$Q$2,"MISSING","PENDING"))')
        due_col = "I" if kind == "ATD" else "L"
        ws.cell(index, 15, f"=MAX(0,$Q$2-{due_col}{index})")
        ws.cell(index, 16, f'=COUNTIFS(\'Booking Vessel Details\'!$E$2:$E${detail_last_row},A{index},\'Booking Vessel Details\'!$I$2:$I${detail_last_row},E{index},\'Booking Vessel Details\'!$H$2:$H${detail_last_row},C{index})')
        ws.cell(index, 17, _excel_text(", ".join(record["booking_refs"])))

        if is_atd:
            ws.cell(index, 18, _excel_text(", ".join(record.get("container_numbers", []))))

    last_row = 7 + len(rows)
    if rows:
        _add_table(ws, f"A7:{get_column_letter(end_col)}{last_row}", f"{kind}MissingScheduleLegsTable")
        if is_atd:
            ws.tables[f"{kind}MissingScheduleLegsTable"].tableStyleInfo.showRowStripes = False
        else:
            ws.conditional_formatting.add(f"A8:Q{last_row}", FormulaRule(formula=['$B8="TRANSSHIPMENT"'], fill=PatternFill("solid", fgColor=TRANS_FILL)))
            _status_conditional_formats(ws, [f"K8:K{last_row}", f"N8:N{last_row}"])
        for row in ws.iter_rows(min_row=8, max_row=last_row, min_col=1, max_col=end_col):
            for cell in row:
                cell.font = Font(name="Aptos", size=10, color=TEXT)
                cell.alignment = Alignment(vertical="center", wrap_text=(cell.column in (17, 18)))
        for row_no in range(8, last_row + 1):
            for col_no in (9, 10, 12, 13):
                ws.cell(row_no, col_no).number_format = "yyyy-mm-dd"
        for row_no, record in enumerate(rows, start=8):
            if is_atd:
                # Office theme: Accent 2, lighter 60%; Accent 3, lighter 40%.
                shade = Color(theme=6, tint=0.4) if record.get("container_numbers") else Color(theme=5, tint=0.6)
                ws.cell(row_no, 1).fill = PatternFill("solid", fgColor=shade)
                for cell in ws.iter_rows(min_row=row_no, max_row=row_no, min_col=1, max_col=end_col):
                    for c in cell:
                        c.font = Font(name="Aptos", size=10, color=TEXT, bold=record.get("has_fcl_booking", False))
            if record["has_mas_booking"]:
                ws.cell(row_no, 5).font = Font(name="Aptos", size=10, bold=True, color=MAS_RED)
    else:
        ws["B8"] = "No matching records as of this date."
        ws["B8"].font = Font(name="Aptos", size=10, italic=True, color=GREEN)
        last_row = 8
    ws.freeze_panes = "C8"
    ws.sheet_view.showGridLines = False
    _set_widths(ws, {"A": 15, "B": 18, "C": 10, "D": 10, "E": 29, "F": 14, "G": 29, "H": 29, "I": 13, "J": 13, "K": 14, "L": 13, "M": 13, "N": 14, "O": 14, "P": 14, "Q": 44})
    if is_atd:
        ws.column_dimensions["R"].width = 36
    ws.print_title_rows = "1:7"


def _write_arrivals_48h_sheet(ws, rows: list[dict[str, Any]], as_of: date) -> None:
    target_date = as_of + timedelta(days=2)
    _title(ws, f"Vessels Arriving in 48 Hours â€” ETA {target_date.isoformat()}", 16)
    ws.merge_cells("A2:N2")
    ws["A2"] = "Rule: ETA is exactly two calendar days after the as-of date. Only schedules with at least one filtered booking are included."
    ws["A2"].fill = PatternFill("solid", fgColor=PALE_GOLD)
    ws["A2"].font = Font(name="Aptos", size=10, italic=True, color=AMBER)
    ws["A2"].alignment = Alignment(wrap_text=True)
    ws["O2"] = "Target ETA"
    ws["P2"] = target_date
    for cell in (ws["O2"], ws["P2"]):
        cell.fill = PatternFill("solid", fgColor=PALE_TEAL)
        cell.font = Font(name="Aptos", size=10, bold=True, color=NAVY)
        cell.alignment = Alignment(horizontal="center")
    ws["P2"].number_format = "yyyy-mm-dd"

    data_last = max(8, 7 + len(rows))
    for merged_range in ("A4:C4", "E4:G4", "I4:K4"):
        ws.merge_cells(merged_range)
    _write_kpi(ws, "A4", "D4", "Arrival Schedule Legs", f'=COUNTA($A$8:$A${data_last})')
    _write_kpi(ws, "E4", "H4", "Booking Exposures", f'=SUM($O$8:$O${data_last})')
    _write_kpi(ws, "I4", "L4", "MAS-linked Legs", f'=COUNTIF($P$8:$P${data_last},"YES")')
    ws.merge_cells("A5:P5")
    ws["A5"] = f"Source: {ACTIVTRACK_URL}"
    ws["A5"].font = Font(name="Aptos", size=9, color="2962A3")

    headers = ["Schedule Ref", "Route Type", "Leg No.", "Total Legs", "Vessel Name", "Voyage", "POL", "POD", "ETD", "ATD", "ETA", "ATA", "Arrival Status", "Booking References", "Booking Count", "MAS Booking"]
    for col, header in enumerate(headers, start=1):
        ws.cell(7, col, header)
    _style_header(ws, 7, 1, 16)

    for index, record in enumerate(rows, start=8):
        values = [
            _excel_text(record["schedule_ref"]), record["route_type"], record["leg_no"], record["total_legs"],
            _excel_text(record["vessel_name"]), _excel_text(record["voyage"]), _excel_text(record["pol"]),
            _excel_text(record["pod"]), record["etd"], record["atd"], record["eta"], record["ata"],
            "ARRIVED" if record["ata"] else "DUE IN 48H", _excel_text(", ".join(record["booking_refs"])),
            record["booking_count"], "YES" if record["has_mas_booking"] else "NO",
        ]
        for col, value in enumerate(values, start=1):
            ws.cell(index, col, value)

    last_row = 7 + len(rows)
    if rows:
        _add_table(ws, f"A7:P{last_row}", "Arrivals48HoursTable")
        ws.conditional_formatting.add(f"A8:P{last_row}", FormulaRule(formula=['$B8="TRANSSHIPMENT"'], fill=PatternFill("solid", fgColor=TRANS_FILL)))
        for row_no in range(8, last_row + 1):
            for col_no in (9, 10, 11, 12):
                ws.cell(row_no, col_no).number_format = "yyyy-mm-dd"
            for col_no in range(1, 17):
                ws.cell(row_no, col_no).font = Font(name="Aptos", size=10, color=TEXT)
                ws.cell(row_no, col_no).alignment = Alignment(vertical="center", wrap_text=(col_no == 14))
        for row_no, record in enumerate(rows, start=8):
            if record["has_mas_booking"]:
                ws.cell(row_no, 5).font = Font(name="Aptos", size=10, bold=True, color=MAS_RED)
    else:
        ws["B8"] = "No booked vessel legs have an ETA exactly 48 hours after the as-of date."
        ws["B8"].font = Font(name="Aptos", size=10, italic=True, color=GREEN)

    ws.freeze_panes = "C8"
    ws.sheet_view.showGridLines = False
    _set_widths(ws, {"A": 15, "B": 18, "C": 10, "D": 10, "E": 29, "F": 14, "G": 29, "H": 29, "I": 13, "J": 13, "K": 13, "L": 13, "M": 16, "N": 44, "O": 14, "P": 13})
    ws.print_title_rows = "1:7"


def _write_summary(ws, source: dict[str, Any], all_legs: list[dict[str, Any]], as_of: date, detail_last_row: int, atd_count: int, ata_count: int, arrival_48h_count: int) -> int:
    _title(ws, "ActivTrack Vessel, Voyage & Schedule Summary", 18)
    vessel_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    vessel_booking_refs: dict[str, set[str]] = defaultdict(set)
    for leg in all_legs:
        vessel_rows[leg["vessel_name"]].append(leg)
        vessel_booking_refs[leg["vessel_name"]].update(leg["booking_refs"])
    vessel_names = sorted(vessel_rows, key=lambda name: (-len(vessel_booking_refs[name]), name.lower()))

    hierarchy: list[dict[str, Any]] = []
    for vessel in vessel_names:
        rows = vessel_rows[vessel]
        voyages = sorted({str(row["voyage"]) for row in rows if row["voyage"]})
        hierarchy.append({"type": "vessel", "vessel_name": vessel, "voyages": ", ".join(voyages), "booking_refs": vessel_booking_refs[vessel], "has_mas_booking": any(row["has_mas_booking"] for row in rows)})
        rows.sort(key=lambda item: (item["etd"] or date.max, item["schedule_ref"], item["leg_no"]))
        hierarchy.extend({**row, "type": "schedule"} for row in rows)

    for merged_range in ("A3:C3", "E3:G3", "I3:K3", "M3:O3", "A4:C4", "E4:G4", "I4:K4", "M4:O4"):
        ws.merge_cells(merged_range)
    _write_kpi(ws, "A3", "D3", "Reportable Bookings", f"=SUM('Booking Vessel Details'!$V$2:$V${detail_last_row})")
    _write_kpi(ws, "E3", "H3", "Vessel Schedules", f"=SUM('Booking Vessel Details'!$W$2:$W${detail_last_row})")
    _write_kpi(ws, "I3", "L3", "Vessels", f'=COUNTIF($D$11:$D${10 + len(hierarchy)},"VESSEL TOTAL")')
    _write_kpi(ws, "M3", "P3", "Transshipment Bookings", f'=SUMIFS(\'Booking Vessel Details\'!$V$2:$V${detail_last_row},\'Booking Vessel Details\'!$F$2:$F${detail_last_row},"transshipment")')
    _write_kpi(ws, "A4", "D4", "Direct Bookings", f'=SUMIFS(\'Booking Vessel Details\'!$V$2:$V${detail_last_row},\'Booking Vessel Details\'!$F$2:$F${detail_last_row},"direct")')
    atd_last = max(8, 7 + atd_count)
    ata_last = max(8, 7 + ata_count)
    _write_kpi(ws, "E4", "H4", "ATD Missing Legs", f"=COUNTA('ATD Missing'!$A$8:$A${atd_last})")
    _write_kpi(ws, "I4", "L4", "ATA Missing Legs", f"=COUNTA('ATA Missing'!$A$8:$A${ata_last})")
    _write_kpi(ws, "M4", "P4", "Schedule Legs", f'=COUNTIF($D$11:$D${10 + len(hierarchy)},"<>VESSEL TOTAL")')
    arrival_last = max(8, 7 + arrival_48h_count)
    _write_kpi(ws, "Q3", "R3", "48HR Arrivals", f"=COUNTA('48HR Arrivals'!$A$8:$A${arrival_last})")
    ws["Q4"] = "As of Date"
    ws["R4"] = as_of
    for cell in (ws["Q4"], ws["R4"]):
        cell.fill = PatternFill("solid", fgColor=PALE_GOLD)
        cell.font = Font(name="Aptos", size=10, bold=True, color=AMBER)
        cell.alignment = Alignment(horizontal="center")
    ws["R4"].number_format = "yyyy-mm-dd"

    ws.merge_cells("A6:R6")
    ws["A6"] = _filter_caption(source)
    ws["A6"].fill = PatternFill("solid", fgColor=PALE_GOLD)
    ws["A6"].font = Font(name="Aptos", size=10, bold=True, color=AMBER)
    ws["A6"].border = _thin_border()
    ws.merge_cells("A7:R7")
    missing_count = int((source.get("totals") or {}).get("incomplete_bookings") or 0)
    if missing_count:
        ws["A7"] = (
            f"Attention: {missing_count} filtered booking(s) have no usable vessel schedule. "
            "They are excluded from vessel/date totals and listed on the red Missing Schedules sheet."
        )
        ws["A7"].fill = PatternFill("solid", fgColor=PALE_RED)
        ws["A7"].font = Font(name="Aptos", size=10, bold=True, color=RED)
    else:
        ws["A7"] = "Dark teal rows are vessel totals. Rows immediately below show that vessel's schedules and legs. Transshipment legs are highlighted light orange."
        ws["A7"].fill = PatternFill("solid", fgColor=PALE_GRAY)
        ws["A7"].font = Font(name="Aptos", size=10, italic=True, color=MUTED)
    ws["A7"].alignment = Alignment(wrap_text=True)
    ws.merge_cells("A8:R8")
    ws["A8"] = f"Source: {ACTIVTRACK_URL}"
    ws["A8"].font = Font(name="Aptos", size=9, color="2962A3")

    headers = ["Vessel Name", "Schedule Ref", "Voyage", "Route Type", "Leg No.", "Total Legs", "POL", "POD", "ETD", "ATD", "ATD Status", "ETA", "ATA", "ATA Status", "Booking Count", "Direct Bookings", "T/S Bookings", "Schedule Count"]
    for col, header in enumerate(headers, start=1):
        ws.cell(10, col, header)
    _style_header(ws, 10, 1, 18)

    for index, row in enumerate(hierarchy, start=11):
        if row["type"] == "vessel":
            ws.append([
                _excel_text(row["vessel_name"]), "VESSEL TOTAL", _excel_text(row["voyages"]), "VESSEL TOTAL", None, None, None, None, None, None,
                "SEE BELOW", None, None, "SEE BELOW",
                f'=SUMIFS(\'Booking Vessel Details\'!$U$2:$U${detail_last_row},\'Booking Vessel Details\'!$I$2:$I${detail_last_row},A{index})',
                f'=SUMIFS(\'Booking Vessel Details\'!$U$2:$U${detail_last_row},\'Booking Vessel Details\'!$I$2:$I${detail_last_row},A{index},\'Booking Vessel Details\'!$F$2:$F${detail_last_row},"direct")',
                f'=SUMIFS(\'Booking Vessel Details\'!$U$2:$U${detail_last_row},\'Booking Vessel Details\'!$I$2:$I${detail_last_row},A{index},\'Booking Vessel Details\'!$F$2:$F${detail_last_row},"transshipment")',
                f'=SUMIFS(\'Booking Vessel Details\'!$X$2:$X${detail_last_row},\'Booking Vessel Details\'!$I$2:$I${detail_last_row},A{index})',
            ])
            for cell in ws[index]:
                cell.fill = PatternFill("solid", fgColor=TEAL)
                cell.font = Font(name="Aptos", size=10, bold=True, color=WHITE)
            if row["has_mas_booking"]:
                ws.cell(index, 1).font = Font(name="Aptos", size=10, bold=True, color=MAS_RED)
        else:
            ws.append([
                _excel_text(row["vessel_name"]), f"â†³ {_excel_text(row['schedule_ref'])}", _excel_text(row["voyage"]), row["route_type"],
                row["leg_no"], row["total_legs"], _excel_text(row["pol"]), _excel_text(row["pod"]), row["etd"], row["atd"],
                f'=IF(J{index}<>"","UPDATED",IF(I{index}<=$R$4,"MISSING","PENDING"))', row["eta"], row["ata"],
                f'=IF(M{index}<>"","UPDATED",IF(L{index}<=$R$4,"MISSING","PENDING"))',
                f'=COUNTIFS(\'Booking Vessel Details\'!$E$2:$E${detail_last_row},SUBSTITUTE(B{index},"â†³ ",""),\'Booking Vessel Details\'!$I$2:$I${detail_last_row},A{index},\'Booking Vessel Details\'!$H$2:$H${detail_last_row},E{index})',
                f'=IF(D{index}="DIRECT",O{index},0)', f'=IF(D{index}="TRANSSHIPMENT",O{index},0)', "",
            ])
            if row["has_mas_booking"]:
                ws.cell(index, 1).font = Font(name="Aptos", size=10, bold=True, color=MAS_RED)
    last_row = 10 + len(hierarchy)
    _add_table(ws, f"A10:R{last_row}", "VesselScheduleBreakdownTable")
    ws.conditional_formatting.add(f"A11:R{last_row}", FormulaRule(formula=['$D11="TRANSSHIPMENT"'], fill=PatternFill("solid", fgColor=TRANS_FILL)))
    _status_conditional_formats(ws, [f"K11:K{last_row}", f"N11:N{last_row}"])
    for row_no in range(11, last_row + 1):
        for col_no in (9, 10, 12, 13):
            ws.cell(row_no, col_no).number_format = "yyyy-mm-dd"
        for col_no in range(1, 19):
            ws.cell(row_no, col_no).alignment = Alignment(vertical="center", wrap_text=(col_no == 3))
    ws.freeze_panes = "C11"
    ws.sheet_view.showGridLines = False
    _set_widths(ws, {"A": 29, "B": 17, "C": 21, "D": 19, "E": 10, "F": 10, "G": 27, "H": 27, "I": 13, "J": 13, "K": 14, "L": 13, "M": 13, "N": 14, "O": 14, "P": 15, "Q": 14, "R": 14})
    ws.print_title_rows = "1:10"
    return len(vessel_names)


def build_report(source: dict[str, Any], output_path: str | Path, as_of: date | None = None) -> ReportStats:
    """Validate normalized data and write the operational Excel report."""
    as_of = as_of or date.today()
    normalized = normalize_source(source)
    detail_records, all_legs, _ = _build_records(normalized)
    if not detail_records:
        raise ReportDataError("The selected filter produced no booking-leg rows.")

    atd_missing = sorted(
        [item for item in all_legs if item["etd"] and item["etd"] <= as_of and not item["atd"]],
        key=lambda item: (item["etd"], item["vessel_name"]),
    )
    ata_missing = sorted(
        [item for item in all_legs if item["eta"] and item["eta"] <= as_of and not item["ata"]],
        key=lambda item: (item["eta"], item["vessel_name"]),
    )
    arrival_target = as_of + timedelta(days=2)
    arriving_48h = sorted(
        [item for item in all_legs if item["booking_count"] > 0 and item["eta"] == arrival_target],
        key=lambda item: (item["eta"], item["vessel_name"], item["schedule_ref"], item["leg_no"]),
    )

    wb = Workbook()
    ws_summary = wb.active
    ws_summary.title = "Vessel Summary"
    write_console_sheet(wb, source)
    ws_details = wb.create_sheet("Booking Vessel Details")
    ws_atd = wb.create_sheet("ATD Missing")
    ws_ata = wb.create_sheet("ATA Missing")
    ws_arrivals = wb.create_sheet("48HR Arrivals")
    if normalized["incomplete_bookings"]:
        ws_incomplete = wb.create_sheet("Missing Schedules")
        _write_incomplete_bookings_sheet(ws_incomplete, normalized["incomplete_bookings"])
    detail_last_row = _write_details(ws_details, detail_records)
    _write_missing_sheet(ws_atd, atd_missing, "ATD", as_of, detail_last_row)
    _write_missing_sheet(ws_ata, ata_missing, "ATA", as_of, detail_last_row)
    _write_arrivals_48h_sheet(ws_arrivals, arriving_48h, as_of)
    vessel_count = _write_summary(ws_summary, normalized, all_legs, as_of, detail_last_row, len(atd_missing), len(ata_missing), len(arriving_48h))

    try:
        wb.calculation.fullCalcOnLoad = True
        wb.calculation.forceFullCalc = True
        wb.calculation.calcMode = "auto"
    except AttributeError:
        pass
    wb.active = 0

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    from container_history import add_history_sheet
    add_history_sheet(wb,source)
    color_report_rows(wb, source)
    wb.save(output)
    return ReportStats(
        output_path=output,
        bookings=len(normalized["bookings"]),
        schedules=len(normalized["schedules"]),
        vessels=vessel_count,
        schedule_legs=len(all_legs),
        detail_rows=len(detail_records),
        atd_missing_legs=len(atd_missing),
        ata_missing_legs=len(ata_missing),
        arriving_48h_legs=len(arriving_48h),
        missing_schedule_bookings=len(normalized["incomplete_bookings"]),
    )
