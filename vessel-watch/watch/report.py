from __future__ import annotations
import html
import json
import sqlite3
from pathlib import Path


def write_report(report, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    esc = lambda s: html.escape(str(s if s is not None else "—"))
    rows = []
    for r in report["results"]:
        changes = "<br>".join(f"{esc(c['field'])}: {esc(c['old'])} → <b>{esc(c['new'])}</b> ({esc(c['days'])} days)" for c in r["changes"])
        source_links = {c.get("source_url", "") for c in r["changes"] if c.get("source_url")}
        links = " ".join(f'<a href="{esc(url)}" target="_blank" rel="noreferrer">Source</a>' for url in source_links)
        cov = "<br>".join(f"{esc(k.upper())}: {esc(v)}" for k,v in r["coverage"].items())
        actual = "<br>".join(f"Vessel {esc(x['field'].upper())}: {esc(x['date'])} — shipment proof needed" for x in r.get("vessel_actuals", []))
        berthing = "<br>".join(f"Trico ETB: {esc(c['etb'])} {esc(c.get('etb_time',''))} ({esc(c.get('terminal',''))}) — estimate only" for c in r.get('berthing_checks',[]))
        flag = "🔴 " if r["mas_bookings"] else ""
        cls = "manual" if r["notification"].startswith("MANUAL") else ""
        coverage_cls = "ok" if all(v == "MATCHED" for v in r["coverage"].values()) else "attention"
        rows.append(f"<tr><td>{flag}{esc(r['vs'])}<br>Leg {r['leg']}/{r['total_legs']}</td>"
            f"<td>{esc(r['vessel'])}<br>{esc(r['voyage'])}<br><small>{esc(r['carrier'])}</small></td>"
            f"<td>{esc(r['pol'])} → {esc(r['pod'])}</td><td>{r['booking_count']} captured<br>{esc(', '.join(r['bookings']))}</td>"
            f"<td class='fresh'>{changes or 'No comparable date change'}<br>{links}</td>"
            f"<td class='{coverage_cls}'>{cov}<br>{actual}<br>{berthing}</td><td class='{cls}'>{esc(r['notification'])}</td></tr>")
    failures = "".join(f"<tr><td>{esc(s['site'])}</td><td>{esc(s['vessel'])}</td><td>{esc(s['status'])}</td><td>{esc(s.get('detail',''))}</td></tr>" for s in report["checks"])
    missing = "".join(f"<tr><td>{esc(r['booking'])}</td><td>{esc(r['vs'])}</td><td><b><i>{esc(r['hbl'])}</i></b></td><td>{esc(r['cs'])}</td><td>{esc(', '.join(r['missing']))}</td></tr>" for r in report["baseline"]["missing_details"])
    align = "".join(f"<li>{esc(r['vessel'])} / {esc(r['voyage'])} → {esc(r['pod'])}: {esc('; '.join(x['vs']+' '+x['eta'] for x in r['rows']))}</li>" for r in report["alignment_issues"])
    baseline = report["baseline"]
    text = f"""<!doctype html><html><head><meta charset="utf-8"><title>Vessel Watch report</title>
<style>body{{font:15px Segoe UI,Arial;background:#0b0b0b;color:#e5e5e5;margin:30px}}h1{{font-family:Consolas,monospace;margin-bottom:8px}}h2{{margin-top:36px}}table{{border-collapse:collapse;width:100%;background:#151515;margin:18px 0 32px}}th{{background:#242424;color:#e5e5e5;text-align:left}}td,th{{padding:14px;border:1px solid #333;vertical-align:top}}td{{max-width:330px;overflow-wrap:anywhere}}.fresh{{color:#cc9df4}}.manual{{color:#ff827d;font-weight:700}}.attention{{background:#302917;color:#f2d681}}.ok{{color:#8bccaa}}small{{color:#aaa}}.scope{{background:#222018;border-left:3px solid #d8bc70;padding:16px;line-height:1.7}}a{{color:#9bc1ff}}p{{color:#aaa;line-height:1.7}}</style></head>
<body><h1>Vessel Watch — tracking report</h1><p>Run: {esc(report['run_id'])} · {esc(report['created_at'])} · {esc(report['status'])}</p>
<p>ActiveTrack capture: {esc(baseline['captured_at'])}<br>Source: {esc(baseline['source_path'])}<br>{baseline['bookings']} bookings · {baseline['schedules']} schedules · {len(report['results'])} legs</p>
<div class="scope">Counts and MAS checks cover the captured filter only. Verify all linked bookings before choosing automatic notification. Purple = compared date changes; yellow = incomplete verification; red = manual notice required. This report does not update ActiveTrack or send emails. Vessel actuals are not proof that your containers loaded.</div>
<h2>Schedule comparisons</h2><table><tr><th>VS / leg</th><th>Vessel / voyage</th><th>Route</th><th>Bookings</th><th>Old → source date</th><th>Verification coverage</th><th>Customer notice</th></tr>{''.join(rows)}</table>
<h2>Website check log</h2><table><tr><th>Site</th><th>Vessel</th><th>Status</th><th>Detail</th></tr>{failures}</table>
<h2>Same-voyage arrival mismatches</h2><ul>{align or '<li>None in this captured scope.</li>'}</ul>
<h2>Missing booking details</h2><table><tr><th>Booking</th><th>VS</th><th>HBL</th><th>CS</th><th>Missing</th></tr>{missing}</table>
</body></html>"""
    result = folder / "report.html"
    result.write_text(text, encoding="utf-8")
    return result


class History:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, created_at TEXT, status TEXT, path TEXT, summary TEXT)")
        self.db.execute("CREATE TABLE IF NOT EXISTS observations (run_id TEXT, site TEXT, vessel TEXT, body TEXT)")

    def save(self, report, path):
        with self.db:
            self.db.execute("INSERT INTO runs VALUES(?,?,?,?,?)", (report["run_id"], report["created_at"], report["status"], str(path),
                  json.dumps({"legs": len(report["results"]), "changes": sum(bool(x["changes"]) for x in report["results"])})))
            self.db.executemany("INSERT INTO observations VALUES(?,?,?,?)", [(report["run_id"], c["site"], c["vessel"], json.dumps(c)) for c in report["observations"]])

    def close(self):
        self.db.close()
