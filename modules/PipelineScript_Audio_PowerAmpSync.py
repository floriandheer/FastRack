#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_PowerAmpSync.py
Description: Sync MusicBee/iTunes playlists and music to PowerAmp (Android)
Author: Florian Dheer
Version: 2.1.0

Features:
- Reads playlists from iTunes XML (exported from MusicBee or iTunes)
- Selective playlist sync with treeview selection
- Converts audio (FLAC, WAV, MP3, etc.) to Opus for optimal phone storage
- Configurable Opus quality (64k to 192k VBR)
- Skip existing files option for fast incremental syncs
- Exports playlists to M3U8 format (UTF-8 for Unicode support)
- Configurable source/destination path mapping
- Same UI patterns as TraktorSyncPlaylists for consistency
- ADB direct sync: Push files directly to Android phone via USB
- Mirror sync: Delete orphaned files no longer in any playlist
- Cover art handling: Copies/extracts album art alongside music
"""

import os
import sys
import json
import re
import shutil
import threading
import datetime
import subprocess
import tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from shared_window_icon import apply_category_icon
from shared_scrollable_frame import ScrollableFrame
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from pathlib import Path
import xml.etree.ElementTree as ET
import urllib.parse

# ============================================================================
# CONSTANTS AND CONFIGURATION
# ============================================================================

APP_NAME = "Sync to PowerAmp"
APP_VERSION = "2.1.0"

# Configuration paths
APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "PipelineManager")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "poweramp_sync_config.json")

# Local ADB path (relative to script location)
SCRIPT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # Parent of modules/
LOCAL_ADB_DIR = os.path.join(SCRIPT_DIR, "tools", "platform-tools")
LOCAL_ADB_EXE = os.path.join(LOCAL_ADB_DIR, "adb.exe" if sys.platform == "win32" else "adb")

# Header color (matching pipeline style)
HEADER_COLOR = "#2c3e50"

# Valid audio extensions
VALID_EXTENSIONS = {'.mp3', '.flac', '.wav', '.aiff', '.m4a', '.ogg', '.opus', '.wma', '.aac'}

# Built-in preset that mirrors the Auto Select rule (digits 1-9 prefix).
# Resolved dynamically against the loaded playlist list, never stored.
DEFAULT_PRESET_NAME = "Default (Auto)"


# ============================================================================
# LOGGING SETUP
# ============================================================================

from shared_logging import get_logger, setup_logging as setup_shared_logging

logger = get_logger("poweramp_sync")


# ============================================================================
# DATA CLASSES
# ============================================================================

@dataclass
class SyncSettings:
    """Sync configuration settings."""
    itunes_xml_path: str = ""
    destination_path: str = ""  # Playlist destination
    music_destination_path: str = ""  # Music files destination
    source_music_prefix: str = "M:\\"
    android_music_prefix: str = "/storage/emulated/0/Music/"
    use_relative_paths: bool = False
    selected_playlists: List[str] = field(default_factory=list)
    # Sync mode: "music_and_playlists" or "playlists_only"
    sync_mode: str = "music_and_playlists"
    # Opus conversion settings
    convert_to_opus: bool = True
    skip_existing: bool = True
    opus_bitrate: str = "128k"  # Options: "192k", "160k", "128k", "96k", "64k"
    # ADB sync settings
    sync_target: str = "local"  # "local" or "adb"
    adb_music_path: str = "/storage/emulated/0/Music/"
    adb_playlist_path: str = "/storage/emulated/0/Music/Playlists/"
    adb_device_id: str = ""  # Last selected ADB device serial (remembered across runs)
    # Saved playlist selection presets
    playlist_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    active_preset: str = ""
    # Audiobooks / Podcasts sync settings (plain copy, no conversion)
    audiobooks_source_path: str = ""
    podcasts_source_path: str = ""
    audiobooks_dest_path: str = ""
    podcasts_dest_path: str = ""
    adb_audiobooks_path: str = "/storage/emulated/0/Audiobooks/"
    adb_podcasts_path: str = "/storage/emulated/0/Podcasts/"
    extras_skip_existing: bool = True
    extras_mirror: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SyncSettings':
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


# Audio formats that should be converted to Opus (all non-opus audio)
CONVERTIBLE_EXTENSIONS = {'.flac', '.wav', '.aiff', '.aif', '.alac', '.ape', '.wv', '.mp3', '.m4a', '.aac', '.wma', '.ogg'}


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
# GUI APPLICATION
# ============================================================================

class PowerAmpSyncApp:
    """Main application GUI."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("900x850")
        self.root.minsize(900, 500)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # Initialize managers
        self.config_manager = ConfigManager()

        # State
        self.all_playlists: List[str] = []
        self.playlist_data: Dict[str, Dict] = {}
        self.itunes_root = None
        self.tracks_dict = {}  # Track ID -> track info
        self.syncing = False
        self.bitrate_radios = []
        self.adb_device_id: Optional[str] = None
        self.adb_device_name: str = ""
        self.adb_path: Optional[str] = None  # Will be set by _find_adb()
        # Combobox-backed device list: [(device_id, state, label)]
        self._device_entries: List[Tuple[str, str, str]] = []

        # Find ADB executable
        self._find_adb()

        # Reload playlists when the iTunes XML changes on disk (e.g. iTunes rewrote it).
        self.root.bind("<FocusIn>", self._on_window_focus, add="+")

        # Create UI
        self._create_ui()

        # Load settings and initialize default paths
        self._initialize_default_paths()

        # Handle window close
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # =========================================================================
    # ADB HELPER METHODS
    # =========================================================================

    def _find_adb(self) -> None:
        """Find ADB executable - checks local tools folder first, then system PATH."""
        # Check local tools folder first
        if os.path.exists(LOCAL_ADB_EXE):
            self.adb_path = LOCAL_ADB_EXE
            logger.info(f"Found local ADB: {LOCAL_ADB_EXE}")
            return

        # Check system PATH
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(["adb", "version"], **process_args, timeout=10)
            if result.returncode == 0:
                self.adb_path = "adb"
                logger.info("Found ADB in system PATH")
                return
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

        # ADB not found
        self.adb_path = None
        logger.warning("ADB not found - install platform-tools to tools/platform-tools/ or add to PATH")

    def _check_adb(self) -> bool:
        """Check if ADB is available."""
        return self.adb_path is not None

    def _list_adb_devices(self) -> List[Tuple[str, str]]:
        """Return every attached ADB device as (device_id, state) tuples.

        State is the literal token adb reports per device — "device" means
        ready, others include "unauthorized", "offline", "no permissions".
        Returning ALL of them (not just the first ready one) is what lets the
        sync flow refuse to run when multiple devices would be ambiguous.
        """
        if not self.adb_path:
            return []
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
            }
            result = subprocess.run([self.adb_path, "devices"], **process_args, timeout=10)
            if result.returncode != 0:
                return []

            output = result.stdout.decode("utf-8", errors="replace")
            devices: List[Tuple[str, str]] = []
            # Skip the "List of devices attached" header
            for line in output.splitlines()[1:]:
                parts = line.strip().split("\t")
                if len(parts) >= 2:
                    dev_id = parts[0].strip()
                    state = parts[1].strip().split()[0] if parts[1].strip() else ""
                    if dev_id:
                        devices.append((dev_id, state))
            return devices
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return []

    def _resolve_adb_device(self) -> Tuple[bool, str]:
        """Ensure self.adb_device_id is a connected & ready device.

        Called from validation right before sync. Returns (ok, error_message);
        on success self.adb_device_id is guaranteed non-empty and points to a
        device whose state is "device". On failure, the message is suitable
        for showing in a messagebox.
        """
        if not self._check_adb():
            return False, "ADB is not installed or not found.\n\nInstall Android Platform Tools."

        devices = self._list_adb_devices()
        ready = [(dev_id, state) for dev_id, state in devices if state == "device"]

        if not ready:
            if devices:
                summary = "\n".join(f"  • {dev_id} — {state}" for dev_id, state in devices)
                return False, (
                    "No usable ADB device. Detected:\n"
                    f"{summary}\n\n"
                    "If you see 'unauthorized', accept the USB-debugging prompt on the phone.\n"
                    "If you see 'offline', unplug and re-plug, or run `adb kill-server`."
                )
            return False, (
                "No Android device connected.\n\n"
                "Plug in the phone and enable USB debugging."
            )

        # Honor an existing selection if it's still attached and ready.
        if self.adb_device_id and any(dev_id == self.adb_device_id for dev_id, _ in ready):
            return True, ""

        # Stale selection — drop it before deciding.
        self.adb_device_id = None
        self.adb_device_name = ""

        if len(ready) == 1:
            self.adb_device_id = ready[0][0]
            self.adb_device_name = self._get_adb_device_name(self.adb_device_id)
            return True, ""

        names = ", ".join(dev_id for dev_id, _ in ready)
        return False, (
            f"{len(ready)} ADB devices are connected ({names}).\n\n"
            "Pick one from the Device dropdown in the Android Device (ADB) panel, "
            "then start the sync again."
        )

    def _get_adb_device_name(self, device_id: str) -> str:
        """Get the model name of the connected device."""
        if not self.adb_path:
            return device_id
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(
                [self.adb_path, "-s", device_id, "shell", "getprop", "ro.product.model"],
                **process_args, timeout=10
            )
            if result.returncode == 0:
                return result.stdout.decode('utf-8', errors='replace').strip()
            return device_id
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return device_id

    def _adb_cmd(self, *args) -> List[str]:
        """Build an ADB command pinned to the selected device via -s.

        Always emits -s when a device is selected, so multi-device setups
        (phone + emulator, phone + cached wireless adb, two phones) never
        hit "more than one device/emulator" mid-sync.
        """
        cmd = [self.adb_path]
        if self.adb_device_id:
            cmd.extend(["-s", self.adb_device_id])
        cmd.extend(args)
        return cmd

    def _require_adb_device(self) -> Tuple[bool, str]:
        """Defensive check used by every ADB operation right before it runs."""
        if not self.adb_path:
            return False, "ADB not available"
        if not self.adb_device_id:
            return False, "No ADB device selected (open the Device dropdown and pick one)"
        return True, ""

    def _adb_push(self, local_path: str, remote_path: str) -> Tuple[bool, str]:
        """Push a file to the connected Android device.

        Returns:
            Tuple of (success, error_message)
        """
        ok, err = self._require_adb_device()
        if not ok:
            return False, err
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            # Ensure remote directory exists
            remote_dir = os.path.dirname(remote_path).replace('\\', '/')
            subprocess.run(
                self._adb_cmd("shell", f"mkdir -p '{remote_dir}'"),
                **process_args, timeout=30
            )

            result = subprocess.run(
                self._adb_cmd("push", local_path, remote_path),
                **process_args, timeout=300
            )
            if result.returncode == 0:
                return True, ""
            else:
                error = result.stderr.decode('utf-8', errors='replace').strip()
                logger.error(f"ADB push failed for {remote_path}: {error}")
                return False, error
        except subprocess.TimeoutExpired:
            return False, "Timeout"
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.error(f"ADB push failed: {e}")
            return False, str(e)

    def _adb_list_files(self, remote_dir: str) -> List[str]:
        """List all files recursively in a directory on the device.

        Returns a list of file paths relative to remote_dir.
        """
        ok, err = self._require_adb_device()
        if not ok:
            logger.error(f"ADB list files skipped: {err}")
            return []
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            # Use find to list all files recursively
            remote_dir = remote_dir.rstrip('/')
            result = subprocess.run(
                self._adb_cmd("shell", f"find '{remote_dir}' -type f 2>/dev/null"),
                **process_args, timeout=120
            )
            if result.returncode != 0:
                return []

            output = result.stdout.decode('utf-8', errors='replace')
            files = []
            for line in output.strip().split('\n'):
                line = line.strip()
                if line and line.startswith(remote_dir):
                    # Get path relative to remote_dir
                    relative = line[len(remote_dir):].lstrip('/')
                    if relative:
                        files.append(relative)
            return files
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.error(f"ADB list files failed: {e}")
            return []

    def _local_list_files(self, local_dir: str) -> List[str]:
        """List all files recursively in a local directory.

        Returns paths relative to local_dir, with forward slashes.
        """
        files: List[str] = []
        if not local_dir or not os.path.isdir(local_dir):
            return files
        for root, _dirs, fnames in os.walk(local_dir):
            for f in fnames:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_dir).replace('\\', '/')
                files.append(rel)
        return files

    def _walk_relative_files(self, local_dir: str) -> List[Tuple[str, str]]:
        """List all files recursively under local_dir as (relative_path, full_path)."""
        results: List[Tuple[str, str]] = []
        if not local_dir or not os.path.isdir(local_dir):
            return results
        for root, _dirs, fnames in os.walk(local_dir):
            for f in fnames:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, local_dir)
                results.append((rel, full))
        return results

    def _adb_delete(self, remote_path: str) -> bool:
        """Delete a file on the device."""
        ok, _err = self._require_adb_device()
        if not ok:
            return False
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(
                self._adb_cmd("shell", f"rm '{remote_path}'"),
                **process_args, timeout=30
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError) as e:
            logger.error(f"ADB delete failed: {e}")
            return False

    def _adb_rmdir_empty(self, remote_dir: str) -> bool:
        """Remove an empty directory on the device."""
        ok, _err = self._require_adb_device()
        if not ok:
            return False
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(
                self._adb_cmd("shell", f"rmdir '{remote_dir}' 2>/dev/null"),
                **process_args, timeout=30
            )
            return result.returncode == 0
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _adb_exists(self, remote_path: str) -> bool:
        """Check if a file or directory exists on the device."""
        ok, _err = self._require_adb_device()
        if not ok:
            return False
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(
                self._adb_cmd("shell", f"test -e '{remote_path}' && echo exists"),
                **process_args, timeout=30
            )
            output = result.stdout.decode('utf-8', errors='replace').strip()
            return 'exists' in output
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _create_ui(self) -> None:
        """Create the complete UI."""
        # Header (matching Traktor Sync style)
        header = tk.Frame(self.root, bg=HEADER_COLOR, height=60)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(header, text=APP_NAME, font=("Arial", 16, "bold"),
                 fg="white", bg=HEADER_COLOR).place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Main content — wrapped in a ScrollableFrame rather than gridded
        # directly onto root: this window's natural content height (the
        # settings/mode tabs plus library/sync results tabs) can exceed a
        # laptop's usable screen height, which would otherwise strand
        # controls and results off-screen with no way to reach them.
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

        main = ttk.Frame(scroll.get_frame())
        main.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)  # Results panel grows

        # === UNIVERSAL SETTINGS (shared across every tab below) ===
        universal_frame = ttk.LabelFrame(main, text="Settings")
        universal_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        universal_frame.columnconfigure(0, weight=1)

        target_row = ttk.Frame(universal_frame)
        target_row.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        ttk.Label(target_row, text="Destination:").pack(side=tk.LEFT, padx=(0, 15))
        self.sync_target_var = tk.StringVar(value="local")
        ttk.Radiobutton(target_row, text="Local Folder", variable=self.sync_target_var,
                        value="local", command=self._on_sync_target_changed).pack(side=tk.LEFT, padx=(0, 20))
        ttk.Radiobutton(target_row, text="Android Device (ADB)", variable=self.sync_target_var,
                        value="adb", command=self._on_sync_target_changed).pack(side=tk.LEFT)

        # sync_mode_var still needed for internal logic
        self.sync_mode_var = tk.StringVar(value="music_and_playlists")

        # Device picker — shared by every tab, only relevant when target=adb.
        # Combobox lists every attached device so the user can disambiguate
        # multi-device setups (phone + emulator, two phones, stale wireless
        # adb sessions, etc.) instead of silently grabbing the first one and
        # hitting "more than one device/emulator" during sync.
        self.universal_adb_frame = ttk.Frame(universal_frame)
        self.universal_adb_frame.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 5))
        self.universal_adb_frame.grid_remove()  # Hidden by default (target=local)

        adb_status_row = ttk.Frame(self.universal_adb_frame)
        adb_status_row.grid(row=0, column=0, sticky="ew")
        ttk.Label(adb_status_row, text="Device:").pack(side=tk.LEFT, padx=(0, 10))
        self.adb_device_combo_var = tk.StringVar()
        self.adb_device_combo = ttk.Combobox(
            adb_status_row, textvariable=self.adb_device_combo_var,
            state="readonly", width=42,
        )
        self.adb_device_combo.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.adb_device_combo.bind("<<ComboboxSelected>>", self._on_adb_device_selected)
        self.detect_device_btn = ttk.Button(
            adb_status_row, text="Refresh", command=self._detect_adb_device, width=10,
        )
        self.detect_device_btn.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(adb_status_row, text="?", command=self._show_adb_help, width=3).pack(side=tk.LEFT, padx=(5, 0))

        # Status sub-row — shows readiness / multi-device hints under the picker
        adb_status_row2 = ttk.Frame(self.universal_adb_frame)
        adb_status_row2.grid(row=1, column=0, sticky="w", pady=(3, 0))
        self.adb_device_status_var = tk.StringVar(value="No device detected")
        ttk.Label(adb_status_row2, textvariable=self.adb_device_status_var, foreground="gray").pack(side=tk.LEFT)

        # === CONTENT TABS (mode selector sits below the universal settings) ===
        # 'clam' is required on Windows for the tab background/foreground
        # colors below to actually render — the native theme silently
        # ignores TNotebook.Tab color overrides (font/padding still apply
        # under native, but color doesn't, which is why an earlier pass at
        # this only changed size, not color).
        tab_style = ttk.Style()
        tab_style.theme_use("clam")
        tab_style.configure("PowerAmp.TNotebook.Tab",
                            font=("Segoe UI", 9, "bold"),
                            padding=[12, 6],
                            background="#bdc3c7",
                            foreground=HEADER_COLOR)
        tab_style.map("PowerAmp.TNotebook.Tab",
                     background=[("selected", HEADER_COLOR)],
                     foreground=[("selected", "#ffffff")],
                     padding=[("selected", [16, 9])])

        self.content_notebook = ttk.Notebook(main, style="PowerAmp.TNotebook")
        self.content_notebook.grid(row=1, column=0, sticky="nsew")
        self._suppress_notebook_wheel_cycling(self.content_notebook)

        playlists_tab = ttk.Frame(self.content_notebook)
        self.content_notebook.add(playlists_tab, text="\U0001F3B5 Playlist Selection")
        playlists_tab.columnconfigure(0, weight=1)

        # === OPTIONS (Playlist-Selection-specific, before configuration) ===
        options_frame = ttk.LabelFrame(playlists_tab, text="Options")
        options_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        # Conversion options
        conv_row = ttk.Frame(options_frame)
        conv_row.grid(row=0, column=0, sticky="w", padx=10, pady=5)

        self.convert_opus_var = tk.BooleanVar(value=True)
        self.convert_opus_checkbox = ttk.Checkbutton(conv_row, text="Convert all audio to Opus",
                                                     variable=self.convert_opus_var, command=self._on_convert_opus_changed)
        self.convert_opus_checkbox.pack(side=tk.LEFT)

        ttk.Label(conv_row, text="Quality:").pack(side=tk.LEFT, padx=(30, 8))
        self.bitrate_var = tk.StringVar(value="128k")
        self.quality_combo = ttk.Combobox(conv_row, textvariable=self.bitrate_var, state="readonly", width=22,
                                          values=["192 kbps (Transparent)", "160 kbps (Excellent)", "128 kbps (Recommended)", "96 kbps (Good)", "64 kbps (Efficient)"])
        self.quality_combo.pack(side=tk.LEFT)
        self.quality_combo.current(2)  # Default to 128 kbps
        self.quality_combo.bind("<<ComboboxSelected>>", self._on_quality_changed)

        skip_row = ttk.Frame(options_frame)
        skip_row.grid(row=1, column=0, sticky="w", padx=10, pady=5)

        self.skip_existing_var = tk.BooleanVar(value=True)
        self.skip_existing_checkbox = ttk.Checkbutton(skip_row, text="Skip existing files", variable=self.skip_existing_var)
        self.skip_existing_checkbox.pack(side=tk.LEFT)

        # === CONFIGURATION (file paths) ===
        config_frame = ttk.LabelFrame(playlists_tab, text="Configuration")
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
        ttk.Button(config_frame, text="Browse", command=self._browse_itunes_xml).grid(row=current_row, column=2, padx=5, pady=10)

        current_row += 1

        ttk.Separator(config_frame, orient="horizontal").grid(row=current_row, column=0, columnspan=3, sticky="ew", padx=10, pady=5)

        current_row += 1

        # --- Destination ---
        dest_label = ttk.Label(config_frame, text="Destination", font=("Arial", 9, "bold"))
        dest_label.grid(row=current_row, column=0, columnspan=3, sticky="w", padx=10, pady=(5, 0))

        current_row += 1

        # Local destination frame (hidden when ADB is selected)
        self.local_dest_frame = ttk.Frame(config_frame)
        self.local_dest_frame.grid(row=current_row, column=0, columnspan=3, sticky="ew")
        self.local_dest_frame.columnconfigure(1, weight=1)

        # Music destination folder
        self.music_dest_label = ttk.Label(self.local_dest_frame, text="Music Folder:")
        self.music_dest_label.grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.music_dest_var = tk.StringVar()
        self.music_dest_entry = ttk.Entry(self.local_dest_frame, textvariable=self.music_dest_var, width=50)
        self.music_dest_entry.grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        self.music_dest_browse_btn = ttk.Button(self.local_dest_frame, text="Browse", command=self._browse_music_destination)
        self.music_dest_browse_btn.grid(row=0, column=2, padx=5, pady=10)

        # Playlist destination folder
        ttk.Label(self.local_dest_frame, text="Playlist Folder:").grid(row=1, column=0, sticky="w", padx=10, pady=10)
        self.dest_var = tk.StringVar()
        ttk.Entry(self.local_dest_frame, textvariable=self.dest_var, width=50).grid(row=1, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(self.local_dest_frame, text="Browse", command=self._browse_destination).grid(row=1, column=2, padx=5, pady=10)

        current_row += 1

        # ADB destination frame (initially hidden) — device picker itself now
        # lives in the universal Settings section above; this only holds the
        # Playlist-Selection-specific remote paths.
        self.adb_frame = ttk.LabelFrame(config_frame, text="Android (ADB) Paths")
        self.adb_frame.grid(row=current_row, column=0, columnspan=3, sticky="ew", padx=10, pady=10)
        self.adb_frame.columnconfigure(1, weight=1)
        self.adb_frame.grid_remove()  # Hidden by default

        # ADB Music path
        ttk.Label(self.adb_frame, text="Music path:").grid(row=0, column=0, sticky="w", padx=5, pady=3)
        self.adb_music_path_var = tk.StringVar(value="/storage/emulated/0/Music/")
        ttk.Entry(self.adb_frame, textvariable=self.adb_music_path_var).grid(row=0, column=1, columnspan=2, sticky="ew", padx=5, pady=3)

        # ADB Playlist path
        ttk.Label(self.adb_frame, text="Playlist path:").grid(row=1, column=0, sticky="w", padx=5, pady=3)
        self.adb_playlist_path_var = tk.StringVar(value="/storage/emulated/0/Music/Playlists/")
        ttk.Entry(self.adb_frame, textvariable=self.adb_playlist_path_var).grid(row=1, column=1, columnspan=2, sticky="ew", padx=5, pady=3)

        current_row += 1

        # Path mapping
        self.path_mapping_frame = ttk.LabelFrame(config_frame, text="Path Mapping")
        self.path_mapping_frame.grid(row=current_row, column=0, columnspan=3, sticky="ew", padx=10, pady=10)
        self.path_mapping_frame.columnconfigure(1, weight=1)

        map_row = ttk.Frame(self.path_mapping_frame)
        map_row.grid(row=0, column=0, columnspan=3, sticky="ew", padx=5, pady=5)

        self.source_prefix_var = tk.StringVar()
        ttk.Entry(map_row, textvariable=self.source_prefix_var, width=12).pack(side=tk.LEFT)
        ttk.Label(map_row, text="  \u2192  ").pack(side=tk.LEFT)
        self.android_prefix_var = tk.StringVar()
        ttk.Entry(map_row, textvariable=self.android_prefix_var, width=30).pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.relative_paths_var = tk.BooleanVar()
        ttk.Checkbutton(self.path_mapping_frame, text="Use relative paths only", variable=self.relative_paths_var).grid(
            row=1, column=0, columnspan=3, sticky="w", padx=5, pady=(0, 5))

        current_row += 1

        # === PLAYLIST SELECTION (Traktor style) ===
        playlist_frame = ttk.LabelFrame(config_frame, text="Playlist Selection")
        playlist_frame.grid(row=current_row, column=0, columnspan=3, sticky="nsew", padx=10, pady=10)
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(2, weight=1)

        # Selection mode
        mode_sel_frame = ttk.Frame(playlist_frame)
        mode_sel_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)

        ttk.Label(mode_sel_frame, text="Selection Mode:").grid(row=0, column=0, sticky="w", padx=5)
        self.selection_mode = tk.StringVar(value="include")
        ttk.Radiobutton(mode_sel_frame, text="Include Selected", variable=self.selection_mode,
                        value="include", command=self._update_selection_summary).grid(row=0, column=1, padx=10)
        ttk.Radiobutton(mode_sel_frame, text="Exclude Selected", variable=self.selection_mode,
                        value="exclude", command=self._update_selection_summary).grid(row=0, column=2, padx=10)

        # Filter and stats
        filter_frame = ttk.Frame(playlist_frame)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        filter_frame.columnconfigure(1, weight=1)

        ttk.Label(filter_frame, text="Filter:").grid(row=0, column=0, sticky="w", padx=5)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add('write', self._filter_playlists)
        ttk.Entry(filter_frame, textvariable=self.filter_var).grid(row=0, column=1, sticky="ew", padx=5)

        self.selection_summary = tk.StringVar(value="No playlists loaded")
        ttk.Label(filter_frame, textvariable=self.selection_summary, font=("Arial", 9),
                  foreground="blue").grid(row=0, column=2, padx=10, sticky="e")

        # Treeview
        list_frame = ttk.Frame(playlist_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.playlist_tree = ttk.Treeview(list_frame, columns=("tracks", "type"), show="tree headings", height=10)
        self.playlist_tree.grid(row=0, column=0, sticky="nsew")

        self.playlist_tree.heading("#0", text="Playlist Name")
        self.playlist_tree.heading("tracks", text="Tracks")
        self.playlist_tree.heading("type", text="Type")
        self.playlist_tree.column("#0", width=300)
        self.playlist_tree.column("tracks", width=80, anchor="center")
        self.playlist_tree.column("type", width=100, anchor="center")

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.playlist_tree.config(yscrollcommand=scrollbar.set)
        self.playlist_tree.bind('<<TreeviewSelect>>', lambda e: self._update_selection_summary())

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

        # Selection buttons
        sel_btn_frame = ttk.Frame(playlist_frame)
        sel_btn_frame.grid(row=4, column=0, sticky="ew", pady=5)

        ttk.Button(sel_btn_frame, text="Select All", command=self._select_all_playlists).grid(row=0, column=0, padx=5)
        ttk.Button(sel_btn_frame, text="Clear All", command=self._clear_all_playlists).grid(row=0, column=1, padx=5)
        ttk.Button(sel_btn_frame, text="Auto Select", command=self._auto_select_playlists).grid(row=0, column=2, padx=5)

        current_row += 1

        # === ACTION BUTTONS (Traktor style, inside config) ===
        main_btn_frame = ttk.Frame(config_frame)
        main_btn_frame.grid(row=current_row, column=0, columnspan=3, sticky="ew", pady=10)
        main_btn_frame.columnconfigure(1, weight=1)

        self.save_settings_btn = ttk.Button(main_btn_frame, text="Save Settings", command=self._save_settings, width=15)
        self.save_settings_btn.grid(row=0, column=0, padx=10)

        # Action buttons aligned together on the right
        action_btns = ttk.Frame(main_btn_frame)
        action_btns.grid(row=0, column=1, sticky="e", padx=10)

        self.sync_playlists_btn = tk.Button(action_btns, text="Sync Playlists", command=self._start_sync_playlists_only,
                                             width=15, bg="yellow", fg="black", font=('', 9, 'bold'))
        self.sync_playlists_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.sync_btn = tk.Button(action_btns, text="Start Sync", command=self._start_sync,
                                  width=15, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.cancel_btn = tk.Button(action_btns, text="Cancel", command=self._cancel_sync,
                                    width=15, bg="red", fg="white", font=('', 9, 'bold'),
                                    state=tk.DISABLED)
        self.cancel_btn.pack(side=tk.LEFT)

        # === AUDIOBOOKS & PODCASTS TAB ===
        extras_tab = ttk.Frame(self.content_notebook)
        self.content_notebook.add(extras_tab, text="\U0001F3A7 Audiobooks & Podcasts")
        self._create_extras_tab(extras_tab)

        # === RESULTS PANEL ===
        results_frame = ttk.LabelFrame(main, text="Sync Progress and Results")
        results_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

        # Notebook with tabs
        self.results_notebook = ttk.Notebook(results_frame)
        self.results_notebook.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self._suppress_notebook_wheel_cycling(self.results_notebook)

        # Library tab
        lib_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(lib_tab, text="Library Info")
        lib_tab.columnconfigure(0, weight=1)
        lib_tab.rowconfigure(0, weight=1)

        self.analysis_text = tk.Text(lib_tab, wrap=tk.WORD, height=6, font=("Consolas", 9), bg="#fafafa")
        self.analysis_text.grid(row=0, column=0, sticky="nsew")
        lib_scroll = ttk.Scrollbar(lib_tab, orient="vertical", command=self.analysis_text.yview)
        lib_scroll.grid(row=0, column=1, sticky="ns")
        self.analysis_text.config(yscrollcommand=lib_scroll.set)

        # Sync log tab
        sync_tab = ttk.Frame(self.results_notebook)
        self.results_notebook.add(sync_tab, text="Sync Log")
        sync_tab.columnconfigure(0, weight=1)
        sync_tab.rowconfigure(0, weight=1)

        self.sync_text = tk.Text(sync_tab, wrap=tk.WORD, height=6, font=("Consolas", 9), bg="#fafafa")
        self.sync_text.grid(row=0, column=0, sticky="nsew")
        sync_scroll = ttk.Scrollbar(sync_tab, orient="vertical", command=self.sync_text.yview)
        sync_scroll.grid(row=0, column=1, sticky="ns")
        self.sync_text.config(yscrollcommand=sync_scroll.set)

        # === STATUS BAR ===
        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1,
                                   relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=2, column=0, sticky="ew")

        self.root.bind_all("<MouseWheel>", self._on_body_mouse_wheel)
        self.root.bind_all("<Button-4>", self._on_body_mouse_wheel)
        self.root.bind_all("<Button-5>", self._on_body_mouse_wheel)
        self.root.bind_all("<TouchpadScroll>", self._on_body_touchpad_scroll)

    def _suppress_notebook_wheel_cycling(self, notebook: ttk.Notebook) -> None:
        """ttk::Notebook ships a standard, cross-platform Tcl binding on its
        "TNotebook" class that cycles tabs on any scroll over it - on Tk 9
        that's driven by <TouchpadScroll> for a trackpad (see
        shared_scrollable_frame's comment on TIP 684; <MouseWheel> is
        mouse-only there now) and by <MouseWheel>/<Button-4/5> for an actual
        wheel. Neutralize both class bindings outright (belt) and add
        instance-level overrides that forward to the window body and stop
        propagation (suspenders), since instance bindings run before class
        ones in Tk's bindtag order."""
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            self.root.bind_class("TNotebook", seq, lambda e: "break")
            notebook.bind(seq, self._on_notebook_mouse_wheel)
        self.root.bind_class("TNotebook", "<TouchpadScroll>", lambda e: "break")
        notebook.bind("<TouchpadScroll>", self._on_notebook_touchpad_scroll)

    def _pointer_over_self_scrolling_widget(self, event) -> bool:
        """True while the pointer is over a widget that already scrolls
        itself (the playlist tree, the library/sync result text boxes) -
        those should handle wheel/trackpad input natively rather than
        scrolling the window body."""
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        self_scrolling_widgets = (
            getattr(self, "playlist_tree", None),
            getattr(self, "analysis_text", None),
            getattr(self, "sync_text", None),
        )
        w = widget
        while w is not None:
            if w in self_scrolling_widgets:
                return True
            w = w.master
        return False

    def _on_body_mouse_wheel(self, event) -> None:
        if not self._pointer_over_self_scrolling_widget(event):
            self._body_scroll._on_mouse_wheel(event)

    def _on_body_touchpad_scroll(self, event) -> None:
        if not self._pointer_over_self_scrolling_widget(event):
            self._body_scroll._on_touchpad_scroll(event)

    def _on_notebook_mouse_wheel(self, event):
        self._on_body_mouse_wheel(event)
        return "break"

    def _on_notebook_touchpad_scroll(self, event):
        self._on_body_touchpad_scroll(event)
        return "break"

    def _create_extras_tab(self, parent: ttk.Frame) -> None:
        """Create the Audiobooks & Podcasts tab (plain file copy, no Opus conversion)."""
        parent.columnconfigure(0, weight=1)

        # Shared column geometry so the label/entry/browse/sync columns line
        # up vertically across the three separate frames below (Source
        # Folders, Local destination, ADB destination) regardless of which
        # destination frame happens to be visible.
        LABEL_WIDTH = 20
        ENTRY_WIDTH = 50
        BROWSE_COL_MINSIZE = 90

        # --- Source folders ---
        # Just source config here - no action button. Syncing happens from
        # the Destination rows below (where the files are actually going),
        # which is where the Sync button for each mode now lives.
        sources_frame = ttk.LabelFrame(parent, text="Source Folders")
        sources_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        sources_frame.columnconfigure(1, weight=1)
        sources_frame.columnconfigure(2, minsize=BROWSE_COL_MINSIZE)
        # No Sync button here (it lives on the Destination rows below), but
        # reserve the same column 3 width anyway - otherwise this frame has
        # one fewer fixed-width column than Destination's, so its Entry
        # (the only stretchy column) would soak up the extra space and end
        # up wider than the destination path fields.
        sources_frame.columnconfigure(3, minsize=BROWSE_COL_MINSIZE)

        ttk.Label(sources_frame, text="Audiobooks:", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        self.audiobooks_source_var = tk.StringVar(value="M:\\Audiobooks")
        ttk.Entry(sources_frame, textvariable=self.audiobooks_source_var, width=ENTRY_WIDTH).grid(
            row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(sources_frame, text="Browse", command=self._browse_audiobooks_source).grid(
            row=0, column=2, padx=5, pady=10)

        ttk.Label(sources_frame, text="Downloaded Podcasts:", width=LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=10)
        self.podcasts_source_var = tk.StringVar(value="M:\\Podcasts")
        ttk.Entry(sources_frame, textvariable=self.podcasts_source_var, width=ENTRY_WIDTH).grid(
            row=1, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(sources_frame, text="Browse", command=self._browse_podcasts_source).grid(
            row=1, column=2, padx=5, pady=10)

        # --- Destination ---
        # Target (Local/ADB) and the device picker itself now live in the
        # universal Settings section above the tabs — this frame only holds
        # the Audiobooks/Podcasts-specific paths, each with its own Sync
        # button - click the one for wherever it's actually going.
        dest_frame = ttk.LabelFrame(parent, text="Destination")
        dest_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        dest_frame.columnconfigure(1, weight=1)

        # Local destination (hidden when ADB is selected)
        self.extras_local_frame = ttk.Frame(dest_frame)
        self.extras_local_frame.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.extras_local_frame.columnconfigure(1, weight=1)
        self.extras_local_frame.columnconfigure(2, minsize=BROWSE_COL_MINSIZE)

        ttk.Label(self.extras_local_frame, text="Audiobooks Folder:", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        self.audiobooks_dest_var = tk.StringVar()
        ttk.Entry(self.extras_local_frame, textvariable=self.audiobooks_dest_var, width=ENTRY_WIDTH).grid(
            row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(self.extras_local_frame, text="Browse", command=self._browse_audiobooks_dest).grid(
            row=0, column=2, padx=5, pady=10)
        self.sync_audiobooks_btn = tk.Button(self.extras_local_frame, text="Sync", command=self._start_sync_audiobooks,
                                              width=10, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_audiobooks_btn.grid(row=0, column=3, padx=(0, 10), pady=10)

        ttk.Label(self.extras_local_frame, text="Podcasts Folder:", width=LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=10)
        self.podcasts_dest_var = tk.StringVar()
        ttk.Entry(self.extras_local_frame, textvariable=self.podcasts_dest_var, width=ENTRY_WIDTH).grid(
            row=1, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(self.extras_local_frame, text="Browse", command=self._browse_podcasts_dest).grid(
            row=1, column=2, padx=5, pady=10)
        self.sync_podcasts_btn = tk.Button(self.extras_local_frame, text="Sync", command=self._start_sync_podcasts,
                                            width=10, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_podcasts_btn.grid(row=1, column=3, padx=(0, 10), pady=10)

        # ADB destination (hidden by default) — no Browse (can't pick a
        # phone path from a file dialog), so Sync sits in column 2 - the
        # same column Browse occupies in the other two frames - instead of
        # column 3, otherwise it'd sit one column further right than Browse
        # and the rows would look staggered instead of aligned. Column 3
        # becomes the phantom spacer instead, to keep this frame's total
        # column-width budget (and therefore its Entry width) matching.
        self.extras_adb_frame = ttk.Frame(dest_frame)
        self.extras_adb_frame.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.extras_adb_frame.columnconfigure(1, weight=1)
        self.extras_adb_frame.columnconfigure(3, minsize=BROWSE_COL_MINSIZE)
        self.extras_adb_frame.grid_remove()

        ttk.Label(self.extras_adb_frame, text="Audiobooks path:", width=LABEL_WIDTH, anchor="w").grid(
            row=0, column=0, sticky="w", padx=10, pady=10)
        self.adb_audiobooks_path_var = tk.StringVar(value="/storage/emulated/0/Audiobooks/")
        ttk.Entry(self.extras_adb_frame, textvariable=self.adb_audiobooks_path_var, width=ENTRY_WIDTH).grid(
            row=0, column=1, sticky="ew", padx=5, pady=10)
        self.sync_audiobooks_adb_btn = tk.Button(
            self.extras_adb_frame, text="Sync", command=self._start_sync_audiobooks,
            width=10, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_audiobooks_adb_btn.grid(row=0, column=2, padx=5, pady=10)

        ttk.Label(self.extras_adb_frame, text="Podcasts path:", width=LABEL_WIDTH, anchor="w").grid(
            row=1, column=0, sticky="w", padx=10, pady=10)
        self.adb_podcasts_path_var = tk.StringVar(value="/storage/emulated/0/Podcasts/")
        ttk.Entry(self.extras_adb_frame, textvariable=self.adb_podcasts_path_var, width=ENTRY_WIDTH).grid(
            row=1, column=1, sticky="ew", padx=5, pady=10)
        self.sync_podcasts_adb_btn = tk.Button(
            self.extras_adb_frame, text="Sync", command=self._start_sync_podcasts,
            width=10, bg="green", fg="white", font=('', 9, 'bold'))
        self.sync_podcasts_adb_btn.grid(row=1, column=2, padx=5, pady=10)

        # --- Options ---
        opts_frame = ttk.LabelFrame(parent, text="Options")
        opts_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)

        self.extras_skip_existing_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts_frame, text="Skip existing files", variable=self.extras_skip_existing_var).grid(
            row=0, column=0, sticky="w", padx=10, pady=5)

        self.extras_mirror_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts_frame, text="Delete files removed from source (mirror)",
                        variable=self.extras_mirror_var).grid(row=0, column=1, sticky="w", padx=10, pady=5)

        # --- Actions ---
        # Sync buttons live per-row above now; this just holds the shared
        # Cancel for whichever one is running.
        action_frame = ttk.Frame(parent)
        action_frame.grid(row=3, column=0, sticky="ew", padx=5, pady=10)

        self.extras_cancel_btn = tk.Button(action_frame, text="Cancel", command=self._cancel_sync,
                                            width=15, bg="red", fg="white", font=('', 9, 'bold'),
                                            state=tk.DISABLED)
        self.extras_cancel_btn.pack(side=tk.LEFT)

    def _browse_audiobooks_source(self) -> None:
        """Browse for the audiobooks source folder."""
        directory = filedialog.askdirectory(title="Select Audiobooks Source Folder")
        if directory:
            self.audiobooks_source_var.set(directory)

    def _browse_podcasts_source(self) -> None:
        """Browse for the podcasts source folder."""
        directory = filedialog.askdirectory(title="Select Podcasts Source Folder")
        if directory:
            self.podcasts_source_var.set(directory)

    def _browse_audiobooks_dest(self) -> None:
        """Browse for the audiobooks destination folder."""
        directory = filedialog.askdirectory(title="Select Audiobooks Destination Folder")
        if directory:
            self.audiobooks_dest_var.set(directory)

    def _browse_podcasts_dest(self) -> None:
        """Browse for the podcasts destination folder."""
        directory = filedialog.askdirectory(title="Select Podcasts Destination Folder")
        if directory:
            self.podcasts_dest_var.set(directory)

    def _on_quality_changed(self, event=None) -> None:
        """Handle quality combobox selection."""
        selection = self.quality_combo.get()
        # Map display text to internal value
        mapping = {
            "192 kbps (Transparent)": "192k",
            "160 kbps (Excellent)": "160k",
            "128 kbps (Recommended)": "128k",
            "96 kbps (Good)": "96k",
            "64 kbps (Efficient)": "64k"
        }
        self.bitrate_var.set(mapping.get(selection, "128k"))

    def _initialize_default_paths(self) -> None:
        """Initialize default paths from config or common locations."""
        settings = self.config_manager.settings

        # Try to find iTunes/MusicBee XML
        if settings.itunes_xml_path and os.path.exists(settings.itunes_xml_path):
            self.itunes_xml_var.set(settings.itunes_xml_path)
        else:
            possible_paths = [
                os.path.join(os.environ.get('USERPROFILE', ''), 'Music', 'iTunes', 'iTunes Music Library.xml'),
                os.path.join(os.environ.get('USERPROFILE', ''), 'Music', 'iTunes', 'iTunes Library.xml'),
                "M:\\iTunes Music Library.xml",
                "~/Music/iTunes/iTunes Music Library.xml",
            ]
            for path in possible_paths:
                expanded = os.path.expanduser(path)
                if os.path.exists(expanded):
                    self.itunes_xml_var.set(expanded)
                    break

        # Load other settings
        self.dest_var.set(settings.destination_path)
        self.music_dest_var.set(settings.music_destination_path)
        self.source_prefix_var.set(settings.source_music_prefix)
        self.android_prefix_var.set(settings.android_music_prefix)
        self.relative_paths_var.set(settings.use_relative_paths)

        # Load sync mode and Opus conversion settings
        self.sync_mode_var.set(settings.sync_mode)
        self.convert_opus_var.set(settings.convert_to_opus)
        self.skip_existing_var.set(settings.skip_existing)

        # Load ADB settings
        self.sync_target_var.set(settings.sync_target)
        self.adb_music_path_var.set(settings.adb_music_path)
        self.adb_playlist_path_var.set(settings.adb_playlist_path)
        # Seed the remembered device id — _detect_adb_device will reselect it
        # if the device is still attached (otherwise the user picks again).
        if settings.adb_device_id:
            self.adb_device_id = settings.adb_device_id

        # Set quality combobox based on saved bitrate
        bitrate_to_combo = {
            "192k": "192 kbps (Transparent)",
            "160k": "160 kbps (Excellent)",
            "128k": "128 kbps (Recommended)",
            "96k": "96 kbps (Good)",
            "64k": "64 kbps (Efficient)"
        }
        combo_value = bitrate_to_combo.get(settings.opus_bitrate, "128 kbps (Recommended)")
        self.quality_combo.set(combo_value)
        self.bitrate_var.set(settings.opus_bitrate)

        # Load Audiobooks/Podcasts settings
        self.audiobooks_source_var.set(settings.audiobooks_source_path or "M:\\Audiobooks")
        self.podcasts_source_var.set(settings.podcasts_source_path or "M:\\Podcasts")
        self.audiobooks_dest_var.set(settings.audiobooks_dest_path)
        self.podcasts_dest_var.set(settings.podcasts_dest_path)
        self.adb_audiobooks_path_var.set(settings.adb_audiobooks_path)
        self.adb_podcasts_path_var.set(settings.adb_podcasts_path)
        self.extras_skip_existing_var.set(settings.extras_skip_existing)
        self.extras_mirror_var.set(settings.extras_mirror)

        # Update UI state based on sync mode and target
        self._on_sync_mode_changed()
        self._on_sync_target_changed()

    def _browse_itunes_xml(self) -> None:
        """Browse for iTunes XML file."""
        filename = filedialog.askopenfilename(
            title="Select iTunes/MusicBee XML Library",
            filetypes=[("XML Files", "*.xml"), ("All Files", "*.*")]
        )
        if filename:
            self.itunes_xml_var.set(filename)

    def _schedule_autoload_playlists(self, delay=400) -> None:
        """Debounced auto-load of playlists when iTunes XML path changes."""
        if self._itunes_autoload_after_id is not None:
            try:
                self.root.after_cancel(self._itunes_autoload_after_id)
            except Exception:
                pass
        self._itunes_autoload_after_id = self.root.after(delay, self._auto_load_playlists)

    def _auto_load_playlists(self) -> None:
        self._itunes_autoload_after_id = None
        xml_path = self.itunes_xml_var.get()
        if not xml_path or not os.path.exists(xml_path):
            return
        if xml_path == self._loaded_xml_path:
            return
        self._load_playlists(silent=True)

    def _on_window_focus(self, event) -> None:
        # FocusIn fires for child widgets too — only react to the root window.
        if event.widget is not self.root:
            return
        self._check_xml_mtime()

    def _check_xml_mtime(self) -> None:
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

    def _browse_destination(self) -> None:
        """Browse for destination folder."""
        directory = filedialog.askdirectory(title="Select PowerAmp Playlists Folder")
        if directory:
            self.dest_var.set(directory)

    def _browse_music_destination(self) -> None:
        """Browse for music destination folder."""
        directory = filedialog.askdirectory(title="Select Music Destination Folder")
        if directory:
            self.music_dest_var.set(directory)

    def _on_sync_mode_changed(self) -> None:
        """Handle sync mode change - enable/disable music sync options."""
        playlists_only = self.sync_mode_var.get() == "playlists_only"
        is_adb = self.sync_target_var.get() == "adb"

        # Enable/disable music sync widgets (respecting both mode and target)
        state = "disabled" if playlists_only else "normal"
        combo_state = "disabled" if playlists_only else "readonly"

        # Local music folder is disabled in playlists_only mode or when using ADB
        local_music_state = "disabled" if (playlists_only or is_adb) else "normal"
        self.music_dest_entry.config(state=local_music_state)
        self.music_dest_browse_btn.config(state=local_music_state)

        self.convert_opus_checkbox.config(state=state)
        self.skip_existing_checkbox.config(state=state)
        self.quality_combo.config(state=combo_state)

    def _on_sync_target_changed(self) -> None:
        """Handle sync target change - show/hide local vs ADB settings."""
        is_adb = self.sync_target_var.get() == "adb"

        if is_adb:
            self.universal_adb_frame.grid()
            # Auto-populate the device picker when the panel becomes visible
            # so the user sees what's attached without clicking Refresh first.
            if not self._device_entries:
                self.root.after_idle(self._detect_adb_device)
        else:
            self.universal_adb_frame.grid_remove()

        if is_adb:
            self.local_dest_frame.grid_remove()
            self.adb_frame.grid()
        else:
            self.adb_frame.grid_remove()
            self.local_dest_frame.grid()

        if is_adb:
            self.extras_local_frame.grid_remove()
            self.extras_adb_frame.grid()
        else:
            self.extras_adb_frame.grid_remove()
            self.extras_local_frame.grid()

        # Update sync mode UI state as well (local music folder depends on target)
        self._on_sync_mode_changed()

    def _device_label(self, dev_id: str, state: str, model: str) -> str:
        """Combobox label for one device entry."""
        if state != "device":
            return f"{dev_id}  —  {state}"
        if model and model != dev_id:
            return f"{model}  ({dev_id})"
        return dev_id

    def _detect_adb_device(self) -> None:
        """Refresh the device picker. Auto-selects when unambiguous."""
        self.adb_device_status_var.set("Detecting...")
        self.root.update_idletasks()

        # Re-check for ADB in case user just dropped platform-tools into place
        self._find_adb()

        if not self._check_adb():
            self.adb_device_status_var.set("ADB not found — add to tools/platform-tools/")
            self._set_device_choices([])
            self.analysis_text.delete(1.0, tk.END)
            self.analysis_text.insert(tk.END, "ADB not found!\n\n")
            self.analysis_text.insert(tk.END, "To enable ADB sync, download Android Platform Tools:\n")
            self.analysis_text.insert(tk.END, "https://developer.android.com/tools/releases/platform-tools\n\n")
            self.analysis_text.insert(tk.END, f"Extract to: {LOCAL_ADB_DIR}\n\n")
            self.analysis_text.insert(tk.END, "The folder should contain adb.exe, fastboot.exe, etc.\n")
            return

        devices = self._list_adb_devices()
        if not devices:
            self._set_device_choices([])
            self.adb_device_status_var.set("No device connected")
            return

        # Build dropdown entries — only query model for ready devices (fast path)
        entries: List[Tuple[str, str, str]] = []
        for dev_id, state in devices:
            model = self._get_adb_device_name(dev_id) if state == "device" else ""
            entries.append((dev_id, state, self._device_label(dev_id, state, model)))
        self._set_device_choices(entries)

        ready = [e for e in entries if e[1] == "device"]

        # Honor a previously chosen device if still present and ready
        chosen: Optional[Tuple[str, str, str]] = None
        if self.adb_device_id:
            for entry in ready:
                if entry[0] == self.adb_device_id:
                    chosen = entry
                    break

        if chosen is None and len(ready) == 1:
            chosen = ready[0]

        if chosen is not None:
            self._select_adb_device_entry(chosen, persist=False)
            logger.info(f"ADB device selected: {chosen[2]}")
        else:
            # Either multiple ready devices and no prior pick, or no ready
            # devices at all — force the user to choose explicitly.
            self.adb_device_id = None
            self.adb_device_name = ""
            self.adb_device_combo_var.set("")
            if len(ready) > 1:
                self.adb_device_status_var.set(
                    f"{len(ready)} devices ready — pick one from the dropdown"
                )
            else:
                bad = [e for e in entries if e[1] != "device"]
                hint = f" ({bad[0][1]})" if len(bad) == 1 else ""
                self.adb_device_status_var.set(
                    f"No ready device{hint} — check authorization on the phone"
                )

    def _set_device_choices(self, entries: List[Tuple[str, str, str]]) -> None:
        """Replace the combobox contents. entries is [(id, state, label)]."""
        self._device_entries = entries
        self.adb_device_combo["values"] = [e[2] for e in entries]
        if not entries:
            self.adb_device_combo_var.set("")

    def _select_adb_device_entry(
        self, entry: Tuple[str, str, str], persist: bool = True
    ) -> None:
        """Apply an entry from _device_entries as the active device."""
        dev_id, state, label = entry
        self.adb_device_combo_var.set(label)
        if state == "device":
            self.adb_device_id = dev_id
            self.adb_device_name = self._get_adb_device_name(dev_id)
            self.adb_device_status_var.set(f"Connected: {label}")
            if persist:
                # Quiet persistence — don't show the "Settings saved" popup
                try:
                    self.config_manager.update_settings(adb_device_id=dev_id)
                except Exception as e:
                    logger.warning(f"Could not persist ADB device id: {e}")
        else:
            self.adb_device_id = None
            self.adb_device_name = ""
            self.adb_device_status_var.set(f"Not ready ({state}) — check the phone")

    def _on_adb_device_selected(self, _event=None) -> None:
        """User picked an entry in the device dropdown."""
        label = self.adb_device_combo_var.get()
        for entry in getattr(self, "_device_entries", []):
            if entry[2] == label:
                self._select_adb_device_entry(entry, persist=True)
                return

    def _show_adb_help(self) -> None:
        """Show ADB setup instructions in a dialog."""
        help_text = """ADB Setup Instructions

1. ENABLE DEVELOPER OPTIONS
   - Go to Settings → About phone
   - Tap "Build number" 7 times
   - You'll see "You are now a developer!"

2. ENABLE USB DEBUGGING
   - Go to Settings → Developer options
   - Enable "USB debugging"

3. CONNECT YOUR PHONE
   - Connect via USB cable
   - On your phone, tap "Allow USB debugging"
   - Check "Always allow from this computer"

4. INSTALL ADB (if not already)
   - Download Android Platform Tools from:
     https://developer.android.com/tools/releases/platform-tools
   - Extract to: tools/platform-tools/ in this script's folder

5. CLICK "Detect Device"

TROUBLESHOOTING
- If "unauthorized": Check phone for USB debugging prompt
- If "offline": Reconnect USB or restart phone
- Try different USB cable (some only charge)
- Try different USB port (USB 2.0 often works better)
"""
        # Create a simple help dialog
        help_window = tk.Toplevel(self.root)
        help_window.title("ADB Setup Help")
        help_window.geometry("500x480")
        help_window.transient(self.root)

        text_widget = tk.Text(help_window, wrap=tk.WORD, padx=15, pady=15, font=("Consolas", 10))
        text_widget.pack(fill=tk.BOTH, expand=True)
        text_widget.insert(tk.END, help_text)
        text_widget.config(state=tk.DISABLED)

        ttk.Button(help_window, text="Close", command=help_window.destroy).pack(pady=10)

    def _on_convert_opus_changed(self) -> None:
        """Handle conversion checkbox change - check for FFmpeg."""
        if self.convert_opus_var.get():
            has_ffmpeg = self._check_ffmpeg()
            if not has_ffmpeg:
                messagebox.showwarning(
                    "FFmpeg Required",
                    "FFmpeg is required for audio conversion but was not found.\n\n"
                    "Install FFmpeg:\n"
                    "- Windows: https://ffmpeg.org/download.html\n"
                    "- Or use: winget install ffmpeg\n\n"
                    "After installing, restart the application."
                )
                self.root.after(100, lambda: self.convert_opus_var.set(False))

    def _check_ffmpeg(self) -> bool:
        """Check if FFmpeg is available."""
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            subprocess.run(["ffmpeg", "-version"], **process_args, timeout=10)
            return True
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            return False

    def _check_ffmpeg_ui(self) -> None:
        """Check FFmpeg availability and show result in UI."""
        self.analysis_text.delete(1.0, tk.END)
        self.analysis_text.insert(tk.END, "Checking FFmpeg installation...\n\n")
        self.root.update_idletasks()

        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            result = subprocess.run(["ffmpeg", "-version"], **process_args, timeout=10)
            output = result.stdout.decode('utf-8', errors='replace')

            # Extract version info
            version_line = output.split('\n')[0] if output else "Unknown version"
            self.analysis_text.insert(tk.END, f"FFmpeg found: {version_line}\n")
            self.ffmpeg_status_var.set("FFmpeg OK")
            logger.info(f"FFmpeg available: {version_line}")

        except FileNotFoundError:
            self.analysis_text.insert(tk.END, "FFmpeg NOT FOUND!\n\n")
            self.analysis_text.insert(tk.END, "Install FFmpeg:\n")
            self.analysis_text.insert(tk.END, "- Windows: https://ffmpeg.org/download.html\n")
            self.analysis_text.insert(tk.END, "- Or use: winget install ffmpeg\n")
            self.analysis_text.insert(tk.END, "- macOS: brew install ffmpeg\n")
            self.analysis_text.insert(tk.END, "- Linux: apt install ffmpeg\n")
            self.ffmpeg_status_var.set("FFmpeg NOT FOUND")
            self.convert_opus_var.set(False)

        except Exception as e:
            self.analysis_text.insert(tk.END, f"Error checking FFmpeg: {e}\n")
            self.ffmpeg_status_var.set("Error")

    def _save_settings(self) -> None:
        """Save current UI settings to config."""
        selected = [
            self.playlist_tree.item(item, "text")
            for item in self.playlist_tree.selection()
        ]

        self.config_manager.update_settings(
            itunes_xml_path=self.itunes_xml_var.get(),
            destination_path=self.dest_var.get(),
            music_destination_path=self.music_dest_var.get(),
            source_music_prefix=self.source_prefix_var.get(),
            android_music_prefix=self.android_prefix_var.get(),
            use_relative_paths=self.relative_paths_var.get(),
            selected_playlists=selected,
            sync_mode=self.sync_mode_var.get(),
            convert_to_opus=self.convert_opus_var.get(),
            skip_existing=self.skip_existing_var.get(),
            opus_bitrate=self.bitrate_var.get(),
            # ADB settings
            sync_target=self.sync_target_var.get(),
            adb_music_path=self.adb_music_path_var.get(),
            adb_playlist_path=self.adb_playlist_path_var.get(),
            adb_device_id=self.adb_device_id or "",
            # Preset state (presets dict already mutated in place; explicit for clarity)
            playlist_presets=self.config_manager.settings.playlist_presets,
            active_preset=self.preset_var.get(),
            # Audiobooks/Podcasts settings
            audiobooks_source_path=self.audiobooks_source_var.get(),
            podcasts_source_path=self.podcasts_source_var.get(),
            audiobooks_dest_path=self.audiobooks_dest_var.get(),
            podcasts_dest_path=self.podcasts_dest_var.get(),
            adb_audiobooks_path=self.adb_audiobooks_path_var.get(),
            adb_podcasts_path=self.adb_podcasts_path_var.get(),
            extras_skip_existing=self.extras_skip_existing_var.get(),
            extras_mirror=self.extras_mirror_var.get(),
        )
        self.status_var.set("Settings saved")
        messagebox.showinfo("Settings Saved", "Configuration saved successfully!")

    def _load_playlists(self, silent: bool = False) -> None:
        """Load playlists from iTunes XML into the treeview.

        silent=True suppresses error popups (used by the path-change auto-loader).
        """
        xml_path = self.itunes_xml_var.get()
        if not xml_path or not os.path.exists(xml_path):
            if not silent:
                messagebox.showerror("Error", "Please select a valid iTunes/MusicBee XML file first!")
            return

        self.status_var.set("Loading playlists...")
        self.analysis_text.delete(1.0, tk.END)

        # Clear existing data
        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)
        self.all_playlists = []
        self.playlist_data = {}
        self.tracks_dict = {}

        try:
            # Parse the XML file
            self.analysis_text.insert(tk.END, f"Loading: {xml_path}\n\n")
            self.root.update_idletasks()

            with open(xml_path, 'r', encoding='utf-8') as f:
                xml_content = f.read()
                self.itunes_root = ET.fromstring(xml_content)

            library_dict = next((child for child in self.itunes_root if child.tag == 'dict'), None)
            if library_dict is None:
                if not silent:
                    messagebox.showerror("Error", "Invalid iTunes XML format!")
                return

            # First, build tracks dictionary for track lookups
            self._build_tracks_dict(library_dict)

            # Find playlists
            playlists_element = None
            for i in range(len(library_dict)):
                if library_dict[i].tag == 'key' and library_dict[i].text == 'Playlists':
                    if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'array':
                        playlists_element = library_dict[i + 1]
                    break

            if playlists_element is not None:
                auto_generated = {
                    "Library", "Music", "Liked Songs", "Recently Added",
                    "Top 100 Most Played", "Recently Played", "My Top Rated",
                    "Downloaded", "Audiobooks", "Podcasts", "Movies", "TV Shows"
                }

                for playlist_dict in playlists_element:
                    if playlist_dict.tag == 'dict':
                        playlist_name = None
                        playlist_id = None
                        track_count = 0
                        track_ids = []
                        is_smart = False
                        is_master = False
                        is_folder = False

                        # Extract playlist information
                        for i in range(0, len(playlist_dict), 2):
                            if i + 1 < len(playlist_dict) and playlist_dict[i].tag == 'key':
                                key = playlist_dict[i].text
                                value_element = playlist_dict[i + 1]

                                if key == 'Name':
                                    playlist_name = value_element.text
                                elif key == 'Playlist ID':
                                    playlist_id = value_element.text
                                elif key == 'Playlist Items' and value_element.tag == 'array':
                                    track_count = len(value_element)
                                    # Extract track IDs
                                    for item in value_element:
                                        if item.tag == 'dict':
                                            for j in range(0, len(item), 2):
                                                if j + 1 < len(item) and item[j].tag == 'key' and item[j].text == 'Track ID':
                                                    if item[j + 1].text:
                                                        track_ids.append(item[j + 1].text)
                                elif key == 'Smart Info':
                                    is_smart = True
                                elif key == 'Master':
                                    is_master = True
                                elif key == 'Folder':
                                    is_folder = True

                        if playlist_name and not is_folder:
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
                                'is_master': is_master
                            }

                            # Add to treeview
                            self.playlist_tree.insert(
                                "", "end",
                                text=playlist_name,
                                values=(track_count, playlist_type)
                            )

                self.analysis_text.insert(tk.END, f"Found {len(self.all_playlists)} playlists\n")
                self.analysis_text.insert(tk.END, f"Total tracks in library: {len(self.tracks_dict)}\n\n")

                # Show breakdown by type
                type_counts = {}
                for data in self.playlist_data.values():
                    t = data['type']
                    type_counts[t] = type_counts.get(t, 0) + 1

                self.analysis_text.insert(tk.END, "Playlist types:\n")
                for t, count in sorted(type_counts.items()):
                    self.analysis_text.insert(tk.END, f"  {t}: {count}\n")

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
                    self._auto_select_playlists()
                self._filter_playlists()

            else:
                if not silent:
                    messagebox.showerror("Error", "No playlists found in XML!")

        except Exception as e:
            if not silent:
                messagebox.showerror("Error", f"Failed to load playlists: {str(e)}")
            self.status_var.set("Error loading playlists")
            logger.error(f"Error loading playlists: {e}")

    def _build_tracks_dict(self, library_dict) -> None:
        """Build a dictionary of track ID -> track info from the iTunes XML."""
        tracks_element = None
        for i in range(len(library_dict)):
            if library_dict[i].tag == 'key' and library_dict[i].text == 'Tracks':
                if i + 1 < len(library_dict) and library_dict[i + 1].tag == 'dict':
                    tracks_element = library_dict[i + 1]
                break

        if tracks_element is None:
            return

        for i in range(0, len(tracks_element), 2):
            if i + 1 >= len(tracks_element):
                break

            if tracks_element[i].tag == 'key' and tracks_element[i + 1].tag == 'dict':
                track_id = tracks_element[i].text
                track_dict = tracks_element[i + 1]

                track_info = {}
                for j in range(0, len(track_dict), 2):
                    if j + 1 >= len(track_dict):
                        break

                    if track_dict[j].tag == 'key':
                        key = track_dict[j].text
                        value_element = track_dict[j + 1]

                        if key == 'Location':
                            # Decode the file:// URL
                            location = value_element.text
                            if location:
                                # Convert file://localhost/M:/... to M:/...
                                if location.startswith('file://localhost/'):
                                    location = urllib.parse.unquote(location[17:])
                                elif location.startswith('file:///'):
                                    location = urllib.parse.unquote(location[8:])
                                elif location.startswith('file://'):
                                    location = urllib.parse.unquote(location[7:])
                                track_info['location'] = location
                        elif key == 'Name':
                            track_info['name'] = value_element.text
                        elif key == 'Artist':
                            track_info['artist'] = value_element.text

                if track_info:
                    self.tracks_dict[track_id] = track_info

    def _filter_playlists(self, *args) -> None:
        """Filter displayed playlists based on search term."""
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
                playlist_type = playlist_info.get('type', 'User')

                item_id = self.playlist_tree.insert(
                    "", "end",
                    text=playlist_name,
                    values=(track_count, playlist_type)
                )

                # Restore selection
                if playlist_name in selected_playlists:
                    self.playlist_tree.selection_add(item_id)

        self._update_selection_summary()

    def _update_selection_summary(self) -> None:
        """Update the selection summary text."""
        if not self.all_playlists:
            self.selection_summary.set("No playlists loaded")
            return

        selected_items = self.playlist_tree.selection()
        total_playlists = len(self.all_playlists)
        selected_count = len(selected_items)

        # Calculate total tracks
        playlists_to_process = self._get_selected_playlists()
        total_tracks = sum(
            self.playlist_data.get(name, {}).get('track_count', 0)
            for name in playlists_to_process
        )

        if self.selection_mode.get() == "include":
            if selected_count == 0:
                self.selection_summary.set(f"No playlists selected (0/{total_playlists})")
            else:
                self.selection_summary.set(
                    f"Will sync {selected_count}/{total_playlists} playlists ({total_tracks} total tracks)"
                )
        else:  # exclude mode
            processed_count = total_playlists - selected_count
            if selected_count == 0:
                self.selection_summary.set(
                    f"Will sync all {total_playlists} playlists ({total_tracks} total tracks)"
                )
            else:
                self.selection_summary.set(
                    f"Will sync {processed_count}/{total_playlists} playlists (excluding {selected_count})"
                )

        # Update sync button state
        if hasattr(self, 'sync_btn'):
            if self.selection_mode.get() == "include" and selected_count == 0:
                self.sync_btn.config(state=tk.DISABLED)
                self.sync_playlists_btn.config(state=tk.DISABLED)
            else:
                self.sync_btn.config(state=tk.NORMAL)
                self.sync_playlists_btn.config(state=tk.NORMAL)

    def _get_selected_playlists(self) -> List[str]:
        """Get the list of playlists that should be processed."""
        selected_items = self.playlist_tree.selection()
        selected_playlists = [
            self.playlist_tree.item(item, "text")
            for item in selected_items
        ]

        if self.selection_mode.get() == "include":
            return selected_playlists
        else:
            # Exclude mode - return all except selected
            all_visible = [
                self.playlist_tree.item(item, "text")
                for item in self.playlist_tree.get_children()
            ]
            return [p for p in all_visible if p not in selected_playlists]

    def _select_all_playlists(self) -> None:
        """Select all visible playlists."""
        items = self.playlist_tree.get_children()
        self.playlist_tree.selection_set(items)
        self._update_selection_summary()

    def _clear_all_playlists(self) -> None:
        """Clear all playlist selections."""
        self.playlist_tree.selection_remove(self.playlist_tree.selection())
        self._update_selection_summary()

    def _auto_select_playlists(self) -> None:
        """Auto-select playlists (those starting with numbers 1-9)."""
        self._clear_all_playlists()

        for item in self.playlist_tree.get_children():
            playlist_name = self.playlist_tree.item(item, "text")

            # Only select playlists that start with a digit 1-9
            if playlist_name and len(playlist_name) > 0:
                first_char = playlist_name[0]
                if first_char.isdigit() and first_char != '0':
                    self.playlist_tree.selection_add(item)

        self._update_selection_summary()

    # ------------------------------------------------------------------
    # Playlist presets
    # ------------------------------------------------------------------

    def _refresh_preset_combo(self, select: Optional[str] = None) -> None:
        """Refresh the preset dropdown values from settings."""
        if not hasattr(self, "preset_combo"):
            return
        names = [DEFAULT_PRESET_NAME] + sorted(self.config_manager.settings.playlist_presets.keys())
        self.preset_combo["values"] = names
        if select and select in names:
            self.preset_var.set(select)
        elif self.preset_var.get() not in names:
            self.preset_var.set(DEFAULT_PRESET_NAME)

    def _apply_selection(self, playlists: List[str], mode: str) -> None:
        """Apply a (playlists, mode) selection to the tree, logging missing names."""
        self.selection_mode.set(mode if mode in ("include", "exclude") else "include")
        self._clear_all_playlists()

        wanted = set(playlists)
        found = set()
        for item in self.playlist_tree.get_children():
            name = self.playlist_tree.item(item, "text")
            if name in wanted:
                self.playlist_tree.selection_add(item)
                found.add(name)

        missing = sorted(wanted - found)
        if missing and hasattr(self, "sync_text"):
            self._append_sync_text(
                f"Preset: skipped {len(missing)} playlist(s) not in current XML: {', '.join(missing)}\n"
            )
        self._update_selection_summary()

    def _load_preset(self) -> None:
        """Load the currently selected preset into the playlist tree."""
        name = self.preset_var.get()
        if not name:
            return

        if not self.all_playlists:
            messagebox.showinfo("Load Preset", "Load playlists first, then apply the preset.")
            return

        if name == DEFAULT_PRESET_NAME:
            self._auto_select_playlists()
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

    def _save_preset_as(self) -> None:
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

    def _overwrite_preset(self) -> None:
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

    def _delete_preset(self) -> None:
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

    def _preview_selection(self) -> None:
        """Show a preview of what will be synced."""
        selected_playlists = self._get_selected_playlists()

        if not selected_playlists:
            messagebox.showwarning("No Selection", "No playlists are selected for sync!")
            return

        # Create preview window
        preview_window = tk.Toplevel(self.root)
        preview_window.title("Sync Preview")
        preview_window.geometry("550x450")
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
        preview_text.insert(tk.END, "=" * 50 + "\n\n")

        preview_text.insert(tk.END, f"Destination: {self.dest_var.get()}\n")
        preview_text.insert(tk.END, f"Path mapping: {self.source_prefix_var.get()} → {self.android_prefix_var.get()}\n\n")

        total_tracks = 0
        for playlist_name in selected_playlists:
            playlist_info = self.playlist_data.get(playlist_name, {})
            track_count = playlist_info.get('track_count', 0)
            playlist_type = playlist_info.get('type', 'Unknown')

            preview_text.insert(tk.END, f"• {playlist_name}\n")
            preview_text.insert(tk.END, f"  Type: {playlist_type}, Tracks: {track_count}\n")
            preview_text.insert(tk.END, f"  Output: {playlist_name}.m3u\n\n")
            total_tracks += track_count

        preview_text.insert(tk.END, f"\nTotal tracks across all playlists: {total_tracks}\n")
        preview_text.insert(tk.END, "(Note: Duplicate tracks will appear in each playlist)")

        preview_text.config(state=tk.DISABLED)

        # Close button
        ttk.Button(preview_window, text="Close", command=preview_window.destroy).pack(pady=10)

    def _get_relative_path(self, source_path: str) -> str:
        """Get relative path from source prefix for file organization."""
        source_prefix = self.source_prefix_var.get().replace('\\', '/')
        path = source_path.replace('\\', '/')

        if path.lower().startswith(source_prefix.lower()):
            return path[len(source_prefix):].lstrip('/')
        elif len(path) > 2 and path[1] == ':':
            return path[3:].lstrip('/')
        return os.path.basename(path)

    def _get_dest_file_path(self, source_path: str, convert_to_opus: bool) -> str:
        """Calculate destination path for a source file."""
        music_dest = self.music_dest_var.get().strip()
        relative_path = self._get_relative_path(source_path)

        # Change extension if converting
        if convert_to_opus:
            file_ext = os.path.splitext(relative_path)[1].lower()
            if file_ext in CONVERTIBLE_EXTENSIONS:
                relative_path = os.path.splitext(relative_path)[0] + '.opus'

        return os.path.join(music_dest, relative_path)

    def _find_cover_art(self, source_path: str) -> Optional[str]:
        """Find cover art image in the same folder as the source file."""
        source_dir = os.path.dirname(source_path)
        try:
            for f in os.listdir(source_dir):
                if f.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tiff', '.tif', '.webp')):
                    return os.path.join(source_dir, f)
        except OSError:
            pass
        return None

    def _source_has_embedded_art(self, source_path: str) -> bool:
        """Check if the source file has embedded cover art."""
        try:
            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }
            # Use ffprobe to check for video/image streams (cover art)
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
                 "stream=codec_type", "-of", "csv=p=0", source_path],
                **process_args, timeout=30
            )
            # If there's output, there's a video/image stream (cover art)
            return bool(result.stdout.decode('utf-8', errors='replace').strip())
        except Exception:
            return False

    def _extract_cover_art(self, source_audio_path: str, dest_cover_path: str) -> bool:
        """Extract embedded cover art from an audio file using FFmpeg."""
        try:
            os.makedirs(os.path.dirname(dest_cover_path), exist_ok=True)

            cmd = [
                "ffmpeg", "-y", "-i", source_audio_path,
                "-an",              # No audio
                "-vcodec", "copy",  # Copy the image stream as-is
                dest_cover_path
            ]

            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }

            result = subprocess.run(cmd, **process_args, timeout=60)

            if result.returncode == 0 and os.path.exists(dest_cover_path):
                # Verify the file is not empty/corrupt
                if os.path.getsize(dest_cover_path) > 100:
                    return True
                else:
                    os.remove(dest_cover_path)
                    return False
            return False

        except Exception as e:
            logger.error(f"Error extracting cover art from {source_audio_path}: {e}")
            return False

    def _ensure_cover_art(self, source_path: str, dest_dir: str, processed_dirs: set) -> Optional[str]:
        """Ensure cover art exists in the destination directory.

        Args:
            source_path: Path to a source audio file
            dest_dir: Destination directory where cover should be placed
            processed_dirs: Set of already-processed destination directories

        Returns:
            Path to cover art if created/found, None otherwise
        """
        # Skip if we've already processed this directory
        if dest_dir in processed_dirs:
            return None

        processed_dirs.add(dest_dir)

        # Check if cover already exists in destination
        dest_cover = os.path.join(dest_dir, "cover.jpg")
        if os.path.exists(dest_cover):
            return dest_cover

        # Also check for other common cover names in destination
        for name in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png']:
            existing = os.path.join(dest_dir, name)
            if os.path.exists(existing):
                return existing

        os.makedirs(dest_dir, exist_ok=True)
        source_dir = os.path.dirname(source_path)

        # Try to find and copy external cover art from source
        external_cover = self._find_cover_art(source_path)
        if external_cover:
            try:
                # Determine extension from source
                ext = os.path.splitext(external_cover)[1].lower()
                if ext not in ['.jpg', '.jpeg', '.png']:
                    ext = '.jpg'
                dest_cover = os.path.join(dest_dir, f"cover{ext}")
                shutil.copy2(external_cover, dest_cover)
                return dest_cover
            except Exception as e:
                logger.error(f"Failed to copy cover art: {e}")

        # No external cover - try to extract from this audio file
        if self._source_has_embedded_art(source_path):
            dest_cover = os.path.join(dest_dir, "cover.jpg")
            if self._extract_cover_art(source_path, dest_cover):
                return dest_cover

        # Try to find any audio file in source dir with embedded art
        try:
            for f in os.listdir(source_dir):
                if f.lower().endswith(tuple(VALID_EXTENSIONS)):
                    audio_file = os.path.join(source_dir, f)
                    if self._source_has_embedded_art(audio_file):
                        dest_cover = os.path.join(dest_dir, "cover.jpg")
                        if self._extract_cover_art(audio_file, dest_cover):
                            return dest_cover
        except OSError:
            pass

        return None

    def _convert_to_opus(self, source_path: str, dest_path: str) -> bool:
        """Convert an audio file to Opus using FFmpeg, preserving metadata."""
        try:
            # Ensure destination directory exists
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)

            # Build FFmpeg command based on bitrate setting
            bitrate = self.bitrate_var.get()

            # Build the FFmpeg command for Opus encoding
            # Opus uses VBR by default and the bitrate is a target
            cmd = [
                "ffmpeg", "-y", "-i", source_path,
                "-c:a", "libopus",
                "-b:a", bitrate,
                "-vbr", "on",           # Enable VBR for better quality
                "-compression_level", "10",  # Max compression effort
                "-application", "audio",     # Optimize for music (not speech)
                "-vn",                  # No video output (Opus doesn't support embedded art)
                "-map_metadata", "0",   # Copy metadata from source
                dest_path
            ]

            process_args = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE,
                "creationflags": subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            }

            result = subprocess.run(cmd, **process_args, timeout=300)

            if result.returncode == 0 and os.path.exists(dest_path):
                return True
            else:
                logger.error(f"FFmpeg conversion failed for {source_path}: {result.stderr.decode('utf-8', errors='replace')}")
                return False

        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg timeout converting {source_path}")
            return False
        except Exception as e:
            logger.error(f"Error converting {source_path}: {e}")
            return False

    def _copy_file(self, source_path: str, dest_path: str) -> bool:
        """Copy a file to destination."""
        try:
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(source_path, dest_path)
            return True
        except Exception as e:
            logger.error(f"Error copying {source_path}: {e}")
            return False

    def _translate_path(self, windows_path: str) -> str:
        """Translate a Windows path to an Android-compatible path."""
        path = windows_path.strip()

        # Normalize path separators
        path = path.replace('\\', '/')

        if self.relative_paths_var.get():
            # Return just the filename
            return os.path.basename(path)

        # Replace source prefix with Android prefix
        source_prefix = self.source_prefix_var.get().replace('\\', '/')
        android_prefix = self.android_prefix_var.get()

        # Case-insensitive prefix replacement
        if path.lower().startswith(source_prefix.lower()):
            path = android_prefix + path[len(source_prefix):]
        elif len(path) > 2 and path[1] == ':':
            # Handle drive letter
            path = android_prefix + path[3:]

        # Ensure forward slashes
        path = path.replace('\\', '/')

        return path

    def _write_m3u8_playlist(self, playlist_name: str, track_ids: List[str], output_path: str,
                              path_mapping: Optional[Dict[str, str]] = None) -> Tuple[bool, int]:
        """Write a playlist to M3U/M3U8 format optimized for PowerAmp.

        Args:
            playlist_name: Name of the playlist
            track_ids: List of track IDs
            output_path: Path to write the M3U file
            path_mapping: Optional dict mapping source paths to android paths (for converted files)
        """
        try:
            tracks_written = 0

            # Write with UTF-8 BOM for better compatibility with Android apps
            with open(output_path, 'w', encoding='utf-8-sig') as f:
                # Write M3U header - PowerAmp reads #PLAYLIST: for the display name
                # No blank lines between header directives for better compatibility
                f.write('#EXTM3U\n')
                f.write(f'#PLAYLIST:{playlist_name}\n')

                for track_id in track_ids:
                    track_info = self.tracks_dict.get(track_id, {})
                    location = track_info.get('location', '')

                    if location:
                        # Use path mapping if available, otherwise translate directly
                        if path_mapping and location in path_mapping:
                            android_path = path_mapping[location]
                        else:
                            android_path = self._translate_path(location)

                        # Write EXTINF line with track info if available
                        name = track_info.get('name', os.path.basename(location))
                        artist = track_info.get('artist', '')
                        if artist:
                            f.write(f'#EXTINF:-1,{artist} - {name}\n')
                        else:
                            f.write(f'#EXTINF:-1,{name}\n')

                        f.write(f'{android_path}\n')
                        tracks_written += 1

            return True, tracks_written

        except Exception as e:
            logger.error(f"Error writing playlist {playlist_name}: {e}")
            return False, 0

    def _start_sync_playlists_only(self) -> None:
        """Start sync in playlists-only mode."""
        self.sync_mode_var.set("playlists_only")
        self._do_start_sync()

    def _start_sync(self) -> None:
        """Start the full music + playlists sync."""
        self.sync_mode_var.set("music_and_playlists")
        self._do_start_sync()

    def _do_start_sync(self) -> None:
        """Start the sync process."""
        selected_playlists = self._get_selected_playlists()

        if not selected_playlists:
            messagebox.showwarning("No Selection", "Please select at least one playlist to sync.")
            return

        # Determine sync target mode
        is_adb_mode = self.sync_target_var.get() == "adb"
        playlists_only = self.sync_mode_var.get() == "playlists_only"
        convert_to_opus = self.convert_opus_var.get()
        mirror_sync = True  # Always enabled - delete orphaned files not in playlists

        # Get destinations based on mode
        if is_adb_mode:
            music_dest = self.adb_music_path_var.get().strip().rstrip('/')
            playlist_dest = self.adb_playlist_path_var.get().strip().rstrip('/')

            # Resolve and pin the target device. This is the single gate that
            # guarantees self.adb_device_id is set to a ready device before any
            # sync work runs — otherwise _adb_cmd would skip the -s flag and
            # multi-device setups would flood the log with "more than one
            # device/emulator" errors per file.
            ok, err_msg = self._resolve_adb_device()
            if not ok:
                messagebox.showerror("ADB Device", err_msg)
                return
            # Reflect the resolved device in the picker UI
            for entry in self._device_entries:
                if entry[0] == self.adb_device_id:
                    self.adb_device_combo_var.set(entry[2])
                    self.adb_device_status_var.set(f"Connected: {entry[2]}")
                    break
        else:
            playlist_dest = self.dest_var.get().strip()
            music_dest = self.music_dest_var.get().strip()

            if not playlist_dest:
                messagebox.showwarning("No Destination", "Please specify a playlist destination folder.")
                return

        sync_music = not playlists_only

        # Validate music sync settings
        if sync_music and not is_adb_mode and not music_dest:
            messagebox.showwarning("No Music Destination",
                "Please specify a music destination folder, or select 'Playlists Only' mode.")
            return

        if sync_music and convert_to_opus:
            if not self._check_ffmpeg():
                messagebox.showerror(
                    "FFmpeg Not Found",
                    "FFmpeg is required for audio conversion but was not found.\n\n"
                    "Either install FFmpeg or disable the conversion option."
                )
                return

        # Create local destinations if needed (not for ADB mode)
        if not is_adb_mode:
            try:
                os.makedirs(playlist_dest, exist_ok=True)
                if sync_music:
                    os.makedirs(music_dest, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Error", f"Cannot create destination folder:\n{e}")
                return

        # Build confirmation message
        target_label = "Android device (ADB)" if is_adb_mode else "local folder"
        if playlists_only:
            msg_parts = [f"Export {len(selected_playlists)} playlist(s) to {target_label}"]
        else:
            msg_parts = [f"Sync {len(selected_playlists)} playlist(s) to {target_label}"]
            msg_parts.append(f"\nMusic to: {music_dest}")
            if convert_to_opus:
                msg_parts.append(f"\nConvert all audio to Opus ({self.bitrate_var.get()})")
        msg_parts.append(f"\nPlaylists to: {playlist_dest}")
        msg_parts.append("\n\nMirror sync: orphaned files will be deleted")
        msg_parts.append("\n\nProceed?")

        if not messagebox.askyesno("Confirm Sync", "".join(msg_parts)):
            return

        # Start sync in thread
        self.syncing = True
        self._set_sync_buttons_state(tk.DISABLED, tk.NORMAL)

        self.sync_text.delete(1.0, tk.END)
        self.results_notebook.select(1)  # Switch to Sync Progress tab

        def sync_thread():
            path_mapping = {}  # source path -> android path
            expected_files = set()  # Files that should exist after sync (relative paths)
            playlist_success = 0
            playlist_error = 0

            mode_label = "ADB" if is_adb_mode else "Local"
            self._append_sync_text(f"Starting {mode_label} sync of {len(selected_playlists)} playlist(s)...\n")
            if sync_music:
                self._append_sync_text(f"Music destination: {music_dest}\n")
                if convert_to_opus:
                    self._append_sync_text(f"Converting audio to Opus ({self.bitrate_var.get()})\n")
            self._append_sync_text(f"Playlist destination: {playlist_dest}\n")
            self._append_sync_text("Mirror sync: ON (orphaned files will be deleted)\n")
            self._append_sync_text("=" * 50 + "\n\n")

            # Phase 1: Sync music files if enabled
            if sync_music:
                self._append_sync_text("PHASE 1: Syncing music files...\n")

                # Gather unique tracks from all selected playlists
                unique_tracks = {}  # source_path -> track_info
                for playlist_name in selected_playlists:
                    playlist_info = self.playlist_data.get(playlist_name, {})
                    for track_id in playlist_info.get('track_ids', []):
                        track_info = self.tracks_dict.get(track_id, {})
                        location = track_info.get('location', '')
                        if location and location not in unique_tracks:
                            unique_tracks[location] = track_info

                total_tracks = len(unique_tracks)
                self._append_sync_text(f"Found {total_tracks} unique tracks to sync\n\n")

                # Build expected files set for mirror sync
                for source_path in unique_tracks.keys():
                    relative_path = self._get_relative_path(source_path)
                    if convert_to_opus:
                        file_ext = os.path.splitext(relative_path)[1].lower()
                        if file_ext in CONVERTIBLE_EXTENSIONS:
                            relative_path = os.path.splitext(relative_path)[0] + '.opus'
                    expected_files.add(relative_path.replace('\\', '/'))

                copied_count = 0
                converted_count = 0
                pushed_count = 0
                skipped_count = 0
                error_count = 0
                covers_count = 0
                skip_existing = self.skip_existing_var.get()
                processed_cover_dirs = set()  # Track dirs where we've handled cover art

                # Pre-scan destination ONCE for fast existence checks.
                # Avoids one subprocess (or stat) per track during the sync loop.
                self._append_sync_text("Scanning destination for existing files...\n")
                self.root.after(0, lambda: self.status_var.set("Scanning destination..."))
                if is_adb_mode:
                    existing_set = set(self._adb_list_files(music_dest))
                else:
                    existing_set = set(self._local_list_files(self.music_dest_var.get().strip()))
                self._append_sync_text(f"Found {len(existing_set)} existing file(s) in destination\n\n")

                for i, (source_path, track_info) in enumerate(unique_tracks.items()):
                    if not self.syncing:
                        self._append_sync_text("\nSync cancelled by user.\n")
                        break

                    # Update status
                    self.root.after(0, lambda n=i+1, t=total_tracks:
                                    self.status_var.set(f"Syncing music {n}/{t}"))

                    file_ext = os.path.splitext(source_path)[1].lower()
                    needs_conversion = convert_to_opus and file_ext in CONVERTIBLE_EXTENSIONS
                    relative_path = self._get_relative_path(source_path)

                    # Calculate destination path and android path
                    if needs_conversion:
                        relative_path_converted = os.path.splitext(relative_path)[0] + '.opus'
                    else:
                        relative_path_converted = relative_path

                    if is_adb_mode:
                        dest_path = music_dest + '/' + relative_path_converted.replace('\\', '/')
                        android_path = dest_path
                    else:
                        dest_path = os.path.join(self.music_dest_var.get().strip(), relative_path_converted)
                        android_path = self.android_prefix_var.get() + relative_path_converted.replace('\\', '/')

                    path_mapping[source_path] = android_path

                    # Check if source file exists
                    if not os.path.exists(source_path):
                        self._append_sync_text(f"✗ Missing: {os.path.basename(source_path)}\n")
                        error_count += 1
                        continue

                    filename = os.path.basename(source_path)

                    # Relative form for set membership against the pre-built scan.
                    relative_dest_key = relative_path_converted.replace('\\', '/').lstrip('/')

                    if is_adb_mode:
                        # ADB mode: O(1) check against the pre-scanned set
                        if skip_existing and relative_dest_key in existing_set:
                            skipped_count += 1
                            continue

                        # Convert to temp file then push
                        if needs_conversion:
                            self._append_sync_text(f"Converting & pushing: {filename}...")
                            with tempfile.NamedTemporaryFile(suffix='.opus', delete=False) as tmp:
                                tmp_path = tmp.name

                            try:
                                if self._convert_to_opus(source_path, tmp_path):
                                    success, error = self._adb_push(tmp_path, dest_path)
                                    if success:
                                        self._append_sync_text(" OK\n")
                                        converted_count += 1
                                        pushed_count += 1
                                    else:
                                        self._append_sync_text(f" PUSH FAILED: {error}\n")
                                        error_count += 1
                                else:
                                    self._append_sync_text(" CONVERT FAILED\n")
                                    error_count += 1
                            finally:
                                if os.path.exists(tmp_path):
                                    os.remove(tmp_path)
                        else:
                            # Push file directly
                            self._append_sync_text(f"Pushing: {filename}...")
                            success, error = self._adb_push(source_path, dest_path)
                            if success:
                                self._append_sync_text(" OK\n")
                                pushed_count += 1
                            else:
                                self._append_sync_text(f" FAILED: {error}\n")
                                error_count += 1

                        # Handle cover art for ADB
                        dest_dir = os.path.dirname(dest_path)
                        if dest_dir not in processed_cover_dirs:
                            processed_cover_dirs.add(dest_dir)
                            # Check the pre-scanned set rather than another adb shell call
                            cover_rel_dir = os.path.dirname(relative_dest_key)
                            cover_rel = (cover_rel_dir + '/cover.jpg').lstrip('/') if cover_rel_dir else 'cover.jpg'
                            remote_cover = dest_dir + '/cover.jpg'
                            if cover_rel not in existing_set:
                                # Try to find and push cover art
                                cover_source = self._find_cover_art(source_path)
                                if cover_source:
                                    success, _ = self._adb_push(cover_source, remote_cover)
                                    if success:
                                        covers_count += 1
                                elif self._source_has_embedded_art(source_path):
                                    # Extract to temp then push
                                    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
                                        tmp_cover = tmp.name
                                    try:
                                        if self._extract_cover_art(source_path, tmp_cover):
                                            success, _ = self._adb_push(tmp_cover, remote_cover)
                                            if success:
                                                covers_count += 1
                                    finally:
                                        if os.path.exists(tmp_cover):
                                            os.remove(tmp_cover)
                    else:
                        # Local mode: O(1) check against the pre-scanned set
                        if skip_existing and relative_dest_key in existing_set:
                            skipped_count += 1
                            # Still check for cover art even if audio is skipped
                            dest_dir = os.path.dirname(dest_path)
                            if dest_dir not in processed_cover_dirs:
                                cover_result = self._ensure_cover_art(source_path, dest_dir, processed_cover_dirs)
                                if cover_result:
                                    covers_count += 1
                            continue

                        # Copy or convert
                        if needs_conversion:
                            self._append_sync_text(f"Converting: {filename}...")
                            if self._convert_to_opus(source_path, dest_path):
                                self._append_sync_text(" OK\n")
                                converted_count += 1
                            else:
                                self._append_sync_text(" FAILED\n")
                                error_count += 1
                        else:
                            if self._copy_file(source_path, dest_path):
                                copied_count += 1
                            else:
                                self._append_sync_text(f"✗ Failed to copy: {filename}\n")
                                error_count += 1

                        # Handle cover art for this destination folder
                        dest_dir = os.path.dirname(dest_path)
                        if dest_dir not in processed_cover_dirs:
                            cover_result = self._ensure_cover_art(source_path, dest_dir, processed_cover_dirs)
                            if cover_result:
                                covers_count += 1

                # Music sync summary
                self._append_sync_text(f"\nMusic sync complete:\n")
                self._append_sync_text(f"  Converted: {converted_count}\n")
                if is_adb_mode:
                    self._append_sync_text(f"  Pushed: {pushed_count}\n")
                else:
                    self._append_sync_text(f"  Copied: {copied_count}\n")
                self._append_sync_text(f"  Skipped (existing): {skipped_count}\n")
                self._append_sync_text(f"  Cover art added: {covers_count}\n")
                if error_count > 0:
                    self._append_sync_text(f"  Errors: {error_count}\n")
                self._append_sync_text("\n" + "=" * 50 + "\n\n")

                if not self.syncing:
                    self.root.after(0, self._sync_complete)
                    return

            # Phase 2: Mirror sync - delete orphaned files
            if mirror_sync and sync_music and self.syncing:
                phase_num = "2"
                self._append_sync_text(f"PHASE {phase_num}: Cleaning up orphaned files...\n")

                deleted_count = 0
                deleted_dirs = 0

                if is_adb_mode:
                    # Reuse the pre-scan from Phase 1 if available; otherwise scan now.
                    if 'existing_set' in locals():
                        existing_files = list(existing_set)
                        self._append_sync_text(f"Reusing earlier scan ({len(existing_files)} files on device)\n")
                    else:
                        self._append_sync_text("Scanning device for existing files...\n")
                        existing_files = self._adb_list_files(music_dest)
                        self._append_sync_text(f"Found {len(existing_files)} files on device\n")

                    # Find orphaned files (exclude cover art from deletion consideration)
                    orphaned = []
                    for f in existing_files:
                        # Skip cover art files - they're cleaned up with empty folders
                        if os.path.basename(f).lower() in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png']:
                            continue
                        if f not in expected_files:
                            orphaned.append(f)

                    if orphaned:
                        self._append_sync_text(f"Found {len(orphaned)} orphaned files to delete\n")
                        for orphan in orphaned:
                            if not self.syncing:
                                break
                            full_path = music_dest + '/' + orphan
                            if self._adb_delete(full_path):
                                self._append_sync_text(f"  Deleted: {orphan}\n")
                                deleted_count += 1
                            else:
                                self._append_sync_text(f"  Failed to delete: {orphan}\n")

                        # Clean up empty directories
                        self._append_sync_text("\nCleaning up empty directories...\n")
                        # Get unique parent directories of deleted files
                        deleted_parents = set()
                        for orphan in orphaned:
                            parent = os.path.dirname(orphan)
                            while parent:
                                deleted_parents.add(parent)
                                parent = os.path.dirname(parent)

                        # Sort by depth (deepest first) to clean from bottom up
                        for parent in sorted(deleted_parents, key=lambda x: x.count('/'), reverse=True):
                            full_dir = music_dest + '/' + parent
                            # Try to delete cover art in the directory first
                            for cover_name in ['cover.jpg', 'cover.png', 'folder.jpg']:
                                self._adb_delete(full_dir + '/' + cover_name)
                            if self._adb_rmdir_empty(full_dir):
                                deleted_dirs += 1
                    else:
                        self._append_sync_text("No orphaned files found\n")
                else:
                    # Local mode mirror sync — reuse the Phase 1 scan if available.
                    local_music_dest = self.music_dest_var.get().strip()
                    if 'existing_set' in locals():
                        existing_files = list(existing_set)
                        self._append_sync_text(f"Reusing earlier scan ({len(existing_files)} files locally)\n")
                    else:
                        self._append_sync_text("Scanning local folder for existing files...\n")
                        existing_files = self._local_list_files(local_music_dest)
                        self._append_sync_text(f"Found {len(existing_files)} files locally\n")

                    # Find orphaned files
                    orphaned = []
                    for f in existing_files:
                        # Skip cover art files
                        if os.path.basename(f).lower() in ['cover.jpg', 'cover.png', 'folder.jpg', 'folder.png']:
                            continue
                        if f not in expected_files:
                            orphaned.append(f)

                    if orphaned:
                        self._append_sync_text(f"Found {len(orphaned)} orphaned files to delete\n")
                        for orphan in orphaned:
                            if not self.syncing:
                                break
                            full_path = os.path.join(local_music_dest, orphan)
                            try:
                                os.remove(full_path)
                                self._append_sync_text(f"  Deleted: {orphan}\n")
                                deleted_count += 1
                            except OSError as e:
                                self._append_sync_text(f"  Failed to delete {orphan}: {e}\n")

                        # Clean up empty directories
                        self._append_sync_text("\nCleaning up empty directories...\n")
                        for orphan in orphaned:
                            parent_dir = os.path.dirname(os.path.join(local_music_dest, orphan))
                            while parent_dir and parent_dir != local_music_dest:
                                try:
                                    # Remove cover art first
                                    for cover_name in ['cover.jpg', 'cover.png', 'folder.jpg']:
                                        cover_path = os.path.join(parent_dir, cover_name)
                                        if os.path.exists(cover_path):
                                            os.remove(cover_path)
                                    # Try to remove directory if empty
                                    os.rmdir(parent_dir)
                                    deleted_dirs += 1
                                    parent_dir = os.path.dirname(parent_dir)
                                except OSError:
                                    break
                    else:
                        self._append_sync_text("No orphaned files found\n")

                self._append_sync_text(f"\nCleanup complete: {deleted_count} files deleted, {deleted_dirs} empty directories removed\n")
                self._append_sync_text("=" * 50 + "\n\n")

            # Phase 3: Write playlists
            phase_num = "3" if (mirror_sync and sync_music) else ("2" if sync_music else "1")
            self._append_sync_text(f"PHASE {phase_num}: Writing playlists...\n\n")

            for i, playlist_name in enumerate(selected_playlists):
                if not self.syncing:
                    self._append_sync_text("\nSync cancelled by user.\n")
                    break

                self.root.after(0, lambda n=i+1, t=len(selected_playlists), p=playlist_name:
                                self.status_var.set(f"Writing playlist {n}/{t}: {p}"))

                playlist_info = self.playlist_data.get(playlist_name, {})
                track_ids = playlist_info.get('track_ids', [])

                # Create output filename
                output_filename = f"{playlist_name}.m3u"
                output_filename = re.sub(r'[<>:"/\\|?*]', '_', output_filename)

                if is_adb_mode:
                    # Write to temp file then push
                    with tempfile.NamedTemporaryFile(mode='w', suffix='.m3u', delete=False, encoding='utf-8-sig') as tmp:
                        tmp_path = tmp.name

                    # Use path mapping for playlist
                    mapping = path_mapping if sync_music else None
                    success, tracks_written = self._write_m3u8_playlist(playlist_name, track_ids, tmp_path, mapping)

                    if success:
                        remote_path = playlist_dest + '/' + output_filename
                        push_success, push_error = self._adb_push(tmp_path, remote_path)
                        if push_success:
                            playlist_success += 1
                            self._append_sync_text(f"✓ {playlist_name} ({tracks_written} tracks)\n")
                        else:
                            playlist_error += 1
                            self._append_sync_text(f"✗ {playlist_name} - Push failed: {push_error}\n")
                    else:
                        playlist_error += 1
                        self._append_sync_text(f"✗ {playlist_name} - Failed to write\n")

                    # Clean up temp file
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                else:
                    # Local mode
                    output_path = os.path.join(playlist_dest, output_filename)
                    mapping = path_mapping if sync_music else None
                    success, tracks_written = self._write_m3u8_playlist(playlist_name, track_ids, output_path, mapping)

                    if success:
                        playlist_success += 1
                        self._append_sync_text(f"✓ {playlist_name} ({tracks_written} tracks)\n")
                    else:
                        playlist_error += 1
                        self._append_sync_text(f"✗ {playlist_name} - Failed to write\n")

            # Final summary
            self._append_sync_text("\n" + "=" * 50 + "\n")
            self._append_sync_text(f"Sync complete!\n")
            self._append_sync_text(f"  Playlists written: {playlist_success}\n")
            if playlist_error > 0:
                self._append_sync_text(f"  Playlist errors: {playlist_error}\n")

            self.root.after(0, self._sync_complete)

        threading.Thread(target=sync_thread, daemon=True).start()

    def _start_sync_audiobooks(self) -> None:
        """Sync just Audiobooks — plain file copy, no conversion."""
        self._start_sync_extras([("Audiobooks", self.audiobooks_source_var.get().strip(),
                                   self.audiobooks_dest_var, self.adb_audiobooks_path_var)])

    def _start_sync_podcasts(self) -> None:
        """Sync just Downloaded Podcasts — plain file copy, no conversion."""
        self._start_sync_extras([("Podcasts", self.podcasts_source_var.get().strip(),
                                   self.podcasts_dest_var, self.adb_podcasts_path_var)])

    def _start_sync_extras(self, categories) -> None:
        """Sync the given (label, source_dir, local_dest_var, adb_path_var) categories."""
        for label, source_dir, _local_var, _adb_var in categories:
            if not source_dir or not os.path.isdir(source_dir):
                messagebox.showwarning("Invalid Source", f"{label} source folder does not exist:\n{source_dir}")
                return

        is_adb_mode = self.sync_target_var.get() == "adb"

        if is_adb_mode:
            ok, err_msg = self._resolve_adb_device()
            if not ok:
                messagebox.showerror("ADB Device", err_msg)
                return
            for entry in self._device_entries:
                if entry[0] == self.adb_device_id:
                    self.adb_device_combo_var.set(entry[2])
                    self.adb_device_status_var.set(f"Connected: {entry[2]}")
                    break
        else:
            for label, _source_dir, local_var, _adb_var in categories:
                if not local_var.get().strip():
                    messagebox.showwarning("No Destination", f"Please specify a destination folder for {label}.")
                    return
            for label, _source_dir, local_var, _adb_var in categories:
                try:
                    os.makedirs(local_var.get().strip(), exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"Cannot create destination folder for {label}:\n{e}")
                    return

        skip_existing = self.extras_skip_existing_var.get()
        mirror = self.extras_mirror_var.get()

        target_label = "Android device (ADB)" if is_adb_mode else "local folder"
        msg = f"Sync {', '.join(c[0] for c in categories)} to {target_label}?"
        if mirror:
            msg += "\n\nMirror sync: files removed from source will be deleted on the destination."
        if not messagebox.askyesno("Confirm Sync", msg):
            return

        self.syncing = True
        self._set_sync_buttons_state(tk.DISABLED, tk.NORMAL)

        self.sync_text.delete(1.0, tk.END)
        self.results_notebook.select(1)  # Switch to Sync Progress tab

        def sync_thread():
            for label, source_dir, local_var, adb_var in categories:
                if not self.syncing:
                    break

                self._append_sync_text(f"=== {label} ===\n")

                files = self._walk_relative_files(source_dir)
                total = len(files)
                self._append_sync_text(f"Found {total} file(s) in {source_dir}\n")

                dest_root = adb_var.get().strip().rstrip('/') if is_adb_mode else local_var.get().strip()
                self._append_sync_text(f"Destination: {dest_root}\n\n")

                expected = {rel.replace('\\', '/') for rel, _full in files}

                if is_adb_mode:
                    existing_set = set(self._adb_list_files(dest_root))
                else:
                    existing_set = set(self._local_list_files(dest_root))

                copied = 0
                pushed = 0
                skipped = 0
                errors = 0

                for i, (rel_path, full_path) in enumerate(files):
                    if not self.syncing:
                        self._append_sync_text("\nSync cancelled by user.\n")
                        break

                    self.root.after(0, lambda n=i + 1, t=total, lbl=label:
                                     self.status_var.set(f"Syncing {lbl} {n}/{t}"))

                    rel_key = rel_path.replace('\\', '/').lstrip('/')

                    if skip_existing and rel_key in existing_set:
                        skipped += 1
                        continue

                    if is_adb_mode:
                        dest_path = dest_root + '/' + rel_key
                        success, error = self._adb_push(full_path, dest_path)
                        if success:
                            pushed += 1
                        else:
                            self._append_sync_text(f"✗ {rel_path}: {error}\n")
                            errors += 1
                    else:
                        dest_path = os.path.join(dest_root, rel_path)
                        if self._copy_file(full_path, dest_path):
                            copied += 1
                        else:
                            self._append_sync_text(f"✗ Failed to copy: {rel_path}\n")
                            errors += 1

                self._append_sync_text(f"\n{label} sync: ")
                if is_adb_mode:
                    self._append_sync_text(f"pushed {pushed}, ")
                else:
                    self._append_sync_text(f"copied {copied}, ")
                self._append_sync_text(f"skipped {skipped}, errors {errors}\n")

                if mirror and self.syncing:
                    self._append_sync_text(f"\nCleaning up orphaned {label.lower()} files...\n")
                    deleted = 0
                    orphaned = [f for f in existing_set if f not in expected]
                    for orphan in orphaned:
                        if not self.syncing:
                            break
                        if is_adb_mode:
                            if self._adb_delete(dest_root + '/' + orphan):
                                deleted += 1
                        else:
                            try:
                                os.remove(os.path.join(dest_root, orphan))
                                deleted += 1
                            except OSError:
                                pass
                    self._append_sync_text(f"Deleted {deleted} orphaned file(s)\n")

                self._append_sync_text("\n" + "=" * 50 + "\n\n")

            self._append_sync_text("Sync complete!\n")
            self.root.after(0, lambda: self._sync_complete("Audiobooks/Podcasts sync complete!"))

        threading.Thread(target=sync_thread, daemon=True).start()

    def _append_sync_text(self, text: str) -> None:
        """Append text to sync output (thread-safe)."""
        self.root.after(0, lambda: self._do_append_sync_text(text))

    def _do_append_sync_text(self, text: str) -> None:
        """Actually append text to sync output."""
        self.sync_text.insert(tk.END, text)
        self.sync_text.see(tk.END)
        self.root.update_idletasks()

    def _set_sync_buttons_state(self, start_state: str, cancel_state: str) -> None:
        """Enable/disable every Sync/Cancel button together (only one sync runs at a time)."""
        self.sync_btn.config(state=start_state)
        self.sync_playlists_btn.config(state=start_state)
        self.sync_audiobooks_btn.config(state=start_state)
        self.sync_podcasts_btn.config(state=start_state)
        self.sync_audiobooks_adb_btn.config(state=start_state)
        self.sync_podcasts_adb_btn.config(state=start_state)
        self.cancel_btn.config(state=cancel_state)
        self.extras_cancel_btn.config(state=cancel_state)

    def _sync_complete(self, message: str = "Playlists have been synced to PowerAmp format!") -> None:
        """Handle sync completion."""
        self.syncing = False
        self._set_sync_buttons_state(tk.NORMAL, tk.DISABLED)
        self.status_var.set("Sync complete")

        messagebox.showinfo("Sync Complete", message)

    def _cancel_sync(self) -> None:
        """Cancel the current sync operation."""
        self.syncing = False
        self.status_var.set("Cancelling...")

    def _on_closing(self) -> None:
        """Handle window close event."""
        if self.syncing:
            if not messagebox.askyesno("Quit", "A sync is in progress. Are you sure you want to quit?"):
                return
            self.syncing = False

        self.root.destroy()


# ============================================================================
# MAIN ENTRY POINT
# ============================================================================

def main():
    """Main application entry point."""
    setup_shared_logging("poweramp_sync")

    root = tk.Tk()
    apply_category_icon(root)
    app = PowerAmpSyncApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
