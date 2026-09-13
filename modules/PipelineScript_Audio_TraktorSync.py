#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Traktor_SyncPlaylists.py
Description: UI wrapper for iTunes/Music Playlist Sync functionality with improved playlist selection and FLAC conversion with album art
"""

import os
import sys
import time
import datetime
import argparse
import threading
import shutil
import logging
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from shared_window_icon import apply_category_icon
from shared_scrollable_frame import ScrollableFrame, safe_bind_touchpad_scroll
from shared_color_button import ColorButton
import xml.etree.ElementTree as ET
import urllib.parse
import re
import subprocess
import tempfile
import copy
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, asdict, field

# Setup logging using shared utility
from shared_logging import get_logger, setup_logging as setup_shared_logging

# Get logger reference (configured in main())
logger = get_logger("traktor_sync")
VALID_EXTENSIONS = {'.mp3', '.flac', '.wav', '.aiff', '.m4a', '.ogg', '.opus'}

def _appdata_dir() -> str:
    """Platform-appropriate PipelineManager app-data folder (same convention
    as rak_settings._get_appdata_path / the Traktor Playlist Sync tool)."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return os.path.join(home, "AppData", "Local", "PipelineManager")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "PipelineManager")
    return os.path.join(home, ".local", "share", "PipelineManager")


# Configuration paths
APP_DATA_DIR = _appdata_dir()
CONFIG_FILE = os.path.join(APP_DATA_DIR, "traktor_sync_config.json")

# Built-in preset that mirrors the Auto Select rule (digits 1-9 prefix).
# Resolved dynamically against the loaded playlist list, never stored.
DEFAULT_PRESET_NAME = "Default (Auto)"


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class SyncSettings:
    """Sync configuration settings."""
    itunes_xml_path: str = ""
    dj_library_path: str = ""
    export_xml_path: str = ""
    target_os: str = "Local"
    mac_dj_library_path: str = "/Users/flori/Music/DJ Library"
    debug_mode: bool = True
    skip_existing: bool = True
    overwrite_all: bool = False
    delete_removed: bool = True
    selected_playlists: List[str] = field(default_factory=list)
    selection_mode: str = "include"
    playlist_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    active_preset: str = ""
    last_mode: str = ""
    import_source_dir: str = ""
    import_skip_existing: bool = True
    import_overwrite_all: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SyncSettings':
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


# ============================================================================
# CONFIGURATION MANAGER
# ============================================================================

class ConfigManager:
    """Manages persistent configuration settings."""

    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.settings = self._load_settings()

    def _load_settings(self) -> SyncSettings:
        """Load settings from file or create defaults."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"Loaded settings from {self.config_path}")
                    return SyncSettings.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load settings: {e}, using defaults")

        return SyncSettings()

    def save_settings(self) -> bool:
        """Save current settings to file."""
        try:
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings.to_dict(), f, indent=2)
            logger.info(f"Settings saved to {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    def update_settings(self, **kwargs) -> None:
        """Update specific settings."""
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()


# ============================================================================
# IMPORT SOURCE DETECTION - pure function, no Tk dependency
# ============================================================================

def detect_import_source(source_dir: str) -> "tuple[Optional[str], Optional[str]]":
    """Given a folder (e.g. a USB stick, or a shared/synced folder written by
    this same tool's Export on another machine), locate the DJ Library
    folder and its DJ Library.xml inside it.

    Returns (dj_library_dir, xml_path) - either may be None if not found.
    """
    if not source_dir or not os.path.isdir(source_dir):
        return None, None

    try:
        entries = os.listdir(source_dir)
    except OSError:
        entries = []

    dj_library_dir = None
    for entry in entries:
        full = os.path.join(source_dir, entry)
        if os.path.isdir(full) and entry.strip().lower() == "dj library":
            dj_library_dir = full
            break

    if dj_library_dir is None:
        # No "DJ Library" subfolder - maybe the picked folder *is* the DJ
        # Library folder itself (audio files sit directly inside it).
        try:
            if any(
                os.path.splitext(f)[1].lower() in VALID_EXTENSIONS
                for f in entries if os.path.isfile(os.path.join(source_dir, f))
            ):
                dj_library_dir = source_dir
        except OSError:
            pass

    xml_candidates = [os.path.join(source_dir, e) for e in entries if e.lower().endswith(".xml")]
    xml_path = None
    exact = [p for p in xml_candidates if os.path.basename(p).lower() == "dj library.xml"]
    if exact:
        xml_path = exact[0]
    elif len(xml_candidates) == 1:
        xml_path = xml_candidates[0]
    elif xml_candidates:
        xml_path = max(xml_candidates, key=os.path.getmtime)

    return dj_library_dir, xml_path


class PlaylistSyncUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Traktor Sync")
        self.root.geometry("900x850")
        self.root.minsize(900, 500)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        header_frame = tk.Frame(self.root, bg="#2c3e50", height=60)
        header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        header_frame.grid_propagate(False)

        title_label = tk.Label(header_frame, text="Traktor Sync",
                             font=("Arial", 16, "bold"), fg="white", bg="#2c3e50")
        title_label.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Wrapped in a ScrollableFrame rather than gridded directly onto
        # root: this window's natural content height (config panel + the
        # analysis/sync/XML results tabs) can exceed a laptop's usable
        # screen height, which would otherwise strand the sync buttons and
        # results off-screen with no way to reach them.
        scroll = ScrollableFrame(self.root)
        scroll.grid(row=1, column=0, sticky="nsew")
        self._body_scroll = scroll
        # ScrollableFrame normally only activates its wheel binding while
        # the pointer is directly over its own background (<Enter>/<Leave>
        # on the outer frame) - but this window is almost entirely covered
        # by native ttk widgets (Treeview, Notebook, etc.), which own real
        # windows of their own, so that crossing event essentially never
        # fires here. Replace it with a permanent binding instead (see
        # _on_body_mouse_wheel).
        scroll.unbind("<Enter>")
        scroll.unbind("<Leave>")

        main_frame = ttk.Frame(scroll.get_frame())
        main_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(0, weight=1)

        self.config_manager = ConfigManager()

        notebook = ttk.Notebook(main_frame)
        notebook.grid(row=0, column=0, sticky="nsew")
        self._main_notebook = notebook
        # See create_results_panel()'s comment on self.results_notebook for
        # why the TNotebook class bindings need neutralizing on Tk 9 - that
        # call already does it globally (bind_class), but each Notebook
        # instance still needs its own forwarding binding.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            notebook.bind(seq, self._on_notebook_mouse_wheel)
        safe_bind_touchpad_scroll(notebook.bind, "<TouchpadScroll>", self._on_notebook_touchpad_scroll)
        notebook.bind("<<NotebookTabChanged>>", lambda e: self._update_import_destination_info())

        export_tab = ttk.Frame(notebook)
        notebook.add(export_tab, text="Export")
        export_tab.columnconfigure(0, weight=1)
        export_tab.rowconfigure(2, weight=1)
        self.create_config_panel(export_tab)
        self.create_results_panel(export_tab)

        import_tab = ttk.Frame(notebook)
        notebook.add(import_tab, text="Import")
        self.create_import_tab(import_tab)

        self.root.bind_all("<MouseWheel>", self._on_body_mouse_wheel)
        self.root.bind_all("<Button-4>", self._on_body_mouse_wheel)
        self.root.bind_all("<Button-5>", self._on_body_mouse_wheel)
        safe_bind_touchpad_scroll(self.root.bind_all, "<TouchpadScroll>", self._on_body_touchpad_scroll)

        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1,
                                 relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=2, column=0, sticky="ew")

        self.syncing = False
        self.importing = False
        self._import_detected_dj_library_dir = None
        self._import_detected_xml_path = None
        self.initialize_default_paths()
        self.all_playlists = []  # Will be populated when analyzing XML
        self.playlist_data = {}  # Store playlist metadata
        self.itunes_root = None  # Store the XML root for reuse

        # Reload playlists when the iTunes XML changes on disk (e.g. iTunes rewrote it).
        self.root.bind("<FocusIn>", self._on_window_focus, add="+")

    def _pointer_over_self_scrolling_widget(self, event):
        """True while the pointer is over a widget that already scrolls
        itself (the playlist tree, the analysis/sync/XML/import result text
        boxes) - those should handle wheel/trackpad input natively rather
        than scrolling the window body."""
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        self_scrolling_widgets = (
            getattr(self, "playlist_tree", None),
            getattr(self, "analysis_text", None),
            getattr(self, "sync_text", None),
            getattr(self, "xml_text", None),
            getattr(self, "import_text", None),
        )
        w = widget
        while w is not None:
            if w in self_scrolling_widgets:
                return True
            w = w.master
        return False

    def _on_body_mouse_wheel(self, event):
        if not self._pointer_over_self_scrolling_widget(event):
            self._body_scroll._on_mouse_wheel(event)

    def _on_body_touchpad_scroll(self, event):
        if not self._pointer_over_self_scrolling_widget(event):
            self._body_scroll._on_touchpad_scroll(event)

    def _on_notebook_mouse_wheel(self, event):
        self._on_body_mouse_wheel(event)
        return "break"

    def _on_notebook_touchpad_scroll(self, event):
        self._on_body_touchpad_scroll(event)
        return "break"

    def initialize_default_paths(self):
        """Initialize paths from saved settings or use defaults."""
        settings = self.config_manager.settings

        # Try to load iTunes XML from saved settings first
        if settings.itunes_xml_path and os.path.exists(settings.itunes_xml_path):
            self.itunes_xml_var.set(settings.itunes_xml_path)
        else:
            # Fall back to default paths - "~" resolves to the right home
            # directory on both Windows and macOS, so the same candidate
            # list works cross-platform without an OS branch. Includes both
            # the legacy iTunes export and modern macOS Music.app's XML
            # (only written if "Share Library XML with other apps" is on).
            possible_itunes_paths = [
                "~/Music/iTunes/iTunes Music Library.xml",
                "~/Music/iTunes/iTunes Library.xml",
                "~/Music/Music/Music Library.xml",
                "~/My Music/iTunes/iTunes Music Library.xml",
                "M:\\iTunes Music Library.xml",
            ]

            for path in possible_itunes_paths:
                expanded_path = os.path.expanduser(path)
                if os.path.exists(expanded_path):
                    self.itunes_xml_var.set(expanded_path)
                    break

        # Load DJ Library and Export XML paths - default to "~/Music/DJ
        # Library" on any OS, matching the External-mode Mac path already
        # hardcoded below (mac_dj_library_path) for the shared-folder setup.
        default_dj_path = os.path.join(os.path.expanduser("~"), "Music")
        if settings.dj_library_path:
            self.dj_library_var.set(settings.dj_library_path)
        else:
            self.dj_library_var.set(os.path.join(default_dj_path, 'DJ Library'))

        if settings.export_xml_path:
            self.export_xml_var.set(settings.export_xml_path)
        else:
            self.export_xml_var.set(os.path.join(default_dj_path, 'DJ Library.xml'))

        # Load other settings
        self.target_os_var.set(settings.target_os)
        self.mac_dj_library_var.set(settings.mac_dj_library_path)
        self.debug_var.set(settings.debug_mode)
        self.skip_existing_var.set(settings.skip_existing)
        self.overwrite_all_var.set(settings.overwrite_all)
        self.delete_removed_var.set(settings.delete_removed)
        self.selection_mode.set(settings.selection_mode)

        # Update Mac paths visibility based on loaded target_os
        self.on_target_os_changed()

        # Load Import tab settings and refresh its auto-detected info.
        self.import_source_dir_var.set(settings.import_source_dir)
        self.import_skip_existing_var.set(settings.import_skip_existing)
        self.import_overwrite_all_var.set(settings.import_overwrite_all)
        self._run_import_autodetect()
        self._update_import_destination_info()

    def create_config_panel(self, parent):
        # === OPTIONS (first, before configuration) ===
        options_frame = ttk.LabelFrame(parent, text="Options")
        options_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        # Destination Selection
        dest_row = ttk.Frame(options_frame)
        dest_row.grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=5)

        ttk.Label(dest_row, text="Destination:").pack(side=tk.LEFT, padx=(0, 10))
        self.target_os_var = tk.StringVar(value="Local")
        ttk.Radiobutton(dest_row, text="Local", variable=self.target_os_var,
                        value="Local", command=self.on_target_os_changed).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Radiobutton(dest_row, text="External", variable=self.target_os_var,
                        value="External", command=self.on_target_os_changed).pack(side=tk.LEFT)

        self.os_info_label = ttk.Label(dest_row, text="", foreground="blue", font=("Arial", 9))
        self.os_info_label.pack(side=tk.LEFT, padx=(15, 0))

        self.debug_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Show detailed info for missing tracks", variable=self.debug_var).grid(row=1, column=0, sticky="w", padx=10, pady=5)

        # File handling mode - using mutually exclusive checkboxes
        file_mode_label = ttk.Label(options_frame, text="For files that already exist:", font=('', 9, 'bold'))
        file_mode_label.grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 2))

        # Frame to hold checkboxes side by side
        file_mode_frame = ttk.Frame(options_frame)
        file_mode_frame.grid(row=3, column=0, columnspan=3, sticky="w", padx=30, pady=2)

        self.skip_existing_var = tk.BooleanVar(value=True)
        self.overwrite_all_var = tk.BooleanVar(value=False)

        def on_skip_toggle():
            if self.skip_existing_var.get():
                self.overwrite_all_var.set(False)
            elif not self.overwrite_all_var.get():
                self.skip_existing_var.set(True)

        def on_overwrite_toggle():
            if self.overwrite_all_var.get():
                self.skip_existing_var.set(False)
            elif not self.skip_existing_var.get():
                self.overwrite_all_var.set(True)

        skip_checkbox = ttk.Checkbutton(
            file_mode_frame,
            text="\u23ed  Skip copying",
            variable=self.skip_existing_var,
            command=on_skip_toggle
        )
        skip_checkbox.grid(row=0, column=0, sticky="w", padx=(0, 20))

        overwrite_checkbox = ttk.Checkbutton(
            file_mode_frame,
            text="\u267b  Overwrite files",
            variable=self.overwrite_all_var,
            command=on_overwrite_toggle
        )
        overwrite_checkbox.grid(row=0, column=1, sticky="w")

        # Convert to FLAC is now always enabled (no checkbox)
        self.convert_flac_var = tk.BooleanVar(value=True)

        # Embed album art is now always enabled (no checkbox)
        self.preserve_album_art_var = tk.BooleanVar(value=True)

        self.delete_removed_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options_frame, text="Delete removed tracks (tracks no longer in any playlists)", variable=self.delete_removed_var).grid(row=4, column=0, columnspan=3, sticky="w", padx=10, pady=5)

        # === CONFIGURATION (file paths) ===
        config_frame = ttk.LabelFrame(parent, text="Configuration")
        config_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        config_frame.columnconfigure(1, weight=1)

        current_row = 0

        # --- Source ---
        source_label = ttk.Label(config_frame, text="Source", font=("Arial", 9, "bold"))
        source_label.grid(row=current_row, column=0, columnspan=3, sticky="w", padx=10, pady=(10, 0))

        current_row += 1

        # iTunes XML path (source)
        ttk.Label(config_frame, text="iTunes XML:").grid(row=current_row, column=0, sticky="w", padx=10, pady=10)
        self.itunes_xml_var = tk.StringVar()
        self._itunes_autoload_after_id = None
        self._loaded_xml_path = None
        self._loaded_xml_mtime = None
        self.itunes_xml_var.trace_add('write', lambda *args: self._schedule_autoload_playlists())
        ttk.Entry(config_frame, textvariable=self.itunes_xml_var, width=50).grid(row=current_row, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(config_frame, text="Browse", command=self.browse_itunes_xml).grid(row=current_row, column=2, padx=5, pady=10)

        current_row += 1

        ttk.Separator(config_frame, orient="horizontal").grid(row=current_row, column=0, columnspan=3, sticky="ew", padx=10, pady=5)

        current_row += 1

        # --- Destination ---
        dest_label = ttk.Label(config_frame, text="Destination", font=("Arial", 9, "bold"))
        dest_label.grid(row=current_row, column=0, columnspan=3, sticky="w", padx=10, pady=(5, 0))

        current_row += 1

        # DJ Library path (destination)
        ttk.Label(config_frame, text="DJ Library Folder:").grid(row=current_row, column=0, sticky="w", padx=10, pady=10)
        self.dj_library_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.dj_library_var, width=50).grid(row=current_row, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(config_frame, text="Browse", command=self.browse_dj_library).grid(row=current_row, column=2, padx=5, pady=10)

        current_row += 1

        # Export XML path (destination)
        ttk.Label(config_frame, text="Export XML:").grid(row=current_row, column=0, sticky="w", padx=10, pady=10)
        self.export_xml_var = tk.StringVar()
        ttk.Entry(config_frame, textvariable=self.export_xml_var, width=50).grid(row=current_row, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(config_frame, text="Browse", command=self.browse_export_xml).grid(row=current_row, column=2, padx=5, pady=10)

        current_row += 1

        # External-specific paths frame (initially hidden)
        self.mac_paths_frame = ttk.LabelFrame(config_frame, text="External Destination Paths (for XML references)")
        self.mac_paths_frame.grid(row=current_row, column=0, columnspan=3, sticky="ew", padx=10, pady=10)
        self.mac_paths_frame.columnconfigure(1, weight=1)
        self.mac_paths_frame.grid_remove()  # Hidden by default

        # External DJ Library path
        ttk.Label(self.mac_paths_frame, text="External DJ Library:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.mac_dj_library_var = tk.StringVar(value="/Users/flori/Music/DJ Library")
        ttk.Entry(self.mac_paths_frame, textvariable=self.mac_dj_library_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=10)

        ttk.Label(self.mac_paths_frame, text="Example: /Users/flori/Music/DJ Library",
                 foreground="gray", font=("Arial", 8)).grid(row=1, column=1, sticky="w", padx=5, pady=0)

        current_row += 1

        # IMPROVED Playlist selection section
        playlist_frame = ttk.LabelFrame(config_frame, text="Playlist Selection")
        playlist_frame.grid(row=current_row, column=0, columnspan=3, sticky="nsew", padx=10, pady=10)
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(2, weight=1)
        
        # Selection mode
        mode_frame = ttk.Frame(playlist_frame)
        mode_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        
        ttk.Label(mode_frame, text="Selection Mode:").grid(row=0, column=0, sticky="w", padx=5)
        self.selection_mode = tk.StringVar(value="include")
        ttk.Radiobutton(mode_frame, text="Include Selected", variable=self.selection_mode, 
                       value="include", command=self.update_selection_mode).grid(row=0, column=1, padx=10)
        ttk.Radiobutton(mode_frame, text="Exclude Selected", variable=self.selection_mode, 
                       value="exclude", command=self.update_selection_mode).grid(row=0, column=2, padx=10)
        
        # Filter and stats
        filter_frame = ttk.Frame(playlist_frame)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        filter_frame.columnconfigure(1, weight=1)
        
        ttk.Label(filter_frame, text="Filter:").grid(row=0, column=0, sticky="w", padx=5)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add('write', self.filter_playlists)
        ttk.Entry(filter_frame, textvariable=self.filter_var).grid(row=0, column=1, sticky="ew", padx=5)
        
        # Selection summary label
        self.selection_summary = tk.StringVar()
        self.selection_summary.set("No playlists loaded")
        ttk.Label(filter_frame, textvariable=self.selection_summary, font=("Arial", 9), 
                 foreground="blue").grid(row=0, column=2, padx=10, sticky="e")
        
        # Single listbox with checkboxes (simulated with selection)
        list_frame = ttk.Frame(playlist_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        
        # Create Treeview for better playlist display
        self.playlist_tree = ttk.Treeview(list_frame, columns=("tracks", "new_tracks", "type"), show="tree headings", height=10)
        self.playlist_tree.grid(row=0, column=0, sticky="nsew")

        # Configure columns
        self.playlist_tree.heading("#0", text="Playlist Name")
        self.playlist_tree.heading("tracks", text="Tracks")
        self.playlist_tree.heading("new_tracks", text="New")
        self.playlist_tree.heading("type", text="Type")

        self.playlist_tree.column("#0", width=300)
        self.playlist_tree.column("tracks", width=80, anchor="center")
        self.playlist_tree.column("new_tracks", width=80, anchor="center")
        self.playlist_tree.column("type", width=100, anchor="center")
        
        # Scrollbar for treeview
        tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.playlist_tree.config(yscrollcommand=tree_scroll.set)
        
        # Presets frame
        preset_frame = ttk.Frame(playlist_frame)
        preset_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=5)
        preset_frame.columnconfigure(1, weight=1)

        ttk.Label(preset_frame, text="Preset:").grid(row=0, column=0, sticky="w", padx=5)
        self.preset_var = tk.StringVar(value=self.config_manager.settings.active_preset)
        self.preset_combo = ttk.Combobox(preset_frame, textvariable=self.preset_var, state="readonly")
        self.preset_combo.grid(row=0, column=1, sticky="ew", padx=5)
        self.preset_combo.bind("<<ComboboxSelected>>", lambda e: self._load_preset())
        ttk.Button(preset_frame, text="Save As", command=self._save_preset_as).grid(row=0, column=2, padx=2)
        ttk.Button(preset_frame, text="Save", command=self._overwrite_preset).grid(row=0, column=3, padx=2)
        ttk.Button(preset_frame, text="Delete", command=self._delete_preset).grid(row=0, column=4, padx=2)
        self._refresh_preset_combo()

        # Buttons frame
        btn_frame = ttk.Frame(playlist_frame)
        btn_frame.grid(row=4, column=0, sticky="ew", pady=5)
        btn_frame.columnconfigure(4, weight=1)

        ttk.Button(btn_frame, text="Select All", command=self.select_all_playlists).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Clear All", command=self.clear_all_playlists).grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="Auto Select", command=self.auto_select_playlists).grid(row=0, column=2, padx=5)
        ttk.Button(btn_frame, text="Calculate New Tracks", command=self.calculate_new_tracks_for_selected).grid(row=0, column=3, padx=5)
        ttk.Button(btn_frame, text="Preview Selection", command=self.preview_selection).grid(row=0, column=4, padx=5)
        
        # Main action buttons
        main_btn_frame = ttk.Frame(config_frame)
        main_btn_frame.grid(row=current_row+1, column=0, columnspan=3, sticky="ew", pady=10)
        main_btn_frame.columnconfigure(1, weight=1)

        self.save_settings_btn = ttk.Button(main_btn_frame, text="Save Settings", command=self._save_settings, width=15)
        self.save_settings_btn.grid(row=0, column=0, padx=10)

        # Action buttons aligned together on the right
        action_btns = ttk.Frame(main_btn_frame)
        action_btns.grid(row=0, column=1, sticky="e", padx=10)

        self.export_xml_btn = ColorButton(action_btns, text="Export XML", command=self.export_xml_only, width=15, bg="yellow", fg="black", font=('', 9, 'bold'))
        self.export_xml_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.sync_btn = ColorButton(action_btns, text="Start Sync", command=self.start_sync, width=15, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.cancel_btn = ColorButton(action_btns, text="Cancel", command=self._cancel_sync, width=15, bg="red", fg="white", font=('', 9, 'bold'), state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT)

        # Initialize Mac paths visibility
        self.on_target_os_changed()

    def on_target_os_changed(self, event=None):
        """Handle target OS selection change"""
        target_os = self.target_os_var.get()

        if target_os == "External":
            self.mac_paths_frame.grid()
            self.os_info_label.config(text="Export to USB/local, then copy to external device")
        else:
            self.mac_paths_frame.grid_remove()
            self.os_info_label.config(text="")

    # ------------------------------------------------------------------
    # Import tab: bring a DJ Library folder + XML exported (by this same
    # tool, on another machine) into this machine's own DJ Library.
    # ------------------------------------------------------------------

    def create_import_tab(self, tab):
        tab.columnconfigure(0, weight=1)

        # --- Source ---
        source_frame = ttk.LabelFrame(tab, text="Source: folder to import from (e.g. a USB stick or shared folder)")
        source_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        source_frame.columnconfigure(1, weight=1)

        ttk.Label(source_frame, text="Folder:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.import_source_dir_var = tk.StringVar()
        self._import_autodetect_after_id = None
        self.import_source_dir_var.trace_add('write', lambda *a: self._schedule_import_autodetect())
        ttk.Entry(source_frame, textvariable=self.import_source_dir_var, width=50).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(source_frame, text="Browse", command=self._browse_import_source).grid(row=0, column=2, padx=5, pady=10)

        self.import_info_var = tk.StringVar(value="No folder selected")
        ttk.Label(source_frame, textvariable=self.import_info_var, foreground="gray", wraplength=820, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10)
        )

        # --- Destination ---
        dest_frame = ttk.LabelFrame(tab, text="Destination")
        dest_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        dest_frame.columnconfigure(0, weight=1)
        self.import_dest_info_var = tk.StringVar()
        ttk.Label(dest_frame, textvariable=self.import_dest_info_var, foreground="gray", wraplength=820, justify="left").grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 0)
        )
        ttk.Label(
            dest_frame, text="Set on the Export tab (DJ Library Folder / Export XML).",
            foreground="gray", font=("Arial", 8),
        ).grid(row=1, column=0, sticky="w", padx=10, pady=(0, 10))

        # --- Options ---
        options_frame = ttk.LabelFrame(tab, text="Options")
        options_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)

        ttk.Label(options_frame, text="For files that already exist:", font=('', 9, 'bold')).grid(
            row=0, column=0, sticky="w", padx=10, pady=(10, 2)
        )

        file_mode_frame = ttk.Frame(options_frame)
        file_mode_frame.grid(row=1, column=0, sticky="w", padx=30, pady=(2, 10))

        self.import_skip_existing_var = tk.BooleanVar(value=True)
        self.import_overwrite_all_var = tk.BooleanVar(value=False)

        def on_import_skip_toggle():
            if self.import_skip_existing_var.get():
                self.import_overwrite_all_var.set(False)
            elif not self.import_overwrite_all_var.get():
                self.import_skip_existing_var.set(True)

        def on_import_overwrite_toggle():
            if self.import_overwrite_all_var.get():
                self.import_skip_existing_var.set(False)
            elif not self.import_skip_existing_var.get():
                self.import_overwrite_all_var.set(True)

        ttk.Checkbutton(
            file_mode_frame, text="⏭  Skip existing files", variable=self.import_skip_existing_var,
            command=on_import_skip_toggle,
        ).grid(row=0, column=0, sticky="w", padx=(0, 20))
        ttk.Checkbutton(
            file_mode_frame, text="♻  Overwrite files", variable=self.import_overwrite_all_var,
            command=on_import_overwrite_toggle,
        ).grid(row=0, column=1, sticky="w")

        # --- Actions ---
        action_frame = ttk.Frame(tab)
        action_frame.grid(row=3, column=0, sticky="ew", pady=(5, 10))
        action_frame.columnconfigure(0, weight=1)
        tab.rowconfigure(4, weight=1)

        self.import_btn = ColorButton(action_frame, text="Import", command=self.start_import, width=15,
                                       bg="#c0392b", fg="white", font=('', 9, 'bold'), state=tk.DISABLED)
        self.import_btn.pack(side=tk.RIGHT, padx=10, anchor="n")

        # --- Log ---
        log_frame = ttk.LabelFrame(tab, text="Import Log")
        log_frame.grid(row=4, column=0, sticky="nsew", padx=5, pady=(0, 5))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)

        self.import_text = tk.Text(log_frame, wrap=tk.WORD, height=8, font=("Consolas", 9))
        self.import_text.grid(row=0, column=0, sticky="nsew")
        import_log_scroll = ttk.Scrollbar(log_frame, command=self.import_text.yview)
        import_log_scroll.grid(row=0, column=1, sticky="ns")
        self.import_text.config(yscrollcommand=import_log_scroll.set)

    def _browse_import_source(self):
        directory = filedialog.askdirectory(title="Select folder to import from")
        if directory:
            self.import_source_dir_var.set(directory)

    def _schedule_import_autodetect(self, delay=400):
        """Debounced auto-detect of the DJ Library folder/XML when the import
        source folder path changes."""
        if self._import_autodetect_after_id is not None:
            try:
                self.root.after_cancel(self._import_autodetect_after_id)
            except Exception:
                pass
        self._import_autodetect_after_id = self.root.after(delay, self._run_import_autodetect)

    def _run_import_autodetect(self):
        self._import_autodetect_after_id = None
        source_dir = self.import_source_dir_var.get()
        dj_library_dir, xml_path = detect_import_source(source_dir)
        self._import_detected_dj_library_dir = dj_library_dir
        self._import_detected_xml_path = xml_path

        if not source_dir:
            self.import_info_var.set("No folder selected")
            self.import_btn.config(state=tk.DISABLED)
            return
        if not os.path.isdir(source_dir):
            self.import_info_var.set("Folder not found")
            self.import_btn.config(state=tk.DISABLED)
            return

        parts = []
        if dj_library_dir:
            try:
                track_count = sum(
                    1 for f in os.listdir(dj_library_dir)
                    if os.path.isfile(os.path.join(dj_library_dir, f))
                    and os.path.splitext(f)[1].lower() in VALID_EXTENSIONS
                )
            except OSError:
                track_count = 0
            parts.append(f"DJ Library folder: {dj_library_dir} ({track_count} track(s))")
        else:
            parts.append("DJ Library folder: not found")

        parts.append(f"XML: {os.path.basename(xml_path)}" if xml_path else "XML: not found")
        self.import_info_var.set(" | ".join(parts))
        self.import_btn.config(state=tk.NORMAL if (dj_library_dir and xml_path) else tk.DISABLED)

    def _update_import_destination_info(self):
        if not hasattr(self, "import_dest_info_var"):
            return
        dj_library = self.dj_library_var.get() if hasattr(self, "dj_library_var") else ""
        export_xml = self.export_xml_var.get() if hasattr(self, "export_xml_var") else ""
        if dj_library and export_xml:
            self.import_dest_info_var.set(f"Will import into: {dj_library}\nXML target: {export_xml}")
        else:
            self.import_dest_info_var.set("Set the DJ Library Folder and Export XML path on the Export tab first.")

    def start_import(self):
        if self.importing or self.syncing:
            return

        dj_library_dir = self._import_detected_dj_library_dir
        xml_path = self._import_detected_xml_path
        if not dj_library_dir or not os.path.isdir(dj_library_dir):
            messagebox.showerror("Import", "No DJ Library folder detected in that source folder.")
            return
        if not xml_path or not os.path.exists(xml_path):
            messagebox.showerror("Import", "No DJ Library.xml found in that source folder.")
            return

        dest_dj_library = self.dj_library_var.get()
        dest_xml = self.export_xml_var.get()
        if not dest_dj_library or not dest_xml:
            messagebox.showerror("Import", "Set the DJ Library Folder and Export XML path on the Export tab first.")
            return

        self.import_btn.config(state=tk.DISABLED)
        self.importing = True
        self.import_text.delete(1.0, tk.END)

        thread = threading.Thread(
            target=self._import_process,
            args=(dj_library_dir, xml_path, dest_dj_library, dest_xml),
            daemon=True,
        )
        thread.start()

    def _import_process(self, source_dj_library_dir, source_xml_path, dest_dj_library, dest_xml):
        try:
            skip_existing = self.import_skip_existing_var.get()
            os.makedirs(dest_dj_library, exist_ok=True)

            self.append_to_text_widget(self.import_text, f"Importing from: {source_dj_library_dir}\n")
            self.append_to_text_widget(self.import_text, f"Into: {dest_dj_library}\n\n")

            try:
                filenames = sorted(os.listdir(source_dj_library_dir))
            except OSError as e:
                self.append_to_text_widget(self.import_text, f"Error reading source folder: {e}\n")
                filenames = []

            copied = 0
            skipped = 0
            errors = 0
            for filename in filenames:
                src_file = os.path.join(source_dj_library_dir, filename)
                if not os.path.isfile(src_file):
                    continue
                dest_file = os.path.join(dest_dj_library, filename)
                if skip_existing and os.path.exists(dest_file):
                    skipped += 1
                    continue
                try:
                    shutil.copy2(src_file, dest_file)
                    copied += 1
                except Exception as e:
                    errors += 1
                    self.append_to_text_widget(self.import_text, f"Error copying {filename}: {e}\n")

            self.append_to_text_widget(
                self.import_text, f"\nCopied {copied} file(s), skipped {skipped} existing, {errors} error(s)\n"
            )

            # Bring in the updated XML manifest, backing up whatever was there first.
            if os.path.exists(dest_xml):
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
                backup_path = f"{dest_xml}.bak-{timestamp}"
                shutil.copy2(dest_xml, backup_path)
                self.append_to_text_widget(self.import_text, f"Backed up existing XML to: {backup_path}\n")

            shutil.copy2(source_xml_path, dest_xml)
            self.append_to_text_widget(self.import_text, f"Copied XML to: {dest_xml}\n")

            self.status_var.set("Import complete!")
            self.append_to_text_widget(self.import_text, "\nImport complete.\n")
            self.root.after(0, lambda: messagebox.showinfo(
                "Import Complete", f"Copied {copied} file(s) ({skipped} skipped, {errors} error(s))."
            ))
            self.config_manager.update_settings(last_mode="import")
            self.root.after(0, self._save_settings_silent)

        except Exception as e:
            error_msg = f"Error during import: {e}"
            logger.error(error_msg)
            self.append_to_text_widget(self.import_text, f"\n{error_msg}\n")
            self.status_var.set("Error during import")
            self.root.after(0, lambda: messagebox.showerror("Import Error", error_msg))
        finally:
            self.root.after(0, self._enable_import_button)
            self.importing = False

    def _enable_import_button(self):
        self.import_btn.config(state=tk.NORMAL)

    def _save_settings(self):
        """Save current UI settings to config file (button handler - shows a confirmation)."""
        self._save_settings_silent()
        self.status_var.set("Settings saved")
        messagebox.showinfo("Settings Saved", "Configuration saved successfully!")

    def _save_settings_silent(self):
        """Save current UI settings to config file with no confirmation popup -
        used after a successful Export/Sync/Import so direct-run from the
        FastRack hub always replays the most recently used settings."""
        # Collect selected playlists
        selected = [
            self.playlist_tree.item(item, "text")
            for item in self.playlist_tree.selection()
        ]

        self.config_manager.update_settings(
            itunes_xml_path=self.itunes_xml_var.get(),
            dj_library_path=self.dj_library_var.get(),
            export_xml_path=self.export_xml_var.get(),
            target_os=self.target_os_var.get(),
            mac_dj_library_path=self.mac_dj_library_var.get(),
            debug_mode=self.debug_var.get(),
            skip_existing=self.skip_existing_var.get(),
            overwrite_all=self.overwrite_all_var.get(),
            delete_removed=self.delete_removed_var.get(),
            selected_playlists=selected,
            selection_mode=self.selection_mode.get(),
            playlist_presets=self.config_manager.settings.playlist_presets,
            active_preset=self.preset_var.get(),
            import_source_dir=self.import_source_dir_var.get(),
            import_skip_existing=self.import_skip_existing_var.get(),
            import_overwrite_all=self.import_overwrite_all_var.get(),
        )

    def update_selection_mode(self):
        """Update the UI when selection mode changes"""
        self.update_selection_summary()
    
    def update_selection_summary(self):
        """Update the selection summary text"""
        if not self.all_playlists:
            self.selection_summary.set("No playlists loaded")
            return

        selected_items = self.playlist_tree.selection()
        total_playlists = len(self.all_playlists)
        selected_count = len(selected_items)

        # Calculate total new tracks (only if XML exists)
        export_xml = self.export_xml_var.get()
        has_existing_xml = export_xml and os.path.exists(export_xml)

        playlists_to_process = self.get_selected_playlists()
        total_new_tracks = 0
        total_tracks = 0
        for playlist_name in playlists_to_process:
            playlist_info = self.playlist_data.get(playlist_name, {})
            total_new_tracks += playlist_info.get('new_track_count', 0)
            total_tracks += playlist_info.get('track_count', 0)

        if self.selection_mode.get() == "include":
            if selected_count == 0:
                self.selection_summary.set(f"⚠️ No playlists selected (0/{total_playlists})")
            else:
                if has_existing_xml:
                    self.selection_summary.set(f"✓ Will sync {selected_count}/{total_playlists} playlists ({total_new_tracks} new tracks of {total_tracks} total)")
                else:
                    self.selection_summary.set(f"✓ Will sync {selected_count}/{total_playlists} playlists ({total_tracks} total tracks)")
        else:  # exclude mode
            processed_count = total_playlists - selected_count
            if selected_count == 0:
                if has_existing_xml:
                    self.selection_summary.set(f"✓ Will sync all {total_playlists} playlists ({total_new_tracks} new tracks of {total_tracks} total)")
                else:
                    self.selection_summary.set(f"✓ Will sync all {total_playlists} playlists ({total_tracks} total tracks)")
            else:
                if has_existing_xml:
                    self.selection_summary.set(f"✓ Will sync {processed_count}/{total_playlists} playlists (excluding {selected_count}) - {total_new_tracks} new tracks of {total_tracks} total")
                else:
                    self.selection_summary.set(f"✓ Will sync {processed_count}/{total_playlists} playlists (excluding {selected_count}) - {total_tracks} total tracks")
        
        # Update button states
        if hasattr(self, 'sync_btn'):
            if self.selection_mode.get() == "include" and selected_count == 0:
                self.sync_btn.config(state=tk.DISABLED)
                self.export_xml_btn.config(state=tk.DISABLED)
            else:
                self.sync_btn.config(state=tk.NORMAL)
                self.export_xml_btn.config(state=tk.NORMAL)

    def select_all_playlists(self):
        """Select all visible playlists"""
        items = self.playlist_tree.get_children()
        self.playlist_tree.selection_set(items)
        self.update_selection_summary()

    def clear_all_playlists(self):
        """Clear all playlist selections"""
        self.playlist_tree.selection_remove(self.playlist_tree.selection())
        self.update_selection_summary()

    def auto_select_playlists(self):
        """Auto-select playlists (only those starting with numbers 1-9)"""
        self.clear_all_playlists()

        for item in self.playlist_tree.get_children():
            playlist_name = self.playlist_tree.item(item, "text")

            # Only select playlists that start with a digit 1-9
            # Exclude playlists that don't start with a number or start with 0
            if playlist_name and len(playlist_name) > 0:
                first_char = playlist_name[0]
                if first_char.isdigit() and first_char != '0':
                    self.playlist_tree.selection_add(item)

        self.update_selection_summary()

    # ------------------------------------------------------------------
    # Playlist presets
    # ------------------------------------------------------------------

    def _refresh_preset_combo(self, select: Optional[str] = None):
        """Refresh the preset dropdown values from settings."""
        if not hasattr(self, "preset_combo"):
            return
        names = [DEFAULT_PRESET_NAME] + sorted(self.config_manager.settings.playlist_presets.keys())
        self.preset_combo["values"] = names
        if select and select in names:
            self.preset_var.set(select)
        elif self.preset_var.get() not in names:
            self.preset_var.set(DEFAULT_PRESET_NAME)

    def _apply_selection(self, playlists: List[str], mode: str):
        """Apply a (playlists, mode) selection to the tree, logging missing names."""
        self.selection_mode.set(mode if mode in ("include", "exclude") else "include")
        self.clear_all_playlists()

        wanted = set(playlists)
        found = set()
        for item in self.playlist_tree.get_children():
            name = self.playlist_tree.item(item, "text")
            if name in wanted:
                self.playlist_tree.selection_add(item)
                found.add(name)

        missing = sorted(wanted - found)
        if missing and hasattr(self, "sync_text"):
            self.append_to_text_widget(
                self.sync_text,
                f"Preset: skipped {len(missing)} playlist(s) not in current XML: {', '.join(missing)}\n"
            )
        self.update_selection_summary()

    def _load_preset(self):
        """Load the currently selected preset into the playlist tree."""
        name = self.preset_var.get()
        if not name:
            return

        if not self.all_playlists:
            messagebox.showinfo("Load Preset", "Load playlists first, then apply the preset.")
            return

        if name == DEFAULT_PRESET_NAME:
            self.auto_select_playlists()
            self.status_var.set(f"Applied preset: {name}")
            return

        preset = self.config_manager.settings.playlist_presets.get(name)
        if not preset:
            messagebox.showerror("Load Preset", f"Preset '{name}' not found.")
            self._refresh_preset_combo()
            return

        self._apply_selection(preset.get("playlists", []), preset.get("mode", "include"))
        self.status_var.set(f"Applied preset: {name}")

    def _capture_current_selection(self) -> Dict[str, Any]:
        """Capture the current tree selection + mode as a preset payload."""
        selected = [
            self.playlist_tree.item(item, "text")
            for item in self.playlist_tree.selection()
        ]
        return {"playlists": selected, "mode": self.selection_mode.get()}

    def _save_preset_as(self):
        """Prompt for a name and save the current selection as a new preset."""
        from tkinter import simpledialog
        name = simpledialog.askstring("Save Preset", "Preset name:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name:
            return
        if name == DEFAULT_PRESET_NAME:
            messagebox.showerror("Save Preset", f"'{DEFAULT_PRESET_NAME}' is reserved.")
            return

        presets = self.config_manager.settings.playlist_presets
        if name in presets:
            if not messagebox.askyesno("Save Preset", f"Preset '{name}' exists. Overwrite?"):
                return

        presets[name] = self._capture_current_selection()
        self.config_manager.save_settings()
        self._refresh_preset_combo(select=name)
        self.status_var.set(f"Saved preset: {name}")

    def _overwrite_preset(self):
        """Save the current selection into the currently selected user preset."""
        name = self.preset_var.get()
        if not name or name == DEFAULT_PRESET_NAME:
            messagebox.showinfo("Save Preset", "Select a saved preset to save into, or use Save As to create a new one.")
            return

        presets = self.config_manager.settings.playlist_presets
        if name not in presets:
            messagebox.showerror("Save Preset", f"Preset '{name}' not found.")
            self._refresh_preset_combo()
            return

        if not messagebox.askyesno("Save Preset", f"Save current selection into '{name}'?"):
            return

        presets[name] = self._capture_current_selection()
        self.config_manager.save_settings()
        self.status_var.set(f"Saved preset: {name}")

    def _delete_preset(self):
        """Delete the currently selected user preset."""
        name = self.preset_var.get()
        if not name or name == DEFAULT_PRESET_NAME:
            messagebox.showinfo("Delete Preset", f"'{DEFAULT_PRESET_NAME}' is built-in and cannot be deleted.")
            return

        presets = self.config_manager.settings.playlist_presets
        if name not in presets:
            self._refresh_preset_combo()
            return

        if not messagebox.askyesno("Delete Preset", f"Delete preset '{name}'?"):
            return

        del presets[name]
        self.config_manager.save_settings()
        self._refresh_preset_combo()
        self.status_var.set(f"Deleted preset: {name}")

    def preview_selection(self):
        """Show a preview of what will be synced"""
        selected_playlists = self.get_selected_playlists()
        
        if not selected_playlists:
            messagebox.showwarning("No Selection", "No playlists are selected for sync!")
            return
        
        # Create preview window
        preview_window = tk.Toplevel(self.root)
        preview_window.title("Sync Preview")
        preview_window.geometry("500x400")
        preview_window.transient(self.root)
        
        # Create text widget with scrollbar
        text_frame = ttk.Frame(preview_window)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        preview_text = tk.Text(text_frame, wrap=tk.WORD)
        preview_scroll = ttk.Scrollbar(text_frame, orient="vertical", command=preview_text.yview)
        preview_text.config(yscrollcommand=preview_scroll.set)
        
        preview_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        preview_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        
        # Populate preview
        preview_text.insert(tk.END, f"Sync Preview - {len(selected_playlists)} playlists selected\n")
        preview_text.insert(tk.END, "="*50 + "\n\n")
        
        total_tracks = 0
        for playlist_name in selected_playlists:
            playlist_info = self.playlist_data.get(playlist_name, {})
            track_count = playlist_info.get('track_count', 0)
            playlist_type = playlist_info.get('type', 'Unknown')
            
            preview_text.insert(tk.END, f"• {playlist_name}\n")
            preview_text.insert(tk.END, f"  Type: {playlist_type}, Tracks: {track_count}\n\n")
            total_tracks += track_count
        
        preview_text.insert(tk.END, f"Total tracks across all playlists: {total_tracks}\n")
        preview_text.insert(tk.END, "(Note: Duplicate tracks will only be copied once)")
        
        preview_text.config(state=tk.DISABLED)
        
        # Close button
        ttk.Button(preview_window, text="Close", command=preview_window.destroy).pack(pady=10)

    def filter_playlists(self, *args):
        """Filter displayed playlists based on search term"""
        filter_text = self.filter_var.get().lower()
        
        # Store current selections
        selected_playlists = set()
        for item in self.playlist_tree.selection():
            playlist_name = self.playlist_tree.item(item, "text")
            selected_playlists.add(playlist_name)
        
        # Clear and repopulate treeview
        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)
        
        for playlist_name in self.all_playlists:
            if not filter_text or filter_text in playlist_name.lower():
                playlist_info = self.playlist_data.get(playlist_name, {})
                track_count = playlist_info.get('track_count', 0)
                new_track_count = playlist_info.get('new_track_count', 0)
                playlist_type = playlist_info.get('type', 'User')

                # Show "-" if new track count wasn't calculated (no existing XML)
                export_xml = self.export_xml_var.get()
                if not export_xml or not os.path.exists(export_xml):
                    new_track_display = "-"
                else:
                    new_track_display = new_track_count

                item_id = self.playlist_tree.insert("", "end", text=playlist_name,
                                                   values=(track_count, new_track_display, playlist_type))
                
                # Restore selection if it was selected before
                if playlist_name in selected_playlists:
                    self.playlist_tree.selection_add(item_id)
        
        self.update_selection_summary()
        
        # Bind selection event to update summary
        self.playlist_tree.bind('<<TreeviewSelect>>', lambda e: self.update_selection_summary())

    def get_selected_playlists(self):
        """Get the list of playlists that should be processed based on current selection"""
        selected_items = self.playlist_tree.selection()
        selected_playlists = []
        
        for item in selected_items:
            playlist_name = self.playlist_tree.item(item, "text")
            selected_playlists.append(playlist_name)
        
        if self.selection_mode.get() == "include":
            # Only process selected playlists
            return selected_playlists
        else:
            # Process all playlists except selected ones
            all_visible_playlists = []
            for item in self.playlist_tree.get_children():
                playlist_name = self.playlist_tree.item(item, "text")
                all_visible_playlists.append(playlist_name)
            
            return [p for p in all_visible_playlists if p not in selected_playlists]

    def check_ffmpeg_for_flac_conversion(self, *args):
        """Check if FFmpeg is available when FLAC conversion is enabled"""
        if self.convert_flac_var.get():  # If the checkbox was checked
            has_ffmpeg = self.check_ffmpeg()
            if not has_ffmpeg:
                messagebox.showwarning(
                    "FFmpeg Not Found", 
                    "FFmpeg is not installed or not in your PATH. FLAC conversion will not work.\n\n"
                    "To enable FLAC conversion, please install FFmpeg:\n"
                    "- Windows: https://ffmpeg.org/download.html\n"
                    "- macOS: brew install ffmpeg\n"
                    "- Linux: apt-get install ffmpeg or equivalent\n\n"
                    "The FLAC conversion option has been disabled."
                )
                # Disable the checkbox without triggering this function again
                self.root.after(100, lambda: self.convert_flac_var.set(False))

    def create_results_panel(self, parent):
        results_frame = ttk.LabelFrame(parent, text="Sync Progress and Results")
        results_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        
        self.results_notebook = ttk.Notebook(results_frame)
        self.results_notebook.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        # ttk::Notebook ships a standard, cross-platform Tcl binding on its
        # "TNotebook" class that cycles tabs on any scroll over it - on Tk 9
        # that's driven by <TouchpadScroll> for a trackpad (see
        # shared_scrollable_frame's comment on TIP 684; <MouseWheel> is
        # mouse-only there now) and by <MouseWheel>/<Button-4/5> for an
        # actual wheel. Neutralize both class bindings outright (belt) and
        # add instance-level overrides that forward to the window body and
        # stop propagation (suspenders), since instance bindings run before
        # class ones in Tk's bindtag order.
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_class("TNotebook", seq, lambda e: "break")
            self.results_notebook.bind(seq, self._on_notebook_mouse_wheel)
        safe_bind_touchpad_scroll(self.root.bind_class, "TNotebook", "<TouchpadScroll>", lambda e: "break")
        safe_bind_touchpad_scroll(self.results_notebook.bind, "<TouchpadScroll>", self._on_notebook_touchpad_scroll)

        self.analysis_frame = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.analysis_frame, text="Library Analysis")
        self.analysis_frame.columnconfigure(0, weight=1)
        self.analysis_frame.rowconfigure(0, weight=1)
        
        self.analysis_text = tk.Text(self.analysis_frame, wrap=tk.WORD)
        self.analysis_text.grid(row=0, column=0, sticky="nsew")
        
        analysis_scrollbar = ttk.Scrollbar(self.analysis_frame, command=self.analysis_text.yview)
        analysis_scrollbar.grid(row=0, column=1, sticky="ns")
        self.analysis_text.config(yscrollcommand=analysis_scrollbar.set)
        
        self.sync_frame = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.sync_frame, text="Sync Progress")
        self.sync_frame.columnconfigure(0, weight=1)
        self.sync_frame.rowconfigure(0, weight=1)
        
        self.sync_text = tk.Text(self.sync_frame, wrap=tk.WORD)
        self.sync_text.grid(row=0, column=0, sticky="nsew")
        
        sync_scrollbar = ttk.Scrollbar(self.sync_frame, command=self.sync_text.yview)
        sync_scrollbar.grid(row=0, column=1, sticky="ns")
        self.sync_text.config(yscrollcommand=sync_scrollbar.set)
        
        self.xml_frame = ttk.Frame(self.results_notebook)
        self.results_notebook.add(self.xml_frame, text="XML Export")
        self.xml_frame.columnconfigure(0, weight=1)
        self.xml_frame.rowconfigure(0, weight=1)

        self.xml_text = tk.Text(self.xml_frame, wrap=tk.WORD)
        self.xml_text.grid(row=0, column=0, sticky="nsew")

        xml_scrollbar = ttk.Scrollbar(self.xml_frame, command=self.xml_text.yview)
        xml_scrollbar.grid(row=0, column=1, sticky="ns")
        self.xml_text.config(yscrollcommand=xml_scrollbar.set)

    def delete_removed_tracks(self, dj_library, export_xml_path):
        """Delete tracks in DJ Library that are not referenced in the exported XML"""
        deleted_count = 0

        if not dj_library or not os.path.exists(dj_library):
            return deleted_count

        if not export_xml_path or not os.path.exists(export_xml_path):
            self.append_to_text_widget(self.sync_text, "Warning: Export XML not found, skipping delete step.\n")
            return deleted_count

        # Parse the exported XML to get all tracks that are actually referenced
        active_files = set()
        try:
            with open(export_xml_path, 'r', encoding='utf-8') as f:
                export_root = ET.fromstring(f.read())

            # Get the main dictionary element
            export_dict = next((child for child in export_root if child.tag == 'dict'), None)
            if export_dict is None:
                self.append_to_text_widget(self.sync_text, "Warning: Invalid export XML structure, skipping delete step.\n")
                return deleted_count

            # Find the Tracks dictionary in the exported XML
            tracks_element = None
            for i in range(len(export_dict)):
                if export_dict[i].tag == 'key' and export_dict[i].text == 'Tracks':
                    if i + 1 < len(export_dict) and export_dict[i + 1].tag == 'dict':
                        tracks_element = export_dict[i + 1]
                    break

            if tracks_element is not None:
                # Extract all track locations from the exported XML
                for i in range(0, len(tracks_element), 2):
                    if i+1 >= len(tracks_element):
                        break

                    if tracks_element[i].tag == 'key' and tracks_element[i+1].tag == 'dict':
                        track_dict = tracks_element[i+1]

                        # Look for the Location field
                        for j in range(0, len(track_dict), 2):
                            if j+1 >= len(track_dict):
                                break

                            if track_dict[j].tag == 'key' and track_dict[j].text == 'Location':
                                location = track_dict[j+1].text
                                if location:
                                    # Extract filename from the location URL
                                    # Locations in export XML point to DJ Library files
                                    filename = os.path.basename(urllib.parse.unquote(location))
                                    active_files.add(filename)
                                break

            self.append_to_text_widget(self.sync_text, f"Found {len(active_files)} active track(s) in exported XML\n")

        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error reading export XML: {e}\n")
            self.append_to_text_widget(self.sync_text, "Skipping delete step to avoid data loss.\n")
            return deleted_count

        # Get all files currently in DJ Library
        try:
            existing_files = os.listdir(dj_library)

            for existing_file in existing_files:
                # Skip special folders (legacy _Removed, etc.)
                if existing_file.startswith('_'):
                    continue

                file_path = os.path.join(dj_library, existing_file)

                # Skip if not a file
                if not os.path.isfile(file_path):
                    continue

                # If this file is not in our active files list, delete it
                if existing_file not in active_files:
                    os.remove(file_path)
                    self.append_to_text_widget(self.sync_text, f"Deleted: {existing_file}\n")
                    deleted_count += 1

        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error deleting tracks: {e}\n")

        if deleted_count > 0:
            self.append_to_text_widget(self.sync_text, f"\nDeleted {deleted_count} removed track(s) from {dj_library}\n")
        else:
            self.append_to_text_widget(self.sync_text, "No orphaned tracks to delete.\n")

        return deleted_count

    def browse_itunes_xml(self):
        filename = filedialog.askopenfilename(
            title="Select iTunes XML Library",
            filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]
        )
        if filename:
            self.itunes_xml_var.set(filename)

    def _schedule_autoload_playlists(self, delay=400):
        """Debounced auto-load of playlists when iTunes XML path changes."""
        if self._itunes_autoload_after_id is not None:
            try:
                self.root.after_cancel(self._itunes_autoload_after_id)
            except Exception:
                pass
        self._itunes_autoload_after_id = self.root.after(delay, self._auto_load_playlists)

    def _auto_load_playlists(self):
        self._itunes_autoload_after_id = None
        xml_path = self.itunes_xml_var.get()
        if not xml_path or not os.path.exists(xml_path):
            return
        if xml_path == self._loaded_xml_path:
            return
        self.load_playlists(silent=True)

    def _on_window_focus(self, event):
        # FocusIn fires for child widgets too — only react to the root window.
        if event.widget is not self.root:
            return
        self._check_xml_mtime()

    def _check_xml_mtime(self):
        """Reload playlists if the loaded iTunes XML changed on disk (e.g. iTunes rewrote it)."""
        xml_path = self._loaded_xml_path
        if not xml_path or not os.path.exists(xml_path):
            return
        try:
            current_mtime = os.path.getmtime(xml_path)
        except OSError:
            return
        if self._loaded_xml_mtime is not None and current_mtime != self._loaded_xml_mtime:
            self._loaded_xml_path = None  # force the dedup check to pass
            self._schedule_autoload_playlists(delay=0)
    
    def browse_dj_library(self):
        directory = filedialog.askdirectory(title="Select DJ Library Folder")
        if directory:
            self.dj_library_var.set(directory)
            # Set export XML in the parent directory of the DJ Library folder
            parent_dir = os.path.dirname(directory)
            self.export_xml_var.set(os.path.join(parent_dir, 'DJ Library.xml'))
    
    def browse_export_xml(self):
        filename = filedialog.asksaveasfilename(
            title="Select Export XML Location",
            defaultextension=".xml",
            filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]
        )
        if filename:
            self.export_xml_var.set(filename)

    def count_new_tracks(self, track_locations, debug=False, return_details=False):
        """Count how many tracks from the given list don't exist in the DJ Library

        Args:
            track_locations: List of track file paths to check
            debug: Enable debug output
            return_details: If True, return (count, list_of_new_track_info)

        Returns:
            If return_details=False: int (count of new tracks)
            If return_details=True: tuple (count, list of dicts with track info)
        """
        dj_library = self.dj_library_var.get()
        if not dj_library or not os.path.exists(dj_library):
            # If DJ Library doesn't exist, all tracks are new
            if return_details:
                return len(track_locations), []
            return len(track_locations)

        convert_to_flac = self.convert_flac_var.get()
        new_count = 0
        new_tracks_details = []

        # Get list of existing files in DJ Library (for faster lookup)
        try:
            existing_files = set(os.listdir(dj_library))
        except:
            existing_files = set()

        if debug:
            print(f"\n=== DEBUG: Checking {len(track_locations)} tracks ===")
            print(f"DJ Library: {dj_library}")
            print(f"Existing files in DJ Library: {len(existing_files)}")
            print(f"Convert to FLAC: {convert_to_flac}")

        for track_path in track_locations:
            try:
                if not os.path.exists(track_path):
                    # Track source file doesn't exist, skip it
                    if debug:
                        print(f"SKIP (doesn't exist): {track_path}")
                    continue

                file_ext = os.path.splitext(track_path)[1].lower()
                original_base_name = os.path.splitext(os.path.basename(track_path))[0]

                # Extract track title from metadata (same logic as in update_dj_library)
                track_title = self.get_track_title(track_path)
                base_name = track_title if track_title else original_base_name

                # Determine destination extension
                dest_ext = ".flac" if convert_to_flac and file_ext != '.flac' else file_ext
                dest_filename = base_name + dest_ext

                # Check if file doesn't exist in DJ Library
                is_new = dest_filename not in existing_files
                if is_new:
                    new_count += 1
                    if return_details:
                        new_tracks_details.append({
                            'source_path': track_path,
                            'source_filename': os.path.basename(track_path),
                            'dest_filename': dest_filename,
                            'track_title': track_title if track_title else original_base_name
                        })

                if debug:
                    status = "NEW" if is_new else "EXISTS"
                    print(f"{status}: {os.path.basename(track_path)} -> {dest_filename}")
            except Exception as e:
                # If we can't determine, count it as new
                new_count += 1
                if debug:
                    print(f"ERROR (counted as new): {track_path} - {e}")

        if debug:
            print(f"=== Total NEW tracks: {new_count} ===\n")

        if return_details:
            return new_count, new_tracks_details
        return new_count

    def calculate_new_tracks_for_selected(self):
        """Calculate new track counts only for selected playlists (button handler)"""
        # Check if playlists are loaded
        if not self.all_playlists:
            messagebox.showwarning("Warning", "Please load playlists first!")
            return

        # Check if there's an existing export XML file
        export_xml = self.export_xml_var.get()
        if not export_xml or not os.path.exists(export_xml):
            messagebox.showinfo("Info", "No existing DJ Library XML found. All tracks will be new when you sync.")
            return

        # Get selected playlists
        selected_playlists = self.get_selected_playlists()
        if not selected_playlists:
            messagebox.showwarning("Warning", "No playlists selected!")
            return

        # Get the library dict
        if not hasattr(self, 'itunes_root') or self.itunes_root is None:
            messagebox.showerror("Error", "iTunes library data not loaded!")
            return

        library_dict = next((child for child in self.itunes_root if child.tag == 'dict'), None)
        if library_dict is None:
            messagebox.showerror("Error", "Invalid iTunes XML structure!")
            return

        self.status_var.set("Step 1/3: Extracting track locations from iTunes library...")
        self.root.update_idletasks()

        # First, extract track ID to location mapping from the library
        track_id_to_location = {}

        # Find the Tracks dictionary
        tracks_element = None
        for i in range(len(library_dict)):
            if library_dict[i].tag == 'key' and library_dict[i].text == 'Tracks':
                if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'dict':
                    tracks_element = library_dict[i + 1]
                break

        if tracks_element is None:
            messagebox.showerror("Error", "No tracks found in iTunes XML!")
            return

        # Extract track locations with progress
        total_track_elements = len(tracks_element) // 2
        for i in range(0, len(tracks_element), 2):
            if i+1 >= len(tracks_element):
                break

            # Update progress every 100 tracks
            if i % 200 == 0:
                progress = (i // 2) + 1
                self.status_var.set(f"Step 1/3: Extracting track locations ({progress}/{total_track_elements})...")
                self.root.update_idletasks()

            if tracks_element[i].tag == 'key' and tracks_element[i+1].tag == 'dict':
                track_id = tracks_element[i].text
                track_dict = tracks_element[i+1]

                # Find the Location key
                for j in range(0, len(track_dict), 2):
                    if j+1 >= len(track_dict):
                        break

                    if track_dict[j].tag == 'key' and track_dict[j].text == 'Location':
                        location = track_dict[j+1].text
                        if location:
                            # Decode the file:// URL
                            try:
                                from urllib.parse import unquote, urlparse
                                parsed = urlparse(location)
                                file_path = unquote(parsed.path)
                                # Fix Windows paths
                                if file_path.startswith('/') and ':' in file_path:
                                    file_path = file_path[1:]
                                if os.path.exists(file_path):
                                    track_id_to_location[track_id] = file_path
                            except:
                                pass
                        break

        self.status_var.set(f"Step 2/3: Mapping playlist tracks...")
        self.root.update_idletasks()

        # Calculate new track counts ONLY for selected playlists
        total_selected = len(selected_playlists)
        total_new_found = 0

        # Clear the analysis tab for new tracks details
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(tk.END, "=" * 80 + "\n")
        self.analysis_text.insert(tk.END, "NEW TRACKS ANALYSIS\n")
        self.analysis_text.insert(tk.END, "=" * 80 + "\n\n")

        # Collect all track locations from selected playlists for later comparison
        all_selected_track_locations = set()

        for idx, playlist_name in enumerate(selected_playlists, 1):
            # Update progress for each playlist
            self.status_var.set(f"Step 3/3: Checking new tracks ({idx}/{total_selected}): {playlist_name[:30]}...")
            self.root.update_idletasks()

            playlist_info = self.playlist_data.get(playlist_name, {})
            track_ids = playlist_info.get('track_ids', [])

            # Get locations for these track IDs
            track_locations = [track_id_to_location[tid] for tid in track_ids if tid in track_id_to_location]

            # Add to the set of all selected tracks
            all_selected_track_locations.update(track_locations)

            # Count how many are new and get details (enable debug for first playlist only)
            debug_enabled = (idx == 1)
            new_count, new_tracks_details = self.count_new_tracks(track_locations, debug=debug_enabled, return_details=True)
            self.playlist_data[playlist_name]['new_track_count'] = new_count
            total_new_found += new_count

            # Add details to the analysis tab
            if new_count > 0:
                self.analysis_text.insert(tk.END, f"\n{playlist_name}\n")
                self.analysis_text.insert(tk.END, "-" * len(playlist_name) + "\n")
                self.analysis_text.insert(tk.END, f"New tracks: {new_count}\n\n")

                for track in new_tracks_details:
                    self.analysis_text.insert(tk.END, f"  • {track['track_title']}\n")
                    self.analysis_text.insert(tk.END, f"    Source: {track['source_filename']}\n")
                    self.analysis_text.insert(tk.END, f"    Will be saved as: {track['dest_filename']}\n\n")

        # Now check for removed tracks (orphaned files in DJ Library)
        self.analysis_text.insert(tk.END, "\n" + "=" * 80 + "\n")
        self.analysis_text.insert(tk.END, "REMOVED TRACKS (No longer in selected playlists)\n")
        self.analysis_text.insert(tk.END, "=" * 80 + "\n\n")

        dj_library = self.dj_library_var.get()
        removed_tracks = []

        if dj_library and os.path.exists(dj_library):
            self.status_var.set("Checking for removed tracks...")
            self.root.update_idletasks()

            # Get all files currently in DJ Library
            try:
                existing_files = os.listdir(dj_library)
                convert_to_flac = self.convert_flac_var.get()

                # For each file in DJ Library, check if it corresponds to any selected track
                for existing_file in existing_files:
                    if existing_file.startswith('_'):  # Skip special folders
                        continue

                    file_path = os.path.join(dj_library, existing_file)
                    if not os.path.isfile(file_path):
                        continue

                    # Check if this file corresponds to any track in our selected playlists
                    is_orphaned = True
                    for track_location in all_selected_track_locations:
                        # Calculate what this track would be named
                        file_ext = os.path.splitext(track_location)[1].lower()
                        original_base_name = os.path.splitext(os.path.basename(track_location))[0]
                        track_title = self.get_track_title(track_location)
                        base_name = track_title if track_title else original_base_name
                        dest_ext = ".flac" if convert_to_flac and file_ext != '.flac' else file_ext
                        expected_filename = base_name + dest_ext

                        if existing_file == expected_filename:
                            is_orphaned = False
                            break

                    if is_orphaned:
                        removed_tracks.append(existing_file)

            except Exception as e:
                self.analysis_text.insert(tk.END, f"Error checking for removed tracks: {e}\n\n")

        if removed_tracks:
            self.analysis_text.insert(tk.END, f"Found {len(removed_tracks)} orphaned file(s) in DJ Library:\n\n")
            for track_file in sorted(removed_tracks):
                self.analysis_text.insert(tk.END, f"  • {track_file}\n")
            self.analysis_text.insert(tk.END, f"\nThese files will be deleted when you run the sync.\n")
        else:
            self.analysis_text.insert(tk.END, "No orphaned tracks found. DJ Library is in sync with selected playlists.\n")

        # Add summary at the end
        self.analysis_text.insert(tk.END, "\n" + "=" * 80 + "\n")
        self.analysis_text.insert(tk.END, f"SUMMARY:\n")
        self.analysis_text.insert(tk.END, f"  New tracks: {total_new_found}\n")
        self.analysis_text.insert(tk.END, f"  Removed tracks: {len(removed_tracks)}\n")
        self.analysis_text.insert(tk.END, "=" * 80 + "\n")

        # Refresh the display
        self.filter_playlists()
        self.status_var.set(f"Calculated new tracks for {total_selected} playlist(s) - Found {total_new_found} new tracks total")

        # Show a message box with the results and switch to the Library Analysis tab
        messagebox.showinfo("Calculation Complete",
                          f"Checked {total_selected} playlist(s)\nFound {total_new_found} new tracks total\n\nSee 'Library Analysis' tab for full details")

        # Switch to the Library Analysis tab
        self.results_notebook.select(0)

    def load_playlists(self, silent=False):
        """Load playlists from iTunes XML into the treeview.

        silent=True suppresses error popups (used by the path-change auto-loader).
        """
        xml_path = self.itunes_xml_var.get()
        if not xml_path or not os.path.exists(xml_path):
            if not silent:
                messagebox.showerror("Error", "Please select a valid iTunes XML file first!")
            return
        
        self.status_var.set("Loading playlists...")
        
        # Clear existing data
        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)
        self.all_playlists = []
        self.playlist_data = {}
        
        try:
            # Parse the XML file and store the root
            with open(xml_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
                self.itunes_root = ET.fromstring(xml_content)
                
            library_dict = next((child for child in self.itunes_root if child.tag == 'dict'), None)
            if library_dict is None:
                if not silent:
                    messagebox.showerror("Error", "Invalid iTunes XML format!")
                return
                
            playlists_element = None
            for i in range(len(library_dict)):
                if library_dict[i].tag == 'key' and library_dict[i].text == 'Playlists':
                    if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'array':
                        playlists_element = library_dict[i + 1]
                    break
                    
            if playlists_element is not None:
                auto_generated = {"Library", "Music", "Liked Songs", "Recently Added", 
                                "Top 100 Most Played", "Recently Played", "My Top Rated"}
                
                for playlist_dict in playlists_element:
                    if playlist_dict.tag == 'dict':
                        playlist_name = None
                        playlist_id = None
                        track_count = 0
                        track_ids = []
                        is_smart = False
                        is_master = False

                        # Extract playlist information
                        for i in range(0, len(playlist_dict), 2):
                            if i+1 < len(playlist_dict) and playlist_dict[i].tag == 'key':
                                key = playlist_dict[i].text
                                value_element = playlist_dict[i+1]

                                if key == 'Name':
                                    playlist_name = value_element.text
                                elif key == 'Playlist ID':
                                    playlist_id = value_element.text
                                elif key == 'Playlist Items' and value_element.tag == 'array':
                                    track_count = len(value_element)
                                    # Extract track IDs from playlist items
                                    for item in value_element:
                                        if item.tag == 'dict':
                                            for j in range(0, len(item), 2):
                                                if j+1 < len(item) and item[j].tag == 'key' and item[j].text == 'Track ID':
                                                    if item[j+1].text:
                                                        track_ids.append(item[j+1].text)
                                elif key == 'Smart Info':
                                    is_smart = True
                                elif key == 'Master':
                                    is_master = True
                        
                        if playlist_name:
                            # Determine playlist type
                            if is_master:
                                playlist_type = "Master"
                            elif playlist_name in auto_generated or playlist_name.startswith("Top 25 "):
                                playlist_type = "System"
                            elif is_smart:
                                playlist_type = "Smart"
                            else:
                                playlist_type = "User"
                            
                            self.all_playlists.append(playlist_name)
                            self.playlist_data[playlist_name] = {
                                'id': playlist_id,
                                'track_count': track_count,
                                'track_ids': track_ids,
                                'type': playlist_type,
                                'is_smart': is_smart,
                                'is_master': is_master,
                                'new_track_count': 0  # Will be calculated later
                            }

                            # Add to treeview (new_track_count will be shown as 0 initially)
                            item_id = self.playlist_tree.insert("", "end", text=playlist_name,
                                                               values=(track_count, 0, playlist_type))
                
                self.status_var.set(f"Loaded {len(self.all_playlists)} playlists")
                self._loaded_xml_path = xml_path
                try:
                    self._loaded_xml_mtime = os.path.getmtime(xml_path)
                except OSError:
                    self._loaded_xml_mtime = None

                active = self.preset_var.get()
                if active and active != DEFAULT_PRESET_NAME and active in self.config_manager.settings.playlist_presets:
                    self._load_preset()
                else:
                    self.auto_select_playlists()  # Auto-select user playlists
                self.filter_playlists()  # Apply initial filtering
            else:
                if not silent:
                    messagebox.showerror("Error", "No playlists found in XML!")
        except Exception as e:
            if not silent:
                messagebox.showerror("Error", f"Failed to load playlists: {str(e)}")
            self.status_var.set("Error loading playlists")

    def get_subprocess_args(self):
        """Get platform-specific arguments for subprocess to hide console windows"""
        process_args = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE
        }
        
        # On Windows, add creationflags to hide the console window
        if sys.platform == "win32":
            process_args["creationflags"] = 0x08000000  # CREATE_NO_WINDOW flag
            
        return process_args

    def check_ffmpeg_ui(self):
        self.status_var.set("Checking for FFmpeg...")
        
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(tk.END, "Checking for FFmpeg...\n\n")
        self.root.update_idletasks()
        
        try:
            process_args = self.get_subprocess_args()
            result = subprocess.run(["ffmpeg", "-version"], **process_args)
            version_output = result.stdout.decode()
            version_info = version_output.split('\n')[0] if version_output else "Unknown version"
            
            self.analysis_text.insert(tk.END, f"✓ FFmpeg is installed and available\n\n")
            self.analysis_text.insert(tk.END, f"Version information:\n{version_info}\n\n")
            self.analysis_text.insert(tk.END, "You can enable FLAC conversion with album art embedding in the options.")
            
            self.status_var.set("FFmpeg check complete - Available")
        except FileNotFoundError:
            self.analysis_text.insert(tk.END, "✗ FFmpeg is NOT installed or not in your PATH.\n\n")
            self.analysis_text.insert(tk.END, "Audio conversion to FLAC will be disabled.\n\n")
            self.analysis_text.insert(tk.END, "To enable FLAC conversion, please install FFmpeg:\n")
            self.analysis_text.insert(tk.END, "- Windows: https://ffmpeg.org/download.html\n")
            self.analysis_text.insert(tk.END, "- macOS: brew install ffmpeg\n")
            self.analysis_text.insert(tk.END, "- Linux: apt-get install ffmpeg or equivalent\n")
            
            self.convert_flac_var.set(False)
            self.status_var.set("FFmpeg check complete - Not available")
        except Exception as e:
            self.analysis_text.insert(tk.END, f"Error checking for FFmpeg: {e}\n")
            self.status_var.set("Error checking for FFmpeg")

    def check_flac_ui(self):
        self.status_var.set("Checking for FLAC...")

        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(tk.END, "Checking for FLAC encoder/decoder...\n\n")
        self.root.update_idletasks()

        try:
            process_args = self.get_subprocess_args()
            result = subprocess.run(["flac", "--version"], **process_args)
            version_output = result.stdout.decode()
            version_info = version_output.strip() if version_output else "Unknown version"

            self.analysis_text.insert(tk.END, f"✓ FLAC is installed and available\n\n")
            self.analysis_text.insert(tk.END, f"Version information:\n{version_info}\n\n")
            self.analysis_text.insert(tk.END, "FLAC encoder/decoder is available for audio processing.")

            self.status_var.set("FLAC check complete - Available")
        except FileNotFoundError:
            self.analysis_text.insert(tk.END, "✗ FLAC is NOT installed or not in your PATH.\n\n")
            self.analysis_text.insert(tk.END, "Note: This application uses FFmpeg for FLAC conversion, so a standalone FLAC encoder is optional.\n\n")
            self.analysis_text.insert(tk.END, "To install FLAC encoder/decoder:\n")
            self.analysis_text.insert(tk.END, "- Windows: Download from https://xiph.org/flac/download.html\n")
            self.analysis_text.insert(tk.END, "- macOS: brew install flac\n")
            self.analysis_text.insert(tk.END, "- Linux: apt-get install flac or equivalent\n")

            self.status_var.set("FLAC check complete - Not available")
        except Exception as e:
            self.analysis_text.insert(tk.END, f"Error checking for FLAC: {e}\n")
            self.status_var.set("Error checking for FLAC")

    def export_xml_only(self):
        """Export XML without copying files"""
        # Set export_xml_only mode to True
        self.export_xml_only_var = tk.BooleanVar(value=True)
        # Call the regular sync process
        self.start_sync()

    def start_sync(self):
        if self.importing:
            messagebox.showerror("Error", "An import is currently running - wait for it to finish first.")
            return

        # Initialize export_xml_only_var if it doesn't exist (for normal sync)
        if not hasattr(self, 'export_xml_only_var'):
            self.export_xml_only_var = tk.BooleanVar(value=False)

        itunes_xml = self.itunes_xml_var.get()
        dj_library = self.dj_library_var.get()
        export_xml = self.export_xml_var.get()

        if not itunes_xml or not os.path.exists(itunes_xml):
            messagebox.showerror("Error", "iTunes XML file not found!")
            return

        if not dj_library:
            messagebox.showerror("Error", "Please specify a DJ Library folder!")
            return

        if not export_xml:
            messagebox.showerror("Error", "Please specify an export XML path!")
            return

        # Check playlist selection
        selected_playlists = self.get_selected_playlists()
        if not selected_playlists:
            messagebox.showerror("Error", "No playlists selected for sync!")
            return

        # Make sure the export_xml path is a file, not a directory
        if os.path.isdir(export_xml):
            export_xml = os.path.join(export_xml, "DJ Library.xml")
            self.export_xml_var.set(export_xml)
            messagebox.showinfo("XML Path Updated",
                               f"Export path was a directory. Updated to: {export_xml}")

        # Check if we have write permissions for the XML file's directory
        export_dir = os.path.dirname(export_xml)
        if export_dir and not os.access(export_dir, os.W_OK):
            alt_path = os.path.join(os.path.expanduser("~"), "Documents", "DJ Library.xml")
            response = messagebox.askquestion("Permission Issue",
                                            f"No write permission for {export_dir}. Would you like to save to {alt_path} instead?")
            if response == 'yes':
                self.export_xml_var.set(alt_path)
                export_xml = alt_path
            else:
                messagebox.showinfo("Sync Cancelled", "Please select a writable location for the XML export.")
                return

        if not os.path.exists(dj_library):
            try:
                os.makedirs(dj_library)
                self.analysis_text.insert(tk.END, f"Created DJ Library folder: {dj_library}\n")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create DJ Library folder: {e}")
                return

        self.sync_btn.config(state=tk.DISABLED)
        self.export_xml_btn.config(state=tk.DISABLED)
        self.cancel_btn.config(state=tk.NORMAL)
        self.syncing = True

        self.analysis_text.delete(1.0, tk.END)
        self.sync_text.delete(1.0, tk.END)
        self.xml_text.delete(1.0, tk.END)

        thread = threading.Thread(target=self.sync_process, daemon=True)
        thread.start()

    def sync_process(self):
        try:
            itunes_xml = self.itunes_xml_var.get()
            dj_library = self.dj_library_var.get()
            export_xml = self.export_xml_var.get()

            debug_missing = self.debug_var.get()
            skip_existing = self.skip_existing_var.get()  # True = skip existing files, False = overwrite all
            convert_to_flac = self.convert_flac_var.get()
            preserve_album_art = self.preserve_album_art_var.get()
            export_xml_only = self.export_xml_only_var.get()
            
            # Check for FFmpeg at the beginning if FLAC conversion is enabled
            if convert_to_flac:
                self.append_to_text_widget(self.analysis_text, "Checking for FFmpeg...\n")
                has_ffmpeg = self.check_ffmpeg()
                if not has_ffmpeg:
                    # Show a warning dialog
                    self.root.after(0, lambda: messagebox.showwarning(
                        "FFmpeg Not Found", 
                        "FLAC conversion is enabled but FFmpeg is not installed.\n"
                        "FLAC conversion will be disabled for this sync."
                    ))
                    
                    self.append_to_text_widget(self.analysis_text, "FLAC conversion disabled due to missing FFmpeg.\n")
                    convert_to_flac = False
                    self.root.after(0, lambda: self.convert_flac_var.set(False))
            
            start_time = time.time()
            
            self.status_var.set("Step 1: Analyzing iTunes Library...")
            self.append_to_text_widget(self.analysis_text, "===== STEP 1: ANALYZING ITUNES LIBRARY =====\n")
            self.results_notebook.select(0)
            
            # Modified to return more detailed information including ALL playlist memberships
            selected_playlist_data, tracks_to_copy, tracks_metadata, source_path_to_playlists = self.analyze_library(
                itunes_xml, debug_missing)

            step1_time = time.time() - start_time
            self.append_to_text_widget(self.analysis_text, f"Step 1 completed in {step1_time:.2f} seconds\n\n")

            if tracks_to_copy:
                if export_xml_only:
                    # Skip file copying, but create path mapping to destination paths
                    self.append_to_text_widget(self.sync_text, "===== STEP 2: CREATING PATH MAPPING (Export XML Only Mode) =====\n")
                    self.append_to_text_widget(self.sync_text, "File copying skipped - creating path mappings to destination library\n\n")
                    self.results_notebook.select(1)

                    # Create path mapping (original path -> destination DJ library path)
                    file_mapping = self.create_path_mapping(tracks_to_copy, dj_library, convert_to_flac)
                    synced_tracks = True  # Set to True to proceed with XML creation

                    self.append_to_text_widget(self.sync_text,
                        f"Created path mappings for {len(file_mapping)} tracks pointing to destination library\n\n")
                else:
                    self.status_var.set("Step 2: Updating DJ Library...")
                    self.append_to_text_widget(self.sync_text, "===== STEP 2: UPDATING DJ LIBRARY =====\n")
                    self.results_notebook.select(1)

                    step2_start = time.time()
                    synced_tracks, file_mapping = self.update_dj_library(
                        tracks_to_copy, dj_library, skip_existing, convert_to_flac, preserve_album_art, source_path_to_playlists)

                    step2_time = time.time() - step2_start
                    self.append_to_text_widget(self.sync_text, f"Step 2 completed in {step2_time:.2f} seconds\n\n")

                if synced_tracks:
                    self.status_var.set("Step 3: Creating updated XML...")
                    self.append_to_text_widget(self.xml_text, "===== STEP 3: CREATING UPDATED XML =====\n")
                    self.results_notebook.select(2)

                    step3_start = time.time()
                    self.create_new_xml(
                        itunes_xml, export_xml, selected_playlist_data, tracks_metadata, file_mapping, dj_library, export_xml_only)

                    step3_time = time.time() - step3_start
                    self.append_to_text_widget(self.xml_text, f"Step 3 completed in {step3_time:.2f} seconds\n\n")

            # Delete removed tracks if option is enabled
            if self.delete_removed_var.get() and not export_xml_only:
                delete_start = time.time()
                self.append_to_text_widget(self.sync_text, "\nStep 4: Deleting removed tracks...\n")
                self.status_var.set("Deleting removed tracks...")

                deleted_count = self.delete_removed_tracks(dj_library, export_xml)

                delete_time = time.time() - delete_start
                self.append_to_text_widget(self.sync_text, f"Step 4 completed in {delete_time:.2f} seconds - Deleted {deleted_count} file(s)\n\n")

            total_time = time.time() - start_time
            self.append_to_text_widget(self.xml_text, f"\nTotal sync process completed in {total_time:.2f} seconds\n")
            self.status_var.set("Sync complete!")

            self.root.after(0, lambda: messagebox.showinfo("Sync Complete",
                f"Playlist Sync completed in {total_time:.2f} seconds."))
            self.config_manager.update_settings(last_mode="export")
            self.root.after(0, self._save_settings_silent)

        except Exception as e:
            error_msg = f"Error during sync process: {str(e)}"
            logger.error(error_msg)
            self.append_to_text_widget(self.sync_text, f"\n{error_msg}\n")
            self.status_var.set("Error during sync")
            self.root.after(0, lambda: messagebox.showerror("Sync Error", error_msg))
        
        finally:
            self.root.after(0, self.enable_buttons)
            self.syncing = False
    
    def enable_buttons(self):
        self.sync_btn.config(state=tk.NORMAL)
        self.export_xml_btn.config(state=tk.NORMAL)
        self.cancel_btn.config(state=tk.DISABLED)

    def _cancel_sync(self):
        """Cancel the current sync operation."""
        self.syncing = False
        self.status_var.set("Cancelling...")
    
    def append_to_text_widget(self, text_widget, message):
        def update_text():
            text_widget.insert(tk.END, message)
            text_widget.see(tk.END)
            text_widget.update_idletasks()
        self.root.after(0, update_text)
    
    def check_ffmpeg(self):
        try:
            process_args = self.get_subprocess_args()
            subprocess.run(["ffmpeg", "-version"], **process_args)
            return True
        except:
            return False

    def check_flac(self):
        try:
            process_args = self.get_subprocess_args()
            subprocess.run(["flac", "--version"], **process_args)
            return True
        except:
            return False
    
    def safe_int_conversion(self, value):
        """Safely convert a value to an integer, handling decimal points."""
        try:
            # First try direct conversion
            return int(value)
        except ValueError:
            try:
                # If it has a decimal point, convert to float first then int
                return int(float(value))
            except (ValueError, TypeError):
                # If all else fails, return 0 or some default value
                return 0

    def get_track_title(self, track_path):
        """Extract track title from audio file metadata using ffprobe"""
        try:
            process_args = self.get_subprocess_args()
            
            # Use ffprobe to extract metadata
            result = subprocess.run([
                "ffprobe",
                "-v", "quiet",
                "-show_entries", "format_tags=title",
                "-of", "csv=p=0",
                track_path
            ], **process_args)
            
            if result.returncode == 0 and result.stdout:
                title = result.stdout.decode('utf-8').strip()
                if title and title != "N/A":
                    return self.sanitize_filename(title)
            
            # Fallback: try different tag variations
            for tag in ["TITLE", "Title", "title"]:
                result = subprocess.run([
                    "ffprobe",
                    "-v", "quiet",
                    "-show_entries", f"format_tags={tag}",
                    "-of", "csv=p=0",
                    track_path
                ], **process_args)
                
                if result.returncode == 0 and result.stdout:
                    title = result.stdout.decode('utf-8').strip()
                    if title and title != "N/A":
                        return self.sanitize_filename(title)
            
            # If no title found, return None to use original filename
            return None
            
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Warning: Could not extract title from {os.path.basename(track_path)}: {e}\n")
            return None

    def sanitize_filename(self, title):
        """Sanitize track title for use as filename"""
        # Remove or replace invalid filename characters
        invalid_chars = '<>:"/\\|?*'
        sanitized = title
        
        for char in invalid_chars:
            sanitized = sanitized.replace(char, '')
        
        # Replace multiple spaces with single space
        sanitized = ' '.join(sanitized.split())
        
        # Remove leading/trailing spaces and dots
        sanitized = sanitized.strip('. ')

        # Limit length to avoid filesystem issues
        if len(sanitized) > 200:
            sanitized = sanitized[:200].strip()

        # If sanitization results in empty string, return None
        if not sanitized:
            return None

        return sanitized

    def find_album_art(self, track_path):
        """Find album art image in the same folder as the track."""
        track_dir = os.path.dirname(track_path)
        try:
            for f in os.listdir(track_dir):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp')):
                    return os.path.join(track_dir, f)
        except OSError:
            pass
        return None

    def read_flac_comment_metadata(self, flac_path):
        """Read the COMMENT metadata field from a FLAC file using metaflac"""
        if not flac_path.lower().endswith('.flac'):
            return None

        try:
            process_args = self.get_subprocess_args()

            # Use metaflac to read COMMENT field
            result = subprocess.run([
                "metaflac",
                "--show-tag=COMMENT",
                flac_path
            ], **process_args)

            if result.returncode == 0 and result.stdout:
                output = result.stdout.decode('utf-8').strip()
                # Output format is "COMMENT=value", extract the value
                if output.startswith("COMMENT="):
                    return output[8:]  # Remove "COMMENT=" prefix

            return None

        except Exception as e:
            return None

    def read_flac_genre_metadata(self, flac_path):
        """Read the GENRE metadata field from a FLAC file using metaflac"""
        if not flac_path.lower().endswith('.flac'):
            return None

        try:
            process_args = self.get_subprocess_args()

            # Use metaflac to read GENRE field
            result = subprocess.run([
                "metaflac",
                "--show-tag=GENRE",
                flac_path
            ], **process_args)

            if result.returncode == 0 and result.stdout:
                output = result.stdout.decode('utf-8').strip()
                # Output format is "GENRE=value", extract the value
                if output.startswith("GENRE="):
                    return output[6:]  # Remove "GENRE=" prefix

            return None

        except Exception as e:
            return None

    def write_flac_genre_metadata(self, flac_path, genre):
        """Write genre to the FLAC GENRE metadata field using metaflac"""
        if not genre or not flac_path.lower().endswith('.flac'):
            return False

        try:
            process_args = self.get_subprocess_args()

            # Use metaflac to write GENRE field
            result = subprocess.run([
                "metaflac",
                "--remove-tag=GENRE",
                f"--set-tag=GENRE={genre}",
                flac_path
            ], **process_args)

            if result.returncode == 0:
                return True
            else:
                return False

        except Exception as e:
            return False

    def write_flac_comment_metadata(self, flac_path, playlist_names, temp_dir):
        """Write playlist names to the FLAC COMMENT metadata field using metaflac"""
        if not playlist_names or not flac_path.lower().endswith('.flac'):
            return False

        try:
            # Join playlist names with comma separator
            comment_value = ", ".join(playlist_names)

            process_args = self.get_subprocess_args()

            # Use metaflac to directly write to COMMENT field (avoids FFmpeg field mapping issues)
            # First remove any existing COMMENT and DESCRIPTION tags
            result = subprocess.run([
                "metaflac",
                "--remove-tag=COMMENT",
                "--remove-tag=DESCRIPTION",
                f"--set-tag=COMMENT={comment_value}",
                flac_path
            ], **process_args)

            if result.returncode == 0:
                return True
            else:
                return False

        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Warning: Could not write comment metadata to {os.path.basename(flac_path)}: {e}\n")
            return False

    def update_existing_metadata(self, tracks, dj_library, convert_to_flac, source_path_to_playlists, tracks_metadata):
        """Update FLAC metadata for existing files in the DJ Library with current playlist and genre information from iTunes XML"""
        self.append_to_text_widget(self.sync_text, f"Updating metadata for existing FLAC files in {dj_library}...\n")

        if not tracks:
            return 0

        updated_count = 0
        skipped_count = 0
        unchanged_count = 0
        error_count = 0
        total_tracks = len(tracks)

        # Create a temporary directory for metadata operations in the DJ Library location
        # This ensures it's on the same drive with sufficient space
        temp_dir = tempfile.mkdtemp(dir=dj_library)

        try:
            for i, track_path in enumerate(tracks):
                # Show progress periodically
                if (i + 1) % 100 == 0:
                    progress_pct = (i + 1) * 100 / total_tracks
                    self.status_var.set(f"Checking metadata: {i+1}/{total_tracks} ({progress_pct:.1f}%)")

                try:
                    # Get the destination path (same logic as in create_path_mapping/update_dj_library)
                    file_ext = os.path.splitext(track_path)[1].lower()
                    original_base_name = os.path.splitext(os.path.basename(track_path))[0]

                    # Extract track title from metadata
                    track_title = self.get_track_title(track_path)
                    base_name = track_title if track_title else original_base_name

                    # Determine destination extension
                    dest_ext = ".flac" if convert_to_flac and file_ext != '.flac' else file_ext
                    dest_path = os.path.join(dj_library, base_name + dest_ext)

                    # Handle filename conflicts - check for numbered variants
                    # If the base file doesn't exist, look for -01, -02, etc.
                    actual_dest_path = None
                    if os.path.exists(dest_path):
                        actual_dest_path = dest_path
                    else:
                        # Check for conflict-renamed versions
                        for counter in range(1, 100):  # Check up to 99
                            numbered_path = os.path.join(dj_library, f"{base_name} -{counter:02d}{dest_ext}")
                            if os.path.exists(numbered_path):
                                actual_dest_path = numbered_path
                                break

                    # Check if file exists and is FLAC
                    if actual_dest_path and actual_dest_path.lower().endswith('.flac'):
                        # Get playlist names and genre for this track from iTunes XML
                        playlist_names = source_path_to_playlists.get(track_path, [])

                        # Find the track metadata by matching the file path
                        itunes_genre = None
                        for track_id, metadata in tracks_metadata.items():
                            if metadata.get('file_path') == track_path:
                                itunes_genre = metadata.get('Genre')
                                break

                        # Read current metadata from FLAC file
                        current_comment = self.read_flac_comment_metadata(actual_dest_path)
                        current_genre = self.read_flac_genre_metadata(actual_dest_path)

                        # Prepare new metadata values
                        new_comment = ", ".join(playlist_names) if playlist_names else None
                        new_genre = itunes_genre

                        # Check if either comment or genre needs updating
                        comment_needs_update = (new_comment and current_comment != new_comment)
                        genre_needs_update = (new_genre and current_genre != new_genre)

                        if comment_needs_update or genre_needs_update:
                            # Update metadata that has changed
                            if comment_needs_update:
                                if not self.write_flac_comment_metadata(actual_dest_path, playlist_names, temp_dir):
                                    error_count += 1

                            if genre_needs_update:
                                if not self.write_flac_genre_metadata(actual_dest_path, new_genre):
                                    error_count += 1

                            updated_count += 1
                            if updated_count % 50 == 0:
                                progress_pct = (i + 1) * 100 / total_tracks
                                self.status_var.set(f"Updating metadata: {i+1}/{total_tracks} ({progress_pct:.1f}%)")
                                self.append_to_text_widget(
                                    self.sync_text,
                                    f"Progress: {updated_count} updated, {unchanged_count} unchanged, {skipped_count} skipped\n"
                                )
                        else:
                            # Metadata is already correct
                            unchanged_count += 1
                    else:
                        # File doesn't exist or is not FLAC
                        skipped_count += 1

                except Exception as e:
                    error_count += 1
                    self.append_to_text_widget(
                        self.sync_text,
                        f"Error updating metadata for {os.path.basename(track_path)}: {e}\n"
                    )

            # Final summary
            self.append_to_text_widget(
                self.sync_text,
                f"\nMetadata update summary:\n"
                f"  - Updated: {updated_count} files (playlist info changed)\n"
                f"  - Unchanged: {unchanged_count} files (already up to date)\n"
                f"  - Skipped: {skipped_count} files (not FLAC or not found)\n"
                f"  - Errors: {error_count} files\n"
            )

        finally:
            # Clean up temp directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass

        return updated_count

    def extract_embedded_art(self, source_path, temp_dir):
        """Extract embedded album art from audio file using FFmpeg"""
        try:
            # Create a temporary file for the extracted art
            art_path = os.path.join(temp_dir, "embedded_art.jpg")
            
            process_args = self.get_subprocess_args()
            
            # Try to extract embedded artwork
            result = subprocess.run([
                "ffmpeg",
                "-i", source_path,
                "-an",  # No audio
                "-vcodec", "copy",
                "-y",  # Overwrite output
                art_path
            ], **process_args)
            
            if result.returncode == 0 and os.path.exists(art_path) and os.path.getsize(art_path) > 0:
                return art_path
            else:
                return None
                
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Warning: Could not extract embedded art: {e}\n")
            return None
    
    def analyze_library(self, xml_path, debug_missing=True):
        """
        Analyze the iTunes library XML and extract the selected playlists and their tracks.
        This is a complete rewrite of get_playlist_tracks to properly extract all needed data.
        """
        self.append_to_text_widget(self.analysis_text, f"Analyzing iTunes XML: {xml_path}...\n")
        
        try:
            # Check if we already parsed the XML in load_playlists
            if self.itunes_root is None:
                with open(xml_path, 'r', encoding='utf-8') as f:
                    self.itunes_root = ET.fromstring(f.read())
        except Exception as e:
            self.append_to_text_widget(self.analysis_text, f"Error parsing XML: {e}\n")
            return [], [], {}, {}

        # Get the main dictionary element
        library_dict = next((child for child in self.itunes_root if child.tag == 'dict'), None)
        if library_dict is None:
            self.append_to_text_widget(self.analysis_text, "Invalid iTunes XML structure: missing main dict\n")
            return [], [], {}, {}

        # First, extract all tracks from the library
        tracks_metadata = {}
        tracks_to_copy = []
        track_ids_to_paths = {}
        
        # Find the Tracks dictionary
        tracks_element = None
        for i in range(len(library_dict)):
            if library_dict[i].tag == 'key' and library_dict[i].text == 'Tracks':
                if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'dict':
                    tracks_element = library_dict[i + 1]
                break
                
        if tracks_element is None:
            self.append_to_text_widget(self.analysis_text, "No tracks found in iTunes XML\n")
            return [], [], {}, {}
            
        # Process all tracks
        self.append_to_text_widget(self.analysis_text, "Processing tracks from iTunes library...\n")
        total_tracks = 0
        valid_tracks = 0
        
        # Extract track data
        for i in range(0, len(tracks_element), 2):
            if i+1 >= len(tracks_element):
                break
                
            if tracks_element[i].tag == 'key' and tracks_element[i+1].tag == 'dict':
                track_id = tracks_element[i].text
                track_dict = tracks_element[i+1]
                
                # Create a dictionary to store this track's metadata
                track_data = {}
                location = None
                file_path = None
                
                # Extract all properties for this track
                for j in range(0, len(track_dict), 2):
                    if j+1 >= len(track_dict):
                        break
                        
                    if track_dict[j].tag == 'key':
                        key = track_dict[j].text
                        value_element = track_dict[j+1]
                        
                        # Store the value based on its type
                        if value_element.tag == 'string':
                            track_data[key] = value_element.text
                        elif value_element.tag == 'integer':
                            # Use safe conversion for integers
                            try:
                                track_data[key] = self.safe_int_conversion(value_element.text)
                            except:
                                track_data[key] = 0
                        elif value_element.tag == 'date':
                            track_data[key] = value_element.text
                        elif value_element.tag == 'true':
                            track_data[key] = True
                        elif value_element.tag == 'false':
                            track_data[key] = False
                        
                        # If this is a location, decode the URL
                        if key == 'Location' and value_element.text:
                            location = value_element.text
                            if location.startswith("file://localhost/"):
                                file_path = urllib.parse.unquote(location.replace("file://localhost/", ""))
                            elif location.startswith("file:///"):
                                file_path = urllib.parse.unquote(location.replace("file:///", ""))
                            else:
                                file_path = urllib.parse.unquote(location)
                                if file_path.startswith("file://"):
                                    file_path = file_path.replace("file://", "")

                            # Convert to Windows path format
                            file_path = file_path.replace("/", "\\")
                
                total_tracks += 1
                
                # Store this track's metadata using its ID
                track_id_num = str(track_data.get('Track ID', ''))  # Make sure it's a string
                if track_id_num:
                    tracks_metadata[track_id_num] = track_data
                    
                    # If this track has a valid location, store it
                    if file_path:
                        track_data['file_path'] = file_path
                        track_data['exists'] = os.path.isfile(file_path)
                        track_data['location'] = location
                        file_ext = os.path.splitext(file_path)[1].lower()
                        track_data['extension'] = file_ext
                        
                        # Check if this is a valid audio file that we want to copy
                        if file_ext in VALID_EXTENSIONS and os.path.isfile(file_path):
                            valid_tracks += 1
                            track_ids_to_paths[track_id_num] = file_path
                
                # Log progress every 500 tracks
                if total_tracks % 500 == 0:
                    self.append_to_text_widget(
                        self.analysis_text, 
                        f"Processed {total_tracks} tracks ({valid_tracks} valid)...\n"
                    )
        
        self.append_to_text_widget(
            self.analysis_text, 
            f"Found {valid_tracks} valid audio tracks out of {total_tracks} total tracks\n"
        )
        
        # Now extract the selected playlists
        selected_playlist_data = []
        selected_playlists = self.get_selected_playlists()
        
        self.append_to_text_widget(
            self.analysis_text, 
            f"Selected playlists to process: {', '.join(selected_playlists)}\n"
        )
        
        # Find the Playlists array
        playlists_element = None
        for i in range(len(library_dict)):
            if library_dict[i].tag == 'key' and library_dict[i].text == 'Playlists':
                if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'array':
                    playlists_element = library_dict[i + 1]
                break
                
        if playlists_element is None:
            self.append_to_text_widget(self.analysis_text, "No playlists found in iTunes XML\n")
            return [], [], {}, {}
        
        # Process playlists
        self.append_to_text_widget(self.analysis_text, "Processing playlists...\n")
        missing_tracks = {}
        playlist_count = 0

        # Build mapping of track_id -> ALL playlist names (for comment metadata)
        track_id_to_all_playlists = {}

        for playlist_dict in playlists_element:
            if playlist_dict.tag != 'dict':
                continue
                
            # Extract playlist metadata
            playlist_data = {}
            playlist_items = []
            
            for j in range(0, len(playlist_dict), 2):
                if j+1 >= len(playlist_dict):
                    break
                    
                if playlist_dict[j].tag == 'key':
                    key = playlist_dict[j].text
                    value_element = playlist_dict[j+1]
                    
                    if key == 'Name' and value_element.tag == 'string':
                        playlist_data['Name'] = value_element.text
                    elif key == 'Playlist ID' and value_element.tag == 'integer':
                        # Use safe conversion
                        try:
                            playlist_data['ID'] = self.safe_int_conversion(value_element.text)
                        except:
                            playlist_data['ID'] = 0
                    elif key == 'Playlist Items' and value_element.tag == 'array':
                        # Extract all track IDs in this playlist
                        for item_dict in value_element:
                            if item_dict.tag == 'dict':
                                for k in range(0, len(item_dict), 2):
                                    if k+1 >= len(item_dict):
                                        break
                                    if item_dict[k].text == 'Track ID' and item_dict[k+1].tag == 'integer':
                                        # Use safe conversion
                                        try:
                                            track_id = str(self.safe_int_conversion(item_dict[k+1].text))
                                            playlist_items.append(track_id)
                                        except:
                                            # Just skip problematic track IDs
                                            pass
            
            playlist_name = playlist_data.get('Name', 'Unknown')
            playlist_id = playlist_data.get('ID', 'Unknown')

            # Add all tracks to the track_id -> playlists mapping (for ALL playlists, not just selected)
            # Skip system playlists like Library, Music, etc.
            system_playlists = ["Library", "Music", "Downloaded", "Podcasts", "Audiobooks", "Movies", "TV Shows"]
            if playlist_name not in system_playlists and playlist_items:
                for track_id in playlist_items:
                    if track_id not in track_id_to_all_playlists:
                        track_id_to_all_playlists[track_id] = []
                    if playlist_name not in track_id_to_all_playlists[track_id]:
                        track_id_to_all_playlists[track_id].append(playlist_name)

            # Check if this playlist should be included based on our selection
            if playlist_name in selected_playlists:
                playlist_count += 1
                valid_tracks_in_playlist = 0
                missing_tracks_in_playlist = 0
                
                # Add the valid tracks from this playlist to our list of tracks to copy
                for track_id in playlist_items:
                    if track_id in track_ids_to_paths:
                        # This is a valid track - add it to our copy list
                        track_path = track_ids_to_paths[track_id]
                        tracks_to_copy.append(track_path)
                        valid_tracks_in_playlist += 1
                        # Debug: log each track being added
                        if playlist_name == "Bew" or len(playlist_items) <= 10:  # Log for test playlists
                            track_name = tracks_metadata.get(track_id, {}).get('Name', 'Unknown')
                            self.append_to_text_widget(
                                self.analysis_text,
                                f"  + Adding track ID {track_id}: {track_name} -> {os.path.basename(track_path)}\n"
                            )
                    else:
                        # This track is missing or invalid - track for debugging
                        missing_tracks_in_playlist += 1
                        if debug_missing:
                            if track_id not in missing_tracks:
                                if track_id in tracks_metadata:
                                    track_info = tracks_metadata[track_id]
                                    missing_tracks[track_id] = {
                                        'name': track_info.get('Name', 'Unknown'),
                                        'artist': track_info.get('Artist', 'Unknown'),
                                        'playlists': [playlist_name]
                                    }
                                else:
                                    missing_tracks[track_id] = {
                                        'error': "Track ID not found in library",
                                        'playlists': [playlist_name]
                                    }
                            else:
                                missing_tracks[track_id]['playlists'].append(playlist_name)
                
                # Store information about this playlist
                selected_playlist_data.append({
                    'name': playlist_name,
                    'id': playlist_id,
                    'track_ids': playlist_items
                })
                
                self.append_to_text_widget(
                    self.analysis_text,
                    f"✓ Playlist: {playlist_name} (ID: {playlist_id}) with {len(playlist_items)} tracks " + 
                    f"({valid_tracks_in_playlist} valid, {missing_tracks_in_playlist} missing)\n"
                )
            else:
                self.append_to_text_widget(self.analysis_text, f"- Skipping playlist: {playlist_name}\n")
        
        # Remove duplicates from tracks_to_copy
        tracks_before_dedup = len(tracks_to_copy)
        tracks_to_copy = list(set(tracks_to_copy))
        tracks_after_dedup = len(tracks_to_copy)

        if tracks_before_dedup != tracks_after_dedup:
            self.append_to_text_widget(
                self.analysis_text,
                f"\nRemoved {tracks_before_dedup - tracks_after_dedup} duplicate track references " +
                f"({tracks_before_dedup} → {tracks_after_dedup} unique tracks)\n"
            )
        
        self.append_to_text_widget(
            self.analysis_text, 
            f"\nSelected {playlist_count} playlists with {len(tracks_to_copy)} unique valid tracks\n"
        )
        
        # Report on missing tracks
        if debug_missing and missing_tracks:
            self.append_to_text_widget(self.analysis_text, "\n==== MISSING TRACKS ANALYSIS ====\n")
            self.append_to_text_widget(self.analysis_text, f"Total missing tracks: {len(missing_tracks)}\n")
            
            # Categorize missing tracks
            no_location_count = 0
            file_not_found_count = 0
            unsupported_format_count = 0
            other_error_count = 0
            
            for track_id, info in missing_tracks.items():
                if "error" in info:
                    other_error_count += 1
                elif track_id in tracks_metadata:
                    track_info = tracks_metadata[track_id]
                    if 'Location' not in track_info:
                        no_location_count += 1
                    elif not track_info.get('exists', False):
                        file_not_found_count += 1
                    elif track_info.get('extension') not in VALID_EXTENSIONS:
                        unsupported_format_count += 1
                    else:
                        other_error_count += 1
                else:
                    other_error_count += 1
            
            self.append_to_text_widget(self.analysis_text, f"  - Tracks with no file location: {no_location_count}\n")
            self.append_to_text_widget(self.analysis_text, f"  - Files not found on disk: {file_not_found_count}\n")
            self.append_to_text_widget(self.analysis_text, f"  - Unsupported file formats: {unsupported_format_count}\n")
            self.append_to_text_widget(self.analysis_text, f"  - Other errors: {other_error_count}\n\n")
            
            # If detailed debugging is enabled, show more info
            if self.debug_var.get():
                self.append_to_text_widget(self.analysis_text, "Detailed missing tracks information:\n")
                for track_id, info in missing_tracks.items():
                    track_name = info.get("name", "Unknown")
                    artist = info.get("artist", "Unknown")
                    playlists = ", ".join(info.get("playlists", []))
                    
                    if "error" in info:
                        self.append_to_text_widget(self.analysis_text, 
                            f"Track ID {track_id}: {info['error']} (Playlists: {playlists})\n")
                    elif track_id in tracks_metadata:
                        track_info = tracks_metadata[track_id]
                        if 'Location' not in track_info:
                            self.append_to_text_widget(self.analysis_text, 
                                f"'{track_name}' by {artist} has no file location (Playlists: {playlists})\n")
                        elif not track_info.get('exists', False):
                            self.append_to_text_widget(self.analysis_text, 
                                f"'{track_name}' by {artist} - file not found: {track_info.get('file_path', 'Unknown')} (Playlists: {playlists})\n")
                        elif track_info.get('extension') not in VALID_EXTENSIONS:
                            self.append_to_text_widget(self.analysis_text, 
                                f"'{track_name}' by {artist} - unsupported format: {track_info.get('extension', 'Unknown')} (Playlists: {playlists})\n")
                        else:
                            self.append_to_text_widget(self.analysis_text,
                                f"'{track_name}' by {artist} - unknown error (Playlists: {playlists})\n")

        # Convert track_id -> playlists mapping to source_path -> playlists mapping
        source_path_to_playlists = {}
        for track_id, playlist_names in track_id_to_all_playlists.items():
            if track_id in tracks_metadata:
                file_path = tracks_metadata[track_id].get('file_path')
                if file_path:
                    source_path_to_playlists[file_path] = playlist_names

        self.append_to_text_widget(self.analysis_text,
            f"Built playlist membership info for {len(source_path_to_playlists)} tracks\n")

        return selected_playlist_data, tracks_to_copy, tracks_metadata, source_path_to_playlists
    
    def create_path_mapping(self, tracks, dj_library, convert_to_flac=True):
        """
        Create a mapping from original file paths to destination DJ library paths
        without actually copying files. Used in export-xml-only mode.
        """
        file_mapping = {}

        for track_path in tracks:
            try:
                file_ext = os.path.splitext(track_path)[1].lower()
                original_base_name = os.path.splitext(os.path.basename(track_path))[0]

                # Extract track title from metadata
                track_title = self.get_track_title(track_path)
                base_name = track_title if track_title else original_base_name

                # Determine destination extension
                dest_ext = ".flac" if convert_to_flac and file_ext != '.flac' else file_ext
                dest_path = os.path.join(dj_library, base_name + dest_ext)
                original_dest_path = dest_path

                # Handle filename conflicts with OTHER tracks in current session.
                # Mirror update_dj_library's " -01"/" -02" numbering so paths match
                # the files the original sync actually wrote to disk. Do NOT renumber
                # just because the file exists — in export-xml-only mode every
                # mapped file is expected to already be on disk.
                if dest_path in file_mapping.values() and dest_path != file_mapping.get(track_path):
                    # Renumber the first occurrence to " -01"
                    name_without_ext = os.path.splitext(original_dest_path)[0]
                    for src_path, dst_path in file_mapping.items():
                        if dst_path == original_dest_path:
                            file_mapping[src_path] = f"{name_without_ext} -01{dest_ext}"
                            break

                    # Find the next available "-NN" suffix for this track
                    counter = 2
                    dest_path = f"{name_without_ext} -{counter:02d}{dest_ext}"
                    while dest_path in file_mapping.values() and dest_path != file_mapping.get(track_path):
                        counter += 1
                        dest_path = f"{name_without_ext} -{counter:02d}{dest_ext}"

                file_mapping[track_path] = dest_path

            except Exception as e:
                self.append_to_text_widget(self.sync_text,
                    f"Warning: Could not create mapping for {os.path.basename(track_path)}: {e}\n")
                # Fall back to simple path mapping
                file_mapping[track_path] = os.path.join(dj_library, os.path.basename(track_path))

        return file_mapping

    def update_dj_library(self, tracks, dj_library, skip_existing=True, convert_to_flac=True, preserve_album_art=True, source_path_to_playlists=None):
        """Update the DJ Library by copying tracks and optionally converting to FLAC with album art.
        If source_path_to_playlists is provided, writes playlist names to FLAC comment metadata."""
        if source_path_to_playlists is None:
            source_path_to_playlists = {}
        self.append_to_text_widget(self.sync_text, f"Updating {dj_library} with {len(tracks)} tracks...\n")
        if not tracks:
            self.append_to_text_widget(self.sync_text, "No tracks to copy!\n")
            return [], {}

        if not os.path.exists(dj_library):
            os.makedirs(dj_library)
            self.append_to_text_widget(self.sync_text, f"Created DJ Library directory: {dj_library}\n")

        copied_count = 0
        converted_count = 0
        skipped_count = 0
        skipped_flac_count = 0
        error_count = 0
        album_art_embedded_count = 0
        comment_metadata_count = 0
        synced_tracks = []
        
        # Create a mapping from original file path to DJ library path
        file_mapping = {}
        
        total_tracks = len(tracks)
        track_list = list(tracks)

        # Create a temporary directory for album art operations in the DJ Library location
        # This ensures it's on the same drive with sufficient space
        temp_dir = tempfile.mkdtemp(dir=dj_library)
        
        try:
            for i, track_path in enumerate(track_list):
                try:
                    file_ext = os.path.splitext(track_path)[1].lower()
                    original_base_name = os.path.splitext(os.path.basename(track_path))[0]
                    
                    # Extract track title from metadata
                    track_title = self.get_track_title(track_path)
                    base_name = track_title if track_title else original_base_name
                    
                    dest_ext = ".flac" if convert_to_flac and file_ext != '.flac' else file_ext
                    dest_path = os.path.join(dj_library, base_name + dest_ext)

                    # Handle filename conflicts with OTHER tracks in current session
                    # When duplicates exist, ALL instances get numbered starting from -01
                    original_dest_path = dest_path

                    # Check if this filename is already used by another track in this session
                    if dest_path in file_mapping.values() and dest_path != file_mapping.get(track_path):
                        # Find the first track that's using this base name (without number)
                        first_track_path = None
                        for src_path, dst_path in file_mapping.items():
                            if dst_path == original_dest_path:
                                first_track_path = src_path
                                break

                        # Rename the first occurrence to add -01
                        if first_track_path:
                            name_without_ext = os.path.splitext(original_dest_path)[0]
                            new_first_dest = f"{name_without_ext} -01{dest_ext}"
                            old_first_dest = file_mapping[first_track_path]

                            # Update the mapping
                            file_mapping[first_track_path] = new_first_dest

                            # If the file was already copied, rename it on disk
                            if os.path.exists(old_first_dest) and not os.path.exists(new_first_dest):
                                try:
                                    os.rename(old_first_dest, new_first_dest)
                                    self.append_to_text_widget(
                                        self.sync_text,
                                        f"→ Renumbered first occurrence: {os.path.basename(new_first_dest)}\n"
                                    )
                                except Exception as e:
                                    self.append_to_text_widget(
                                        self.sync_text,
                                        f"⚠ Could not rename {os.path.basename(old_first_dest)}: {e}\n"
                                    )

                        # Now find the next available number for this track
                        counter = 2  # Start from -02 since first one is now -01
                        name_without_ext = os.path.splitext(original_dest_path)[0]
                        dest_path = f"{name_without_ext} -{counter:02d}{dest_ext}"

                        # Keep incrementing until we find an unused number
                        while dest_path in file_mapping.values() and dest_path != file_mapping.get(track_path):
                            counter += 1
                            dest_path = f"{name_without_ext} -{counter:02d}{dest_ext}"

                        self.append_to_text_widget(
                            self.sync_text,
                            f"→ Numbered duplicate: {os.path.basename(dest_path)}\n"
                        )

                    # NOW check if file already exists and we should skip it (from previous run)
                    if os.path.exists(dest_path) and skip_existing:
                        # File exists and we want to skip - use the existing file
                        file_mapping[track_path] = dest_path
                        synced_tracks.append(dest_path)
                        skipped_count += 1

                        # Log the skip
                        self.append_to_text_widget(
                            self.sync_text,
                            f"⊳ Skipping track {i+1}/{total_tracks}: {base_name} (file already exists)\n"
                        )

                        if i % 20 == 0 or i+1 == total_tracks:
                            progress_pct = (i + 1) * 100 / total_tracks
                            self.status_var.set(f"Processing track {i+1}/{total_tracks} ({progress_pct:.1f}%)")
                            self.append_to_text_widget(
                                self.sync_text,
                                f"Progress: {i+1}/{total_tracks} tracks processed ({skipped_count} skipped, {copied_count} copied)\n"
                            )
                        continue  # Skip to next track
                    
                    # Add this mapping regardless of whether we copy the file
                    file_mapping[track_path] = dest_path
                    synced_tracks.append(dest_path)
                    
                    is_flac = file_ext == '.flac'
                    
                    progress_pct = (i + 1) * 100 / total_tracks
                    if track_title:
                        progress_msg = f"Processing track {i+1}/{total_tracks} ({progress_pct:.1f}%): {track_title}\n"
                    else:
                        progress_msg = f"Processing track {i+1}/{total_tracks} ({progress_pct:.1f}%): {os.path.basename(track_path)}\n"
                    self.status_var.set(f"Processing track {i+1}/{total_tracks} ({progress_pct:.1f}%)")
                    
                    if os.path.exists(dest_path):
                        # File exists but skip_existing is False - log and continue
                        self.append_to_text_widget(self.sync_text, f"{progress_msg}File already exists, will overwrite: {os.path.basename(dest_path)}\n")
                    
                    # Copy or convert the file (either it doesn't exist, or we're overwriting)
                    if is_flac or not convert_to_flac:
                        # For FLAC files or when not converting, copy directly but potentially add album art
                        if is_flac and preserve_album_art and convert_to_flac:
                            self.append_to_text_widget(
                                self.sync_text, 
                                f"{progress_msg}Copying FLAC file with album art check: {os.path.basename(track_path)}\n"
                            )
                            success = self.copy_with_album_art(track_path, dest_path, temp_dir)
                            if success:
                                album_art_embedded_count += 1
                        else:
                            self.append_to_text_widget(
                                self.sync_text, 
                                f"{progress_msg}Copying directly: {os.path.basename(track_path)}\n"
                            )
                            shutil.copy2(track_path, dest_path)
                        
                        copied_count += 1
                        if is_flac:
                            skipped_flac_count += 1

                        # Write playlist names to FLAC comment metadata if destination is FLAC
                        if dest_path.lower().endswith('.flac') and track_path in source_path_to_playlists:
                            playlist_names = source_path_to_playlists[track_path]
                            if self.write_flac_comment_metadata(dest_path, playlist_names, temp_dir):
                                comment_metadata_count += 1
                                self.append_to_text_widget(
                                    self.sync_text,
                                    f"  → Added playlist info to comment: {', '.join(playlist_names)}\n"
                                )
                    elif convert_to_flac:
                        self.append_to_text_widget(
                            self.sync_text, 
                            f"{progress_msg}Converting to FLAC: {os.path.basename(track_path)}\n"
                        )
                        success, has_art = self.convert_to_flac(track_path, dest_path, temp_dir, preserve_album_art)
                        if success:
                            converted_count += 1
                            copied_count += 1
                            if has_art:
                                album_art_embedded_count += 1
                            self.append_to_text_widget(
                                self.sync_text,
                                f"✓ Conversion to FLAC complete for: {os.path.basename(track_path)}\n"
                            )
                            # Write playlist names to FLAC comment metadata
                            if track_path in source_path_to_playlists:
                                playlist_names = source_path_to_playlists[track_path]
                                if self.write_flac_comment_metadata(dest_path, playlist_names, temp_dir):
                                    comment_metadata_count += 1
                                    self.append_to_text_widget(
                                        self.sync_text,
                                        f"  → Added playlist info to comment: {', '.join(playlist_names)}\n"
                                    )
                        else:
                            self.append_to_text_widget(
                                self.sync_text,
                                f"⚠ Conversion failed, falling back to regular copy for {os.path.basename(track_path)}\n"
                            )
                            try:
                                # Fall back to copying the original format
                                dest_path = os.path.join(dj_library, base_name + file_ext)
                                file_mapping[track_path] = dest_path  # Update the mapping
                                shutil.copy2(track_path, dest_path)
                                copied_count += 1
                            except Exception as e:
                                self.append_to_text_widget(self.sync_text, f"✗ Fallback copy also failed: {str(e)}\n")
                                error_count += 1
                except Exception as e:
                    self.append_to_text_widget(self.sync_text, f"✗ Error processing {track_path}: {e}\n")
                    error_count += 1
        finally:
            # Clean up temporary directory
            try:
                shutil.rmtree(temp_dir)
            except:
                pass
        
        self.append_to_text_widget(self.sync_text, f"\n=== UPDATE SUMMARY ===\n")
        self.append_to_text_widget(self.sync_text, f"Total tracks processed: {total_tracks}\n")
        self.append_to_text_widget(self.sync_text, f"Files copied: {copied_count}\n")
        self.append_to_text_widget(self.sync_text, f"Files converted to FLAC: {converted_count}\n")
        self.append_to_text_widget(self.sync_text, f"Files with album art embedded: {album_art_embedded_count}\n")
        self.append_to_text_widget(self.sync_text, f"Files with playlist info in comment: {comment_metadata_count}\n")
        self.append_to_text_widget(self.sync_text, f"Files skipped (already exist): {skipped_count}\n")
        self.append_to_text_widget(self.sync_text, f"FLAC files copied directly: {skipped_flac_count}\n")
        self.append_to_text_widget(self.sync_text, f"Errors encountered: {error_count}\n")
        self.append_to_text_widget(self.sync_text, f"Total files in DJ Library: {len(os.listdir(dj_library))}\n")
        self.append_to_text_widget(self.sync_text, f"=====================\n\n")
        return synced_tracks, file_mapping

    def copy_with_album_art(self, source_path, dest_path, temp_dir):
        """Copy a FLAC file and add external album art if found"""
        try:
            # First, copy the file normally
            shutil.copy2(source_path, dest_path)
            
            # Then try to add external album art
            external_art = self.find_album_art(source_path)
            if external_art:
                self.append_to_text_widget(self.sync_text, f"Found external album art: {os.path.basename(external_art)}\n")
                
                # Use FFmpeg to add the album art
                temp_output = os.path.join(temp_dir, "temp_with_art.flac")
                
                process_args = self.get_subprocess_args()
                
                result = subprocess.run([
                    "ffmpeg",
                    "-i", dest_path,
                    "-i", external_art,
                    "-c", "copy",
                    "-disposition:v:0", "attached_pic",
                    "-y",
                    temp_output
                ], **process_args)
                
                if result.returncode == 0 and os.path.exists(temp_output):
                    # Replace the original with the version that has album art
                    shutil.move(temp_output, dest_path)
                    self.append_to_text_widget(self.sync_text, f"✓ Added album art to FLAC file\n")
                    return True
                else:
                    self.append_to_text_widget(self.sync_text, f"⚠ Failed to add album art, keeping original file\n")
            
            return False
            
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error adding album art: {e}\n")
            return False
    
    def convert_to_wav(self, source_path, wav_path):
        """Convert an audio file to WAV format using FFmpeg (first step for Traktor compatibility)"""
        try:
            file_ext = os.path.splitext(source_path)[1].lower()
            
            if file_ext == '.wav':
                shutil.copy2(source_path, wav_path)
                self.append_to_text_widget(
                    self.sync_text,
                    f"File is already in WAV format, copied directly: {os.path.basename(source_path)}\n"
                )
                return True
                
            self.append_to_text_widget(self.sync_text, f"Converting {file_ext} to WAV (step 1/2)...\n")
            
            # Use platform-specific arguments to hide console window
            process_args = self.get_subprocess_args()
            
            # Convert to WAV without any album art (clean conversion)
            process = subprocess.run([
                "ffmpeg", 
                "-i", source_path, 
                "-c:a", "pcm_s16le",  # Standard WAV encoding
                "-ar", "44100",       # Standard sample rate
                "-y",  # Overwrite output file if it exists
                wav_path
            ], check=True, **process_args)
            
            if os.path.exists(wav_path):
                wav_size = os.path.getsize(wav_path)
                self.append_to_text_widget(self.sync_text, f"WAV file created successfully ({wav_size} bytes)\n")
                return True
            else:
                self.append_to_text_widget(self.sync_text, "WARNING: WAV file was not created!\n")
                return False
                
        except subprocess.CalledProcessError as e:
            self.append_to_text_widget(self.sync_text, f"FFmpeg error during WAV conversion: {e}\n")
            self.append_to_text_widget(self.sync_text, f"Error output: {e.stderr.decode() if e.stderr else 'No error output'}\n")
            return False
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error converting file to WAV {source_path}: {e}\n")
            return False

    def convert_wav_to_flac(self, wav_path, flac_path, original_source_path, temp_dir, preserve_album_art=True):
        """Convert WAV to FLAC format with album art (second step for Traktor compatibility)"""
        try:
            self.append_to_text_widget(self.sync_text, f"Converting WAV to FLAC with album art (step 2/2)...\n")
            
            # Use platform-specific arguments to hide console window
            process_args = self.get_subprocess_args()
            
            has_album_art = False
            
            if preserve_album_art:
                # Find album art from the original source file location
                external_art = self.find_album_art(original_source_path)
                embedded_art = self.extract_embedded_art(original_source_path, temp_dir)
                
                art_to_use = embedded_art or external_art
                
                if art_to_use:
                    self.append_to_text_widget(self.sync_text, f"Embedding album art: {os.path.basename(art_to_use)}\n")
                    
                    # Convert WAV to FLAC with album art
                    process = subprocess.run([
                        "ffmpeg", 
                        "-i", wav_path,
                        "-i", art_to_use,
                        "-c:a", "flac",
                        "-c:v", "copy",
                        "-disposition:v:0", "attached_pic",
                        "-y",  # Overwrite output file if it exists
                        flac_path
                    ], check=True, **process_args)
                    
                    has_album_art = True
                else:
                    # Convert WAV to FLAC without album art
                    self.append_to_text_widget(self.sync_text, f"No album art found, converting WAV to FLAC without art\n")
                    process = subprocess.run([
                        "ffmpeg", 
                        "-i", wav_path, 
                        "-c:a", "flac",
                        "-y",  # Overwrite output file if it exists
                        flac_path
                    ], check=True, **process_args)
            else:
                # Convert WAV to FLAC without trying to preserve album art
                process = subprocess.run([
                    "ffmpeg", 
                    "-i", wav_path, 
                    "-c:a", "flac",
                    "-y",  # Overwrite output file if it exists
                    flac_path
                ], check=True, **process_args)
            
            if os.path.exists(flac_path):
                flac_size = os.path.getsize(flac_path)
                self.append_to_text_widget(self.sync_text, f"FLAC file created successfully ({flac_size} bytes)\n")
                return True, has_album_art
            else:
                self.append_to_text_widget(self.sync_text, "WARNING: FLAC file was not created!\n")
                return False, False
                
        except subprocess.CalledProcessError as e:
            self.append_to_text_widget(self.sync_text, f"FFmpeg error during FLAC conversion: {e}\n")
            self.append_to_text_widget(self.sync_text, f"Error output: {e.stderr.decode() if e.stderr else 'No error output'}\n")
            return False, False
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error converting WAV to FLAC {wav_path}: {e}\n")
            return False, False

    def convert_to_flac(self, source_path, dest_path, temp_dir, preserve_album_art=True):
        """Convert an audio file to FLAC format using two-step process: source → WAV → FLAC with album art"""
        try:
            file_ext = os.path.splitext(source_path)[1].lower()
            
            if file_ext == '.flac':
                # If already FLAC, use copy_with_album_art logic
                has_art = self.copy_with_album_art(source_path, dest_path, temp_dir)
                self.append_to_text_widget(
                    self.sync_text,
                    f"File is already in FLAC format, copied directly: {os.path.basename(source_path)}\n"
                )
                return True, has_art
                
            self.append_to_text_widget(self.sync_text, f"Converting {file_ext} to FLAC via WAV (for Traktor compatibility)...\n")
            
            # Create temporary WAV file
            base_name = os.path.splitext(os.path.basename(source_path))[0]
            temp_wav_path = os.path.join(temp_dir, f"{base_name}_temp.wav")
            
            try:
                # Step 1: Convert source to WAV
                if not self.convert_to_wav(source_path, temp_wav_path):
                    return False, False
                
                # Step 2: Convert WAV to FLAC with album art
                success, has_art = self.convert_wav_to_flac(temp_wav_path, dest_path, source_path, temp_dir, preserve_album_art)
                
                # Clean up temporary WAV file
                if os.path.exists(temp_wav_path):
                    os.remove(temp_wav_path)
                
                return success, has_art
                
            except Exception as e:
                # Clean up temporary WAV file on error
                if os.path.exists(temp_wav_path):
                    try:
                        os.remove(temp_wav_path)
                    except:
                        pass
                raise e
                
        except Exception as e:
            self.append_to_text_widget(self.sync_text, f"Error in two-step conversion for {source_path}: {e}\n")
            return False, False
    
    def update_playlist_items(self, playlist_dict, valid_track_ids):
        """
        Update a playlist's items to only include valid tracks.
        Returns the number of tracks kept in the playlist.
        """
        playlist_items = None
        playlist_items_index = None
        
        # Find the Playlist Items array
        for i in range(0, len(playlist_dict), 2):
            if i+1 >= len(playlist_dict):
                break
            if playlist_dict[i].tag == 'key' and playlist_dict[i].text == 'Playlist Items':
                if playlist_dict[i+1].tag == 'array':
                    playlist_items = playlist_dict[i+1]
                    playlist_items_index = i+1
                break
        
        if playlist_items is None:
            # No items in this playlist
            return 0
        
        # Count the valid tracks we keep
        kept_count = 0
        
        # No need to filter if it's a special playlist like "Library"
        for i in range(len(playlist_dict)):
            if playlist_dict[i].tag == 'key' and playlist_dict[i].text == 'Master':
                # This is a special playlist like Library - keep all items
                return len(playlist_items)
        
        # Create a new array with only valid tracks
        new_items_array = ET.Element('array')
        
        # Process each item
        for item_dict in playlist_items:
            if item_dict.tag != 'dict':
                continue
                
            # Extract the Track ID
            track_id = None
            for i in range(0, len(item_dict), 2):
                if i+1 >= len(item_dict):
                    break
                if item_dict[i].tag == 'key' and item_dict[i].text == 'Track ID':
                    # Get the track ID
                    try:
                        if item_dict[i+1].tag == 'integer':
                            track_id = str(self.safe_int_conversion(item_dict[i+1].text))
                        else:
                            track_id = item_dict[i+1].text
                    except:
                        track_id = str(item_dict[i+1].text)
                    break
            
            if track_id is not None and track_id in valid_track_ids:
                # This track is in our list of valid tracks - keep it
                new_items_array.append(copy.deepcopy(item_dict))
                kept_count += 1
        
        # Replace the items array
        playlist_dict[playlist_items_index] = new_items_array
        
        return kept_count
    
    def windows_to_mac_path(self, windows_path):
        """Convert a Windows path to Mac path format"""
        # Get the Mac DJ Library base path
        mac_base = self.mac_dj_library_var.get()
        
        # Extract just the filename from the Windows path
        filename = os.path.basename(windows_path)
        
        # Construct Mac path (using forward slashes)
        mac_path = f"{mac_base}/{filename}"
        
        return mac_path
    
    def create_new_xml(self, original_xml_path, export_xml_path, playlists_data, tracks_metadata, file_mapping, dj_library, export_xml_only=False):
        """
        Create a new iTunes-compatible XML file that only includes the selected playlists
        and points to the correct locations in the DJ Library (or Mac destination paths).
        The file_mapping provides the path translations regardless of export_xml_only mode.
        """
        self.append_to_text_widget(self.xml_text, f"Creating new iTunes XML at {export_xml_path}...\n")
        start_time = time.time()
        
        try:
            # First, load the original XML as text to preserve formatting
            with open(original_xml_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
                
            # Parse the XML
            tree = ET.fromstring(xml_content)
                    
            # We'll create a deep copy to modify
            new_tree = copy.deepcopy(tree)
            
            # Get the main dict from the new tree
            new_dict = next((child for child in new_tree if child.tag == 'dict'), None)
            if new_dict is None:
                self.append_to_text_widget(self.xml_text, "Invalid iTunes XML format: missing main dict\n")
                return False
                
            # Update application version and date
            current_date = datetime.datetime.now().strftime("%Y-%m-%d")
            # Create iTunes-format timestamp (ISO 8601 format with timezone)
            current_timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            target_os = self.target_os_var.get()
            use_mac_paths = (target_os == "External")

            for i in range(len(new_dict)):
                if new_dict[i].tag == 'key' and new_dict[i].text == 'Application Version':
                    if i+1 < len(new_dict) and new_dict[i+1].tag == 'string':
                        new_dict[i+1].text = f"DJ Export {current_date}"
                elif new_dict[i].tag == 'key' and new_dict[i].text == 'Date':
                    # Update the Date field so Traktor recognizes this as a new/updated library
                    if i+1 < len(new_dict) and new_dict[i+1].tag == 'date':
                        new_dict[i+1].text = current_timestamp
                        self.append_to_text_widget(self.xml_text, f"Updated library Date to: {current_timestamp}\n")
                elif new_dict[i].tag == 'key' and new_dict[i].text == 'Music Folder':
                    if i+1 < len(new_dict) and new_dict[i+1].tag == 'string':
                        # Update music folder path to DJ library
                        if use_mac_paths:
                            # Use Mac path
                            mac_folder = self.mac_dj_library_var.get()
                            if not mac_folder.endswith("/"):
                                mac_folder += "/"
                            new_dict[i+1].text = f"file://{mac_folder}"
                        else:
                            # Use Windows path
                            dj_path = dj_library.replace("\\", "/")
                            if not dj_path.startswith("/"):
                                dj_path = "/" + dj_path
                            if not dj_path.endswith("/"):
                                dj_path += "/"
                            new_dict[i+1].text = f"file://localhost{dj_path}"
            
            # Build a set of track IDs that are in our playlists
            track_ids_to_keep = set()
            for playlist in playlists_data:
                # Ensure all track IDs are strings
                track_ids_to_keep.update([str(track_id) for track_id in playlist['track_ids']])
                
            self.append_to_text_widget(self.xml_text, f"Identified {len(track_ids_to_keep)} unique tracks to keep\n")
                
            # Find the Tracks dictionary and modify it
            tracks_element = None
            tracks_element_index = None
            for i in range(len(new_dict)):
                if new_dict[i].tag == 'key' and new_dict[i].text == 'Tracks':
                    if i+1 < len(new_dict) and new_dict[i+1].tag == 'dict':
                        tracks_element = new_dict[i+1]
                        tracks_element_index = i+1
                    break
                    
            if tracks_element is None:
                self.append_to_text_widget(self.xml_text, "No tracks element found in XML\n")
                return False
                
            # Create a new tracks dictionary with only the tracks we want to keep
            new_tracks_dict = ET.Element('dict')
            tracks_processed = 0
            tracks_kept = 0
            
            # Create a reverse mapping from file paths to their new DJ library paths
            original_to_dj_path = {}
            target_os = self.target_os_var.get()
            use_mac_paths = (target_os == "External")

            if use_mac_paths:
                self.append_to_text_widget(self.xml_text,
                    "Using external device paths in XML for cross-platform transfer\n")

            for orig_path, dj_path in file_mapping.items():
                # Format the paths as URLs
                if use_mac_paths:
                    # Convert Windows path to Mac path
                    mac_path = self.windows_to_mac_path(dj_path)
                    dj_url = f"file://{mac_path}"
                    dj_url = urllib.parse.quote(dj_url, safe='/:')
                else:
                    # Use Windows path
                    dj_url_path = dj_path.replace("\\", "/")
                    if not dj_url_path.startswith("/"):
                        dj_url_path = "/" + dj_url_path
                    dj_url = f"file://localhost{dj_url_path}"
                    dj_url = urllib.parse.quote(dj_url, safe='/:')

                original_to_dj_path[orig_path] = dj_url
                
            # Process all tracks
            for i in range(0, len(tracks_element), 2):
                if i+1 >= len(tracks_element):
                    break
                    
                if tracks_element[i].tag == 'key' and tracks_element[i+1].tag == 'dict':
                    track_id = tracks_element[i].text
                    track_dict = tracks_element[i+1]
                    
                    tracks_processed += 1
                    
                    # Get Track ID from the track dict for proper comparison
                    track_id_from_dict = None
                    for j in range(0, len(track_dict), 2):
                        if j+1 >= len(track_dict):
                            break
                        if track_dict[j].tag == 'key' and track_dict[j].text == 'Track ID':
                            # Safely convert the track ID
                            try:
                                if track_dict[j+1].tag == 'integer':
                                    track_id_from_dict = str(self.safe_int_conversion(track_dict[j+1].text))
                                else:
                                    track_id_from_dict = track_dict[j+1].text
                            except:
                                track_id_from_dict = str(track_dict[j+1].text)
                            break
                    
                    # If we couldn't get the Track ID, use the key as fallback
                    if track_id_from_dict is None:
                        track_id_from_dict = track_id
                    
                    # Check if this track is in our playlists
                    if str(track_id_from_dict) in track_ids_to_keep:
                        track_metadata = tracks_metadata.get(str(track_id_from_dict), {})
                        original_path = track_metadata.get('file_path', '')
                        
                        # Create a copy of this track's dict
                        new_track_dict = copy.deepcopy(track_dict)

                        # If we have location info, update it
                        if original_path and original_path in original_to_dj_path:
                            # Find and update the Location element
                            for j in range(0, len(new_track_dict), 2):
                                if j+1 >= len(new_track_dict):
                                    break
                                if new_track_dict[j].tag == 'key' and new_track_dict[j].text == 'Location':
                                    # Update to the new DJ library path
                                    new_track_dict[j+1].text = original_to_dj_path[original_path]
                                    break
                        
                        # Add this track to our new tracks dictionary
                        new_key = ET.Element('key')
                        new_key.text = track_id
                        new_tracks_dict.append(new_key)
                        new_tracks_dict.append(new_track_dict)
                        tracks_kept += 1
                        
                        # Log progress
                        if tracks_kept % 100 == 0:
                            self.append_to_text_widget(
                                self.xml_text,
                                f"Processed {tracks_processed} tracks, kept {tracks_kept}...\n"
                            )
            
            # Replace the tracks dictionary
            new_dict[tracks_element_index] = new_tracks_dict
            
            self.append_to_text_widget(
                self.xml_text,
                f"Processed {tracks_processed} tracks, kept {tracks_kept} for the selected playlists\n"
            )
            
            # Now update the playlists array
            playlists_element = None
            playlists_element_index = None
            for i in range(len(new_dict)):
                if new_dict[i].tag == 'key' and new_dict[i].text == 'Playlists':
                    if i+1 < len(new_dict) and new_dict[i+1].tag == 'array':
                        playlists_element = new_dict[i+1]
                        playlists_element_index = i+1
                    break
                    
            if playlists_element is None:
                self.append_to_text_widget(self.xml_text, "No playlists element found in XML\n")
                return False
                
            # Create a new playlists array
            new_playlists_array = ET.Element('array')
            playlists_kept = 0
            
            # First, find and keep any system playlists (always needed)
            system_playlists = ["Library", "Music"]
            for playlist_dict in playlists_element:
                if playlist_dict.tag != 'dict':
                    continue
                    
                is_system_playlist = False
                playlist_name = None
                
                for i in range(0, len(playlist_dict), 2):
                    if i+1 >= len(playlist_dict):
                        break
                    if playlist_dict[i].tag == 'key' and playlist_dict[i].text == 'Name':
                        playlist_name = playlist_dict[i+1].text
                        if playlist_name in system_playlists:
                            is_system_playlist = True
                        break
                            
                if is_system_playlist:
                    # Keep this system playlist
                    new_playlist = copy.deepcopy(playlist_dict)
                    
                    # Update the playlist items to only include tracks we're keeping
                    self.update_playlist_items(new_playlist, track_ids_to_keep)
                    
                    new_playlists_array.append(new_playlist)
                    playlists_kept += 1
                    self.append_to_text_widget(self.xml_text, f"Added system playlist: {playlist_name}\n")
            
            # Now add our selected playlists, preserving all their track references
            selected_playlist_names = [p['name'] for p in playlists_data]
            for playlist_dict in playlists_element:
                if playlist_dict.tag != 'dict':
                    continue
                    
                playlist_name = None
                for i in range(0, len(playlist_dict), 2):
                    if i+1 >= len(playlist_dict):
                        break
                    if playlist_dict[i].tag == 'key' and playlist_dict[i].text == 'Name':
                        playlist_name = playlist_dict[i+1].text
                        break
                        
                if playlist_name in selected_playlist_names:
                    # This is one of our selected playlists
                    new_playlist = copy.deepcopy(playlist_dict)
                    
                    # Make sure we're preserving all valid tracks in this playlist
                    # (this ensures tracks appear in all playlists they belong to)
                    track_count = self.update_playlist_items(new_playlist, track_ids_to_keep)
                    
                    new_playlists_array.append(new_playlist)
                    playlists_kept += 1
                    self.append_to_text_widget(self.xml_text, f"Added playlist: {playlist_name} with {track_count} tracks\n")
            
            # Replace the playlists array
            new_dict[playlists_element_index] = new_playlists_array
            
            self.append_to_text_widget(
                self.xml_text,
                f"Kept {playlists_kept} playlists in total\n"
            )
            
            # Convert the XML tree to a string and write to the export file
            xml_str = ET.tostring(new_tree, encoding='utf-8')
            
            # Make it human-readable with proper formatting
            xml_pretty = xml_str.decode()
            
            with open(export_xml_path, 'w', encoding='utf-8') as f:
                f.write(xml_pretty)
                
            total_time = time.time() - start_time
            
            summary_msg = (
                f"XML export complete in {total_time:.2f} seconds\n"
                f"Created XML with {tracks_kept} tracks in {playlists_kept} playlists\n"
            )
            
            if use_mac_paths:
                summary_msg += (
                    f"\n{'='*60}\n"
                    f"CROSS-PLATFORM TRANSFER INSTRUCTIONS:\n"
                    f"{'='*60}\n"
                    f"1. Files exported to: {dj_library}\n"
                    f"2. XML created with external device paths at: {export_xml_path}\n"
                    f"3. Copy both the folder and XML to your external device\n"
                    f"4. Place files at: {self.mac_dj_library_var.get()}\n"
                    f"{'='*60}\n"
                )
            
            self.append_to_text_widget(self.xml_text, summary_msg)
            
            return True
            
        except Exception as e:
            self.append_to_text_widget(self.xml_text, f"Error creating XML: {e}\n")
            import traceback
            self.append_to_text_widget(self.xml_text, f"Traceback: {traceback.format_exc()}\n")
            return False

def run_headless_sync(args=None) -> bool:
    """Run a full sync with no visible window, using saved settings from
    ConfigManager. Returns True on success, False on failure/abort."""
    outcome = {"success": True}

    def _showerror(title="", message="", *a, **kw):
        logger.error(f"{title}: {message}")
        outcome["success"] = False

    def _showwarning(title="", message="", *a, **kw):
        logger.warning(f"{title}: {message}")

    def _showinfo(title="", message="", *a, **kw):
        logger.info(f"{title}: {message}")

    def _askquestion(title="", message="", *a, **kw):
        logger.error(f"{title}: {message} (no one to confirm in direct-run mode — "
                      f"aborting; open the tool via the gear icon to resolve this).")
        return "no"

    messagebox.showerror = _showerror
    messagebox.showwarning = _showwarning
    messagebox.showinfo = _showinfo
    messagebox.askquestion = _askquestion

    root = tk.Tk()
    root.withdraw()
    app = PlaylistSyncUI(root)

    if args:
        if args.itunes_xml:
            app.itunes_xml_var.set(args.itunes_xml)
        if args.dj_library:
            app.dj_library_var.set(args.dj_library)
        if args.export_xml:
            app.export_xml_var.set(args.export_xml)

    if not app.itunes_xml_var.get() or not os.path.exists(app.itunes_xml_var.get()):
        logger.error("No iTunes XML library configured — open the tool (gear icon) to set it up first.")
        root.destroy()
        return False

    app.load_playlists()

    orig_enable_buttons = app.enable_buttons
    def _finish():
        orig_enable_buttons()
        root.quit()
    app.enable_buttons = _finish

    app.start_sync()
    if not app.syncing:
        # start_sync() bailed out synchronously (validation error) before
        # spawning the worker thread - nothing left to wait for.
        root.destroy()
        return outcome["success"]

    root.mainloop()
    root.destroy()
    return outcome["success"]


def run_headless_import(args=None) -> bool:
    """Run a folder import with no visible window, using saved settings from
    ConfigManager (the Import source folder, and the Export tab's DJ Library
    Folder / Export XML as the destination). Returns True on success."""
    outcome = {"success": True}

    def _showerror(title="", message="", *a, **kw):
        logger.error(f"{title}: {message}")
        outcome["success"] = False

    def _showwarning(title="", message="", *a, **kw):
        logger.warning(f"{title}: {message}")

    def _showinfo(title="", message="", *a, **kw):
        logger.info(f"{title}: {message}")

    messagebox.showerror = _showerror
    messagebox.showwarning = _showwarning
    messagebox.showinfo = _showinfo

    root = tk.Tk()
    root.withdraw()
    app = PlaylistSyncUI(root)

    if not app.import_source_dir_var.get() or not os.path.isdir(app.import_source_dir_var.get()):
        logger.error("No import source folder configured — open the tool (gear icon) to set it up first.")
        root.destroy()
        return False

    app._run_import_autodetect()
    if not app._import_detected_dj_library_dir or not app._import_detected_xml_path:
        logger.error("Could not detect a DJ Library folder/XML in the configured import source folder.")
        root.destroy()
        return False

    orig_enable_import_button = app._enable_import_button
    def _finish():
        orig_enable_import_button()
        root.quit()
    app._enable_import_button = _finish

    app.start_import()
    if not app.importing:
        # start_import() bailed out synchronously (validation error) before
        # spawning the worker thread - nothing left to wait for.
        root.destroy()
        return outcome["success"]

    root.mainloop()
    root.destroy()
    return outcome["success"]


def run_headless(args=None) -> bool:
    """Replay whichever mode (export or import) was last used, with saved
    settings - the FastRack hub's direct-run entry point. Mirrors the same
    dispatch pattern as Sync Traktor Playlists' run_headless()."""
    config_manager = ConfigManager()
    if config_manager.settings.last_mode == "import":
        return run_headless_import(args)
    return run_headless_sync(args)


def main():
    # Setup logging when the app actually runs (not at import time)
    setup_shared_logging("traktor_sync")

    parser = argparse.ArgumentParser(description="iTunes Playlist Sync Tool")
    parser.add_argument("--itunes-xml", help="Path to iTunes XML library file (overrides saved settings)")
    parser.add_argument("--dj-library", help="Path to DJ Library folder (overrides saved settings)")
    parser.add_argument("--export-xml", help="Path for exported XML file (overrides saved settings)")
    parser.add_argument("--auto-run", action="store_true",
                         help="Run export or import (whichever was last used) immediately with saved settings, no window")
    args = parser.parse_args()

    if args.auto_run:
        return 0 if run_headless(args) else 1

    root = tk.Tk()
    apply_category_icon(root)
    app = PlaylistSyncUI(root)
    if args.itunes_xml:
        app.itunes_xml_var.set(args.itunes_xml)
    if args.dj_library:
        app.dj_library_var.set(args.dj_library)
    if args.export_xml:
        app.export_xml_var.set(args.export_xml)
    root.mainloop()
    return 0

if __name__ == "__main__":
    sys.exit(main())