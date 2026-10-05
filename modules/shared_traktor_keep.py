#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Traktor-aware keep list for the DJ Library sync.

Traktor playlists (e.g. "Preparation") keep pointing at DJ Library files after a track rolls out of the
MusicBee playlists that feed the sync. This module reads what Traktor still references so the sync can
(1) not delete those files, (2) bring missing ones back from MusicBee, and (3) quarantine instead of delete.
"""

import os
import re
import shutil
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Tuple

KEEP_PLAYLIST_NAME = "Traktor Keep (auto)"
QUARANTINE_FOLDER = "_Removed"


def norm_name(filename: str) -> str:
    return unicodedata.normalize("NFC", filename or "").casefold()


def _dir_key(path: str) -> str:
    return unicodedata.normalize("NFC", path or "").replace("/", "\\").rstrip("\\").casefold()


def _key(s: str) -> str:
    return unicodedata.normalize("NFC", s or "").casefold().strip()


def _squash(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return " ".join(re.findall(r"[^\W_]+", s))


def find_collection_nml() -> Optional[str]:
    """Newest 'Traktor <version>' collection.nml under ~/Documents/Native Instruments."""
    docs = os.path.join(os.path.expanduser("~"), "Documents", "Native Instruments")
    if not os.path.isdir(docs):
        return None
    names = [n for n in os.listdir(docs) if n.lower().startswith("traktor")]
    names.sort(key=lambda n: [(1, int(p)) if p.isdigit() else (0, p) for p in re.split(r"(\d+)", n)], reverse=True)
    for n in names:
        candidate = os.path.join(docs, n, "collection.nml")
        if os.path.isfile(candidate):
            return candidate
    return None


@dataclass(frozen=True)
class TraktorEntry:
    file: str
    title: str = ""
    artist: str = ""
    album: str = ""


@dataclass
class TraktorRefs:
    entries: List[TraktorEntry] = field(default_factory=list)
    playlist_refs: Dict[str, List[str]] = field(default_factory=dict)   # norm filename -> Traktor playlists


def _walk_playlists(folder: ET.Element, trail: Tuple[str, ...] = ()):
    subnodes = folder.find("SUBNODES")
    if subnodes is None:
        return
    for node in subnodes.findall("NODE"):
        name = node.get("NAME", "")
        kind = node.get("TYPE", "")
        if kind == "FOLDER":
            yield from _walk_playlists(node, trail + (name,))
        elif kind == "PLAYLIST":
            yield " / ".join(trail + (name,)), node


def load_refs(nml_path: str, dj_library: str) -> TraktorRefs:
    """Collection entries and playlist references that live directly in `dj_library`."""
    root = ET.parse(nml_path).getroot()
    lib = _dir_key(dj_library)
    refs = TraktorRefs()

    collection = root.find("COLLECTION")
    if collection is not None:
        for e in collection.findall("ENTRY"):
            loc = e.find("LOCATION")
            if loc is None or not loc.get("FILE"):
                continue
            directory = loc.get("VOLUME", "") + loc.get("DIR", "").replace("/:", "\\")
            if _dir_key(directory) != lib:
                continue
            album = e.find("ALBUM")
            refs.entries.append(TraktorEntry(
                loc.get("FILE"), e.get("TITLE", ""), e.get("ARTIST", ""),
                album.get("TITLE", "") if album is not None else ""))

    playlists = root.find("PLAYLISTS")
    top = playlists.find("NODE") if playlists is not None else None
    if top is not None:
        for label, node in _walk_playlists(top):
            playlist = node.find("PLAYLIST")
            if playlist is None:
                continue
            for entry in playlist.findall("ENTRY"):
                pk = entry.find("PRIMARYKEY")
                key = pk.get("KEY") if pk is not None else None
                if not key or "/:" not in key:
                    continue
                directory, filename = key.rsplit("/:", 1)
                if _dir_key(directory.replace("/:", "\\")) != lib:
                    continue
                names = refs.playlist_refs.setdefault(norm_name(filename), [])
                if label not in names:
                    names.append(label)
    return refs


def missing_referenced(refs: TraktorRefs, existing_files: Iterable[str]) -> List[TraktorEntry]:
    """Entries a Traktor playlist references whose file is not in the DJ Library folder (NFC + casefold)."""
    have = {norm_name(f) for f in existing_files}
    seen = set()
    out = []
    for e in refs.entries:
        k = norm_name(e.file)
        if k in have or k in seen or k not in refs.playlist_refs:
            continue
        seen.add(k)
        out.append(e)
    return out


def match_to_library(entries: Iterable[TraktorEntry],
                     library: Iterable[Tuple[str, str, str, str]]) -> Tuple[Dict[str, str], List[Tuple[TraktorEntry, str]]]:
    """Match Traktor entries to MusicBee tracks. `library` yields (track_id, artist, title, album).
    Returns ({norm filename: track_id}, [(entry, reason)]). Only unique matches are accepted."""
    by_at = defaultdict(list)
    by_title = defaultdict(list)
    album_of: Dict[str, str] = {}
    artist_tokens: Dict[str, set] = {}
    for tid, artist, title, album in library:
        by_at[(_key(artist), _key(title))].append(tid)
        by_title[_squash(title)].append(tid)
        album_of[tid] = _key(album)
        artist_tokens[tid] = set(_squash(artist).split())

    matched: Dict[str, str] = {}
    unmatched: List[Tuple[TraktorEntry, str]] = []
    for e in entries:
        cands = by_at.get((_key(e.artist), _key(e.title)), [])
        if len(cands) > 1 and e.album:
            cands = [c for c in cands if album_of[c] == _key(e.album)] or cands
        if not cands:
            same_title = by_title.get(_squash(e.title), [])
            wanted = set(_squash(e.artist).split())
            cands = [c for c in same_title if wanted & artist_tokens[c]] or (same_title if len(same_title) == 1 else [])
        if len(cands) == 1:
            matched[norm_name(e.file)] = cands[0]
        elif cands:
            unmatched.append((e, f"{len(cands)} possible tracks"))
        else:
            unmatched.append((e, "no matching MusicBee track"))
    return matched, unmatched


def move_to_quarantine(file_path: str, dj_library: str, date_label: str) -> str:
    """Move `file_path` to <dj_library>/_Removed/<date_label>/ (never overwrites); returns the new path."""
    target_dir = os.path.join(dj_library, QUARANTINE_FOLDER, date_label)
    os.makedirs(target_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(file_path))
    target = os.path.join(target_dir, base + ext)
    n = 1
    while os.path.exists(target):
        target = os.path.join(target_dir, f"{base} ({n}){ext}")
        n += 1
    shutil.move(file_path, target)
    return target
