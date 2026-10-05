#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tk front end for shared_musicbee_playlists: the pre-sync check and the manual "Check playlists" report."""

import os
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Dict, Iterable, List, Optional

import shared_musicbee_playlists as doctor
from shared_logging import get_logger

logger = get_logger("playlist_doctor_dialog")


def apply_extra_ids(playlist_data: Dict[str, dict], extras: Dict[str, List[str]]) -> None:
    """Merge resolved track ids into playlist_data[name]['track_ids']. The untouched list is kept
    as 'base_track_ids' so a later run with different extras (or none) never inherits old ones."""
    for name, info in playlist_data.items():
        base = info.setdefault("base_track_ids", list(info.get("track_ids", [])))
        info["track_ids"] = doctor.merge_track_ids(base, extras.get(name, []))


def _scan(parent, itunes_root, xml_path, selected, settings):
    playlists_dir = getattr(settings, "musicbee_playlists_dir", "") or doctor.default_playlists_dir(xml_path)
    if not os.path.isdir(playlists_dir):
        logger.info(f"Playlist doctor: playlists folder not found: {playlists_dir}")
        return None, playlists_dir
    roots = list(getattr(settings, "staging_roots", None) or doctor.default_staging_roots())
    if parent is not None:
        parent.config(cursor="watch")
        parent.update_idletasks()
    try:
        index = doctor.LibraryIndex.from_itunes_root(itunes_root)
        report = doctor.scan(playlists_dir, index, selected=selected, staging_roots=roots)
    finally:
        if parent is not None:
            parent.config(cursor="")
    return report, playlists_dir


class _Dialog:
    """mode 'preflight': Resolve / Repair / Sync without / Cancel.  mode 'check': Repair / Close."""

    def __init__(self, parent, report: doctor.ScanReport, mode: str):
        self.report = report
        self.mode = mode
        self.result = "cancel"
        self.win = tk.Toplevel(parent)
        self.win.title("Playlist check")
        self.win.geometry("900x520")
        self.win.transient(parent)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        running = doctor.musicbee_running()
        can_resolve = bool(report.resolvable)

        intro = ("Some playlist entries point to files that no longer exist (usually the Soulseek staging folder "
                 "after it was moved into the library). MusicBee's XML drops these, so they would be missing "
                 "from the sync.")
        ttk.Label(self.win, text=intro, wraplength=860, justify="left").pack(anchor="w", padx=12, pady=(12, 6))

        frame = ttk.Frame(self.win)
        frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=6)
        text = tk.Text(frame, wrap=tk.NONE, font=("Consolas", 9))
        ys = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=text.xview)
        text.config(yscrollcommand=ys.set, xscrollcommand=xs.set)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        text.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        text.insert(tk.END, doctor.format_report(report, limit=300))
        text.config(state=tk.DISABLED)

        note = ""
        if can_resolve and running:
            note = "MusicBee is running - close it to enable permanent repair."
        elif can_resolve:
            note = "Repair rewrites the .mbp playlist files (a backup is made first)."
        if note:
            ttk.Label(self.win, text=note, foreground="gray").pack(anchor="w", padx=12)

        bar = ttk.Frame(self.win)
        bar.pack(fill=tk.X, padx=12, pady=12)
        if mode == "preflight":
            ttk.Button(bar, text="Cancel", command=self._cancel).pack(side=tk.RIGHT, padx=(6, 0))
            ttk.Button(bar, text="Sync without", command=lambda: self._finish("skip")).pack(side=tk.RIGHT, padx=(6, 0))
            repair_btn = ttk.Button(bar, text="Repair playlists + resolve", command=self._repair)
            repair_btn.pack(side=tk.RIGHT, padx=(6, 0))
            main_label = "Resolve for this sync" if can_resolve else "Continue"
            main_btn = ttk.Button(bar, text=main_label, command=lambda: self._finish("resolve"))
            main_btn.pack(side=tk.RIGHT, padx=(6, 0))
            main_btn.focus_set()
        else:
            ttk.Button(bar, text="Close", command=self._cancel).pack(side=tk.RIGHT, padx=(6, 0))
            repair_btn = ttk.Button(bar, text="Repair playlists", command=self._repair)
            repair_btn.pack(side=tk.RIGHT, padx=(6, 0))
        if not can_resolve or running:
            repair_btn.state(["disabled"])

        self.win.grab_set()
        self.win.wait_window()

    def _finish(self, result: str) -> None:
        self.result = result
        self.win.destroy()

    def _cancel(self) -> None:
        self.result = "cancel"
        self.win.destroy()

    def _repair(self) -> None:
        try:
            res = doctor.repair(self.report, apply=True)
        except doctor.MusicBeeRunningError as e:
            messagebox.showerror("Repair playlists", str(e), parent=self.win)
            return
        except Exception as e:
            logger.error(f"Playlist repair failed: {e}")
            messagebox.showerror("Repair playlists", f"Repair failed: {e}", parent=self.win)
            return
        lines = [f"Replaced {res.replaced} entr(ies) in {len(res.files_changed)} playlist(s)."]
        if res.dropped:
            lines.append(f"Dropped {res.dropped} duplicate(s).")
        if res.skipped:
            lines.append(f"Skipped {len(res.skipped)} file(s) with an unrecognised layout.")
        if res.backup_dir:
            lines.append(f"Backups: {res.backup_dir}")
        lines.append("MusicBee picks the fix up the next time it starts.")
        messagebox.showinfo("Repair playlists", "\n".join(lines), parent=self.win)
        self._finish("repair" if self.mode == "preflight" else "cancel")


def run_preflight(parent, itunes_root, xml_path: str, selected_playlists: Iterable[str],
                  settings, interactive: bool = True) -> Optional[Dict[str, List[str]]]:
    """Check the selected playlists before a sync. Returns None if the user cancelled, otherwise
    {playlist: [extra track ids]} to merge in ({} = nothing to add). Never raises: a doctor problem
    must not block a sync. With interactive=False (direct-run, nobody to answer) no window is opened:
    resolvable entries are used for this sync, the findings are logged, and no file is written."""
    if not getattr(settings, "playlist_doctor_enabled", True) or itunes_root is None:
        return {}
    try:
        report, _ = _scan(parent if interactive else None, itunes_root, xml_path, set(selected_playlists), settings)
        if report is None or not report.issues:
            return {}
        if not interactive:
            logger.info("Playlist doctor (direct run, resolving in memory only):")
            for line in doctor.format_report(report, limit=200).splitlines():
                if line.strip():
                    logger.info(f"  {line}")
            return report.extra_track_ids()
        action = _Dialog(parent, report, "preflight").result
    except Exception as e:
        logger.warning(f"Playlist doctor skipped: {e}")
        return {}
    if action == "cancel":
        return None
    if action == "skip":
        return {}
    return report.extra_track_ids()


def show_check(parent, itunes_root, xml_path: str, settings) -> None:
    """Manual 'Check playlists': scan every playlist and show the report."""
    if itunes_root is None:
        messagebox.showinfo("Check playlists", "Load the playlists first.", parent=parent)
        return
    try:
        report, playlists_dir = _scan(parent, itunes_root, xml_path, None, settings)
    except Exception as e:
        logger.error(f"Playlist check failed: {e}")
        messagebox.showerror("Check playlists", f"Check failed: {e}", parent=parent)
        return
    if report is None:
        messagebox.showinfo("Check playlists", f"MusicBee playlists folder not found:\n{playlists_dir}", parent=parent)
        return
    if not report.issues:
        messagebox.showinfo("Check playlists", doctor.format_report(report), parent=parent)
        return
    _Dialog(parent, report, "check")
