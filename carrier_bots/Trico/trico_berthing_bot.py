"""Read Tricologi's published Colombo berthing schedule once.

This collector is supplementary.  It exports ETB/terminal planning data and
never converts ETB into ETA or ATA.
"""

from __future__ import annotations

import os

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S")
WATCH_ROOT = ROOT.parents[1] / "vessel-watch"

# Reuse the tested Vessel Watch collector and its Selenium environment.
try:
    import selenium  # noqa: F401
except ModuleNotFoundError:
    tracker_python = WATCH_ROOT / ".venv" / "Scripts" / "python.exe"
    if tracker_python.exists() and Path(sys.executable).resolve() != tracker_python.resolve():
        raise SystemExit(subprocess.run([str(tracker_python), str(Path(__file__).resolve()), *sys.argv[1:]]).returncode)
    raise SystemExit("Selenium is not installed. Run vessel-watch\\01_SETUP.bat once, then retry.")

sys.path.insert(0, str(WATCH_ROOT))

from watch.browser import Browser  # noqa: E402
from watch.engine import collect_with_retry  # noqa: E402


CALL_FIELDS = [
    "vessel", "voyage", "port", "terminal", "etb", "etb_time",
    "source_updated_at", "checked_at", "source_url", "source_kind", "raw",
]


def main() -> None:
    config = json.loads((WATCH_ROOT / "sites.json").read_text(encoding="utf-8"))
    site = next(item for item in config["sites"] if item.get("id") == "trico")
    OUT.mkdir(parents=True, exist_ok=True)
    audit = OUT / "evidence"
    audit.mkdir(exist_ok=True)
    print("[1/1] Published Colombo berthing schedule")

    browser = None
    status = "OK"
    detail = ""
    calls: list[dict] = []
    try:
        browser = Browser(ROOT / "edge_profile", headless=os.environ.get("TRACKING_HEADLESS") == "1", family="edge")
        calls = collect_with_retry(browser, site, "All published Colombo calls", audit, print)
        detail = f"{len(calls)} published vessel/voyage rows read"
    except Exception as exc:
        status = f"ERROR: {type(exc).__name__}"
        detail = str(exc).split("Stacktrace:", 1)[0][:550]
    finally:
        if browser is not None:
            browser.close()

    with (OUT / "trico_calls.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALL_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(calls)
    (OUT / "results.json").write_text(
        json.dumps([{
            "vessel": "ALL PUBLISHED COLOMBO CALLS",
            "status": status,
            "detail": detail,
            "rows": len(calls),
        }], indent=2),
        encoding="utf-8",
    )
    print(f"Trico: {detail or status}")
    print(f"Finished. Results: {OUT}")
    if status != "OK":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
