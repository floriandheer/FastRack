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
"""

import os
import socket
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_appdata import get_appdata_path
from shared_color_button import ColorButton
import shared_traktor_bundle as bundle

logger = get_logger("traktor_drive_sync")

APP_NAME = "Traktor Drive Sync"
HEADER_COLOR = "#2c3e50"
CONFIG_FILE = os.path.join(str(get_appdata_path()), "traktor_drive_sync_config.json")
DEFAULT_BUNDLE_NAME = "DJ Transfer"

PLAYLIST_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "PipelineScript_Audio_TraktorPlaylistSync.py")


def _load_config() -> dict:
    import json
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_config(data: dict) -> None:
    import json
    try:
        os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning(f"Could not save settings: {e}")


def _traktor_sync_defaults() -> tuple:
    """DJ Library folder / XML as already configured in Traktor Sync (Local), else the usual defaults."""
    music = os.path.join(os.path.expanduser("~"), "Music")
    library, xml = os.path.join(music, "DJ Library"), os.path.join(music, "DJ Library.xml")
    try:
        from PipelineScript_Audio_TraktorSync import ConfigManager
        settings = ConfigManager().settings
        library = settings.dj_library_path_local or library
        xml = settings.export_xml_path_local or xml
    except Exception as e:  # defaults are fine if Traktor Sync isn't set up
        logger.info(f"No Traktor Sync settings to prefill from: {e}")
    return library, xml


def _default_bundle_dir() -> str:
    try:
        from PipelineScript_Audio_TraktorPlaylistSync import removable_drive_roots
        roots = removable_drive_roots()
    except Exception:
        roots = []
    return os.path.join(roots[0], DEFAULT_BUNDLE_NAME) if roots else ""


class DriveSyncUI:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("820x720")
        self.root.minsize(700, 520)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.busy = False

        self.config = _load_config()
        library, xml = _traktor_sync_defaults()

        header = tk.Frame(root, bg=HEADER_COLOR)
        header.grid(row=0, column=0, sticky="ew")
        tk.Label(header, text=APP_NAME, bg=HEADER_COLOR, fg="white", font=("Arial", 16, "bold")).pack(pady=12)

        main = ttk.Frame(root)
        main.grid(row=1, column=0, sticky="nsew", padx=8, pady=8)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        notebook = ttk.Notebook(main)
        notebook.grid(row=0, column=0, sticky="ew")
        export_tab, import_tab = ttk.Frame(notebook), ttk.Frame(notebook)
        notebook.add(export_tab, text="Export to drive")
        notebook.add(import_tab, text="Import from drive")

        # --- Export tab ---
        self.exp_library = tk.StringVar(value=self.config.get("export_library", library))
        self.exp_xml = tk.StringVar(value=self.config.get("export_xml", xml))
        self.exp_bundle = tk.StringVar(value=self.config.get("export_bundle") or _default_bundle_dir())
        self.exp_playlists = tk.BooleanVar(value=self.config.get("export_playlists", True))
        self.exp_prune = tk.BooleanVar(value=self.config.get("export_prune", False))

        self._path_row(export_tab, 0, "DJ Library folder:", self.exp_library, folder=True)
        self._path_row(export_tab, 1, "iTunes XML:", self.exp_xml, folder=False)
        self._path_row(export_tab, 2, "Folder on drive:", self.exp_bundle, folder=True)
        ttk.Checkbutton(export_tab, text="Include Traktor playlists (uses Traktor Playlist Sync's saved selection)",
                        variable=self.exp_playlists).grid(row=3, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        ttk.Checkbutton(export_tab, text="Remove files from the drive that are no longer in my DJ Library",
                        variable=self.exp_prune).grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=2)
        self.export_btn = ColorButton(export_tab, text="Export everything to drive", command=self._start_export,
                                      width=26, bg="#27ae60", fg="white", font=("", 9, "bold"))
        self.export_btn.grid(row=5, column=0, columnspan=3, sticky="e", padx=10, pady=10)
        export_tab.columnconfigure(1, weight=1)

        # --- Import tab ---
        library_dest = self.config.get("import_library", library)
        self.imp_bundle = tk.StringVar(value=self.config.get("import_bundle") or _default_bundle_dir())
        self.imp_library = tk.StringVar(value=library_dest)
        self.imp_xml = tk.StringVar(value=self.config.get("import_xml", xml))
        self.imp_overwrite = tk.BooleanVar(value=False)
        self.imp_review = tk.BooleanVar(value=True)
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
        self.import_btn = ColorButton(import_tab, text="Import from drive", command=self._start_import,
                                      width=26, bg="#c0392b", fg="white", font=("", 9, "bold"))
        self.import_btn.grid(row=6, column=0, columnspan=3, sticky="e", padx=10, pady=10)
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

    def _save(self):
        _save_config({
            "export_library": self.exp_library.get(), "export_xml": self.exp_xml.get(),
            "export_bundle": self.exp_bundle.get(), "export_playlists": self.exp_playlists.get(),
            "export_prune": self.exp_prune.get(), "import_bundle": self.imp_bundle.get(),
            "import_library": self.imp_library.get(), "import_xml": self.imp_xml.get(),
        })

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
        self._save()
        self.log_text.delete(1.0, tk.END)
        self._set_busy(True)
        threading.Thread(target=self._export_worker, daemon=True).start()

    def _export_worker(self):
        try:
            playlist_file = None
            with tempfile.TemporaryDirectory() as tmp:
                if self.exp_playlists.get():
                    playlist_file = self._export_playlists(tmp)
                result = bundle.export_bundle(
                    self.exp_library.get(), self.exp_xml.get(), self.exp_bundle.get(), self._log,
                    playlist_file=playlist_file, machine_name=socket.gethostname(), prune=self.exp_prune.get(),
                )
            m = result.music
            self._log(f"\nDone: {m.copied} copied, {m.skipped} unchanged, {m.pruned} removed, {m.errors} error(s).")
            self.root.after(0, lambda: messagebox.showinfo(
                "Export complete",
                f"Everything is on the drive:\n{self.exp_bundle.get()}\n\n"
                f"{m.copied} file(s) copied, {m.skipped} unchanged."
                + ("" if playlist_file else "\n\nTraktor playlists were NOT included - see the log."),
            ))
        except Exception as e:
            logger.exception("Export failed")
            self._log(f"\nERROR: {e}")
            self.root.after(0, lambda: messagebox.showerror("Export failed", str(e)))
        finally:
            self._set_busy(False)

    def _export_playlists(self, tmp_dir: str):
        """Export Traktor playlists with Playlist Sync's saved settings; None (with a log line) if not possible."""
        try:
            import PipelineScript_Audio_TraktorPlaylistSync as playlist_sync
            path = playlist_sync.export_playlist_file(playlist_sync.ConfigManager(), output_dir=tmp_dir)
        except Exception as e:
            self._log(f"WARNING: Traktor playlists skipped: {e}")
            return None
        if path is None:
            self._log("WARNING: Traktor playlists skipped - open Traktor Playlist Sync once to set up "
                      "collection.nml, 'This Machine' and the playlist selection.")
        return path

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
        self._save()
        self.log_text.delete(1.0, tk.END)
        self._set_busy(True)
        threading.Thread(target=self._import_worker, daemon=True).start()

    def _import_worker(self):
        try:
            result = bundle.import_bundle(
                self.imp_bundle.get(), self.imp_library.get(), self.imp_xml.get(), self._log,
                overwrite=self.imp_overwrite.get(),
            )
            m = result.music
            self._log(f"\nDone: {m.copied} copied, {m.skipped} unchanged, {m.errors} error(s).")
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
            f"{result.music.copied} file(s) copied, XML updated for this machine."
            + ("\n\nTraktor Playlist Sync will open to review the playlists." if review else
               "\n\nThis bundle has no Traktor playlists." if not result.playlist_file else ""),
        )
        if review:
            self._open_playlist_review(result.playlist_file)

    def _open_playlist_review(self, playlist_file: str):
        """Point Traktor Playlist Sync's Import tab at the bundle's playlist file and open it."""
        try:
            from PipelineScript_Audio_TraktorPlaylistSync import ConfigManager
            ConfigManager().update_settings(import_file_path=playlist_file,
                                            import_source_dir=self.imp_bundle.get(), last_mode="import")
            subprocess.Popen([sys.executable, PLAYLIST_SCRIPT], cwd=os.path.dirname(PLAYLIST_SCRIPT))
        except Exception as e:
            logger.exception("Could not open Traktor Playlist Sync")
            messagebox.showwarning("Playlists", f"Couldn't open Traktor Playlist Sync: {e}\n\n"
                                                f"Open it and use Import > 'Latest from drive...'.")


def main():
    setup_shared_logging("traktor_drive_sync")
    root = tk.Tk()
    apply_category_icon(root)
    DriveSyncUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
