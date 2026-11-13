"""Floating arrival watch, using authorized MarineTraffic AIS API access."""
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

INTERVAL = 3600
STALE_SECONDS = 7200
DATA_PATH = Path.home() / "Documents" / "ActivTrack Reports" / "arrival_queue.json"
COLORS = {"UNDERWAY": "#ef4444", "ANCHOR": "#f59e0b", "MOORED": "#22c55e", "UNKNOWN": "#a1a1aa"}


def classify(code: object) -> tuple[str, str]:
    value = str(code).strip()
    return {"0": ("UNDERWAY", "Underway by engine"), "1": ("ANCHOR", "At anchor"),
            "5": ("MOORED", "Moored")}.get(value, ("UNKNOWN", "Other / unavailable"))


def read_position(imo: str, api_key: str) -> dict:
    if not re.fullmatch(r"\d{7}", imo):
        raise ValueError("A 7-digit IMO number is required")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", api_key):
        raise ValueError("MarineTraffic API access is not configured")
    params = urlencode({"v": 6, "imo": imo, "timespan": 1440, "protocol": "jsono"})
    url = f"https://services.marinetraffic.com/api/exportvessel/{api_key}?{params}"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=25) as response:
            data = json.load(response)
    except Exception as exc:
        # API keys are part of the URL. Do not expose exception text or URLs.
        raise RuntimeError("MarineTraffic access failed or was denied") from exc
    if isinstance(data, dict):
        data = data.get("DATA") or data.get("data") or []
    if not isinstance(data, list) or len(data) != 1 or not isinstance(data[0], dict):
        raise ValueError("No unique vessel position returned")
    row = data[0]
    if str(row.get("IMO") or "").strip() != imo:
        raise ValueError("Returned vessel identity does not match IMO")
    raw_time = str(row.get("TIMESTAMP") or "")
    try:
        seen = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds()
        if age < -300 or age > STALE_SECONDS:
            raise ValueError("AIS position is stale")
    except (ValueError, TypeError) as exc:
        raise ValueError("AIS time missing or stale") from exc
    status, label = classify(row.get("STATUS"))
    return {"status": status, "label": label, "ais_at": seen.astimezone().isoformat(timespec="seconds"),
            "port": str(row.get("CURRENT_PORT") or "").strip(),
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}


class ArrivalQueue:
    def __init__(self, parent):
        self.parent = parent
        self.window = tk.Toplevel(parent)
        self.window.title("Arrival Que")
        self.window.geometry("340x310+20+20")
        self.window.minsize(300, 220)
        self.window.attributes("-topmost", True)
        try:
            self.window.attributes("-alpha", 0.92)
        except tk.TclError:
            pass
        self.window.configure(bg="#17191e")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.items = self._load()
        self.results = {}
        self.running = False
        self.closed = False
        self.last_checked = None
        self.next_check = None
        head = tk.Frame(self.window, bg="#17191e")
        head.pack(fill="x", padx=12, pady=(9, 4))
        tk.Label(head, text="ARRIVAL QUE", bg="#17191e", fg="white", font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Button(head, text="Manage", command=self.manage, relief="flat", bg="#343945", fg="white").pack(side="right")
        self.timer = tk.Canvas(self.window, width=112, height=80, bg="#17191e", highlightthickness=0)
        self.timer.pack(side="right", padx=(0, 10), anchor="n")
        list_area = tk.Frame(self.window, bg="#17191e")
        list_area.pack(fill="both", expand=True, padx=(12, 0))
        self.list_canvas = tk.Canvas(list_area, bg="#17191e", highlightthickness=0)
        scroll = ttk.Scrollbar(list_area, orient="vertical", command=self.list_canvas.yview)
        self.list_canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.list_canvas.pack(side="left", fill="both", expand=True)
        self.list_frame = tk.Frame(self.list_canvas, bg="#17191e")
        self.list_window = self.list_canvas.create_window((0, 0), window=self.list_frame, anchor="nw")
        self.list_frame.bind("<Configure>", lambda _event: self.list_canvas.configure(scrollregion=self.list_canvas.bbox("all")))
        self.list_canvas.bind("<Configure>", lambda event: self.list_canvas.itemconfigure(self.list_window, width=event.width))
        self.note = tk.Label(self.window, text="", bg="#17191e", fg="#c2c6d1", font=("Segoe UI", 8), anchor="w")
        self.note.pack(fill="x", padx=12, pady=(3, 8))
        self._render()
        self.window.after(200, self.refresh)
        self._tick()

    def _load(self):
        try:
            data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
            return [item for item in data.get("vessels", []) if isinstance(item, dict) and item.get("name") and item.get("imo")]
        except (OSError, ValueError, TypeError):
            return []

    def _save(self):
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        DATA_PATH.write_text(json.dumps({"vessels": self.items}, indent=2), encoding="utf-8")

    def manage(self):
        dialog = tk.Toplevel(self.window)
        dialog.title("Manage arrival vessels")
        dialog.geometry("520x420")
        dialog.transient(self.window)
        tk.Label(dialog, text="Enter one vessel per line: NAME, IMO (7 digits)", anchor="w").pack(fill="x", padx=12, pady=(12, 4))
        tk.Label(dialog, text="Example: WAN HAI 507, 9326407", anchor="w").pack(fill="x", padx=12)
        editor = tk.Text(dialog, height=12, font=("Consolas", 10))
        editor.pack(fill="both", expand=True, padx=12, pady=8)
        editor.insert("1.0", "\n".join(f"{x['name']}, {x['imo']}" for x in self.items))
        tk.Label(dialog, text="An authorized MarineTraffic vessel-position API key is needed. It is kept only for this app session.", wraplength=490, anchor="w", justify="left").pack(fill="x", padx=12)
        def save():
            parsed, seen = [], set()
            for line in editor.get("1.0", "end").splitlines():
                if not line.strip():
                    continue
                parts = line.rsplit(",", 1)
                if len(parts) != 2 or not parts[0].strip() or not re.fullmatch(r"\d{7}", parts[1].strip()):
                    messagebox.showerror("Arrival Que", "Each line needs a vessel name and a 7-digit IMO, separated by a comma.", parent=dialog)
                    return
                name, imo = parts[0].strip(), parts[1].strip()
                if imo not in seen:
                    parsed.append({"name": name, "imo": imo})
                    seen.add(imo)
            self.items = parsed
            self.results = {k: v for k, v in self.results.items() if k in seen}
            self._save()
            dialog.destroy()
            self._render()
            self.refresh()
        bar = tk.Frame(dialog)
        bar.pack(fill="x", padx=12, pady=12)
        def set_key():
            key = simpledialog.askstring("MarineTraffic access", "Enter your authorized MarineTraffic API key:", show="*", parent=dialog)
            if key is None:
                return
            if not re.fullmatch(r"[a-fA-F0-9]{40}", key.strip()):
                messagebox.showerror("Arrival Que", "The API key must contain 40 hexadecimal characters.", parent=dialog)
                return
            os.environ["MARINETRAFFIC_API_KEY"] = key.strip()
            messagebox.showinfo("Arrival Que", "API key set for this app session. Save the vessel list to start checking.", parent=dialog)
        ttk.Button(bar, text="Set API key", command=set_key).pack(side="left")
        ttk.Button(bar, text="Save and check", command=save).pack(side="right")
        ttk.Button(bar, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)

    def refresh(self):
        if self.running or self.closed:
            return
        if not self.items:
            self.note.config(text="Add vessels with Manage to begin.")
            self.next_check = None
            return
        key = os.environ.get("MARINETRAFFIC_API_KEY", "").strip()
        if not key:
            self.note.config(text="API access not configured. See Manage.")
            self.next_check = None
            self._render()
            return
        self.running = True
        self.next_check = None
        self.note.config(text="Checking MarineTraffic...")
        snapshot = list(self.items)
        def worker():
            results = {}
            for item in snapshot:
                try:
                    results[item["imo"]] = read_position(item["imo"], key)
                except Exception as exc:
                    results[item["imo"]] = {"status": "UNKNOWN", "label": str(exc), "checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            if not self.closed:
                self.window.after(0, lambda: self._finish(results))
        threading.Thread(target=worker, name="arrival-check", daemon=True).start()

    def _finish(self, results):
        if self.closed:
            return
        self.results = results
        self.running = False
        self.last_checked = datetime.now().astimezone()
        self.next_check = time.monotonic() + INTERVAL
        self.note.config(text="Click a vessel for details. Checks run hourly while open.")
        self._render()
        if set(results) != {item["imo"] for item in self.items}:
            self.window.after(100, self.refresh)

    def _render(self):
        for child in self.list_frame.winfo_children():
            child.destroy()
        if not self.items:
            tk.Label(self.list_frame, text="No vessels added", bg="#17191e", fg="#a1a1aa").pack(anchor="w", pady=12)
            return
        for item in self.items:
            result = self.results.get(item["imo"], {})
            status = result.get("status", "UNKNOWN")
            text = item["name"] if len(item["name"]) <= 16 else item["name"][:15] + "…"
            row = tk.Frame(self.list_frame, bg="#17191e")
            row.pack(fill="x", pady=2)
            tk.Label(row, text="●", fg=COLORS.get(status, COLORS["UNKNOWN"]), bg="#17191e", font=("Segoe UI", 12)).pack(side="left")
            tk.Button(row, text=text, bg="#17191e", fg="white", relief="flat", anchor="w", command=lambda i=item: self.details(i)).pack(side="left", fill="x", expand=True)
            tk.Label(row, text={"UNDERWAY":"SAILING", "ANCHOR":"ANCHOR", "MOORED":"MOORED", "UNKNOWN":"CHECK"}.get(status, "CHECK"), fg=COLORS.get(status, COLORS["UNKNOWN"]), bg="#17191e", font=("Segoe UI", 8, "bold")).pack(side="right")

    def details(self, item):
        r = self.results.get(item["imo"], {})
        text = f"{item['name']}\nIMO {item['imo']}\n\nStatus: {r.get('label', 'Not checked')}\nPort: {r.get('port') or 'Not available'}\nAIS received: {r.get('ais_at') or 'Not available'}\nChecked: {r.get('checked_at') or 'Not yet'}"
        messagebox.showinfo("Arrival Que", text, parent=self.window)

    def _tick(self):
        if self.closed:
            return
        expired = False
        for result in self.results.values():
            if result.get("status") == "UNKNOWN" or not result.get("ais_at"):
                continue
            try:
                seen = datetime.fromisoformat(result["ais_at"])
                if (datetime.now(timezone.utc) - seen.astimezone(timezone.utc)).total_seconds() > STALE_SECONDS:
                    result["status"] = "UNKNOWN"
                    result["label"] = "AIS position is stale"
                    expired = True
            except (ValueError, TypeError):
                result["status"] = "UNKNOWN"
                result["label"] = "AIS time unavailable"
                expired = True
        if expired:
            self._render()
        self.timer.delete("all")
        self.timer.create_oval(13, 3, 78, 68, outline="#555b66", width=5)
        remaining = max(0, self.next_check - time.monotonic()) if self.next_check is not None else 0
        if self.next_check is not None:
            self.timer.create_arc(13, 3, 78, 68, start=90, extent=-360 * remaining / INTERVAL, style="arc", outline="#8b5cf6", width=5)
        self.timer.create_text(46, 29, text=self.last_checked.strftime("%H:%M") if self.last_checked else "--:--", fill="white", font=("Segoe UI", 10, "bold"))
        self.timer.create_text(46, 46, text=f"{int(remaining // 60):02d}:{int(remaining % 60):02d}" if self.next_check is not None else "WAIT", fill="#c2c6d1", font=("Segoe UI", 8))
        if self.next_check is not None and remaining <= 0:
            self.refresh()
        self.window.after(1000, self._tick)

    def close(self):
        self.closed = True
        self.window.destroy()
