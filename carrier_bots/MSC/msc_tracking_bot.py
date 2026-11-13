"""MSC public vessel-schedule collector and ActiveTrack comparator.

The bot is read-only: it opens MSC's official public schedule page, captures
the currently published vessel calls, and writes comparison files.  It never
updates ActiveTrack and it never sends notices.
"""

from __future__ import annotations

import os

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_ROOT = ROOT.parent
sys.path.insert(0, str(BOT_ROOT))

from carrier_bot_common import normal, requested_vessels, save_outputs  # noqa: E402


MSC_URL = "https://www.msc.com/en/search-a-schedule?agencyPath=slv"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
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


def _date(value: str | None) -> str | None:
    text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", value or "", flags=re.I)
    match = re.search(r"\b\w{3}\s+\d{1,2}\s+\w{3}\s+\d{4}(?:\s+\d{1,2}:\d{2})?", text)
    if not match:
        return None
    for fmt in ("%a %d %b %Y %H:%M", "%a %d %b %Y"):
        try:
            return datetime.strptime(match.group(0), fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _dismiss_cookies(driver: webdriver.Edge) -> None:
    """Close OneTrust when a saved preference panel blocks the schedule tabs."""
    if getattr(driver, "_globactiv_cookies_checked", False):
        return
    selectors = (
        "#onetrust-reject-all-handler",
        "button.ot-close-icon",
        ".onetrust-close-btn-handler",
    )
    def visible_button(page):
        for selector in selectors:
            for button in page.find_elements(By.CSS_SELECTOR, selector):
                if button.is_displayed() and button.is_enabled():
                    return button
        return False

    try:
        WebDriverWait(driver, 6).until(visible_button).click()
        WebDriverWait(driver, 6).until(
            lambda page: not any(
                item.is_displayed()
                for item in page.find_elements(By.CSS_SELECTOR, "#onetrust-banner-sdk, .onetrust-pc-dark-filter")
            )
        )
    except Exception:
        # Some returning profiles have already saved a choice and show no panel.
        pass
    driver._globactiv_cookies_checked = True


def search(driver: webdriver.Edge, vessel: str) -> None:
    driver.get(MSC_URL)
    wait = WebDriverWait(driver, 45)
    _dismiss_cookies(driver)
    vessel_tab = wait.until(
        lambda page: next(
            (item for item in page.find_elements(By.CSS_SELECTOR, "button.msc-search-schedule__nav-button")
             if item.is_displayed() and normal(item.text) == "VESSEL"),
            None,
        )
    )
    vessel_tab.click()
    field = wait.until(lambda page: page.find_element(By.CSS_SELECTOR, "input[placeholder='Enter vessel name']"))
    # MSC's Alpine input rerenders on every Selenium keystroke.  Set the same
    # public component value once and call its autocomplete action; this is
    # both faster and avoids the input collapsing to the final character.
    driver.execute_script(
        """
        let node = arguments[0];
        while (node && !node.__x) node = node.parentElement;
        if (!node || !node.__x) throw new Error('MSC schedule component unavailable');
        node.__x.$data.vessel = arguments[1];
        node.__x.$data.fetchVesselAutoComplete();
        """,
        field,
        vessel,
    )

    option = wait.until(
        lambda page: next(
            (item for item in page.find_elements(By.CSS_SELECTOR, "button[x-text='entry.Name']")
             if item.is_displayed() and item.is_enabled() and normal(item.text) == normal(vessel)),
            None,
        )
    )
    option.click()
    wait.until(
        lambda page: page.find_elements(By.CSS_SELECTOR, ".msc-search-schedule__result-step")
        and "FETCHING RESULTS" not in normal(page.find_element(By.TAG_NAME, "body").text)
    )


def extract(driver: webdriver.Edge, vessel: str) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    for row in driver.find_elements(By.CSS_SELECTOR, ".msc-search-schedule__result-step"):
        try:
            carrier_vessel = normal(row.find_element(By.CSS_SELECTOR, "[x-text='entry.VesselName']").text)
            voyage = normal(row.find_element(By.CSS_SELECTOR, "[x-text='entry.DepartureVoyageNo']").text)
            port = normal(row.find_element(By.CSS_SELECTOR, "[x-text*='entry.PortName']").text)
            eta = _date(row.find_element(By.CSS_SELECTOR, "[x-text='entry.LiveArrivalFormattedDate']").text)
            etd = _date(row.find_element(By.CSS_SELECTOR, "[x-text='entry.LiveDepartureFormattedDate']").text)
        except Exception:
            continue
        if carrier_vessel != normal(vessel):
            continue
        record = {
            "carrier_vessel": carrier_vessel,
            "carrier_voyage": voyage,
            "port": port,
            "carrier_eta": eta,
            "carrier_ata": None,
            "carrier_etd": etd,
            "carrier_atd": None,
            "source_url": MSC_URL,
        }
        key = tuple(record.get(field) for field in (
            "carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_etd",
        ))
        if port and voyage and (eta or etd) and key not in seen:
            seen.add(key)
            rows.append(record)
    if not rows:
        raise ValueError("No MSC vessel-schedule rows were found")
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
                record["status"] = f"ERROR: {type(error).__name__}: {error}"
            statuses.append(record)
    finally:
        driver.quit()

    matched, updates = save_outputs(OUT, statuses, rows)
    print(f"\nFinished. Results: {OUT}", flush=True)
    print(f"Exact legs matched: {matched} | Date changes to review: {updates}", flush=True)


if __name__ == "__main__":
    main()
