#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_MusicBeeCleanup.py
Description: Standalone MusicBee maintenance. Finds dead and duplicate playlist entries and fixes the
ones you tick; reports missing library files and songs that exist as several files.

Only playlist files are ever edited (a backup is made first, and MusicBee must be closed). Music files and
MusicBee's library database are never touched.
"""

import csv
import datetime
import json
import os
import sys
import threading
import tkinter as tk
from dataclasses import asdict, dataclass, field
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import shared_musicbee_playlists as mb
from shared_appdata import get_appdata_path
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_open_path import reveal_path
from shared_window_icon import apply_category_icon

logger = get_logger("musicbee_cleanup")

APP_NAME = "MusicBee Cleanup"
HEADER_COLOR = "#2c3e50"
CONFIG_FILE = os.path.join(str(get_appdata_path()), "musicbee_cleanup_config.json")
DEFAULT_XML = os.path.join("M:\\", mb.DEFAULT_XML_NAME)
CHECKED, UNCHECKED = "\u2611", "\u2610"


@dataclass
class Settings:
    itunes_xml_path: str = ""
    playlists_dir: str = ""
    staging_roots: List[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str = CONFIG_FILE) -> "Settings":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str = CONFIG_FILE) -> None:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(asdict(self), f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save settings: {e}")


def _stamp(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


class CleanupApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.settings = Settings.load()
        self.scan_data: Optional[mb.FullScan] = None
        self.rows: Dict[str, mb.FixRow] = {}
        self.scanning = False

        root.title(APP_NAME)
        root.geometry("1150x740")
        root.minsize(900, 540)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = tk.Frame(root, bg=HEADER_COLOR, height=56)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(header, text=APP_NAME, font=("Arial", 16, "bold"), fg="white", bg=HEADER_COLOR).place(
            relx=0.5, rely=0.5, anchor=tk.CENTER)

        self._build_config()
        notebook = ttk.Notebook(root)
        notebook.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 6))
        self._build_playlists_tab(notebook)
        self._build_library_tab(notebook)

        self.status_var = tk.StringVar(value="Ready")
        tk.Label(root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W).grid(
            row=3, column=0, sticky="ew")

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(200, self._start_scan)

    # ------------------------------------------------------------------ UI

    def _build_config(self) -> None:
        frame = ttk.LabelFrame(self.root, text="MusicBee")
        frame.grid(row=1, column=0, sticky="ew", padx=10, pady=8)
        frame.columnconfigure(1, weight=1)

        xml = self.settings.itunes_xml_path or (DEFAULT_XML if os.path.isfile(DEFAULT_XML) else "")
        self.xml_var = tk.StringVar(value=xml)
        self.pl_var = tk.StringVar(value=self.settings.playlists_dir)
        self.staging_var = tk.StringVar(value=";".join(self.settings.staging_roots or mb.default_staging_roots()))

        ttk.Label(frame, text="iTunes XML:").grid(row=0, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(frame, textvariable=self.xml_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frame, text="Browse", command=self._browse_xml).grid(row=0, column=2, padx=4)

        ttk.Label(frame, text="Playlists folder:").grid(row=1, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(frame, textvariable=self.pl_var).grid(row=1, column=1, sticky="ew", padx=4)
        ttk.Button(frame, text="Browse", command=self._browse_playlists).grid(row=1, column=2, padx=4)
        ttk.Label(frame, text="(empty = 'Playlists' next to the XML)", foreground="gray").grid(
            row=1, column=3, sticky="w", padx=4)

        ttk.Label(frame, text="Staging folders:").grid(row=2, column=0, sticky="w", padx=8, pady=3)
        ttk.Entry(frame, textvariable=self.staging_var).grid(row=2, column=1, sticky="ew", padx=4)
        ttk.Label(frame, text="separate several with ;", foreground="gray").grid(row=2, column=3, sticky="w", padx=4)

        bar = ttk.Frame(frame)
        bar.grid(row=3, column=0, columnspan=4, sticky="ew", padx=8, pady=6)
        self.scan_btn = ttk.Button(bar, text="Scan", command=self._start_scan)
        self.scan_btn.pack(side=tk.LEFT)
        self.musicbee_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.musicbee_var).pack(side=tk.LEFT, padx=14)
        self.xml_info_var = tk.StringVar()
        ttk.Label(bar, textvariable=self.xml_info_var, foreground="gray").pack(side=tk.LEFT, padx=4)

    def _build_playlists_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Playlists")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        self.summary_var = tk.StringVar(value="Not scanned yet")
        ttk.Label(tab, textvariable=self.summary_var, wraplength=1050, justify="left").grid(
            row=0, column=0, sticky="w", padx=8, pady=6)

        frame = ttk.Frame(tab)
        frame.grid(row=1, column=0, sticky="nsew", padx=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        cols = ("check", "playlist", "problem", "entry", "fix")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="browse")
        for col, text, width in (("check", "", 36), ("playlist", "Playlist", 170), ("problem", "Problem", 210),
                                 ("entry", "Entry", 430), ("fix", "Fix", 260)):
            self.tree.heading(col, text=text)
            self.tree.column(col, width=width, anchor=tk.CENTER if col == "check" else tk.W, stretch=col != "check")
        self.tree.tag_configure("dim", foreground="gray")
        ys = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        xs = ttk.Scrollbar(frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        xs.grid(row=1, column=0, sticky="ew")
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        bar = ttk.Frame(tab)
        bar.grid(row=2, column=0, sticky="ew", padx=8, pady=8)
        ttk.Button(bar, text="Safe defaults", command=self._reset_defaults).pack(side=tk.LEFT)
        ttk.Button(bar, text="Tick all", command=lambda: self._set_all(True)).pack(side=tk.LEFT, padx=6)
        ttk.Button(bar, text="Untick all", command=lambda: self._set_all(False)).pack(side=tk.LEFT)
        ttk.Label(bar, text="Double-click a row to show its playlist file", foreground="gray").pack(
            side=tk.LEFT, padx=14)
        self.fix_btn = ttk.Button(bar, text="Fix ticked...", command=self._fix_ticked, state=tk.DISABLED)
        self.fix_btn.pack(side=tk.RIGHT)

    def _build_library_tab(self, notebook: ttk.Notebook) -> None:
        tab = ttk.Frame(notebook)
        notebook.add(tab, text="Library")
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(2, weight=2)

        top = ttk.Frame(tab)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        ttk.Label(top, text="Report only: this tool never deletes music files or edits MusicBee's library.",
                  foreground="gray").pack(side=tk.LEFT)
        ttk.Button(top, text="Export report as CSV...", command=self._export_csv).pack(side=tk.RIGHT)

        self.missing_frame = ttk.LabelFrame(tab, text="Library tracks whose file is missing")
        self.missing_frame.grid(row=1, column=0, sticky="nsew", padx=8, pady=4)
        self.missing_tree = self._make_tree(self.missing_frame, ("artist", "title", "path"),
                                            (("artist", "Artist", 220), ("title", "Title", 260), ("path", "Path", 600)),
                                            show="headings")
        self.missing_tree.bind("<Double-1>", self._on_missing_double_click)

        self.dups_frame = ttk.LabelFrame(tab, text="Songs that exist as several files (usually album vs compilation)")
        self.dups_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)
        self.dups_tree = self._make_tree(self.dups_frame, ("album", "path"),
                                         (("#0", "Song", 380), ("album", "Album", 260), ("path", "Path", 480)),
                                         show="tree headings")
        self.dups_tree.bind("<Double-1>", self._on_dups_double_click)

        self.empty_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.empty_var, foreground="gray", wraplength=1050).grid(
            row=3, column=0, sticky="w", padx=8, pady=(2, 8))

    @staticmethod
    def _make_tree(parent, columns, headings, show: str) -> ttk.Treeview:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        tree = ttk.Treeview(parent, columns=columns, show=show, selectmode="browse")
        for col, text, width in headings:
            tree.heading(col, text=text)
            tree.column(col, width=width, anchor=tk.W)
        ys = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=ys.set)
        tree.grid(row=0, column=0, sticky="nsew")
        ys.grid(row=0, column=1, sticky="ns")
        return tree

    # ------------------------------------------------------------- settings

    def _browse_xml(self) -> None:
        path = filedialog.askopenfilename(title="MusicBee iTunes XML", filetypes=[("XML", "*.xml"), ("All", "*.*")])
        if path:
            self.xml_var.set(path)

    def _browse_playlists(self) -> None:
        path = filedialog.askdirectory(title="MusicBee Playlists folder")
        if path:
            self.pl_var.set(path)

    def _save_settings(self) -> None:
        self.settings.itunes_xml_path = self.xml_var.get().strip()
        self.settings.playlists_dir = self.pl_var.get().strip()
        self.settings.staging_roots = [r.strip() for r in self.staging_var.get().split(";") if r.strip()]
        self.settings.save()

    def _on_close(self) -> None:
        self._save_settings()
        self.root.destroy()

    # ----------------------------------------------------------------- scan

    def _refresh_musicbee_status(self) -> bool:
        running = mb.musicbee_running()
        self.musicbee_var.set("MusicBee is running - close it before fixing" if running else "MusicBee is closed")
        return running

    def _start_scan(self) -> None:
        if self.scanning:
            return
        xml = self.xml_var.get().strip()
        if not xml or not os.path.isfile(xml):
            messagebox.showerror(APP_NAME, "Select MusicBee's iTunes XML file first.")
            return
        playlists_dir = self.pl_var.get().strip() or mb.default_playlists_dir(xml)
        if not os.path.isdir(playlists_dir):
            messagebox.showerror(APP_NAME, f"Playlists folder not found:\n{playlists_dir}")
            return
        roots = [r.strip() for r in self.staging_var.get().split(";") if r.strip()]
        self._save_settings()
        self._refresh_musicbee_status()
        self.scanning = True
        self.scan_btn.config(state=tk.DISABLED)
        self.status_var.set("Scanning - reading the MusicBee XML takes a few seconds...")
        threading.Thread(target=self._scan_worker, args=(xml, playlists_dir, roots), daemon=True).start()

    def _scan_worker(self, xml: str, playlists_dir: str, roots: List[str]) -> None:
        try:
            data = mb.full_scan(xml, playlists_dir, roots)
        except Exception as e:
            logger.exception("Scan failed")
            self.root.after(0, self._scan_failed, str(e))
            return
        self.root.after(0, self._scan_done, data)

    def _scan_failed(self, message: str) -> None:
        self.scanning = False
        self.scan_btn.config(state=tk.NORMAL)
        self.status_var.set("Scan failed")
        messagebox.showerror(APP_NAME, f"Scan failed:\n{message}")

    def _scan_done(self, data: mb.FullScan) -> None:
        self.scanning = False
        self.scan_btn.config(state=tk.NORMAL)
        self.scan_data = data

        info = f"XML exported {_stamp(data.xml_mtime)}"
        if data.newest_playlist_mtime > data.xml_mtime:
            info += " (older than your newest playlist change - tracks added since are not in it)"
        self.xml_info_var.set(info)

        self.rows = {}
        self.tree.delete(*self.tree.get_children())
        for n, row in enumerate(mb.build_fix_rows(data.report, data.dupes)):
            iid = str(n)
            self.rows[iid] = row
            self.tree.insert("", tk.END, iid=iid, tags=() if row.fixable else ("dim",),
                             values=(CHECKED if row.checked else UNCHECKED if row.fixable else "",
                                     row.playlist, row.problem, row.entry, row.fix))
        self._update_fix_button()

        n_dead = sum(1 for r in self.rows.values() if r.problem.startswith("Dead"))
        n_dupe = sum(1 for r in self.rows.values() if r.problem.startswith("Same"))
        n_stage = sum(1 for r in self.rows.values() if r.problem.startswith("In staging"))
        n_pl = len({r.playlist for r in self.rows.values()})
        if self.rows:
            self.summary_var.set(
                f"{len(self.rows)} finding(s) in {n_pl} of {data.report.playlists_scanned} playlists: "
                f"{n_dead} dead entr{'y' if n_dead == 1 else 'ies'}, {n_dupe} duplicate row(s), "
                f"{n_stage} still in staging. Ticked rows are fixed; entries that would drop a song start unticked.")
        else:
            self.summary_var.set(f"No playlist problems found in {data.report.playlists_scanned} playlists.")

        self.missing_tree.delete(*self.missing_tree.get_children())
        for t in data.missing:
            self.missing_tree.insert("", tk.END, values=(t.artist, t.title, t.path))
        self.missing_frame.config(text=f"Library tracks whose file is missing ({len(data.missing)})")

        self.dups_tree.delete(*self.dups_tree.get_children())
        for group in data.dup_songs:
            parent = self.dups_tree.insert("", tk.END, text=f"{group[0].artist} - {group[0].title}  ({len(group)} files)")
            for t in group:
                self.dups_tree.insert(parent, tk.END, values=(t.album, t.path))
        self.dups_frame.config(
            text=f"Songs that exist as several files ({len(data.dup_songs)}; usually album vs compilation)")
        self.empty_var.set("Empty playlists: " + (", ".join(data.empty) if data.empty else "none"))
        self.status_var.set(f"Scan finished {datetime.datetime.now().strftime('%H:%M:%S')}")

    # ------------------------------------------------------------- selection

    def _on_tree_click(self, event) -> None:
        if self.tree.identify("region", event.x, event.y) != "cell" or self.tree.identify_column(event.x) != "#1":
            return
        iid = self.tree.identify_row(event.y)
        row = self.rows.get(iid)
        if row is not None and row.fixable:
            self._set_checked(iid, not row.checked)
            self._update_fix_button()

    def _on_tree_double_click(self, event) -> None:
        if self.tree.identify_column(event.x) == "#1":
            return
        row = self.rows.get(self.tree.identify_row(event.y))
        if row is not None:
            reveal_path(row.file)

    def _set_checked(self, iid: str, value: bool) -> None:
        self.rows[iid].checked = value
        self.tree.set(iid, "check", CHECKED if value else UNCHECKED)

    def _set_all(self, value: bool) -> None:
        for iid, row in self.rows.items():
            if row.fixable:
                self._set_checked(iid, value)
        self._update_fix_button()

    def _reset_defaults(self) -> None:
        if self.scan_data is None:
            return
        defaults = mb.build_fix_rows(self.scan_data.report, self.scan_data.dupes)
        for iid, default in zip(sorted(self.rows, key=int), defaults):
            if self.rows[iid].fixable:
                self._set_checked(iid, default.checked)
        self._update_fix_button()

    def _update_fix_button(self) -> None:
        n = sum(1 for r in self.rows.values() if r.checked and r.fixable)
        self.fix_btn.config(text=f"Fix ticked ({n})..." if n else "Fix ticked...",
                            state=tk.NORMAL if n else tk.DISABLED)

    # ------------------------------------------------------------------ fix

    def _fix_ticked(self) -> None:
        ticked = [r for r in self.rows.values() if r.checked and r.fixable]
        if not ticked:
            return

        groups: Dict[str, List[mb.FixRow]] = {}
        for r in self.rows.values():
            if r.group:
                groups.setdefault(r.group, []).append(r)
        for members in groups.values():
            if all(m.checked for m in members):
                messagebox.showerror(
                    APP_NAME, f"Every copy of '{members[0].entry.split('  |  ')[0]}' in playlist "
                              f"'{members[0].playlist}' is ticked. Leave one file unticked so the song stays.")
                return

        if self._refresh_musicbee_status():
            messagebox.showerror(APP_NAME, "MusicBee is running. Close it first - it would overwrite the changes.")
            return

        counts = {"replace": 0, "remove": 0, "dedupe": 0}
        for r in ticked:
            counts[r.action] += 1
        files = len({r.file for r in ticked})
        summary = (f"Apply {len(ticked)} fix(es) in {files} playlist(s)?\n\n"
                   f"  Replace dead entries with the real track: {counts['replace']}\n"
                   f"  Remove extra copies of the same file: {counts['dedupe']}\n"
                   f"  Remove entries from playlists: {counts['remove']}\n\n"
                   f"Every changed playlist is backed up first to:\n{mb.default_backup_root()}")
        if not messagebox.askyesno(APP_NAME, summary):
            return

        try:
            result = mb.apply_edits(mb.edits_from_rows(ticked), apply=True)
        except Exception as e:
            logger.exception("Fix failed")
            messagebox.showerror(APP_NAME, f"Fix failed: {e}\n\nBackups (if any): {mb.default_backup_root()}")
            return

        lines = [f"Changed {len(result.files_changed)} playlist(s): {result.replaced} replaced, "
                 f"{result.removed} removed, {result.dropped} duplicate copies dropped."]
        if result.skipped:
            lines.append(f"Skipped {len(result.skipped)} playlist(s) with an unrecognised layout.")
        if result.backup_dir:
            lines.append(f"Backups: {result.backup_dir}")
        lines.append("MusicBee shows the changes the next time it starts.")
        messagebox.showinfo(APP_NAME, "\n".join(lines))
        self._start_scan()

    # -------------------------------------------------------------- library

    def _on_missing_double_click(self, event) -> None:
        iid = self.missing_tree.identify_row(event.y)
        if iid:
            folder = os.path.dirname(self.missing_tree.item(iid, "values")[2])
            if os.path.isdir(folder):
                reveal_path(folder)
            else:
                messagebox.showinfo(APP_NAME, f"The folder no longer exists:\n{folder}")

    def _on_dups_double_click(self, event) -> None:
        iid = self.dups_tree.identify_row(event.y)
        if iid and self.dups_tree.parent(iid):
            reveal_path(self.dups_tree.item(iid, "values")[1])

    def _export_csv(self) -> None:
        if self.scan_data is None:
            return
        path = filedialog.asksaveasfilename(
            title="Export report", defaultextension=".csv",
            initialfile=f"musicbee_cleanup_{datetime.date.today().isoformat()}.csv",
            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        data = self.scan_data
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Section", "Artist or playlist", "Title or problem", "Album or entry", "Path or fix"])
            for r in self.rows.values():
                w.writerow(["Playlist", r.playlist, r.problem, r.entry, r.fix])
            for t in data.missing:
                w.writerow(["Missing file", t.artist, t.title, t.album, t.path])
            for group in data.dup_songs:
                for t in group:
                    w.writerow(["Song in several files", t.artist, t.title, t.album, t.path])
            for name in data.empty:
                w.writerow(["Empty playlist", name, "", "", ""])
        self.status_var.set(f"Report saved to {path}")


def main() -> int:
    setup_shared_logging("musicbee_cleanup")
    root = tk.Tk()
    apply_category_icon(root)
    CleanupApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
