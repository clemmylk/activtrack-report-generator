"""Carrier tracking window for the ActivTrack report application."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from tracking_report import (
    TrackingReportStats,
    build_tracking_report,
    filter_in_transit_vessels,
    latest_booking_source,
    parse_vessel_names,
)
from tracking_config import CARRIERS, CARRIER_REPORTS_DIR


class TrackingWindow:
    def __init__(self, parent: tk.Misc, output_dir_getter, embedded=False) -> None:
        self.parent = parent
        self.output_dir_getter = output_dir_getter
        self.embedded=embedded
        self.window = tk.Frame(parent,bg='#FFFFFF') if embedded else tk.Toplevel(parent)
        if embedded:self.window.pack(fill='both',expand=True)
        else:
            self.window.title("Start Tracking")
            self.window.geometry("900x650")
            self.window.minsize(780, 560)
            self.window.transient(parent)

        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="carrier-tracking")
        self.busy = False
        self._active_process=None
        self.stop_requested=False
        self.carrier_buttons: list[ttk.Button] = []
        self.status_var = tk.StringVar(value="Choose a carrier to begin.")
        self.source_var = tk.StringVar(value="Checking latest booking report…")
        self.progress_text_var = tk.StringVar(value="Ready")
        self.last_output: Path | None = None

        self._build_ui()
        self._refresh_source_label()
        if not embedded:self.window.protocol("WM_DELETE_WINDOW", self._close)
        from desktop_ui import lighten_widgets
        lighten_widgets(self.window)

    def _build_ui(self) -> None:
        top_line = tk.Frame(self.window, bg="#FF4D42", height=3)
        top_line.pack(fill="x")
        body = ttk.Frame(self.window, padding=(26, 20, 26, 22))
        body.pack(fill="both", expand=True)

        ttk.Label(body, text="START TRACKING", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(body, text="Carrier Vessel Tracking", style="Display.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            body,
            text="Paste vessel names in any order. Repeated names are skipped automatically.",
        ).pack(anchor="w")

        source_card = ttk.Frame(body, style="Card.TFrame", padding=16)
        source_card.pack(fill="x", pady=(16, 12))
        ttk.Label(source_card, text="ACTIVE SOURCE", style="Step.TLabel").pack(anchor="w")
        ttk.Label(source_card, textvariable=self.source_var, style="Hint.TLabel", wraplength=800).pack(
            anchor="w", pady=(5, 0)
        )

        carrier_frame = ttk.Frame(body)
        carrier_frame.pack(fill="x", pady=(0, 14))
        button_columns = 4
        for column in range(button_columns):
            carrier_frame.columnconfigure(column, weight=1, uniform="carrier-buttons")
        for index, carrier in enumerate(CARRIERS):
            button = ttk.Button(
                carrier_frame,
                text=carrier,
                style="Primary.TButton",
                command=lambda name=carrier: self._open_vessel_input(name),
            )
            button.grid(
                row=index // button_columns,
                column=index % button_columns,
                sticky="ew",
                padx=(0 if index % button_columns == 0 else 5, 5),
                pady=(0, 6),
            )
            self.carrier_buttons.append(button)
        folder_row = (len(CARRIERS) + button_columns - 1) // button_columns
        ttk.Button(
            carrier_frame,
            text="Open Report Folder",
            style="Secondary.TButton",
            command=self._open_output_folder,
        ).grid(row=folder_row, column=0, columnspan=button_columns, sticky="ew", pady=(2, 0))

        activity = ttk.Frame(body, style="Card.TFrame", padding=18)
        activity.pack(fill="both", expand=True)
        header = ttk.Frame(activity, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="TRACKING PROGRESS", style="Step.TLabel").pack(side="left")
        ttk.Label(header, textvariable=self.progress_text_var, style="Hint.TLabel").pack(side="right")
        self.progress = ttk.Progressbar(activity, mode="determinate", maximum=1, value=0)
        self.progress.pack(fill="x", pady=(9, 9))
        ttk.Label(activity, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_box = tk.Text(
            activity,
            height=13,
            wrap="word",
            bg="#0F0F11",
            fg="#D8D8DC",
            relief="flat",
            insertbackground="#FF4D42",
            highlightthickness=1,
            highlightbackground="#303034",
            highlightcolor="#FF4D42",
            font=("Cascadia Mono", 9),
            padx=12,
            pady=10,
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True)
        self._log("Ready. Choose " + ", ".join(CARRIERS) + ".")

    def _refresh_source_label(self) -> None:
        try:
            source = latest_booking_source()
        except FileNotFoundError as exc:
            self.source_var.set(str(exc))
        else:
            changed = datetime.fromtimestamp(source.stat().st_mtime).strftime("%d %b %Y %H:%M")
            self.source_var.set(f"{source.name}  •  updated {changed}")

    def _open_vessel_input(self, carrier: str) -> None:
        if self.busy:
            return
        script = CARRIERS[carrier]
        if not script.exists():
            messagebox.showerror("Start Tracking", f"{carrier} bot was not found:\n{script}", parent=self.window)
            return

        dialog = tk.Toplevel(self.window)
        dialog.title(f"{carrier} vessels")
        dialog.geometry("680x560")
        dialog.minsize(560, 460)
        dialog.configure(bg="#0B0B0C")
        dialog.transient(self.window.winfo_toplevel())
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=22)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)
        ttk.Label(frame, text=f"{carrier} vessel list", style="Display.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(
            frame,
            text="Paste one name per line, or separate names with commas or semicolons.",
        ).grid(row=1, column=0, sticky="w", pady=(4, 10))
        text = tk.Text(
            frame,
            wrap="word",
            bg="#171719",
            fg="#F5F5F4",
            insertbackground="#FF4D42",
            highlightthickness=1,
            highlightbackground="#3A3A3E",
            highlightcolor="#FF4D42",
            font=("Segoe UI", 11),
            padx=12,
            pady=10,
        )
        text.grid(row=2, column=0, sticky="nsew")

        input_path = script.parent / "vessels.txt"
        if input_path.exists():
            previous = input_path.read_text(encoding="utf-8").strip()
            if previous:
                text.insert("1.0", previous)
                text.tag_add("sel", "1.0", "end-1c")
        text.focus_set()

        actions = ttk.Frame(frame)
        actions.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        ttk.Button(actions, text="Cancel", style="Secondary.TButton", command=dialog.destroy).pack(side="right")

        def start() -> None:
            names = parse_vessel_names(text.get("1.0", "end"))
            if not names:
                messagebox.showerror("Start Tracking", "Paste at least one vessel name.", parent=dialog)
                return
            dialog.destroy()
            self._start_tracking(carrier, names)

        ttk.Button(actions, text="Track", style="Primary.TButton", command=start, width=14).pack(
            side="right", padx=(0, 10)
        )
        ttk.Label(actions, text="Ctrl+Enter also starts tracking", style="Hint.TLabel").pack(side="left")
        dialog.bind("<Control-Return>", lambda _event: start())

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in self.carrier_buttons:
            button.configure(state=state)

    def _start_tracking(self, carrier: str, vessels: list[str]) -> None:
        self.stop_requested=False
        if not getattr(self,'can_start',lambda:True)():
            self.status_var.set('Another tracking operation is running. Let it finish or stop it first.')
            return
        try:
            source = latest_booking_source()
        except FileNotFoundError as exc:
            messagebox.showerror("Start Tracking", str(exc), parent=self.window)
            self._refresh_source_label()
            return

        vessels, skipped = filter_in_transit_vessels(source, vessels)
        if skipped:
            self._log(
                f"Skipped {len(skipped)} vessel(s) because ATA is recorded or no active ATA-blank leg exists: "
                + ", ".join(skipped)
            )
        if not vessels:
            self.status_var.set("No in-transit vessels to track")
            messagebox.showinfo(
                "Start Tracking",
                "None of the pasted vessels has a blank ATA in the latest ActiveTrack booking report.\n\n"
                "No carrier browser was opened.",
                parent=self.window,
            )
            return

        script = CARRIERS[carrier]
        input_path = script.parent / "vessels.txt"
        input_path.write_text("\n".join(vessels) + "\n", encoding="utf-8")
        self._set_busy(True)
        self.progress.configure(maximum=max(len(vessels), 1), value=0)
        self.progress_text_var.set(f"0 / {len(vessels)}")
        self.status_var.set(f"{carrier}: starting browser tracking…")
        self._log(f"{carrier}: {len(vessels)} unique vessel name(s).")
        self._log(f"Comparing with {source.name}.")

        def work() -> TrackingReportStats:
            python = Path(sys.executable)
            console_python = python.with_name("python.exe")
            if console_python.exists():
                python = console_python
            env = os.environ.copy()
            env["ACTIVTRACK_SOURCE_FILE"] = str(source)
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            process = subprocess.Popen(
                [str(python), "-u", str(script)],
                cwd=str(script.parent),
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
            assert process.stdout is not None
            self._active_process=process
            if self.stop_requested:self.stop_tracking()
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                self.window.after(0, lambda message=line: self._log(message))
                progress = re.search(r"\[(\d+)/(\d+)\]", line)
                if progress:
                    current, total = int(progress.group(1)), int(progress.group(2))
                    self.window.after(0, lambda c=current, t=total: self._update_progress(c, t))
            return_code = process.wait()
            self._active_process=None
            if self.stop_requested:raise RuntimeError('Tracking stopped by user. Completed carrier files are retained.')
            if return_code != 0:
                raise RuntimeError(f"{carrier} tracking bot stopped with exit code {return_code}.")

            results_root = script.parent / "results"
            candidates = [path for path in results_root.iterdir() if path.is_dir() and (path / "results.json").exists()]
            if not candidates:
                raise RuntimeError(f"{carrier} did not create a results folder.")
            result_dir = max(candidates, key=lambda path: path.stat().st_mtime)

            output_dir = CARRIER_REPORTS_DIR
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_carrier = re.sub(r"[^A-Za-z0-9]+", "_", carrier).strip("_")
            output_path = output_dir / f"Vessel_Tracking_{safe_carrier}_{stamp}.xlsx"
            self.window.after(0, lambda: self.status_var.set("Building the Excel comparison…"))
            return build_tracking_report(carrier, vessels, source, result_dir, output_path)

        future = self.executor.submit(work)

        def finished() -> None:
            try:
                stats = future.result()
            except Exception as exc:
                self._set_busy(False)
                self.status_var.set("Tracking needs attention")
                self.progress_text_var.set("Stopped")
                self._log(f"ERROR: {exc}")
                messagebox.showerror("Start Tracking", str(exc), parent=self.window)
                return
            self._set_busy(False)
            self.last_output = stats.output_path
            self.progress.configure(value=max(len(vessels), 1))
            self.progress_text_var.set(f"{len(vessels)} / {len(vessels)}")
            self.status_var.set(f"{carrier} tracking completed")
            self._log(
                f"Excel saved: {stats.rows} row(s), {stats.delayed_rows} delayed, "
                f"{stats.advanced_rows} advanced, {stats.no_data_rows} without carrier data."
            )
            messagebox.showinfo(
                "Start Tracking",
                f"{carrier} tracking completed.\n\n"
                f"Requested vessels: {stats.requested_vessels}\n"
                f"Comparison rows: {stats.rows}\n"
                f"Delayed: {stats.delayed_rows}\n"
                f"Advanced: {stats.advanced_rows}\n"
                f"No carrier data: {stats.no_data_rows}\n\n"
                f"Saved to:\n{stats.output_path}",
                parent=self.window,
            )
            try:
                os.startfile(stats.output_path)  # type: ignore[attr-defined]
            except OSError:
                pass

        future.add_done_callback(lambda _future: self.window.after(0, finished))

    def stop_tracking(self):
        self.stop_requested=True
        self.status_var.set('Stopping the current carrier…')
        process=self._active_process
        if process and process.poll() is None:
            from automated_tracking_window import AutomatedTrackingWindow
            import threading
            threading.Thread(target=AutomatedTrackingWindow._terminate_process_tree,args=(process,),daemon=True).start()

    def _update_progress(self, current: int, total: int) -> None:
        self.progress.configure(maximum=max(total, 1), value=current)
        self.progress_text_var.set(f"{current} / {total}")
        self.status_var.set(f"Tracking vessel {current} of {total}…")

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{stamp}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _open_output_folder(self) -> None:
        try:
            folder = self.last_output.parent if self.last_output else CARRIER_REPORTS_DIR
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Start Tracking", str(exc), parent=self.window)

    def _close(self) -> None:
        if self.busy:
            messagebox.showinfo(
                "Start Tracking",
                "Tracking is still running. Leave this window open until the Excel report is created.",
                parent=self.window,
            )
            return
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()
