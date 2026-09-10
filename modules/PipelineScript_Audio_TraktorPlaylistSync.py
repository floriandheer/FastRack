#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_TraktorPlaylistSync.py
Description: Export selected Traktor playlists/smart lists from this machine's
             collection.nml into a portable NML fragment, with track locations
             remapped to another machine's library layout, ready to bring over
             and merge in via Traktor's own Preferences > File Management >
             Import Collection.

Unlike PipelineScript_Audio_TraktorSync.py (which builds a Traktor DJ library
FROM a MusicBee/iTunes export), this tool works the other direction: it reads
crates/smart lists that already exist natively inside Traktor and packages
them for the *other* computer, assuming the actual audio files already live
in a shared/synced location reachable from both machines (NAS, cloud folder,
etc.) - so no file copying or format conversion happens here, only playlist
data and path remapping.
"""

import os
import re
import sys
import copy
import socket
import argparse
import datetime
import xml.etree.ElementTree as ET
from collections import Counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Any, Tuple

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging

logger = get_logger("traktor_playlist_sync")

APP_NAME = "Traktor Playlist Sync"
APP_VERSION = "1.0.0"
HEADER_COLOR = "#2c3e50"

def _appdata_dir() -> str:
    """Platform-appropriate PipelineManager app-data folder (same
    convention as rak_settings._get_appdata_path) - this file's config
    holds per-machine profiles, so it has to land in the right place on
    both Windows and macOS rather than the Windows-only path this used to
    be, which just silently created a stray ~/AppData folder on Mac."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        return os.path.join(home, "AppData", "Local", "PipelineManager")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "PipelineManager")
    windows_appdata = "/mnt/c/Users"
    if os.path.exists(windows_appdata):
        user_path = os.path.join(windows_appdata, os.environ.get("USER", ""))
        if os.path.exists(user_path):
            return os.path.join(user_path, "AppData", "Local", "PipelineManager")
    return os.path.join(home, ".local", "share", "PipelineManager")


APP_DATA_DIR = _appdata_dir()
CONFIG_FILE = os.path.join(APP_DATA_DIR, "traktor_playlist_sync_config.json")

# Built-in preset that mirrors the Auto Select rule (digits 1-9 prefix).
DEFAULT_PRESET_NAME = "Default (Auto)"


# ============================================================================
# NML DATA MODEL
# ============================================================================

@dataclass
class MachineProfile:
    """One machine's view of the shared DJ library, in Traktor's own path tokens.

    `volume` and `dir_prefix` are copied verbatim from a real LOCATION element
    (VOLUME + DIR) in that machine's collection.nml - never hand-derived from
    an OS path - because Traktor's VOLUME/DIR encoding differs by platform
    (Windows drive letter vs. Mac volume name) in ways that aren't worth
    reverse-engineering when we can just read an example straight from Traktor.
    """
    name: str = ""
    volume: str = ""
    dir_prefix: str = ""

    def to_dict(self) -> Dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> 'MachineProfile':
        return cls(name=d.get("name", ""), volume=d.get("volume", ""), dir_prefix=d.get("dir_prefix", ""))

    @property
    def is_configured(self) -> bool:
        return bool(self.volume and self.dir_prefix)

    @property
    def root_key(self) -> str:
        return f"{self.volume}{self.dir_prefix}"


@dataclass
class SyncSettings:
    """Sync configuration settings."""
    collection_nml_path: str = ""
    machine_profiles: Dict[str, Dict[str, str]] = field(default_factory=dict)
    this_machine_profile: str = ""
    export_target_profile: str = ""
    export_output_dir: str = ""
    selected_playlists: List[str] = field(default_factory=list)
    selection_mode: str = "include"
    playlist_presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    active_preset: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'SyncSettings':
        valid_fields = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid_fields)


class ConfigManager:
    """Manages persistent configuration settings."""

    def __init__(self, config_path: str = CONFIG_FILE):
        self.config_path = config_path
        self.settings = self._load_settings()

    def _load_settings(self) -> SyncSettings:
        if os.path.exists(self.config_path):
            try:
                import json
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    logger.info(f"Loaded settings from {self.config_path}")
                    return SyncSettings.from_dict(data)
            except Exception as e:
                logger.warning(f"Failed to load settings: {e}, using defaults")
        return SyncSettings()

    def save_settings(self) -> bool:
        try:
            import json
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings.to_dict(), f, indent=2)
            logger.info(f"Settings saved to {self.config_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save settings: {e}")
            return False

    def update_settings(self, **kwargs) -> None:
        for key, value in kwargs.items():
            if hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()


# ============================================================================
# NML PARSING / REWRITING - pure functions, no Tk dependency
# ============================================================================

class PlaylistNode:
    """A PLAYLIST or SMARTLIST leaf found while walking the PLAYLISTS tree."""

    __slots__ = ("path", "name", "kind", "element")

    def __init__(self, path: Tuple[str, ...], name: str, kind: str, element: ET.Element):
        self.path = path       # ancestor folder names, excluding $ROOT
        self.name = name
        self.kind = kind       # "PLAYLIST" or "SMARTLIST"
        self.element = element

    @property
    def display_name(self) -> str:
        return "/".join(self.path + (self.name,)) if self.path else self.name


@dataclass
class ExportStats:
    playlists_exported: int = 0
    smartlists_exported: int = 0
    tracks_exported: int = 0
    tracks_skipped_outside_library: int = 0
    tracks_missing_from_collection: int = 0


def load_nml(path: str) -> ET.ElementTree:
    return ET.parse(path)


def get_collection_element(root: ET.Element) -> Optional[ET.Element]:
    return root.find("COLLECTION")


def get_playlists_root_node(root: ET.Element) -> Optional[ET.Element]:
    """Return the $ROOT <NODE TYPE="FOLDER"> element inside <PLAYLISTS>."""
    playlists = root.find("PLAYLISTS")
    if playlists is None:
        return None
    return playlists.find("NODE")


def entry_key(entry: ET.Element) -> Optional[str]:
    """The same VOLUME+DIR+FILE string Traktor uses to match a playlist
    PRIMARYKEY against a COLLECTION entry."""
    loc = entry.find("LOCATION")
    if loc is None:
        return None
    return f"{loc.get('VOLUME', '')}{loc.get('DIR', '')}{loc.get('FILE', '')}"


def build_entry_index(collection: ET.Element) -> Dict[str, ET.Element]:
    index: Dict[str, ET.Element] = {}
    for entry in collection.findall("ENTRY"):
        key = entry_key(entry)
        if key:
            index[key] = entry
    return index


def walk_playlist_nodes(folder_node: ET.Element, path: Tuple[str, ...] = ()) -> List[PlaylistNode]:
    """Recursively collect PLAYLIST/SMARTLIST leaves under a FOLDER node."""
    results: List[PlaylistNode] = []
    subnodes = folder_node.find("SUBNODES")
    if subnodes is None:
        return results
    for node in subnodes.findall("NODE"):
        node_type = node.get("TYPE", "")
        name = node.get("NAME", "")
        if node_type == "FOLDER":
            results.extend(walk_playlist_nodes(node, path + (name,)))
        elif node_type in ("PLAYLIST", "SMARTLIST"):
            results.append(PlaylistNode(path, name, node_type, node))
        # Silently ignore unknown node types from future Traktor versions.
    return results


def playlist_track_keys(node: PlaylistNode) -> List[str]:
    """Ordered PRIMARYKEY KEY strings for a PLAYLIST node. Empty for SMARTLIST
    (smart lists are a live query, not a stored track list)."""
    if node.kind != "PLAYLIST":
        return []
    playlist_el = node.element.find("PLAYLIST")
    if playlist_el is None:
        return []
    keys = []
    for entry_el in playlist_el.findall("ENTRY"):
        pk = entry_el.find("PRIMARYKEY")
        if pk is not None and pk.get("KEY"):
            keys.append(pk.get("KEY"))
    return keys


def playlist_track_count(node: PlaylistNode) -> Optional[int]:
    """None for SMARTLIST - its size depends on what matches the query."""
    if node.kind == "SMARTLIST":
        return None
    playlist_el = node.element.find("PLAYLIST")
    if playlist_el is None:
        return 0
    try:
        return int(playlist_el.get("ENTRIES", "0"))
    except ValueError:
        return len(playlist_el.findall("ENTRY"))


def rewrite_key_string(key: str, src: MachineProfile, dst: MachineProfile) -> Optional[str]:
    """Rewrite a Traktor path key from src's library root to dst's.

    Returns None if `key` doesn't fall under src's registered root (e.g. a
    track that lives outside the shared DJ library folder) - such tracks
    can't be safely relocated for the other machine.
    """
    prefix = src.root_key
    if not prefix or not key.startswith(prefix):
        return None
    remainder = key[len(prefix):]
    return f"{dst.root_key}{remainder}"


def rewrite_entry_location(entry: ET.Element, src: MachineProfile, dst: MachineProfile) -> bool:
    """Mutate a (deep-copied) ENTRY's LOCATION VOLUME/DIR in place."""
    loc = entry.find("LOCATION")
    if loc is None:
        return False
    volume = loc.get("VOLUME", "")
    dir_ = loc.get("DIR", "")
    if volume != src.volume or not dir_.startswith(src.dir_prefix):
        return False
    remainder = dir_[len(src.dir_prefix):]
    loc.set("VOLUME", dst.volume)
    loc.set("DIR", f"{dst.dir_prefix}{remainder}")
    return True


def detect_profile_from_sample(entry_index: Dict[str, ET.Element], sample_filename: str) -> Optional[Tuple[str, str]]:
    """Find a COLLECTION entry whose FILE matches `sample_filename` (basename
    match) and return its (VOLUME, DIR) - i.e. the library root as Traktor
    encodes it on that machine, provided the sample track sits directly at
    the top of the shared library folder rather than in a subfolder."""
    target = os.path.basename(sample_filename)
    for entry in entry_index.values():
        loc = entry.find("LOCATION")
        if loc is not None and loc.get("FILE") == target:
            return loc.get("VOLUME", ""), loc.get("DIR", "")
    return None


def detect_profile_from_nml_file(nml_path: str, sample_filename: str) -> Optional[Tuple[str, str]]:
    """Same as detect_profile_from_sample, but against a standalone NML file
    (e.g. a copy of the other machine's collection.nml carried over once)."""
    tree = load_nml(nml_path)
    root = tree.getroot()
    collection = get_collection_element(root)
    if collection is None:
        return None
    index = build_entry_index(collection)
    return detect_profile_from_sample(index, sample_filename)


def detect_library_root(entry_index: Dict[str, ET.Element], min_coverage: float = 0.5) -> Optional[Tuple[str, str]]:
    """Infer a machine's shared-library root straight from its loaded
    collection - no sample file to pick. VOLUME is whichever volume holds
    the most tracks (the shared library disk, as opposed to a stray
    external drive). DIR is the deepest "/:folder/:.../:" prefix that
    still covers at least `min_coverage` of that volume's tracks, rather
    than one every single track must share - a real collection almost
    always has a handful of outliers on the same volume (a Traktor
    recording, one stray download) that would otherwise drag a strict
    "all tracks" common prefix all the way back up to the volume root and
    make it useless. Weighting by how many tracks live under each prefix
    (not just counting distinct folders) also means a folder holding
    thousands of tracks correctly outweighs a handful of one-off
    subfolders when picking how deep to go."""
    dir_counts_by_volume: Dict[str, Counter] = {}
    totals_by_volume: Counter = Counter()
    for entry in entry_index.values():
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        volume, dir_ = loc.get("VOLUME", ""), loc.get("DIR", "")
        if not volume or not dir_:
            continue
        dir_counts_by_volume.setdefault(volume, Counter())[dir_] += 1
        totals_by_volume[volume] += 1
    if not totals_by_volume:
        return None

    volume = totals_by_volume.most_common(1)[0][0]
    total = totals_by_volume[volume]

    prefix_counts: Counter = Counter()
    for dir_, count in dir_counts_by_volume[volume].items():
        prefix = ""
        for segment in dir_.split("/:"):
            if not segment:
                continue
            prefix += f"/:{segment}"
            prefix_counts[prefix + "/:"] += count

    candidates = [p for p, c in prefix_counts.items() if c / total >= min_coverage]
    if not candidates:
        return None
    return volume, max(candidates, key=len)


def _build_folder_tree(nodes: List[PlaylistNode]) -> Dict[str, Any]:
    """{"folders": {name: subtree}, "leaves": [PlaylistNode, ...]} rooted at $ROOT."""
    root: Dict[str, Any] = {"folders": {}, "leaves": []}
    for node in nodes:
        cursor = root
        for part in node.path:
            cursor = cursor["folders"].setdefault(part, {"folders": {}, "leaves": []})
        cursor["leaves"].append(node)
    return root


def _emit_playlist_leaf(node: PlaylistNode, entry_index: Dict[str, ET.Element],
                         needed_entries: Dict[str, str], src: MachineProfile,
                         dst: MachineProfile, stats: ExportStats) -> ET.Element:
    """Deep-copy one playlist/smart-list NODE with paths rewritten for dst."""
    new_node = copy.deepcopy(node.element)

    if node.kind == "SMARTLIST":
        # A smart list is just a saved query (e.g. over COMMENT tags) - no
        # track references to rewrite, so it works unchanged on the other
        # machine as long as the matching tracks exist there too.
        stats.smartlists_exported += 1
        return new_node

    playlist_el = new_node.find("PLAYLIST")
    surviving = 0
    if playlist_el is not None:
        for entry_el in list(playlist_el.findall("ENTRY")):
            pk = entry_el.find("PRIMARYKEY")
            original_key = pk.get("KEY") if pk is not None else None
            if not original_key:
                playlist_el.remove(entry_el)
                continue
            if original_key not in entry_index:
                playlist_el.remove(entry_el)
                stats.tracks_missing_from_collection += 1
                continue
            new_key = rewrite_key_string(original_key, src, dst)
            if new_key is None:
                playlist_el.remove(entry_el)
                stats.tracks_skipped_outside_library += 1
                continue
            pk.set("KEY", new_key)
            needed_entries[original_key] = new_key
            surviving += 1
        playlist_el.set("ENTRIES", str(surviving))

    stats.playlists_exported += 1
    stats.tracks_exported += surviving
    return new_node


def _emit_folder(name: str, tree: Dict[str, Any], entry_index: Dict[str, ET.Element],
                  needed_entries: Dict[str, str], src: MachineProfile,
                  dst: MachineProfile, stats: ExportStats) -> ET.Element:
    folder_el = ET.Element("NODE", {"TYPE": "FOLDER", "NAME": name})
    subnodes_el = ET.SubElement(folder_el, "SUBNODES")
    count = 0
    for sub_name, sub_tree in tree["folders"].items():
        subnodes_el.append(_emit_folder(sub_name, sub_tree, entry_index, needed_entries, src, dst, stats))
        count += 1
    for leaf in tree["leaves"]:
        subnodes_el.append(_emit_playlist_leaf(leaf, entry_index, needed_entries, src, dst, stats))
        count += 1
    subnodes_el.set("COUNT", str(count))
    return folder_el


def build_export_root(source_root: ET.Element, selected_nodes: List[PlaylistNode],
                       entry_index: Dict[str, ET.Element], src: MachineProfile,
                       dst: MachineProfile) -> Tuple[ET.Element, ExportStats]:
    """Build a standalone <NML> element containing only the selected
    playlists/smart lists and the COLLECTION entries they reference, with
    every LOCATION and PRIMARYKEY remapped from src's library root to dst's."""
    stats = ExportStats()
    needed_entries: Dict[str, str] = {}  # original_key -> new_key (insertion-ordered)

    tree = _build_folder_tree(selected_nodes)
    root_folder_el = _emit_folder("$ROOT", tree, entry_index, needed_entries, src, dst, stats)

    new_root = ET.Element("NML", {"VERSION": source_root.get("VERSION", "19")})

    head = source_root.find("HEAD")
    if head is not None:
        new_root.append(copy.deepcopy(head))
    else:
        ET.SubElement(new_root, "HEAD", {"COMPANY": "www.native-instruments.com", "PROGRAM": "Traktor"})

    collection_el = ET.Element("COLLECTION", {"ENTRIES": str(len(needed_entries))})
    for original_key in needed_entries:
        entry_copy = copy.deepcopy(entry_index[original_key])
        rewrite_entry_location(entry_copy, src, dst)
        collection_el.append(entry_copy)
    new_root.append(collection_el)

    playlists_el = ET.Element("PLAYLISTS")
    playlists_el.append(root_folder_el)
    new_root.append(playlists_el)

    return new_root, stats


def write_nml(root_element: ET.Element, output_path: str) -> None:
    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n')
        f.write(ET.tostring(root_element, encoding="utf-8"))


# ============================================================================
# SMALL REUSABLE DIALOG: filter + pick one string from a list
# ============================================================================

def pick_from_list(parent, title: str, items: List[str]) -> Optional[str]:
    """Modal filterable picker. Returns the chosen string, or None if cancelled."""
    result: Dict[str, Optional[str]] = {"value": None}

    win = tk.Toplevel(parent)
    win.title(title)
    win.geometry("420x420")
    win.transient(parent)
    win.grab_set()

    ttk.Label(win, text="Filter:").pack(anchor="w", padx=10, pady=(10, 0))
    filter_var = tk.StringVar()
    ttk.Entry(win, textvariable=filter_var).pack(fill=tk.X, padx=10, pady=(0, 5))

    list_frame = ttk.Frame(win)
    list_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
    listbox = tk.Listbox(list_frame, activestyle="dotbox")
    scroll = ttk.Scrollbar(list_frame, orient="vertical", command=listbox.yview)
    listbox.config(yscrollcommand=scroll.set)
    listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    scroll.pack(side=tk.RIGHT, fill=tk.Y)

    sorted_items = sorted(items, key=str.lower)

    def refresh(*_args):
        listbox.delete(0, tk.END)
        needle = filter_var.get().lower()
        for item in sorted_items:
            if not needle or needle in item.lower():
                listbox.insert(tk.END, item)

    filter_var.trace_add('write', refresh)
    refresh()

    def confirm(*_args):
        selection = listbox.curselection()
        if selection:
            result["value"] = listbox.get(selection[0])
        win.destroy()

    listbox.bind("<Double-Button-1>", confirm)

    btn_frame = ttk.Frame(win)
    btn_frame.pack(fill=tk.X, padx=10, pady=10)
    ttk.Button(btn_frame, text="Cancel", command=win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
    ttk.Button(btn_frame, text="Select", command=confirm).pack(side=tk.RIGHT)

    win.wait_window()
    return result["value"]


# ============================================================================
# MACHINE PROFILE EDITOR DIALOG
# ============================================================================

class ProfileEditorDialog:
    """Create/edit one MachineProfile. Detection helpers avoid the user ever
    having to hand-type Traktor's internal "/:" path token syntax."""

    def __init__(self, parent, app: 'TraktorPlaylistSyncUI', profile: Optional[MachineProfile] = None,
                 existing_names: Optional[List[str]] = None):
        self.app = app
        self.result: Optional[MachineProfile] = None
        self._existing_names = set(existing_names or [])
        original_name = profile.name if profile else None
        if original_name in self._existing_names:
            self._existing_names.discard(original_name)

        self.win = tk.Toplevel(parent)
        self.win.title("Machine Profile")
        self.win.geometry("520x260")
        self.win.transient(parent)
        self.win.grab_set()

        form = ttk.Frame(self.win)
        form.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar(value=profile.name if profile else "")
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Volume:").grid(row=1, column=0, sticky="w", pady=5)
        self.volume_var = tk.StringVar(value=profile.volume if profile else "")
        ttk.Entry(form, textvariable=self.volume_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Dir prefix:").grid(row=2, column=0, sticky="w", pady=5)
        default_dir = profile.dir_prefix if profile else self.app.get_this_machine_dir_default()
        self.dir_var = tk.StringVar(value=default_dir)
        ttk.Entry(form, textvariable=self.dir_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(
            form,
            text="e.g. Volume \"C:\" / \"Macintosh HD\", Dir prefix \"/:Users/:flori/:Music/:DJ Library/:\"",
            foreground="gray", font=("Arial", 8), wraplength=470, justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))

        detect_frame = ttk.LabelFrame(form, text="Detect from a track file (no need to type the syntax by hand)")
        detect_frame.grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)
        detect_frame.columnconfigure(0, weight=1)
        detect_frame.columnconfigure(1, weight=1)

        ttk.Button(
            detect_frame, text="From this machine's loaded collection...",
            command=self._detect_from_local,
        ).grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        ttk.Button(
            detect_frame, text="From another collection.nml file...",
            command=self._detect_from_reference,
        ).grid(row=0, column=1, sticky="ew", padx=5, pady=5)

        btn_frame = ttk.Frame(self.win)
        btn_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        ttk.Button(btn_frame, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT)

        self.win.wait_window()

    def _detect_from_local(self):
        if not self.app.entry_index:
            messagebox.showinfo("Detect", "Load a collection.nml first.", parent=self.win)
            return
        found = detect_library_root(self.app.entry_index)
        if found:
            self.volume_var.set(found[0])
            self.dir_var.set(found[1])
            return

        # No common folder could be determined automatically (e.g. an
        # oddly scattered collection) - fall back to picking one sample
        # track known to sit directly in the shared library folder.
        filename = filedialog.askopenfilename(title="Pick a track that sits directly in the shared DJ library folder")
        if not filename:
            return
        found = detect_profile_from_sample(self.app.entry_index, filename)
        if not found:
            messagebox.showwarning(
                "Detect", f"No entry named '{os.path.basename(filename)}' found in the loaded collection.",
                parent=self.win,
            )
            return
        self.volume_var.set(found[0])
        self.dir_var.set(found[1])

    def _detect_from_reference(self):
        nml_path = filedialog.askopenfilename(
            title="Select the other machine's collection.nml (a copy is fine)",
            filetypes=[("Traktor Collection", "*.nml"), ("All Files", "*.*")],
        )
        if not nml_path:
            return

        try:
            tree = load_nml(nml_path)
            collection = get_collection_element(tree.getroot())
            index = build_entry_index(collection) if collection is not None else {}
            found = detect_library_root(index)
        except Exception as e:
            messagebox.showerror("Detect", f"Could not read that NML file:\n{e}", parent=self.win)
            return

        if not found:
            # Couldn't find a common folder in that file either - fall
            # back to matching one filename shared between both machines.
            candidates = sorted({
                entry.find("LOCATION").get("FILE", "")
                for entry in self.app.entry_index.values()
                if entry.find("LOCATION") is not None and entry.find("LOCATION").get("FILE")
            })
            filename = pick_from_list(
                self.win, "Pick a filename you know exists on that machine too (ideally in the shared library root)",
                candidates,
            )
            if not filename:
                return
            found = detect_profile_from_nml_file(nml_path, filename)
            if not found:
                messagebox.showwarning("Detect", f"No entry named '{filename}' found in that file.", parent=self.win)
                return

        self.volume_var.set(found[0])
        self.dir_var.set(found[1])

    def _save(self):
        name = self.name_var.get().strip()
        volume = self.volume_var.get().strip()
        dir_prefix = self.dir_var.get().strip()

        if not name:
            messagebox.showerror("Machine Profile", "Name is required.", parent=self.win)
            return
        if name in self._existing_names:
            messagebox.showerror("Machine Profile", f"A profile named '{name}' already exists.", parent=self.win)
            return
        if not volume or not dir_prefix:
            messagebox.showerror("Machine Profile", "Volume and Dir prefix are both required.", parent=self.win)
            return
        if not dir_prefix.startswith("/:") or not dir_prefix.endswith("/:"):
            if not messagebox.askyesno(
                "Machine Profile",
                "Dir prefix doesn't look like Traktor's \"/:folder/:\" syntax - save anyway?",
                parent=self.win,
            ):
                return

        self.result = MachineProfile(name=name, volume=volume, dir_prefix=dir_prefix)
        self.win.destroy()


# ============================================================================
# MAIN UI
# ============================================================================

class TraktorPlaylistSyncUI:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("900x1050")
        self.root.minsize(900, 750)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self.config_manager = ConfigManager()

        # NML state
        self.nml_tree: Optional[ET.ElementTree] = None
        self.collection_element: Optional[ET.Element] = None
        self.entry_index: Dict[str, ET.Element] = {}
        self.all_nodes: List[PlaylistNode] = []
        self._loaded_nml_path: Optional[str] = None
        self._loaded_nml_mtime: Optional[float] = None
        self._nml_autoload_after_id = None

        self._create_header()
        self._create_config_panel()
        self._create_results_panel()

        self.status_var = tk.StringVar(value="Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.grid(row=2, column=0, sticky="ew")

        self._initialize_default_paths()

        self.root.bind("<FocusIn>", self._on_window_focus, add="+")

    # ------------------------------------------------------------------
    # Header / layout scaffolding
    # ------------------------------------------------------------------

    def _create_header(self):
        header = tk.Frame(self.root, bg=HEADER_COLOR, height=60)
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        tk.Label(header, text=APP_NAME, font=("Arial", 16, "bold"), fg="white", bg=HEADER_COLOR).place(
            relx=0.5, rely=0.5, anchor=tk.CENTER
        )

    def _create_config_panel(self):
        main = ttk.Frame(self.root)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=1)
        self._main = main

        # --- Source ---
        source_frame = ttk.LabelFrame(main, text="Source: this machine's Traktor collection")
        source_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        source_frame.columnconfigure(1, weight=1)

        ttk.Label(source_frame, text="collection.nml:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.nml_path_var = tk.StringVar()
        self.nml_path_var.trace_add('write', lambda *a: self._schedule_autoload())
        ttk.Entry(source_frame, textvariable=self.nml_path_var, width=55).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(source_frame, text="Browse", command=self._browse_nml).grid(row=0, column=2, padx=5, pady=10)
        ttk.Button(source_frame, text="Reload", command=lambda: self._load_nml(silent=False)).grid(row=0, column=3, padx=(0, 10), pady=10)

        self.nml_info_var = tk.StringVar(value="No collection loaded")
        ttk.Label(source_frame, textvariable=self.nml_info_var, foreground="gray").grid(
            row=1, column=0, columnspan=4, sticky="w", padx=10, pady=(0, 10)
        )

        # --- Machine profiles ---
        profiles_frame = ttk.LabelFrame(main, text="Machine Profiles (shared DJ library root, per machine)")
        profiles_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        profiles_frame.columnconfigure(1, weight=1)

        ttk.Label(profiles_frame, text="This machine:").grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self.this_machine_var = tk.StringVar()
        self.this_machine_combo = ttk.Combobox(profiles_frame, textvariable=self.this_machine_var, state="readonly")
        self.this_machine_combo.grid(row=0, column=1, sticky="ew", padx=5, pady=8)
        self.this_machine_combo.bind("<<ComboboxSelected>>", lambda e: self._on_profiles_changed())

        ttk.Label(profiles_frame, text="Export for:").grid(row=1, column=0, sticky="w", padx=10, pady=8)
        self.target_machine_var = tk.StringVar()
        self.target_machine_combo = ttk.Combobox(profiles_frame, textvariable=self.target_machine_var, state="readonly")
        self.target_machine_combo.grid(row=1, column=1, sticky="ew", padx=5, pady=8)
        self.target_machine_combo.bind("<<ComboboxSelected>>", lambda e: self._on_profiles_changed())

        profile_btns = ttk.Frame(profiles_frame)
        profile_btns.grid(row=0, column=2, rowspan=2, sticky="ns", padx=10)
        ttk.Button(profile_btns, text="New...", command=self._new_profile, width=10).pack(pady=2)
        ttk.Button(profile_btns, text="Edit...", command=self._edit_profile, width=10).pack(pady=2)
        ttk.Button(profile_btns, text="Delete", command=self._delete_profile, width=10).pack(pady=2)

        self.profiles_summary_var = tk.StringVar(value="No profiles configured yet.")
        ttk.Label(profiles_frame, textvariable=self.profiles_summary_var, foreground="gray", font=("Arial", 8),
                  wraplength=650, justify="left").grid(row=2, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 8))

        # --- Output ---
        output_frame = ttk.LabelFrame(main, text="Output")
        output_frame.grid(row=2, column=0, sticky="ew", padx=5, pady=5)
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Save folder:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.output_dir_var = tk.StringVar()
        ttk.Entry(output_frame, textvariable=self.output_dir_var, width=55).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(output_frame, text="Browse", command=self._browse_output_dir).grid(row=0, column=2, padx=5, pady=10)

        # --- Playlist selection ---
        playlist_frame = ttk.LabelFrame(main, text="Playlist / Smart List Selection")
        playlist_frame.grid(row=3, column=0, sticky="nsew", padx=5, pady=5)
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(2, weight=1)
        main.rowconfigure(3, weight=1)

        mode_frame = ttk.Frame(playlist_frame)
        mode_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        ttk.Label(mode_frame, text="Selection Mode:").grid(row=0, column=0, sticky="w", padx=5)
        self.selection_mode = tk.StringVar(value="include")
        ttk.Radiobutton(mode_frame, text="Include Selected", variable=self.selection_mode, value="include",
                        command=self._update_selection_summary).grid(row=0, column=1, padx=10)
        ttk.Radiobutton(mode_frame, text="Exclude Selected", variable=self.selection_mode, value="exclude",
                        command=self._update_selection_summary).grid(row=0, column=2, padx=10)

        filter_frame = ttk.Frame(playlist_frame)
        filter_frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        filter_frame.columnconfigure(1, weight=1)
        ttk.Label(filter_frame, text="Filter:").grid(row=0, column=0, sticky="w", padx=5)
        self.filter_var = tk.StringVar()
        self.filter_var.trace_add('write', self._filter_playlists)
        ttk.Entry(filter_frame, textvariable=self.filter_var).grid(row=0, column=1, sticky="ew", padx=5)
        self.selection_summary = tk.StringVar(value="No playlists loaded")
        ttk.Label(filter_frame, textvariable=self.selection_summary, font=("Arial", 9), foreground="blue").grid(
            row=0, column=2, padx=10, sticky="e"
        )

        list_frame = ttk.Frame(playlist_frame)
        list_frame.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.playlist_tree = ttk.Treeview(
            list_frame, columns=("type", "tracks", "in_library"), show="tree headings", height=10
        )
        self.playlist_tree.grid(row=0, column=0, sticky="nsew")
        self.playlist_tree.heading("#0", text="Playlist")
        self.playlist_tree.heading("type", text="Type")
        self.playlist_tree.heading("tracks", text="Tracks")
        self.playlist_tree.heading("in_library", text="In Shared Library")
        self.playlist_tree.column("#0", width=320)
        self.playlist_tree.column("type", width=90, anchor="center")
        self.playlist_tree.column("tracks", width=70, anchor="center")
        self.playlist_tree.column("in_library", width=130, anchor="center")
        self.playlist_tree.bind('<<TreeviewSelect>>', lambda e: self._update_selection_summary())

        tree_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.playlist_tree.config(yscrollcommand=tree_scroll.set)

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

        btn_frame = ttk.Frame(playlist_frame)
        btn_frame.grid(row=4, column=0, sticky="ew", pady=5)
        ttk.Button(btn_frame, text="Select All", command=self._select_all).grid(row=0, column=0, padx=5)
        ttk.Button(btn_frame, text="Clear All", command=self._clear_all).grid(row=0, column=1, padx=5)
        ttk.Button(btn_frame, text="Auto Select", command=self._auto_select).grid(row=0, column=2, padx=5)

        # --- Actions ---
        action_frame = ttk.Frame(main)
        action_frame.grid(row=4, column=0, sticky="ew", pady=10)
        action_frame.columnconfigure(1, weight=1)

        ttk.Button(action_frame, text="Save Settings", command=self._save_settings, width=15).grid(row=0, column=0, padx=10)

        right_btns = ttk.Frame(action_frame)
        right_btns.grid(row=0, column=1, sticky="e", padx=10)
        self.export_btn = tk.Button(right_btns, text="Export NML", command=self._export, width=15,
                                     bg="green", fg="white", font=('', 9, 'bold'))
        self.export_btn.pack(side=tk.LEFT)

    def _create_results_panel(self):
        results_frame = ttk.LabelFrame(self._main, text="Results")
        results_frame.grid(row=5, column=0, sticky="nsew", padx=5, pady=5)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)
        self._main.rowconfigure(5, weight=1)

        notebook = ttk.Notebook(results_frame)
        notebook.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        info_tab = ttk.Frame(notebook)
        notebook.add(info_tab, text="Library Info")
        info_tab.columnconfigure(0, weight=1)
        info_tab.rowconfigure(0, weight=1)
        self.info_text = tk.Text(info_tab, wrap=tk.WORD, height=6, font=("Consolas", 9))
        self.info_text.grid(row=0, column=0, sticky="nsew")
        info_scroll = ttk.Scrollbar(info_tab, command=self.info_text.yview)
        info_scroll.grid(row=0, column=1, sticky="ns")
        self.info_text.config(yscrollcommand=info_scroll.set)

        log_tab = ttk.Frame(notebook)
        notebook.add(log_tab, text="Export Log")
        log_tab.columnconfigure(0, weight=1)
        log_tab.rowconfigure(0, weight=1)
        self.log_text = tk.Text(log_tab, wrap=tk.WORD, height=6, font=("Consolas", 9))
        self.log_text.grid(row=0, column=0, sticky="nsew")
        log_scroll = ttk.Scrollbar(log_tab, command=self.log_text.yview)
        log_scroll.grid(row=0, column=1, sticky="ns")
        self.log_text.config(yscrollcommand=log_scroll.set)

    def _log(self, message: str):
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)

    # ------------------------------------------------------------------
    # Settings load/save
    # ------------------------------------------------------------------

    def _initialize_default_paths(self):
        settings = self.config_manager.settings

        default_candidates = self._default_nml_candidates()
        newest_default = next((c for c in default_candidates if os.path.exists(c)), None)

        saved = settings.collection_nml_path
        if saved and os.path.exists(saved):
            # A saved path from a previous Traktor version still exists
            # (Traktor never deletes old version folders) but is no longer
            # the newest install - auto-follow the upgrade. Only do this
            # when the saved path is itself one of our own auto-detected
            # defaults; a path the user deliberately browsed to (a custom
            # location, a synced copy, etc.) is never silently replaced.
            if newest_default and saved != newest_default and saved in default_candidates:
                self.nml_path_var.set(newest_default)
            else:
                self.nml_path_var.set(saved)
        elif newest_default:
            self.nml_path_var.set(newest_default)

        self.output_dir_var.set(settings.export_output_dir or os.path.join(os.path.expanduser("~"), "Desktop"))
        self.selection_mode.set(settings.selection_mode)

        self._refresh_profile_combos()
        if settings.this_machine_profile:
            self.this_machine_var.set(settings.this_machine_profile)
        if settings.export_target_profile:
            self.target_machine_var.set(settings.export_target_profile)
        self._update_profiles_summary()

        if self.nml_path_var.get():
            self._load_nml(silent=True)

    @staticmethod
    def _default_nml_candidates() -> List[str]:
        """Every 'Traktor <version>' folder under ~/Documents/Native
        Instruments, newest version first. That base path is identical on
        Windows and macOS (Traktor installs to the platform's own
        Documents folder either way), so this needs no OS-specific
        handling - only os.path.expanduser/join, which already do the
        right thing on both."""
        docs = os.path.join(os.path.expanduser("~"), "Documents", "Native Instruments")
        entries = []
        if os.path.isdir(docs):
            for entry in os.listdir(docs):
                if entry.lower().startswith("traktor"):
                    entries.append(entry)
        # Numeric-aware sort (newest first) so e.g. "Traktor 10.0.0" sorts
        # above "Traktor 9.0.0" - plain reverse string sort would put the
        # "1" before the "9" and get that backwards. Each split part is
        # tagged (0, str) or (1, int) rather than left as a bare mix of
        # types, so entries with a different number of version segments
        # still compare safely instead of raising TypeError.
        version_key = lambda name: [
            (1, int(part)) if part.isdigit() else (0, part)
            for part in re.split(r"(\d+)", name)
        ]
        entries.sort(key=version_key, reverse=True)
        return [os.path.join(docs, entry, "collection.nml") for entry in entries]

    def _save_settings(self):
        selected = self._get_selected_display_names()
        self.config_manager.update_settings(
            collection_nml_path=self.nml_path_var.get(),
            machine_profiles=self.config_manager.settings.machine_profiles,
            this_machine_profile=self.this_machine_var.get(),
            export_target_profile=self.target_machine_var.get(),
            export_output_dir=self.output_dir_var.get(),
            selected_playlists=selected,
            selection_mode=self.selection_mode.get(),
            playlist_presets=self.config_manager.settings.playlist_presets,
            active_preset=self.preset_var.get(),
        )
        self.status_var.set("Settings saved")
        messagebox.showinfo("Settings Saved", "Configuration saved successfully!")

    # ------------------------------------------------------------------
    # NML loading
    # ------------------------------------------------------------------

    def _browse_nml(self):
        filename = filedialog.askopenfilename(
            title="Select Traktor collection.nml", filetypes=[("Traktor Collection", "*.nml"), ("All Files", "*.*")]
        )
        if filename:
            self.nml_path_var.set(filename)

    def _schedule_autoload(self, delay=400):
        if self._nml_autoload_after_id is not None:
            try:
                self.root.after_cancel(self._nml_autoload_after_id)
            except Exception:
                pass
        self._nml_autoload_after_id = self.root.after(delay, self._auto_load_nml)

    def _auto_load_nml(self):
        self._nml_autoload_after_id = None
        path = self.nml_path_var.get()
        if not path or not os.path.exists(path) or path == self._loaded_nml_path:
            return
        self._load_nml(silent=True)

    def _on_window_focus(self, event):
        if event.widget is not self.root:
            return
        path = self._loaded_nml_path
        if not path or not os.path.exists(path):
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if self._loaded_nml_mtime is not None and mtime != self._loaded_nml_mtime:
            self._loaded_nml_path = None
            self._schedule_autoload(delay=0)

    def _load_nml(self, silent=True):
        path = self.nml_path_var.get()
        if not path or not os.path.exists(path):
            if not silent:
                messagebox.showerror("Error", f"File not found: {path}")
            return

        self.status_var.set("Loading collection.nml...")
        self.root.update_idletasks()
        try:
            self.nml_tree = load_nml(path)
            root = self.nml_tree.getroot()
            self.collection_element = get_collection_element(root)
            if self.collection_element is None:
                raise ValueError("No <COLLECTION> element found - is this a Traktor collection.nml?")
            self.entry_index = build_entry_index(self.collection_element)
            self._ensure_this_machine_profile()

            playlists_root = get_playlists_root_node(root)
            self.all_nodes = walk_playlist_nodes(playlists_root) if playlists_root is not None else []

            self._loaded_nml_path = path
            self._loaded_nml_mtime = os.path.getmtime(path)

            entry_count = len(self.collection_element.findall("ENTRY"))
            playlist_count = sum(1 for n in self.all_nodes if n.kind == "PLAYLIST")
            smart_count = sum(1 for n in self.all_nodes if n.kind == "SMARTLIST")
            self.nml_info_var.set(
                f"{entry_count} tracks - {playlist_count} playlists, {smart_count} smart lists"
            )

            self.info_text.delete(1.0, tk.END)
            self.info_text.insert(tk.END, f"Loaded: {path}\n")
            self.info_text.insert(tk.END, f"NML version: {root.get('VERSION', '?')}\n")
            self.info_text.insert(tk.END, f"Tracks in collection: {entry_count}\n")
            self.info_text.insert(tk.END, f"Playlists: {playlist_count}\n")
            self.info_text.insert(tk.END, f"Smart lists: {smart_count}\n")

            self._filter_playlists()
            self._restore_selection(self.config_manager.settings.selected_playlists)
            self.status_var.set(f"Loaded {len(self.all_nodes)} playlists/smart lists")
        except Exception as e:
            logger.error(f"Failed to load NML: {e}")
            self.status_var.set("Failed to load collection.nml")
            if not silent:
                messagebox.showerror("Error", f"Could not load collection.nml:\n{e}")

    # ------------------------------------------------------------------
    # Machine profiles
    # ------------------------------------------------------------------

    def _ensure_this_machine_profile(self):
        """Auto-register and select this machine's profile from the
        collection that was just loaded, so a machine that's never been
        set up before needs zero manual steps - no typing a name, no
        picking a sample track file. Never modifies an existing profile's
        volume/dir_prefix (e.g. one synced over from a previous session)
        - only creates a new one when nothing already matches, and always
        makes sure "this machine" points at whichever profile actually
        matches what's loaded, since a stale mismatch there would rewrite
        exported paths against the wrong root."""
        detected = detect_library_root(self.entry_index)
        if not detected:
            return
        volume, dir_prefix = detected

        profiles = self.config_manager.settings.machine_profiles
        matching_name = next(
            (name for name, raw in profiles.items()
             if raw.get("volume") == volume and raw.get("dir_prefix") == dir_prefix),
            None,
        )
        created_new = matching_name is None
        if created_new:
            matching_name = self._unique_profile_name(self._default_machine_name())
            profiles[matching_name] = MachineProfile(name=matching_name, volume=volume, dir_prefix=dir_prefix).to_dict()
            self._refresh_profile_combos()

        if not (created_new or self.this_machine_var.get() != matching_name):
            return  # Nothing changed - matching profile already selected.

        self.this_machine_var.set(matching_name)
        self.config_manager.update_settings(machine_profiles=profiles, this_machine_profile=matching_name)
        self._on_profiles_changed()

    @staticmethod
    def _default_machine_name() -> str:
        """This computer's hostname, as a friendly starting name - trimmed
        of the ".local" mDNS suffix macOS hostnames usually carry."""
        name = socket.gethostname() or ""
        if name.lower().endswith(".local"):
            name = name[:-len(".local")]
        return name.strip() or ("Mac" if sys.platform == "darwin" else "PC")

    def _unique_profile_name(self, base: str) -> str:
        existing = set(self.config_manager.settings.machine_profiles.keys())
        if base not in existing:
            return base
        n = 2
        while f"{base} ({n})" in existing:
            n += 1
        return f"{base} ({n})"

    def get_this_machine_dir_default(self) -> str:
        """Best-effort starting point for a brand-new profile's dir prefix -
        copy an existing profile's, since the shared folder structure is
        usually identical across machines (same username, mirrored layout)."""
        profiles = self.config_manager.settings.machine_profiles
        if profiles:
            first = next(iter(profiles.values()))
            return first.get("dir_prefix", "")
        return ""

    def _refresh_profile_combos(self):
        names = sorted(self.config_manager.settings.machine_profiles.keys())
        self.this_machine_combo["values"] = names
        self.target_machine_combo["values"] = names

    def _get_profile(self, name: str) -> Optional[MachineProfile]:
        raw = self.config_manager.settings.machine_profiles.get(name)
        return MachineProfile.from_dict(raw) if raw else None

    def _new_profile(self):
        existing = list(self.config_manager.settings.machine_profiles.keys())
        dialog = ProfileEditorDialog(self.root, self, existing_names=existing)
        if dialog.result:
            self.config_manager.settings.machine_profiles[dialog.result.name] = dialog.result.to_dict()
            self.config_manager.save_settings()
            self._refresh_profile_combos()
            if not self.this_machine_var.get():
                self.this_machine_var.set(dialog.result.name)
            elif not self.target_machine_var.get():
                self.target_machine_var.set(dialog.result.name)
            self._on_profiles_changed()

    def _edit_profile(self):
        name = self.this_machine_var.get() or self.target_machine_var.get()
        if not name:
            messagebox.showinfo("Edit Profile", "Select a profile first (This machine / Export for).")
            return
        current = self._get_profile(name)
        existing = [n for n in self.config_manager.settings.machine_profiles.keys() if n != name]
        dialog = ProfileEditorDialog(self.root, self, profile=current, existing_names=existing)
        if dialog.result:
            profiles = self.config_manager.settings.machine_profiles
            if name != dialog.result.name:
                profiles.pop(name, None)
            profiles[dialog.result.name] = dialog.result.to_dict()
            self.config_manager.save_settings()
            self._refresh_profile_combos()
            if self.this_machine_var.get() == name:
                self.this_machine_var.set(dialog.result.name)
            if self.target_machine_var.get() == name:
                self.target_machine_var.set(dialog.result.name)
            self._on_profiles_changed()

    def _delete_profile(self):
        name = self.this_machine_var.get() or self.target_machine_var.get()
        if not name:
            messagebox.showinfo("Delete Profile", "Select a profile first.")
            return
        if not messagebox.askyesno("Delete Profile", f"Delete machine profile '{name}'?"):
            return
        self.config_manager.settings.machine_profiles.pop(name, None)
        self.config_manager.save_settings()
        if self.this_machine_var.get() == name:
            self.this_machine_var.set("")
        if self.target_machine_var.get() == name:
            self.target_machine_var.set("")
        self._refresh_profile_combos()
        self._on_profiles_changed()

    def _update_profiles_summary(self):
        this_p = self._get_profile(self.this_machine_var.get())
        dst_p = self._get_profile(self.target_machine_var.get())
        parts = []
        if this_p:
            parts.append(f"This machine ({this_p.name}): {this_p.volume} + {this_p.dir_prefix}")
        if dst_p:
            parts.append(f"Export for ({dst_p.name}): {dst_p.volume} + {dst_p.dir_prefix}")
        self.profiles_summary_var.set(" | ".join(parts) if parts else "No profiles configured yet.")

    def _on_profiles_changed(self):
        self._update_profiles_summary()
        self._filter_playlists()

    # ------------------------------------------------------------------
    # Playlist tree / selection
    # ------------------------------------------------------------------

    def _in_library_display(self, node: PlaylistNode, this_profile: Optional[MachineProfile]) -> str:
        if node.kind == "SMARTLIST":
            return "n/a (dynamic)"
        if not this_profile or not this_profile.is_configured:
            return "-"
        keys = playlist_track_keys(node)
        if not keys:
            return "0/0"
        in_lib = sum(1 for k in keys if k.startswith(this_profile.root_key))
        return f"{in_lib}/{len(keys)}"

    def _filter_playlists(self, *_args):
        filter_text = self.filter_var.get().lower()
        selected_names = {
            self.playlist_tree.item(i, "text") for i in self.playlist_tree.selection()
        }

        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)

        this_profile = self._get_profile(self.this_machine_var.get())

        for node in self.all_nodes:
            name = node.display_name
            if filter_text and filter_text not in name.lower():
                continue
            kind_label = "Playlist" if node.kind == "PLAYLIST" else "Smart List"
            count = playlist_track_count(node)
            count_display = "dynamic" if count is None else str(count)
            in_lib_display = self._in_library_display(node, this_profile)

            item_id = self.playlist_tree.insert(
                "", "end", text=name, values=(kind_label, count_display, in_lib_display)
            )
            if name in selected_names:
                self.playlist_tree.selection_add(item_id)

        self._update_selection_summary()

    def _select_all(self):
        self.playlist_tree.selection_set(self.playlist_tree.get_children())
        self._update_selection_summary()

    def _clear_all(self):
        self.playlist_tree.selection_remove(self.playlist_tree.selection())
        self._update_selection_summary()

    def _auto_select(self):
        """Select playlists/smart lists whose name starts with a digit 1-9,
        matching the same convention used by the MusicBee->Traktor sync tool."""
        self._clear_all()
        for item in self.playlist_tree.get_children():
            name = self.playlist_tree.item(item, "text")
            leaf = name.rsplit("/", 1)[-1]
            if leaf and leaf[0].isdigit() and leaf[0] != '0':
                self.playlist_tree.selection_add(item)
        self._update_selection_summary()

    def _get_selected_display_names(self) -> List[str]:
        return [self.playlist_tree.item(i, "text") for i in self.playlist_tree.selection()]

    def _get_nodes_to_export(self) -> List[PlaylistNode]:
        selected_names = set(self._get_selected_display_names())
        by_name = {n.display_name: n for n in self.all_nodes}
        visible_names = [self.playlist_tree.item(i, "text") for i in self.playlist_tree.get_children()]

        if self.selection_mode.get() == "include":
            names = [n for n in visible_names if n in selected_names]
        else:
            names = [n for n in visible_names if n not in selected_names]
        return [by_name[n] for n in names if n in by_name]

    def _restore_selection(self, names: List[str]):
        wanted = set(names)
        for item in self.playlist_tree.get_children():
            if self.playlist_tree.item(item, "text") in wanted:
                self.playlist_tree.selection_add(item)
        self._update_selection_summary()

    def _update_selection_summary(self):
        total = len(self.playlist_tree.get_children())
        selected = len(self.playlist_tree.selection())
        if total == 0:
            self.selection_summary.set("No playlists loaded")
        elif self.selection_mode.get() == "include":
            self.selection_summary.set(f"Will export {selected}/{total}" if selected else f"No selection (0/{total})")
        else:
            self.selection_summary.set(f"Will export {total - selected}/{total} (excluding {selected})")

        can_export = total > 0 and (self.selection_mode.get() == "exclude" or selected > 0)
        self.export_btn.config(state=tk.NORMAL if can_export else tk.DISABLED)

    # ------------------------------------------------------------------
    # Presets (mirrors the pattern used by the MusicBee/PowerAmp sync tools)
    # ------------------------------------------------------------------

    def _refresh_preset_combo(self, select: Optional[str] = None):
        names = [DEFAULT_PRESET_NAME] + sorted(self.config_manager.settings.playlist_presets.keys())
        self.preset_combo["values"] = names
        if select and select in names:
            self.preset_var.set(select)
        elif self.preset_var.get() not in names:
            self.preset_var.set(DEFAULT_PRESET_NAME)

    def _capture_current_selection(self) -> Dict[str, Any]:
        return {"playlists": self._get_selected_display_names(), "mode": self.selection_mode.get()}

    def _apply_selection(self, names: List[str], mode: str):
        self.selection_mode.set(mode if mode in ("include", "exclude") else "include")
        self._clear_all()
        wanted = set(names)
        found = set()
        for item in self.playlist_tree.get_children():
            name = self.playlist_tree.item(item, "text")
            if name in wanted:
                self.playlist_tree.selection_add(item)
                found.add(name)
        missing = sorted(wanted - found)
        if missing:
            self._log(f"Preset: skipped {len(missing)} playlist(s) not in current collection: {', '.join(missing)}")
        self._update_selection_summary()

    def _load_preset(self):
        name = self.preset_var.get()
        if not name or not self.all_nodes:
            if not self.all_nodes:
                messagebox.showinfo("Load Preset", "Load a collection first, then apply the preset.")
            return
        if name == DEFAULT_PRESET_NAME:
            self._auto_select()
            self.status_var.set(f"Applied preset: {name}")
            return
        preset = self.config_manager.settings.playlist_presets.get(name)
        if not preset:
            messagebox.showerror("Load Preset", f"Preset '{name}' not found.")
            self._refresh_preset_combo()
            return
        self._apply_selection(preset.get("playlists", []), preset.get("mode", "include"))
        self.status_var.set(f"Applied preset: {name}")

    def _save_preset_as(self):
        name = simpledialog.askstring("Save Preset", "Preset name:", parent=self.root)
        if not name:
            return
        name = name.strip()
        if not name or name == DEFAULT_PRESET_NAME:
            messagebox.showerror("Save Preset", f"'{DEFAULT_PRESET_NAME}' is reserved.")
            return
        presets = self.config_manager.settings.playlist_presets
        if name in presets and not messagebox.askyesno("Save Preset", f"Preset '{name}' exists. Overwrite?"):
            return
        presets[name] = self._capture_current_selection()
        self.config_manager.save_settings()
        self._refresh_preset_combo(select=name)
        self.status_var.set(f"Saved preset: {name}")

    def _overwrite_preset(self):
        name = self.preset_var.get()
        if not name or name == DEFAULT_PRESET_NAME:
            messagebox.showinfo("Save Preset", "Select a saved preset, or use Save As to create one.")
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

    # ------------------------------------------------------------------
    # Output / export
    # ------------------------------------------------------------------

    def _browse_output_dir(self):
        directory = filedialog.askdirectory(title="Select folder to save the exported NML")
        if directory:
            self.output_dir_var.set(directory)

    def _export(self):
        if self.nml_tree is None:
            messagebox.showerror("Export", "Load a collection.nml first.")
            return

        src_name = self.this_machine_var.get()
        dst_name = self.target_machine_var.get()
        src_profile = self._get_profile(src_name)
        dst_profile = self._get_profile(dst_name)

        if not src_profile or not src_profile.is_configured:
            messagebox.showerror("Export", "Set up a 'This machine' profile first (New... button).")
            return
        if not dst_profile or not dst_profile.is_configured:
            messagebox.showerror("Export", "Set up an 'Export for' profile first (New... button).")
            return
        if src_name == dst_name:
            if not messagebox.askyesno(
                "Export", "Source and destination profiles are the same - paths won't be remapped. Continue anyway?"
            ):
                return

        nodes = self._get_nodes_to_export()
        if not nodes:
            messagebox.showwarning("Export", "No playlists/smart lists selected.")
            return

        output_dir = self.output_dir_var.get()
        if not output_dir:
            messagebox.showerror("Export", "Choose an output folder first.")
            return
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        output_path = os.path.join(output_dir, f"TraktorSync_to_{dst_name}_{timestamp}.nml")

        self.status_var.set("Exporting...")
        self.root.update_idletasks()

        try:
            source_root = self.nml_tree.getroot()
            export_root, stats = build_export_root(source_root, nodes, self.entry_index, src_profile, dst_profile)
            write_nml(export_root, output_path)
        except Exception as e:
            logger.error(f"Export failed: {e}")
            import traceback
            self._log(f"ERROR: {e}\n{traceback.format_exc()}")
            messagebox.showerror("Export", f"Export failed:\n{e}")
            self.status_var.set("Export failed")
            return

        self._log(f"Exported to: {output_path}")
        self._log(f"Playlists exported: {stats.playlists_exported}")
        self._log(f"Smart lists exported: {stats.smartlists_exported}")
        self._log(f"Tracks included: {stats.tracks_exported}")
        if stats.tracks_skipped_outside_library:
            self._log(
                f"WARNING: {stats.tracks_skipped_outside_library} track(s) skipped - "
                f"not under {src_profile.name}'s registered library root, so they can't be relocated on {dst_profile.name}."
            )
        if stats.tracks_missing_from_collection:
            self._log(
                f"WARNING: {stats.tracks_missing_from_collection} track reference(s) in a playlist had no matching "
                f"COLLECTION entry (likely deleted from the library) and were dropped."
            )

        self.status_var.set(f"Exported {stats.tracks_exported} tracks to {os.path.basename(output_path)}")
        messagebox.showinfo(
            "Export Complete",
            f"Saved to:\n{output_path}\n\n"
            f"{stats.playlists_exported} playlist(s), {stats.smartlists_exported} smart list(s), "
            f"{stats.tracks_exported} track(s).\n\n"
            f"On the other machine: Traktor Preferences > File Management > Import Collection, then pick this file.",
        )
        self._save_settings_silent()

    def _save_settings_silent(self):
        selected = self._get_selected_display_names()
        self.config_manager.update_settings(
            collection_nml_path=self.nml_path_var.get(),
            this_machine_profile=self.this_machine_var.get(),
            export_target_profile=self.target_machine_var.get(),
            export_output_dir=self.output_dir_var.get(),
            selected_playlists=selected,
            selection_mode=self.selection_mode.get(),
        )


# ============================================================================
# ENTRY POINT
# ============================================================================

def main():
    setup_shared_logging("traktor_playlist_sync")

    parser = argparse.ArgumentParser(description="Traktor Playlist Sync")
    parser.add_argument("--collection-nml", help="Path to Traktor collection.nml")
    args, _unknown = parser.parse_known_args()

    root = tk.Tk()
    apply_category_icon(root)
    app = TraktorPlaylistSyncUI(root)
    if args.collection_nml and os.path.exists(args.collection_nml):
        app.nml_path_var.set(args.collection_nml)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
