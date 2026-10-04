"""
Sandbox Versioning
Author: Florian Dheer
Description: Sibling-file versioning for the Sandbox browser. Versions live
next to each other in the same folder as ``<name>_v002.<ext>``; a file with
no ``_vNNN`` suffix counts as version 1. Pure filesystem helpers, no UI and
no sidecar state, so versions stay visible (and usable) in Explorer.
"""

import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

_VERSION_RE = re.compile(r"^(?P<base>.+)_v(?P<num>\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class VersionInfo:
    path: str
    number: int
    modified: datetime
    size: int
    is_latest: bool = False

    @property
    def label(self) -> str:
        return f"v{self.number:03d}"


def split_version(filename: str) -> Tuple[str, int, str]:
    """``report_v003.docx`` -> ("report", 3, ".docx"); an unsuffixed
    ``report.docx`` -> ("report", 1, ".docx")."""
    stem, ext = os.path.splitext(filename)
    m = _VERSION_RE.match(stem)
    if m:
        return m.group("base"), int(m.group("num")), ext
    return stem, 1, ext


def _group_key(directory: str, base: str, ext: str) -> Tuple[str, str, str]:
    return os.path.normcase(directory), base.lower(), ext.lower()


def group_key(path: str) -> Tuple[str, str, str]:
    base, _num, ext = split_version(os.path.basename(path))
    return _group_key(os.path.dirname(path), base, ext)


def _version_info(path: str, number: int) -> Optional[VersionInfo]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return VersionInfo(path, number, datetime.fromtimestamp(st.st_mtime), st.st_size)


def collapse_to_latest(file_paths: List[str]) -> List[Tuple[str, int]]:
    """Group a folder's files into version families and return
    ``(latest_path, version_count)`` per family, ordered by first appearance.
    Used by the tree so only the latest version of each document shows."""
    groups: Dict[Tuple[str, str, str], List[Tuple[int, bool, str]]] = {}
    order: List[Tuple[str, str, str]] = []
    for path in file_paths:
        base, num, ext = split_version(os.path.basename(path))
        key = _group_key(os.path.dirname(path), base, ext)
        if key not in groups:
            groups[key] = []
            order.append(key)
        # explicit _vNNN beats the implicit v1 on a tie
        explicit = bool(_VERSION_RE.match(os.path.splitext(os.path.basename(path))[0]))
        groups[key].append((num, explicit, path))
    return [(max(groups[k])[2], len(groups[k])) for k in order]


def list_versions(path: str) -> List[VersionInfo]:
    """All versions of the document ``path`` belongs to, newest first."""
    directory = os.path.dirname(path)
    key = group_key(path)
    found: List[VersionInfo] = []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    for name in names:
        full = os.path.join(directory, name)
        if not os.path.isfile(full):
            continue
        base, num, ext = split_version(name)
        if _group_key(directory, base, ext) != key:
            continue
        info = _version_info(full, num)
        if info:
            found.append(info)
    found.sort(key=lambda v: (v.number, v.path), reverse=True)
    if found:
        found[0] = VersionInfo(found[0].path, found[0].number, found[0].modified,
                               found[0].size, is_latest=True)
    return found


def latest_version(path: str) -> Optional[VersionInfo]:
    versions = list_versions(path)
    return versions[0] if versions else None


def version_up(path: str) -> str:
    """Copy the current latest version of ``path``'s document to the next
    number and return the new file's path. The previous versions are left
    untouched."""
    latest = latest_version(path)
    if latest is None:
        raise FileNotFoundError(path)
    base, _num, ext = split_version(os.path.basename(latest.path))
    directory = os.path.dirname(latest.path)
    number = latest.number + 1
    while True:
        target = os.path.join(directory, f"{base}_v{number:03d}{ext}")
        if not os.path.exists(target):
            break
        number += 1
    shutil.copy(latest.path, target)  # new mtime = when the version was made
    return target
