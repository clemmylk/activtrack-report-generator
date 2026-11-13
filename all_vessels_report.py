"""Separate all-vessel export for the desktop app's existing captured filter."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import re
import textwrap
from console_rules import write_console_sheet, color_report_rows
from typing import Any

# Keep the installed app's existing Excel engine and dependencies.
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.utils import get_column_letter

from report_builder import (
    normalize_source,
    _build_records,
    _excel_text,
    _filter_caption,
    _write_incomplete_bookings_sheet,
)

PURPLE = "7030A0"
DARK_GREEN = "006400"
NAVY = "17365D"
TEXT = "1F2933"
COLOMBO = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class AllVesselsStats:
    output_path: Path
    bookings: int
    vessels: int
    containers: int
    schedules: int
    legs: int
    in_transit_legs: int
    missing_schedule_bookings: int


def _containers(booking: dict) -> set[str]:
    numbers: set[str] = set()
    values = [booking.get("container_number"), booking.get("container_numbers")]
    for container in booking.get("containers") or []:
        values.append(container.get("container_number") or container.get("number") if isinstance(container, dict) else container)
    for value in values:
        parts = value if isinstance(value, list) else [value]
        for part in parts:
            # A count means identified containers, never TBA or an LCL booking count.
            numbers.update(re.findall(r"(?<![A-Z0-9])[A-Z]{4}[\s-]?\d{7}(?![A-Z0-9])", str(part or "").upper()))
    return {re.sub(r"[\s-]", "", number) for number in numbers}


def _is_msc(vessel: str, carrier: str) -> bool:
    return bool(re.search(r"\bMSC\b|MEDITERRANEAN SHIPPING", f"{vessel} {carrier}", re.I))


def _last_time(value: Any):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if stamp.tzinfo:
            stamp = stamp.astimezone(COLOMBO).replace(tzinfo=None)
        return stamp
    except (TypeError, ValueError):
        return _excel_text(value)


def _leg_status(leg: dict, as_of: date) -> str:
    if leg["ata"] and leg["ata"] <= as_of:
        return "ARRIVED"
    if leg["atd"] and leg["atd"] <= as_of:
        return "IN TRANSIT"
    if leg["etd"] and leg["etd"] <= as_of:
        return "DEPARTURE UNCONFIRMED"
    return "SCHEDULED" if leg["etd"] else "DATES MISSING"


def build_all_vessels_report(source: dict, output_path: str | Path, as_of: date | None = None) -> AllVesselsStats:
    """Readable vessel groups, with shipment identifiers on a separate filterable tab."""
    as_of = as_of or datetime.now(COLOMBO).date()
    normalized = normalize_source(source)
    _, legs, _ = _build_records(normalized)
    used_legs = [leg for leg in legs if leg["booking_count"]]
    raw_bookings = {str(b.get("reference_number")): b for b in source["bookings"]}
    raw_schedules = {str(s.get("reference_number")): s for s in source["schedules"]}
    notices = (source.get("notification_history") or {}).get("schedules") or {}
    all_containers = set().union(*(_containers(b) for b in source["bookings"]))
    def carrier_for(leg):
        raw = raw_schedules.get(leg["schedule_ref"], {})
        carrier = raw.get("carrier") or raw.get("carrier_name") or ""
        if isinstance(carrier, dict):
            carrier = carrier.get("name") or carrier.get("abbreviation") or ""
        return str(carrier or "Not specified")

    groups: dict[tuple[str, str], list[dict]] = {}
    for leg in used_legs:
        groups.setdefault((str(leg["vessel_name"]).strip().upper(), carrier_for(leg)), []).append(leg)
    vessel_names = {vessel for vessel, _ in groups} - {"", "TBA", "TBC", "(VESSEL NOT SPECIFIED)"}
    in_transit = sum(_leg_status(leg, as_of) == "IN TRANSIT" for leg in used_legs)
    wb = Workbook()
    ws = wb.active
    ws.title = "ALL VESSELS"
    details = wb.create_sheet("Shipment Details")
    info = wb.create_sheet("Report Info")
    if normalized["incomplete_bookings"]:
        missing_schedules = wb.create_sheet("Missing Schedules")
        _write_incomplete_bookings_sheet(missing_schedules, normalized["incomplete_bookings"])
    teal, pale_teal, pale_green, pale_purple = "216D7A", "E8F3F5", "EDF7EF", "EEE5F7"
    blue, border_color = "E6EDF5", "CBD5E1"
    bottom = Border(bottom=Side(style="hair", color=border_color))

    def banner(sheet, address, value, fill=NAVY, color="FFFFFF", size=11, height=None):
        sheet.merge_cells(address)
        cell = sheet[address.split(":")[0]]
        cell.value = _excel_text(value)
        cell.font = Font(name="Calibri", size=size, bold=True, color=color)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        for cells in sheet[address]:
            for item in cells:
                item.fill = PatternFill("solid", fgColor=fill)
        if height:
            sheet.row_dimensions[cell.row].height = height

    def style_sheet(sheet, widths, header_row=1):
        sheet.sheet_view.showGridLines = False
        sheet.sheet_view.zoomScale = 100
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A3
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        sheet.print_title_rows = f"1:{header_row}"
        sheet.sheet_properties.outlinePr.summaryRight = False
        sheet.sheet_properties.outlinePr.summaryBelow = False
        for column, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(column)].width = width

    def header(sheet, labels, row=1):
        for col, label in enumerate(labels, 1):
            cell = sheet.cell(row, col, label)
            cell.font = Font(name="Calibri", size=9, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = Border(right=Side(style="thin", color="FFFFFF"))
        sheet.row_dimensions[row].height = 24

    def row_style(sheet, row_no, values, trans=False, stripe=False, bold_cols=()):
        fill = pale_green if trans else (blue if stripe else "FFFFFF")
        for column, value in enumerate(values, 1):
            cell = sheet.cell(row_no, column, _excel_text(value) if isinstance(value, str) else value)
            cell.font = Font(name="Calibri", size=9, bold=column in bold_cols,
                             color=DARK_GREEN if trans else TEXT)
            cell.fill = PatternFill("solid", fgColor=fill)
            cell.alignment = Alignment(vertical="center", wrap_text=True)
            cell.border = bottom

    def status_box(cell, status, trans=False):
        fills = {"IN TRANSIT": "DDEBF7", "DEPARTURE UNCONFIRMED": "FFF2CC", "ARRIVED": "D9EAD3",
                 "SCHEDULED": "EEF0F3", "DATES MISSING": "FCE4D6"}
        cell.fill = PatternFill("solid", fgColor=fills[status])
        cell.font = Font(name="Calibri", size=8, bold=True, color=DARK_GREEN if trans else NAVY)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    def notice_for(leg):
        return notices.get(leg["schedule_ref"], {"status": "History not captured"})

    filters = (source.get("filter") or {}).get("api_filters") or {}
    filter_labels = {"eta_from": "ETA from", "eta_to": "to", "etd_from": "ETD from", "etd_to": "to",
                     "booking_confirmed_date_from": "Confirmed from", "booking_confirmed_date_to": "to",
                     "booking_received_date_from": "Received from", "booking_received_date_to": "to"}
    caption = "  |  ".join(f"{filter_labels.get(k, k)} {v}" for k, v in filters.items() if k != "deleted")
    filtered_total = int(normalized["totals"].get("filtered_bookings") or len(normalized["bookings"]))
    incomplete_total = len(normalized["incomplete_bookings"])
    if caption:
        caption += f"  |  {filtered_total} filtered / {len(normalized['bookings'])} reportable"
        if incomplete_total:
            caption += f" / {incomplete_total} missing schedules"
    else:
        caption = _filter_caption(normalized)
    info.sheet_view.showGridLines = False
    info.sheet_view.zoomScale = 100
    info.sheet_properties.tabColor = "A7B6C8"
    for column, width in enumerate([25, 16, 25, 16, 25, 16, 25, 16], 1):
        info.column_dimensions[get_column_letter(column)].width = width
    banner(info, "A1:H1", "ActivTrack All Vessels â€” Report Info", size=16, height=30)
    metrics = [
        (3, "A3", "B3", "VESSELS", len(vessel_names)),
        (3, "C3", "D3", "CONTAINERS", len(all_containers)),
        (3, "E3", "F3", "REPORTABLE BOOKINGS", len(normalized["bookings"])),
        (3, "G3", "H3", "VESSEL SCHEDULES", len({l["schedule_ref"] for l in used_legs})),
        (4, "A4", "B4", "IN-TRANSIT LEGS", in_transit),
        (4, "C4", "D4", "DEPARTURE UNCONFIRMED", sum(_leg_status(l, as_of) == "DEPARTURE UNCONFIRMED" for l in used_legs)),
        (4, "E4", "F4", "ARRIVED LEGS", sum(_leg_status(l, as_of) == "ARRIVED" for l in used_legs)),
        (4, "G4", "H4", "AS-OF DATE", as_of),
    ]
    for row, address, value_cell, label, value in metrics:
        cell = info[address]
        cell.value = label
        cell.fill = PatternFill("solid", fgColor=pale_teal)
        cell.font = Font(name="Calibri", size=9, bold=True, color=NAVY)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell = info[value_cell]
        cell.value = value
        cell.fill = PatternFill("solid", fgColor=teal)
        cell.font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.number_format = "dd mmm yy" if isinstance(value, date) else "#,##0"
        info.row_dimensions[row].height = 22
    banner(info, "A6:H6", f"Applied filter: {caption}", fill="FFF2CC", color="855A00", size=9, height=21)
    if incomplete_total:
        banner(
            info,
            "A7:H7",
            f"ATTENTION: {incomplete_total} filtered booking(s) have no usable vessel schedule. They are excluded from vessel/date totals and listed on the red Missing Schedules sheet.",
            fill="FDECEC",
            color="A61B1B",
            size=9,
            height=29,
        )
    else:
        banner(info, "A7:H7", "Use the CARRIER or VESSEL NAME filter arrows to narrow the list. Vessel ID is the ActiveTrack VS reference. Use + / âˆ’ to collapse a vessel group.", fill="F2F5F9", color=NAVY, size=9, height=21)
    banner(info, "A8:H8", "MSC: purple vessel names. Transshipments: dark-green text and pale-green rows. Notification timestamps use the basis shown in the last column.", fill="FFFFFF", color="52606D", size=9, height=21)
    main_headers = ["VESSEL ID", "VESSEL NAME", "CARRIER", "LAST NOTIFICATION DATE", "VOYAGE", "ROUTE TYPE", "LEG", "POL", "POD",
                    "ETD", "ATD", "ETA", "ATA", "STATUS", "BOOKINGS", "CONTAINERS", "NOTICE TYPE", "NOTICE RECORD", "TIMESTAMP BASIS"]
    main_widths = [12, 23, 20, 20, 11, 14, 7, 20, 20, 11, 11, 11, 11, 20, 9, 10, 18, 24, 18]
    style_sheet(ws, main_widths)
    header(ws, main_headers)
    ws.freeze_panes = "E2"
    ws.sheet_properties.tabColor = NAVY

    detail_headers = ["VESSEL ID", "VESSEL NAME", "CARRIER", "LAST NOTIFICATION DATE", "BOOKING", "HBL", "SO", "CUSTOMER", "VOYAGE", "ROUTE", "LEG", "POL", "POD",
                      "ETD", "ATD", "ETA", "ATA", "STATUS", "CONTAINER NUMBERS", "MBL NUMBER", "NOTICE TYPE", "NOTICE RECORD", "TIMESTAMP BASIS"]
    detail_widths = [12, 23, 20, 20, 12, 20, 20, 30, 11, 14, 7, 20, 20, 11, 11, 11, 11, 20, 28, 22, 18, 24, 18]
    style_sheet(details, detail_widths)
    header(details, detail_headers)
    details.freeze_panes = "E2"
    details.sheet_properties.tabColor = teal
    row_no, detail_no = 2, 2
    order = {"IN TRANSIT": 0, "DEPARTURE UNCONFIRMED": 1, "SCHEDULED": 2, "DATES MISSING": 3, "ARRIVED": 4}
    for (vessel, carrier), vessel_legs in sorted(groups.items()):
        vessel_legs.sort(key=lambda leg: (order[_leg_status(leg, as_of)], leg["etd"] or date.max, leg["schedule_ref"], leg["leg_no"]))
        refs = {ref for leg in vessel_legs for ref in leg["booking_refs"]}
        containers = set().union(*(_containers(raw_bookings.get(ref, {})) for ref in refs))
        msc = any(_is_msc(vessel, carrier_for(leg)) for leg in vessel_legs)
        group_fill, group_text = (pale_purple, PURPLE) if msc else (teal, "FFFFFF")
        schedule_count = len({l["schedule_ref"] for l in vessel_legs})
        leg_count = len(vessel_legs)
        times = [_last_time(notice_for(leg).get("timestamp")) for leg in vessel_legs]
        latest = max((stamp for stamp in times if isinstance(stamp, datetime)), default=None)
        group_values = ["VESSEL TOTAL", vessel, carrier, latest, "", f"{schedule_count} schedule{'s' if schedule_count != 1 else ''}", f"{leg_count} leg{'s' if leg_count != 1 else ''}",
                        "", "", None, None, None, None,
                        f"{sum(_leg_status(l, as_of) == 'IN TRANSIT' for l in vessel_legs)} in transit", len(refs), len(containers), "", "", ""]
        row_style(ws, row_no, group_values)
        for cell in ws[row_no]:
            cell.fill = PatternFill("solid", fgColor=group_fill)
            cell.font = Font(name="Calibri", size=9, bold=True, color=group_text)
            cell.border = Border(top=Side(style="medium", color="FFFFFF"), bottom=Side(style="thin", color=teal))
            cell.alignment = Alignment(vertical="center", horizontal="center" if cell.column in {4, 7, 15, 16} else "left", wrap_text=True)
        ws.cell(row_no, 4).number_format = "dd mmm yy hh:mm"
        ws.row_dimensions[row_no].height = max(22, max(len(textwrap.wrap(vessel, width=24)),len(textwrap.wrap(carrier,width=25))) * 12 + 4)
        row_no += 1
        first_detail_row = row_no
        for index, leg in enumerate(vessel_legs):
            trans = leg["route_type"].lower() == "transshipment"
            notice = notice_for(leg)
            timestamp = _last_time(notice.get("timestamp"))
            record = f"SENT / log {notice.get('log_id')}" if notice.get("status") == "SENT" else notice.get("status", "Unavailable")
            notice_type = str(notice.get("type") or "").replace("_", " ").upper()
            leg_containers = set().union(*(_containers(raw_bookings.get(ref, {})) for ref in leg["booking_refs"]))
            values = [leg["schedule_ref"], leg["vessel_name"], carrier, timestamp, leg["voyage"], leg["route_type"], f"{leg['leg_no']}/{leg['total_legs']}",
                      leg["pol"], leg["pod"], leg["etd"], leg["atd"], leg["eta"], leg["ata"], _leg_status(leg, as_of),
                      len(leg["booking_refs"]), len(leg_containers), notice_type, record, notice.get("time_basis", "")]
            row_style(ws, row_no, values, trans, index % 2 == 1, bold_cols={1, 2, 5})
            ws.cell(row_no, 2).alignment = Alignment(indent=1, vertical="center", wrap_text=True)
            for col in (4, 5, 6, 7, 10, 11, 12, 13, 15, 16):
                ws.cell(row_no, col).alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            for col in (10, 11, 12, 13):
                ws.cell(row_no, col).number_format = "dd mmm yy"
            ws.cell(row_no, 4).number_format = "dd mmm yy hh:mm"
            if _is_msc(leg["vessel_name"], carrier_for(leg)):
                ws.cell(row_no, 2).font = Font(name="Calibri", size=9, bold=True, color=PURPLE)
            status_box(ws.cell(row_no, 14), _leg_status(leg, as_of), trans)
            if notice.get("status") != "SENT":
                ws.cell(row_no, 18).fill = PatternFill("solid", fgColor="FFF2CC")
            lines = max(len(textwrap.wrap(str(values[col - 1] or ""), width=int(main_widths[col - 1]) - 2)) for col in (2, 3, 6, 8, 9, 14, 17, 18, 19))
            ws.row_dimensions[row_no].height = max(22, lines * 12 + 4)
            for ref in leg["booking_refs"]:
                booking = raw_bookings.get(ref, {})
                dvalues = [leg["schedule_ref"], leg["vessel_name"], carrier, timestamp, ref, str(booking.get("hbl_number") or ""), str(booking.get("so_number") or ""),
                           booking.get("customer_name", ""), leg["voyage"], leg["route_type"],
                           f"{leg['leg_no']}/{leg['total_legs']}", leg["pol"], leg["pod"], leg["etd"], leg["atd"], leg["eta"], leg["ata"],
                           _leg_status(leg, as_of), ", ".join(sorted(_containers(booking))), str(booking.get("mbl_number") or ""),
                           notice_type, record, notice.get("time_basis", "")]
                row_style(details, detail_no, dvalues, trans, detail_no % 2 == 0, bold_cols={1, 2, 5, 6})
                for col in (1, 2, 3, 5, 6, 7, 9, 19, 20):
                    details.cell(detail_no, col).alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
                for col in (14, 15, 16, 17):
                    details.cell(detail_no, col).number_format = "dd mmm yy"
                details.cell(detail_no, 4).number_format = "dd mmm yy hh:mm"
                details.cell(detail_no, 4).alignment = Alignment(horizontal="center", vertical="center")
                if _is_msc(leg["vessel_name"], carrier_for(leg)):
                    details.cell(detail_no, 2).font = Font(name="Calibri", size=9, bold=True, color=PURPLE)
                status_box(details.cell(detail_no, 18), _leg_status(leg, as_of), trans)
                lines = max(len(textwrap.wrap(str(value or ""), width=max(8, int(detail_widths[col - 1]) - 2)))
                            for col, value in enumerate(dvalues, 1))
                details.row_dimensions[detail_no].height = min(409, max(22, lines * 12 + 4))
                detail_no += 1
            row_no += 1
        ws.row_dimensions.group(first_detail_row, row_no - 1, outline_level=1, hidden=False)
    if detail_no > 2:
        table = Table(displayName="AllVesselShipmentDetails", ref=f"A1:W{detail_no - 1}")
        table.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=False)
        details.add_table(table)
    ws.print_options.horizontalCentered = True
    ws.auto_filter.ref = f"A1:S{row_no - 1}"
    ws.print_area = f"A1:S{row_no - 1}"
    details.print_area = f"A1:W{detail_no - 1}"
    wb.active = 0
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    from container_history import add_history_sheet
    add_history_sheet(wb,source)
    write_console_sheet(wb, source)
    color_report_rows(wb, source)
    wb.save(output)
    return AllVesselsStats(
        output,
        len(normalized["bookings"]),
        len(vessel_names),
        len(all_containers),
        len({leg["schedule_ref"] for leg in used_legs}),
        len(used_legs),
        in_transit,
        len(normalized["incomplete_bookings"]),
    )
