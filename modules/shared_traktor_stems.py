#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Traktor stem links in collection.nml.

Traktor records "this track has a stem version" as one bit (0x40) of the track's <INFO FLAGS="...">
attribute (12 -> 76 once stems are generated). The stem file itself lives in Traktor's own Stems folder
(~/Music/Traktor/Stems/<nnn>/<ID>.stem.mp4) and is found from the track, not from the NML. Stem files never
travel with the music, so a track copied to another machine with that bit still set cannot load: Traktor
looks for stems that are not there. Clearing the bit is exactly what "Remove stems" in Traktor does.

Restoring goes the other way for the stem files that still exist. Traktor's own logs say which track each stem
file was made for ("Finished stem separation job for track <title> ... stored to <file>"), and that is the
only track-to-file mapping available, so a restore only touches tracks whose title identifies exactly one
track in the collection.
"""

import datetime
import glob
import html
import os
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

STEM_FLAG = 0x40

# An <INFO ...> tag; quoted values may contain '>' so they are skipped as a unit.
_INFO_TAG = re.compile(rb'<INFO\b(?:[^>"]|"[^"]*")*>')
_FLAGS_ATTR = re.compile(rb'(\s)FLAGS="(\d+)"')
_TAG_ATTR = re.compile(rb'\s([A-Z_0-9]+)="([^"]*)"')
_COLLECTION_ENTRY = re.compile(rb'(<ENTRY\b(?:[^>"]|"[^"]*")*>)(.*?)</ENTRY>', re.S)
_LOCATION_TAG = re.compile(rb'<LOCATION\b(?:[^>"]|"[^"]*")*>')
_STEM_JOB = re.compile(r'- Finished stem separation job for track "(.*?)" - result: successful: '
                       r'stem file stored to "(.*?)"')


# ----------------------------------------------------------------------
# Reading the collection
# ----------------------------------------------------------------------

@dataclass
class StemScan:
    tracks: int = 0       # <INFO> tags, i.e. tracks with metadata in the collection
    flagged: int = 0      # of those, tracks that claim a stem version


def scan_stem_flags(data: bytes) -> StemScan:
    result = StemScan()
    for tag in _INFO_TAG.finditer(data):
        result.tracks += 1
        flags = _FLAGS_ATTR.search(tag.group(0))
        if flags and int(flags.group(2)) & STEM_FLAG:
            result.flagged += 1
    return result


@dataclass(frozen=True)
class Track:
    title: str
    artist: str
    dir: str
    file: str
    flags: int                    # 0 when the attribute is absent
    info: Tuple[int, int]         # byte span of this track's <INFO ...> tag in the file

    @property
    def key(self) -> Tuple[str, str]:
        return (self.dir, self.file)

    @property
    def has_stems(self) -> bool:
        return bool(self.flags & STEM_FLAG)

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


def _attrs(tag: bytes) -> Dict[str, str]:
    return {m.group(1).decode("ascii"): html.unescape(m.group(2).decode("utf-8", "replace"))
            for m in _TAG_ATTR.finditer(tag)}


def read_tracks(data: bytes) -> List[Track]:
    """Every collection entry (playlist entries have no LOCATION and are skipped) with its byte span of INFO."""
    tracks = []
    for entry in _COLLECTION_ENTRY.finditer(data):
        body_start = entry.start(2)
        location = _LOCATION_TAG.search(entry.group(2))
        info = _INFO_TAG.search(entry.group(2))
        if not location or not info:
            continue
        head, loc = _attrs(entry.group(1)), _attrs(location.group(0))
        flags = _FLAGS_ATTR.search(info.group(0))
        tracks.append(Track(
            title=head.get("TITLE", ""), artist=head.get("ARTIST", ""),
            dir=loc.get("DIR", ""), file=loc.get("FILE", ""),
            flags=int(flags.group(2)) if flags else 0,
            info=(body_start + info.start(), body_start + info.end()),
        ))
    return tracks


# ----------------------------------------------------------------------
# Removing the links
# ----------------------------------------------------------------------

def _without_stem_flag(info_tag: bytes) -> bytes:
    """One <INFO ...> tag with the stem bit cleared; when no bit is left Traktor omits FLAGS, so this does too."""
    flags = _FLAGS_ATTR.search(info_tag)
    if not flags or not int(flags.group(2)) & STEM_FLAG:
        return info_tag
    remaining = int(flags.group(2)) & ~STEM_FLAG
    replacement = flags.group(1) + b'FLAGS="%d"' % remaining if remaining else b""
    return info_tag[:flags.start()] + replacement + info_tag[flags.end():]


def strip_stem_flags(data: bytes) -> Tuple[bytes, int]:
    """Clear the stem bit on every track; returns the new bytes and how many tracks changed.
    Only the FLAGS attribute is touched."""
    changed = 0

    def fix(match: "re.Match[bytes]") -> bytes:
        nonlocal changed
        tag = match.group(0)
        new = _without_stem_flag(tag)
        changed += new != tag
        return new

    return _INFO_TAG.sub(fix, data), changed


def remove_stem_flags(data: bytes, keys: Set[Tuple[str, str]]) -> Tuple[bytes, int]:
    """Like strip_stem_flags, but only for the tracks at these (DIR, FILE) locations."""
    pieces, cursor, changed = [], 0, 0
    for track in read_tracks(data):
        if track.key in keys and track.has_stems:
            start, end = track.info
            pieces += [data[cursor:start], _without_stem_flag(data[start:end])]
            cursor, changed = end, changed + 1
    pieces.append(data[cursor:])
    return b"".join(pieces), changed


def clear_stem_flag(entry: ET.Element) -> bool:
    """Same as strip_stem_flags for one parsed <ENTRY>. True when the entry had a stem link."""
    info = entry.find("INFO")
    if info is None:
        return False
    try:
        flags = int(info.get("FLAGS", "0"))
    except ValueError:
        return False
    if not flags & STEM_FLAG:
        return False
    flags &= ~STEM_FLAG
    if flags:
        info.set("FLAGS", str(flags))
    else:
        del info.attrib["FLAGS"]
    return True


# ----------------------------------------------------------------------
# Writing safely
# ----------------------------------------------------------------------

def _check_collection(data: bytes) -> None:
    if b"<NML" not in data[:1024] or b"<COLLECTION" not in data:
        raise ValueError("That doesn't look like a Traktor collection.nml.")


def _replace_with_backup(path: str, new_data: bytes) -> str:
    """Timestamped backup next to the file, then the new content swapped in atomically (a live collection is never
    left half-written). Refuses output that is no longer well-formed XML before anything is touched."""
    ET.fromstring(new_data)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    backup_path = f"{path}.bak-{stamp}"
    shutil.copy2(path, backup_path)

    tmp_path = f"{path}.tmp-{os.getpid()}"
    try:
        with open(tmp_path, "wb") as f:
            f.write(new_data)
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    return backup_path


@dataclass
class CleanResult:
    tracks: int
    flagged: int
    applied: bool
    backup_path: Optional[str] = None


def clean_collection(path: str, apply: bool, only_missing_in: Optional[Dict[str, List[str]]] = None) -> CleanResult:
    """Count (apply=False) or remove (apply=True) the stem links in a collection.nml. Close Traktor first.

    With `only_missing_in` (title -> stem files that exist, see surviving_stems) only the links that have no stem file
    are removed; links that cannot be judged because the title is shared by several tracks are left alone."""
    with open(path, "rb") as f:
        data = f.read()
    _check_collection(data)

    scan = scan_stem_flags(data)
    keys = None
    if only_missing_in is not None:
        keys = {t.key for t in audit_links(data, only_missing_in).broken}
        scan = StemScan(scan.tracks, len(keys))
    if not apply or not scan.flagged:
        return CleanResult(scan.tracks, scan.flagged, applied=False)

    new_data, changed = strip_stem_flags(data) if keys is None else remove_stem_flags(data, keys)
    return CleanResult(scan.tracks, changed, applied=True, backup_path=_replace_with_backup(path, new_data))


@dataclass
class LinkAudit:
    """How the stem links in a collection compare with the stem files that exist (matched by title, via the logs)."""
    linked: List[Track] = field(default_factory=list)
    broken: List[Track] = field(default_factory=list)              # linked, and no stem file is known for the title
    unsure: List[Track] = field(default_factory=list)              # linked, but the title is shared so it can't be told
    files_not_linked: List[Track] = field(default_factory=list)    # a stem file exists for the track, but it isn't linked


def audit_links(data: bytes, stems: Dict[str, List[str]]) -> LinkAudit:
    tracks = read_tracks(data)
    count = Counter(t.title for t in tracks)
    claims = Counter(path for paths in stems.values() for path in paths)
    audit = LinkAudit()
    for track in tracks:
        paths = stems.get(track.title)
        clear = paths and count[track.title] == 1 and len(paths) == 1 and claims[paths[0]] == 1
        if track.has_stems:
            audit.linked.append(track)
            if not paths:
                audit.broken.append(track)
            elif not clear:
                audit.unsure.append(track)
        elif clear:
            audit.files_not_linked.append(track)
    return audit


# ----------------------------------------------------------------------
# Restoring the links for stem files that still exist
# ----------------------------------------------------------------------

def surviving_stems(traktor_docs_dir: str) -> Dict[str, List[str]]:
    """Track title -> stem files that still exist, from every 'Traktor <version>/Logs/Traktor.log' under the
    'Native Instruments' documents folder."""
    found: Dict[str, List[str]] = {}
    for log in sorted(glob.glob(os.path.join(traktor_docs_dir, "Traktor *", "Logs", "Traktor.log"))):
        with open(log, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        for line in text.splitlines():
            job = _STEM_JOB.search(line)
            if not job:
                continue
            title, stem = job.groups()
            if os.path.isfile(stem) and os.path.getsize(stem) > 0:
                files = found.setdefault(title, [])
                if stem not in files:
                    files.append(stem)
    return found


@dataclass
class RestorePlan:
    to_flag: List[Track] = field(default_factory=list)                # stem file exists and the backup had the link
    already_flagged: List[Track] = field(default_factory=list)
    not_flagged_in_backup: List[Track] = field(default_factory=list)  # stem file exists but the backup had no link
    ambiguous: List[Tuple[str, List[Track]]] = field(default_factory=list)   # title, tracks sharing it (skipped)
    unmatched: List[Tuple[str, str]] = field(default_factory=list)    # title, stem file - no such title now (skipped)


def plan_restore(current: bytes, backup: bytes, stems: Dict[str, List[str]]) -> RestorePlan:
    tracks = read_tracks(current)
    by_title: Dict[str, List[Track]] = {}
    for track in tracks:
        by_title.setdefault(track.title, []).append(track)
    had_stems = {t.key for t in read_tracks(backup) if t.has_stems}
    claims = Counter(path for paths in stems.values() for path in paths)   # one file for two titles = unsafe

    plan = RestorePlan()
    for title in sorted(stems):
        paths, candidates = stems[title], by_title.get(title, [])
        if not candidates:
            plan.unmatched.append((title, paths[0]))
        elif len(candidates) > 1 or len(paths) > 1 or any(claims[p] > 1 for p in paths):
            plan.ambiguous.append((title, candidates))
        elif candidates[0].has_stems:
            plan.already_flagged.append(candidates[0])
        elif candidates[0].key in had_stems:
            plan.to_flag.append(candidates[0])
        else:
            plan.not_flagged_in_backup.append(candidates[0])
    return plan


def _with_stem_flag(info_tag: bytes, flags: int) -> bytes:
    value = flags | STEM_FLAG
    existing = _FLAGS_ATTR.search(info_tag)
    if existing:
        return info_tag[:existing.start()] + existing.group(1) + b'FLAGS="%d"' % value + info_tag[existing.end():]
    attribute = b' FLAGS="%d"' % value
    filesize = re.search(rb'\sFILESIZE=', info_tag)   # where Traktor writes it, right before FILESIZE
    at = filesize.start() if filesize else len(info_tag) - (2 if info_tag.endswith(b"/>") else 1)
    return info_tag[:at] + attribute + info_tag[at:]


def add_stem_flags(data: bytes, keys: Set[Tuple[str, str]]) -> Tuple[bytes, int]:
    """Set the stem bit on the tracks at these (DIR, FILE) locations; every other byte stays as it was."""
    pieces, cursor, changed = [], 0, 0
    for track in read_tracks(data):
        if track.key in keys and not track.has_stems:
            start, end = track.info
            pieces += [data[cursor:start], _with_stem_flag(data[start:end], track.flags)]
            cursor, changed = end, changed + 1
    pieces.append(data[cursor:])
    return b"".join(pieces), changed


def find_stem_backup(collection_path: str) -> Optional[str]:
    """Newest 'collection.nml.bak-*' next to the collection that still has stem links (what removing the links in Traktor Stems leaves)."""
    for candidate in sorted(glob.glob(f"{collection_path}.bak-*"), key=os.path.getmtime, reverse=True):
        try:
            with open(candidate, "rb") as f:
                if scan_stem_flags(f.read()).flagged:
                    return candidate
        except OSError:
            continue
    return None


@dataclass
class RestoreResult:
    plan: RestorePlan
    applied: bool
    backup_path: Optional[str] = None


def restore_collection(path: str, backup_collection: str, stems: Dict[str, List[str]], apply: bool) -> RestoreResult:
    """Preview (apply=False) or set the stem bit again (apply=True) on the tracks whose stem file still exists and
    whose link was in `backup_collection`. Close Traktor first."""
    with open(path, "rb") as f:
        current = f.read()
    with open(backup_collection, "rb") as f:
        backup = f.read()
    _check_collection(current)
    _check_collection(backup)

    plan = plan_restore(current, backup, stems)
    if not apply or not plan.to_flag:
        return RestoreResult(plan, applied=False)
    new_data, _changed = add_stem_flags(current, {t.key for t in plan.to_flag})
    return RestoreResult(plan, applied=True, backup_path=_replace_with_backup(path, new_data))
