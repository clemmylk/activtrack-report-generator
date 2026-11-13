"""Compact arrival-watch island using authorized MarineTraffic data."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import tkinter as tk
from datetime import datetime, timezone
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from arrival_queue import DATA_PATH, INTERVAL, STALE_SECONDS, read_position
from arrival_aisstream import check_vessels, load_key, save_key

BG = "#101218"
OUTSIDE = "#ff00ff"
COLORS = {"UNDERWAY": "#ef5350", "ANCHOR": "#ffab40", "MOORED": "#32d583", "UNKNOWN": "#a1a8b5"}


def name_key(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().upper())


def saved_vessel_imos(name: str, reports: Path | None = None) -> set[str]:
    """Use exact names only; ambiguous historical names are not guessed."""
    folder = reports or DATA_PATH.parent
    paths = sorted(folder.glob("ActivTrack_Vessel_Booking_Report_*_Source.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)[:10]
    found = set()
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            for schedule in data.get("schedules") or []:
                for leg in schedule.get("transshipments") or []:
                    vessel = leg.get("vessel") or {}
                    imo = str(vessel.get("imo_number") or "").strip()
                    if name_key(str(vessel.get("name") or "")) == name_key(name) and re.fullmatch(r"\d{7}", imo):
                        found.add(imo)
        except (OSError, ValueError, TypeError, AttributeError):
            continue
    return found


def resolve_name(name: str, key: str, known_imo: str = "") -> str:
    if re.fullmatch(r"\d{7}", str(known_imo)):
        return str(known_imo)
    local = saved_vessel_imos(name)
    if len(local) == 1:
        return next(iter(local))
    if len(local) > 1:
        raise ValueError("Multiple matching vessel identities")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", key):
        raise ValueError("MarineTraffic API access needed")
    params = urlencode({"shipname": name, "protocol": "jsono"})
    try:
        with urlopen(Request(f"https://services.marinetraffic.com/api/shipsearch/{key}?{params}",
                             headers={"Accept": "application/json"}), timeout=25) as response:
            data = json.load(response)
    except Exception as exc:
        raise RuntimeError("Vessel search unavailable; check API access") from exc
    if isinstance(data, dict):
        data = data.get("DATA") or data.get("data") or []
    if not isinstance(data, list):
        raise ValueError("Vessel search returned no usable result")
    exact = {str(row.get("IMO") or "").strip() for row in data if isinstance(row, dict)
             and name_key(str(row.get("SHIPNAME") or "")) == name_key(name)
             and re.fullmatch(r"\d{7}", str(row.get("IMO") or "").strip())}
    if len(exact) != 1:
        raise ValueError("Vessel name needs identity review" if exact else "Vessel name not found")
    return next(iter(exact))


class ArrivalIsland:
    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Arrival Que")
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-transparentcolor", OUTSIDE)
            self.window.attributes("-alpha", .96)
        except tk.TclError:
            pass
        self.window.configure(bg=OUTSIDE)
        self.scale = max(1.0, min(2.5, self.window.winfo_fpixels('1i') / 96.0))
        self.window.geometry(f"{round(320*self.scale)}x{round(54*self.scale)}+18+12")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.canvas = tk.Canvas(self.window, bg=OUTSIDE, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.items = self._load()
        self.ais_key = load_key()
        self.results = {}
        self.last_checked = None
        self.next_check = None
        self.running = False
        self.pending = False
        self.closed = False
        self.expanded = False
        self.offset = 0
        self.canvas.bind("<Button-1>", self._click)
        self.canvas.bind("<Button-3>", lambda _e: self.manage())
        self.canvas.bind("<MouseWheel>", self._scroll)
        self.window.after(100, self.refresh)
        self._tick()

    def _load(self):
        try:
            obj = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            result, seen = [], set()
            for item in obj.get("vessels", []):
                if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                    continue
                name = str(item["name"]).strip()
                if name_key(name) not in seen:
                    result.append({"name": name, "imo": str(item.get("imo") or "").strip()})
                    seen.add(name_key(name))
            return result
        except (OSError, ValueError, TypeError):
            return []

    def _save(self):
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps({"vessels": self.items}, indent=2), encoding="utf-8")

    def manage(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Manage arrival vessels")
        dialog.geometry("480x395")
        dialog.transient(self.window)
        tk.Label(dialog, text="Vessel names — one per line", font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=15, pady=(12, 4))
        editor = tk.Text(dialog, height=10, font=("Segoe UI", 10))
        editor.pack(fill="both", expand=True, padx=15, pady=5)
        editor.insert("1.0", "\n".join(item["name"] for item in self.items))
        tk.Label(dialog, text="Save checks these names near Sri Lanka immediately. AISStream key is saved securely for this Windows account.", wraplength=440, justify="left").pack(anchor="w", padx=15, pady=4)
        bar = ttk.Frame(dialog)
        bar.pack(fill="x", padx=15, pady=12)
        def set_key():
            key = simpledialog.askstring("AISStream access", "Enter your free AISStream key:", show="*", parent=dialog)
            if key is None:
                return
            if not key.strip():
                messagebox.showerror("Arrival Que", "Enter an AISStream key.", parent=dialog)
                return
            try:
                save_key(key.strip())
                self.ais_key = key.strip()
            except OSError:
                messagebox.showerror("Arrival Que", "Windows could not save the key securely.", parent=dialog)
        def save():
            old = {name_key(x["name"]): x for x in self.items}
            items, seen = [], set()
            for line in editor.get("1.0", "end").splitlines():
                name = line.strip()
                if not name or name_key(name) in seen:
                    continue
                # Accept previous NAME, IMO format for a painless migration.
                match = re.fullmatch(r"(.+?),\s*(\d{7})", name)
                imo = match.group(2) if match else ""
                name = match.group(1).strip() if match else name
                k = name_key(name)
                if k in seen:
                    continue
                seen.add(k)
                items.append({"name": name, "imo": imo or old.get(k, {}).get("imo", "")})
            self.items = items
            self.offset = 0
            self.results = {k: v for k, v in self.results.items() if k in seen}
            self._save()
            dialog.destroy()
            self.expanded = True
            self._resize()
            self.refresh()
        ttk.Button(bar, text="API key", command=set_key).pack(side="left")
        ttk.Button(bar, text="Save and check", command=save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)

    def _resize(self):
        height = min(480, max(146, 100 + len(self.items) * 30)) if self.expanded else 54
        self.window.geometry(f"{round(320*self.scale)}x{round(height*self.scale)}+18+12")

    def refresh(self):
        if self.closed:
            return
        if self.running:
            self.pending = True
            return
        if not self.items:
            self.next_check = None
            self._draw()
            return
        key = self.ais_key
        if not key:
            self.next_check = None
            self.results = {name_key(item["name"]): {"status": "UNKNOWN", "label": "AISStream key required"} for item in self.items}
            self._draw()
            return
        self.running = True
        self.next_check = None
        snapshot = [dict(item) for item in self.items]
        self._draw()
        def work():
            result = check_vessels(snapshot, key)
            if not self.closed:
                try:
                    self.window.after(0, lambda: self._finish(result))
                except (RuntimeError, tk.TclError):
                    pass
        threading.Thread(target=work, name="arrival-island", daemon=True).start()

    def _finish(self, results):
        if self.closed:
            return
        active = {name_key(x["name"]) for x in self.items}
        self.results = {k: v for k, v in results.items() if k in active}
        for item in self.items:
            result = self.results.get(name_key(item["name"]), {})
            if result.get("imo"):
                item["imo"] = result["imo"]
        self._save()
        self.running = False
        self.last_checked = datetime.now().astimezone()
        self.next_check = time.monotonic() + INTERVAL
        if self.pending or active != set(results):
            self.pending = False
            self.refresh()
        else:
            self._draw()

    def _round_rect(self, x1, y1, x2, y2, r):
        self.canvas.create_polygon(x1+r,y1,x2-r,y1,x2,y1,x2,y1+r,x2,y2-r,x2,y2,x2-r,y2,x1+r,y2,x1,y2,x1,y2-r,x1,y1+r,x1,y1, smooth=True, fill=BG, outline="#3a3e49", width=1)

    def _draw(self):
        if self.closed:
            return
        self.canvas.delete("all")
        h = min(480, max(146, 100 + len(self.items)*30)) if self.expanded else 54
        self._round_rect(2,2,318,h-2,24)
        self.canvas.create_text(20,27, text="●", fill="#8b5cf6", font=("Segoe UI", 12), anchor="w")
        self.canvas.create_text(42,19, text="ARRIVAL QUE", fill="#f6f7fa", font=("Segoe UI", 9, "bold"), anchor="w")
        label = "CHECKING" if self.running else ("KEY REQUIRED" if self.items and not self.ais_key else f"{len(self.items)} VESSELS")
        self.canvas.create_text(42,37, text=label, fill="#a1a8b5", font=("Segoe UI", 7), anchor="w")
        remaining = max(0, self.next_check-time.monotonic()) if self.next_check is not None else 0
        self.canvas.create_oval(218,7,256,45, outline="#4b5160", width=3)
        if self.next_check is not None:
            self.canvas.create_arc(218,7,256,45,start=90,extent=-360*remaining/INTERVAL,style="arc",outline="#9c7aff",width=3)
        self.canvas.create_text(237,22,text=self.last_checked.strftime("%H:%M") if self.last_checked else "--:--",fill="white",font=("Segoe UI",7,"bold"))
        self.canvas.create_text(237,33,text=f"{int(remaining//60):02d}:{int(remaining%60):02d}" if self.next_check is not None else "WAIT",fill="#b5bac5",font=("Segoe UI",6))
        self.canvas.create_text(279,27,text="−" if self.expanded else "+",fill="white",font=("Segoe UI",15))
        self.canvas.create_text(303,27,text="×",fill="#a1a8b5",font=("Segoe UI",13))
        if self.expanded:
            self.canvas.create_line(16,53,304,53,fill="#303440")
            visible = self.items[self.offset:self.offset+12]
            for i,item in enumerate(visible):
                y=72+i*30
                result=self.results.get(name_key(item["name"]),{})
                status=result.get("status","UNKNOWN")
                self.canvas.create_text(22,y,text="●",fill=COLORS.get(status,COLORS["UNKNOWN"]),font=("Segoe UI",11),anchor="w")
                name=item["name"]
                self.canvas.create_text(42,y,text=name if len(name)<=19 else name[:18]+"…",fill="white",font=("Segoe UI",9,"bold"),anchor="w")
                status_label = {"UNDERWAY":"UNDERWAY","ANCHOR":"ANCHOR","MOORED":"MOORED","UNKNOWN":"NO SIGNAL"}.get(status,"NO SIGNAL")
                if self.running and status == 'UNKNOWN':
                    status_label = 'LISTENING'
                elif status == 'UNKNOWN' and ('failed' in result.get('label','').lower() or 'confirm' in result.get('label','').lower()):
                    status_label = 'FEED ERROR'
                self.canvas.create_text(293,y,text=status_label,fill=COLORS.get(status,COLORS["UNKNOWN"]),font=("Segoe UI",8,"bold"),anchor="e")
            y=h-22
            self.canvas.create_text(23,y,text="MANAGE",fill="#bba7ff",font=("Segoe UI",8,"bold"),anchor="w")
            self.canvas.create_text(175,y,text="CHECK NOW",fill="#bba7ff",font=("Segoe UI",8,"bold"),anchor="w")
            if len(self.items)>12:
                self.canvas.create_text(160,54+12*30+12,text=f"{self.offset+1}–{min(self.offset+12,len(self.items))} of {len(self.items)} · scroll",fill="#a1a8b5",font=("Segoe UI",7))
        self.canvas.scale("all", 0, 0, self.scale, self.scale)

    def _scroll(self, event):
        if self.expanded and len(self.items) > 12:
            self.offset = max(0, min(len(self.items)-12, self.offset + (-1 if event.delta > 0 else 1)))
            self._draw()

    def _click(self,event):
        x, y = event.x / self.scale, event.y / self.scale
        if y < 53:
            if x > 292:
                self.close()
            else:
                self.expanded = not self.expanded
                self._resize()
                self._draw()
            return
        height = min(480, max(146, 100 + len(self.items)*30))
        if y >= height-42:
            self.manage() if x < 150 else self.refresh()
            return
        index = self.offset + int((y-57)//30)
        if self.offset <= index < min(len(self.items),self.offset+12):
            item=self.items[index]
            r=self.results.get(name_key(item["name"]),{})
            messagebox.showinfo("Arrival Que",f"{item['name']}\nIMO: {r.get('imo') or item.get('imo') or 'Not resolved'}\nStatus: {r.get('label','Not checked')}\nPort: {r.get('port') or 'Not available'}\nAIS time: {r.get('ais_at') or 'Not available'}\nChecked: {r.get('checked_at') or 'Not available'}",parent=self.window)

    def _tick(self):
        if self.closed:
            return
        for result in self.results.values():
            if result.get("status") == "UNKNOWN" or not result.get("ais_at"):
                continue
            try:
                age=(datetime.now(timezone.utc)-datetime.fromisoformat(result["ais_at"]).astimezone(timezone.utc)).total_seconds()
                if age>STALE_SECONDS:
                    result.update(status="UNKNOWN",label="AIS position is stale")
            except (TypeError,ValueError):
                result.update(status="UNKNOWN",label="AIS time unavailable")
        if self.next_check is not None and time.monotonic() >= self.next_check:
            self.refresh()
        self._draw()
        self.window.after(1000,self._tick)

    def close(self):
        self.closed=True
        self.window.destroy()
