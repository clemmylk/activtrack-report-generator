"""Integrated, clipboard-only notice headers and date-change remarks."""
from datetime import date, datetime
import tkinter as tk
from tkinter import ttk, messagebox

HEADERS = [
    ("ETA delay", "ETA CHANGED AND DELAYED"),
    ("ETD delay", "ETD CHANGED AND DELAYED"),
    ("ETD + ETA delay", "ETD AND ETA CHANGED AND DELAYED"),
    ("First leg ETA", "FIRST LEG VESSEL ETA CHANGED AND DELAYED"),
    ("First leg ETD", "FIRST LEG VESSEL ETD CHANGED AND DELAYED"),
    ("Second leg ETA", "SECOND LEG VESSEL ETA CHANGED AND DELAYED"),
    ("Second leg ETD", "SECOND LEG VESSEL ETD CHANGED AND DELAYED"),
    ("Vessel change", "VESSEL SCHEDULE CHANGE"),
    ("Vessel + ETD + ETA", "VESSEL CHANGE, ETD AND ETA CHANGED AND DELAYED"),
    ("Arrived", "VESSEL HAS ARRIVED"),
    ("Departed", "SHIPMENT HAS DEPARTED"),
    ("First leg departed", "FIRST LEG VESSEL HAS DEPARTED"),
    ("Second leg departed", "SECOND LEG VESSEL HAS DEPARTED"),
    ("Second leg added", "SECOND LEG VESSEL ADDED"),
    ("ETA advanced", "ETA CHANGED AND ADVANCED"),
    ("ETD advanced", "ETD CHANGED AND ADVANCED"),
    ("Arriving within 48h", "SHIPMENT ARRIVING WITHIN 48H"),
    ("First leg arrived", "FIRST LEG VESSEL ARRIVED"),
    ("Second leg arrived", "SECOND LEG VESSEL ARRIVED"),
]

def parse_date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text or text in {"—", "-"}:
        return None
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %b %y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Invalid date: {text}. Use YYYY-MM-DD.")

def changes_for(row):
    changes = []
    for field in ("etd", "eta"):
        old, new = parse_date(row.get("at_" + field)), parse_date(row.get("carrier_" + field))
        if old and new and old != new:
            changes.append((field.upper(), old, new, (new - old).days))
    return changes

def leg_prefix(row):
    try:
        leg, total = int(row.get("leg") or 1), int(row.get("total_legs") or 1)
    except (ValueError, TypeError):
        return ""
    if leg == 2:
        return "SECOND LEG VESSEL "
    if leg == 1 and total > 1:
        return "FIRST LEG VESSEL "
    if leg > 2:
        return f"LEG {leg} VESSEL "
    return ""

def build_notice(row, reason=""):
    changes = changes_for(row)
    initial_vessel = str(row.get("vessel") or "").strip()
    revised_vessel = str(row.get("revised_vessel") or "").strip()
    revised_voyage = str(row.get("revised_voyage") or "").strip()
    vessel_changed = bool(revised_vessel and (revised_vessel.upper() != initial_vessel.upper() or (revised_voyage and revised_voyage.upper() != str(row.get("voyage") or "").upper())))
    if not changes and not vessel_changed:
        raise ValueError("No confirmed date difference is available. Both original and revised dates are required.")
    directions = ["DELAYED" if change[3] > 0 else "ADVANCED" for change in changes]
    prefix = leg_prefix(row)
    if not changes:
        header = prefix + ("SCHEDULE CHANGE" if initial_vessel else "ADDED")
    elif len(set(directions)) == 1:
        header = prefix + " AND ".join(change[0] for change in changes) + " CHANGED AND " + directions[0]
    else:
        header = prefix + " AND ".join(f"{change[0]} {direction}" for change, direction in zip(changes, directions))
    if vessel_changed and changes:
        header = "VESSEL CHANGE, " + header
    elif vessel_changed and not prefix:
        header = "VESSEL SCHEDULE CHANGE" if initial_vessel else "VESSEL ADDED"
    intro = "PLEASE BE INFORMED THAT THE SUBJECT SHIPMENT " + prefix + "SCHEDULE HAS BEEN REVISED"
    reason = " ".join(str(reason or "").split()).strip().rstrip(".")
    if reason.lower().startswith("due to "):
        reason = reason[7:]
    if reason:
        intro += " DUE TO " + reason.upper()
    lines = [intro + ".", ""]
    identity = " / ".join(str(row.get(key) or "").strip() for key in ("vessel", "voyage") if row.get(key))
    if identity:
        lines.append("VESSEL / VOYAGE: " + identity.upper())
    if vessel_changed:
        revised = " / ".join(v for v in (revised_vessel,revised_voyage) if v)
        lines.append("REVISED VESSEL / VOYAGE: " + revised.upper())
    if row.get("vs_id"):
        lines.append("SCHEDULE: " + str(row["vs_id"]).upper())
    for field, before, after, delta in changes:
        port = str(row.get("pol" if field == "ETD" else "pod") or "").strip()
        label = field + (" " + port.upper() if port else "")
        direction = "DELAYED" if delta > 0 else "ADVANCED"
        days = abs(delta)
        lines += ["", f"{label}: {before:%d %b %Y} → {after:%d %b %Y}.".upper(),
                  f"({direction} BY {days} {'DAY' if days == 1 else 'DAYS'})."]
    lines += ["", "WE WILL CONTINUE TO MONITOR THE SHIPMENT AND KEEP YOU POSTED."]
    return {"header": header, "remarks": "\n".join(lines)}

def copy_text(window, text):
    window.clipboard_clear()
    window.clipboard_append(text)
    window.update_idletasks()

class NoticeTools:
    """Native child window, independent of the old PowerShell installation."""
    def __init__(self, parent, row=None):
        self.window = tk.Toplevel(parent)
        self.window.title("ActivTrack · Notice Headers & Remarks")
        self.window.geometry("900x700")
        self.window.minsize(760, 580)
        self.window.configure(bg="#0B1220")
        self.row = dict(row or {})
        self.status = tk.StringVar(value="Clipboard only · Nothing is sent to ActiveTrack or customers")
        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)
        headers, builder = ttk.Frame(notebook, padding=16), ttk.Frame(notebook, padding=16)
        notebook.add(headers, text="Notice headers")
        notebook.add(builder, text="Build remarks")
        ttk.Label(headers, text="Choose a header, then copy it.", font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(0,12))
        listing = ttk.Treeview(headers, columns=("label", "text"), show="headings", height=17)
        listing.heading("label", text="NOTICE TYPE"); listing.heading("text", text="HEADER TO COPY")
        listing.column("label", width=190); listing.column("text", width=540)
        listing.pack(fill="both", expand=True)
        for i,(label,text) in enumerate(HEADERS):
            listing.insert("", "end", iid=str(i), values=(label,text))
        def copy_header(event=None):
            selection = listing.selection()
            if selection:
                self.copy(HEADERS[int(selection[0])][1], "Header copied")
        ttk.Button(headers, text="Copy selected header", command=copy_header).pack(anchor="e", pady=(12,0))
        listing.bind("<Double-1>", copy_header)
        listing.bind("<Return>", copy_header)
        fields = ttk.Frame(builder); fields.pack(fill="x")
        self.fields = {}
        labels = [("vessel","Vessel"),("voyage","Voyage"),("vs_id","VS ID"),
                  ("pol","POL"),("pod","POD"),("leg","Leg number"),("total_legs","Total legs"),
                  ("revised_vessel","Revised vessel (optional)"),("revised_voyage","Revised voyage (optional)"),
                  ("at_etd","Original ETD"),("carrier_etd","Revised ETD"),
                  ("at_eta","Original ETA"),("carrier_eta","Revised ETA"),("reason","Reason (optional)")]
        for i,(key,label) in enumerate(labels):
            r,c = i//2,(i%2)*2
            ttk.Label(fields, text=label).grid(row=r,column=c,sticky="w",padx=(0,8),pady=4)
            default = self.row.get(key) or (1 if key in {"leg","total_legs"} else "")
            self.fields[key] = tk.StringVar(value=str(default))
            ttk.Entry(fields,textvariable=self.fields[key],width=25).grid(row=r,column=c+1,sticky="ew",padx=(0,14),pady=4)
        fields.columnconfigure(1,weight=1);fields.columnconfigure(3,weight=1)
        ttk.Label(builder,text="Dates: YYYY-MM-DD. Leave unknown pairs blank. Only changed date pairs are included.").pack(anchor="w",pady=8)
        self.preview = tk.Text(builder,wrap="word",height=7,bg="#111D32",fg="#E2E8F0",font=("Segoe UI",10),padx=12,pady=12,state="disabled")
        self.preview.pack(fill="both",expand=True)
        actions = ttk.Frame(builder);actions.pack(side="bottom",before=self.preview,fill="x",pady=(10,0))
        ttk.Button(actions,text="Preview",command=self.refresh).pack(side="left")
        ttk.Button(actions,text="Copy header",command=lambda:self.copy_part("header")).pack(side="left",padx=8)
        ttk.Button(actions,text="Copy remarks",command=lambda:self.copy_part("remarks")).pack(side="left")
        ttk.Label(self.window,textvariable=self.status,wraplength=850).pack(side="bottom",before=notebook,fill="x",padx=16,pady=(0,12))
        self.payload = None
        if row:
            notebook.select(builder)
            self.refresh()
        from desktop_ui import lighten_widgets
        lighten_widgets(self.window)

    def refresh(self):
        row = {key:value.get().strip() for key,value in self.fields.items()}
        try:
            for key in ("etd","eta"):
                if bool(row["at_"+key]) != bool(row["carrier_"+key]):
                    raise ValueError(f"Enter both original and revised {key.upper()}, or leave both blank.")
            self.payload = build_notice(row,row.pop("reason"))
            text = self.payload["header"] + "\n\n" + self.payload["remarks"]
        except ValueError as exc:
            self.payload = None
            text = str(exc)
        self.preview.configure(state="normal");self.preview.delete("1.0","end")
        self.preview.insert("1.0",text);self.preview.configure(state="disabled")
        return self.payload

    def copy_part(self, part):
        payload = self.refresh()
        if payload:
            self.copy(payload[part],part.title()+" copied")
        else:
            self.status.set("Complete the date pairs shown in the preview before copying.")

    def copy(self,text,status):
        try:
            copy_text(self.window,text)
            self.status.set(status+" · Clipboard only; nothing sent")
        except tk.TclError as exc:
            messagebox.showerror("Clipboard unavailable",str(exc),parent=self.window)
