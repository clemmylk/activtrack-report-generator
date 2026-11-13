"""Maersk public vessel-schedule collector and ActiveTrack comparator.

Read-only by design: the bot captures published planned port calls, produces
comparison files, and never updates ActiveTrack or sends notifications.
"""

from __future__ import annotations

import os

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_ROOT = ROOT.parent
sys.path.insert(0, str(BOT_ROOT))

from carrier_bot_common import (  # noqa: E402
    normal,
    requested_vessels,
    save_outputs,
)


MAERSK_URL = "https://www.maersk.com/schedules/vesselSchedules"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.edge.options import Options
    from selenium.webdriver.edge.service import Service
    from selenium.webdriver.support.ui import WebDriverWait
except ModuleNotFoundError:
    tracker_python = BOT_ROOT / "vessel-watch" / ".venv" / "Scripts" / "python.exe"
    if tracker_python.exists() and "vessel-watch\\.venv\\scripts\\python.exe" not in str(sys.executable).lower():
        raise SystemExit(subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")


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


def _dismiss_cookies(driver: webdriver.Edge) -> None:
    if getattr(driver, "_globactiv_cookies_checked", False):
        return
    try:
        button = WebDriverWait(driver, 5).until(
            lambda page: next(
                (item for item in page.find_elements(By.TAG_NAME, "button")
                 if item.is_displayed() and normal(item.text) == "ESSENTIAL ONLY"),
                None,
            )
        )
        button.click()
    except Exception:
        pass
    driver._globactiv_cookies_checked = True


def search(driver: webdriver.Edge, vessel: str) -> None:
    driver.get(MAERSK_URL)
    wait = WebDriverWait(driver, 18)
    _dismiss_cookies(driver)
    try:
        host = wait.until(lambda page: page.find_element(By.CSS_SELECTOR, "mc-typeahead#vesselName"))
        field = host.shadow_root.find_element(By.ID, "mc-input-vesselName")
    except Exception as error:
        body = normal(driver.find_element(By.TAG_NAME, "body").text)[:160]
        raise RuntimeError(f"Maersk vessel field unavailable: {driver.title} | {driver.current_url} | {body}") from error
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(vessel)
    try:
        option = wait.until(
            lambda page: next(
                (item for item in host.shadow_root.find_elements(By.CSS_SELECTOR, "[role='option']")
                 if item.is_displayed() and normal(item.text) == normal(vessel)),
                None,
            )
        )
    except Exception as error:
        raise RuntimeError("Vessel is not listed on the Maersk schedule site") from error

    option.click()
    try:
        search_host = wait.until(
            lambda page: page.find_element(By.CSS_SELECTOR, "mc-button[data-test='vessel-search-button']")
        )
        driver.execute_script("arguments[0].shadowRoot.querySelector('button').click()", search_host)
    except Exception as error:
        raise RuntimeError("Maersk search button unavailable") from error
    try:
        wait.until(
            lambda page: page.find_elements(By.CSS_SELECTOR, "[data-test='vessel-schedules-results']")
            and normal(vessel) in normal(page.find_element(By.CSS_SELECTOR, "[data-test='vessel-schedules-results']").text)
            and page.find_elements(By.CSS_SELECTOR, ".ptp-results__transport-plan--item")
        )
    except Exception as error:
        raise RuntimeError(f"Maersk result did not load ({driver.current_url})") from error


def _event(text: str, label: str) -> tuple[str, str] | None:
    match = re.search(
        rf"{label}\s*-\s*([A-Z0-9/-]+)\s*(\d{{1,2}}\s+[A-Z]{{3}}\s+\d{{4}}\s+\d{{1,2}}:\d{{2}})",
        normal(text),
    )
    if not match:
        return None
    try:
        value = datetime.strptime(match.group(2).title(), "%d %b %Y %H:%M").date().isoformat()
    except ValueError:
        return None
    return normal(match.group(1)), value


def extract(driver: webdriver.Edge, vessel: str) -> list[dict]:
    result = driver.find_element(By.CSS_SELECTOR, "[data-test='vessel-schedules-results']")
    if normal(vessel) not in normal(result.text):
        raise ValueError("Maersk result identity did not match the requested vessel")

    rows: list[dict] = []
    current_port = ""
    current_call: dict[str, dict] = {}
    for item in result.find_elements(By.CSS_SELECTOR, ".ptp-results__transport-plan--item"):
        links = item.find_elements(By.CSS_SELECTOR, "a[href*='/schedules/portCalls']")
        if links and normal(links[0].text):
            current_port = normal(links[0].text)
            # The same vessel/voyage can call the same port more than once.
            # Keep each displayed call separate so dates from two rotations
            # can never be merged into an impossible arrival/departure pair.
            current_call = {}
        if not current_port:
            continue
        for label, date_field, actual_field in (
            ("ARRIVAL", "carrier_eta", "carrier_ata"),
            ("DEPARTURE", "carrier_etd", "carrier_atd"),
        ):
            event = _event(item.text, label)
            if not event:
                continue
            voyage, value = event
            row = current_call.get(voyage)
            if row is None:
                row = {
                "carrier_vessel": normal(vessel),
                "carrier_voyage": voyage,
                "port": current_port,
                "carrier_eta": None,
                "carrier_ata": None,
                "carrier_etd": None,
                "carrier_atd": None,
                "source_url": driver.current_url,
                }
                current_call[voyage] = row
                rows.append(row)
            # Maersk's public schedule labels these as planned calls.  Never
            # infer an actual timestamp from a planned arrival/departure.
            row[date_field] = value
            row[actual_field] = None
    if not rows:
        raise ValueError("No Maersk vessel-schedule rows were found")
    return rows


def main() -> None:
    requested = requested_vessels(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    statuses: list[dict] = []
    rows: list[dict] = []
    driver = open_edge()
    try:
        for index, vessel in enumerate(requested, 1):
            print(f"[{index}/{len(requested)}] {vessel}", flush=True)
            record = {
                "vessel": vessel,
                "checked_at": datetime.now().isoformat(timespec="seconds"),
                "status": "OK",
                "rows": [],
            }
            try:
                search(driver, vessel)
                record["rows"] = extract(driver, vessel)
                rows.extend(record["rows"])
            except Exception as error:
                message = str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__
                record["status"] = f"ERROR: {message}"
            statuses.append(record)
    finally:
        driver.quit()

    matched, updates = save_outputs(OUT, statuses, rows)
    print(f"\nFinished. Results: {OUT}", flush=True)
    print(f"Exact legs matched: {matched} | Date changes to review: {updates}", flush=True)


if __name__ == "__main__":
    main()
