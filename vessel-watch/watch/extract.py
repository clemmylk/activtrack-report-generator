"""Visible timetable parsers. Vessel movements never imply container movements."""
from __future__ import annotations

import re
from .model import iso_date, key, utc_now


def dated(value):
    day = iso_date(value)
    if not day:
        return None
    time = re.search(r"\b(\d{1,2}:\d{2})\b", value)
    return {"date": day, "time": time.group(1) if time else None,
            "actual": bool(re.search(r"\bactual\b", value, re.I))}


def parse_tables(snapshot, vessel, parser="generic", mapping=None):
    if parser == "one":
        return parse_one(snapshot, vessel)
    if parser == "trico":
        return parse_trico(snapshot)
    if parser == "yangming":
        return parse_yangming(snapshot, vessel)
    if parser == "msc":
        return parse_msc(snapshot, vessel)
    if parser == "cosco":
        return parse_cosco(snapshot, vessel)
    calls = []
    checked = utc_now()
    for table in snapshot["tables"]:
        rows = table["rows"]
        if mapping:
            headers = mapping
            start = int(mapping.get("data_start", 1))
        else:
            headers, start = discover_columns(rows, parser)
        if not headers:
            continue
        for cells in rows[start:]:
            def cell(field):
                idx = headers.get(field)
                return str(cells[idx]).strip() if isinstance(idx, int) and idx < len(cells) else ""
            port = cell("port")
            if not port or key(port) in {"PORT", "LOCATION", "CURRENT STATUS", "NEXT STATUS"}:
                continue
            c = {"vessel": vessel, "port": port, "voyage": cell("voyage"),
                 "arrival_voyage": cell("arrival_voyage") or cell("voyage"),
                 "departure_voyage": cell("departure_voyage") or cell("voyage"),
                 "source_url": snapshot["url"], "checked_at": checked, "raw": " | ".join(cells),
                 "source_kind": "vessel_timetable", "remarks": cell("remarks")}
            for estimate, actual in [("eta", "ata"), ("etd", "atd"), ("etb", "atb")]:
                data = dated(cell(estimate))
                if data:
                    is_actual = data["actual"] or key(cell("status")) == "ACTUAL"
                    field = actual if is_actual else estimate
                    c[field] = data["date"]
                    c[field + "_time"] = data["time"] or cell(estimate + "_time") or None
                actual_data = dated(cell(actual))
                if actual_data:
                    c[actual] = actual_data["date"]
                    c[actual + "_time"] = actual_data["time"]
            if any(c.get(f) for f in ("eta", "etd", "etb", "ata", "atd", "atb")):
                calls.append(c)
    unique = {}
    for call in calls:
        # Preserve conflicts. Only exact duplicates can be collapsed.
        identity = tuple((f, str(v)) for f, v in sorted(call.items()) if f not in {"raw", "checked_at"})
        unique[identity] = call
    return list(unique.values())


def parse_cosco(snapshot, vessel):
    """COSCO vessel result tables. ETA/ATA and ETD/ATD are separate columns."""
    text = snapshot.get('text','')
    if key(vessel) not in key(text) or 'PERIOD OF INQUIRY' not in key(text):
        raise ValueError('COSCO result vessel identity or schedule heading missing.')
    voyages=[]
    for value in re.findall(r'Voyage\s+([A-Z0-9-]+\s*/\s*[A-Z0-9-]+)',text,re.I):
        clean=re.sub(r'\s+','',value).upper()
        if clean not in voyages: voyages.append(clean)
    tables=[t['rows'] for t in snapshot['tables'] if t.get('rows') and t['rows'] != [['No filter data']]]
    if not tables or len(voyages) < len(tables):
        raise ValueError('COSCO voyage/table grouping is incomplete.')
    calls=[]
    for voyage,rows in zip(voyages,tables):
        for row in rows:
            if len(row) != 6 or not row[0].strip():
                raise ValueError('Incomplete COSCO timetable row.')
            call={'vessel':vessel,'voyage':voyage,'port':row[0].strip(),
                  'source_url':snapshot['url'],'checked_at':utc_now(),
                  'source_kind':'vessel_timetable','raw':' | '.join(row),
                  'remarks':'Source combines voyage directions; exact leg matching requires confirmation.'}
            for idx,field in [(1,'eta'),(2,'ata'),(3,'etd'),(4,'atd')]:
                if row[idx].strip():
                    value=dated(row[idx])
                    if not value: raise ValueError('COSCO date unreadable; no date inferred.')
                    call[field],call[field+'_time']=value['date'],value['time']
            if not any(call.get(f) for f in ('eta','ata','etd','atd')):
                raise ValueError('COSCO row has no dated movement.')
            calls.append(call)
    return calls


def parse_msc(snapshot, vessel):
    """MSC's visible vessel result cards explicitly label both dates estimated."""
    text = snapshot.get('text', '')
    header = 'Port or Location\nVessel / Voyage No.\nEstimated Time of Arrival\nEstimated Time of Departure\nService\n'
    if text.count(header) != 1 or 'Search schedule results provided by MSC' not in text:
        raise ValueError('MSC vessel result layout missing or incomplete.')
    body = text.split(header, 1)[1].split('Search schedule results provided by MSC', 1)[0]
    lines = [s.strip() for s in body.splitlines() if s.strip()]
    if not lines or len(lines) % 7:
        raise ValueError('Incomplete MSC port-call cards.')
    calls = []
    for i in range(0,len(lines),7):
        port, code, ship, voyage, arrival, departure, service = lines[i:i+7]
        if key(ship) != key(vessel) or not re.fullmatch(r'\([A-Z]{5}\)',code):
            raise ValueError('MSC vessel identity or port code mismatch.')
        call = {'vessel': vessel, 'voyage': voyage, 'port': port, 'port_code':code[1:-1],
                'service':service, 'source_url':snapshot['url'],'checked_at':utc_now(),
                'source_kind':'vessel_timetable','raw':' | '.join(lines[i:i+7])}
        for field,value in [('eta',arrival),('etd',departure)]:
            value = re.sub(r'(\d{1,2})(st|nd|rd|th)\b',r'\1',value)
            parsed = dated(value)
            if not parsed:
                raise ValueError('MSC date unreadable; no date inferred.')
            call[field], call[field+'_time'] = parsed['date'], parsed['time']
        calls.append(call)
    return calls


def parse_yangming(snapshot, vessel):
    """Read the selected voyage table, not the planned-versus-actual summary."""
    text = snapshot.get('text', '')
    identity = re.search(r'Vessel Name\s*:\s*\n([^\n]+)', text)
    if not identity or key(re.sub(r'\s*\([A-Z0-9]+\)$', '', identity.group(1))) != key(vessel):
        raise ValueError('Yang Ming result vessel does not match the requested vessel.')
    groups = re.findall(r'(?<!Current )Comn Voyage\s*:\s*\n([^\n]+)', text)
    if len(groups) != 1:
        raise ValueError('Selected Yang Ming voyage heading is missing or ambiguous.')
    voyage = groups[0].strip()
    tables = [t['rows'] for t in snapshot['tables'] if t['rows'] and t['rows'][0] ==
              ['Port','Terminal','Arrival','Arrival','Berth','Berth','Departure','Departure']]
    if len(tables) != 2:
        raise ValueError('Yang Ming summary/detail table layout changed.')
    calls = []
    for row in tables[1][2:]:
        if len(row) != 8:
            raise ValueError('Incomplete Yang Ming timetable row.')
        call = {'vessel': vessel, 'voyage': voyage, 'port': row[0], 'terminal': row[1],
                'source_url': snapshot['url'], 'checked_at': utc_now(),
                'source_kind': 'vessel_timetable', 'raw': ' | '.join(row),
                'remarks': 'Source combines voyage directions; exact leg matching requires confirmation.' if '/' in voyage else ''}
        for col, estimate, actual in [(2,'eta','ata'),(4,'etb','atb'),(6,'etd','atd')]:
            value = dated(row[col])
            status = key(row[col+1])
            if not value or status not in {'ACTUAL','ESTIMATED','PLANNED'}:
                raise ValueError('Unrecognised Yang Ming date/status; no actual inferred.')
            field = actual if status == 'ACTUAL' else estimate
            call[field], call[field+'_time'] = value['date'], value['time']
        calls.append(call)
    return calls


def parse_trico(snapshot):
    """All published Colombo ETBs in one page. Never turn a berth estimate into ATA."""
    calls=[]
    for table in snapshot['tables']:
        rows=table['rows']
        if not rows or [key(c) for c in rows[0]] != ['TERMINAL','VESSEL/VOYAGE','OPENING - TIME','CLOSING - TIME','ETB','LAST UPDATED']:
            continue
        for row in rows[1:]:
            if len(row)!=6:
                raise ValueError('Trico row layout changed; capture is incomplete.')
            identity=re.fullmatch(r'(.+?)\s*/\s*([A-Za-z0-9-]+)',row[1])
            etb=dated(row[4])
            if not identity or not etb:
                raise ValueError('Trico vessel/voyage or ETB could not be read exactly.')
            calls.append({'vessel':identity.group(1).strip(),'voyage':identity.group(2),'port':'COLOMBO',
                'terminal':row[0],'etb':etb['date'],'etb_time':etb['time'],'source_updated_at':row[5],
                'checked_at':utc_now(),'source_url':snapshot['url'],'source_kind':'berthing_estimate',
                'raw':' | '.join(row)})
    if not calls:
        raise ValueError('Trico ETB table not found. No arrival status inferred.')
    return calls


def discover_columns(rows, parser):
    for i, row in enumerate(rows[:12]):
        labels = [key(c).replace(".", "") for c in row]
        if parser == "oocl" and "LOCAL TIME" in labels and "VOYAGE" in labels:
            # Verified OOCL desktop table: port, arr time/service/voyage, dep time/service/voyage, remarks.
            if len(labels) >= 7 and labels.count("LOCAL TIME") == 2:
                return {"port": 0, "eta": 1, "arrival_voyage": 3, "etd": 4, "departure_voyage": 6,
                        "remarks": 7}, i + 1
        if parser == "oocl":
            continue
        h = {}
        for j, label in enumerate(labels):
            if label in {"PORT", "PORT NAME", "LOCATION"}: h["port"] = j
            elif label in {"ETA", "ARRIVAL", "ARRIVAL DATE", "ESTIMATED ARRIVAL"}: h["eta"] = j
            elif label in {"ETD", "DEPARTURE", "DEPARTURE DATE", "ESTIMATED DEPARTURE"}: h["etd"] = j
            elif label in {"ETB", "BERTHING"}: h["etb"] = j
            elif label == "ATA": h["ata"] = j
            elif label == "ATD": h["atd"] = j
            elif label in {"VOYAGE", "VOYAGE NO", "VOYAGE NUMBER"}: h["voyage"] = j
            elif label in {"ARRIVAL VOYAGE", "ARR VOYAGE"}: h["arrival_voyage"] = j
            elif label in {"DEPARTURE VOYAGE", "DEP VOYAGE"}: h["departure_voyage"] = j
            elif label == "STATUS": h["status"] = j
            elif label in {"REMARKS", "REMARK"}: h["remarks"] = j
        if parser == "wanhai" and "ARRIVAL VOYAGE" in labels and "ETB" in labels:
            # Wan Hai current/next status table has a blank port header.
            h.update(port=1)
            for f in ("eta", "etd", "etb"):
                if f in h and h[f]+1 < len(labels) and labels[h[f]+1] == "TIME":
                    h[f + "_time"] = h[f]+1
            return h, i+1
        if "port" in h and any(f in h for f in ("eta", "etd", "ata", "atd")):
            return h, i+1
    return {}, 0


def parse_one(snapshot, vessel):
    """ONE's currently visible card layout. A=actual; C=coastal/planned."""
    text = snapshot.get("text", "")
    identity = re.search(r"\nVessel\s*\n\s*([^\n]+)\s*\n\s*Service Lane", text)
    if not identity or key(re.sub(r"\s*\([A-Z0-9]+\)$", "", identity.group(1).strip())) != key(vessel):
        raise ValueError("ONE result vessel heading did not match the requested vessel.")
    total = re.search(r"Total\s+(\d+)\s+Calling Ports", text)
    if not total:
        raise ValueError("ONE calling-port count was not found; page layout may have changed.")
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    calls = []
    for i, label in enumerate(lines):
        if label != "Port" or i+11 >= len(lines) or lines[i+2] != "Voyage" or lines[i+4] != "Terminal":
            continue
        if lines[i+6] != "Arrival Time" or lines[i+9] != "Departure Time":
            raise ValueError("ONE date labels changed; refusing shifted column interpretation.")
        c = {"vessel": vessel, "port": lines[i+1], "voyage": lines[i+3], "terminal": lines[i+5],
             "source_url":snapshot["url"], "checked_at":utc_now(), "source_kind":"vessel_timetable",
             "raw":" | ".join(lines[i:i+12]), "skipped":False}
        for estimate, actual, offset in [("eta","ata",7),("etd","atd",10)]:
            value, flag = lines[i+offset], lines[i+offset+1]
            if value == "SKIP CALLING":
                c["skipped"] = True
                continue
            day = dated(value)
            if not day or flag not in {"A","C"}:
                raise ValueError("ONE date or actual/planned marker was not recognised.")
            field = actual if flag == "A" else estimate
            c[field], c[field+"_time"] = day["date"], day["time"]
        calls.append(c)
    if len(calls) != int(total.group(1)):
        raise ValueError(f"ONE reported {total.group(1)} calling ports; only {len(calls)} were captured.")
    return calls
