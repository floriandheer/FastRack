#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_RealTime_ResolumeSync.py
Description: Push/pull the Resolume Avenue user folder between local PC and NAS.
Author: Florian Dheer
Version: 1.0.0

Synchronizes:
  Local: C:\\Users\\<user>\\Documents\\Resolume Avenue
  NAS:   I:\\Realtime\\_LIBRARY\\Resolume Avenue   (mapped via Synology Drive)

Why not real bidirectional sync: Synology Drive already manages the NAS-side
mapping, and Resolume rewrites composition/clip files frequently which causes
conflicts with continuous two-way sync tools. This module exposes explicit
Push (local -> NAS) and Pull (NAS -> local) operations using robocopy /MIR
so the user picks which side wins on each run.

Safety:
- Aborts when Avenue.exe or Arena.exe is running.
- Default dry-run (/L) on first use of each direction so the user can preview
  before any destructive mirror operation.
"""

import os
import sys
import json
import subprocess
import threading
import datetime
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Callable

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_appdata import get_appdata_path

logger = get_logger("resolume_sync")


# ====================================
# CONSTANTS
# ====================================

APP_NAME = "Resolume Sync"
APP_VERSION = "1.0.0"

DEFAULT_LOCAL_PATH = str(Path.home() / "Documents" / "Resolume Avenue")
DEFAULT_NAS_PATH = r"I:\Realtime\_LIBRARY\Resolume Avenue"

RESOLUME_PROCESS_NAMES = ("Avenue.exe", "Arena.exe")

# robocopy excludes
EXCLUDE_DIRS = ["Cache", "Thumbs", "Crash Reports"]
EXCLUDE_FILES = ["*.log", "log_*.txt", "crash_*.txt", "Thumbs.db", "desktop.ini"]

# robocopy success: exit codes 0-7 are success; 8+ are failure
ROBOCOPY_SUCCESS_MAX = 7


# ====================================
# CONFIG
# ====================================

class ResolumeSyncConfig:
    """Persisted user configuration."""

    def __init__(self):
        app_data = get_appdata_path()
        app_data.mkdir(parents=True, exist_ok=True)
        self.config_file = app_data / "resolume_sync_config.json"
        self.config = self._load()

    def _defaults(self) -> Dict:
        return {
            "local_path": DEFAULT_LOCAL_PATH,
            "nas_path": DEFAULT_NAS_PATH,
            "last_push_at": "",
            "last_pull_at": "",
            "dry_run_default": True,
        }

    def _load(self) -> Dict:
        default = self._defaults()
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                default.update({k: v for k, v in loaded.items() if k in default})
            except Exception as e:
                logger.error(f"Error loading config: {e}")
        else:
            self._save(default)
        return default

    def _save(self, data: Optional[Dict] = None):
        try:
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(data if data is not None else self.config, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def save(self):
        self._save()

    def get(self, key: str):
        return self.config.get(key, self._defaults().get(key))

    def set(self, key: str, value):
        self.config[key] = value
        self._save()


# ====================================
# SYNC ENGINE
# ====================================

class ResolumeSyncEngine:
    """robocopy-based mirror operations + Resolume process detection."""

    @staticmethod
    def running_resolume_processes() -> List[str]:
        """Return the subset of RESOLUME_PROCESS_NAMES currently running."""
        if sys.platform != "win32":
            return []
        try:
            result = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except Exception as e:
            logger.warning(f"tasklist failed: {e}")
            return []

        lower_names = {n.lower() for n in RESOLUME_PROCESS_NAMES}
        found = []
        for line in result.stdout.splitlines():
            # CSV: "Image","PID","Session","Session#","MemUsage"
            if not line.startswith('"'):
                continue
            image = line.split('","', 1)[0].lstrip('"').lower()
            if image in lower_names and image not in (f.lower() for f in found):
                # preserve canonical casing for display
                for canonical in RESOLUME_PROCESS_NAMES:
                    if canonical.lower() == image:
                        found.append(canonical)
                        break
        return found

    @staticmethod
    def build_robocopy_args(src: str, dst: str, dry_run: bool) -> List[str]:
        # /MIR = mirror, /R:2 /W:5 = 2 retries, 5s wait, /MT:8 = 8 threads
        # /NP = no per-file progress (cleaner log), /NDL = no dir list,
        # /TEE not used here — we capture stdout directly.
        args = [
            "robocopy", src, dst, "/MIR",
            "/R:2", "/W:5", "/MT:8", "/NP", "/NDL",
        ]
        for d in EXCLUDE_DIRS:
            args.extend(["/XD", d])
        for f in EXCLUDE_FILES:
            args.extend(["/XF", f])
        if dry_run:
            args.append("/L")
        return args

    @staticmethod
    def run_robocopy(
        src: str,
        dst: str,
        dry_run: bool,
        on_output: Callable[[str], None],
    ) -> Tuple[bool, int, str]:
        """Run robocopy, stream stdout via on_output. Returns (success, exit_code, summary)."""
        if not os.path.isdir(src):
            return False, -1, f"Source not found: {src}"

        # Ensure destination parent exists (robocopy creates the leaf itself)
        try:
            os.makedirs(dst, exist_ok=True)
        except Exception as e:
            return False, -1, f"Failed to prepare destination: {e}"

        args = ResolumeSyncEngine.build_robocopy_args(src, dst, dry_run)
        on_output(f"$ {' '.join(args)}")
        on_output("")

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
        except FileNotFoundError:
            return False, -1, "robocopy not found (not running on Windows?)"
        except Exception as e:
            return False, -1, f"Failed to launch robocopy: {e}"

        last_lines: List[str] = []
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            on_output(line)
            last_lines.append(line)
            if len(last_lines) > 30:
                last_lines.pop(0)

        exit_code = proc.wait()
        success = exit_code <= ROBOCOPY_SUCCESS_MAX
        summary = ResolumeSyncEngine._extract_summary(last_lines, exit_code, dry_run)
        return success, exit_code, summary

    @staticmethod
    def _extract_summary(last_lines: List[str], exit_code: int, dry_run: bool) -> str:
        # Robocopy prints a stats block near the end with lines like:
        #   "    Files :  ...  Copied : ...  Skipped : ..."
        wanted_keys = ("Dirs :", "Files :", "Bytes :")
        captured = [ln for ln in last_lines if any(k in ln for k in wanted_keys)]
        mode = "[DRY-RUN] " if dry_run else ""
        if not captured:
            return f"{mode}robocopy exited with code {exit_code}"
        return f"{mode}exit code {exit_code}\n" + "\n".join(captured)


# ====================================
# UI COLORS
# ====================================

COLORS = {
    "bg": "#1a1a2e",
    "bg_card": "#16213e",
    "fg": "#e0e0e0",
    "fg_dim": "#8888aa",
    "accent": "#0f3460",
    "highlight": "#533483",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "info": "#3498db",
    "border": "#2a2a4a",
    "push": "#27ae60",
    "pull": "#2980b9",
}


# ====================================
# SETTINGS DIALOG
# ====================================

class ResolumeSettingsDialog:
    """Modal to edit local + NAS paths."""

    def __init__(self, parent: tk.Tk, config: ResolumeSyncConfig):
        self.parent = parent
        self.config = config
        self.changed = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Resolume Sync Settings")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.dialog.configure(bg=COLORS["bg"])

        self._build_ui()

        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        pad = {"padx": 15, "pady": 6}

        self.local_var = tk.StringVar(value=self.config.get("local_path"))
        self.nas_var = tk.StringVar(value=self.config.get("nas_path"))
        self.dry_var = tk.BooleanVar(value=bool(self.config.get("dry_run_default")))

        self._path_row("Local folder", self.local_var, **pad)
        self._path_row("NAS folder (Synology Drive mapped)", self.nas_var, **pad)

        opts_frame = tk.LabelFrame(self.dialog, text="Defaults", bg=COLORS["bg"],
                                   fg=COLORS["fg_dim"], font=("Arial", 9))
        opts_frame.pack(fill=tk.X, **pad)
        tk.Checkbutton(
            opts_frame, text="Dry-run by default (preview only, no changes)",
            variable=self.dry_var, bg=COLORS["bg"], fg=COLORS["fg"],
            selectcolor=COLORS["accent"], activebackground=COLORS["bg"],
            activeforeground=COLORS["fg"], font=("Arial", 10),
        ).pack(anchor=tk.W, padx=10, pady=8)

        btn_frame = tk.Frame(self.dialog, bg=COLORS["bg"])
        btn_frame.pack(fill=tk.X, padx=15, pady=(5, 15))
        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy,
                  bg=COLORS["accent"], fg=COLORS["fg"], relief=tk.FLAT,
                  padx=15).pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="Save", command=self._on_save,
                  bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                  relief=tk.FLAT, padx=15).pack(side=tk.RIGHT)

    def _path_row(self, label: str, var: tk.StringVar, **pad):
        frame = tk.LabelFrame(self.dialog, text=label, bg=COLORS["bg"],
                              fg=COLORS["fg_dim"], font=("Arial", 9))
        frame.pack(fill=tk.X, **pad)
        inner = tk.Frame(frame, bg=COLORS["bg"])
        inner.pack(fill=tk.X, padx=10, pady=8)
        entry = tk.Entry(inner, textvariable=var, bg=COLORS["bg_card"],
                         fg=COLORS["fg"], insertbackground=COLORS["fg"],
                         font=("Consolas", 9), relief=tk.FLAT, width=55)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        def browse():
            d = filedialog.askdirectory(title=f"Select {label}", parent=self.dialog)
            if d:
                var.set(d.replace("/", "\\"))

        tk.Button(inner, text="Browse", command=browse,
                  bg=COLORS["accent"], fg=COLORS["fg"], relief=tk.FLAT).pack(side=tk.LEFT)

    def _on_save(self):
        local = self.local_var.get().strip()
        nas = self.nas_var.get().strip()
        if not local or not nas:
            messagebox.showerror("Error", "Both paths must be set.", parent=self.dialog)
            return
        self.config.set("local_path", local)
        self.config.set("nas_path", nas)
        self.config.set("dry_run_default", bool(self.dry_var.get()))
        self.changed = True
        self.dialog.destroy()


# ====================================
# MAIN UI
# ====================================

class ResolumeSyncUI:

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("820x620")
        self.root.minsize(720, 540)
        self.root.configure(bg=COLORS["bg"])

        self.config = ResolumeSyncConfig()
        self.operation_running = False
        self.dry_run_var = tk.BooleanVar(value=bool(self.config.get("dry_run_default")))

        self._build_ui()
        self._refresh_status()

    # ---- build ----

    def _build_ui(self):
        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=55)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text=APP_NAME, font=("Arial", 15, "bold"),
                 fg="white", bg="#2c3e50").place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Paths display
        paths_frame = tk.LabelFrame(self.root, text="Folders", bg=COLORS["bg"],
                                    fg=COLORS["fg_dim"], font=("Arial", 9))
        paths_frame.pack(fill=tk.X, padx=12, pady=(10, 5))

        self.local_label = self._path_display(paths_frame, "Local:", "")
        self.nas_label = self._path_display(paths_frame, "NAS:  ", "")

        # Controls row
        controls = tk.Frame(self.root, bg=COLORS["bg"])
        controls.pack(fill=tk.X, padx=12, pady=(0, 5))

        tk.Checkbutton(
            controls, text="Dry-run (preview only)", variable=self.dry_run_var,
            bg=COLORS["bg"], fg=COLORS["fg"], selectcolor=COLORS["accent"],
            activebackground=COLORS["bg"], activeforeground=COLORS["fg"],
            font=("Arial", 10),
        ).pack(side=tk.LEFT)

        tk.Button(controls, text="Settings", command=self._open_settings,
                  bg=COLORS["highlight"], fg=COLORS["fg"], relief=tk.FLAT,
                  padx=10).pack(side=tk.RIGHT)

        tk.Button(controls, text="Check Resolume", command=self._check_resolume,
                  bg=COLORS["accent"], fg=COLORS["fg"], relief=tk.FLAT,
                  padx=10).pack(side=tk.RIGHT, padx=(0, 6))

        # Action buttons
        actions = tk.Frame(self.root, bg=COLORS["bg"])
        actions.pack(fill=tk.X, padx=12, pady=(4, 6))

        self.push_btn = tk.Button(
            actions, text="⬆  Push  Local → NAS", command=self._on_push,
            bg=COLORS["push"], fg="white", font=("Arial", 11, "bold"),
            relief=tk.FLAT, padx=18, pady=10,
        )
        self.push_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 6))

        self.pull_btn = tk.Button(
            actions, text="⬇  Pull  NAS → Local", command=self._on_pull,
            bg=COLORS["pull"], fg="white", font=("Arial", 11, "bold"),
            relief=tk.FLAT, padx=18, pady=10,
        )
        self.pull_btn.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(6, 0))

        # Last-sync display
        last_frame = tk.Frame(self.root, bg=COLORS["bg"])
        last_frame.pack(fill=tk.X, padx=12, pady=(0, 5))
        self.last_push_var = tk.StringVar()
        self.last_pull_var = tk.StringVar()
        tk.Label(last_frame, textvariable=self.last_push_var, bg=COLORS["bg"],
                 fg=COLORS["fg_dim"], font=("Arial", 9)).pack(side=tk.LEFT, padx=(0, 20))
        tk.Label(last_frame, textvariable=self.last_pull_var, bg=COLORS["bg"],
                 fg=COLORS["fg_dim"], font=("Arial", 9)).pack(side=tk.LEFT)

        # Log pane
        log_frame = tk.LabelFrame(self.root, text="Output", bg=COLORS["bg"],
                                  fg=COLORS["fg_dim"], font=("Arial", 9))
        log_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 5))

        self.log = scrolledtext.ScrolledText(
            log_frame, wrap=tk.WORD, bg="#0d1117", fg=COLORS["fg"],
            font=("Consolas", 9), insertbackground=COLORS["fg"],
            relief=tk.FLAT, state=tk.DISABLED, height=14,
        )
        self.log.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log.tag_configure("error", foreground=COLORS["error"])
        self.log.tag_configure("success", foreground=COLORS["success"])
        self.log.tag_configure("info", foreground=COLORS["info"])
        self.log.tag_configure("warning", foreground=COLORS["warning"])
        self.log.tag_configure("muted", foreground=COLORS["fg_dim"])

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(self.root, textvariable=self.status_var, bg=COLORS["border"],
                 fg=COLORS["fg_dim"], anchor=tk.W, padx=10,
                 font=("Arial", 9)).pack(fill=tk.X, side=tk.BOTTOM)

    def _path_display(self, parent: tk.Frame, label: str, value: str) -> tk.Label:
        row = tk.Frame(parent, bg=COLORS["bg"])
        row.pack(fill=tk.X, padx=10, pady=3)
        tk.Label(row, text=label, bg=COLORS["bg"], fg=COLORS["fg_dim"],
                 font=("Consolas", 9), width=6, anchor=tk.W).pack(side=tk.LEFT)
        lbl = tk.Label(row, text=value, bg=COLORS["bg_card"], fg=COLORS["fg"],
                       font=("Consolas", 9), anchor=tk.W, padx=6, pady=2)
        lbl.pack(side=tk.LEFT, fill=tk.X, expand=True)
        return lbl

    # ---- helpers ----

    def _append(self, msg: str, tag: Optional[str] = None):
        self.log.config(state=tk.NORMAL)
        if tag:
            self.log.insert(tk.END, msg + "\n", tag)
        else:
            self.log.insert(tk.END, msg + "\n")
        self.log.see(tk.END)
        self.log.config(state=tk.DISABLED)

    def _clear_log(self):
        self.log.config(state=tk.NORMAL)
        self.log.delete(1.0, tk.END)
        self.log.config(state=tk.DISABLED)

    def _set_buttons(self, state: str):
        self.push_btn.config(state=state)
        self.pull_btn.config(state=state)

    def _refresh_status(self):
        self.local_label.config(text=self.config.get("local_path"))
        self.nas_label.config(text=self.config.get("nas_path"))
        self.last_push_var.set(f"Last push:  {self.config.get('last_push_at') or '—'}")
        self.last_pull_var.set(f"Last pull:  {self.config.get('last_pull_at') or '—'}")

    # ---- handlers ----

    def _open_settings(self):
        dlg = ResolumeSettingsDialog(self.root, self.config)
        self.root.wait_window(dlg.dialog)
        if dlg.changed:
            self.dry_run_var.set(bool(self.config.get("dry_run_default")))
            self._refresh_status()

    def _check_resolume(self):
        running = ResolumeSyncEngine.running_resolume_processes()
        if running:
            self._append(f"Resolume is running: {', '.join(running)}", "warning")
            self.status_var.set("Resolume is running — sync will be blocked")
        else:
            self._append("Resolume is not running. Safe to sync.", "success")
            self.status_var.set("Resolume not running")

    def _on_push(self):
        self._run_sync(
            direction="push",
            src=self.config.get("local_path"),
            dst=self.config.get("nas_path"),
            label="Local → NAS",
        )

    def _on_pull(self):
        self._run_sync(
            direction="pull",
            src=self.config.get("nas_path"),
            dst=self.config.get("local_path"),
            label="NAS → Local",
        )

    def _run_sync(self, direction: str, src: str, dst: str, label: str):
        if self.operation_running:
            return

        # Pre-flight: Resolume process check
        running = ResolumeSyncEngine.running_resolume_processes()
        if running:
            messagebox.showerror(
                "Resolume is running",
                f"Detected: {', '.join(running)}\n\nClose Resolume before syncing.",
                parent=self.root,
            )
            return

        if not os.path.isdir(src):
            messagebox.showerror("Source not found", f"Source folder does not exist:\n\n{src}",
                                 parent=self.root)
            return

        dry_run = bool(self.dry_run_var.get())

        # Confirm destructive mirror when not dry-running
        if not dry_run:
            confirm = messagebox.askyesno(
                f"Confirm {label}",
                f"This will MIRROR:\n  {src}\n  → {dst}\n\n"
                f"Files on the destination that don't exist on the source will be DELETED "
                f"(except excluded items: {', '.join(EXCLUDE_DIRS)}).\n\nProceed?",
                parent=self.root,
                icon="warning",
            )
            if not confirm:
                return

        self._clear_log()
        self._append(f"=== {label} {'(DRY-RUN)' if dry_run else ''} ===", "info")
        self._append(f"Started at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "muted")
        self._set_buttons(tk.DISABLED)
        self.operation_running = True
        self.status_var.set(f"Running {label}...")

        def emit(msg: str):
            self.root.after(0, self._append, msg)

        def worker():
            try:
                success, code, summary = ResolumeSyncEngine.run_robocopy(
                    src=src, dst=dst, dry_run=dry_run, on_output=emit,
                )
                tag = "success" if success else "error"
                self.root.after(0, self._append, "", None)
                self.root.after(0, self._append, summary, tag)

                if success and not dry_run:
                    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    key = "last_push_at" if direction == "push" else "last_pull_at"
                    self.config.set(key, ts)

                status_msg = (
                    f"{label}: {'OK' if success else 'failed'} (exit {code})"
                    + (" — dry-run" if dry_run else "")
                )
                self.root.after(0, lambda: self.status_var.set(status_msg))
            except Exception as e:
                logger.exception("Sync failed")
                self.root.after(0, self._append, f"Error: {e}", "error")
                self.root.after(0, lambda: self.status_var.set(f"{label}: error"))
            finally:
                self.operation_running = False
                self.root.after(0, lambda: self._set_buttons(tk.NORMAL))
                self.root.after(0, self._refresh_status)

        threading.Thread(target=worker, daemon=True).start()


# ====================================
# MAIN
# ====================================

def main():
    setup_shared_logging("resolume_sync")

    root = tk.Tk()
    apply_category_icon(root, "RealTime")
    ResolumeSyncUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
