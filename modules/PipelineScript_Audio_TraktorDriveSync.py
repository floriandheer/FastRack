#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_TraktorDriveSync.py
Description: One-button transfer of the whole DJ setup between machines via a
             drive. Export copies the DJ Library music, the XML and the Traktor
             playlist export into one bundle folder. Import (on the other
             machine) copies the music, rewrites the XML's paths for that
             machine, and hands the playlists to Traktor Playlist Sync, which
             rewrites its own paths and shows what would change before merging.

Everything chosen in the window is remembered. The FastRack hub's one-click
button (--auto-run) replays whichever mode (export or import) was used last,
with the saved settings.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from dataclasses import dataclass, asdict
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Optional

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_appdata import get_appdata_path
from shared_color_button import ColorButton
from shared_mode_lock import filter_mode_change, make_header_title, add_header_lock
from shared_action_bar import ActionBar
import shared_traktor_bundle as bundle

logger = get_logger("traktor_drive_sync")

APP_NAME = "Traktor Drive Sync"
HEADER_COLOR = "#2c3e50"
CONFIG_FILE = os.path.join(str(get_appdata_path()), "traktor_drive_sync_config.json")
DEFAULT_BUNDLE_NAME = "DJ Transfer"

PLAYLIST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "PipelineScript_Audio_TraktorPlaylistSync.py")

Log = Callable[[str], None]


# ============================================================================
# SETTINGS
# ============================================================================

def _traktor_sync_defaults() -> tuple:
    """DJ Library folder / XML as already configured in Traktor Music Sync (Local), else the usual defaults."""
    music = os.path.join(os.path.expanduser("~"), "Music")
    library, xml = os.path.join(music, "DJ Library"), os.path.join(music, "DJ Library.xml")
    try:
        from PipelineScript_Audio_TraktorSync import ConfigManager as TraktorSyncConfig
        settings = TraktorSyncConfig().settings
        library = settings.dj_library_path_local or library
        xml = settings.export_xml_path_local or xml
    except Exception as e:  # defaults are fine if Traktor Music Sync isn't set up
        logger.info(f"No Traktor Music Sync settings to prefill from: {e}")
    return library, xml


@dataclass
class DriveSyncSettings:
    """Everything the window offers, saved between runs."""
    last_mode: str = ""              # "export" | "import" - what the one-click button replays
    mode_locked: bool = False        # keep the hub's out/in switch where it is
    export_library: str = ""
    export_xml: str = ""
    export_bundle: str = ""
    export_playlists: bool = True
    export_prune: bool = False
    import_bundle: str = ""
    import_library: str = ""
    import_xml: str = ""
    import_overwrite: bool = False
    import_review: bool = True

    @classmethod
    def from_dict(cls, data: dict) -> "DriveSyncSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class ConfigManager:
    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.settings = self._load()

    def _load(self) -> DriveSyncSettings:
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return DriveSyncSettings.from_dict(json.load(f))
        except (OSError, ValueError):
            pass
        library, xml = _traktor_sync_defaults()
        return DriveSyncSettings(export_library=library, export_xml=xml,
                                 import_library=library, import_xml=xml)

    def save_settings(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(asdict(self.settings), f, indent=2)
            return True
        except OSError as e:
            logger.warning(f"Could not save settings: {e}")
            return False

    def update_settings(self, **kwargs) -> None:
        for key, value in filter_mode_change(self.settings, kwargs).items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()


# ============================================================================
# THE WORK - shared by the window and the one-click run
# ============================================================================

def locate_bundle(saved: str) -> str:
    """The saved bundle folder if it exists, otherwise a 'DJ Transfer' bundle
    found on a connected drive (drive letters / volume names differ per machine)."""
    if saved and os.path.isdir(saved):
        return saved
    try:
        from PipelineScript_Audio_TraktorPlaylistSync import removable_drive_roots
        for root in removable_drive_roots():
            candidate = os.path.join(root, DEFAULT_BUNDLE_NAME)
            if bundle.read_manifest(candidate) is not None:
                return candidate
    except Exception as e:
        logger.info(f"Drive search failed: {e}")
    return saved


def default_bundle_dir() -> str:
    try:
        from PipelineScript_Audio_TraktorPlaylistSync import removable_drive_roots
        roots = removable_drive_roots()
    except Exception:
        roots = []
    return os.path.join(roots[0], DEFAULT_BUNDLE_NAME) if roots else ""


def export_playlists(tmp_dir: str, log: Log) -> Optional[str]:
    """Export Traktor playlists with Playlist Sync's saved settings; None (with a log line) if not possible."""
    try:
        import PipelineScript_Audio_TraktorPlaylistSync as playlist_sync
        path = playlist_sync.export_playlist_file(playlist_sync.ConfigManager(), output_dir=tmp_dir)
    except Exception as e:
        log(f"WARNING: Traktor playlists skipped: {e}")
        return None
    if path is None:
        log("WARNING: Traktor playlists skipped - open Traktor Playlist Sync once to set up "
            "collection.nml, 'This Machine' and the playlist selection.")
    return path


def run_export(settings: DriveSyncSettings, log: Log):
    """Copy music + XML (+ playlists) to the drive. Returns (ExportResult, playlists_included)."""
    with tempfile.TemporaryDirectory() as tmp:
        playlist_file = export_playlists(tmp, log) if settings.export_playlists else None
        result = bundle.export_bundle(
            settings.export_library, settings.export_xml, settings.export_bundle, log,
            playlist_file=playlist_file, machine_name=socket.gethostname(), prune=settings.export_prune,
        )
    m = result.music
    log(f"\nDone: {bundle.copy_summary(m)}, {m.pruned} removed, {m.errors} error(s).")
    return result, playlist_file is not None


def run_import(settings: DriveSyncSettings, log: Log):
    """Copy music from the bundle, localise the XML. Returns ImportResult."""
    result = bundle.import_bundle(
        settings.import_bundle, settings.import_library, settings.import_xml, log,
        overwrite=settings.import_overwrite,
    )
    m = result.music
    log(f"\nDone: {bundle.copy_summary(m)}, {m.errors} error(s).")
    return result


def open_playlist_review(playlist_file: str, bundle_dir: str) -> None:
    """Point Traktor Playlist Sync's Import tab at the bundle's playlist file and open it."""
    from PipelineScript_Audio_TraktorPlaylistSync import ConfigManager as PlaylistConfig
    PlaylistConfig().update_settings(import_file_path=playlist_file, import_source_dir=bundle_dir)
    subprocess.Popen([sys.executable, PLAYLIST_SCRIPT], cwd=os.path.dirname(PLAYLIST_SCRIPT))


# ============================================================================
# HEADLESS (--auto-run) - the hub's one-click button
# ============================================================================

def run_headless_export(config_manager) -> bool:
    settings = config_manager.settings
    settings.export_bundle = settings.export_bundle or default_bundle_dir()
    if not settings.export_bundle:
        logger.error("Export: no folder on the drive set and no drive found - open the tool to choose one.")
        return False
    drive = os.path.splitdrive(settings.export_bundle)[0]
    if drive and not os.path.isdir(drive + os.sep):
        logger.error(f"Export: drive {drive} isn't connected.")
        return False
    try:
        run_export(settings, logger.info)
    except Exception:
        logger.exception("Export failed")
        return False
    config_manager.update_settings(last_mode="export", export_bundle=settings.export_bundle)
    return True


def run_headless_import(config_manager) -> bool:
    settings = config_manager.settings
    settings.import_bundle = locate_bundle(settings.import_bundle)
    if bundle.read_manifest(settings.import_bundle) is None:
        logger.error(f"Import: no bundle found at '{settings.import_bundle}' or on a connected drive - "
                     f"connect the drive or open the tool to choose the folder.")
        return False
    try:
        result = run_import(settings, logger.info)
    except Exception:
        logger.exception("Import failed")
        return False
    config_manager.update_settings(last_mode="import", import_bundle=settings.import_bundle)
    if settings.import_review and result.playlist_file:
        try:
            open_playlist_review(result.playlist_file, settings.import_bundle)
            logger.info("Opened Traktor Playlist Sync to review the playlists.")
        except Exception:
            logger.exception("Could not open Traktor Playlist Sync - open it and use Import > 'Latest from drive...'")
    return True


def run_headless(args=None) -> bool:
    """Replay whichever mode (export or import) was last used, with saved settings."""
    config_manager = ConfigManager()
    if config_manager.settings.last_mode == "import":
        logger.info("Direct run: IMPORT (the mode last used in this tool) - copies the bundle from the drive "
                    "and adjusts the paths for this machine")
        return run_headless_import(config_manager)
    logger.info("Direct run: EXPORT (the mode last used in this tool) - copies music, XML and Traktor "
                "playlists to the drive")
    return run_headless_export(config_manager)


# ============================================================================
# WINDOW
# ============================================================================

class DriveSyncUI:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("820x720")
        self.root.minsize(700, 520)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.busy = False

        self.config_manager = ConfigManager()
        s = self.config_manager.settings

        header = tk.Frame(root, bg=HEADER_COLOR)
        header.grid(row=0, column=0, sticky="ew")
        header.configure(height=60)
        header.grid_propagate(False)
        self.mode_locked = add_header_lock(make_header_title(header, APP_NAME), self.config_manager)

        # Save Settings + the main buttons live under the header.
        self.action_bar = ActionBar(root)
        self.action_bar.grid(row=1, column=0, sticky="ew")
        self.action_bar.add_save_button(self._save_clicked)

        main = ttk.Frame(root)
        main.grid(row=2, column=0, sticky="nsew", padx=8, pady=8)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        self.notebook = ttk.Notebook(main)
        self.notebook.grid(row=0, column=0, sticky="ew")
        export_tab, import_tab = ttk.Frame(self.notebook), ttk.Frame(self.notebook)
        self.notebook.add(export_tab, text="Export to drive")
        self.notebook.add(import_tab, text="Import from drive")
        if s.last_mode == "import":
            self.notebook.select(import_tab)

        # --- Export tab ---
        self.exp_library = tk.StringVar(value=s.export_library)
        self.exp_xml = tk.StringVar(value=s.export_xml)
        self.exp_bundle = tk.StringVar(value=s.export_bundle or default_bundle_dir())
        self.exp_playlists = tk.BooleanVar(value=s.export_playlists)
        self.exp_prune = tk.BooleanVar(value=s.export_prune)

        self._path_row(export_tab, 0, "DJ Library folder:", self.exp_library, folder=True)
        self._path_row(export_tab, 1, "iTunes XML:", self.exp_xml, folder=False)
        self._path_row(export_tab, 2, "Folder on drive:", self.exp_bundle, folder=True)
        ttk.Checkbutton(export_tab, text="Include Traktor playlists (uses Traktor Playlist Sync's saved selection)",
                        variable=self.exp_playlists).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        ttk.Checkbutton(export_tab, text="Remove files from the drive that are no longer in my DJ Library",
                        variable=self.exp_prune).grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        self.export_btn = ColorButton(self.action_bar.group("export"), text="Export everything to drive",
                                      command=self._start_export, width=26, bg="#27ae60", fg="white",
                                      font=("", 9, "bold"))
        self.export_btn.pack(side=tk.LEFT)
        export_tab.columnconfigure(1, weight=1)

        # --- Import tab ---
        self.imp_bundle = tk.StringVar(value=locate_bundle(s.import_bundle))
        self.imp_library = tk.StringVar(value=s.import_library)
        self.imp_xml = tk.StringVar(value=s.import_xml)
        self.imp_overwrite = tk.BooleanVar(value=s.import_overwrite)
        self.imp_review = tk.BooleanVar(value=s.import_review)
        self.imp_info = tk.StringVar(value="")

        self._path_row(import_tab, 0, "Folder on drive:", self.imp_bundle, folder=True)
        ttk.Label(import_tab, textvariable=self.imp_info, foreground="gray", wraplength=720,
                  justify="left").grid(row=1, column=0, columnspan=3, sticky="w", padx=10)
        self._path_row(import_tab, 2, "My DJ Library folder:", self.imp_library, folder=True)
        self._path_row(import_tab, 3, "My iTunes XML:", self.imp_xml, folder=False)
        ttk.Checkbutton(import_tab, text="Overwrite music files that already exist",
                        variable=self.imp_overwrite).grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        ttk.Checkbutton(import_tab, text="Then open Traktor Playlist Sync to review and merge the playlists",
                        variable=self.imp_review).grid(row=5, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        self.import_btn = ColorButton(self.action_bar.group("import"), text="Import from drive",
                                      command=self._start_import, width=26, bg="#c0392b", fg="white",
                                      font=("", 9, "bold"))
        self.import_btn.pack(side=tk.LEFT)
        self.action_bar.follow(self.notebook, ["export", "import"])
        import_tab.columnconfigure(1, weight=1)
        self.imp_bundle.trace_add("write", lambda *a: self._refresh_bundle_info())
        self._refresh_bundle_info()

        # --- Log ---
        log_frame = ttk.LabelFrame(main, text="Log")
        log_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_frame, wrap=tk.WORD, height=12, font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=scroll.set)

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _path_row(self, parent, row, label, var, folder):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", padx=10, pady=6)
        ttk.Entry(parent, textvariable=var, width=60).grid(row=row, column=1, sticky="ew", padx=5, pady=6)

        def browse():
            if folder:
                chosen = filedialog.askdirectory(title=label.rstrip(":"), initialdir=var.get() or None)
            else:
                chosen = filedialog.asksaveasfilename(title=label.rstrip(":"), initialfile=os.path.basename(var.get()),
                                                      initialdir=os.path.dirname(var.get()) or None,
                                                      filetypes=[("XML", "*.xml")], confirmoverwrite=False)
            if chosen:
                var.set(chosen)

        ttk.Button(parent, text="Browse", command=browse).grid(row=row, column=2, padx=5, pady=6)

    def _refresh_bundle_info(self):
        self.imp_info.set(bundle.describe_bundle(self.imp_bundle.get()) if self.imp_bundle.get() else "")

    def _log(self, message: str):
        logger.info(message)
        self.root.after(0, lambda: (self.log_text.insert(tk.END, message + "\n"), self.log_text.see(tk.END)))

    def _set_busy(self, busy: bool):
        self.busy = busy
        state = tk.DISABLED if busy else tk.NORMAL
        self.root.after(0, lambda: (self.export_btn.config(state=state), self.import_btn.config(state=state)))

    def _collect(self) -> DriveSyncSettings:
        """The window's current choices as settings. last_mode is read fresh from
        disk: the hub's out/in switch may have changed it while this window was
        open, and saving a stale copy would undo that."""
        return DriveSyncSettings(
            last_mode=ConfigManager(self.config_manager.config_path).settings.last_mode,
            mode_locked=self.mode_locked.get(),
            export_library=self.exp_library.get().strip(), export_xml=self.exp_xml.get().strip(),
            export_bundle=self.exp_bundle.get().strip(), export_playlists=self.exp_playlists.get(),
            export_prune=self.exp_prune.get(),
            import_bundle=self.imp_bundle.get().strip(), import_library=self.imp_library.get().strip(),
            import_xml=self.imp_xml.get().strip(), import_overwrite=self.imp_overwrite.get(),
            import_review=self.imp_review.get(),
        )

    def _save(self, **extra):
        self.config_manager.settings = self._collect()
        self.config_manager.update_settings(**extra)

    def _save_clicked(self):
        self._save()
        self._log("Settings saved.")

    def _on_close(self):
        self._save()
        self.root.destroy()

    # ------------------------------------------------------------------
    # export
    # ------------------------------------------------------------------

    def _start_export(self):
        if self.busy:
            return
        target = self.exp_bundle.get().strip()
        if not target:
            messagebox.showerror("Export", "Pick the folder on the drive first.")
            return
        if self.exp_prune.get() and not messagebox.askyesno(
            "Export", f"Files in '{os.path.join(target, bundle.MUSIC_SUBFOLDER)}' that are no longer in your DJ Library "
                      f"will be deleted from the drive. Continue?"
        ):
            return
        self._save(last_mode="export")
        self.log_text.delete(1.0, tk.END)
        self._set_busy(True)
        threading.Thread(target=self._export_worker, daemon=True).start()

    def _export_worker(self):
        try:
            result, with_playlists = run_export(self._collect(), self._log)
            m = result.music
            self.root.after(0, lambda: messagebox.showinfo(
                "Export complete",
                f"Everything is on the drive:\n{self.exp_bundle.get()}\n\n"
                f"{bundle.copy_summary(m)}."
                + ("" if with_playlists or not self.exp_playlists.get()
                   else "\n\nTraktor playlists were NOT included - see the log."),
            ))
        except Exception as e:
            logger.exception("Export failed")
            self._log(f"\nERROR: {e}")
            self.root.after(0, lambda: messagebox.showerror("Export failed", str(e)))
        finally:
            self._set_busy(False)

    # ------------------------------------------------------------------
    # import
    # ------------------------------------------------------------------

    def _start_import(self):
        if self.busy:
            return
        if bundle.read_manifest(self.imp_bundle.get()) is None:
            messagebox.showerror("Import", "That folder isn't a bundle written by 'Export to drive'.")
            return
        if not self.imp_library.get() or not self.imp_xml.get():
            messagebox.showerror("Import", "Set your DJ Library folder and iTunes XML first.")
            return
        self._save(last_mode="import")
        self.log_text.delete(1.0, tk.END)
        self._set_busy(True)
        threading.Thread(target=self._import_worker, daemon=True).start()

    def _import_worker(self):
        try:
            result = run_import(self._collect(), self._log)
            self.root.after(0, lambda: self._after_import(result))
        except Exception as e:
            logger.exception("Import failed")
            self._log(f"\nERROR: {e}")
            self.root.after(0, lambda: messagebox.showerror("Import failed", str(e)))
        finally:
            self._set_busy(False)

    def _after_import(self, result):
        review = self.imp_review.get() and result.playlist_file
        messagebox.showinfo(
            "Import complete",
            f"{bundle.copy_summary(result.music)}. XML updated for this machine."
            + ("\n\nTraktor Playlist Sync will open to review the playlists." if review else
               "\n\nThis bundle has no Traktor playlists." if not result.playlist_file else ""),
        )
        if review:
            try:
                open_playlist_review(result.playlist_file, self.imp_bundle.get())
            except Exception as e:
                logger.exception("Could not open Traktor Playlist Sync")
                messagebox.showwarning("Playlists", f"Couldn't open Traktor Playlist Sync: {e}\n\n"
                                                    f"Open it and use Import > 'Latest from drive...'.")


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    setup_shared_logging("traktor_drive_sync")

    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--auto-run", action="store_true",
                        help="Run export or import (whichever was last used) immediately with saved settings, no window")
    args, _unknown = parser.parse_known_args()

    if args.auto_run:
        try:
            return 0 if run_headless() else 1
        except Exception:
            logger.exception("Unhandled error during headless run")
            return 1

    root = tk.Tk()
    apply_category_icon(root)
    DriveSyncUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
