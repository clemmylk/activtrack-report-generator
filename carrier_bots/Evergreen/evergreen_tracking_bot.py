"""Evergreen / ShipmentLink timetable collector and ActiveTrack comparator.

Put vessel names into vessels.txt, one per line, then run:
    py evergreen_tracking_bot.py

The bot reads only the public ShipmentLink vessel timetable in Edge.  It finds
the newest ActivTrack_All_Vessels_*_Source.json automatically and writes
source-backed schedule differences to updates_only.csv.  It never updates
ActiveTrack itself.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "vessels.txt"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
SHIPMENTLINK_URL = "https://ss.shipmentlink.com/tvs2/jsp/TVS2_VesselSchedule.jsp"
ACTIVTRACK_REPORTS = (Path.home() / "Documents" / "ActivTrack Reports")

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.support.ui import Select, WebDriverWait
except ModuleNotFoundError:
    tracker_python = ROOT.parents[1] / "vessel-watch" / ".venv" / "Scripts" / "python.exe"
    if tracker_python.exists() and Path(sys.executable).resolve() != tracker_python.resolve():
        result = subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(result.returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")


def normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().upper()


def voyage_key(value: str | None) -> str:
    """Match ShipmentLink's full voyage with ActiveTrack's shortened voyage."""
    text = normal(value)
    suffix = re.search(r"(\d+[NSEW])$", text)
    return suffix.group(1) if suffix else text


def port_key(value: str | None) -> str:
    text = normal(value)
    aliases = {
        "HONG KONG": "HONG KONG", "KAOHSIUNG": "KAOHSIUNG", "SHEKOU": "SHEKOU",
        "SINGAPORE": "SINGAPORE", "SHANGHAI": "SHANGHAI", "NINGBO": "NINGBO",
        "XIAMEN": "XIAMEN", "SURABAYA": "SURABAYA", "SEMARANG": "SEMARANG",
        "JAKARTA": "JAKARTA", "HO CHI MINH": "HO CHI MINH", "COLOMBO": "COLOMBO",
        "FELIXSTOWE": "FELIXSTOWE", "LONDON GATEWAY": "LONDON GATEWAY",
        "ROTTERDAM": "ROTTERDAM", "HAMBURG": "HAMBURG",
        "LAEM CHABANG": "LAEM CHABANG",
    }
    return next((key for token, key in aliases.items() if token in text), text)


def vessel_list() -> list[str]:
    override = os.environ.get("TRACKING_VESSELS")
    if not override and not INPUT.exists():
        raise SystemExit("Missing vessels.txt")
    output, seen = [], set()
    lines = override.splitlines() if override else INPUT.read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        vessel = normal(line)
        if vessel and not vessel.startswith("#") and vessel not in seen:
            output.append(vessel)
            seen.add(vessel)
    if not output:
        raise SystemExit("Add at least one vessel name to vessels.txt")
    return output


def open_edge() -> webdriver.Edge:
    profile = ROOT / "edge_profile"
    profile.mkdir(exist_ok=True)
    options = Options()
    if os.environ.get("TRACKING_HEADLESS") == "1":
        options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--window-size=1500,1000")
    options.page_load_strategy = "eager"
    return webdriver.Edge(options=options, service=Service())


def choose_vessel(driver: webdriver.Edge, vessel: str) -> bool:
    select_element = driver.find_element(By.ID, "vslCode")
    select = Select(select_element)
    matches = [(option.text, option.get_attribute("value")) for option in select.options if normal(option.text) == vessel]
    if len(matches) != 1:
        return False
    # ShipmentLink occasionally rebuilds the long dropdown while Selenium's
    # Select helper is applying a choice. Dispatching the standard change event
    # on the fresh element avoids that stale-option race.
    driver.execute_script("arguments[0].value=arguments[1]; arguments[0].dispatchEvent(new Event('change', {bubbles:true}));", select_element, matches[0][1])
    # Query the button in the browser at click time; the site sometimes redraws
    # the form immediately after the select change.
    driver.execute_script("document.getElementById('submitButton').click();")
    expected = "THE VESSEL SCHEDULES OF " + vessel
    WebDriverWait(driver, 20).until(lambda page: expected in normal(page.find_element(By.TAG_NAME, "body").text))
    return True


def extract_timetable(driver: webdriver.Edge, vessel: str) -> tuple[str | None, list[dict]]:
    """Read each displayed port/date grid, removing repeated nested tables."""
    payload = driver.execute_script(r"""
const vessel = arguments[0];
const clean = value => (value.innerText || value.textContent || '').replace(/\s+/g, ' ').trim();
const escapedVessel = vessel.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const voyageSource = escapedVessel + '\\s+([A-Z0-9]*\\d[A-Z0-9]*(?:-[A-Z0-9]+)*[NSEW])';
const voyageBefore = element => {
  // ShipmentLink can show both eastbound and westbound timetables on one page.
  // Read the closest voyage heading before this specific timetable row instead
  // of assigning the page's first voyage to every grid.
  const range = document.createRange();
  range.selectNodeContents(document.body);
  range.setEndBefore(element);
  const matches = [...range.toString().matchAll(new RegExp(voyageSource, 'gi'))];
  return matches.length ? matches[matches.length - 1][1].toUpperCase() : null;
};
const grids = [], seen = new Set();
for (const table of document.querySelectorAll('table')) {
  const rows = [...table.rows].map(row => [...row.cells].map(cell => clean(cell)));
  for (let index = 1; index < rows.length; index++) {
    const ports = rows[index - 1].slice(1);
    const dates = rows[index].slice(1);
    if (!/ARR\s*DEP/i.test(rows[index][0] || '') || !ports.length || ports.length !== dates.length) continue;
    if (!ports.some(port => port) || !dates.some(value => /\d{1,2}\/\d{1,2}/.test(value))) continue;
    const voyage = voyageBefore(table.rows[index - 1]);
    const key = JSON.stringify([voyage, ports, dates]);
    if (!seen.has(key)) { seen.add(key); grids.push({voyage, ports, dates}); }
  }
}
return {text: document.body.innerText, grids};
""", vessel)
    # Require a digit: this prevents matching the next word in prose such as
    # "EVER OBEY are ..." as though it were a voyage.
    pattern = re.compile(re.escape(vessel) + r"\s+([A-Z0-9]*\d[A-Z0-9]*(?:-[A-Z0-9]+)*[NSEW])", re.I)
    match = pattern.search(payload["text"])
    fallback_voyage = normal(match.group(1)) if match else None
    rows, voyages = [], []
    for grid in payload["grids"]:
        voyage = normal(grid.get("voyage")) or fallback_voyage
        if voyage and voyage not in voyages:
            voyages.append(voyage)
        for port, pair in zip(grid["ports"], grid["dates"]):
            dates = re.findall(r"\d{1,2}/\d{1,2}", pair)
            if port and dates:
                rows.append({"carrier_vessel": vessel, "carrier_voyage": voyage, "port": normal(port),
                             "arrival_mmdd": dates[0], "departure_mmdd": dates[1] if len(dates) > 1 else None,
                             "source_url": driver.current_url})
    return ", ".join(voyages) or fallback_voyage, rows


def latest_activtrack_file() -> Path | None:
    override = Path(os.environ["ACTIVTRACK_SOURCE_FILE"]) if os.environ.get("ACTIVTRACK_SOURCE_FILE") else None
    if override and override.is_file():
        return override
    files = list(ACTIVTRACK_REPORTS.glob("ActivTrack_Vessel_Booking_Report_*_Source.json"))
    if not files:
        files = list(ACTIVTRACK_REPORTS.glob("ActivTrack_All_Vessels_*_Source.json"))
    return max(files, key=lambda path: path.stat().st_mtime) if files else None


def schedules_from_source(data: dict) -> list[dict]:
    if data.get("schedules"):
        return data["schedules"]
    schedules: dict[str, dict] = {}
    for booking in data.get("bookings") or []:
        schedule = (booking or {}).get("vessel_schedule")
        if not schedule:
            continue
        key = str(schedule.get("id") or schedule.get("reference_number") or "")
        if key and key not in schedules:
            schedules[key] = schedule
    return list(schedules.values())


def month_day_to_target(mmdd: str | None, target: str | None) -> str | None:
    if not mmdd:
        return None
    month, day = map(int, mmdd.split("/"))
    base = datetime.fromisoformat(target).date() if target else date.today()
    candidates = [date(year, month, day) for year in (base.year - 1, base.year, base.year + 1)]
    return min(candidates, key=lambda candidate: abs((candidate - base).days)).isoformat()


def nearest(rows: list[dict], at_date: str | None, field: str) -> dict | None:
    if not rows:
        return None
    candidate_dates = [(row, month_day_to_target(row[field], at_date)) for row in rows]
    candidate_dates = [(row, value) for row, value in candidate_dates if value]
    if not candidate_dates:
        return None
    if not at_date:
        return candidate_dates[0][0] if len(candidate_dates) == 1 else None
    target = datetime.fromisoformat(at_date).date()
    row, value = min(candidate_dates, key=lambda item: abs((datetime.fromisoformat(item[1]).date() - target).days))
    return row if abs((datetime.fromisoformat(value).date() - target).days) <= 60 else None


def compare_with_activtrack(carrier_rows: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    source = latest_activtrack_file()
    if not source:
        return [], [], None
    data = json.loads(source.read_text(encoding="utf-8"))
    matched, updates = [], []
    for schedule in schedules_from_source(data):
        for leg in schedule.get("transshipments") or []:
            vessel_data = leg.get("vessel") or {}
            vessel, voyage = normal(vessel_data.get("name")), voyage_key(leg.get("voyage"))
            if not vessel or not voyage:
                continue
            base = {"vs_id": schedule.get("reference_number"), "leg": leg.get("leg_number"), "vessel": vessel_data.get("name"), "voyage": leg.get("voyage"),
                    "pol": (leg.get("departure_port") or {}).get("name"), "pod": (leg.get("arrival_port") or {}).get("name")}
            # ShipmentLink may include a service/rotation prefix (1416-027E)
            # while ActiveTrack stores only the voyage suffix (027E). Keep the
            # direction in the key so 027E never matches the 027W timetable.
            exact = [row for row in carrier_rows if normal(row["carrier_vessel"]) == vessel and voyage_key(row["carrier_voyage"]) == voyage]
            etd = nearest([row for row in exact if port_key(row["port"]) == port_key(base["pol"]) and row["departure_mmdd"]], leg.get("etd"), "departure_mmdd")
            eta = nearest([row for row in exact if port_key(row["port"]) == port_key(base["pod"]) and row["arrival_mmdd"]], leg.get("eta"), "arrival_mmdd")
            if not etd and not eta:
                continue
            row = {**base, "carrier_voyage": (etd or eta)["carrier_voyage"], "at_etd": leg.get("etd"),
                   "carrier_etd": month_day_to_target(etd["departure_mmdd"], leg.get("etd")) if etd else None,
                   "at_eta": leg.get("eta"), "carrier_eta": month_day_to_target(eta["arrival_mmdd"], leg.get("eta")) if eta else None,
                   "source_url": (etd or eta)["source_url"]}
            matched.append(row)
            if (etd and not leg.get("atd") and row["carrier_etd"] != leg.get("etd")) or (eta and not leg.get("ata") and row["carrier_eta"] != leg.get("eta")):
                updates.append(row)
    return updates, matched, str(source)


def save_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    vessels = vessel_list()
    OUT.mkdir(parents=True, exist_ok=True)
    driver, results, carrier_rows = open_edge(), [], []
    try:
        for number, vessel in enumerate(vessels, 1):
            print(f"[{number}/{len(vessels)}] {vessel}")
            record = {"vessel": vessel, "checked_at": datetime.now().isoformat(timespec="seconds"), "status": "OK", "voyage": None, "rows": []}
            for attempt in range(2):
                try:
                    # ShipmentLink replaces parts of the page after Submit. A
                    # clean page + one retry makes a stale select harmless.
                    driver.get(SHIPMENTLINK_URL)
                    WebDriverWait(driver, 20).until(lambda page: page.find_elements(By.ID, "vslCode"))
                    if not choose_vessel(driver, vessel):
                        record["status"] = "NOT_LISTED_ON_SHIPMENTLINK"
                    else:
                        record["voyage"], record["rows"] = extract_timetable(driver, vessel)
                        if not record["rows"]:
                            record["status"] = "NO_TIMETABLE_ROWS"
                    break
                except Exception as error:
                    if attempt:
                        record["status"] = f"ERROR: {type(error).__name__}: {error}"
            results.append(record)
            carrier_rows.extend(record["rows"])
    finally:
        driver.quit()

    updates, matched, activtrack_source = compare_with_activtrack(carrier_rows)
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    fields = ["carrier_vessel", "carrier_voyage", "port", "arrival_mmdd", "departure_mmdd", "source_url"]
    save_csv(OUT / "timetable.csv", carrier_rows, fields)
    compare_fields = ["vs_id", "leg", "vessel", "voyage", "carrier_voyage", "pol", "pod", "at_etd", "carrier_etd", "at_eta", "carrier_eta", "source_url"]
    save_csv(OUT / "updates_only.csv", updates, compare_fields)
    save_csv(OUT / "matched_legs.csv", matched, compare_fields)
    (OUT / "comparison.json").write_text(json.dumps({"activtrack_source": activtrack_source, "updates_only": updates, "matched_legs": matched}, indent=2), encoding="utf-8")
    print(f"\nFinished. Results: {OUT}")
    print(f"Exact legs matched: {len(matched)} | Date changes to review: {len(updates)}")


if __name__ == "__main__":
    main()
