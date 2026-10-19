#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_TraktorStems.py
Description: Everything about Traktor's stems in one window.

A track "has stems" in two places: a link on the track in collection.nml (bit 0x40 of its INFO FLAGS) and a stem file
in Traktor's own Stems folder. They drift apart - stem files never travel with the music, so a synced track can claim
stems that are not on this machine and then refuse to load. The status panel at the top compares the two; the tabs
follow the life of a stem:

  Create    pick a playlist and have Traktor create the stems for every track in it (macOS; presses Traktor's Shift+S
            and waits on Traktor's log for each result)
  Re-link   set the link again on tracks whose stem file still exists (matched by title through Traktor's logs, and
            only where a collection backup says the link was there)
  Clean up  remove the links (all, or only those without a stem file), delete the stem files (to the Trash on a Mac),
            or both

Whatever changes collection.nml makes a backup next to it first, and Traktor must be closed for those. Every command
line action only previews unless --apply is given.

    python PipelineScript_Audio_TraktorStems.py                                  window
    python PipelineScript_Audio_TraktorStems.py status
    python PipelineScript_Audio_TraktorStems.py playlists
    python PipelineScript_Audio_TraktorStems.py create "Sets/Peak" [--include-existing] [--apply]
    python PipelineScript_Audio_TraktorStems.py relink [--backup FILE] [--apply]
    python PipelineScript_Audio_TraktorStems.py unlink [--all] [--apply]      (default: only links without a stem file)
    python PipelineScript_Audio_TraktorStems.py delete [--permanent] [--apply]
    python PipelineScript_Audio_TraktorStems.py clean [--permanent] [--apply]  (unlink --all, then delete)
    put  --collection <path to collection.nml>  before the action to use another file than the newest Traktor version's
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, List, Optional

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_appdata import get_appdata_path
from shared_color_button import ColorButton
from shared_action_bar import ActionBar
from shared_mode_lock import make_header_title
from shared_traktor_keep import find_collection_nml
from shared_traktor_stems import (
    CleanResult, RestorePlan, RestoreResult, clean_collection, find_stem_backup, restore_collection, surviving_stems,
)
from shared_traktor_stemfiles import (
    CleanupError, DeleteResult, StemStatus, can_trash, collection_status, default_stem_roots, delete_stem_files,
    find_stem_files, human_size,
)
from shared_traktor_stemgen import (
    DriverError, LogWatcher, MacTraktorKeys, Playlist, PlanItem, RunOptions, RunSummary,
    estimate, load_playlists, make_plan, recent_stem_titles, run_plan,
)

logger = get_logger("traktor_stems")

APP_NAME = "Traktor Stems"
HEADER_COLOR = "#2c3e50"
CONFIG_FILE = os.path.join(str(get_appdata_path()), "traktor_stems_config.json")


# ============================================================================
# SMALL HELPERS (shared by the window and the command line)
# ============================================================================

def running_traktor() -> List[str]:
    """Traktor processes that are open right now (the playlist sync tool already knows how to look)."""
    try:
        from PipelineScript_Audio_TraktorPlaylistSync import running_traktor_processes
        return running_traktor_processes()
    except Exception as e:
        logger.warning(f"Could not check whether Traktor is running: {e}")
        return []


def docs_for(collection: str) -> str:
    """The 'Native Instruments' documents folder: collection.nml sits in '<it>/Traktor <version>/'."""
    return os.path.dirname(os.path.dirname(os.path.abspath(collection)))


def log_for(collection: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(collection)), "Logs", "Traktor.log")


def stems_for(collection: str) -> dict:
    """Track title -> stem files that still exist, from Traktor's logs."""
    return surviving_stems(docs_for(collection))


def describe_links(result: CleanResult, only_missing: bool) -> str:
    what = "stem links without a stem file" if only_missing else "stem links"
    if not result.flagged:
        return f"No {what} to remove."
    if result.applied:
        return f"Removed {result.flagged} {what} (of {result.tracks} tracks)."
    return f"{result.flagged} {what} would be removed (of {result.tracks} tracks)."


def describe_restore(result: RestoreResult) -> str:
    plan = result.plan
    skipped = len(plan.ambiguous) + len(plan.unmatched)
    head = (f"Re-linked {len(plan.to_flag)} tracks." if result.applied
            else f"{len(plan.to_flag)} tracks can be re-linked.")
    return f"{head} Skipped {skipped} (same title on several tracks: {len(plan.ambiguous)}, title not found: {len(plan.unmatched)})."


def restore_report(plan: RestorePlan) -> List[str]:
    lines = [f"  Stem file exists and the backup had the link : {len(plan.to_flag)}  (re-linked)",
             f"  Already linked                               : {len(plan.already_flagged)}",
             f"  Stem file exists, but the backup had no link : {len(plan.not_flagged_in_backup)}  (left alone)"]
    if plan.ambiguous:
        lines += ["", f"Skipped - the same title is on several tracks, so the stem file can't be matched ({len(plan.ambiguous)}):"]
        for title, tracks in plan.ambiguous:
            lines.append(f'  "{title}"')
            lines += [f"      {t.label}   [{t.file}]" for t in tracks]
    if plan.unmatched:
        lines += ["", f"Skipped - no track with that title in the collection any more ({len(plan.unmatched)}):"]
        lines += [f'  "{title}"   ->   {stem}' for title, stem in plan.unmatched]
    if plan.not_flagged_in_backup:
        lines += ["", f"Left alone - stem file exists but the backup had no link ({len(plan.not_flagged_in_backup)}):"]
        lines += [f"  {t.label}   [{t.file}]" for t in plan.not_flagged_in_backup]
    return lines


def describe_delete(result: DeleteResult) -> str:
    where = "Deleted" if result.permanent else "Moved to the Trash:"
    text = f"{where} {result.removed} stem files ({human_size(result.size)})."
    return text + (f" {len(result.failed)} could not be removed." if result.failed else "")


def summarize_run(summary: RunSummary) -> List[str]:
    lines = [f"Created stems for {len(summary.created)} track(s); {summary.skipped} skipped."]
    if summary.failed:
        lines.append(f"{len(summary.failed)} failed:")
        lines += [f"  {r.title}: {r.summary}" for r in summary.failed]
    lines.append("Finished the whole playlist." if summary.complete else f"STOPPED: {summary.stopped}")
    lines.append("Traktor records the new stems in collection.nml when it saves (quitting Traktor does).")
    return lines


def keep_awake() -> Optional["subprocess.Popen"]:
    """Stops a Mac sleeping during a long run (until this process ends or the result is terminated)."""
    if sys.platform != "darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    except OSError:
        return None


def unlink_everything_and_delete(collection: str, permanent: bool, log: Callable[[str], None]) -> str:
    """Both clean-ups in the only safe order: links first, so no track is left claiming stems that were just deleted.
    A failure in the first step stops before any file is touched."""
    links = clean_collection(collection, apply=True)
    log(describe_links(links, only_missing=False) + (f"  Backup: {links.backup_path}" if links.backup_path else ""))
    stems = find_stem_files(default_stem_roots(docs_for(collection)))
    deleted = delete_stem_files(stems, permanent=permanent, log=log) if stems else DeleteResult(permanent=permanent)
    log(describe_delete(deleted))
    return f"{describe_links(links, only_missing=False)}\n{describe_delete(deleted)}"


# ============================================================================
# WINDOW
# ============================================================================

class StemsUI:
    def __init__(self, root: tk.Tk, collection: Optional[str] = None, backup: Optional[str] = None):
        self.root = root
        self.busy = False
        self.running = False                       # a Create run is in progress
        self.cancel = threading.Event()
        self.playlists: List[Playlist] = []
        self.recent = set()
        self.status = StemStatus()
        self.config = self._load_config()
        root.title(APP_NAME)
        root.geometry("940x860")
        root.minsize(760, 640)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        header = tk.Frame(root, bg=HEADER_COLOR, height=60)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        make_header_title(header, APP_NAME)

        self.action_bar = ActionBar(root)
        self.action_bar.grid(row=1, column=0, sticky="ew")

        main = ttk.Frame(root)
        main.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=3)
        main.rowconfigure(3, weight=1)

        # --- the collection and how its stems stand ---
        self.path_var = tk.StringVar(value=collection or find_collection_nml() or "")
        self.backup_var = tk.StringVar(value=backup or "")
        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="collection.nml:").grid(row=0, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(top, textvariable=self.path_var).grid(row=0, column=1, sticky="ew", padx=5, pady=6)
        ttk.Button(top, text="Browse", command=self._browse_collection).grid(row=0, column=2, padx=5)
        ttk.Button(top, text="Reload", command=self._reload).grid(row=0, column=3, padx=(0, 5))

        status = ttk.LabelFrame(main, text="Stems in this collection")
        status.grid(row=1, column=0, sticky="ew", pady=(2, 6))
        self.headline_var, self.detail_var = tk.StringVar(), tk.StringVar()
        ttk.Label(status, textvariable=self.headline_var, font=("", 10, "bold")).pack(anchor="w", padx=10, pady=(6, 0))
        ttk.Label(status, textvariable=self.detail_var, foreground="gray").pack(anchor="w", padx=10, pady=(0, 6))

        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=2, column=0, sticky="nsew")
        create_tab, relink_tab, clean_tab = ttk.Frame(self.notebook), ttk.Frame(self.notebook), ttk.Frame(self.notebook)
        self.notebook.add(create_tab, text="  1  Create stems  ")
        self.notebook.add(relink_tab, text="  2  Re-link  ")
        self.notebook.add(clean_tab, text="  3  Clean up  ")
        self._build_create(create_tab)
        self._build_relink(relink_tab)
        self._build_clean(clean_tab)
        self.action_bar.follow(self.notebook, ["create", "relink", "clean"])

        # --- one log for everything ---
        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=8, font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=scroll.set)

        root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._reload()

    # ------------------------------------------------------------------
    # building the tabs
    # ------------------------------------------------------------------

    def _button(self, parent, text, command, bg, width, **kw) -> ColorButton:
        button = ColorButton(parent, text=text, command=command, width=width, bg=bg, fg="white", font=("", 9, "bold"), **kw)
        button.pack(side=tk.LEFT, padx=(0, 8))
        return button

    @staticmethod
    def _tree(parent, columns, row=0, height=6) -> ttk.Treeview:
        tree = ttk.Treeview(parent, columns=[c[0] for c in columns], show="headings", height=height, selectmode="browse")
        for key, title, width in columns:
            tree.heading(key, text=title)
            tree.column(key, width=width, anchor="w")
        tree.grid(row=row, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(parent, command=tree.yview)
        scroll.grid(row=row, column=1, sticky="ns")
        tree.config(yscrollcommand=scroll.set)
        return tree

    def _build_create(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)
        tab.rowconfigure(2, weight=2)
        ttk.Label(tab, foreground="gray", wraplength=840, justify="left",
                  text="Pick a playlist. In Traktor, open it, sort it by its # column and click the first track, then press "
                       "Create stems and leave the computer alone: this presses Traktor's Shift+S for each track and waits "
                       "for Traktor's log to report it (about 2-3 minutes a track). macOS only.",
                  ).grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
        lists = ttk.LabelFrame(tab, text="Playlist")
        lists.grid(row=1, column=0, sticky="nsew", padx=6)
        lists.columnconfigure(0, weight=1)
        lists.rowconfigure(0, weight=1)
        self.playlist_tree = self._tree(lists, [("name", "Playlist", 560), ("tracks", "Tracks", 80), ("need", "Need stems", 100)], height=5)
        self.playlist_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_tracks())
        tracks = ttk.LabelFrame(tab, text="Tracks")
        tracks.grid(row=2, column=0, sticky="nsew", padx=6, pady=(6, 0))
        tracks.columnconfigure(0, weight=1)
        tracks.rowconfigure(0, weight=1)
        self.track_tree = self._tree(tracks, [("pos", "#", 45), ("artist", "Artist", 220), ("title", "Title", 340), ("plan", "Plan", 240)], height=6)
        options = ttk.Frame(tab)
        options.grid(row=3, column=0, sticky="ew", pady=6)
        self.skip_var = tk.BooleanVar(value=self.config.get("skip_existing", True))
        ttk.Checkbutton(options, text="Skip tracks that already have stems", variable=self.skip_var,
                        command=self._show_tracks).pack(side=tk.LEFT, padx=10)
        ttk.Label(options, text="Give up on a track after").pack(side=tk.LEFT, padx=(20, 4))
        self.timeout_var = tk.IntVar(value=self.config.get("timeout_minutes", 15))
        ttk.Spinbox(options, from_=3, to=90, width=4, textvariable=self.timeout_var).pack(side=tk.LEFT)
        ttk.Label(options, text="min").pack(side=tk.LEFT, padx=(4, 0))
        buttons = self.action_bar.group("create")
        self.create_btn = self._button(buttons, "Create stems", self._create, "#27ae60", 18)
        self.stop_btn = self._button(buttons, "Stop", self._stop, "#c0392b", 10, state=tk.DISABLED)

    def _build_relink(self, tab):
        tab.columnconfigure(1, weight=1)
        ttk.Label(tab, foreground="gray", wraplength=840, justify="left",
                  text="For stem files that are still on disk but whose track lost its link (see 'Stem file found, but not linked' "
                       "above). Traktor's logs say which track each stem file was made for, and the backup below says which "
                       "tracks had the link; only those get it back. Titles shared by several tracks, or no longer in the "
                       "collection, are skipped and listed in the log. Close Traktor first.",
                  ).grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=(8, 4))
        ttk.Label(tab, text="Backup with the links:").grid(row=1, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(tab, textvariable=self.backup_var).grid(row=1, column=1, sticky="ew", padx=5, pady=6)
        ttk.Button(tab, text="Browse", command=self._browse_backup).grid(row=1, column=2, padx=5)
        self.relink_var = tk.StringVar()
        ttk.Label(tab, textvariable=self.relink_var, font=("", 10, "bold"), wraplength=840, justify="left").grid(
            row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(6, 4))
        buttons = self.action_bar.group("relink")
        self.preview_btn = self._button(buttons, "Preview", self._preview_relink, "#7f8c8d", 14)
        self.relink_btn = self._button(buttons, "Re-link tracks", self._relink, "#27ae60", 18)

    def _build_clean(self, tab):
        tab.columnconfigure(0, weight=1)
        links = ttk.LabelFrame(tab, text="Stem links in collection.nml")
        links.grid(row=0, column=0, sticky="ew", padx=6, pady=(8, 4))
        links.columnconfigure(0, weight=1)
        ttk.Label(links, foreground="gray", wraplength=640, justify="left",
                  text="Clears the 'has stems' mark on tracks, so Traktor loads them normally. The stem files are not touched.",
                  ).grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.scope_var = tk.StringVar(value="missing")
        ttk.Radiobutton(links, text="Only links without a stem file (keeps the ones that work)", variable=self.scope_var,
                        value="missing").grid(row=1, column=0, sticky="w", padx=10)
        ttk.Radiobutton(links, text="All links", variable=self.scope_var, value="all").grid(row=2, column=0, sticky="w", padx=10, pady=(0, 8))
        holder = ttk.Frame(links)
        holder.grid(row=1, column=1, rowspan=2, padx=10, pady=(0, 8))
        self.unlink_btn = self._button(holder, "Remove links", self._remove_links, "#c0392b", 18)

        files = ttk.LabelFrame(tab, text="Stem files on disk")
        files.grid(row=1, column=0, sticky="ew", padx=6, pady=4)
        files.columnconfigure(0, weight=1)
        self.files_var = tk.StringVar()
        ttk.Label(files, textvariable=self.files_var, wraplength=640, justify="left").grid(row=0, column=0, columnspan=2, sticky="w", padx=10, pady=(6, 2))
        self.permanent_var = tk.BooleanVar(value=not can_trash())
        permanent = ttk.Checkbutton(files, variable=self.permanent_var, state=tk.NORMAL if can_trash() else tk.DISABLED,
                                    text="Delete permanently instead of moving to the Trash (frees the space at once, can't be undone)")
        permanent.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 8))
        holder = ttk.Frame(files)
        holder.grid(row=0, column=1, rowspan=2, padx=10, pady=(0, 8))
        self.delete_btn = self._button(holder, "Delete stem files", self._delete_files, "#c0392b", 18)

        everything = ttk.LabelFrame(tab, text="Everything")
        everything.grid(row=2, column=0, sticky="ew", padx=6, pady=4)
        ttk.Label(everything, foreground="gray", wraplength=820, justify="left",
                  text="Removes every link and then deletes every stem file (the 'Remove everything' button above, same Trash / "
                       "permanent choice as the stem files). Afterwards there is nothing left to re-link, and every track needs "
                       "its stems created again.").pack(anchor="w", padx=10, pady=8)
        self.everything_btn = self._button(self.action_bar.group("clean"), "Remove everything", self._remove_everything, "#8e1b1b", 22)

    # ------------------------------------------------------------------
    # settings, logging, busy state
    # ------------------------------------------------------------------

    def _load_config(self) -> dict:
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_config(self):
        selected = self._selected()
        data = {"skip_existing": self.skip_var.get(), "timeout_minutes": self.timeout_var.get(),
                "playlist": selected.name if selected else self.config.get("playlist", "")}
        try:
            os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            logger.warning(f"Could not save settings: {e}")

    def _log(self, message: str):
        logger.info(message)
        self.root.after(0, lambda: (self.log_text.insert(tk.END, message + "\n"), self.log_text.see(tk.END)))

    def _action_buttons(self):
        return (self.create_btn, self.preview_btn, self.relink_btn, self.unlink_btn, self.delete_btn, self.everything_btn)

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.root.after(0, lambda: [b.config(state=state) for b in self._action_buttons()])
        self.root.after(0, lambda: self.stop_btn.config(state=tk.NORMAL if (busy and self.running) else tk.DISABLED))

    def _in_background(self, work: Callable[[], Optional[str]], failed: str):
        """Run `work` off the window's thread; it returns the text for the result dialog."""
        def target():
            try:
                message = work()
                if message:
                    self.root.after(0, lambda: messagebox.showinfo(APP_NAME, message))
            except (CleanupError, DriverError, ValueError, OSError) as e:
                self._log(f"\nERROR: {e}")
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"{failed}:\n{e}"))
            except Exception as e:
                logger.exception(failed)
                self._log(f"\nERROR: {e}")
                self.root.after(0, lambda: messagebox.showerror(APP_NAME, f"{failed}:\n{e}"))
            finally:
                self._set_busy(False)
                self.root.after(0, self._refresh_status)
        self._set_busy(True)
        threading.Thread(target=target, daemon=True).start()

    def _traktor_open(self) -> bool:
        running = running_traktor()
        if running:
            messagebox.showerror("Traktor is running",
                                 f"Close Traktor first ({', '.join(running)} is running) - it would overwrite the change when it exits.")
        return bool(running)

    def _collection(self) -> Optional[str]:
        path = self.path_var.get().strip()
        if not path or not os.path.isfile(path):
            messagebox.showerror(APP_NAME, "Pick an existing collection.nml first.")
            return None
        return path

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------

    def _browse_collection(self):
        chosen = filedialog.askopenfilename(title="Select Traktor collection.nml",
                                            initialdir=os.path.dirname(self.path_var.get()) or None,
                                            filetypes=[("Traktor collection", "*.nml"), ("All files", "*.*")])
        if chosen:
            self.path_var.set(chosen)
            self.backup_var.set("")
            self._reload()

    def _reload(self):
        if self.busy:
            return
        path = self.path_var.get().strip()
        self.playlist_tree.delete(*self.playlist_tree.get_children())
        self.track_tree.delete(*self.track_tree.get_children())
        self.playlists = []
        if not path or not os.path.isfile(path):
            self.headline_var.set("No Traktor collection found - Browse to your collection.nml.")
            self.detail_var.set("")
            self.files_var.set("")
            return
        try:
            self.playlists = load_playlists(path)
            self.recent = recent_stem_titles(docs_for(path))
        except Exception as e:
            logger.exception("Could not read the collection")
            self._log(f"ERROR: could not read {path}: {e}")
        self._fill_playlists()
        wanted = self.config.get("playlist", "")
        for index, playlist in enumerate(self.playlists):
            if playlist.name == wanted:
                self.playlist_tree.selection_set(str(index))
                self.playlist_tree.see(str(index))
        if not self.backup_var.get():
            self.backup_var.set(find_stem_backup(path) or "")
        self._refresh_status()
        self._preview_relink(quiet=True)
        self._log(f"{path}: {len(self.playlists)} playlists")

    def _refresh_status(self):
        path = self.path_var.get().strip()
        if not path or not os.path.isfile(path):
            return
        try:
            self.status = collection_status(path)
        except Exception as e:
            logger.exception("Could not work out the stem status")
            self.headline_var.set("Could not read that file.")
            self.detail_var.set(str(e))
            return
        self.headline_var.set(self.status.headline)
        self.detail_var.set(self.status.detail)
        folders = ", ".join(folder for folder, _ in self.status.roots) or "no stem folders found"
        self.files_var.set(f"{self.status.files} stem files, {human_size(self.status.size)}  ({folders})")

    # ------------------------------------------------------------------
    # tab 1: create
    # ------------------------------------------------------------------

    def _plan(self, playlist: Playlist) -> List[PlanItem]:
        return make_plan(playlist.rows, self.skip_var.get(), self.recent)

    def _fill_playlists(self):
        """Fill the list once; later calls only refresh the 'Need stems' counts (re-selecting rows here would fire the
        selection event again and loop)."""
        existing = set(self.playlist_tree.get_children())
        for index, playlist in enumerate(self.playlists):
            need = sum(1 for item in self._plan(playlist) if not item.skip)
            if str(index) in existing:
                self.playlist_tree.set(str(index), "need", need)
            else:
                self.playlist_tree.insert("", tk.END, iid=str(index), values=(playlist.name, len(playlist.rows), need))

    def _selected(self) -> Optional[Playlist]:
        selection = self.playlist_tree.selection()
        return self.playlists[int(selection[0])] if selection else None

    def _show_tracks(self):
        self._fill_playlists()
        self.track_tree.delete(*self.track_tree.get_children())
        playlist = self._selected()
        if not playlist:
            return
        for item in self._plan(playlist):
            self.track_tree.insert("", tk.END, values=(item.position, item.row.artist, item.row.title, item.skip or "create stems"))
        if playlist.unknown:
            self._log(f"'{playlist.name}': {playlist.unknown} entr{'y' if playlist.unknown == 1 else 'ies'} point at tracks "
                      f"that are not in the collection and are ignored here.")

    def _create(self):
        if self.busy:
            return
        playlist = self._selected()
        if not playlist:
            messagebox.showerror(APP_NAME, "Pick a playlist first.")
            return
        if sys.platform != "darwin":
            messagebox.showerror(APP_NAME, "Pressing Traktor's keys is only set up for macOS.")
            return
        plan = self._plan(playlist)
        todo = [item for item in plan if not item.skip]
        if not todo:
            messagebox.showinfo(APP_NAME, "Nothing to do - every track in this playlist already has stems.")
            return
        if not running_traktor():
            messagebox.showerror(APP_NAME, "Open Traktor first.")
            return
        log_path = log_for(self.path_var.get().strip())
        if not os.path.isfile(log_path):
            messagebox.showerror(APP_NAME, f"Traktor's log wasn't found:\n{log_path}\n\nIt is how this knows a track is finished.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Create stems for {len(todo)} of {len(plan)} tracks in '{playlist.name}'?\nAbout {estimate(plan)}.\n\n"
            f"Before you press Yes:\n"
            f"  1. In Traktor, open this playlist and sort it by its # column (playlist order).\n"
            f"  2. Click the FIRST track so it is highlighted.\n"
            f"  3. Leave the computer alone while it runs - the keys go to whatever is in front.\n\n"
            f"Stop ends it between tracks.",
        ):
            return
        self._save_config()
        self.cancel.clear()
        self.running = True
        minutes = max(3, int(self.timeout_var.get() or 15))

        def work():
            awake = keep_awake()
            try:
                summary = run_plan(plan, MacTraktorKeys(), LogWatcher(log_path), RunOptions(job_timeout=minutes * 60),
                                   self._log, self.cancel)
            finally:
                if awake:
                    awake.terminate()
                self.running = False
            report = summarize_run(summary)
            self._log("\n".join(report))
            return "\n".join(report)

        self._in_background(work, "Creating stems failed")

    def _stop(self):
        self.cancel.set()
        self._log("Stopping after the current track...")

    # ------------------------------------------------------------------
    # tab 2: re-link
    # ------------------------------------------------------------------

    def _browse_backup(self):
        chosen = filedialog.askopenfilename(title="Select a collection backup that still has the stem links",
                                            initialdir=os.path.dirname(self.path_var.get()) or None,
                                            filetypes=[("Collection or backup", "*.nml *.bak*"), ("All files", "*.*")])
        if chosen:
            self.backup_var.set(chosen)
            self._preview_relink()

    def _preview_relink(self, quiet: bool = False):
        path = self.path_var.get().strip()
        backup = self.backup_var.get().strip()
        if not path or not os.path.isfile(path):
            return
        if not backup or not os.path.isfile(backup):
            self.relink_var.set("No backup with stem links found next to this collection - Browse to one.")
            return
        try:
            result = restore_collection(path, backup, stems_for(path), apply=False)
        except Exception as e:
            logger.exception("Re-link preview failed")
            self.relink_var.set("Could not read the collection or the backup.")
            self._log(f"ERROR: {e}")
            return
        self.relink_var.set(describe_restore(result))
        if not quiet:
            self._log("Re-link preview (nothing changed):\n" + "\n".join(restore_report(result.plan)))

    def _relink(self):
        path = self._collection()
        backup = self.backup_var.get().strip()
        if not path or self.busy or self._traktor_open():
            return
        if not backup or not os.path.isfile(backup):
            messagebox.showerror(APP_NAME, "Pick the backup of collection.nml that still has the stem links.")
            return
        try:
            found = restore_collection(path, backup, stems_for(path), apply=False)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not read the collection or the backup:\n{e}")
            return
        if not found.plan.to_flag:
            self.relink_var.set(describe_restore(found))
            messagebox.showinfo(APP_NAME, "Nothing to re-link - " + describe_restore(found))
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Set the stem link again on {len(found.plan.to_flag)} tracks in:\n{path}\n\n"
            f"{len(found.plan.ambiguous) + len(found.plan.unmatched)} titles are skipped (listed in the log). "
            f"A backup is made first. Continue?",
        ):
            return

        def work():
            result = restore_collection(path, backup, stems_for(path), apply=True)
            self._log(f"{describe_restore(result)}\n  Backup of the file before this change: {result.backup_path}\n"
                      + "\n".join(restore_report(result.plan)))
            self.root.after(0, lambda: self.relink_var.set(describe_restore(result)))
            return f"{describe_restore(result)}\n\nBackup:\n{result.backup_path}\n\nOpen Traktor and load one of these tracks to check the stems."

        self._in_background(work, "Re-linking failed")

    # ------------------------------------------------------------------
    # tab 3: clean up
    # ------------------------------------------------------------------

    def _remove_links(self):
        path = self._collection()
        if not path or self.busy or self._traktor_open():
            return
        only_missing = self.scope_var.get() == "missing"
        try:
            found = clean_collection(path, apply=False, only_missing_in=stems_for(path) if only_missing else None)
        except Exception as e:
            messagebox.showerror(APP_NAME, f"Could not read that file:\n{e}")
            return
        if not found.flagged:
            messagebox.showinfo(APP_NAME, describe_links(found, only_missing))
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"{describe_links(found, only_missing)}\n\nIn:\n{path}\n\nA backup is made first (Re-link can use it later). "
            f"Stem files are not deleted. Continue?",
        ):
            return

        def work():
            result = clean_collection(path, apply=True, only_missing_in=stems_for(path) if only_missing else None)
            self._log(f"{describe_links(result, only_missing)}\n  Backup: {result.backup_path}")
            self.root.after(0, lambda: self.backup_var.set(result.backup_path or self.backup_var.get()))
            return f"{describe_links(result, only_missing)}\n\nBackup:\n{result.backup_path}\n\nOpen Traktor and load a track to check."

        self._in_background(work, "Removing the links failed")

    def _where(self) -> str:
        return "permanently" if self.permanent_var.get() else "to the Trash"

    def _delete_files(self):
        path = self._collection()
        if not path or self.busy or self._traktor_open():
            return
        if not self.status.files:
            messagebox.showinfo(APP_NAME, "There are no stem files to delete.")
            return
        warning = (f"\n\nWARNING: {self.status.linked} tracks are still linked to stems. Without their files they won't "
                   f"load in Traktor until their links are removed too (tab 3, or 'Remove everything').") if self.status.linked else ""
        if not messagebox.askyesno(
            APP_NAME,
            f"Delete {self.status.files} stem files ({human_size(self.status.size)}), {self._where()}?{warning}",
            icon="warning",
        ):
            return
        permanent = self.permanent_var.get()

        def work():
            result = delete_stem_files(find_stem_files(default_stem_roots(docs_for(path))), permanent=permanent, log=self._log)
            self._log(describe_delete(result))
            return describe_delete(result)

        self._in_background(work, "Deleting the stem files failed")

    def _remove_everything(self):
        path = self._collection()
        if not path or self.busy or self._traktor_open():
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Remove ALL stems?\n\n  - clear the link on {self.status.linked} tracks in collection.nml (backup made first)\n"
            f"  - delete {self.status.files} stem files ({human_size(self.status.size)}), {self._where()}\n\n"
            f"Afterwards every track needs its stems created again. Continue?",
            icon="warning",
        ):
            return
        permanent = self.permanent_var.get()
        self._in_background(lambda: unlink_everything_and_delete(path, permanent, self._log), "Removing the stems failed")

    def _on_close(self):
        if self.running and not messagebox.askyesno(APP_NAME, "A run is in progress. Stop it and close?"):
            return
        self.cancel.set()
        self._save_config()
        self.root.destroy()


# ============================================================================
# COMMAND LINE
# ============================================================================

def _collection_or_none(args) -> Optional[str]:
    path = args.collection or find_collection_nml()
    if not path or not os.path.isfile(path):
        print("No Traktor collection.nml found - pass --collection <path> before the action.")
        return None
    return path


def _need_traktor_closed(apply: bool) -> bool:
    running = running_traktor() if apply else []
    if running:
        print(f"Close Traktor first ({', '.join(running)} is running).")
    return bool(running)


def cmd_status(args) -> int:
    path = _collection_or_none(args)
    if not path:
        return 1
    status = collection_status(path)
    print(path)
    print(status.headline)
    print(status.detail)
    for folder, _depth in status.roots:
        print(f"  stem folder: {folder}")
    return 0


def cmd_playlists(args) -> int:
    path = _collection_or_none(args)
    if not path:
        return 1
    for playlist in load_playlists(path):
        need = sum(1 for item in make_plan(playlist.rows, not args.include_existing) if not item.skip)
        print(f"{playlist.name}  ({len(playlist.rows)} tracks, {need} need stems)")
    return 0


def cmd_create(args) -> int:
    path = _collection_or_none(args)
    if not path:
        return 1
    playlist = next((p for p in load_playlists(path) if p.name == args.playlist), None)
    if not playlist:
        print(f"No playlist called '{args.playlist}'. Use the 'playlists' action to see them.")
        return 1
    plan = make_plan(playlist.rows, not args.include_existing, recent_stem_titles(docs_for(path)))
    todo = sum(1 for item in plan if not item.skip)
    print(f"{playlist.name}: {todo} of {len(plan)} tracks need stems (about {estimate(plan)})")
    for item in plan:
        print(f"  {item.position:>3}. {item.row.label}   -> {item.skip or 'create stems'}")
    if not args.apply:
        print("\nNothing sent to Traktor. Add --apply to do it.")
        return 0
    if sys.platform != "darwin":
        print("Pressing Traktor's keys is only set up for macOS.")
        return 1
    if not todo:
        return 0
    if not running_traktor():
        print("Open Traktor first.")
        return 1
    print("\nIn Traktor: open the playlist, sort it by its # column, click the FIRST track. Starting in 10 s "
          "(Ctrl+C to cancel) - leave the computer alone while it runs.")
    time.sleep(10)
    awake = keep_awake()
    try:
        summary = run_plan(plan, MacTraktorKeys(), LogWatcher(log_for(path)), RunOptions(job_timeout=args.timeout * 60), print)
    finally:
        if awake:
            awake.terminate()
    print("\n".join(summarize_run(summary)))
    return 0 if summary.complete else 1


def cmd_relink(args) -> int:
    path = _collection_or_none(args)
    if not path or _need_traktor_closed(args.apply):
        return 1
    backup = args.backup or find_stem_backup(path)
    if not backup or not os.path.isfile(backup):
        print("No collection.nml.bak-* with stem links found next to the collection - pass --backup <file>.")
        return 1
    print(f"{path}\nLinks taken from: {backup}")
    result = restore_collection(path, backup, stems_for(path), apply=args.apply)
    print(describe_restore(result))
    print("\n".join(restore_report(result.plan)))
    if result.backup_path:
        print(f"\nBackup of the file before this change: {result.backup_path}")
    elif result.plan.to_flag and not args.apply:
        print("\nNothing changed. Add --apply (Traktor closed) to re-link them.")
    return 0


def cmd_unlink(args) -> int:
    path = _collection_or_none(args)
    if not path or _need_traktor_closed(args.apply):
        return 1
    only_missing = not args.all
    result = clean_collection(path, apply=args.apply, only_missing_in=stems_for(path) if only_missing else None)
    print(path)
    print(describe_links(result, only_missing))
    if result.backup_path:
        print(f"Backup: {result.backup_path}")
    elif result.flagged and not args.apply:
        print("Nothing changed. Add --apply (Traktor closed) to do it.")
    return 0


def _delete(path: str, args) -> int:
    stems = find_stem_files(default_stem_roots(docs_for(path)))
    where = "permanently" if args.permanent else "to the Trash"
    print(f"{len(stems)} stem files, {human_size(sum(s.size for s in stems))} - would be deleted {where}")
    if not args.apply:
        for stem in stems[:10]:
            print(f"  {stem.path}")
        if len(stems) > 10:
            print(f"  ... and {len(stems) - 10} more")
        print("\nNothing changed. Add --apply (Traktor closed) to do it.")
        return 0
    if not stems:
        return 0
    result = delete_stem_files(stems, permanent=args.permanent, log=print)
    print(describe_delete(result))
    return 1 if result.failed else 0


def cmd_delete(args) -> int:
    path = _collection_or_none(args)
    if not path or _need_traktor_closed(args.apply):
        return 1
    try:
        return _delete(path, args)
    except CleanupError as e:
        print(f"Failed: {e}")
        return 1


def cmd_clean(args) -> int:
    path = _collection_or_none(args)
    if not path or _need_traktor_closed(args.apply):
        return 1
    if not args.apply:
        print(describe_links(clean_collection(path, apply=False), False))
        return _delete(path, args)
    try:
        print(unlink_everything_and_delete(path, args.permanent, print))
    except (CleanupError, ValueError, OSError) as e:
        print(f"Failed: {e}")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=APP_NAME, epilog="Every action only previews unless --apply is given.")
    parser.add_argument("--collection", help="collection.nml to use (default: the newest Traktor version's); goes before the action")
    actions = parser.add_subparsers(dest="action")
    actions.add_parser("status", help="how many tracks are linked and how many stem files there are").set_defaults(run=cmd_status)

    sub = actions.add_parser("playlists", help="list the playlists and how many tracks need stems")
    sub.add_argument("--include-existing", action="store_true")
    sub.set_defaults(run=cmd_playlists)

    sub = actions.add_parser("create", help="create stems for every track of a playlist (macOS)")
    sub.add_argument("playlist", help="playlist as listed by 'playlists', e.g. 'Sets/Peak'")
    sub.add_argument("--include-existing", action="store_true", help="also redo tracks that already have stems")
    sub.add_argument("--timeout", type=int, default=15, help="minutes to wait for one track before stopping (default 15)")
    sub.add_argument("--apply", action="store_true", help="really press the keys in Traktor")
    sub.set_defaults(run=cmd_create)

    sub = actions.add_parser("relink", help="set the link again on tracks whose stem file still exists")
    sub.add_argument("--backup", help="collection backup that still has the links (default: the newest collection.nml.bak-* that has them)")
    sub.add_argument("--apply", action="store_true")
    sub.set_defaults(run=cmd_relink)

    sub = actions.add_parser("unlink", help="remove stem links from the collection")
    sub.add_argument("--all", action="store_true", help="every link (default: only links without a stem file)")
    sub.add_argument("--apply", action="store_true")
    sub.set_defaults(run=cmd_unlink)

    for name, text in (("delete", "delete the stem files"), ("clean", "remove all links, then delete the stem files")):
        sub = actions.add_parser(name, help=text)
        sub.add_argument("--permanent", action="store_true", help="delete for good instead of moving to the Trash")
        sub.add_argument("--apply", action="store_true")
        sub.set_defaults(run=cmd_delete if name == "delete" else cmd_clean)
    return parser


def main():
    setup_shared_logging("traktor_stems")
    parser = build_parser()
    args, _unknown = parser.parse_known_args()
    if args.action:
        return args.run(args)

    root = tk.Tk()
    apply_category_icon(root)
    StemsUI(root, args.collection)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
