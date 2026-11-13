"""Shared, user-defined LCL agent classification for every report."""
import re

CONSOLE_FILL = "E4D7F5"
COLOAD_FILL = "DDEBF7"
CONSOLE_AGENTS = (
    "VAST TRUST INTERNATIONAL FORWARDING CO.,LTD",
    "ECU WORLDWIDE (HONG KONG) LIMITED",
    "BARSAN GLOBAL LOGISTICS",
    "PORTFOCUS LOGISTICS INDIA PVT LTD",
    "CYTS-SPIRIT LOGISTICS LIMITED",
    "ECU WORLDWIDE {THAILAND} CO LTD",
    "ECU WORLDWIDE (JAPAN) LTD",
    "ECU WORLDWIDE HAIPHONG",
    "ECU WORLDWIDE KOREA CO.,LTD",
    "ORIENTAL POWER LOGISTICS CO LTD",
)

def normalized_name(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("label") or ""
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

AGENT_KEYS = {normalized_name(name) for name in CONSOLE_AGENTS}

def agent_name(booking):
    value = booking.get("agent_name") or booking.get("agent") or ""
    return str(value.get("name") or "") if isinstance(value, dict) else str(value)

def booking_class(booking):
    if normalized_name(booking.get("shipment_type")) != "LCL":
        return ""
    agent = agent_name(booking)
    if normalized_name(agent) in AGENT_KEYS:
        return "CONSOLE"
    if agent.strip() or booking.get("liner_co_loader_name"):
        return "CO-LOAD"
    return "UNCLASSIFIED LCL"

def schedule_classes(bookings):
    result = {}
    for booking in bookings:
        kind = booking_class(booking)
        if kind in {"CONSOLE", "CO-LOAD"}:
            for key in (booking.get("vessel_schedule_reference_number"), booking.get("vessel_schedule_id")):
                if key:
                    result.setdefault(str(key), set()).add(kind)
    return result

def color_report_rows(workbook, source):
    """Color exact booking rows first, otherwise exact VS rows; preserve font alerts."""
    from openpyxl.styles import PatternFill
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.utils import get_column_letter
    by_booking = {str(b.get("reference_number")): booking_class(b) for b in source.get("bookings", [])}
    by_schedule = schedule_classes(source.get("bookings", []))
    for schedule in source.get("schedules", []):
        kinds = by_schedule.get(str(schedule.get("id")), set())
        if kinds and schedule.get("reference_number"):
            by_schedule.setdefault(str(schedule["reference_number"]), set()).update(kinds)
    for sheet in workbook:
        if sheet.title in {"Container History", "Report Info", "RUN INFO"}:
            continue
        colored_ranges = {}
        for row in sheet:
            kinds = set()
            texts = [str(c.value or "").strip() for c in row[:8]]
            exact = [by_booking[t] for t in texts if t in by_booking]
            if exact:
                kinds.update(exact)
            else:
                for text in texts:
                    match = re.search(r"\bVS-\d+\b", text)
                    if match:
                        kinds.update(by_schedule.get(match.group(), set()))
            color = CONSOLE_FILL if "CONSOLE" in kinds else COLOAD_FILL if "CO-LOAD" in kinds else None
            if color:
                # ATD's VS ID cell retains its independent container-status colour.
                first_column = 2 if sheet.title == "ATD Missing" else 1
                for cell in row[first_column - 1:]:
                    cell.fill = PatternFill("solid", fgColor=color)
                colored_ranges.setdefault(color, []).append(f"{get_column_letter(first_column)}{row[0].row}:{get_column_letter(sheet.max_column)}{row[0].row}")
        if colored_ranges and len(sheet.conditional_formatting):
            for conditional_range in sheet.conditional_formatting:
                for rule in sheet.conditional_formatting[conditional_range]:
                    rule.priority += 2
            for color, ranges in colored_ranges.items():
                rule = FormulaRule(formula=["TRUE"], fill=PatternFill("solid", fgColor=color))
                rule.priority = 1
                sheet.conditional_formatting.add(" ".join(ranges), rule)

def write_console_sheet(workbook, source, index=1):
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    # Live captures use transshipments[].vessel objects; test/older captures
    # may use legs[].vessel_name. Reuse the report's canonical leg resolver.
    from report_builder import normalize_source
    normalized = normalize_source(source)
    booking_links = {b['reference_number']: b for b in normalized['bookings']}
    sheet = workbook.create_sheet("Console & Co-load", index)
    headers = ["VS ID", "Vessel Name", "Voyage", "Classification", "Booking", "Agent", "Consignee", "HBL", "MBL", "Container", "SO", "POL", "POD", "ETD", "ETA", "Leg"]
    sheet.append(headers)
    schedules = {}
    for s in normalized['schedules']:
        for key in (s.get("id"), s.get("reference_number")):
            if key is not None:
                schedules[str(key)] = s
    def text(v):
        if isinstance(v, dict):
            v = v.get("name") or ""
        v = str(v or "")
        return "'" + v if v.startswith(("=", "+", "-", "@")) else v
    bookings = sorted(source.get("bookings", []), key=lambda b: (0 if booking_class(b) == "CONSOLE" else 1, agent_name(b), str(b.get("vessel_schedule_reference_number") or ""), str(b.get("reference_number") or "")))
    for b in bookings:
        kind = booking_class(b)
        if not kind:
            continue
        link = booking_links.get(str(b.get('reference_number')), b)
        s = schedules.get(str(link.get("vessel_schedule_id"))) or schedules.get(str(link.get("vessel_schedule_reference_number"))) or {}
        legs = s.get("legs") or [{}]
        for n, leg in enumerate(legs, 1):
            sheet.append([text(v) for v in (
                s.get("reference_number") or b.get("vessel_schedule_reference_number"),
                leg.get("vessel_name") or "Schedule not captured", leg.get("voyage") or "Not recorded", kind, b.get("reference_number"),
                agent_name(b), b.get("customer_name"), b.get("hbl_number"), b.get("mbl_number"),
                b.get("container_number"), b.get("so_number"), leg.get("departure_port"),
                leg.get("arrival_port"), leg.get("etd"), leg.get("eta"), f"{n}/{len(legs)}",
            )])
            for cell in sheet[sheet.max_row]:
                cell.font = Font(name="Calibri", size=10, bold=cell.column in (1,2,4), color="17365D")
                cell.alignment = Alignment(vertical="center", wrap_text=True)
                cell.fill = PatternFill("solid", fgColor=CONSOLE_FILL if kind == "CONSOLE" else COLOAD_FILL if kind == "CO-LOAD" else "FFF2CC")
            sheet.row_dimensions[sheet.max_row].height = 30
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="17365D")
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
    for i, width in enumerate([15,29,17,19,15,48,40,23,24,28,23,27,27,14,14,8],1):
        sheet.column_dimensions[get_column_letter(i)].width = width
    sheet.row_dimensions[1].height = 26
    sheet.freeze_panes = "C2"
    sheet.auto_filter.ref = f"A1:P{sheet.max_row}"
    sheet.sheet_view.showGridLines = False
    sheet.sheet_properties.tabColor = "7030A0"
    return sheet
