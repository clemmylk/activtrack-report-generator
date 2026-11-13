"""Wan Hai timetable collector.

Add vessel names to vessels.txt, one per line, then run:
    py wanhai_tracking_bot.py

The script opens Microsoft Edge, reads only the timetable visibly published by
Wan Hai, and writes a CSV and JSON result set.  It does not change ActiveTrack
or submit any carrier data.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "vessels.txt"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
WAN_HAI_URL = "https://www.wanhai.com/views/skd/SkdByVsl.xhtml?file_num=64794&top_file_num=64735&parent_id=64834"
ACTIVTRACK_REPORTS = (Path.home() / "Documents" / "ActivTrack Reports")

# Reuse the already-installed Selenium runtime from Vessel Watch when this
# script is started with ordinary ``py``.  This keeps the two-file bot folder
# self-contained and avoids another installation step.
try:
    from selenium import webdriver
    from selenium.common.exceptions import NoAlertPresentException
    from selenium.webdriver.common.by import By
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.support.ui import Select, WebDriverWait
except ModuleNotFoundError:
    tracker_python = ROOT.parents[1] / "vessel-watch" / ".venv" / "Scripts" / "python.exe"
    if tracker_python.exists() and Path(sys.executable).resolve() != tracker_python.resolve():
        completed = subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]])
        raise SystemExit(completed.returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")


ALIASES = {"NAVIOS CHRYSAILS": "NAVIOS CHRYSALIS"}


def normal(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().upper()


def vessel_list() -> list[str]:
    override = os.environ.get("TRACKING_VESSELS")
    if not override and not INPUT.exists():
        raise SystemExit("Missing vessels.txt")
    names, seen = [], set()
    lines = override.splitlines() if override else INPUT.read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        name = normal(line)
        if name and not name.startswith("#") and name not in seen:
            names.append(name)
            seen.add(name)
    if not names:
        raise SystemExit("Add at least one vessel name to vessels.txt")
    return names


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


def select_exact_vessel(driver: webdriver.Edge, name: str) -> bool:
    name = ALIASES.get(name, name)
    matches = []
    for element in driver.find_elements(By.TAG_NAME, "select"):
        select = Select(element)
        for option in select.options:
            label = normal(option.text)
            without_code = re.sub(r"^[A-Z0-9]{2,6}\s*-\s*", "", label)
            if label == name or without_code == name:
                matches.append((select, option.text))
    if len(matches) != 1:
        return False
    select, label = matches[0]
    select.select_by_visible_text(label)
    return True


def extract_rows(driver: webdriver.Edge) -> list[list[str]]:
    return driver.execute_script(r"""
const clean = value => (value.innerText || value.textContent || '').replace(/\s+/g, ' ').trim();
const output = [];
for (const table of document.querySelectorAll('table')) {
  for (const row of table.rows) {
    const cells = [...row.cells].map(clean);
    if (cells.length >= 9 && /\d{4}\/\d{2}\/\d{2}/.test(cells.join(' '))) output.push(cells.slice(0, 10));
  }
}
return output;
""")


def date_only(value: str | None) -> str | None:
    """Return an ISO date from either carrier or ActiveTrack display dates."""
    if not value:
        return None
    value = str(value).strip()
    for pattern, fmt in ((r"^\d{4}/\d{2}/\d{2}$", "%Y/%m/%d"), (r"^\d{4}-\d{2}-\d{2}$", "%Y-%m-%d")):
        if re.match(pattern, value):
            return datetime.strptime(value, fmt).date().isoformat()
    return None


def port_key(value: str | None) -> str:
    """Small, explicit alias map avoids treating different nearby ports as equal."""
    text = normal(value or "")
    # Wan Hai publishes the Shenzhen calls used by these services as SHEKOU.
    # Keep Yantian ahead of the generic Shenzhen alias so "Yantian, Shenzhen"
    # can never be converted to the wrong terminal.
    aliases = (
        ("PORT KLANG NORTH", "PORT KLANG"),
        ("PORT KELANG", "PORT KLANG"),
        ("YANTIAN", "YANTIAN"),
        ("SHEKOU", "SHEKOU"),
        ("SHENZHEN", "SHEKOU"),
        ("HONG KONG", "HONG KONG"),
        ("KAOHSIUNG", "KAOHSIUNG"),
        ("COLOMBO", "COLOMBO"),
        ("SINGAPORE", "SINGAPORE"),
        ("SHANGHAI", "SHANGHAI"),
        ("NINGBO", "NINGBO"),
        ("TAIPEI", "TAIPEI"),
        ("CAI MEP", "CAI MEP"),
    )
    return next((key for token, key in aliases if token in text), text)


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


def closest(candidates: list[dict], target: str | None, field: str) -> dict | None:
    if not candidates:
        return None
    if not target:
        return candidates[-1] if len(candidates) == 1 else None
    target_date = datetime.fromisoformat(target).date()
    ordered = sorted(candidates, key=lambda candidate: abs((datetime.fromisoformat(candidate[field]).date() - target_date).days))
    # Do not turn an unrelated prior/future rotation into a proposed change.
    return ordered[0] if abs((datetime.fromisoformat(ordered[0][field]).date() - target_date).days) <= 60 else None


def compare_with_activtrack(results: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    """Match exact vessel/voyage/port legs and return only source-backed changes.

    A published carrier arrival is compared with ActiveTrack ETA, not ATA.  The
    same rule applies to departure/ETD.  Actual fields are never changed by
    this collector; those still need the normal control-tower verification.
    """
    source = latest_activtrack_file()
    if not source:
        return [], [], None
    data = json.loads(source.read_text(encoding="utf-8"))
    carrier_rows = []
    for result in results:
        vessel = normal(ALIASES.get(result["vessel"], result["vessel"]))
        for row in result["rows"]:
            if len(row) < 10:
                continue
            carrier_rows.append({
                "vessel": vessel, "port": port_key(row[0]),
                "arrival_voyage": normal(row[1]), "carrier_eta": date_only(row[2]),
                "departure_voyage": normal(row[6]), "carrier_etd": date_only(row[7]),
                "carrier_state": normal(row[9]), "source_url": result["source"],
            })

    updates, matched = [], []
    for schedule in schedules_from_source(data):
        for leg in schedule.get("transshipments") or []:
            vessel_data = leg.get("vessel") or {}
            vessel = normal(ALIASES.get(normal(vessel_data.get("name", "")), normal(vessel_data.get("name", ""))))
            voyage = normal(leg.get("voyage", ""))
            if not vessel or not voyage:
                continue
            common = {
                "vs_id": schedule.get("reference_number"), "leg": leg.get("leg_number"),
                "vessel": vessel_data.get("name"), "voyage": leg.get("voyage"),
                "pol": (leg.get("departure_port") or {}).get("name"),
                "pod": (leg.get("arrival_port") or {}).get("name"),
            }
            etd_candidates = [row for row in carrier_rows if row["vessel"] == vessel and row["departure_voyage"] == voyage and row["port"] == port_key(common["pol"]) and row["carrier_etd"]]
            eta_candidates = [row for row in carrier_rows if row["vessel"] == vessel and row["arrival_voyage"] == voyage and row["port"] == port_key(common["pod"]) and row["carrier_eta"]]
            etd = closest(etd_candidates, leg.get("etd"), "carrier_etd")
            eta = closest(eta_candidates, leg.get("eta"), "carrier_eta")
            if not etd and not eta:
                continue
            row = {**common, "at_etd": leg.get("etd"), "carrier_etd": etd.get("carrier_etd") if etd else None,
                   "at_eta": leg.get("eta"), "carrier_eta": eta.get("carrier_eta") if eta else None,
                   "source_state": "; ".join(sorted({item["carrier_state"] for item in (etd, eta) if item})),
                   "source_url": (etd or eta)["source_url"]}
            matched.append(row)
            if (etd and not leg.get("atd") and etd["carrier_etd"] != leg.get("etd")) or (eta and not leg.get("ata") and eta["carrier_eta"] != leg.get("eta")):
                updates.append(row)
    return updates, matched, str(source)


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    vessels = vessel_list()
    OUT.mkdir(parents=True, exist_ok=True)
    driver = open_edge()
    results = []
    try:
        driver.get(WAN_HAI_URL)
        WebDriverWait(driver, 20).until(lambda page: page.find_elements(By.TAG_NAME, "select"))
        for number, vessel in enumerate(vessels, 1):
            record = {"vessel": vessel, "checked_at": datetime.now().isoformat(timespec="seconds"), "source": driver.current_url, "status": "OK", "rows": []}
            print(f"[{number}/{len(vessels)}] {vessel}")
            try:
                if not select_exact_vessel(driver, vessel):
                    record["status"] = "NOT_LISTED_ON_WAN_HAI_SITE"
                else:
                    query = [button for button in driver.find_elements(By.XPATH, "//button[normalize-space()='Query'] | //input[@value='Query']") if button.is_displayed() and button.is_enabled()]
                    if len(query) != 1:
                        raise RuntimeError("Query button unavailable")
                    query[0].click()
                    time.sleep(1.5)
                    try:
                        driver.switch_to.alert.accept()
                    except NoAlertPresentException:
                        pass
                    record["rows"] = extract_rows(driver)
                    if not record["rows"]:
                        record["status"] = "NO_TIMETABLE_ROWS"
            except Exception as error:
                record["status"] = f"ERROR: {type(error).__name__}: {error}"
            results.append(record)
    finally:
        driver.quit()

    updates, matched, activtrack_source = compare_with_activtrack(results)
    (OUT / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    with (OUT / "timetable.csv").open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.writer(output)
        writer.writerow(["vessel", "checked_at", "status", "port", "arrival_voyage", "eta_date", "eta_time", "etb_date", "etb_time", "departure_voyage", "etd_date", "etd_time", "state"])
        for record in results:
            for row in record["rows"]:
                writer.writerow([record["vessel"], record["checked_at"], record["status"], *row])
    compare_fields = ["vs_id", "leg", "vessel", "voyage", "pol", "pod", "at_etd", "carrier_etd", "at_eta", "carrier_eta", "source_state", "source_url"]
    write_csv(OUT / "updates_only.csv", updates, compare_fields)
    write_csv(OUT / "matched_legs.csv", matched, compare_fields)
    (OUT / "comparison.json").write_text(json.dumps({"activtrack_source": activtrack_source, "updates_only": updates, "matched_legs": matched}, indent=2), encoding="utf-8")
    print(f"\nFinished. Results: {OUT}")
    print(f"ActiveTrack source: {activtrack_source or 'not found'}")
    print(f"Exact legs matched: {len(matched)} | Date changes to review: {len(updates)}")


if __name__ == "__main__":
    main()
