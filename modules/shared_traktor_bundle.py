"""Transfer bundle for moving a DJ setup between machines (PC -> drive -> laptop/Mac).

A bundle is a plain folder on the drive:

    <bundle>/DJ Library/            the music files (flat, as TraktorSync writes them)
    <bundle>/DJ Library.xml         the iTunes XML, with the SOURCE machine's paths
    <bundle>/PlaylistSync/*.nml     optional Traktor playlist export (raw source paths)
    <bundle>/bundle.json            manifest

Nothing is rewritten on export. Path rewriting happens on the destination:
the XML's track locations are pointed at the destination's own DJ Library
folder (`localize_itunes_xml`), and the Traktor playlist file is rewritten by
Traktor Playlist Sync's own import using the destination's machine profile.
"""

import datetime
import json
import os
import posixpath
import shutil
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, List, Optional

MUSIC_SUBFOLDER = "DJ Library"
XML_NAME = "DJ Library.xml"
PLAYLIST_SUBFOLDER = "PlaylistSync"
MANIFEST_NAME = "bundle.json"

_IGNORED_FILES = {"desktop.ini", "thumbs.db", ".ds_store"}

Log = Callable[[str], None]


@dataclass
class CopyStats:
    copied: int = 0
    skipped: int = 0
    pruned: int = 0
    errors: int = 0


@dataclass
class ExportResult:
    music: CopyStats = field(default_factory=CopyStats)
    xml_copied: bool = False
    playlist_file: Optional[str] = None


@dataclass
class ImportResult:
    music: CopyStats = field(default_factory=CopyStats)
    xml_path: Optional[str] = None
    xml_backup: Optional[str] = None
    tracks_localized: int = 0
    tracks_missing: int = 0
    playlist_file: Optional[str] = None


# ----------------------------------------------------------------------
# File copying
# ----------------------------------------------------------------------

def _library_files(folder: str) -> List[str]:
    """Top-level files of a DJ Library folder; subfolders such as _Removed are not part of it."""
    try:
        names = os.listdir(folder)
    except OSError:
        return []
    return sorted(
        n for n in names
        if os.path.isfile(os.path.join(folder, n)) and n.lower() not in _IGNORED_FILES
    )


def needs_copy(src: str, dst: str) -> bool:
    """True when dst is missing or differs from src (size, or src is newer)."""
    try:
        dst_stat = os.stat(dst)
    except OSError:
        return True
    src_stat = os.stat(src)
    return src_stat.st_size != dst_stat.st_size or src_stat.st_mtime > dst_stat.st_mtime + 2


def sync_folder(src_dir: str, dst_dir: str, log: Log, prune: bool = False,
                overwrite: bool = False) -> CopyStats:
    """Copy new/changed top-level files from src_dir into dst_dir. With prune,
    files in dst_dir that no longer exist in src_dir are deleted."""
    stats = CopyStats()
    os.makedirs(dst_dir, exist_ok=True)
    source_names = _library_files(src_dir)
    total = len(source_names)
    for index, name in enumerate(source_names, 1):
        src, dst = os.path.join(src_dir, name), os.path.join(dst_dir, name)
        try:
            if overwrite or needs_copy(src, dst):
                shutil.copy2(src, dst)
                stats.copied += 1
                log(f"[{index}/{total}] Copied {name}")
            else:
                stats.skipped += 1
        except OSError as e:
            stats.errors += 1
            log(f"ERROR copying {name}: {e}")

    if prune:
        keep = set(source_names)
        for name in _library_files(dst_dir):
            if name in keep:
                continue
            try:
                os.remove(os.path.join(dst_dir, name))
                stats.pruned += 1
                log(f"Removed {name} (no longer in the source library)")
            except OSError as e:
                stats.errors += 1
                log(f"ERROR removing {name}: {e}")
    return stats


# ----------------------------------------------------------------------
# iTunes XML localisation
# ----------------------------------------------------------------------

def path_to_itunes_url(path: str, platform: str = sys.platform) -> str:
    """Local filesystem path -> the file:// URL form iTunes XML uses on `platform`."""
    p = path.replace("\\", "/")
    if platform == "win32":
        if not p.startswith("/"):
            p = "/" + p
        return "file://localhost" + urllib.parse.quote(p, safe="/:")
    if not p.startswith("/"):
        p = "/" + p
    return "file://" + urllib.parse.quote(p, safe="/:")


def _url_basename(url: str) -> str:
    rest = url
    for prefix in ("file://localhost", "file://"):
        if rest.startswith(prefix):
            rest = rest[len(prefix):]
            break
    return posixpath.basename(urllib.parse.unquote(rest).replace("\\", "/"))


def localize_itunes_xml(src_xml: str, dst_xml: str, dest_dj_library: str,
                        platform: str = sys.platform, available: Optional[set] = None):
    """Write a copy of `src_xml` whose track locations (and Music Folder) point
    into `dest_dj_library` on this machine. The DJ Library is flat, so only the
    file name of each location is kept. Returns (localized, missing) counts,
    where `missing` are tracks not in `available` (if given)."""
    with open(src_xml, "r", encoding="utf-8") as f:
        root = ET.fromstring(f.read())

    library_url = path_to_itunes_url(dest_dj_library, platform).rstrip("/") + "/"
    localized = missing = 0
    for parent in root.iter():
        children = list(parent)
        for i, child in enumerate(children[:-1]):
            nxt = children[i + 1]
            if child.tag != "key" or nxt.tag != "string":
                continue
            if child.text == "Music Folder":
                nxt.text = library_url
            elif child.text == "Location" and nxt.text:
                name = _url_basename(nxt.text)
                nxt.text = path_to_itunes_url(os.path.join(dest_dj_library, name), platform)
                localized += 1
                if available is not None and name not in available:
                    missing += 1

    with open(dst_xml, "w", encoding="utf-8") as f:
        f.write(ET.tostring(root, encoding="utf-8").decode())
    return localized, missing


# ----------------------------------------------------------------------
# Export / import
# ----------------------------------------------------------------------

def export_bundle(dj_library_dir: str, itunes_xml: str, bundle_dir: str, log: Log,
                  playlist_file: Optional[str] = None, machine_name: str = "",
                  prune: bool = False) -> ExportResult:
    """Copy everything needed on another machine into `bundle_dir`."""
    if not os.path.isdir(dj_library_dir):
        raise FileNotFoundError(f"DJ Library folder not found: {dj_library_dir}")
    if not os.path.isfile(itunes_xml):
        raise FileNotFoundError(f"XML not found: {itunes_xml}")
    os.makedirs(bundle_dir, exist_ok=True)

    result = ExportResult()
    log(f"Copying music to {os.path.join(bundle_dir, MUSIC_SUBFOLDER)} ...")
    result.music = sync_folder(dj_library_dir, os.path.join(bundle_dir, MUSIC_SUBFOLDER), log, prune=prune)

    shutil.copy2(itunes_xml, os.path.join(bundle_dir, XML_NAME))
    result.xml_copied = True
    log(f"Copied {XML_NAME}")

    playlist_rel = None
    if playlist_file and os.path.isfile(playlist_file):
        playlist_dir = os.path.join(bundle_dir, PLAYLIST_SUBFOLDER)
        os.makedirs(playlist_dir, exist_ok=True)
        shutil.copy2(playlist_file, playlist_dir)
        playlist_rel = f"{PLAYLIST_SUBFOLDER}/{os.path.basename(playlist_file)}"
        result.playlist_file = os.path.join(playlist_dir, os.path.basename(playlist_file))
        log(f"Copied Traktor playlists: {os.path.basename(playlist_file)}")

    manifest = {
        "version": 1,
        "created": datetime.datetime.now().isoformat(timespec="seconds"),
        "machine": machine_name,
        "platform": sys.platform,
        "music_files": len(_library_files(os.path.join(bundle_dir, MUSIC_SUBFOLDER))),
        "xml": XML_NAME,
        "playlist_file": playlist_rel,
    }
    with open(os.path.join(bundle_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
    return result


def read_manifest(bundle_dir: str) -> Optional[dict]:
    try:
        with open(os.path.join(bundle_dir, MANIFEST_NAME), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def describe_bundle(bundle_dir: str) -> str:
    """One-line summary for the UI, or why this folder isn't a bundle."""
    if not bundle_dir or not os.path.isdir(bundle_dir):
        return "Folder not found"
    manifest = read_manifest(bundle_dir)
    if manifest is None:
        return "No bundle here (bundle.json missing)"
    playlists = "with Traktor playlists" if manifest.get("playlist_file") else "no Traktor playlists"
    return (f"Bundle from {manifest.get('machine') or '?'} - {manifest.get('created', '?')} - "
            f"{manifest.get('music_files', '?')} track(s), {playlists}")


def import_bundle(bundle_dir: str, dest_dj_library: str, dest_xml: str, log: Log,
                  platform: str = sys.platform, overwrite: bool = False) -> ImportResult:
    """Bring a bundle onto this machine: copy music, then write the XML with
    this machine's paths. Traktor playlists are not merged here - the result
    carries the playlist file for Traktor Playlist Sync's import (which has the
    preview and conflict handling)."""
    manifest = read_manifest(bundle_dir)
    if manifest is None:
        raise FileNotFoundError(f"{bundle_dir} is not a bundle (no {MANIFEST_NAME})")
    src_music = os.path.join(bundle_dir, MUSIC_SUBFOLDER)
    src_xml = os.path.join(bundle_dir, manifest.get("xml", XML_NAME))
    if not os.path.isdir(src_music) or not os.path.isfile(src_xml):
        raise FileNotFoundError(f"Bundle is incomplete: needs '{MUSIC_SUBFOLDER}' and '{XML_NAME}'")

    result = ImportResult()
    log(f"Copying music into {dest_dj_library} ...")
    result.music = sync_folder(src_music, dest_dj_library, log, overwrite=overwrite)

    os.makedirs(os.path.dirname(os.path.abspath(dest_xml)), exist_ok=True)
    if os.path.exists(dest_xml):
        stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        result.xml_backup = f"{dest_xml}.bak-{stamp}"
        shutil.copy2(dest_xml, result.xml_backup)
        log(f"Backed up existing XML to {result.xml_backup}")

    available = set(_library_files(dest_dj_library))
    result.tracks_localized, result.tracks_missing = localize_itunes_xml(
        src_xml, dest_xml, dest_dj_library, platform=platform, available=available
    )
    result.xml_path = dest_xml
    log(f"Wrote {dest_xml} with paths for this machine ({result.tracks_localized} track(s))")
    if result.tracks_missing:
        log(f"WARNING: {result.tracks_missing} track(s) in the XML are not in {dest_dj_library}")

    playlist_rel = manifest.get("playlist_file")
    if playlist_rel:
        candidate = os.path.join(bundle_dir, *playlist_rel.split("/"))
        if os.path.isfile(candidate):
            result.playlist_file = candidate
    return result
