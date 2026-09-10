#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_TraktorPlaylistSync.py
Description: Move Traktor playlists/smart lists between this pipeline's own
             machines. Export writes selected playlists - with this machine's
             own raw Traktor paths, unmodified - to a folder next to
             collection.nml and opens it, ready to drop onto a USB stick.
             Import reads such a file on the destination machine, rewrites
             the paths to this machine's own library layout, and merges the
             playlists directly into this machine's live collection.nml.

Both machines only ever need to know their own library root ("This Machine"
profile, below) - the source machine's root travels embedded in the exported
file itself, so no destination-specific setup is ever required. No audio
files are copied - this assumes the actual files already live in a
shared/synced location reachable from both machines (NAS, cloud folder,
etc.), same as before.
"""

import os
import sys
import copy
import shutil
import socket
import argparse
import datetime
import subprocess
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import filedialog, messagebox, ttk, simpledialog
from dataclasses import dataclass, asdict, field
from typing import Optional, Dict, List, Any, Tuple

from shared_window_icon import apply_category_icon
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_open_path import open_path

logger = get_logger("traktor_playlist_sync")

APP_NAME = "Traktor Playlist Sync"
APP_VERSION = "2.0.0"
HEADER_COLOR = "#2c3e50"

APP_DATA_DIR = os.path.join(os.path.expanduser("~"), "AppData", "Local", "PipelineManager")
CONFIG_FILE = os.path.join(APP_DATA_DIR, "traktor_playlist_sync_config.json")

# Built-in preset that mirrors the Auto Select rule (digits 1-9 prefix).
DEFAULT_PRESET_NAME = "Default (Auto)"

# Subfolder next to collection.nml where exports are dropped.
EXPORT_SUBFOLDER = "PlaylistSync"

# Custom element embedded in exported files recording the source machine's
# library root, so Import never has to guess it.
PIPELINEMETA_TAG = "PIPELINEMETA"

TRAKTOR_PROCESS_NAMES = ("Traktor.exe",)


# ============================================================================
# NML DATA MODEL
# ============================================================================

@dataclass
class MachineProfile:
    """This machine's view of the shared DJ library, in Traktor's own path tokens.

    `volume` and `dir_prefix` are copied verbatim from a real LOCATION element
    (VOLUME + DIR) in this machine's collection.nml - never hand-derived from
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
    this_machine: Dict[str, str] = field(default_factory=dict)
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


@dataclass
class ImportStats:
    playlists_added: int = 0
    playlists_replaced: int = 0
    smartlists_added: int = 0
    smartlists_replaced: int = 0
    tracks_merged: int = 0
    tracks_skipped_outside_library: int = 0
    tracks_missing_from_export: int = 0


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
    encodes it on this machine, provided the sample track sits directly at
    the top of the shared library folder rather than in a subfolder."""
    target = os.path.basename(sample_filename)
    for entry in entry_index.values():
        loc = entry.find("LOCATION")
        if loc is not None and loc.get("FILE") == target:
            return loc.get("VOLUME", ""), loc.get("DIR", "")
    return None


# ----------------------------------------------------------------------
# Export: build a standalone fragment, this machine's own raw paths
# ----------------------------------------------------------------------

def write_export_metadata(nml_root: ET.Element, src: MachineProfile) -> None:
    ET.SubElement(nml_root, PIPELINEMETA_TAG, {
        "SRC_NAME": src.name,
        "SRC_VOLUME": src.volume,
        "SRC_DIR_PREFIX": src.dir_prefix,
        "EXPORTED_AT": datetime.datetime.now().isoformat(timespec="seconds"),
    })


def read_export_metadata(nml_root: ET.Element) -> Optional[MachineProfile]:
    meta = nml_root.find(PIPELINEMETA_TAG)
    if meta is None:
        return None
    return MachineProfile(
        name=meta.get("SRC_NAME", ""),
        volume=meta.get("SRC_VOLUME", ""),
        dir_prefix=meta.get("SRC_DIR_PREFIX", ""),
    )


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
                         needed_entries: set, src: MachineProfile, stats: ExportStats) -> ET.Element:
    """Deep-copy one playlist/smart-list NODE, keeping this machine's own raw paths."""
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
            key = pk.get("KEY") if pk is not None else None
            if not key:
                playlist_el.remove(entry_el)
                continue
            if key not in entry_index:
                playlist_el.remove(entry_el)
                stats.tracks_missing_from_collection += 1
                continue
            if not src.root_key or not key.startswith(src.root_key):
                playlist_el.remove(entry_el)
                stats.tracks_skipped_outside_library += 1
                continue
            needed_entries.add(key)
            surviving += 1
        playlist_el.set("ENTRIES", str(surviving))

    stats.playlists_exported += 1
    stats.tracks_exported += surviving
    return new_node


def _emit_folder(name: str, tree: Dict[str, Any], entry_index: Dict[str, ET.Element],
                  needed_entries: set, src: MachineProfile, stats: ExportStats) -> ET.Element:
    folder_el = ET.Element("NODE", {"TYPE": "FOLDER", "NAME": name})
    subnodes_el = ET.SubElement(folder_el, "SUBNODES")
    count = 0
    for sub_name, sub_tree in tree["folders"].items():
        subnodes_el.append(_emit_folder(sub_name, sub_tree, entry_index, needed_entries, src, stats))
        count += 1
    for leaf in tree["leaves"]:
        subnodes_el.append(_emit_playlist_leaf(leaf, entry_index, needed_entries, src, stats))
        count += 1
    subnodes_el.set("COUNT", str(count))
    return folder_el


def build_export_root(source_root: ET.Element, selected_nodes: List[PlaylistNode],
                       entry_index: Dict[str, ET.Element], src: MachineProfile) -> Tuple[ET.Element, ExportStats]:
    """Build a standalone <NML> element containing only the selected
    playlists/smart lists and the COLLECTION entries they reference, with
    this machine's own raw LOCATION/PRIMARYKEY paths left untouched. The
    source machine's library root travels along embedded as PIPELINEMETA,
    so the destination machine can rewrite paths itself at import time."""
    stats = ExportStats()
    needed_entries: set = set()

    tree = _build_folder_tree(selected_nodes)
    root_folder_el = _emit_folder("$ROOT", tree, entry_index, needed_entries, src, stats)

    new_root = ET.Element("NML", {"VERSION": source_root.get("VERSION", "19")})

    head = source_root.find("HEAD")
    if head is not None:
        new_root.append(copy.deepcopy(head))
    else:
        ET.SubElement(new_root, "HEAD", {"COMPANY": "www.native-instruments.com", "PROGRAM": "Traktor"})

    collection_el = ET.Element("COLLECTION", {"ENTRIES": str(len(needed_entries))})
    for key in needed_entries:
        collection_el.append(copy.deepcopy(entry_index[key]))
    new_root.append(collection_el)

    playlists_el = ET.Element("PLAYLISTS")
    playlists_el.append(root_folder_el)
    new_root.append(playlists_el)

    write_export_metadata(new_root, src)

    return new_root, stats


def write_nml(root_element: ET.Element, output_path: str) -> None:
    with open(output_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n')
        f.write(ET.tostring(root_element, encoding="utf-8"))


def write_nml_atomic(root_element: ET.Element, output_path: str) -> None:
    """Write via a temp file + os.replace so a live collection.nml is never
    left half-written if something goes wrong mid-write."""
    tmp_path = f"{output_path}.tmp-{os.getpid()}"
    with open(tmp_path, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n')
        f.write(ET.tostring(root_element, encoding="utf-8"))
    os.replace(tmp_path, output_path)


# ----------------------------------------------------------------------
# Import: rewrite paths from the embedded src root to this machine's own,
# merge directly into this machine's live PLAYLISTS tree + COLLECTION
# ----------------------------------------------------------------------

def _find_child_folder(subnodes_el: ET.Element, name: str) -> Optional[ET.Element]:
    for node in subnodes_el.findall("NODE"):
        if node.get("TYPE") == "FOLDER" and node.get("NAME") == name:
            return node
    return None


def _find_child_leaf(subnodes_el: ET.Element, name: str) -> Optional[ET.Element]:
    for node in subnodes_el.findall("NODE"):
        if node.get("TYPE") in ("PLAYLIST", "SMARTLIST") and node.get("NAME") == name:
            return node
    return None


def _ensure_folder_path(dst_root_folder: ET.Element, path: Tuple[str, ...]) -> ET.Element:
    """Walk/create the FOLDER chain under dst_root_folder (a $ROOT NODE),
    returning the leaf folder's NODE element."""
    current = dst_root_folder
    for part in path:
        subnodes = current.find("SUBNODES")
        if subnodes is None:
            subnodes = ET.SubElement(current, "SUBNODES", {"COUNT": "0"})
        child = _find_child_folder(subnodes, part)
        if child is None:
            child = ET.Element("NODE", {"TYPE": "FOLDER", "NAME": part})
            ET.SubElement(child, "SUBNODES", {"COUNT": "0"})
            subnodes.append(child)
            subnodes.set("COUNT", str(len(subnodes.findall("NODE"))))
        current = child
    return current


def _merge_playlist_entries(new_node: ET.Element, export_entry_index: Dict[str, ET.Element],
                             dst_collection_el: ET.Element, dst_entry_index: Dict[str, ET.Element],
                             src: MachineProfile, dst: MachineProfile, stats: ImportStats) -> int:
    """Rewrite + merge the COLLECTION entries a (deep-copied) PLAYLIST leaf's
    tracks reference, mutating PRIMARYKEY KEYs in place. Returns surviving
    track count. No-op for SMARTLIST (caller only calls this for PLAYLIST)."""
    playlist_el = new_node.find("PLAYLIST")
    if playlist_el is None:
        return 0
    surviving = 0
    for entry_el in list(playlist_el.findall("ENTRY")):
        pk = entry_el.find("PRIMARYKEY")
        key = pk.get("KEY") if pk is not None else None
        if not key or key not in export_entry_index:
            playlist_el.remove(entry_el)
            stats.tracks_missing_from_export += 1
            continue
        new_key = rewrite_key_string(key, src, dst)
        if new_key is None:
            playlist_el.remove(entry_el)
            stats.tracks_skipped_outside_library += 1
            continue
        if new_key not in dst_entry_index:
            entry_copy = copy.deepcopy(export_entry_index[key])
            rewrite_entry_location(entry_copy, src, dst)
            dst_collection_el.append(entry_copy)
            dst_collection_el.set("ENTRIES", str(len(dst_collection_el.findall("ENTRY"))))
            dst_entry_index[new_key] = entry_copy
        pk.set("KEY", new_key)
        surviving += 1
    playlist_el.set("ENTRIES", str(surviving))
    stats.tracks_merged += surviving
    return surviving


def merge_node_into_collection(node: PlaylistNode, export_entry_index: Dict[str, ET.Element],
                                dst_root_folder: ET.Element, dst_collection_el: ET.Element,
                                dst_entry_index: Dict[str, ET.Element], src: MachineProfile,
                                dst: MachineProfile, stats: ImportStats) -> None:
    """Merge one playlist/smart-list node into the destination's live
    PLAYLISTS tree and COLLECTION, replacing any existing node of the same
    name at the same folder path."""
    new_node = copy.deepcopy(node.element)

    if node.kind == "PLAYLIST":
        _merge_playlist_entries(new_node, export_entry_index, dst_collection_el, dst_entry_index, src, dst, stats)

    folder = _ensure_folder_path(dst_root_folder, node.path)
    subnodes = folder.find("SUBNODES")
    if subnodes is None:
        subnodes = ET.SubElement(folder, "SUBNODES", {"COUNT": "0"})

    existing = _find_child_leaf(subnodes, node.name)
    if existing is not None:
        subnodes.remove(existing)
        subnodes.append(new_node)
        if node.kind == "PLAYLIST":
            stats.playlists_replaced += 1
        else:
            stats.smartlists_replaced += 1
    else:
        subnodes.append(new_node)
        if node.kind == "PLAYLIST":
            stats.playlists_added += 1
        else:
            stats.smartlists_added += 1
    subnodes.set("COUNT", str(len(subnodes.findall("NODE"))))


def backup_file(path: str) -> str:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = f"{path}.bak-{timestamp}"
    shutil.copy2(path, backup_path)
    return backup_path


def running_traktor_processes() -> List[str]:
    """Return the subset of TRAKTOR_PROCESS_NAMES currently running, so we
    can refuse to merge into a collection.nml Traktor still has open."""
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

    lower_names = {n.lower() for n in TRAKTOR_PROCESS_NAMES}
    found = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        image = line.split('","', 1)[0].lstrip('"').lower()
        if image in lower_names and image not in (f.lower() for f in found):
            for canonical in TRAKTOR_PROCESS_NAMES:
                if canonical.lower() == image:
                    found.append(canonical)
                    break
    return found


# ============================================================================
# MACHINE PROFILE EDITOR DIALOG
# ============================================================================

class ProfileEditorDialog:
    """Create/edit the local "This Machine" library-root profile. The detect
    button avoids ever having to hand-type Traktor's internal "/:" path
    syntax."""

    def __init__(self, parent, app: 'TraktorPlaylistSyncUI', profile: Optional[MachineProfile] = None):
        self.app = app
        self.result: Optional[MachineProfile] = None

        self.win = tk.Toplevel(parent)
        self.win.title("This Machine")
        self.win.geometry("520x260")
        self.win.transient(parent)
        self.win.grab_set()

        form = ttk.Frame(self.win)
        form.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Name:").grid(row=0, column=0, sticky="w", pady=5)
        self.name_var = tk.StringVar(value=profile.name if profile and profile.name else socket.gethostname())
        ttk.Entry(form, textvariable=self.name_var).grid(row=0, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Volume:").grid(row=1, column=0, sticky="w", pady=5)
        self.volume_var = tk.StringVar(value=profile.volume if profile else "")
        ttk.Entry(form, textvariable=self.volume_var).grid(row=1, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(form, text="Dir prefix:").grid(row=2, column=0, sticky="w", pady=5)
        self.dir_var = tk.StringVar(value=profile.dir_prefix if profile else "")
        ttk.Entry(form, textvariable=self.dir_var).grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)

        ttk.Label(
            form,
            text="e.g. Volume \"C:\" / \"Macintosh HD\", Dir prefix \"/:Users/:flori/:Music/:DJ Library/:\"",
            foreground="gray", font=("Arial", 8), wraplength=470, justify="left",
        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(0, 10))

        ttk.Button(
            form, text="Detect from this machine's loaded collection...",
            command=self._detect_from_local,
        ).grid(row=4, column=0, columnspan=3, sticky="ew", pady=5)

        btn_frame = ttk.Frame(self.win)
        btn_frame.pack(fill=tk.X, padx=15, pady=(0, 15))
        ttk.Button(btn_frame, text="Cancel", command=self.win.destroy).pack(side=tk.RIGHT, padx=(5, 0))
        ttk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT)

        self.win.wait_window()

    def _detect_from_local(self):
        if not self.app.entry_index:
            messagebox.showinfo("Detect", "Load a collection.nml first.", parent=self.win)
            return
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

    def _save(self):
        name = self.name_var.get().strip()
        volume = self.volume_var.get().strip()
        dir_prefix = self.dir_var.get().strip()

        if not name:
            messagebox.showerror("This Machine", "Name is required.", parent=self.win)
            return
        if not volume or not dir_prefix:
            messagebox.showerror("This Machine", "Volume and Dir prefix are both required.", parent=self.win)
            return
        if not dir_prefix.startswith("/:") or not dir_prefix.endswith("/:"):
            if not messagebox.askyesno(
                "This Machine",
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

        # NML state (this machine's own collection.nml - export source AND import target)
        self.nml_tree: Optional[ET.ElementTree] = None
        self.collection_element: Optional[ET.Element] = None
        self.entry_index: Dict[str, ET.Element] = {}
        self.all_nodes: List[PlaylistNode] = []
        self._loaded_nml_path: Optional[str] = None
        self._loaded_nml_mtime: Optional[float] = None
        self._nml_autoload_after_id = None

        # This Machine profile
        self.machine_profile = MachineProfile(name=socket.gethostname())

        # Import state (a fragment file produced by Export on another machine)
        self._import_entry_index: Dict[str, ET.Element] = {}
        self._import_nodes: List[PlaylistNode] = []
        self._import_src_profile: Optional[MachineProfile] = None

        self._create_header()
        self._create_body()

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

    def _create_body(self):
        main = ttk.Frame(self.root)
        main.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(2, weight=3)
        main.rowconfigure(3, weight=2)
        self._main = main

        self._create_source_panel(main)
        self._create_machine_panel(main)

        notebook = ttk.Notebook(main)
        notebook.grid(row=2, column=0, sticky="nsew", padx=5, pady=5)

        export_tab = ttk.Frame(notebook)
        notebook.add(export_tab, text="Export")
        self._create_export_tab(export_tab)

        import_tab = ttk.Frame(notebook)
        notebook.add(import_tab, text="Import")
        self._create_import_tab(import_tab)

        self._create_results_panel(main)

    def _create_source_panel(self, main):
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

    def _create_machine_panel(self, main):
        frame = ttk.LabelFrame(main, text="This Machine (shared DJ library root)")
        frame.grid(row=1, column=0, sticky="ew", padx=5, pady=5)
        frame.columnconfigure(0, weight=1)

        self.machine_summary_var = tk.StringVar()
        ttk.Label(frame, textvariable=self.machine_summary_var, foreground="gray").grid(
            row=0, column=0, sticky="w", padx=10, pady=8
        )
        ttk.Button(frame, text="Set up / Edit...", command=self._setup_machine_profile).grid(
            row=0, column=1, padx=10, pady=8
        )

    def _create_export_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)

        # --- Output ---
        output_frame = ttk.LabelFrame(tab, text="Output")
        output_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        output_frame.columnconfigure(1, weight=1)

        ttk.Label(output_frame, text="Save folder:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.output_dir_var = tk.StringVar()
        ttk.Entry(output_frame, textvariable=self.output_dir_var, width=55).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(output_frame, text="Browse", command=self._browse_output_dir).grid(row=0, column=2, padx=5, pady=10)

        # --- Playlist selection ---
        playlist_frame = ttk.LabelFrame(tab, text="Playlist / Smart List Selection")
        playlist_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        playlist_frame.columnconfigure(0, weight=1)
        playlist_frame.rowconfigure(2, weight=1)
        tab.rowconfigure(1, weight=1)

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
        action_frame = ttk.Frame(tab)
        action_frame.grid(row=2, column=0, sticky="ew", pady=10)
        action_frame.columnconfigure(1, weight=1)

        ttk.Button(action_frame, text="Save Settings", command=self._save_settings, width=15).grid(row=0, column=0, padx=10)

        right_btns = ttk.Frame(action_frame)
        right_btns.grid(row=0, column=1, sticky="e", padx=10)
        self.export_btn = tk.Button(right_btns, text="Export", command=self._export, width=15,
                                     bg="green", fg="white", font=('', 9, 'bold'))
        self.export_btn.pack(side=tk.LEFT)

    def _create_import_tab(self, tab):
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(1, weight=1)

        # --- File picker ---
        file_frame = ttk.LabelFrame(tab, text="Exported file (from another machine, e.g. copied off a USB stick)")
        file_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        file_frame.columnconfigure(1, weight=1)

        ttk.Label(file_frame, text="File:").grid(row=0, column=0, sticky="w", padx=10, pady=10)
        self.import_path_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.import_path_var, width=55).grid(row=0, column=1, sticky="ew", padx=5, pady=10)
        ttk.Button(file_frame, text="Browse", command=self._browse_import_file).grid(row=0, column=2, padx=5, pady=10)

        self.import_info_var = tk.StringVar(value="No file loaded")
        ttk.Label(file_frame, textvariable=self.import_info_var, foreground="gray", wraplength=820, justify="left").grid(
            row=1, column=0, columnspan=3, sticky="w", padx=10, pady=(0, 10)
        )

        # --- Preview / selection ---
        preview_frame = ttk.LabelFrame(tab, text="Playlists / Smart Lists to Merge")
        preview_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(1, weight=1)

        summary_frame = ttk.Frame(preview_frame)
        summary_frame.grid(row=0, column=0, sticky="ew", padx=5, pady=5)
        summary_frame.columnconfigure(0, weight=1)
        self.import_selection_summary = tk.StringVar(value="No file loaded")
        ttk.Label(summary_frame, textvariable=self.import_selection_summary, font=("Arial", 9), foreground="blue").grid(
            row=0, column=0, sticky="w", padx=5
        )

        import_list_frame = ttk.Frame(preview_frame)
        import_list_frame.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)
        import_list_frame.columnconfigure(0, weight=1)
        import_list_frame.rowconfigure(0, weight=1)

        self.import_tree = ttk.Treeview(
            import_list_frame, columns=("type", "tracks", "action"), show="tree headings", height=10
        )
        self.import_tree.grid(row=0, column=0, sticky="nsew")
        self.import_tree.heading("#0", text="Playlist")
        self.import_tree.heading("type", text="Type")
        self.import_tree.heading("tracks", text="Tracks")
        self.import_tree.heading("action", text="Action")
        self.import_tree.column("#0", width=320)
        self.import_tree.column("type", width=90, anchor="center")
        self.import_tree.column("tracks", width=70, anchor="center")
        self.import_tree.column("action", width=130, anchor="center")
        self.import_tree.bind('<<TreeviewSelect>>', lambda e: self._update_import_summary())

        import_scroll = ttk.Scrollbar(import_list_frame, orient="vertical", command=self.import_tree.yview)
        import_scroll.grid(row=0, column=1, sticky="ns")
        self.import_tree.config(yscrollcommand=import_scroll.set)

        import_btn_frame = ttk.Frame(preview_frame)
        import_btn_frame.grid(row=2, column=0, sticky="ew", pady=5)
        ttk.Button(import_btn_frame, text="Select All", command=self._select_all_import).grid(row=0, column=0, padx=5)
        ttk.Button(import_btn_frame, text="Clear All", command=self._clear_all_import).grid(row=0, column=1, padx=5)

        # --- Actions ---
        action_frame = ttk.Frame(tab)
        action_frame.grid(row=2, column=0, sticky="ew", pady=10)
        action_frame.columnconfigure(0, weight=1)

        self.import_btn = tk.Button(action_frame, text="Import into Traktor", command=self._do_import, width=20,
                                     bg="#c0392b", fg="white", font=('', 9, 'bold'))
        self.import_btn.pack(side=tk.RIGHT, padx=10)

    def _create_results_panel(self, main):
        results_frame = ttk.LabelFrame(main, text="Results")
        results_frame.grid(row=3, column=0, sticky="nsew", padx=5, pady=5)
        results_frame.columnconfigure(0, weight=1)
        results_frame.rowconfigure(0, weight=1)

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
        notebook.add(log_tab, text="Log")
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

    def _default_export_dir(self) -> str:
        nml_path = self.nml_path_var.get() if hasattr(self, "nml_path_var") else ""
        if nml_path:
            return os.path.join(os.path.dirname(nml_path), EXPORT_SUBFOLDER)
        return os.path.join(os.path.expanduser("~"), "Desktop", EXPORT_SUBFOLDER)

    def _initialize_default_paths(self):
        settings = self.config_manager.settings

        if settings.collection_nml_path and os.path.exists(settings.collection_nml_path):
            self.nml_path_var.set(settings.collection_nml_path)
        else:
            for candidate in self._default_nml_candidates():
                if os.path.exists(candidate):
                    self.nml_path_var.set(candidate)
                    break

        self.output_dir_var.set(settings.export_output_dir or self._default_export_dir())
        self.selection_mode.set(settings.selection_mode)

        if settings.this_machine:
            self.machine_profile = MachineProfile.from_dict(settings.this_machine)
        else:
            self.machine_profile = MachineProfile(name=socket.gethostname())
        self._update_machine_summary()

        if self.nml_path_var.get():
            self._load_nml(silent=True)

    @staticmethod
    def _default_nml_candidates() -> List[str]:
        docs = os.path.join(os.path.expanduser("~"), "Documents", "Native Instruments")
        candidates = []
        if os.path.isdir(docs):
            for entry in sorted(os.listdir(docs), reverse=True):
                if entry.lower().startswith("traktor"):
                    candidates.append(os.path.join(docs, entry, "collection.nml"))
        return candidates

    def _save_settings(self):
        selected = self._get_selected_display_names()
        self.config_manager.update_settings(
            collection_nml_path=self.nml_path_var.get(),
            this_machine=self.machine_profile.to_dict(),
            export_output_dir=self.output_dir_var.get(),
            selected_playlists=selected,
            selection_mode=self.selection_mode.get(),
            playlist_presets=self.config_manager.settings.playlist_presets,
            active_preset=self.preset_var.get(),
        )
        self.status_var.set("Settings saved")
        messagebox.showinfo("Settings Saved", "Configuration saved successfully!")

    def _save_settings_silent(self):
        selected = self._get_selected_display_names()
        self.config_manager.update_settings(
            collection_nml_path=self.nml_path_var.get(),
            this_machine=self.machine_profile.to_dict(),
            export_output_dir=self.output_dir_var.get(),
            selected_playlists=selected,
            selection_mode=self.selection_mode.get(),
        )

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
            if self._import_nodes:
                self._refresh_import_tree()
            self.status_var.set(f"Loaded {len(self.all_nodes)} playlists/smart lists")
        except Exception as e:
            logger.error(f"Failed to load NML: {e}")
            self.status_var.set("Failed to load collection.nml")
            if not silent:
                messagebox.showerror("Error", f"Could not load collection.nml:\n{e}")

    # ------------------------------------------------------------------
    # This Machine profile
    # ------------------------------------------------------------------

    def _update_machine_summary(self):
        if self.machine_profile.is_configured:
            self.machine_summary_var.set(
                f"{self.machine_profile.name}: {self.machine_profile.volume} + {self.machine_profile.dir_prefix}"
            )
        else:
            self.machine_summary_var.set("Not set up yet - click 'Set up / Edit...'")

    def _setup_machine_profile(self):
        dialog = ProfileEditorDialog(self.root, self, profile=self.machine_profile)
        if dialog.result:
            self.machine_profile = dialog.result
            self.config_manager.update_settings(this_machine=self.machine_profile.to_dict())
            self._update_machine_summary()
            self._filter_playlists()
            if self._import_nodes:
                self._refresh_import_tree()

    # ------------------------------------------------------------------
    # Playlist tree / selection (Export tab)
    # ------------------------------------------------------------------

    def _in_library_display(self, node: PlaylistNode) -> str:
        if node.kind == "SMARTLIST":
            return "n/a (dynamic)"
        if not self.machine_profile.is_configured:
            return "-"
        keys = playlist_track_keys(node)
        if not keys:
            return "0/0"
        in_lib = sum(1 for k in keys if k.startswith(self.machine_profile.root_key))
        return f"{in_lib}/{len(keys)}"

    def _filter_playlists(self, *_args):
        filter_text = self.filter_var.get().lower()
        selected_names = {
            self.playlist_tree.item(i, "text") for i in self.playlist_tree.selection()
        }

        for item in self.playlist_tree.get_children():
            self.playlist_tree.delete(item)

        for node in self.all_nodes:
            name = node.display_name
            if filter_text and filter_text not in name.lower():
                continue
            kind_label = "Playlist" if node.kind == "PLAYLIST" else "Smart List"
            count = playlist_track_count(node)
            count_display = "dynamic" if count is None else str(count)
            in_lib_display = self._in_library_display(node)

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
        if not self.machine_profile.is_configured:
            messagebox.showerror("Export", "Set up 'This Machine' first (button above).")
            return

        nodes = self._get_nodes_to_export()
        if not nodes:
            messagebox.showwarning("Export", "No playlists/smart lists selected.")
            return

        output_dir = self.output_dir_var.get() or self._default_export_dir()
        os.makedirs(output_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in self.machine_profile.name) or "machine"
        output_path = os.path.join(output_dir, f"PlaylistSync_{safe_name}_{timestamp}.nml")

        self.status_var.set("Exporting...")
        self.root.update_idletasks()

        try:
            source_root = self.nml_tree.getroot()
            export_root, stats = build_export_root(source_root, nodes, self.entry_index, self.machine_profile)
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
                f"not under {self.machine_profile.name}'s registered library root, so they can't be relocated."
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
            f"Opening the folder so you can copy it to a USB stick.",
        )
        open_path(output_dir)
        self._save_settings_silent()

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _browse_import_file(self):
        initial_dir = self._default_export_dir()
        filename = filedialog.askopenfilename(
            title="Select an exported Traktor Playlist Sync file",
            initialdir=initial_dir if os.path.isdir(initial_dir) else None,
            filetypes=[("Traktor Playlist Sync", "*.nml"), ("All Files", "*.*")],
        )
        if filename:
            self.import_path_var.set(filename)
            self._load_import_file(filename)

    def _load_import_file(self, path: str):
        try:
            tree = load_nml(path)
            root = tree.getroot()
            src_profile = read_export_metadata(root)
            if src_profile is None:
                raise ValueError(
                    "This file has no Playlist Sync source info - it wasn't produced by this tool's Export."
                )
            collection_el = get_collection_element(root)
            if collection_el is None:
                raise ValueError("No <COLLECTION> element found - is this a valid export file?")
            entry_index = build_entry_index(collection_el)
            playlists_root = get_playlists_root_node(root)
            nodes = walk_playlist_nodes(playlists_root) if playlists_root is not None else []
        except Exception as e:
            logger.error(f"Failed to load import file: {e}")
            messagebox.showerror("Import", f"Could not read that file:\n{e}")
            self.import_info_var.set("Failed to load file")
            return

        self._import_entry_index = entry_index
        self._import_nodes = nodes
        self._import_src_profile = src_profile

        meta = root.find(PIPELINEMETA_TAG)
        exported_at = meta.get("EXPORTED_AT", "?") if meta is not None else "?"
        playlist_count = sum(1 for n in nodes if n.kind == "PLAYLIST")
        smart_count = sum(1 for n in nodes if n.kind == "SMARTLIST")
        self.import_info_var.set(
            f"From: {src_profile.name} ({src_profile.volume}{src_profile.dir_prefix}) - "
            f"exported {exported_at} - {playlist_count} playlist(s), {smart_count} smart list(s)"
        )
        self._refresh_import_tree()
        self.status_var.set(f"Loaded import file: {os.path.basename(path)}")

    def _refresh_import_tree(self):
        for item in self.import_tree.get_children():
            self.import_tree.delete(item)

        existing_names = {n.display_name for n in self.all_nodes}
        for node in self._import_nodes:
            kind_label = "Playlist" if node.kind == "PLAYLIST" else "Smart List"
            count = playlist_track_count(node)
            count_display = "dynamic" if count is None else str(count)
            action = "Replace" if node.display_name in existing_names else "New"
            item_id = self.import_tree.insert(
                "", "end", text=node.display_name, values=(kind_label, count_display, action)
            )
            self.import_tree.selection_add(item_id)

        self._update_import_summary()

    def _select_all_import(self):
        self.import_tree.selection_set(self.import_tree.get_children())
        self._update_import_summary()

    def _clear_all_import(self):
        self.import_tree.selection_remove(self.import_tree.selection())
        self._update_import_summary()

    def _update_import_summary(self):
        total = len(self.import_tree.get_children())
        selected = len(self.import_tree.selection())
        if total == 0:
            self.import_selection_summary.set("No file loaded")
        else:
            self.import_selection_summary.set(f"Will import {selected}/{total}")
        self.import_btn.config(state=tk.NORMAL if selected > 0 else tk.DISABLED)

    def _get_import_nodes_to_merge(self) -> List[PlaylistNode]:
        selected_names = {self.import_tree.item(i, "text") for i in self.import_tree.selection()}
        return [n for n in self._import_nodes if n.display_name in selected_names]

    def _do_import(self):
        if not self._import_nodes:
            messagebox.showerror("Import", "Load an exported file first.")
            return
        if not self.machine_profile.is_configured:
            messagebox.showerror("Import", "Set up 'This Machine' first (button above).")
            return
        if self.nml_tree is None or not self._loaded_nml_path:
            messagebox.showerror("Import", "Load this machine's collection.nml first (Source field above).")
            return

        nodes = self._get_import_nodes_to_merge()
        if not nodes:
            messagebox.showwarning("Import", "No playlists/smart lists selected to import.")
            return

        running = running_traktor_processes()
        if running:
            messagebox.showerror(
                "Traktor is running",
                f"Close Traktor first ({', '.join(running)} is running) - merging into collection.nml while "
                f"Traktor has it open risks your changes being overwritten when Traktor exits.",
            )
            return

        existing_names = {n.display_name for n in self.all_nodes}
        replace_count = sum(1 for n in nodes if n.display_name in existing_names)
        if not messagebox.askyesno(
            "Import",
            f"This will merge {len(nodes)} playlist(s)/smart list(s) directly into:\n{self._loaded_nml_path}\n\n"
            f"{replace_count} will replace an existing playlist of the same name; "
            f"{len(nodes) - replace_count} are new.\n\n"
            f"A backup of your current collection.nml will be made first. Continue?",
        ):
            return

        self.status_var.set("Merging into collection.nml...")
        self.root.update_idletasks()

        try:
            backup_path = backup_file(self._loaded_nml_path)
        except Exception as e:
            logger.error(f"Backup failed: {e}")
            messagebox.showerror("Import", f"Could not create a backup - aborting, nothing was changed:\n{e}")
            self.status_var.set("Import aborted")
            return

        try:
            # Merge against a fresh copy read straight from disk, independent of
            # self.nml_tree, so a failed merge never leaves the live in-memory
            # state (used by the Export tab) partially mutated.
            fresh_tree = load_nml(self._loaded_nml_path)
            fresh_root = fresh_tree.getroot()
            fresh_collection_el = get_collection_element(fresh_root)
            fresh_playlists_root = get_playlists_root_node(fresh_root)
            if fresh_collection_el is None or fresh_playlists_root is None:
                raise ValueError("Destination collection.nml is missing <COLLECTION> or <PLAYLISTS> - unexpected shape.")
            fresh_entry_index = build_entry_index(fresh_collection_el)

            stats = ImportStats()
            for node in nodes:
                merge_node_into_collection(
                    node, self._import_entry_index, fresh_playlists_root, fresh_collection_el,
                    fresh_entry_index, self._import_src_profile, self.machine_profile, stats,
                )

            write_nml_atomic(fresh_root, self._loaded_nml_path)
        except Exception as e:
            logger.error(f"Import failed: {e}")
            import traceback
            self._log(f"ERROR: {e}\n{traceback.format_exc()}")
            messagebox.showerror(
                "Import",
                f"Import failed:\n{e}\n\nYour collection.nml was not modified - nothing was written except "
                f"the backup at:\n{backup_path}",
            )
            self.status_var.set("Import failed")
            return

        self._log(f"Imported into: {self._loaded_nml_path}")
        self._log(f"Backup saved to: {backup_path}")
        self._log(f"Playlists: {stats.playlists_added} added, {stats.playlists_replaced} replaced")
        self._log(f"Smart lists: {stats.smartlists_added} added, {stats.smartlists_replaced} replaced")
        self._log(f"Tracks merged: {stats.tracks_merged}")
        if stats.tracks_skipped_outside_library:
            self._log(
                f"WARNING: {stats.tracks_skipped_outside_library} track(s) skipped - outside "
                f"{self._import_src_profile.name}'s registered library root."
            )
        if stats.tracks_missing_from_export:
            self._log(
                f"WARNING: {stats.tracks_missing_from_export} track reference(s) missing from the export file's collection."
            )

        added = stats.playlists_added + stats.smartlists_added
        replaced = stats.playlists_replaced + stats.smartlists_replaced
        self.status_var.set(f"Imported {len(nodes)} playlist(s)/smart list(s)")
        messagebox.showinfo(
            "Import Complete",
            f"Merged into:\n{self._loaded_nml_path}\n\n"
            f"{added} added, {replaced} replaced, {stats.tracks_merged} track(s).\n\n"
            f"Backup saved to:\n{backup_path}",
        )
        self._load_nml(silent=True)


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
