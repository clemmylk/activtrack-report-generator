"""ActivTrack Vessel Report Generator desktop application."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from activtrack_browser import ActivTrackBrowser, BrowserCaptureError
from report_builder import ReportDataError, ReportStats, build_report
from all_vessels_report import AllVesselsStats, build_all_vessels_report
from tracking_window import TrackingWindow
from automated_tracking_window import AutomatedTrackingWindow
from long_term_tracking import LongTermWindow
from fcl_controller import FCLController
from operations_dashboard import OperationsDashboard
from fcl_tracking import enrich_source
from arrival_alerts import ArrivalAlerts


APP_NAME = "ActivTrack Vessel Report Generator v1.7.0"


class ReportApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("1100x820")
        self.root.minsize(1000, 760)
        self.root.configure(bg="#0B0B0C")
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="activtrack-report")
        self.browser = ActivTrackBrowser(log=self._thread_log)
        self.busy = False
        self.last_output: Path | None = None
        self.tracking_window: TrackingWindow | None = None
        self.automated_tracking_window: AutomatedTrackingWindow | None = None
        self.long_term_window = None
        self.morning_source_path: Path | None = None
        self.dashboard = None
        self.fcl_controller = FCLController(lambda:self.morning_source_path,lambda:bool((self.automated_tracking_window and self.automated_tracking_window.busy) or (self.long_term_window and self.long_term_window.busy) or (self.tracking_window and self.tracking_window.busy)))

        default_output = Path.home() / "Documents" / "ActivTrack Reports"
        self.output_var = tk.StringVar(value=str(default_output))
        self.as_of_var = tk.StringVar(value=date.today().isoformat())
        self.save_json_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        self._configure_style()
        self._build_ui()
        self.arrival_alerts = ArrivalAlerts(self.root, self._log)
        self.root.after(30000,self._scheduled_fcl)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self) -> None:
        from desktop_ui import configure_style
        configure_style()

    def _build_ui(self) -> None:
        from desktop_ui import DesktopUI
        self.ui = DesktopUI(self)

    def _toggle_tools_menu(self) -> None:
        panel = getattr(self, "tools_menu", None)
        if panel is not None and panel.winfo_exists():
            panel.destroy()
            return
        panel = self.tools_menu = tk.Frame(self.root, bg="#111D32", highlightthickness=1, highlightbackground="#334155")
        panel.place(x=0, y=0, width=285, relheight=1)
        panel.lift()
        tk.Label(panel, text="ACTIVTRACK / TOOLS", bg="#111D32", fg="#FFFFFF", font=("Segoe UI",12,"bold")).pack(anchor="w",padx=18,pady=(24,12))
        def action(callback):
            panel.destroy()
            callback()
        for label, callback in (("Notice Headers & Remarks", self._open_notice_tools),
                                ("Live Dashboard", self._open_dashboard),
                                ("Open Reports Folder", self._open_output_folder)):
            ttk.Button(panel,text=label,style="Secondary.TButton",command=lambda c=callback:action(c)).pack(fill="x",padx=14,pady=6)
        tk.Label(panel,text="Notice tools copy text only.\nNo notifications are sent.",bg="#111D32",fg="#B9C8DD",font=("Segoe UI",9),justify="left").pack(anchor="w",padx=18,pady=18)
        ttk.Button(panel,text="Close menu",command=panel.destroy).pack(fill="x",padx=14,pady=6)

    def _open_notice_tools(self) -> None:
        from notice_tools import NoticeTools
        tool = getattr(self,"notice_tools",None)
        if tool is not None and tool.window.winfo_exists():
            tool.window.lift()
            return
        self.notice_tools = NoticeTools(self.root)
        self._log("Notice Headers & Remarks opened. Clipboard only; nothing sent.")

    def _step_card(self, parent: ttk.Frame, column: int, title: str, hint: str) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=18)
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 6, 0 if column == 2 else 6))
        ttk.Label(card, text=title, style="Step.TLabel").pack(anchor="w")
        ttk.Label(card, text=hint, style="Hint.TLabel", wraplength=230, justify="left").pack(anchor="w", pady=(7, 0))

    def _browse_output(self) -> None:
        selected = filedialog.askdirectory(initialdir=self.output_var.get() or str(Path.home()))
        if selected:
            self.output_var.set(selected)

    def _open_long_term(self) -> None:
        if hasattr(self,'ui'):
            self.ui.tracking_mode('longterm')
            return
        if self.long_term_window and self.long_term_window.window.winfo_exists():
            self.long_term_window.window.lift()
            return
        self.long_term_window = LongTermWindow(self.root, self.output_var.get)
        self._log("Long Term Auto opened.")

    def _open_dashboard(self):
        if hasattr(self,'ui'):
            self.ui.show('dashboard')
            return
        if self.dashboard and not self.dashboard.closed:
            self.dashboard.window.lift();return
        self.dashboard=OperationsDashboard(self.root,self.fcl_controller)

    def _scheduled_fcl(self):
        if not self.fcl_controller.closed:
            self.fcl_controller.scheduled_tick()
            self.root.after(30000,self._scheduled_fcl)

    def _validate_settings(self) -> tuple[date, Path]:
        try:
            as_of = datetime.strptime(self.as_of_var.get().strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("As-of date must use YYYY-MM-DD format.") from exc
        output_text = self.output_var.get().strip()
        if not output_text:
            raise ValueError("Choose an output folder.")
        output = Path(output_text).expanduser()
        output.mkdir(parents=True, exist_ok=True)
        return as_of, output

    def _set_busy(self, busy: bool, status: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for button in (self.open_button, self.generate_button, self.import_button, self.all_vessels_button, self.tracking_button, self.automated_tracking_button):
            button.configure(state=state)
        if status:
            self.status_var.set(status)
        if busy:
            self.progress.configure(mode='indeterminate')
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(mode='determinate',value=0)

    def _thread_log(self, message: str) -> None:
        self.root.after(0, lambda: self._log(message))

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{stamp}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")
        if hasattr(self,'ui'):
            self.ui.refresh_state()

    def _run_background(self, work, success_status: str, success_callback=None) -> None:
        if self.busy:
            return
        self._set_busy(True, "Working…")
        future = self.executor.submit(work)

        def finished(done_future) -> None:
            try:
                result = done_future.result()
            except (BrowserCaptureError, ReportDataError, ValueError, OSError, json.JSONDecodeError) as exc:
                self._set_busy(False, "Action needs attention")
                self._log(str(exc))
                messagebox.showerror(APP_NAME, str(exc))
            except Exception as exc:
                self._set_busy(False, "Unexpected error")
                self._log(f"Unexpected error: {exc}")
                messagebox.showerror(APP_NAME, f"Unexpected error:\n{exc}")
            else:
                self._set_busy(False, success_status)
                if success_callback:
                    success_callback(result)

        future.add_done_callback(lambda f: self.root.after(0, lambda: finished(f)))

    def _open_activtrack(self) -> None:
        self._log("Opening or focusing ActivTrack…")

        def work() -> None:
            self.browser.open()

        def success(_result) -> None:
            self._log("ActivTrack is ready. Apply the booking filter, then generate the report.")

        self._run_background(work, "ActivTrack browser is open", success)

    def _output_name(self, output_dir: Path, as_of: date) -> Path:
        stamp = datetime.now().strftime("%Y%m%d_%H%M")
        return output_dir / f"ActivTrack_Vessel_Booking_Report_{as_of.isoformat()}_{stamp}.xlsx"

    def _save_source_json(self, source: dict, report_path: Path) -> Path:
        enrich_source(source)
        json_path = report_path.with_name(report_path.stem + "_Source.json")
        json_path.write_text(json.dumps(source, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        from container_history import refresh_history,annotate,note_saved_capture
        recording_history=refresh_history()
        annotate(source,recording_history)
        enrich_source(source)
        json_path.write_text(json.dumps(source, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        note_saved_capture(json_path,recording_history)
        return json_path

    def _generate_live(self) -> None:
        try:
            as_of, output_dir = self._validate_settings()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        report_path = self._output_name(output_dir, as_of)
        save_json = bool(self.save_json_var.get())
        self._log("Reading every filtered booking with its exact linked vessel schedule…")

        def work() -> ReportStats:
            if not self.browser.is_open and not self.browser.attach_existing():
                raise BrowserCaptureError("Click Open ActivTrack first, then filter the bookings in that Edge page.")
            source = self.browser.capture(refresh=False)
            self._thread_log(f"Validated {len(source['bookings'])} bookings and {len(source['schedules'])} schedules.")
            # The automated morning run must always use this session's first
            # capture.  Keep that frozen source even when the optional audit
            # checkbox is off, so it can never fall back to yesterday's file.
            if save_json or self.morning_source_path is None:
                json_path = self._save_source_json(source, report_path)
                if self.morning_source_path is None:
                    self.morning_source_path = json_path
                    self._thread_log(f"Automated tracking source locked: {json_path.name}")
                elif save_json:
                    self._thread_log(f"Audit source saved: {json_path.name}")
            return build_report(source, report_path, as_of)

        self._run_background(work, "Excel report completed", self._report_success)

    def _import_json(self) -> None:
        input_path = filedialog.askopenfilename(title="Select ActivTrack source JSON", filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not input_path:
            return
        try:
            as_of, output_dir = self._validate_settings()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        report_path = self._output_name(output_dir, as_of)
        self._log(f"Importing saved source: {Path(input_path).name}")

        def work() -> ReportStats:
            source = json.loads(Path(input_path).read_text(encoding="utf-8"))
            if self.morning_source_path is None:
                self.morning_source_path = Path(input_path)
                self._thread_log(f"Automated tracking source locked: {Path(input_path).name}")
            return build_report(source, report_path, as_of)

        self._run_background(work, "Excel report completed", self._report_success)

    def _generate_all_vessels(self) -> None:
        try:
            as_of, output_dir = self._validate_settings()
        except ValueError as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_path = output_dir / f"ActivTrack_All_Vessels_{as_of.isoformat()}_{stamp}.xlsx"
        save_json = bool(self.save_json_var.get())
        self._log("ALL VESSELS: reading the exact current booking page and filter…")

        def work() -> AllVesselsStats:
            if not self.browser.is_open and not self.browser.attach_existing():
                raise BrowserCaptureError("Click Open ActivTrack first, then filter the bookings in that Edge page.")
            source = self.browser.capture_all_vessels()
            if save_json:
                self._save_source_json(source, report_path)
            return build_all_vessels_report(source, report_path, as_of)

        def success(stats: AllVesselsStats) -> None:
            self.last_output = stats.output_path
            self._log(
                f"ALL VESSELS saved: {stats.vessels} vessels, {stats.containers} unique containers, "
                f"{stats.bookings} reportable bookings, {stats.missing_schedule_bookings} missing schedules."
            )
            messagebox.showinfo(APP_NAME,
                f"ALL VESSELS report created.\n\nVessels: {stats.vessels}\n"
                f"Containers: {stats.containers}\nBookings: {stats.bookings}\n"
                f"In-transit vessel legs: {stats.in_transit_legs}\n"
                f"Bookings missing vessel schedules: {stats.missing_schedule_bookings}"
                f"{' (see Missing Schedules sheet)' if stats.missing_schedule_bookings else ''}\n\n"
                f"Saved to:\n{stats.output_path}")
            try:
                os.startfile(stats.output_path)
            except OSError:
                pass

        self._run_background(work, "ALL VESSELS report completed", success)

    def _open_tracking(self) -> None:
        if hasattr(self,'ui'):
            self.ui.tracking_mode('manual')
            return
        if self.tracking_window and self.tracking_window.window.winfo_exists():
            self.tracking_window.window.deiconify()
            self.tracking_window.window.lift()
            self.tracking_window.window.focus_force()
            return
        self.tracking_window = TrackingWindow(self.root, lambda: self.output_var.get())
        self._log("Carrier tracking window opened.")

    def _open_automated_tracking(self) -> None:
        if hasattr(self,'ui'):
            self.ui.tracking_mode('auto')
            if self.ui.other_tracking_busy('auto'):
                messagebox.showinfo(APP_NAME,'Another tracking operation is running. Let it finish or stop it before starting Automated Tracking.',parent=self.root)
                return
        if self.automated_tracking_window and self.automated_tracking_window.window.winfo_exists():
            if getattr(self.automated_tracking_window,'embedded',False):
                self.automated_tracking_window.window.pack(fill='both',expand=True)
                return
            self.automated_tracking_window.window.deiconify()
            self.automated_tracking_window.window.lift()
            self.automated_tracking_window.window.focus_force()
            return
        if self.morning_source_path is None or not self.morning_source_path.is_file():
            messagebox.showerror(
                APP_NAME,
                "Generate the booking report first.\n\n"
                "Automated Tracking uses the first source captured in this app session so it cannot accidentally track an older report.",
            )
            return
        try:
            if self.fcl_controller.busy:
                messagebox.showinfo(APP_NAME,"FCL container checks are running. Wait for them to finish or stop them in the dashboard.");return
            if hasattr(self,'ui'):
                self.ui.auto_intro.pack_forget()
                self.automated_tracking_window = AutomatedTrackingWindow(self.ui.runner_host, self.morning_source_path, self.fcl_controller, embedded=True)
                self.ui.runners['auto']=self.automated_tracking_window
            else:
                self.automated_tracking_window = AutomatedTrackingWindow(self.root, self.morning_source_path, self.fcl_controller)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
            messagebox.showerror(APP_NAME, f"Generate the booking report first.\n\n{exc}")
            return
        self._log("Purple Automated Tracking started with a locked source report.")

    def _report_success(self, stats: ReportStats) -> None:
        self.last_output = stats.output_path
        self._log(
            f"Done: {stats.bookings} bookings, {stats.schedules} schedules, {stats.vessels} vessels, "
            f"{stats.atd_missing_legs} ATD missing legs, {stats.ata_missing_legs} ATA missing legs, "
            f"{stats.arriving_48h_legs} arrivals exactly 48 hours ahead, "
            f"{stats.missing_schedule_bookings} bookings missing schedules."
        )
        messagebox.showinfo(
            APP_NAME,
            "Excel report created successfully.\n\n"
            f"Bookings: {stats.bookings}\nSchedules: {stats.schedules}\nVessels: {stats.vessels}\n"
            f"ATD missing legs: {stats.atd_missing_legs}\nATA missing legs: {stats.ata_missing_legs}\n"
            f"Arrivals exactly 48 hours ahead: {stats.arriving_48h_legs}\n\n"
            f"Bookings missing vessel schedules: {stats.missing_schedule_bookings}"
            f"{' (see Missing Schedules sheet)' if stats.missing_schedule_bookings else ''}\n\n"
            f"Saved to:\n{stats.output_path}",
        )
        try:
            os.startfile(stats.output_path)  # type: ignore[attr-defined]
        except OSError:
            pass

    def _open_output_folder(self) -> None:
        try:
            folder = self.last_output.parent if self.last_output else Path(self.output_var.get()).expanduser()
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(folder)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror(APP_NAME, str(exc))

    def _on_close(self) -> None:
        if self.busy or any(getattr(r,'busy',False) for r in (self.tracking_window,self.automated_tracking_window,self.long_term_window) if r):
            messagebox.showinfo(APP_NAME,'An operation is still running. Stop tracking and wait for its results to save, or let the capture finish, before closing.',parent=self.root)
            return
        if self.dashboard and not self.dashboard.closed:
            self.dashboard.close()
        for runner in (self.tracking_window,self.automated_tracking_window):
            if runner:runner.executor.shutdown(wait=False,cancel_futures=True)
        if self.long_term_window:self.long_term_window.executor.shutdown(wait=False,cancel_futures=True)
        self.arrival_alerts.close()
        self.fcl_controller.close()
        if self.long_term_window and self.long_term_window.busy:
            self.long_term_window.cancel()
            self.long_term_window.executor.shutdown(wait=False)
        try:
            if self.browser.is_open:
                self.browser.close()
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
            self.root.destroy()


def main() -> None:
    # Prevent Windows from stretching a low-resolution bitmap on scaled displays.
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    ReportApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
