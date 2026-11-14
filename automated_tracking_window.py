"""Progress window for the sequential one-button carrier tracking run."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk

from automated_tracking import (
    BOT_ORDER,
    AutomatedTrackingStats,
    build_automated_report,
    build_tracking_plan,
    canonical,
    merge_results,
    merge_trico_results,
    trico_48h_rows,
)
from tracking_config import CARRIERS, CARRIER_REPORTS_DIR, TRICO_SCRIPT
from tracking_report import latest_booking_source


class AutomatedTrackingWindow:
    def __init__(self, parent: tk.Misc, source_path: Path | None = None, fcl_controller=None, embedded=False) -> None:
        self.fcl_controller=fcl_controller
        self.parent = parent
        self.source_path = source_path if source_path and source_path.is_file() else latest_booking_source()
        self.plan_rows, self.groups, _source_data = build_tracking_plan(self.source_path)
        self.total_vessels = sum(len(values) for values in self.groups.values())
        self.trico_rows = trico_48h_rows(self.plan_rows)
        self.trico_vessels = list(dict.fromkeys(
            row["vessel"] for row in self.trico_rows if canonical(row.get("vessel"))
        ))
        self.total_steps = self.total_vessels + (1 if self.trico_rows else 0)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="automated-tracking")
        self.busy = True
        self.stop_requested = threading.Event()
        self._process_lock = threading.Lock()
        self._current_process: subprocess.Popen[str] | None = None

        self.embedded=embedded
        self.window = tk.Frame(parent,bg='#FFFFFF') if embedded else tk.Toplevel(parent)
        if embedded:self.window.pack(fill='both',expand=True)
        else:
            self.window.title("Automated Tracking")
            self.window.geometry("930x690")
            self.window.minsize(820, 600)
            self.window.transient(parent)
            self.window.protocol("WM_DELETE_WINDOW", self._close)

        self.status_var = tk.StringVar(value="Preparing the serial carrier queue…")
        self.progress_text_var = tk.StringVar(value=f"0 / {max(self.total_steps, 1)}")
        self._build_ui()
        self._show_plan()
        from desktop_ui import lighten_widgets
        lighten_widgets(self.window)
        self.window.after(150, self._start)

    def _build_ui(self) -> None:
        tk.Frame(self.window, bg="#7C3AED", height=4).pack(fill="x")
        body = ttk.Frame(self.window, padding=(26, 20, 26, 22))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="AUTOMATED MORNING RUN", style="Eyebrow.TLabel").pack(anchor="w")
        ttk.Label(body, text="Daily Vessel Tracking", style="Display.TLabel").pack(anchor="w", pady=(4, 3))
        ttk.Label(
            body,
            text="The source report is locked. Carrier websites run one at a time and merge into one action workbook.",
        ).pack(anchor="w")

        source_card = ttk.Frame(body, style="Card.TFrame", padding=15)
        source_card.pack(fill="x", pady=(15, 10))
        ttk.Label(source_card, text="LOCKED SOURCE", style="Step.TLabel").pack(anchor="w")
        ttk.Label(source_card, text=self.source_path.name, style="Hint.TLabel").pack(anchor="w", pady=(4, 0))

        self.plan_label = ttk.Label(body, text="", style="TLabel")
        self.plan_label.pack(anchor="w", pady=(1, 10))

        card = ttk.Frame(body, style="Card.TFrame", padding=18)
        card.pack(fill="both", expand=True)
        header = ttk.Frame(card, style="Card.TFrame")
        header.pack(fill="x")
        ttk.Label(header, text="SERIAL TRACKING PROGRESS", style="Step.TLabel").pack(side="left")
        self.stop_button = ttk.Button(
            header, text="STOP TRACKING", style="Danger.TButton", command=self._stop_tracking,
        )
        self.stop_button.pack(side="right")
        ttk.Label(header, textvariable=self.progress_text_var, style="Hint.TLabel").pack(side="right", padx=(0, 14))
        self.progress = ttk.Progressbar(card, mode="determinate", maximum=max(self.total_steps, 1), value=0)
        self.progress.pack(fill="x", pady=(9, 9))
        ttk.Label(card, textvariable=self.status_var, style="Card.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_box = tk.Text(
            card, height=16, wrap="word", bg="#0F0F11", fg="#D8D8DC", relief="flat",
            insertbackground="#7C3AED", highlightthickness=1, highlightbackground="#303034",
            highlightcolor="#7C3AED", font=("Cascadia Mono", 9), padx=12, pady=10, state="disabled",
        )
        self.log_box.pack(fill="both", expand=True)

    def _show_plan(self) -> None:
        parts = [f"{bot}: {len(self.groups[bot])}" for bot in BOT_ORDER if self.groups[bot]]
        manual = sum(1 for row in self.plan_rows if row["bot"] == "MANUAL")
        self.plan_label.configure(
            text=f"ATA-blank legs: {len(self.plan_rows)}  •  Routed vessel/site pairs: {self.total_vessels}  •  Manual legs: {manual}  •  Trico 48h: {len(self.trico_rows)} leg(s)\n"
                 + "  |  ".join(parts)
        )
        self._log(f"Locked source: {self.source_path}")
        queue = [bot for bot in BOT_ORDER if self.groups[bot]]
        if self.trico_rows:
            queue.append("Trico 48h final check")
        self._log("Queue: " + (" → ".join(queue) or "No supported carrier vessels"))

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", f"{stamp}  {message}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _thread_log(self, message: str) -> None:
        self.window.after(0, lambda text=message: self._log(text))

    def _progress(self, completed: int, carrier: str) -> None:
        self.progress.configure(value=completed)
        self.progress_text_var.set(f"{completed} / {max(self.total_steps, 1)}")
        self.status_var.set(f"{carrier}: step {completed} of {self.total_steps}")

    @staticmethod
    def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW,
                check=False,
            )
        else:
            process.terminate()

    def _stop_tracking(self) -> None:
        if not self.busy or self.stop_requested.is_set():
            return
        self.stop_requested.set()
        self.stop_button.configure(state="disabled")
        self.status_var.set("Stopping the current carrier bot…")
        self._log("STOP requested. Closing the active carrier bot and browser; remaining vessels will move to Manual Tracking.")
        with self._process_lock:
            process = self._current_process
        if process and process.poll() is None:
            threading.Thread(target=self._terminate_process_tree, args=(process,), daemon=True).start()

    @staticmethod
    def _synthetic_failure_dir(run_data: Path, bot: str, vessels: list[str], reason: str) -> Path:
        folder = run_data / (re.sub(r"[^A-Za-z0-9]+", "_", bot).strip("_") + "_FAILED")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "results.json").write_text(
            json.dumps([{"vessel": vessel, "status": reason, "rows": []} for vessel in vessels], indent=2),
            encoding="utf-8",
        )
        fields = ["vs_id", "leg", "vessel", "voyage", "pol", "pod", "at_etd", "carrier_etd", "at_eta", "carrier_eta", "source_url"]
        with (folder / "matched_legs.csv").open("w", newline="", encoding="utf-8-sig") as handle:
            csv.DictWriter(handle, fieldnames=fields).writeheader()
        return folder

    @staticmethod
    def _result_matches_source(result_dir: Path, source_path: Path) -> bool:
        """Reject a carrier result if it was compared with another report."""
        comparison_path = result_dir / "comparison.json"
        if not comparison_path.is_file():
            return False
        try:
            comparison = json.loads(comparison_path.read_text(encoding="utf-8-sig"))
            recorded = Path(str(comparison.get("activtrack_source") or "")).resolve()
            return recorded == source_path.resolve()
        except (OSError, ValueError, json.JSONDecodeError):
            return False

    def _start(self) -> None:
        if not self.plan_rows:
            self.busy = False
            self.stop_button.configure(state="disabled")
            self.status_var.set("No ATA-blank vessel legs were found")
            self._log("Nothing to track from the locked source.")
            return

        def work() -> AutomatedTrackingStats:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            CARRIER_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
            run_data = CARRIER_REPORTS_DIR / "Daily Run Data" / stamp
            run_data.mkdir(parents=True, exist_ok=True)
            result_dirs: dict[str, Path] = {}
            completed_offset = 0

            for bot in BOT_ORDER:
                if self.stop_requested.is_set():
                    break
                vessels = self.groups[bot]
                if not vessels:
                    continue
                script = CARRIERS[bot]
                self._thread_log(f"{bot}: starting {len(vessels)} vessel(s).")
                self.window.after(0, lambda name=bot: self.status_var.set(f"{name}: opening carrier website…"))
                env = os.environ.copy()
                env["ACTIVTRACK_SOURCE_FILE"] = str(self.source_path)
                env["TRACKING_VESSELS"] = "\n".join(vessels)
                env["TRACKING_HEADLESS"] = "1"
                flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
                process = subprocess.Popen(
                    [str(Path(sys.executable).with_name("python.exe")), "-u", str(script)],
                    cwd=str(script.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
                )
                with self._process_lock:
                    self._current_process = process
                if self.stop_requested.is_set():
                    self._terminate_process_tree(process)
                result_path: Path | None = None
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    self._thread_log(f"{bot}: {line}")
                    progress = re.search(r"\[(\d+)/(\d+)\]", line)
                    if progress:
                        current = int(progress.group(1))
                        self.window.after(0, lambda done=completed_offset + current, name=bot: self._progress(done, name))
                    if line.startswith("Finished. Results:"):
                        candidate = Path(line.split(":", 1)[1].strip())
                        if candidate.is_dir():
                            result_path = candidate
                return_code = process.wait()
                with self._process_lock:
                    if self._current_process is process:
                        self._current_process = None
                source_matches = bool(
                    result_path
                    and self._result_matches_source(result_path, self.source_path)
                )
                if return_code == 0 and result_path and (result_path / "results.json").exists() and source_matches:
                    archive_path = run_data / re.sub(r"[^A-Za-z0-9]+", "_", bot).strip("_")
                    shutil.copytree(result_path, archive_path, dirs_exist_ok=True)
                    result_dirs[bot] = archive_path
                    self._thread_log(f"{bot}: completed.")
                else:
                    if self.stop_requested.is_set():
                        reason = "STOPPED BY USER"
                    elif return_code == 0 and result_path and not source_matches:
                        reason = "BOT SOURCE MISMATCH"
                    else:
                        reason = f"BOT FAILED (exit {return_code})" if return_code else "BOT RESULTS NOT FOUND"
                    result_dirs[bot] = self._synthetic_failure_dir(run_data, bot, vessels, reason)
                    suffix = "ending the automated run" if self.stop_requested.is_set() else "continuing to the next carrier"
                    self._thread_log(f"{bot}: {reason}; {suffix}.")
                if self.stop_requested.is_set():
                    break
                completed_offset += len(vessels)
                self.window.after(0, lambda done=completed_offset, name=bot: self._progress(done, name))

            trico_result_dir: Path | None = None
            if self.trico_rows and not self.stop_requested.is_set():
                self._thread_log(
                    f"Trico 48h: checking {len(self.trico_rows)} Colombo arrival leg(s) / "
                    f"{len(self.trico_vessels)} vessel(s) from one berthing page read."
                )
                self.window.after(0, lambda: self.status_var.set("Trico 48h: opening berthing schedule…"))
                env = os.environ.copy()
                env["ACTIVTRACK_SOURCE_FILE"] = str(self.source_path)
                env["TRACKING_VESSELS"] = "\n".join(self.trico_vessels)
                env["TRACKING_HEADLESS"] = "1"
                flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == "nt" else 0
                process = subprocess.Popen(
                    [str(Path(sys.executable).with_name("python.exe")), "-u", str(TRICO_SCRIPT)],
                    cwd=str(TRICO_SCRIPT.parent), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", bufsize=1, creationflags=flags,
                )
                with self._process_lock:
                    self._current_process = process
                result_path: Path | None = None
                assert process.stdout is not None
                for raw_line in process.stdout:
                    line = raw_line.strip()
                    if not line:
                        continue
                    self._thread_log(f"Trico 48h: {line}")
                    if line.startswith("Finished. Results:"):
                        candidate = Path(line.split(":", 1)[1].strip())
                        if candidate.is_dir():
                            result_path = candidate
                return_code = process.wait()
                with self._process_lock:
                    if self._current_process is process:
                        self._current_process = None
                if return_code == 0 and result_path and (result_path / "results.json").exists():
                    trico_result_dir = run_data / "Trico_48H"
                    shutil.copytree(result_path, trico_result_dir, dirs_exist_ok=True)
                    self._thread_log("Trico 48h: completed.")
                else:
                    reason = "STOPPED BY USER" if self.stop_requested.is_set() else (
                        f"BOT FAILED (exit {return_code})" if return_code else "BOT RESULTS NOT FOUND"
                    )
                    trico_result_dir = self._synthetic_failure_dir(
                        run_data, "Trico 48H", self.trico_vessels, reason,
                    )
                    self._thread_log(f"Trico 48h: {reason}.")
                if not self.stop_requested.is_set():
                    completed_offset += 1
                    self.window.after(0, lambda done=completed_offset: self._progress(done, "Trico 48h"))

            if self.stop_requested.is_set():
                for bot in BOT_ORDER:
                    vessels = self.groups[bot]
                    if vessels and bot not in result_dirs:
                        result_dirs[bot] = self._synthetic_failure_dir(
                            run_data, bot, vessels, "NOT RUN - STOPPED BY USER",
                        )
                if self.trico_rows and trico_result_dir is None:
                    trico_result_dir = self._synthetic_failure_dir(
                        run_data, "Trico 48H", self.trico_vessels, "NOT RUN - STOPPED BY USER",
                    )

            merged = merge_results(self.plan_rows, result_dirs)
            merged = merge_trico_results(merged, trico_result_dir)
            output_path = CARRIER_REPORTS_DIR / f"Automated_Daily_Vessel_Tracking_{stamp}.xlsx"
            report_dirs = dict(result_dirs)
            if trico_result_dir:
                report_dirs["Trico 48h"] = trico_result_dir
            manifest = {
                "source": str(self.source_path),
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "groups": self.groups,
                "bot_results": {bot: str(path) for bot, path in result_dirs.items()},
                "trico_48h_vessels": self.trico_vessels,
                "trico_48h_legs": [f"{row['vs_id']} / leg {row['leg']}" for row in self.trico_rows],
                "trico_result": str(trico_result_dir) if trico_result_dir else None,
                "stopped_by_user": self.stop_requested.is_set(),
                "output": str(output_path),
            }
            (run_data / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            stats=build_automated_report(self.source_path, merged, output_path, report_dirs)
            from fcl_tracking import REPORTS,write_json,COLOMBO
            write_json(REPORTS/'FCL Tracking'/'vessel_dashboard.json',{'source':str(self.source_path),'checked_at':datetime.now(COLOMBO).isoformat(timespec='seconds'),'rows':merged,'output':str(output_path)})
            return stats

        future = self.executor.submit(work)

        def finished() -> None:
            try:
                stats = future.result()
            except Exception as exc:
                self.busy = False
                self.stop_button.configure(state="disabled")
                self.status_var.set("Automated tracking needs attention")
                self._log(f"ERROR: {exc}")
                messagebox.showerror("Automated Tracking", str(exc), parent=self.window)
                return
            self.busy = False
            self.stop_button.configure(state="disabled")
            stopped = self.stop_requested.is_set()
            if not stopped:
                self.progress.configure(value=max(self.total_steps, 1))
                self.progress_text_var.set(f"{self.total_steps} / {max(self.total_steps, 1)}")
                self.status_var.set("Automated tracking completed")
            else:
                self.status_var.set("Tracking stopped - partial workbook saved")
            self._log(f"Workbook saved: {stats.output_path}")
            title = "Tracking stopped" if stopped else "Morning tracking completed"
            messagebox.showinfo(
                "Automated Tracking",
                f"{title}.\n\nATA-blank legs: {stats.legs}\n"
                f"Routed vessel/site pairs: {stats.routed_vessels}\nMatched legs: {stats.matched_legs}\n"
                f"Verified coverage: {(stats.matched_legs / stats.legs * 100):.1f}%\n"
                f"Trico 48h matches: {stats.trico_matched} / {stats.trico_required}\n"
                f"Action rows: {stats.action_rows}\n"
                f"Priority watch rows: {stats.priority_watch_rows}\n"
                f"Console/co-load rows: {stats.console_watch_rows}\n"
                f"Manual/no data: {stats.manual_rows}\n\nSaved to:\n{stats.output_path}",
                parent=self.window,
            )
            try:
                os.startfile(stats.output_path)  # type: ignore[attr-defined]
            except OSError:
                pass

        future.add_done_callback(lambda _future: self.window.after(0, finished))

    def _close(self) -> None:
        if self.busy:
            message = (
                "Stopping is in progress. Leave this window open until the partial workbook is saved."
                if self.stop_requested.is_set()
                else "Carrier tracking is still running. Use STOP TRACKING to end it safely and save a partial workbook."
            )
            messagebox.showinfo(
                "Automated Tracking",
                message,
                parent=self.window,
            )
            return
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.window.destroy()
