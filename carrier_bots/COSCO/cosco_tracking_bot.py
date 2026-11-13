"""COSCO eLines vessel schedule collector and ActiveTrack comparator.

Add vessel names to vessels.txt, one per line, then run:
    py cosco_tracking_bot.py

The bot reads COSCO's public four-week vessel schedule in Edge and compares
the returned planned/actual dates with the newest local ActiveTrack source.
It creates review files only; it never updates ActiveTrack or sends notices.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "vessels.txt"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
COSCO_URL = "https://elines.coscoshipping.com/ebusiness/sailingSchedule/searchByVesselName"
ACTIVTRACK_REPORTS = (Path.home() / "Documents" / "ActivTrack Reports")

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
except ModuleNotFoundError:
    tracker_python = ROOT.parents[1] / "vessel-watch" / ".venv" / "Scripts" / "python.exe"
    if tracker_python.exists() and "vessel-watch\\.venv\\scripts\\python.exe" not in str(sys.executable).lower():
        raise SystemExit(subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")


def normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().upper()


def port_key(value: str | None) -> str:
    text = normal(value)
    aliases = (
        ("PORT KELANG", "PORT KLANG"), ("PORT KLANG", "PORT KLANG"),
        ("LAEM CHABANG", "LAEM CHABANG"), ("FUZHOU NEW PORT", "FUZHOU"),
        ("HO CHI MINH", "HO CHI MINH"), ("CAI MEP", "CAI MEP"),
        ("COLOMBO", "COLOMBO"), ("SINGAPORE", "SINGAPORE"),
        ("SHANGHAI", "SHANGHAI"), ("NINGBO", "NINGBO"),
        ("SHEKOU", "SHEKOU"), ("XIAMEN", "XIAMEN"),
        ("JAKARTA", "JAKARTA"), ("MUNDRA", "MUNDRA"),
        ("KARACHI", "KARACHI"), ("PIRAEUS", "PIRAEUS"),
        ("FELIXSTOWE", "FELIXSTOWE"), ("HAMBURG", "HAMBURG"),
        ("ROTTERDAM", "ROTTERDAM"),
    )
    return next((key for token, key in aliases if token in text), text)


def voyage_aliases(value: str | None) -> set[str]:
    """Expand COSCO pairs such as 152W/152E for ActiveTrack matching."""
    text = normal(value).replace(" ", "")
    if not text:
        return set()
    aliases = {text}
    aliases.update(part for part in text.split("/") if part)
    return aliases


def vessels() -> list[str]:
    override = os.environ.get("TRACKING_VESSELS")
    if not override and not INPUT.exists():
        raise SystemExit("Missing vessels.txt")
    seen, output = set(), []
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
    profile = ROOT / "edge_profile_bot"
    profile.mkdir(exist_ok=True)
    options = Options()
    if os.environ.get("TRACKING_HEADLESS") == "1":
        options.add_argument("--headless=new")
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--window-size=1500,1000")
    options.add_argument("--disable-gpu")
    options.page_load_strategy = "eager"
    return webdriver.Edge(options=options, service=Service())


def search(driver: webdriver.Edge, vessel: str) -> None:
    driver.get(COSCO_URL)
    wait = WebDriverWait(driver, 30)
    # COSCO currently uses "Please enter..." in Chrome and "Please input..."
    # in Edge, so bind to the stable end of the placeholder instead.
    field = wait.until(lambda page: page.find_element(By.CSS_SELECTOR, "input[placeholder*='vessel name']"))
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(Keys.DELETE)
    field.send_keys(vessel)

    def exact_option(page):
        return next(
            (item for item in page.find_elements(By.CSS_SELECTOR, "li.ivu-select-item")
             if item.is_displayed() and normal(item.text) == normal(vessel)),
            None,
        )

    option = wait.until(exact_option)
    option.click()
    button = wait.until(
        lambda page: next(
            (item for item in page.find_elements(By.TAG_NAME, "button")
             if item.is_displayed() and item.is_enabled() and normal(item.text) == "FIND SCHEDULE"),
            None,
        )
    )
    button.click()
    wait.until(lambda page: "resultByVoyage" in page.current_url and page.find_elements(By.CSS_SELECTOR, ".filter.sailing-title"))

    expected = normal(vessel) + " SEARCH FOUR WEEKS SAILING SCHEDULE"
    body = normal(driver.find_element(By.TAG_NAME, "body").text)
    if expected not in body:
        raise ValueError("COSCO result identity did not match the requested vessel")


def cell_date(value: str | None) -> str | None:
    match = re.search(r"\d{4}-\d{2}-\d{2}", value or "")
    return match.group(0) if match else None


def extract(driver: webdriver.Edge, vessel: str) -> list[dict]:
    rows, seen = [], set()
    for section in driver.find_elements(By.CSS_SELECTOR, ".filter.sailing-title"):
        voyage_match = re.search(r"\bVoyage\s+([^\n]+)", section.text, re.I)
        voyage = normal(voyage_match.group(1)) if voyage_match else None
        if not voyage:
            continue
        for table in section.find_elements(By.CSS_SELECTOR, ".sailing-data-table table"):
            for table_row in table.find_elements(By.CSS_SELECTOR, "tr"):
                cells = table_row.find_elements(By.CSS_SELECTOR, "td")
                if len(cells) < 5:
                    continue
                port = normal(cells[0].text)
                record = {
                    "carrier_vessel": normal(vessel),
                    "carrier_voyage": voyage,
                    "port": port,
                    "carrier_eta": cell_date(cells[1].text),
                    "carrier_ata": cell_date(cells[2].text),
                    "carrier_etd": cell_date(cells[3].text),
                    "carrier_atd": cell_date(cells[4].text),
                    "source_url": driver.current_url,
                }
                key = tuple(record.get(field) for field in ("carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_ata", "carrier_etd", "carrier_atd"))
                if port and any(record[field] for field in ("carrier_eta", "carrier_ata", "carrier_etd", "carrier_atd")) and key not in seen:
                    seen.add(key)
                    rows.append(record)
    if not rows:
        raise ValueError("No COSCO sailing-schedule rows were found")
    return rows


def latest_report() -> Path | None:
    override = Path(os.environ["ACTIVTRACK_SOURCE_FILE"]) if os.environ.get("ACTIVTRACK_SOURCE_FILE") else None
    if override and override.is_file():
        return override
    reports = list(ACTIVTRACK_REPORTS.glob("ActivTrack_Vessel_Booking_Report_*_Source.json"))
    if not reports:
        reports = list(ACTIVTRACK_REPORTS.glob("ActivTrack_All_Vessels_*_Source.json"))
    return max(reports, key=lambda item: item.stat().st_mtime) if reports else None


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


def nearest(rows: list[dict], target: str | None, fields: tuple[str, ...]) -> tuple[dict, str] | None:
    candidates = []
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
    return (row, value) if abs((date.fromisoformat(value) - target_date).days) <= 90 else None


def compare(rows: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    source = latest_report()
    if not source:
        return [], [], None
    source_data = json.loads(source.read_text(encoding="utf-8"))
    matched, updates = [], []
    for schedule in schedules_from_source(source_data):
        for leg in schedule.get("transshipments") or []:
            vessel = normal((leg.get("vessel") or {}).get("name"))
            voyage = normal(leg.get("voyage")).replace(" ", "")
            exact = [
                row for row in rows
                if row["carrier_vessel"] == vessel and voyage in voyage_aliases(row["carrier_voyage"])
            ]
            if not exact:
                continue
            departure_rows = [row for row in exact if port_key(row["port"]) == port_key((leg.get("departure_port") or {}).get("name"))]
            arrival_rows = [row for row in exact if port_key(row["port"]) == port_key((leg.get("arrival_port") or {}).get("name"))]
            departure = nearest(departure_rows, leg.get("etd"), ("carrier_atd", "carrier_etd"))
            arrival = nearest(arrival_rows, leg.get("eta"), ("carrier_ata", "carrier_eta"))
            if not departure and not arrival:
                continue
            source_row = (departure or arrival)[0]
            review = {
                "vs_id": schedule.get("reference_number"),
                "leg": leg.get("leg_number"),
                "vessel": vessel,
                "voyage": leg.get("voyage"),
                "carrier_voyage": source_row["carrier_voyage"],
                "pol": (leg.get("departure_port") or {}).get("name"),
                "pod": (leg.get("arrival_port") or {}).get("name"),
                "at_etd": leg.get("etd"),
                "carrier_etd": departure[0]["carrier_etd"] if departure else None,
                "at_atd": leg.get("atd"),
                "carrier_atd": departure[0]["carrier_atd"] if departure else None,
                "at_eta": leg.get("eta"),
                "carrier_eta": arrival[0]["carrier_eta"] if arrival else None,
                "at_ata": leg.get("ata"),
                "carrier_ata": arrival[0]["carrier_ata"] if arrival else None,
                "source_url": source_row["source_url"],
            }
            matched.append(review)
            if (departure and review["carrier_etd"] and not leg.get("atd") and review["at_etd"] != review["carrier_etd"]) or (arrival and review["carrier_eta"] and not leg.get("ata") and review["at_eta"] != review["carrier_eta"]):
                updates.append(review)
    return updates, matched, str(source)


def save_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    requested = vessels()
    OUT.mkdir(parents=True, exist_ok=True)
    driver, status, rows = open_edge(), [], []
    try:
        for index, vessel in enumerate(requested, 1):
            print(f"[{index}/{len(requested)}] {vessel}")
            record = {"vessel": vessel, "checked_at": datetime.now().isoformat(timespec="seconds"), "status": "OK", "rows": []}
            try:
                search(driver, vessel)
                record["rows"] = extract(driver, vessel)
                rows.extend(record["rows"])
            except Exception as error:
                record["status"] = f"ERROR: {type(error).__name__}: {error}"
            status.append(record)
    finally:
        driver.quit()

    updates, matched, source = compare(rows)
    (OUT / "results.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    timetable_columns = ["carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_ata", "carrier_etd", "carrier_atd", "source_url"]
    compare_columns = ["vs_id", "leg", "vessel", "voyage", "carrier_voyage", "pol", "pod", "at_etd", "carrier_etd", "at_atd", "carrier_atd", "at_eta", "carrier_eta", "at_ata", "carrier_ata", "source_url"]
    save_csv(OUT / "timetable.csv", rows, timetable_columns)
    save_csv(OUT / "matched_legs.csv", matched, compare_columns)
    save_csv(OUT / "updates_only.csv", updates, compare_columns)
    (OUT / "comparison.json").write_text(json.dumps({"activtrack_source": source, "updates_only": updates, "matched_legs": matched}, indent=2), encoding="utf-8")
    print(f"\nFinished. Results: {OUT}")
    print(f"Exact legs matched: {len(matched)} | Date changes to review: {len(updates)}")


if __name__ == "__main__":
    main()
