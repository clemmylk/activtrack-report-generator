from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path
from .browser import Browser, NeedsAttention, AccessRequired, VesselUnavailable
from .checkpoint import fingerprint, load_checkpoint, save_checkpoint, query_key
from .extract import parse_tables
from .model import load_baseline, latest_source, key, missing, utc_now, compare, alignment_issues, port_key
from .report import History, write_report
from .exports import export_excel


def choose_site(leg, config):
    sites = config["sites"]
    override = config.get("vessel_overrides", {}).get(key(leg["vessel"]))
    if override:
        return next((s for s in sites if s["id"] == override), None)
    for field, patterns in [("vessel", "vessel_patterns"), ("carrier", "carrier_patterns")]:
        candidates = [s for s in sites if any(re.search(p, leg[field], re.I) for p in s.get(patterns, []))]
        if len(candidates) == 1:
            return candidates[0]
        if candidates:
            return None
    return None


def collect(browser, site, vessel, audit_folder):
    browser.site_tab(site["id"])
    browser.navigate(site["url"])
    try:
        browser.run_steps(site.get("steps", []), {"vessel": vessel})
    finally:
        browser.remember_site_tab(site["id"])
    snapshots, calls, hashes = [], [], set()
    for page in range(int(site.get("max_pages", 1))):
        browser.guard()
        snapshot = browser.snapshot()
        if site.get("parser") == "trico" and site.get("role") == "arrival_crosscheck":
            pass  # The strict Trico parser reads vessel + voyage identity from every source row.
        elif site.get("identity_mode") == "selected_query":
            if not any(s["op"] in {"select_vessel", "select_text", "fill"} and s.get("value") == "{vessel}" for s in site.get("steps", [])):
                raise ValueError("Selected-query recipe must explicitly select the requested vessel.")
        else:
            selector = site.get("identity_selector")
            if not selector:
                raise NeedsAttention("Site needs a vessel identity selector or a verified vessel search recipe.")
            identity = browser.element({"selector": selector}).text
            if key(identity) != key(vessel):
                raise NeedsAttention("Result vessel identity does not match the requested vessel.")
        extractable = snapshot.get("text", "") if site.get("parser") in {"one","msc","yangming","cosco"} else snapshot["tables"]
        digest = hashlib.sha256(json.dumps(extractable, sort_keys=True).encode()).hexdigest()
        if digest in hashes:
            raise NeedsAttention("Pagination repeated the same result; capture incomplete.")
        hashes.add(digest)
        # Save timetable evidence only, not form fields, cookies or request headers.
        evidence = {"url": snapshot["url"], "vessel": vessel, "checked_at": utc_now(), "tables": snapshot["tables"], "sha256": digest}
        if site.get("parser") in {"one","msc","yangming","cosco"}:
            evidence["text"] = snapshot["text"]
        snapshots.append(evidence)
        calls.extend(parse_tables(snapshot, vessel, site.get("parser", "generic"), site.get("columns")))
        next_spec = site.get("next")
        if not next_spec:
            break
        buttons = browser.driver.find_elements(next_spec.get("by", "css selector"), next_spec["selector"])
        enabled = [e for e in buttons if e.is_displayed() and e.is_enabled() and e.get_attribute("aria-disabled") != "true" and "disabled" not in (e.get_attribute("class") or "")]
        if not enabled:
            break
        if len(enabled) != 1 or page+1 >= int(site.get("max_pages", 1)):
            raise NeedsAttention("Pagination is ambiguous or exceeded the configured maximum.")
        enabled[0].click()
        browser.wait(2)
    if not calls:
        raise NeedsAttention("No dated timetable rows read. Search or table layout needs one-time setup.")
    fname = re.sub(r"[^A-Za-z0-9_-]", "_", site["id"] + "_" + vessel)
    (audit_folder / (fname + ".json")).write_text(json.dumps(snapshots, indent=2), encoding="utf-8")
    return [{**c, "site": site["id"]} for c in calls]


def collect_with_retry(browser, site, vessel, audit, log, attempts=2):
    from selenium.common.exceptions import TimeoutException, WebDriverException, UnexpectedAlertPresentException, NoAlertPresentException
    for attempt in range(attempts):
        try:
            return collect(browser, site, vessel, audit)
        except (TimeoutException, WebDriverException) as exc:
            if isinstance(exc,UnexpectedAlertPresentException):
                message = getattr(exc,'alert_text','') or ''
                if 'server is busy' not in message.lower():
                    raise AccessRequired('Website dialog needs your review in the tracker browser.') from exc
                # Only dismiss this known read-only server-error notice, never arbitrary confirmations.
                try: browser.driver.switch_to.alert.dismiss()
                except NoAlertPresentException: pass
                if attempt+1 >= attempts:
                    raise NeedsAttention('Website reports its server is busy. Saved for later; no tracking data inferred.') from exc
                log(f'{vessel}: website server is busy; retrying once in 2 seconds.')
                browser.wait(2)
                continue
            # Invalid selectors, destroyed sessions, etc. are not transient network failures.
            transient = isinstance(exc, TimeoutException) or any(x in str(exc) for x in ["ERR_CONNECTION", "ERR_TIMED_OUT", "ERR_NAME_NOT_RESOLVED"])
            if not transient or attempt + 1 >= attempts:
                raise
            log(f"{vessel}: slow/unavailable response; retrying once in 2 seconds.")
            browser.wait(2)


def run(config, reports_folder, data_dir, stop=None, log=None, headless=True, only_site=None, limit=None, browser=None, resume=False, family="edge"):
    stop = stop or threading.Event()
    log = log or (lambda _text: None)
    data_dir = Path(data_dir)
    baseline = load_baseline(latest_source(reports_folder))
    # A supplementary long-term JSON uses the exact same explicit-leg schema (bookings may be empty).
    extra_path = config.get("long_term_source")
    if extra_path:
        extra = load_baseline(extra_path)
        known = {(r["vs_id"], r["leg"]) for r in baseline["legs"]}
        baseline["legs"].extend(r for r in extra["legs"] if (r["vs_id"], r["leg"]) not in known)
    checkpoint_path = data_dir / "resume.json"
    digest = fingerprint(baseline["legs"], config)
    state = load_checkpoint(checkpoint_path, digest) if resume else {
        "version": 1, "fingerprint": digest, "only_site": only_site, "completed": {}}
    if resume:
        only_site = state.get("only_site")
        log(f"Resuming saved queue; {len(state['completed'])} fresh vessel results will be reused with their original check times.")
    run_id = utc_now().replace(":", "").replace("+", "_") + "_" + uuid.uuid4().hex[:8]
    folder = data_dir / "reports" / run_id
    audit = folder / "evidence"
    audit.mkdir(parents=True)
    results, observations, checks, assignments = [], [], [], {}
    queue = {}
    for leg in baseline["legs"]:
        site = choose_site(leg, config)
        sid = site["id"] if site else "UNCONFIGURED"
        assignments[(leg["vs_id"], leg["leg"])] = sid
        queue[(sid, key(leg["vessel"]))] = (site, leg["vessel"])
    for site in config['sites']:
        if site.get('role') == 'arrival_crosscheck':
            queue[(site['id'],key('All published Colombo calls'))]=(site,'All published Colombo calls')
    owned_browser = browser is None
    blocked_sites, done = set(), 0
    status_by_key = {}
    try:
        site_order = {sid:i for i,sid in enumerate(config.get('site_order',[s['id'] for s in config['sites']]))}
        for (sid, vkey), (site, vessel) in sorted(queue.items(), key=lambda item:(site_order.get(item[0][0],999), item[0][1])):
            record = {"site": sid, "vessel": vessel, "status": "NEEDS SETUP", "detail": "No verified site recipe assigned"}
            token = query_key(sid, vkey)
            cached = state["completed"].get(token)
            if stop.is_set():
                record.update(status="NOT CHECKED", detail="Stopped by user")
            elif only_site and sid != only_site:
                record.update(status="NOT CHECKED", detail="Outside selected test site")
            elif cached:
                observations.extend(cached["calls"])
                record.update(status="REUSED SAVED RESULT", checked_at=cached["checked_at"],
                              detail=f"Checked {cached['checked_at']}; reused on explicit resume only. Evidence: {cached['evidence_folder']}")
            elif limit is not None and done >= limit:
                record.update(status="NOT CHECKED", detail="Test vessel limit reached")
            elif site and not site.get("enabled"):
                record["detail"] = site.get("validation", "Needs one-time site setup")
            elif site and not site.get('validated',False) and not only_site:
                record['detail'] = 'Not yet live-validated. Use Test selected site after its normal access and search setup.'
            elif sid in blocked_sites:
                record.update(status="NEEDS ATTENTION", detail="Earlier check on this site failed; avoid repeated requests")
            elif missing(vessel):
                record["detail"] = "Vessel name not entered in ActiveTrack"
            elif site:
                done += 1
                log(f"Checking {site['name']}: {vessel}")
                try:
                    if browser is None:
                        browser = Browser(data_dir / "BrowserProfiles" / family, headless=headless, stop=stop, family=family)
                    calls = collect_with_retry(browser, site, vessel, audit, log)
                    checked_at = utc_now()
                    calls = [{**c, "checked_at": checked_at} for c in calls]
                    observations.extend(calls)
                    record.update(status="TABLE READ", checked_at=checked_at, detail=f"{len(calls)} port calls read; exact voyage/port matching follows")
                    state["completed"][token] = {"checked_at": checked_at, "calls": calls, "evidence_folder": str(audit.resolve())}
                except InterruptedError:
                    stop.set()
                    record.update(status="NOT CHECKED", detail="Stopped by user")
                except VesselUnavailable as exc:
                    record.update(status="VESSEL NOT FOUND", detail=str(exc))
                except AccessRequired as exc:
                    record.update(status="NEEDS YOUR VERIFICATION", detail=str(exc))
                    blocked_sites.add(sid)
                except Exception as exc:
                    record.update(status="NEEDS ATTENTION", detail=str(exc).split("Stacktrace:")[0][:550])
                    blocked_sites.add(sid)
                log(f"{vessel}: {record['status']}")
            checks.append(record)
            status_by_key[(sid, vkey)] = record
            state.update(updated_at=utc_now(), last_checks=checks, report_folder=str(folder.resolve()))
            save_checkpoint(checkpoint_path, state)
        for leg in baseline["legs"]:
            sid = assignments[(leg["vs_id"], leg["leg"])]
            calls = [c for c in observations if c["site"] == sid and key(c["vessel"]) == key(leg["vessel"])]
            row = compare(leg, calls, config.get("port_aliases"), config.get("voyage_aliases"), config.get("manual_notice_over_days", 3))
            row["check_status"] = status_by_key[(sid, key(leg["vessel"]))]["status"]
            row["berthing_checks"] = [c for c in observations if c.get('source_kind')=='berthing_estimate'
                and port_key(leg['pod'],config.get('port_aliases'))=='COLOMBO'
                and key(c['vessel'])==key(leg['vessel']) and key(c.get('voyage'))==key(leg['voyage'])]
            results.append(row)
        complete = all(all(v in {"MATCHED", "VESSEL ACTUAL OBSERVED"} for v in r["coverage"].values()) for r in results)
        report = {"run_id": run_id, "created_at": utc_now(), "status": "COMPLETE" if complete else "PARTIAL — SEE CHECK LOG",
            "baseline": {k:v for k,v in baseline.items() if k != "legs"}, "results": results, "checks": checks,
            "observations": observations, "alignment_issues": alignment_issues(baseline["legs"], config.get("port_aliases"))}
        # Release browser RAM before starting the spreadsheet runtime. Keep only
        # user-verification sessions available in the desktop app.
        if browser is not None and (owned_browser or not any(c['status'] == 'NEEDS YOUR VERIFICATION' for c in checks)):
            browser.close()
        path = write_report(report, folder)
        try:
            export_excel(folder / "report.json", folder / "report.xlsx")
            log("Excel and JSON exports saved alongside the report.")
        except Exception as exc:
            report["export_error"] = str(exc)
            path = write_report(report, folder)
            log("Excel needs attention; JSON is saved: " + str(exc))
        history = History(data_dir / "history.sqlite3")
        try:
            history.save(report, path)
        finally:
            history.close()
        (data_dir / "latest_report.txt").write_text(str(path.resolve()), encoding="utf-8")
        log(f"Report saved: {path}")
        return path, report
    finally:
        if owned_browser and browser is not None:
            browser.close()
