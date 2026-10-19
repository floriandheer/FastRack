#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The stem files themselves: find them, say how much room they take, and clear them out.

Traktor keeps a stem file per track as <Stems folder>/<nnn>/<ID>.stem.mp4 (and works on new ones in a temp folder
first). Only files of exactly that shape are ever listed, so nothing else in those folders - or anywhere else -
can be touched: no other file name, no other depth, no symlinks. On a Mac the files go to the Trash through Finder
(Python itself is not allowed into ~/.Trash), so a mistake can be undone; permanent deletion is a separate choice.
"""

import glob
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from shared_traktor_stems import LinkAudit, audit_links, scan_stem_flags, surviving_stems

STEM_SUFFIX = ".stem.mp4"
_NUMBERED_FOLDER = re.compile(r"^\d{3}$")
_STORED_AT = re.compile(r'stem file stored to "(.*?)[\\/]\d{3}[\\/][A-Za-z0-9]+\.stem\.mp4"')

StemRoot = Tuple[str, int]      # (folder, depth): 2 = <folder>/<nnn>/<ID>.stem.mp4, 1 = <folder>/<name>.stem.mp4


@dataclass(frozen=True)
class StemFile:
    path: str
    size: int


def human_size(size: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def default_stem_roots(traktor_docs_dir: str) -> List[StemRoot]:
    """Where Traktor's stems are: the folders its own log says it stored them in, plus the usual places, that exist."""
    home = os.path.expanduser("~")
    stores: List[str] = [os.path.join(home, "Music", "Traktor", "Stems"), os.path.join(home, "Music", "Stems")]
    for log in _logs(traktor_docs_dir):
        with open(log, "rb") as f:
            for found in _STORED_AT.finditer(f.read().decode("utf-8", "replace")):
                stores.append(found.group(1))
    temp = [os.path.join(home, "Library", "Application Support", "Native Instruments", "Traktor", "StemFilesTempDir"),
            os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")),
                         "Native Instruments", "Traktor", "StemFilesTempDir")]
    roots: List[StemRoot] = []
    for folder, depth in [(s, 2) for s in stores] + [(t, 1) for t in temp]:
        folder = os.path.normpath(folder)
        if os.path.isdir(folder) and (folder, depth) not in roots:
            roots.append((folder, depth))
    return roots


def _logs(traktor_docs_dir: str) -> List[str]:
    return sorted(glob.glob(os.path.join(traktor_docs_dir, "Traktor *", "Logs", "Traktor.log")))


def _stem_files_in(folder: str) -> List[StemFile]:
    found = []
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return found
    for entry in entries:
        try:
            if entry.is_file(follow_symlinks=False) and entry.name.endswith(STEM_SUFFIX):
                found.append(StemFile(entry.path, entry.stat(follow_symlinks=False).st_size))
        except OSError:
            continue
    return found


def find_stem_files(roots: List[StemRoot]) -> List[StemFile]:
    """Every stem file under the roots, once each, sorted by path."""
    files: Dict[str, StemFile] = {}
    for folder, depth in roots:
        if depth == 1:
            candidates = [folder]
        else:
            try:
                candidates = [e.path for e in os.scandir(folder)
                              if _NUMBERED_FOLDER.match(e.name) and e.is_dir(follow_symlinks=False)]
            except OSError:
                candidates = []
        for candidate in candidates:
            for stem in _stem_files_in(candidate):
                files[stem.path] = stem
    return sorted(files.values(), key=lambda f: f.path)


# ----------------------------------------------------------------------
# Status of a collection and its stems
# ----------------------------------------------------------------------

@dataclass
class StemStatus:
    tracks: int = 0
    linked: int = 0
    files: int = 0
    size: int = 0
    audit: LinkAudit = field(default_factory=LinkAudit)
    roots: List[StemRoot] = field(default_factory=list)

    @property
    def headline(self) -> str:
        return (f"{self.tracks} tracks  |  {self.linked} linked to stems  |  "
                f"{self.files} stem files on disk ({human_size(self.size)})")

    @property
    def detail(self) -> str:
        return (f"Linked, but no stem file found: {len(self.audit.broken)}   |   "
                f"Stem file found, but not linked: {len(self.audit.files_not_linked)}")


def collection_status(collection: str) -> StemStatus:
    docs = os.path.dirname(os.path.dirname(os.path.abspath(collection)))
    with open(collection, "rb") as f:
        data = f.read()
    scan = scan_stem_flags(data)
    roots = default_stem_roots(docs)
    files = find_stem_files(roots)
    return StemStatus(scan.tracks, scan.flagged, len(files), sum(f.size for f in files),
                      audit_links(data, surviving_stems(docs)), roots)


# ----------------------------------------------------------------------
# Clearing them out
# ----------------------------------------------------------------------

class CleanupError(Exception):
    pass


def can_trash() -> bool:
    return sys.platform == "darwin"


def trash_with_finder(paths: List[str], chunk: int = 40,
                      run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run) -> None:
    """Move files to the Trash through Finder (keeps 'Put Back'). Raises CleanupError; files already moved stay moved."""
    for start in range(0, len(paths), chunk):
        items = ", ".join('POSIX file "%s"' % p.replace("\\", "\\\\").replace('"', '\\"') for p in paths[start:start + chunk])
        done = run(["osascript", "-e", f'tell application "Finder" to delete {{{items}}}'],
                   capture_output=True, text=True, timeout=300)
        if done.returncode != 0:
            err = done.stderr.strip()
            if "-1743" in err or "not authorized" in err.lower():
                raise CleanupError("macOS blocked control of Finder. Allow it under System Settings > Privacy & Security "
                                   f"> Automation, then try again. ({start} of {len(paths)} files were moved before this.)")
            raise CleanupError(f"Finder could not move the files to the Trash: {err or 'unknown error'} "
                               f"({start} of {len(paths)} files were moved before this.)")


@dataclass
class DeleteResult:
    removed: int = 0
    size: int = 0
    permanent: bool = False
    failed: List[Tuple[str, str]] = field(default_factory=list)


def delete_stem_files(files: List[StemFile], permanent: bool = False, log: Callable[[str], None] = lambda _m: None,
                      trash: Callable[[List[str]], None] = trash_with_finder) -> DeleteResult:
    """Move the stem files to the Trash (macOS) or delete them for good (`permanent`, and the only way elsewhere).
    Numbered stem sub-folders left empty are removed too."""
    if not permanent and not can_trash():
        raise CleanupError("Moving to the Trash is only set up for macOS - choose permanent deletion instead.")
    result = DeleteResult(permanent=permanent)
    safe = [f for f in files if f.path.endswith(STEM_SUFFIX) and not os.path.islink(f.path) and os.path.isfile(f.path)]
    if permanent:
        for stem in safe:
            try:
                os.remove(stem.path)
                result.removed, result.size = result.removed + 1, result.size + stem.size
            except OSError as e:
                result.failed.append((stem.path, str(e)))
                log(f"  could not delete {stem.path}: {e}")
    else:
        trash([f.path for f in safe])
        result.removed, result.size = len(safe), sum(f.size for f in safe)
    for parent in {os.path.dirname(f.path) for f in files}:
        if _NUMBERED_FOLDER.match(os.path.basename(parent)):
            try:
                os.rmdir(parent)               # only succeeds when empty
            except OSError:
                pass
    return result
