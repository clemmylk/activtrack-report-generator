"""Read SENT vessel emails using the existing signed-in browser's GET helper."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode

from report_builder import normalize_source

LOGS_URL = "https://api.activtrack.globactiv.com/api/system/logs/email-logs"
MISSING = {"", "TBA", "N/A", "NONE", "NULL", "TBC"}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("value") or value.get("display_name") or value.get("name") or ""
    return str(value or "").strip()


def _has_token(text: str, token: str) -> bool:
    return bool(token and re.search(r"(?<![A-Z0-9])" + re.escape(token.upper()) + r"(?![A-Z0-9])", text.upper()))


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _sent_vessel_log(log: dict) -> bool:
    return (_text(log.get("status")).lower() in {"sent", "delivered", "success", "successful"}
            and "vessel" in _text(log.get("email_type")).lower()
            and _text(log.get("module")).lower().replace("_", " ") in {"sea import", "sea-import"})


def _timestamp_key(value: Any) -> float:
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return stamp.replace(tzinfo=stamp.tzinfo or timezone.utc).timestamp()
    except (ValueError, TypeError):
        return 0


def _template_tokens(template: Any) -> set[str]:
    if isinstance(template, str):
        try:
            template = json.loads(template)
        except ValueError:
            template = {}
    return {_text(value).upper() for node in _walk(template) for key, value in node.items()
            if key in {"hblNumber", "hbl_number", "refNumber", "ref_number", "so_number", "booking_reference"}}


def capture_notification_history(source: dict, fetch: Callable, log: Callable = lambda _: None,
                                 cache_path: Path | None = None,
                                 fetch_many: Callable | None = None) -> dict:
    """One newest-first search per final vessel, stop as soon as every schedule matches.

    Match exact booking identifiers, never vessel name alone. Detail requests are
    used only when the subject lacks a usable identifier (manual placeholders).
    Recipient addresses and email bodies are never copied into the report source.
    """
    normalized = normalize_source(source)
    schedules = {s["reference_number"]: s for s in normalized["schedules"]}
    schedules_by_id = {str(schedule["id"]): schedule for schedule in schedules.values()}
    raw_by_ref = {str(b.get("reference_number")): b for b in source["bookings"]}
    identifiers: dict[str, set[str]] = {ref: set() for ref in schedules}
    for booking in normalized["bookings"]:
        schedule = schedules_by_id[str(booking["vessel_schedule_id"])]
        raw = raw_by_ref.get(booking["reference_number"], {})
        for key in ("reference_number", "hbl_number", "so_number", "ref_number", "gensoft_reference_number"):
            token = _text(raw.get(key))
            if token.upper() not in MISSING:
                identifiers[schedule["reference_number"]].add(token)
    groups: dict[str, list[str]] = {}
    for ref, schedule in schedules.items():
        final_vessel = _text(schedule["legs"][-1]["vessel_name"])
        if final_vessel.upper() not in MISSING:
            groups.setdefault(final_vessel, []).append(ref)

    result = {ref: {"status": "No matching sent vessel notice found"} for ref in schedules}
    requests = 0
    detail_cache = {}
    if cache_path and cache_path.exists():
        try:
            detail_cache = json.loads(cache_path.read_text(encoding="utf-8"))
            if not isinstance(detail_cache, dict):
                detail_cache = {}
        except (OSError, ValueError):
            detail_cache = {}
    group_items = sorted(groups.items())
    states = [{"index": index, "vessel": vessel, "refs": refs, "pending": set(refs),
               "page": 1, "last_page": 1, "logged": False}
              for index, (vessel, refs) in enumerate(group_items, 1)]

    def process_payload(state: dict, payload: dict) -> None:
        nonlocal requests
        records = payload.get("data")
        if not isinstance(records, list):
            raise ValueError("Email history did not return a record list")
        pagination = payload.get("pagination") or {}
        state["last_page"] = max(1, int(pagination.get("last_page") or 1))
        for entry in records:
            if not isinstance(entry, dict) or not _sent_vessel_log(entry):
                continue
            subject = _text(entry.get("subject"))
            candidates = {
                ref for ref in state["pending"]
                if any(_has_token(subject, leg["vessel_name"]) and _has_token(subject, leg["voyage"])
                       for leg in schedules[ref]["legs"])
            }
            if not candidates:
                continue
            matched = {ref for ref in candidates if any(_has_token(subject, token) for token in identifiers[ref])}
            detail = entry
            if not matched and entry.get("id"):
                log_id = str(entry["id"])
                cached = detail_cache.get(log_id)
                if not isinstance(cached, dict) or "tokens" not in cached:
                    if "template_data" not in entry:
                        details_payload = fetch(LOGS_URL + "/" + log_id)
                        requests += 1
                        detail = details_payload.get("data") or {}
                    cached = {"tokens": sorted(_template_tokens(detail.get("template_data") or {})),
                              "sent_at": detail.get("sent_at"), "created_at": detail.get("created_at")}
                    detail_cache[log_id] = cached
                detail = cached
                tokens = set(cached["tokens"])
                matched = {ref for ref in candidates if tokens.intersection(t.upper() for t in identifiers[ref])}
            sent_at = detail.get("sent_at") or entry.get("sent_at")
            stamp = sent_at or detail.get("created_at") or entry.get("created_at")
            if not stamp:
                continue
            for ref in matched:
                previous = result[ref].get("timestamp")
                if not previous or _timestamp_key(stamp) > _timestamp_key(previous):
                    result[ref] = {"status": "SENT", "timestamp": stamp,
                                   "time_basis": "Sent at" if sent_at else "ActiveTrack Trigger At",
                                   "type": _text(entry.get("event")), "log_id": entry.get("id")}
                state["pending"].discard(ref)

    while True:
        active = [state for state in states
                  if state["pending"] and state["page"] <= state["last_page"]]
        if not active:
            break
        urls = [LOGS_URL + "?" + urlencode({"query": state["vessel"], "per_page": 250,
                "page": state["page"], "sort_by": "created_at", "sort_direction": "desc"})
                for state in active]
        payloads: list[Any]
        if fetch_many and len(urls) > 1:
            try:
                payloads = fetch_many(urls)
                if len(payloads) != len(urls):
                    raise ValueError("Notification batch returned an incomplete result")
                requests += len(urls)
            except Exception as exc:
                log(f"Fast notification batch unavailable; continuing normally: {str(exc)[:120]}")
                payloads = []
                for url in urls:
                    try:
                        payloads.append(fetch(url))
                    except Exception as item_exc:
                        payloads.append(item_exc)
                    requests += 1
        else:
            try:
                payloads = [fetch(urls[0])]
            except Exception as exc:
                payloads = [exc]
            requests += 1

        for state, payload in zip(active, payloads):
            try:
                if isinstance(payload, Exception):
                    raise payload
                process_payload(state, payload)
                state["page"] += 1
            except Exception as exc:
                for ref in state["pending"]:
                    result[ref] = {"status": "History unavailable", "error": str(exc)[:180]}
                state["pending"].clear()
                log(f"Notification history unavailable for {state['vessel']}: {exc}")

            finished = not state["pending"] or state["page"] > state["last_page"]
            if finished and not state["logged"]:
                refs = state["refs"]
                log(f"Notification history {state['index']}/{len(groups)}: {state['vessel']} "
                    f"({len(refs) - len(state['pending'])}/{len(refs)} schedules matched).")
                state["logged"] = True
    if cache_path:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = cache_path.with_suffix(".tmp")
            temp_path.write_text(json.dumps(detail_cache), encoding="utf-8")
            temp_path.replace(cache_path)
        except OSError:
            pass  # Cache failures must not hide the verified report.
    return {"schedules": result, "requests": requests,
            "source": LOGS_URL, "scope": "SENT vessel-schedule emails matched to exact filtered booking identifiers"}
