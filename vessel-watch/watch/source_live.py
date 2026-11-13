"""ActiveTrack capture via the website's authenticated read requests.

The allowed URL and fetch behaviour are derived from the user's v1.0.2 app.
Only its booking-list GET is allowed. There is no mutation/notification method.
"""
import json
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .model import utc_now

BOOKINGS_URL = "https://activtrack.globactiv.com/sea-import/bookings"


def validate_url(url):
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != "api.activtrack.globactiv.com" or p.path.rstrip("/") != "/api/sea-import/bookings":
        raise ValueError("Only the observed ActiveTrack booking-list HTTPS URL is allowed.")
    if any(any(x in k.lower() for x in ("token", "password", "secret", "authorization")) for k, _ in parse_qsl(p.query)):
        raise ValueError("Do not save credential-bearing URLs.")
    return p


def discover_url(browser):
    url = browser.driver.execute_script("""
return performance.getEntriesByType('resource').map(e=>e.name)
 .filter(u=>u.startsWith('https://api.activtrack.globactiv.com/api/sea-import/bookings?')).pop() || '';
""")
    validate_url(url)
    return url


def capture(browser, url):
    parts = validate_url(url)
    if urlsplit(browser.driver.current_url).hostname != "activtrack.globactiv.com":
        browser.navigate(BOOKINGS_URL)
    bookings, schedules = {}, {}
    expected = last_page = None
    page = 1
    while page <= (last_page or 1):
        browser.check()
        pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in {"page", "per_page"}]
        target = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(pairs + [("page", str(page)), ("per_page", "250")]), ""))
        result = browser.driver.execute_async_script("""
const done=arguments[arguments.length-1], controller=new AbortController();
const timer=setTimeout(()=>controller.abort(),30000);
fetch(arguments[0], {method:'GET',credentials:'include',headers:{'Accept':'application/json'},signal:controller.signal})
 .then(async r=>done({status:r.status,body:await r.text()}))
 .catch(e=>done({status:0,error:String(e)})).finally(()=>clearTimeout(timer));
""", target)
        if result.get("status") != 200:
            raise ValueError(f"ActiveTrack read failed: HTTP {result.get('status')}. Sign in using the tracker browser.")
        payload = json.loads(result["body"])
        pagination = payload.get("pagination", {})
        if not all(k in pagination for k in ("total", "last_page")):
            raise ValueError("Pagination schema changed; capture stopped.")
        count, pages = int(pagination["total"]), max(1, int(pagination["last_page"]))
        if expected is not None and (count != expected or pages != last_page):
            raise ValueError("Filtered total changed during capture. Retry for one consistent snapshot.")
        if pages > 500:
            raise ValueError("Unexpected pagination count; capture stopped.")
        expected, last_page = count, pages
        records = payload.get("data")
        if not isinstance(records, list):
            raise ValueError("Booking response shape changed.")
        for b in records:
            ref, s = b.get("reference_number"), b.get("vessel_schedule")
            if not ref or not ref.startswith("B-") or not isinstance(s, dict) or not s.get("id"):
                raise ValueError("A booking has no exact linked vessel schedule.")
            if ref in bookings:
                raise ValueError("Repeated booking across pages; capture stopped.")
            sid = str(s["id"])
            if not (s.get("transshipments") or s.get("legs")):
                raise ValueError(f"Missing legs for {s.get('reference_number', sid)}")
            if sid in schedules and schedules[sid] != s:
                raise ValueError("Schedule changed during capture. Retry.")
            b["vessel_schedule_id"] = s["id"]
            b["vessel_schedule_reference_number"] = s.get("reference_number")
            bookings[ref], schedules[sid] = b, s
        page += 1
    if len(bookings) != expected:
        raise ValueError("Booking count validation failed.")
    return {"bookings": list(bookings.values()), "schedules": list(schedules.values()),
            "totals": {"bookings": len(bookings), "schedules": len(schedules)},
            "filter": {k:v for k,v in parse_qsl(parts.query) if k not in {"page", "per_page"}},
            "capture": {"captured_at": utc_now(), "source": BOOKINGS_URL, "booking_pages": last_page}}

