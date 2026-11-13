"""CMA CGM Voyage Finder collector and ActiveTrack comparator.

The bot is read-only: it opens CMA CGM's official public voyage finder,
captures the published port calls, and writes comparison files.  It never
updates ActiveTrack and it never sends notices.
"""

from __future__ import annotations

import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BOT_ROOT = ROOT.parent
sys.path.insert(0, str(BOT_ROOT))

from carrier_bot_common import normal, requested_vessels, save_outputs, wait_for_human_verification  # noqa: E402


CMA_URL = "https://www.cma-cgm.com/ebusiness/schedules/voyage"
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")


class CarrierAccessBlocked(RuntimeError):
    """Raised when CMA CGM's public page refuses an automated browser."""

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
    options.add_argument(f"--user-data-dir={profile}")
    options.add_argument("--window-size=1500,1000")
    options.add_argument("--disable-gpu")
    options.page_load_strategy = "eager"
    return webdriver.Edge(options=options, service=Service())


def _date(value: str | None) -> str | None:
    match = re.search(r"\b\d{2}-[A-Z]{3}-\d{4}\b", normal(value))
    if not match:
        return None
    try:
        return datetime.strptime(match.group(0), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return None


def _dismiss_cookies(driver: webdriver.Edge) -> None:
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
        pass
    driver._globactiv_cookies_checked = True


def search(driver: webdriver.Edge, vessel: str) -> None:
    driver.get(CMA_URL)
    wait_for_human_verification(driver, "CMA CGM")
    wait = WebDriverWait(driver, 45)
    try:
        field = WebDriverWait(driver, 8).until(lambda page: page.find_element(By.ID, "VesselName"))
    except Exception as error:
        raise CarrierAccessBlocked(
            "CMA CGM verification or access is still incomplete after the 60-second wait; use MANUAL TRACKING for this run"
        ) from error
    _dismiss_cookies(driver)
    field.send_keys(Keys.CONTROL, "a")
    field.send_keys(Keys.DELETE)
    field.send_keys(vessel)

    def exact_option(page):
        for item in page.find_elements(By.CSS_SELECTOR, "#VesselName_listbox li[role='option']"):
            label = normal(item.text.split(";", 1)[0])
            if item.is_displayed() and label == normal(vessel):
                return item
        return None

    wait.until(exact_option).click()
    selected = normal(field.get_attribute("value"))
    if ";" not in selected or not selected.startswith(normal(vessel)):
        raise ValueError("CMA CGM vessel identity was not selected from the official list")
    wait.until(lambda page: page.find_element(By.ID, "voyageSub").is_enabled()).click()
    wait.until(lambda page: "/voyage/detail" in page.current_url and page.find_elements(By.CSS_SELECTOR, "#VoyageResult table.k-grid-table tbody tr"))

    result_vessel = normal(
        driver.find_element(By.CSS_SELECTOR, "#VoyageResult .resume-filter li:nth-child(2) strong").text
    )
    if result_vessel != normal(vessel):
        raise ValueError("CMA CGM result identity did not match the requested vessel")


def extract(driver: webdriver.Edge, vessel: str) -> list[dict]:
    voyage = normal(
        driver.find_element(By.CSS_SELECTOR, "#VoyageResult .resume-filter li:first-child strong").text
    )
    carrier_vessel = normal(
        driver.find_element(By.CSS_SELECTOR, "#VoyageResult .resume-filter li:nth-child(2) strong").text
    )
    rows: list[dict] = []
    seen: set[tuple] = set()
    for table_row in driver.find_elements(By.CSS_SELECTOR, "#VoyageResult table.k-grid-table tbody tr"):
        cells = table_row.find_elements(By.CSS_SELECTOR, "td")
        if len(cells) < 3:
            continue
        try:
            port = normal(cells[0].find_element(By.CSS_SELECTOR, "a:first-child").text)
        except Exception:
            port = normal(cells[0].text.splitlines()[0] if cells[0].text else "")
        record = {
            "carrier_vessel": carrier_vessel,
            "carrier_voyage": voyage,
            "port": port,
            "carrier_eta": _date(cells[1].text),
            "carrier_ata": None,
            "carrier_etd": _date(cells[2].text),
            "carrier_atd": None,
            "source_url": driver.current_url,
        }
        key = tuple(record.get(field) for field in (
            "carrier_vessel", "carrier_voyage", "port", "carrier_eta", "carrier_etd",
        ))
        if port and voyage and (record["carrier_eta"] or record["carrier_etd"]) and key not in seen:
            seen.add(key)
            rows.append(record)
    if carrier_vessel != normal(vessel) or not rows:
        raise ValueError("No CMA CGM voyage rows were found")
    return rows


def main() -> None:
    requested = requested_vessels(ROOT)
    OUT.mkdir(parents=True, exist_ok=True)
    statuses: list[dict] = []
    rows: list[dict] = []
    blocked_reason: str | None = None
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
            if blocked_reason:
                record["status"] = blocked_reason
            else:
                try:
                    search(driver, vessel)
                    record["rows"] = extract(driver, vessel)
                    rows.extend(record["rows"])
                except CarrierAccessBlocked as error:
                    blocked_reason = f"BLOCKED: {error}"
                    record["status"] = blocked_reason
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
