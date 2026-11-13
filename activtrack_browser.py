"""Read-only ActivTrack capture through a browser opened by the application.

The user logs in and applies the filter manually.  The application then walks
all visible booking pages and reads JSON responses already delivered to that
browser.  Credentials are never requested or stored by this program.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.error import URLError
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


BOOKINGS_URL = "https://activtrack.globactiv.com/sea-import/bookings"
SCHEDULES_URL = "https://activtrack.globactiv.com/sea-import/vessel-schedule"


class BrowserCaptureError(RuntimeError):
    """Raised when the signed-in browser data cannot be captured completely."""


def _walk_json(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _score(value: Any) -> int:
    if value in (None, "", [], {}):
        return 0
    if isinstance(value, dict):
        return 2 + sum(_score(item) for item in value.values())
    if isinstance(value, list):
        return 2 + sum(_score(item) for item in value)
    return 1


def _merge_richer(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    result = dict(left)
    for key, value in right.items():
        current = result.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = _merge_richer(current, value)
        elif _score(value) > _score(current):
            result[key] = value
    return result


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, ""):
            return value
    return None


def _decode_response_payloads(body: str) -> list[Any]:
    """Decode normal JSON plus JSON objects embedded in Next.js/RSC text."""
    if not body:
        return []
    try:
        return [json.loads(body)]
    except (json.JSONDecodeError, TypeError):
        pass

    payloads: list[Any] = []
    decoder = json.JSONDecoder()
    # Next.js flight responses commonly prefix each JSON value with an id such
    # as ``1:``.  Parsing line by line avoids an expensive scan of a large body.
    for line in body[:8_000_000].splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        for marker in ("{", "["):
            start = candidate.find(marker)
            if start < 0:
                continue
            try:
                value, _end = decoder.raw_decode(candidate[start:])
            except json.JSONDecodeError:
                continue
            payloads.append(value)
            break
        if len(payloads) >= 500:
            break
    return payloads


def _booking_reference(data: dict[str, Any]) -> str:
    value = _first(data, "reference_number", "booking_reference", "bookingReference", "reference")
    text = str(value or "").strip()
    return text if re.match(r"^B[-/]", text, re.I) else ""


def _schedule_reference(data: dict[str, Any]) -> str:
    value = _first(
        data,
        "reference_number",
        "schedule_reference",
        "vessel_schedule_reference_number",
        "vesselScheduleReferenceNumber",
        "vessel_schedule_reference",
        "reference",
    )
    text = str(value or "").strip()
    return text if re.match(r"^VS[-/]", text, re.I) else ""


def _has_schedule_link(data: dict[str, Any]) -> bool:
    return any(
        key in data
        for key in (
            "vessel_schedule_id",
            "vesselScheduleId",
            "schedule_id",
            "vessel_schedule_reference_number",
            "vesselScheduleReferenceNumber",
            "vessel_schedule_reference",
            "vessel_schedule",
            "vesselSchedule",
            "schedule",
        )
    )


def _has_leg_data(data: dict[str, Any]) -> bool:
    legs = _first(data, "legs", "vessel_legs", "schedule_legs", "vessel_schedule_legs", "transshipments")
    if isinstance(legs, list) and legs:
        return True
    return bool(_first(data, "vessel_name", "vessel", "etd", "eta"))


def _entity_name(value: Any) -> str:
    if isinstance(value, dict):
        value = _first(value, "name", "label", "title", "vessel_name", "port_name")
    return str(value or "").strip()


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _entity_name(value)).strip().upper()


def _date_text(value: Any) -> str:
    text = str(value or "").strip()
    match = re.search(r"\d{4}-\d{2}-\d{2}", text)
    return match.group(0) if match else text[:10]


def _schedule_legs(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    legs = _first(schedule, "legs", "vessel_legs", "schedule_legs", "vessel_schedule_legs", "transshipments")
    if isinstance(legs, list) and legs:
        return [item for item in legs if isinstance(item, dict)]
    return [schedule]


def _leg_value(leg: dict[str, Any], *keys: str) -> Any:
    return _first(leg, *keys)


def _schedule_match_score(booking: dict[str, Any], schedule: dict[str, Any]) -> int:
    vessel = _normalized_text(booking.get("vessel_name"))
    voyage = _normalized_text(booking.get("voyage"))
    etd = _date_text(booking.get("etd"))
    pol = _normalized_text(booking.get("departure_port"))
    eta = _date_text(booking.get("eta"))
    if not vessel or not voyage:
        return 0

    best = 0
    legs = _schedule_legs(schedule)
    for leg in legs:
        score = 0
        leg_vessel = _normalized_text(_leg_value(leg, "vessel_name", "vessel", "vesselName"))
        leg_voyage = _normalized_text(_leg_value(leg, "voyage", "voyage_number", "voyageNo"))
        leg_etd = _date_text(_leg_value(leg, "etd", "estimated_departure", "estimatedDeparture"))
        leg_pol = _normalized_text(_leg_value(leg, "departure_port", "pol", "origin_port", "origin"))
        if leg_vessel == vessel:
            score += 6
        if leg_voyage == voyage:
            score += 6
        if etd and leg_etd == etd:
            score += 5
        if pol and leg_pol == pol:
            score += 2
        best = max(best, score)

    schedule_eta = _date_text(_leg_value(legs[-1], "eta", "estimated_arrival", "estimatedArrival")) if legs else ""
    if eta and schedule_eta == eta:
        best += 2
    return best


class ActivTrackBrowser:
    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self.log = log or (lambda _message: None)
        self.driver = None
        self._bookings: dict[str, dict[str, Any]] = {}
        self._schedules: dict[str, dict[str, Any]] = {}
        self._response_urls: set[str] = set()

    @property
    def is_open(self) -> bool:
        if self.driver is None:
            return False
        try:
            _ = self.driver.current_url
            return True
        except Exception:
            return False

    @staticmethod
    def _profile_dir() -> Path:
        return Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ActivTrackReportGenerator" / "BrowserProfile"

    @staticmethod
    def _edge_executable() -> Path | None:
        candidates = (
            Path(os.getenv("PROGRAMFILES(X86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.getenv("PROGRAMFILES", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        )
        return next((path for path in candidates if path.is_file()), None)

    def _devtools_port(self) -> int | None:
        active_port = self._profile_dir() / "DevToolsActivePort"
        try:
            port = active_port.read_text(encoding="utf-8").splitlines()[0].strip()
        except (OSError, IndexError):
            return None
        return int(port) if port.isdigit() else None

    def _activate_existing_bookings_tab(self) -> bool:
        """Focus the dedicated Bookings tab through Edge's local endpoint.

        This avoids starting Selenium just to show a page. Selenium is attached
        later, in the report worker, when browser data is actually required.
        """
        port = self._devtools_port()
        if port is None:
            return False
        base = f"http://127.0.0.1:{port}"
        try:
            with urlopen(f"{base}/json/list", timeout=0.65) as response:
                targets = json.loads(response.read().decode("utf-8"))
            pages = [item for item in targets if item.get("type") == "page"]
            target = next(
                (item for item in pages if "/sea-import/bookings" in str(item.get("url", ""))),
                None,
            )
            if target is None:
                request = Request(f"{base}/json/new?{quote(BOOKINGS_URL, safe='')}", method="PUT")
                with urlopen(request, timeout=0.65) as response:
                    target = json.loads(response.read().decode("utf-8"))
            target_id = str(target.get("id", ""))
            if target_id:
                request = Request(f"{base}/json/activate/{quote(target_id, safe='')}", method="PUT")
                with urlopen(request, timeout=0.65):
                    pass
            self.log("ActivTrack Bookings is ready in the existing Edge window.")
            return True
        except (OSError, URLError, ValueError, TypeError, json.JSONDecodeError):
            return False

    @staticmethod
    def _focus_activtrack_window() -> None:
        """Bring the visible ActivTrack Edge window forward on Windows."""
        if os.name != "nt":
            return
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def visit(hwnd, _lparam):
                length = user32.GetWindowTextLengthW(hwnd)
                if length <= 0 or not user32.IsWindowVisible(hwnd):
                    return True
                title = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, title, length + 1)
                text = title.value.upper()
                if "ACTIVTRACK" in text and "EDGE" in text:
                    user32.ShowWindow(hwnd, 9)
                    user32.SetForegroundWindow(hwnd)
                    return False
                return True

            user32.EnumWindows(enum_proc(visit), 0)
        except Exception:
            pass

    def open(self) -> None:
        profile_dir = self._profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        # This is the normal fast path, including after an app restart. It
        # avoids a potentially slow WebDriver health check and driver startup.
        if self._activate_existing_bookings_tab():
            self._focus_activtrack_window()
            return

        if self.is_open:
            if "/sea-import/bookings" not in str(self.driver.current_url):
                self.driver.execute_cdp_cmd("Page.navigate", {"url": BOOKINGS_URL})
            else:
                self.driver.execute_cdp_cmd("Page.bringToFront", {})
            self._focus_activtrack_window()
            self.log("The application-controlled ActivTrack browser is already open. Your current filter was preserved.")
            return

        edge = self._edge_executable()
        if edge is not None:
            subprocess.Popen(
                [
                    str(edge),
                    f"--user-data-dir={profile_dir}",
                    "--remote-debugging-port=0",
                    "--start-maximized",
                    "--disable-notifications",
                    "--no-first-run",
                    "--no-default-browser-check",
                    BOOKINGS_URL,
                ],
            )
            self.log("ActivTrack is opening in Edge. The control connection will be prepared when you generate a report.")
            return

        # Portable fallback for computers where Edge is not in a standard path.
        try:
            from selenium import webdriver
        except ImportError as exc:
            raise BrowserCaptureError("Selenium is not installed. Run 01_SETUP.bat once, then start the app again.") from exc
        errors: list[str] = []
        for browser_name, options_type, driver_type in (
            ("Microsoft Edge", webdriver.EdgeOptions, webdriver.Edge),
            ("Google Chrome", webdriver.ChromeOptions, webdriver.Chrome),
        ):
            try:
                options = options_type()
                options.add_argument(f"--user-data-dir={profile_dir}")
                options.add_argument("--start-maximized")
                options.add_argument("--disable-notifications")
                options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
                self.driver = driver_type(options=options)
                self.driver.execute_cdp_cmd("Network.enable", {})
                self.driver.get(BOOKINGS_URL)
                self.log(f"{browser_name} opened. Log in and apply the required filter.")
                return
            except Exception as exc:
                errors.append(f"{browser_name}: {exc}")
                self.driver = None
        raise BrowserCaptureError("Could not open Edge or Chrome. " + " | ".join(errors))

    def close(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def attach_existing(self) -> bool:
        """Reconnect only to the dedicated Edge window; never open/navigate a page."""
        if self.is_open:
            return True
        active_port = self._profile_dir() / "DevToolsActivePort"
        deadline = time.time() + 4.0
        port = None
        while time.time() < deadline:
            candidate = self._devtools_port()
            if candidate is not None:
                try:
                    with urlopen(f"http://127.0.0.1:{candidate}/json/version", timeout=0.35):
                        pass
                    port = str(candidate)
                    break
                except (OSError, URLError):
                    pass
            time.sleep(0.1)
        if port is None:
            return False
        try:
            from selenium import webdriver
            options = webdriver.EdgeOptions()
            options.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
            self.driver = webdriver.Edge(options=options)
            booking_handle = None
            for handle in self.driver.window_handles:
                self.driver.switch_to.window(handle)
                if "/sea-import/bookings" in str(self.driver.current_url):
                    booking_handle = handle
                    break
            if booking_handle is None:
                self.driver.execute_script("window.open(arguments[0], '_blank')", BOOKINGS_URL)
                self.driver.switch_to.window(self.driver.window_handles[-1])
            self.log("Reconnected to the same Edge page; its filter was preserved.")
            return True
        except Exception:
            self.driver = None
            return False

    def _ingest_payload(self, payload: Any) -> None:
        for node in _walk_json(payload):
            booking_ref = _booking_reference(node)
            if booking_ref and _has_schedule_link(node):
                flattened = dict(node)
                nested_schedule = node.get("vessel_schedule") or node.get("vesselSchedule") or node.get("schedule")
                if isinstance(nested_schedule, dict):
                    flattened.setdefault("vessel_schedule_id", _first(nested_schedule, "id", "schedule_id"))
                    flattened.setdefault("vessel_schedule_reference_number", _schedule_reference(nested_schedule) or _first(nested_schedule, "reference_number", "reference"))
                self._bookings[booking_ref] = _merge_richer(self._bookings.get(booking_ref, {}), flattened)

            schedule_ref = _schedule_reference(node)
            if schedule_ref and _has_leg_data(node):
                normalized_node = dict(node)
                if "legs" not in normalized_node:
                    for alias in ("vessel_legs", "schedule_legs", "vessel_schedule_legs", "transshipments"):
                        if isinstance(normalized_node.get(alias), list):
                            normalized_node["legs"] = normalized_node[alias]
                            break
                self._schedules[schedule_ref] = _merge_richer(self._schedules.get(schedule_ref, {}), normalized_node)

    def _drain_network(self) -> int:
        if not self.is_open:
            return 0
        captured = 0
        try:
            messages = self.driver.get_log("performance")
        except Exception:
            return 0
        for entry in messages:
            try:
                outer = json.loads(entry["message"])
                message = outer.get("message", {})
                if message.get("method") != "Network.responseReceived":
                    continue
                params = message.get("params", {})
                response = params.get("response", {})
                resource_type = params.get("type", "")
                mime = str(response.get("mimeType", ""))
                url = str(response.get("url", ""))
                if resource_type not in ("XHR", "Fetch") and "json" not in mime.lower():
                    continue
                request_id = params.get("requestId")
                if not request_id:
                    continue
                body = self.driver.execute_cdp_cmd("Network.getResponseBody", {"requestId": request_id}).get("body", "")
                if not body:
                    continue
                before = len(self._bookings) + len(self._schedules)
                payloads = _decode_response_payloads(body)
                for payload in payloads:
                    self._ingest_payload(payload)
                after = len(self._bookings) + len(self._schedules)
                if after > before:
                    captured += after - before
                    self._response_urls.add(url)
            except Exception:
                continue
        return captured

    def _drain_network_for(self, seconds: float = 1.5) -> int:
        """Collect fresh responses while React finishes a table reload."""
        deadline = time.time() + max(0.25, seconds)
        captured = 0
        while time.time() < deadline:
            captured += self._drain_network()
            time.sleep(0.2)
        return captured

    def _visible_booking_rows(self) -> list[dict[str, Any]]:
        script = r"""
const tables = [...document.querySelectorAll('table')];
const table = tables.find(candidate => {
  const headers = [...candidate.querySelectorAll('thead th')].map(x => (x.innerText || '').trim().toUpperCase());
  return headers.includes('ID') && headers.includes('VESSEL') && headers.includes('VOYAGE');
});
if (!table) return [];
const headers = [...table.querySelectorAll('thead th')].map(x => (x.innerText || '').trim().toUpperCase());
const value = (record, name) => record[name] || '';
const output = [];
for (const row of table.querySelectorAll('tbody tr')) {
  const cells = [...row.querySelectorAll('td')].map(x => (x.innerText || '').trim());
  const record = {};
  headers.forEach((header, index) => { if (header) record[header] = cells[index] || ''; });
  const reference = value(record, 'ID');
  if (!/^B[-/]/i.test(reference)) continue;
  output.push({
    reference_number: reference,
    booking_confirmed_date: value(record, 'CONFIRMED DATE'),
    status_label: value(record, 'BL STATUS') || value(record, 'PLAN'),
    customer_name: value(record, 'CUSTOMER'),
    agent_name: value(record, 'AGENT'),
    shipment_type: value(record, 'TYPE'),
    departure_port: value(record, 'POL'),
    etd: value(record, 'ETD'),
    vessel_name: value(record, 'VESSEL'),
    voyage: value(record, 'VOYAGE'),
    eta: value(record, 'ETA'),
    hbl_number: value(record, 'HBL NUMBER'),
    mbl_number: value(record, 'MBL NUMBER')
  });
}
return output;
"""
        try:
            return list(self.driver.execute_script(script) or [])
        except Exception:
            return []

    def _visible_booking_refs(self) -> list[str]:
        return [str(item.get("reference_number", "")) for item in self._visible_booking_rows() if item.get("reference_number")]

    def _visible_schedule_rows(self) -> list[dict[str, Any]]:
        script = r"""
const tables = [...document.querySelectorAll('table')];
const table = tables.find(candidate => {
  const headers = [...candidate.querySelectorAll('thead th')].map(x => (x.innerText || '').trim().toUpperCase());
  return headers.includes('ID') && headers.includes('VESSEL / VOYAGE') && headers.includes('ROUTE');
});
if (!table) return [];
const rows = [];
for (const row of table.querySelectorAll('tbody tr')) {
  const cells = [...row.querySelectorAll('td')];
  const text = index => (cells[index]?.innerText || '').trim();
  const reference = text(1);
  if (!/^VS[-/]/i.test(reference)) continue;
  const routeLines = text(8).split(/\r?\n/).map(x => x.trim()).filter(Boolean);
  const vesselLines = text(9).split(/\r?\n/).map(x => x.trim()).filter(Boolean)
    .filter(x => !/^(direct|transshipment)$/i.test(x));
  const routeType = routeLines.find(x => /^(direct|transshipment)$/i.test(x)) || (vesselLines.length > 1 ? 'transshipment' : 'direct');
  const pol = routeLines[0] || '';
  const pod = routeLines[routeLines.length - 1] || '';
  const legs = vesselLines.map((line, index) => {
    const splitAt = line.lastIndexOf(' / ');
    const vessel = splitAt >= 0 ? line.slice(0, splitAt).trim() : line;
    const voyage = splitAt >= 0 ? line.slice(splitAt + 3).trim() : '';
    return {
      vessel_name: vessel,
      voyage,
      departure_port: index === 0 ? pol : '',
      arrival_port: index === vesselLines.length - 1 ? pod : '',
      cfs_cutoff: index === 0 ? text(4) : '',
      cy_cutoff: index === 0 ? text(5) : '',
      etd: index === 0 ? text(6) : '',
      atd: index === 0 ? text(7) : '',
      eta: index === vesselLines.length - 1 ? text(10) : '',
      ata: index === vesselLines.length - 1 ? text(11) : ''
    };
  });
  rows.push({
    id: reference,
    reference_number: reference,
    carrier_name: text(2),
    route_type: String(routeType).toLowerCase(),
    departure_port: pol,
    arrival_port: pod,
    legs
  });
}
return rows;
"""
        try:
            return list(self.driver.execute_script(script) or [])
        except Exception:
            return []

    def _page_text(self) -> str:
        try:
            return str(self.driver.find_element("tag name", "body").text)
        except Exception:
            return ""

    def _expected_total(self) -> int | None:
        matches = re.findall(r"\b\d+\s*-\s*\d+\s+of\s+([\d,]+)\b", self._page_text(), flags=re.I)
        totals = [int(value.replace(",", "")) for value in matches]
        return max(totals) if totals else None

    def _find_next_button(self):
        script = r"""
const nodes = [...document.querySelectorAll('button,a,[role="button"]')];
for (const el of nodes) {
  const text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim();
  if (/^next$/i.test(text)) return el;
}
return null;
"""
        try:
            return self.driver.execute_script(script)
        except Exception:
            return None

    def _page_size_value(self) -> str:
        script = r"""
const select = [...document.querySelectorAll('select')]
  .find(el => [...el.options].some(option => (option.textContent || '').trim() === '250'));
return select ? String(select.value || '') : '';
"""
        try:
            return str(self.driver.execute_script(script) or "")
        except Exception:
            return ""

    def _set_page_size(self, value: str) -> bool:
        """Select a table page size and wait for ActivTrack to reload it."""
        try:
            from selenium.webdriver.support.ui import Select
        except ImportError:
            return False
        script = r"""
return [...document.querySelectorAll('select')]
  .find(el => [...el.options].some(option => (option.textContent || '').trim() === '250')) || null;
"""
        try:
            element = self.driver.execute_script(script)
            if element is None:
                return False
            selector = Select(element)
            try:
                selector.select_by_value(str(value))
            except Exception:
                selector.select_by_visible_text(str(value))
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if self._page_size_value() == str(value):
                    break
                time.sleep(0.2)
            # Capture immediately: Chrome may evict a large response body if it
            # sits in the performance log until the user clicks Generate.
            self._drain_network_for(1.6)
            return self._page_size_value() == str(value)
        except Exception:
            return False

    def _refresh_current_filter(self) -> None:
        """Re-request page 1 without changing the user's active filter."""
        current = self._page_size_value()
        if not current:
            self.log("Page-size control was not found; using the loaded booking page as-is.")
            self._drain_network_for(1.0)
            return
        self.log("Refreshing the current ActivTrack filter so page 1 can be captured reliably…")
        # Changing away and back forces the table API to return the current
        # filtered rows again.  It also resets pagination to page 1.
        if current == "250":
            self._set_page_size("100")
        if not self._set_page_size("250"):
            self.log("Could not switch to 250 items; continuing with the current page size.")
            self._drain_network_for(1.0)

    @staticmethod
    def _is_disabled(element) -> bool:
        if element is None:
            return True
        try:
            disabled = element.get_attribute("disabled")
            aria = element.get_attribute("aria-disabled")
            classes = str(element.get_attribute("class") or "").lower()
            return disabled is not None or aria == "true" or "disabled" in classes
        except Exception:
            return True

    def _wait_for_page_change(self, previous_refs: list[str], timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.25)
            current = self._visible_booking_refs()
            if current and current != previous_refs:
                return
        raise BrowserCaptureError("ActivTrack did not move to the next booking page. Please check the browser and try again.")

    def _collect_booking_pages(self) -> tuple[list[dict[str, Any]], int, int | None]:
        if "/sea-import/bookings" not in str(self.driver.current_url):
            self.driver.get(BOOKINGS_URL)
            time.sleep(2)
        self._refresh_current_filter()
        self._drain_network_for(0.8)
        all_dom_rows: dict[str, dict[str, Any]] = {}
        pages = 0
        expected = self._expected_total()
        seen_signatures: set[tuple[str, ...]] = set()
        while pages < 500:
            visible_rows = self._visible_booking_rows()
            refs = [str(item.get("reference_number", "")) for item in visible_rows if item.get("reference_number")]
            if not refs:
                raise BrowserCaptureError("No booking rows are visible. Please log in, apply the filter, and wait for the table to load.")
            signature = tuple(refs[:5] + refs[-5:])
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            for item in visible_rows:
                reference = str(item.get("reference_number", ""))
                if reference:
                    all_dom_rows[reference] = item
            pages += 1
            self._drain_network_for(0.5)
            self.log(f"Captured booking page {pages}: {len(all_dom_rows)} complete table rows read.")
            next_button = self._find_next_button()
            if self._is_disabled(next_button):
                break
            try:
                self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_button)
                self.driver.execute_script("arguments[0].click();", next_button)
            except Exception as exc:
                raise BrowserCaptureError(f"Could not open the next booking page: {exc}") from exc
            self._wait_for_page_change(refs)
        self._drain_network()
        return [all_dom_rows[key] for key in sorted(all_dom_rows)], pages, expected

    def _find_search_input(self):
        script = r"""
const inputs = [...document.querySelectorAll('input')];
for (const el of inputs) {
  const style = window.getComputedStyle(el);
  const visible = style.display !== 'none' && style.visibility !== 'hidden' && el.getBoundingClientRect().width > 0;
  const label = [el.placeholder, el.getAttribute('aria-label'), el.name].filter(Boolean).join(' ');
  if (visible && /search/i.test(label)) return el;
}
return null;
"""
        try:
            return self.driver.execute_script(script)
        except Exception:
            return None

    def _complete_schedule_refs(self) -> set[str]:
        completed: set[str] = set()
        for ref, schedule in self._schedules.items():
            legs = _first(schedule, "legs", "vessel_legs", "schedule_legs", "vessel_schedule_legs", "transshipments")
            if isinstance(legs, list) and legs:
                completed.add(ref)
            elif _has_leg_data(schedule):
                completed.add(ref)
        return completed

    def _click_schedule_result(self, reference: str) -> None:
        try:
            elements = self.driver.find_elements("xpath", f"//*[normalize-space()={json.dumps(reference)}]")
        except Exception:
            return
        if not elements:
            return
        old_url = self.driver.current_url
        try:
            self.driver.execute_script("arguments[0].click();", elements[0])
            time.sleep(0.7)
            self._drain_network()
            if self.driver.current_url != old_url:
                self.driver.back()
                time.sleep(0.7)
            else:
                try:
                    from selenium.webdriver.common.keys import Keys
                    self.driver.find_element("tag name", "body").send_keys(Keys.ESCAPE)
                except Exception:
                    pass
        except Exception:
            return

    def _wait_for_schedule_page_change(self, previous_refs: list[str], timeout: float = 12.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(0.25)
            current = [str(item.get("reference_number", "")) for item in self._visible_schedule_rows()]
            if current and current != previous_refs:
                return
        raise BrowserCaptureError("ActivTrack did not move to the next vessel-schedule page.")

    def _collect_all_schedules(self) -> None:
        original_handle = self.driver.current_window_handle
        self.driver.execute_script("window.open(arguments[0], '_blank');", SCHEDULES_URL)
        self.driver.switch_to.window(self.driver.window_handles[-1])
        time.sleep(2.0)
        self._drain_network_for(1.0)
        current_size = self._page_size_value()
        if current_size == "250":
            self._set_page_size("100")
        self._set_page_size("250")

        dom_schedules: dict[str, dict[str, Any]] = {}
        seen_signatures: set[tuple[str, ...]] = set()
        pages = 0
        while pages < 100:
            visible_rows = self._visible_schedule_rows()
            refs = [str(item.get("reference_number", "")) for item in visible_rows if item.get("reference_number")]
            if not refs:
                break
            signature = tuple(refs[:5] + refs[-5:])
            if signature in seen_signatures:
                break
            seen_signatures.add(signature)
            for item in visible_rows:
                reference = str(item.get("reference_number", ""))
                if reference:
                    dom_schedules[reference] = item
            pages += 1
            self._drain_network_for(0.6)
            self.log(
                f"Captured vessel-schedule page {pages}: {len(dom_schedules)} rows; "
                f"{len(self._complete_schedule_refs())} full schedule records."
            )
            next_button = self._find_next_button()
            if self._is_disabled(next_button):
                break
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", next_button)
            self.driver.execute_script("arguments[0].click();", next_button)
            self._wait_for_schedule_page_change(refs)
        self._drain_network_for(0.5)

        # DOM schedule rows are a safe fallback for direct sailings.  Richer API
        # records win because they contain every transshipment leg and cutoff.
        for reference, schedule in dom_schedules.items():
            if reference not in self._schedules:
                self._schedules[reference] = schedule
        try:
            self.driver.close()
            self.driver.switch_to.window(original_handle)
        except Exception:
            pass

    def _match_bookings_to_schedules(self, dom_bookings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        schedules_by_ref = dict(self._schedules)
        schedules_by_id: dict[str, dict[str, Any]] = {}
        for reference, schedule in schedules_by_ref.items():
            schedule.setdefault("reference_number", reference)
            schedule.setdefault("id", reference)
            schedules_by_id[str(_first(schedule, "id", "schedule_id") or reference)] = schedule

        matched_bookings: list[dict[str, Any]] = []
        used_refs: set[str] = set()
        unmatched: list[dict[str, Any]] = []
        for dom_booking in dom_bookings:
            reference = str(dom_booking.get("reference_number", ""))
            network_booking = self._bookings.get(reference, {})
            booking = _merge_richer(dom_booking, network_booking)
            linked_ref = str(
                _first(
                    network_booking,
                    "vessel_schedule_reference_number",
                    "vesselScheduleReferenceNumber",
                    "schedule_reference",
                    "vessel_schedule_reference",
                )
                or ""
            )
            linked_id = str(_first(network_booking, "vessel_schedule_id", "vesselScheduleId", "schedule_id") or "")
            nested = network_booking.get("vessel_schedule") or network_booking.get("vesselSchedule") or network_booking.get("schedule")
            if isinstance(nested, dict):
                linked_ref = linked_ref or _schedule_reference(nested)
                linked_id = linked_id or str(_first(nested, "id", "schedule_id") or "")

            selected = schedules_by_ref.get(linked_ref) if linked_ref else None
            if selected is None and linked_id:
                selected = schedules_by_id.get(linked_id)
            if selected is None:
                scored = sorted(
                    ((_schedule_match_score(dom_booking, schedule), ref, schedule) for ref, schedule in schedules_by_ref.items()),
                    key=lambda item: (-item[0], item[1]),
                )
                if scored and scored[0][0] >= 12:
                    if len(scored) == 1 or scored[0][0] > scored[1][0]:
                        selected = scored[0][2]
            if selected is None:
                unmatched.append(dom_booking)
                continue

            selected_ref = _schedule_reference(selected) or str(_first(selected, "reference_number", "reference") or "")
            selected_id = _first(selected, "id", "schedule_id") or selected_ref
            booking["vessel_schedule_id"] = selected_id
            booking["vessel_schedule_reference_number"] = selected_ref
            matched_bookings.append(booking)
            used_refs.add(selected_ref)

        if unmatched:
            sample = ", ".join(str(item.get("reference_number", "")) for item in unmatched[:8])
            raise BrowserCaptureError(
                f"The booking table was read successfully, but {len(unmatched)} booking(s) could not be linked "
                f"to an exact vessel schedule. Examples: {sample}. No Excel file was created to avoid incorrect vessel details."
            )

        used_schedules = [schedules_by_ref[ref] for ref in sorted(used_refs) if ref in schedules_by_ref]
        return matched_bookings, used_schedules

    def _filter_metadata(self, pages: int, expected: int | None) -> dict[str, Any]:
        etd_from = ""
        etd_to = ""
        try:
            inputs = self.driver.execute_script(r"""
return [...document.querySelectorAll('input')].map(el => ({
  value: el.value || '',
  label: [el.name, el.placeholder, el.getAttribute('aria-label'), el.id].filter(Boolean).join(' ')
}));
""") or []
            date_values = []
            for item in inputs:
                value = str(item.get("value", ""))
                label = str(item.get("label", ""))
                if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                    date_values.append(value)
                if "etd" in label.lower() and "from" in label.lower():
                    etd_from = value
                if "etd" in label.lower() and "to" in label.lower():
                    etd_to = value
            if not etd_from and date_values:
                etd_from = date_values[0]
            if not etd_to and len(date_values) > 1:
                etd_to = date_values[1]
        except Exception:
            pass
        return {
            "sea_freight": "Sea Import",
            "etd_from": etd_from or "Current ActivTrack filter",
            "etd_to": etd_to or "Current ActivTrack filter",
            "ui_pages": pages,
            "ui_total": expected,
        }

    def _latest_booking_api_url(self) -> str:
        """Return the most recent list URL used by the filtered booking table.

        ActivTrack keeps the visible filter in the API query string.  Reading
        the latest resource entry lets us preserve every filter field without
        trying to reverse-engineer the filter dialog.
        """
        script = r"""
const matches = performance.getEntriesByType('resource')
  .map(item => item.name || '')
  .filter(url => /^https:\/\/api\.activtrack\.globactiv\.com\/api\/sea-import\/bookings\?/i.test(url))
  .filter(url => !/\/export(?:\?|$)/i.test(url));
return matches.length ? matches[matches.length - 1] : '';
"""
        try:
            url = str(self.driver.execute_script(script) or "")
        except Exception:
            url = ""
        parts = urlsplit(url)
        if parts.scheme != "https" or parts.netloc.lower() != "api.activtrack.globactiv.com":
            raise BrowserCaptureError(
                "The filtered ActivTrack booking request was not found. Please keep the booking page open, "
                "apply the filter, wait for the table to finish loading, and try again."
            )
        if parts.path.rstrip("/") != "/api/sea-import/bookings":
            raise BrowserCaptureError("ActivTrack returned an unexpected booking data address. No report was created.")
        return url

    @staticmethod
    def _api_page_url(base_url: str, page: int, per_page: int = 1000) -> str:
        parts = urlsplit(base_url)
        pairs = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key not in {"page", "per_page"}]
        pairs.extend((("per_page", str(per_page)), ("page", str(page))))
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs, doseq=True), parts.fragment))

    def _fetch_api_json(self, url: str) -> dict[str, Any]:
        """Issue an authenticated, read-only GET from the signed-in page."""
        script = r"""
const url = arguments[0];
const done = arguments[arguments.length - 1];
fetch(url, {credentials: 'include', headers: {'Accept': 'application/json'}})
  .then(async response => done({status: response.status, body: await response.text()}))
  .catch(error => done({status: 0, error: String(error)}));
"""
        try:
            result = self.driver.execute_async_script(script, url) or {}
        except Exception as exc:
            raise BrowserCaptureError(f"ActivTrack booking data could not be read: {exc}") from exc
        status = int(result.get("status") or 0)
        if status != 200:
            detail = str(result.get("error") or result.get("body") or "")[:300]
            raise BrowserCaptureError(
                f"ActivTrack booking data request failed (HTTP {status}). Please confirm that you are logged in and retry. {detail}"
            )
        try:
            payload = json.loads(str(result.get("body") or ""))
        except json.JSONDecodeError as exc:
            raise BrowserCaptureError("ActivTrack returned unreadable booking data. No Excel file was created.") from exc
        if not isinstance(payload, dict):
            raise BrowserCaptureError("ActivTrack returned an unexpected booking response. No Excel file was created.")
        return payload

    def _fetch_api_json_many(self, urls: list[str], concurrency: int = 3) -> list[dict[str, Any]]:
        """Read several authenticated endpoints in one browser round trip.

        A small worker pool keeps the API load modest while removing dozens of
        Selenium round trips. Callers can fall back to the single-request path
        if a batch is rejected or interrupted.
        """
        if not urls:
            return []
        script = r"""
const urls = arguments[0] || [];
const limit = Math.max(1, Math.min(Number(arguments[1]) || 3, 4));
const done = arguments[arguments.length - 1];
const results = new Array(urls.length);
let next = 0;
async function worker() {
  while (true) {
    const index = next++;
    if (index >= urls.length) return;
    try {
      const response = await fetch(urls[index], {credentials: 'include', headers: {'Accept': 'application/json'}});
      results[index] = {status: response.status, body: await response.text()};
    } catch (error) {
      results[index] = {status: 0, error: String(error)};
    }
  }
}
Promise.all(Array.from({length: Math.min(limit, urls.length)}, () => worker()))
  .then(() => done({results}))
  .catch(error => done({error: String(error), results}));
"""
        try:
            response = self.driver.execute_async_script(script, urls, concurrency) or {}
        except Exception as exc:
            raise BrowserCaptureError(f"ActivTrack batch data could not be read: {exc}") from exc
        raw_results = response.get("results")
        if not isinstance(raw_results, list) or len(raw_results) != len(urls):
            raise BrowserCaptureError("ActivTrack returned an incomplete batch response.")
        payloads: list[dict[str, Any]] = []
        for result in raw_results:
            status = int((result or {}).get("status") or 0)
            if status != 200:
                detail = str((result or {}).get("error") or (result or {}).get("body") or "")[:300]
                raise BrowserCaptureError(
                    f"ActivTrack data request failed (HTTP {status}). Please confirm that you are logged in and retry. {detail}"
                )
            try:
                payload = json.loads(str((result or {}).get("body") or ""))
            except json.JSONDecodeError as exc:
                raise BrowserCaptureError("ActivTrack returned unreadable batch data.") from exc
            if not isinstance(payload, dict):
                raise BrowserCaptureError("ActivTrack returned an unexpected batch response.")
            payloads.append(payload)
        return payloads

    def _capture_filtered_api(self, refresh: bool = True) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int]:
        """Capture every filtered booking and its exact nested vessel schedule."""
        if refresh:
            self._refresh_current_filter()
        base_url = self._latest_booking_api_url()
        self._captured_booking_url = base_url
        visible_total = self._expected_total()
        visible_refs = set(self._visible_booking_refs())
        bookings: dict[str, dict[str, Any]] = {}
        incomplete_bookings: dict[str, dict[str, Any]] = {}
        schedules: dict[str, dict[str, Any]] = {}
        page = 1
        expected = 0
        first_payload = self._fetch_api_json(self._api_page_url(base_url, 1))
        first_pagination = first_payload.get("pagination") if isinstance(first_payload.get("pagination"), dict) else {}
        last_page = max(1, int(first_pagination.get("last_page") or 1))
        payloads_by_page: dict[int, dict[str, Any]] = {1: first_payload}
        if last_page > 1:
            remaining_urls = [self._api_page_url(base_url, item) for item in range(2, last_page + 1)]
            try:
                remaining_payloads = self._fetch_api_json_many(remaining_urls)
            except BrowserCaptureError:
                self.log("Fast booking-page batch was unavailable; completing the same pages normally.")
                remaining_payloads = [self._fetch_api_json(url) for url in remaining_urls]
            payloads_by_page.update({item: payload for item, payload in enumerate(remaining_payloads, 2)})
        while page <= last_page:
            payload = payloads_by_page.get(page)
            if payload is None:
                payload = self._fetch_api_json(self._api_page_url(base_url, page))
            records = payload.get("data")
            pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
            if not isinstance(records, list):
                raise BrowserCaptureError(f"ActivTrack booking page {page} did not contain a usable record list.")
            expected = int(pagination.get("total") or expected or len(records))
            last_page = max(last_page, int(pagination.get("last_page") or 1))
            for record_index, raw in enumerate(records, 1):
                if not isinstance(raw, dict):
                    key = f"page-{page}-row-{record_index}"
                    incomplete_bookings[key] = {
                        "reference_number": "",
                        "capture_issue": "ActivTrack returned an unreadable booking row.",
                    }
                    continue
                booking_ref = _booking_reference(raw)
                flattened = dict(raw)
                status = raw.get("status")
                if isinstance(status, dict):
                    flattened.setdefault("status_label", _first(status, "display_name", "label", "name", "value"))
                nested_schedule = raw.get("vessel_schedule") or raw.get("vesselSchedule") or raw.get("schedule")
                if not booking_ref:
                    key = f"page-{page}-row-{record_index}"
                    flattened["capture_issue"] = "Booking reference and vessel schedule are missing."
                    incomplete_bookings[key] = flattened
                    continue
                if not isinstance(nested_schedule, dict):
                    flattened["capture_issue"] = "No vessel schedule is assigned to this booking in ActiveTrack."
                    incomplete_bookings[booking_ref] = flattened
                    continue
                schedule_ref = _schedule_reference(nested_schedule)
                schedule_id = str(_first(nested_schedule, "id", "schedule_id", "vessel_schedule_id") or "")
                key = schedule_ref or schedule_id
                if not key:
                    flattened["capture_issue"] = "The linked vessel schedule has no usable ID or VS reference."
                    incomplete_bookings[booking_ref] = flattened
                    continue
                flattened.setdefault("vessel_schedule_id", _first(nested_schedule, "id", "schedule_id", "vessel_schedule_id"))
                flattened.setdefault("vessel_schedule_reference_number", schedule_ref)
                bookings[booking_ref] = flattened
                incomplete_bookings.pop(booking_ref, None)
                schedules[key] = _merge_richer(schedules.get(key, {}), nested_schedule)
            self.log(
                f"Captured booking data page {page} of {last_page}: {len(bookings)} linked, "
                f"{len(incomplete_bookings)} missing a usable schedule."
            )
            page += 1

        captured = len(bookings) + len(incomplete_bookings)
        if expected and captured != expected:
            missing = expected - captured
            raise BrowserCaptureError(
                f"ActivTrack returned {expected} filtered bookings, but only {captured} distinct rows were captured "
                f"({missing} missing). Retry so the report does not omit bookings silently."
            )
        if not bookings or not schedules:
            raise BrowserCaptureError("No complete filtered booking and vessel-schedule records were returned by ActivTrack.")
        if incomplete_bookings:
            examples = ", ".join(item.get("reference_number") or "unknown reference" for item in list(incomplete_bookings.values())[:8])
            self.log(
                f"Warning: {len(incomplete_bookings)} booking(s) have no usable vessel schedule and will be listed "
                f"on the Missing Schedules sheet: {examples}."
            )
        if visible_total is not None and visible_total != captured:
            # Colleagues can add bookings while the user's table remains open.
            # The same query with fresh server totals is authoritative; do not
            # refresh/navigate the visible page just to update its stale count.
            self.log(f"Same filter, refreshed server total: {captured} bookings (the open table displays {visible_total}).")
        captured_refs = set(bookings) | set(incomplete_bookings)
        if visible_refs and not visible_refs.issubset(captured_refs):
            self.log(f"{len(visible_refs - captured_refs)} old visible booking(s) no longer match the same live filter; the report uses current server records.")
        # Detect a filter changed while the background capture was running.
        current_pairs = lambda value: sorted((key, val) for key, val in parse_qsl(urlsplit(value).query, keep_blank_values=True)
                                            if key not in {"page", "per_page"})
        if current_pairs(base_url) != current_pairs(self._latest_booking_api_url()):
            raise BrowserCaptureError("The booking filter changed during capture. Retry using the current filter.")
        return (
            [bookings[key] for key in sorted(bookings)],
            [schedules[key] for key in sorted(schedules)],
            [incomplete_bookings[key] for key in sorted(incomplete_bookings)],
            last_page,
            expected,
        )

    def capture(self, refresh: bool = True) -> dict[str, Any]:
        if not self.is_open:
            raise BrowserCaptureError("Open ActivTrack from this application first.")
        if "/sea-import/bookings" not in str(self.driver.current_url):
            raise BrowserCaptureError("Keep the filtered Sea Import Bookings page open in the app's Edge window, then try again.")
        self._bookings.clear()
        self._schedules.clear()
        self._response_urls.clear()
        bookings, schedules, incomplete_bookings, pages, expected = self._capture_filtered_api(refresh=refresh)
        self.log(
            f"Validated all {len(bookings) + len(incomplete_bookings)} filtered booking rows: "
            f"{len(bookings)} linked to {len(schedules)} exact vessel schedules and "
            f"{len(incomplete_bookings)} flagged for schedule correction."
        )

        filter_data = self._filter_metadata(pages, expected)
        # The query is the authoritative filter even when the filter dialog is closed.
        filter_data["api_filters"] = {
            key: value for key, value in parse_qsl(urlsplit(self._captured_booking_url).query, keep_blank_values=True)
            if key not in {"page", "per_page", "sort_by", "sort_direction"} and value
        }
        return {
            "filter": filter_data,
            "totals": {
                "bookings": len(bookings),
                "filtered_bookings": expected or len(bookings) + len(incomplete_bookings),
                "incomplete_bookings": len(incomplete_bookings),
                "schedules": len(schedules),
            },
            "bookings": sorted(bookings, key=lambda item: str(item.get("reference_number", ""))),
            "incomplete_bookings": incomplete_bookings,
            "schedules": schedules,
            "capture": {
                "captured_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "booking_pages": pages,
                "api_booking_rows": len(bookings),
                "api_incomplete_booking_rows": len(incomplete_bookings),
                "matched_schedules": len(schedules),
                "response_count": pages,
                "source": BOOKINGS_URL,
            },
        }

    def capture_all_vessels(self) -> dict[str, Any]:
        """Use the existing page, without navigation or changing its filter/page size."""
        from notification_history import capture_notification_history

        source = self.capture(refresh=False)
        cache_path = Path(os.getenv("LOCALAPPDATA", str(Path.home()))) / "ActivTrackReportGenerator" / "NotificationDetailCache.json"
        source["notification_history"] = capture_notification_history(
            source,
            self._fetch_api_json,
            self.log,
            cache_path=cache_path,
            fetch_many=self._fetch_api_json_many,
        )
        return source
