"""ONE Line vessel timetable collector and ActiveTrack date comparator.

Add vessel names to vessels.txt (one per line), then run:
    py one_line_tracking_bot.py

The bot reads ONE's public Vessel Schedule page in Edge and the newest local
ActivTrack source report.  It writes review candidates only; it never changes
ActiveTrack or sends notifications.
"""

from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INPUT = ROOT / "vessels.txt"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
ONE_URL = "https://www.one-line.com/one-ecom/schedule/vessel-schedule"
ACTIVTRACK_REPORTS = (Path.home() / "Documents" / "ActivTrack Reports")

# ONE lists several same-name vessels under different internal codes. These
# overrides prevent the autocomplete from selecting the first wrong vessel.
PREFERRED_VESSEL_CODES = {
    "MOGRAL": "MOGT",
    "SM MANALI": "LMIT",
}

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.common.exceptions import TimeoutException
    from selenium.webdriver.support.ui import WebDriverWait
except ModuleNotFoundError:
    tracker_python = ROOT.parents[1] / "vessel-watch" / ".venv" / "Scripts" / "python.exe"
    # ``resolve()`` follows the venv launcher back to the global Python on
    # Windows, so compare the executable path text and avoid a fallback loop.
    if tracker_python.exists() and "vessel-watch\\.venv\\scripts\\python.exe" not in str(sys.executable).lower():
        raise SystemExit(subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")


def normal(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip().upper()


def matches_vessel_option(label: str | None, vessel: str) -> bool:
    """Return whether an autocomplete label is the intended ONE vessel."""
    label_key = normal(label)
    code_match = re.search(r"\(([A-Z0-9]+)\)$", label_key)
    base_name = normal(re.sub(r"\s*\([A-Z0-9]+\)$", "", label_key))
    vessel_key = normal(vessel)
    preferred_code = PREFERRED_VESSEL_CODES.get(vessel_key)
    return base_name == vessel_key and (
        preferred_code is None or (code_match and code_match.group(1) == preferred_code)
    )


def port_key(value: str | None) -> str:
    text = normal(value)
    aliases = {"COLOMBO": "COLOMBO", "SINGAPORE": "SINGAPORE", "HONG KONG": "HONG KONG", "SHANGHAI": "SHANGHAI", "NINGBO": "NINGBO", "SHEKOU": "SHEKOU", "LAEM CHABANG": "LAEM CHABANG", "PORT KELANG": "PORT KELANG", "HO CHI MINH": "HO CHI MINH", "CAI MEP": "CAI MEP"}
    return next((key for token, key in aliases.items() if token in text), text)


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
    # Dedicated profile avoids any stale lock after an interrupted test.
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


def clear_page_alerts(driver: webdriver.Edge) -> None:
    """Dismiss ONE informational overlays that can cover the autocomplete.

    ONE occasionally displays an ``EmergencySystemAlert`` after page load.  It
    is not part of the schedule form, but it intercepts every normal Selenium
    click and previously caused the whole carrier run to return zero rows.
    """
    selectors = (
        "[class*='EmergencySystemAlert'] button",
        "[role='dialog'] button[aria-label*='close' i]",
        "[role='dialog'] button",
    )
    for selector in selectors:
        for button in driver.find_elements(By.CSS_SELECTOR, selector):
            if not button.is_displayed():
                continue
            label = normal(" ".join((button.text, button.get_attribute("aria-label") or "")))
            if selector.endswith(" button") or any(token in label for token in ("CLOSE", "OK", "CONFIRM", "DISMISS")):
                driver.execute_script("arguments[0].click()", button)
                return


def safe_click(driver: webdriver.Edge, element) -> None:
    """Click a known ONE control even when a transient banner overlaps it."""
    clear_page_alerts(driver)
    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", element)
    driver.execute_script("arguments[0].click()", element)


def search(driver: webdriver.Edge, vessel: str) -> None:
    driver.get(ONE_URL)
    wait = WebDriverWait(driver, 30)
    clear_page_alerts(driver)
    field = wait.until(lambda page: page.find_element(By.CSS_SELECTOR, "input[placeholder='Input Vessel Name']"))
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(vessel)
    # Autocomplete suggestions are dynamic. Select only a visible exact name,
    # applying a preferred ONE code where duplicate vessel names exist.
    option = wait.until(lambda page: next((item for item in page.find_elements(By.CSS_SELECTOR, "[role=option]")
                                            if item.is_displayed() and matches_vessel_option(item.text, vessel)), None))
    safe_click(driver, option)
    safe_click(driver, wait.until(lambda page: page.find_element(By.CSS_SELECTOR, "button[data-cy='new-schedule-search-btn']")))
    try:
        WebDriverWait(driver, 18).until(
            lambda page: "CALLING PORTS" in normal(page.find_element(By.TAG_NAME, "body").text)
        )
    except TimeoutException as error:
        body = normal(driver.find_element(By.TAG_NAME, "body").text)
        if "FETCHING RESULT" in body:
            raise RuntimeError("BLOCKED: ONE Line schedule service returned no result") from error
        raise RuntimeError("ONE Line result page did not load") from error


def iso_date(value: str) -> str | None:
    value = re.sub(r"\s+", " ", value.strip())
    for fmt in ("%d %b %Y %H:%M", "%d %b %Y", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def extract(driver: webdriver.Edge, vessel: str) -> list[dict]:
    text = driver.find_element(By.TAG_NAME, "body").text
    identity = re.search(r"\nVessel\s*\n\s*([^\n]+)\s*\n\s*Service Lane", text)
    if not identity or normal(re.sub(r"\s*\([A-Z0-9]+\)$", "", identity.group(1))) != vessel:
        raise ValueError("ONE result identity did not match the requested vessel")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    calls = []
    for i, label in enumerate(lines):
        if label != "Port" or i + 11 >= len(lines) or lines[i + 2] != "Voyage" or lines[i + 4] != "Terminal":
            continue
        if lines[i + 6] != "Arrival Time" or lines[i + 9] != "Departure Time":
            continue
        arrival, arrival_flag, departure, departure_flag = lines[i + 7], lines[i + 8], lines[i + 10], lines[i + 11]
        calls.append({"carrier_vessel": vessel, "carrier_voyage": normal(lines[i + 3]), "port": normal(lines[i + 1]),
                      "carrier_eta": iso_date(arrival) if arrival_flag == "C" else None, "carrier_ata": iso_date(arrival) if arrival_flag == "A" else None,
                      "carrier_etd": iso_date(departure) if departure_flag == "C" else None, "carrier_atd": iso_date(departure) if departure_flag == "A" else None,
                      "source_url": driver.current_url})
    if not calls:
        raise ValueError("No ONE calling-port cards were found")
    return calls


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


def compare(rows: list[dict]) -> tuple[list[dict], list[dict], str | None]:
    source = latest_report()
    if not source:
        return [], [], None
    source_data = json.loads(source.read_text(encoding="utf-8"))
    matched, updates = [], []
    for schedule in schedules_from_source(source_data):
        for leg in schedule.get("transshipments") or []:
            vessel = normal((leg.get("vessel") or {}).get("name"))
            voyage = normal(leg.get("voyage"))
            exact = [row for row in rows if row["carrier_vessel"] == vessel and row["carrier_voyage"] == voyage]
            if not exact:
                continue
            departure = next((row for row in exact if port_key(row["port"]) == port_key((leg.get("departure_port") or {}).get("name")) and (row["carrier_etd"] or row["carrier_atd"])), None)
            arrival = next((row for row in exact if port_key(row["port"]) == port_key((leg.get("arrival_port") or {}).get("name")) and (row["carrier_eta"] or row["carrier_ata"])), None)
            if not departure and not arrival:
                continue
            review = {"vs_id": schedule.get("reference_number"), "leg": leg.get("leg_number"), "vessel": vessel, "voyage": voyage,
                      "pol": (leg.get("departure_port") or {}).get("name"), "pod": (leg.get("arrival_port") or {}).get("name"),
                      "at_etd": leg.get("etd"), "carrier_etd": departure["carrier_etd"] if departure else None,
                      "at_atd": leg.get("atd"), "carrier_atd": departure["carrier_atd"] if departure else None,
                      "at_eta": leg.get("eta"), "carrier_eta": arrival["carrier_eta"] if arrival else None,
                      "at_ata": leg.get("ata"), "carrier_ata": arrival["carrier_ata"] if arrival else None,
                      "source_url": (departure or arrival)["source_url"]}
            matched.append(review)
            if (departure and departure["carrier_etd"] and not leg.get("atd") and review["at_etd"] != review["carrier_etd"]) or (arrival and arrival["carrier_eta"] and not leg.get("ata") and review["at_eta"] != review["carrier_eta"]):
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
    site_blocked = ""
    try:
        for index, vessel in enumerate(requested, 1):
            print(f"[{index}/{len(requested)}] {vessel}")
            record = {"vessel": vessel, "checked_at": datetime.now().isoformat(timespec="seconds"), "status": "OK", "rows": []}
            if site_blocked:
                record["status"] = site_blocked
            else:
                try:
                    search(driver, vessel)
                    record["rows"] = extract(driver, vessel)
                    rows.extend(record["rows"])
                except Exception as error:
                    message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
                    record["status"] = message if message.startswith("BLOCKED:") else f"ERROR: {message}"
                    if record["status"].startswith("BLOCKED:"):
                        site_blocked = record["status"]
            status.append(record)
    finally:
        driver.quit()
    updates, matched, source = compare(rows)
    (OUT / "results.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    timetable_columns = ["carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_ata", "carrier_etd", "carrier_atd", "source_url"]
    compare_columns = ["vs_id", "leg", "vessel", "voyage", "pol", "pod", "at_etd", "carrier_etd", "at_atd", "carrier_atd", "at_eta", "carrier_eta", "at_ata", "carrier_ata", "source_url"]
    save_csv(OUT / "timetable.csv", rows, timetable_columns)
    save_csv(OUT / "matched_legs.csv", matched, compare_columns)
    save_csv(OUT / "updates_only.csv", updates, compare_columns)
    (OUT / "comparison.json").write_text(json.dumps({"activtrack_source": source, "updates_only": updates, "matched_legs": matched}, indent=2), encoding="utf-8")
    print(f"\nFinished. Results: {OUT}")
    print(f"Exact legs matched: {len(matched)} | Date changes to review: {len(updates)}")


if __name__ == "__main__":
    main()
