from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def name(value):
    if isinstance(value, dict):
        value = value.get("name") or value.get("label") or ""
    return str(value or "").strip()


def key(value):
    return re.sub(r"\s+", " ", name(value)).upper().strip()


def missing(value):
    return key(value) in {"", "TBA", "TBC", "N/A", "NA", "NONE", "NULL", "PENDING", "-"}


def iso_date(value):
    """Require a year; do not infer a year or ambiguous US numeric dates."""
    text = name(value)
    patterns = [
        (r"\d{4}-\d{2}-\d{2}", "%Y-%m-%d"),
        (r"\d{4}/\d{2}/\d{2}", "%Y/%m/%d"),
        (r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}", "%d %b %Y"),
        (r"\d{1,2}-[A-Za-z]{3}-\d{4}", "%d-%b-%Y"),
    ]
    for pattern, fmt in patterns:
        match = re.search(pattern, text)
        if match:
            try:
                return datetime.strptime(match.group(), fmt).date().isoformat()
            except ValueError:
                return None
    return None


def port_key(value, aliases=None):
    value = key(value)
    base = {
        "COLOMBO, SRI LANKA": "COLOMBO", "LKCMB": "COLOMBO",
        "HONG KONG, HONG KONG": "HONG KONG", "HKHKG": "HONG KONG",
        "SHEKOU, CHINA": "SHEKOU", "CNSHK": "SHEKOU",
        "SINGAPORE, SINGAPORE": "SINGAPORE", "SGSIN": "SINGAPORE",
        "SHANGHAI, CHINA": "SHANGHAI", "CNSHA": "SHANGHAI",
        "XIAMEN, CHINA": "XIAMEN", "CNXMN": "XIAMEN",
        "NINGBO, CHINA": "NINGBO", "CNNGB": "NINGBO",
        "DALIAN, CHINA": "DALIAN", "BUSAN, KOREA, REPUBLIC OF": "BUSAN",
        "KAOHSIUNG, TAIWAN": "KAOHSIUNG", "KAOHSIUNG (TAIWAN)": "KAOHSIUNG",
        "TANJUNG PELEPAS, MALAYSIA": "TANJUNG PELEPAS",
        "CAI MEP, VIETNAM": "CAI MEP",
        "BUSAN": "PUSAN", "BUSAN, KOREA, REPUBLIC OF": "PUSAN",
    }
    # Shenzhen is NOT automatically mapped to Shekou, nor Hong Kong to its terminals.
    base.update({key(k): key(v) for k, v in (aliases or {}).items()})
    return base.get(value, value)


def latest_source(folder):
    files = list(Path(folder).glob("*_Source.json"))
    if not files:
        raise ValueError("No captured *_Source.json report found. Save captured JSON in the report generator.")
    return max(files, key=lambda p: (p.stat().st_mtime_ns, p.name))


def load_baseline(path):
    raw = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    schedules = raw.get("schedules")
    bookings = raw.get("bookings")
    if not isinstance(schedules, list) or not isinstance(bookings, list):
        raise ValueError("Source must contain bookings and schedules arrays.")
    by_id = {}
    for schedule in schedules:
        sid = str(schedule.get("id", ""))
        if not sid or sid in by_id:
            raise ValueError("Missing or duplicate schedule ID in source.")
        by_id[sid] = schedule
    linked = defaultdict(list)
    seen = set()
    for b in bookings:
        ref = b.get("reference_number")
        sid = str(b.get("vessel_schedule_id") or (b.get("vessel_schedule") or {}).get("id") or "")
        if not ref or ref in seen or sid not in by_id:
            raise ValueError(f"Invalid, duplicate, or unlinked booking: {ref}")
        seen.add(ref)
        linked[sid].append(b)
    expected = (raw.get("totals") or {}).get("bookings")
    if expected is not None and int(expected) != len(bookings):
        raise ValueError("Source booking total disagrees with the actual records.")
    legs = []
    missing_details = []
    for b in bookings:
        gaps = [label for label, field in [("HBL", "hbl_number"), ("MBL", "mbl_number"),
                ("Container", "container_number"), ("SO", "so_number")] if missing(b.get(field))]
        if gaps:
            missing_details.append({"booking": b["reference_number"], "vs": b.get("vessel_schedule_reference_number", ""),
                "hbl": name(b.get("hbl_number")), "cs": name(b.get("cs_executive_name") or b.get("cs_executive")), "missing": gaps})
    for sid, s in by_id.items():
        raw_legs = s.get("transshipments") or s.get("legs")
        if not raw_legs:
            raise ValueError(f"{s.get('reference_number', sid)} has no explicit vessel legs.")
        refs = linked[sid]
        mas_refs, unknown = [], []
        for b in refs:
            organisations = [name(b.get(f)) for f in ("customer_name", "customer", "consignee", "billing_party", "shipper_name")]
            if any(re.search(r"\bMAS\b", org, re.I) for org in organisations):
                mas_refs.append(b["reference_number"])
            elif not name(b.get("customer_name") or b.get("customer")):
                unknown.append(b["reference_number"])
        for i, leg in enumerate(raw_legs, 1):
            legs.append({"vs_id": sid, "vs": s.get("reference_number", "VS-" + sid), "leg": i,
                "leg_id": leg.get("id"), "total_legs": len(raw_legs), "final_leg": i == len(raw_legs),
                "vessel": name(leg.get("vessel_name") or leg.get("vessel")), "voyage": name(leg.get("voyage")),
                "carrier": name(s.get("carrier_name") or s.get("carrier")),
                "pol": name(leg.get("departure_port")), "pod": name(leg.get("arrival_port")),
                # Never copy an overall first-leg actual into an intermediate/final leg.
                **{f: iso_date(leg.get(f)) for f in ("eta", "etd", "ata", "atd")},
                "bookings": [b["reference_number"] for b in refs], "booking_count": len(refs),
                "mas_bookings": mas_refs, "mas_unknown": unknown,
                "containers": sorted({name(b.get("container_number")) for b in refs if not missing(b.get("container_number"))}),
                "mbls": sorted({name(b.get("mbl_number")) for b in refs if not missing(b.get("mbl_number"))}),
                "scope": "Captured filter only; total schedule booking count not established"})
    return {"source_path": str(Path(path).resolve()), "captured_at": (raw.get("capture") or {}).get("captured_at", "unknown"),
            "filter": raw.get("filter", {}), "bookings": len(bookings), "schedules": len(schedules),
            "legs": legs, "missing_details": missing_details, "loaded_at": utc_now()}


def compare(leg, calls, aliases=None, voyage_aliases=None, manual_over_days=3):
    result = {**leg, "changes": [], "coverage": {}, "vessel_actuals": [], "exceptions": [], "notification": "NONE"}
    aliases = aliases or {}
    voyage_aliases = {key(k): key(v) for k, v in (voyage_aliases or {}).items()}
    wanted_voyage = voyage_aliases.get(key(leg["voyage"]), key(leg["voyage"]))
    for field, port_field, voyage_field, actual_field in [("etd", "pol", "departure_voyage", "atd"), ("eta", "pod", "arrival_voyage", "ata")]:
        candidates = [c for c in calls if key(c.get("vessel")) == key(leg["vessel"])
                      and port_key(c.get("port"), aliases) == port_key(leg[port_field], aliases)
                      and voyage_aliases.get(key(c.get(voyage_field) or c.get("voyage")), key(c.get(voyage_field) or c.get("voyage"))) == wanted_voyage]
        # Multiple port calls for a voyage are ambiguous even if one is closer to the old ETA.
        unique = {json.dumps({f: c.get(f) for f in (field, actual_field, "etb", "skipped")}, sort_keys=True): c for c in candidates}
        if len(unique) != 1:
            combined = any(key(c.get('vessel')) == key(leg['vessel'])
                           and port_key(c.get('port'), aliases) == port_key(leg[port_field], aliases)
                           and '/' in c.get('voyage','')
                           and wanted_voyage in [key(v) for v in c['voyage'].split('/')]
                           for c in calls)
            result["coverage"][field] = ("COMBINED VOYAGE DIRECTIONS — CONFIRM EXACT LEG" if combined and not unique
                                        else "NO EXACT PORT/VOYAGE MATCH" if not unique else "AMBIGUOUS REPEATED PORT CALL")
            continue
        c = next(iter(unique.values()))
        if c.get("skipped"):
            result["coverage"][field] = "PORT CALL SKIPPED — VERIFY CONNECTION"
            result["exceptions"].append({"port":leg[port_field],"issue":"Carrier shows SKIP CALLING", "source_url":c.get("source_url")})
            result["notification"] = "MANUAL REVIEW REQUIRED — PORT CALL SKIPPED"
            continue
        observed_actual = c.get(actual_field)
        if observed_actual:
            result["vessel_actuals"].append({"field": actual_field, "date": observed_actual, "source": c.get("source_url"),
                 "note": "Vessel event only; shipment loading/arrival proof still required"})
        new = c.get(field)
        if not new:
            result["coverage"][field] = "VESSEL ACTUAL OBSERVED" if observed_actual else "DATE NOT PUBLISHED"
            continue
        result["coverage"][field] = "MATCHED"
        old = leg.get(field)
        if new != old:
            days = (date.fromisoformat(new) - date.fromisoformat(old)).days if old else None
            result["changes"].append({"field": field.upper(), "old": old, "new": new, "days": days,
                "source_url": c.get("source_url"), "checked_at": c.get("checked_at"), "raw": c.get("raw", "")})
    if result["changes"] and not result["exceptions"]:
        manual = bool(leg["mas_bookings"]) or any(c["days"] is None or abs(c["days"]) > manual_over_days for c in result["changes"])
        result["notification"] = "MANUAL NOTICE REQUIRED" if manual else "AUTOMATIC NOTICE CANDIDATE — VERIFY FULL BOOKING/MAS SCOPE"
    return result


def alignment_issues(legs, aliases=None):
    groups = defaultdict(list)
    for leg in legs:
        if leg.get("eta"):
            groups[(key(leg["vessel"]), key(leg["voyage"]), port_key(leg["pod"], aliases))].append(leg)
    return [{"vessel": vessel, "voyage": voyage, "pod": pod,
             "rows": [{"vs": x["vs"], "leg": x["leg"], "eta": x["eta"]} for x in group]}
            for (vessel, voyage, pod), group in groups.items() if len({x["eta"] for x in group}) > 1]
