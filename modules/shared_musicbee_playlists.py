#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MusicBee playlist doctor: find playlist entries whose file no longer exists
(typically the Soulseek staging folder after it was moved into the library),
match them to the real library track, and optionally rewrite the .mbp files.

MusicBee's iTunes XML only lists library tracks, so a dead .mbp entry silently
vanishes from every playlist the sync tools export.

CLI (dry-run unless --apply):
    python shared_musicbee_playlists.py scan   [--xml XML] [--playlists DIR]
    python shared_musicbee_playlists.py repair [--xml XML] [--playlists DIR] [--apply]
"""

import argparse
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Set, Tuple

from shared_appdata import get_appdata_path
from shared_logging import get_logger

logger = get_logger("musicbee_playlists")

SEP = b"\xff\xff\xff\xff"
MBP_VERSION = 4
MUSICBEE_PROCESS_NAMES = ("musicbee.exe",)
DEFAULT_XML_NAME = "iTunes Music Library.xml"

AUDIO_EXTENSIONS = (".mp3", ".flac", ".m4a", ".wav", ".aiff", ".aif", ".ogg", ".opus", ".wma", ".aac")
_FS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|\\\\)")
# A dead file named "... (X Remix)" must not resolve to a library track without that suffix.
_VERSION_WORDS = frozenset({
    "remix", "mix", "edit", "version", "dub", "instrumental", "live", "acoustic", "remaster",
    "remastered", "rework", "radio", "extended", "vip", "reprise", "bootleg",
})


def default_staging_roots() -> List[str]:
    return [os.path.join(os.path.expanduser("~"), "Documents", "Soulseek Downloads")]


def default_playlists_dir(xml_path: str) -> str:
    return os.path.join(os.path.dirname(xml_path), "Playlists")


# ----------------------------------------------------------------------
# .mbp reader / writer
# ----------------------------------------------------------------------

class MbpFormatError(ValueError):
    pass


def _enc7(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def _read7(data: bytes, i: int) -> Tuple[int, int]:
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


@dataclass
class Mbp:
    """version | 7bit-len block(fields joined by \\0, first field = description) | 04 00 |
    SEP 00000000 SEP | count | per entry: 7bit-len + UTF-8 path + SEP"""
    entries: List[str]
    fields: List[str] = field(default_factory=lambda: ["", "", "M:\\", "0", "False", "", "-2"])

    @property
    def description(self) -> str:
        return self.fields[0]

    def to_bytes(self) -> bytes:
        block = b"\x00".join(f.encode("utf-8") for f in self.fields) + b"\x00"
        out = bytearray(struct.pack("<I", MBP_VERSION))
        out += _enc7(len(block)) + block + b"\x04\x00" + SEP + b"\x00\x00\x00\x00" + SEP
        out += struct.pack("<I", len(self.entries))
        for p in self.entries:
            raw = p.encode("utf-8")
            out += _enc7(len(raw)) + raw + SEP
        return bytes(out)

    @classmethod
    def from_bytes(cls, data: bytes) -> "Mbp":
        try:
            if struct.unpack_from("<I", data, 0)[0] != MBP_VERSION:
                raise MbpFormatError("unsupported .mbp version")
            n, i = _read7(data, 4)
            block = data[i:i + n]
            i += n
            if len(block) != n or not block.endswith(b"\x00"):
                raise MbpFormatError("bad header block")
            fields = [f.decode("utf-8") for f in block.split(b"\x00")[:-1]]
            if data[i:i + 2] != b"\x04\x00":
                raise MbpFormatError("bad header marker")
            i += 2
            if data[i:i + 12] != SEP + b"\x00\x00\x00\x00" + SEP:
                raise MbpFormatError("bad header separator")
            i += 12
            (count,) = struct.unpack_from("<I", data, i)
            i += 4
            entries = []
            for _ in range(count):
                n, i = _read7(data, i)
                raw = data[i:i + n]
                if len(raw) != n:
                    raise MbpFormatError("truncated entry")
                i += n
                if data[i:i + 4] != SEP:
                    raise MbpFormatError("missing entry separator")
                i += 4
                entries.append(raw.decode("utf-8"))
            if i != len(data):
                raise MbpFormatError("trailing bytes")
            return cls(entries, fields)
        except (struct.error, IndexError, UnicodeDecodeError) as e:
            raise MbpFormatError(str(e)) from e


def _tolerant_entries(data: bytes) -> List[str]:
    """Best-effort entry extraction for files the strict reader rejects (scan only, never repaired)."""
    out = []
    for seg in data.split(SEP):
        for m in re.finditer(rb"[A-Za-z]:\\", seg):
            i = m.start()
            lengths = []
            if i >= 2 and seg[i - 2] & 0x80 and seg[i - 1] < 0x80:
                lengths.append((seg[i - 2] & 0x7F) | (seg[i - 1] << 7))
            if i >= 1:
                lengths.append(seg[i - 1])
            for n in lengths:
                raw = seg[i:i + n]
                if len(raw) == n and n >= 6:
                    try:
                        p = raw.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    if p.lower().endswith(AUDIO_EXTENSIONS):
                        out.append(p)
                        break
            else:
                continue
            break
    return out


# ----------------------------------------------------------------------
# Library index (built from the iTunes XML both sync tools already parse)
# ----------------------------------------------------------------------

def norm_path(p: str) -> str:
    return unicodedata.normalize("NFC", p or "").replace("/", "\\").casefold()


def _tokens(s: str) -> List[str]:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).casefold()
    return re.findall(r"[^\W_]+", s)


def decode_location(location: str) -> str:
    if location.startswith("file://localhost/"):
        path = location[len("file://localhost/"):]
    elif location.startswith("file:///"):
        path = location[len("file:///"):]
    elif location.startswith("file://"):
        path = location[len("file://"):]
    else:
        path = location
    return urllib.parse.unquote(path).replace("/", "\\")


@dataclass(frozen=True)
class LibraryTrack:
    track_id: str
    path: str
    artist: str = ""
    title: str = ""
    album: str = ""


class LibraryIndex:
    def __init__(self, tracks: Iterable[LibraryTrack]):
        self.tracks: Dict[str, LibraryTrack] = {}
        self._by_path: Dict[str, str] = {}
        self._tok: Dict[str, Tuple[List[str], frozenset]] = {}
        for t in tracks:
            self.tracks[t.track_id] = t
            if t.path:
                self._by_path[norm_path(t.path)] = t.track_id
            self._tok[t.track_id] = (_tokens(t.title), frozenset(_tokens(t.artist)))

    def __len__(self) -> int:
        return len(self.tracks)

    def contains_path(self, path: str) -> bool:
        return norm_path(path) in self._by_path

    def track_for_path(self, path: str) -> Optional[LibraryTrack]:
        tid = self._by_path.get(norm_path(path))
        return self.tracks.get(tid) if tid else None

    @classmethod
    def from_itunes_root(cls, root: ET.Element) -> "LibraryIndex":
        library_dict = next((c for c in root if c.tag == "dict"), None)
        tracks_el = _dict_value(library_dict, "Tracks") if library_dict is not None else None
        records = []
        if tracks_el is not None:
            for key_el, track_el in _pairs(tracks_el):
                if track_el.tag != "dict":
                    continue
                props = {k.text: v.text for k, v in _pairs(track_el) if v.tag in ("string", "integer")}
                records.append(LibraryTrack(
                    track_id=key_el.text,
                    path=decode_location(props.get("Location") or ""),
                    artist=props.get("Artist") or "",
                    title=props.get("Name") or "",
                    album=props.get("Album") or "",
                ))
        return cls(records)

    @classmethod
    def from_xml_path(cls, xml_path: str) -> "LibraryIndex":
        return cls.from_itunes_root(ET.parse(xml_path).getroot())

    def resolve(self, dead_path: str) -> Tuple[Optional[LibraryTrack], int]:
        """Match a dead path to a library track by artist+title. Returns (track, candidate_count);
        track is None unless exactly one best candidate exists."""
        name = os.path.splitext(os.path.basename(dead_path.replace("\\", "/")))[0]
        file_tokens = _tokens(name)
        file_set = set(file_tokens)
        path_set = set(_tokens(dead_path))
        hits = []
        for tid, (title_toks, artist_toks) in self._tok.items():
            if not title_toks or not artist_toks or not (artist_toks & path_set):
                continue
            it = iter(file_tokens)
            if not all(tok in it for tok in title_toks):
                continue
            if (file_set & _VERSION_WORDS) - set(title_toks):
                continue
            hits.append(tid)
        if not hits:
            return None, 0
        longest = max(len(self._tok[t][0]) for t in hits)
        top = [t for t in hits if len(self._tok[t][0]) == longest]
        return (self.tracks[top[0]] if len(top) == 1 else None), len(top)


def _pairs(el: ET.Element) -> Iterator[Tuple[ET.Element, ET.Element]]:
    children = list(el)
    for i in range(0, len(children) - 1, 2):
        if children[i].tag == "key":
            yield children[i], children[i + 1]


def _dict_value(el: ET.Element, key: str) -> Optional[ET.Element]:
    for k, v in _pairs(el):
        if k.text == key:
            return v
    return None


# ----------------------------------------------------------------------
# Scan
# ----------------------------------------------------------------------

@dataclass
class Issue:
    playlist: str
    file: str
    path: str
    kind: str                       # "dead" = file missing, "staging" = still inside a staging folder
    resolved: Optional[LibraryTrack] = None
    candidates: int = 0


@dataclass
class ScanReport:
    issues: List[Issue] = field(default_factory=list)
    playlists_scanned: int = 0
    unreadable: List[str] = field(default_factory=list)

    @property
    def resolvable(self) -> List[Issue]:
        return [i for i in self.issues if i.resolved]

    def by_playlist(self) -> Dict[str, List[Issue]]:
        out: Dict[str, List[Issue]] = {}
        for i in self.issues:
            out.setdefault(i.playlist, []).append(i)
        return out

    def distinct_paths(self) -> List[str]:
        return sorted({i.path for i in self.issues})

    def extra_track_ids(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for i in self.resolvable:
            ids = out.setdefault(i.playlist, [])
            if i.resolved.track_id not in ids:
                ids.append(i.resolved.track_id)
        return out


def list_playlist_files(playlists_dir: str) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(playlists_dir):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("_"))
        for f in sorted(filenames):
            if f.lower().endswith(".mbp"):
                found.append(os.path.join(dirpath, f))
    return found


def read_entries(data: bytes) -> List[str]:
    try:
        return Mbp.from_bytes(data).entries
    except MbpFormatError:
        return _tolerant_entries(data)


def scan(playlists_dir: str, index: LibraryIndex, selected: Optional[Iterable[str]] = None,
         staging_roots: Sequence[str] = ()) -> ScanReport:
    roots = [norm_path(r).rstrip("\\") + "\\" for r in staging_roots if r]
    wanted = set(selected) if selected is not None else None
    report = ScanReport()
    cache: Dict[str, Tuple[Optional[LibraryTrack], int]] = {}
    for file in list_playlist_files(playlists_dir):
        name = os.path.splitext(os.path.basename(file))[0]
        if wanted is not None and name not in wanted:
            continue
        try:
            with open(file, "rb") as f:
                data = f.read()
        except OSError:
            report.unreadable.append(file)
            continue
        report.playlists_scanned += 1
        for p in read_entries(data):
            if not _FS_PATH.match(p):
                continue
            np = norm_path(p)
            staged = any(np.startswith(r) for r in roots)
            if np in index._by_path and not staged:
                continue
            exists = os.path.exists(p)
            if exists and not staged:
                continue
            issue = Issue(name, file, p, "staging" if exists else "dead")
            if not exists:
                if p not in cache:
                    cache[p] = index.resolve(p)
                issue.resolved, issue.candidates = cache[p]
            report.issues.append(issue)
    return report


def format_report(report: ScanReport, limit: int = 60) -> str:
    if not report.issues:
        return f"No problems found in {report.playlists_scanned} playlist(s)."
    n_pl = len(report.by_playlist())
    lines = [
        f"{len(report.issues)} problem entr{'y' if len(report.issues) == 1 else 'ies'} in {n_pl} playlist(s), "
        f"{len(report.distinct_paths())} distinct file(s); "
        f"{len({i.path for i in report.resolvable})} can be matched to a library track automatically.",
        "",
    ]
    shown = 0
    for playlist, issues in sorted(report.by_playlist().items(), key=lambda kv: kv[0].casefold()):
        for i in issues:
            if shown >= limit:
                break
            shown += 1
            base = os.path.basename(i.path)
            if i.resolved:
                target = f"{i.resolved.artist} - {i.resolved.title}  ({i.resolved.path})"
                lines.append(f"[{playlist}] {base}  ->  {target}")
            elif i.kind == "staging":
                lines.append(f"[{playlist}] {base}  ->  still in the staging folder (finish sorting/importing it first)")
            elif i.candidates > 1:
                lines.append(f"[{playlist}] {base}  ->  {i.candidates} possible matches, not guessing")
            else:
                lines.append(f"[{playlist}] {base}  ->  no matching library track")
    if len(report.issues) > shown:
        lines.append(f"... and {len(report.issues) - shown} more")
    if report.unreadable:
        lines.append("")
        lines.append(f"{len(report.unreadable)} playlist file(s) could not be read.")
    return "\n".join(lines)


def merge_track_ids(existing: Sequence[str], extra: Sequence[str]) -> List[str]:
    seen = set(existing)
    merged = list(existing)
    for t in extra:
        if t not in seen:
            seen.add(t)
            merged.append(t)
    return merged


# ----------------------------------------------------------------------
# Repair
# ----------------------------------------------------------------------

class MusicBeeRunningError(RuntimeError):
    pass


def musicbee_running() -> bool:
    if sys.platform != "win32":
        return False
    try:
        result = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"], capture_output=True, text=True, timeout=10,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        logger.warning(f"tasklist failed: {e}")
        return False
    for line in result.stdout.splitlines():
        image = line.split('","', 1)[0].lstrip('"').lower()
        if image in MUSICBEE_PROCESS_NAMES:
            return True
    return False


@dataclass
class RepairResult:
    files_changed: List[str] = field(default_factory=list)
    replaced: int = 0
    dropped: int = 0        # duplicates dropped (replacement already present, or extra copies of one file)
    removed: int = 0        # entries removed on request
    skipped: List[Tuple[str, str]] = field(default_factory=list)
    backup_dir: Optional[str] = None
    applied: bool = False


@dataclass
class FileEdit:
    replace: Dict[str, str] = field(default_factory=dict)    # stored entry -> new path
    remove: Set[str] = field(default_factory=set)            # stored entries to delete (all occurrences)
    dedupe_exact: bool = False                               # keep only the first copy of every file
    dedupe_paths: Set[str] = field(default_factory=set)      # ...or only for these files (norm_path form)


def default_backup_root() -> str:
    return os.path.join(str(get_appdata_path()), "playlist_backups")


def apply_edits(edits: Dict[str, FileEdit], apply: bool = False, backup_root: Optional[str] = None,
                check_running: bool = True) -> RepairResult:
    """Rewrite .mbp playlists. Untouched entries keep their order. With apply=False nothing is written.
    Backups go outside the playlists folder (MusicBee would show a backup subfolder there as a playlist
    folder)."""
    if apply and check_running and musicbee_running():
        raise MusicBeeRunningError("MusicBee is running - close it before changing playlists.")

    result = RepairResult(applied=apply)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(backup_root or default_backup_root(), stamp)

    for file, edit in edits.items():
        try:
            with open(file, "rb") as f:
                original = f.read()
            mbp = Mbp.from_bytes(original)
            if mbp.to_bytes() != original:
                raise MbpFormatError("layout not byte-for-byte reproducible")
        except (OSError, MbpFormatError) as e:
            result.skipped.append((file, str(e)))
            continue

        new_entries: List[str] = []
        present = {norm_path(p) for p in mbp.entries if p not in edit.replace and p not in edit.remove}
        replaced = dropped = removed = 0
        for p in mbp.entries:
            if p in edit.remove:
                removed += 1
                continue
            target = edit.replace.get(p)
            if target is None:
                new_entries.append(p)
                continue
            np = norm_path(target)
            if np in present:
                dropped += 1
                continue
            present.add(np)
            new_entries.append(target)
            replaced += 1
        if edit.dedupe_exact or edit.dedupe_paths:
            seen = set()
            deduped = []
            for p in new_entries:
                key = norm_path(p)
                if key in seen and (edit.dedupe_exact or key in edit.dedupe_paths):
                    dropped += 1
                    continue
                seen.add(key)
                deduped.append(p)
            new_entries = deduped
        if replaced == 0 and dropped == 0 and removed == 0:
            continue

        result.replaced += replaced
        result.dropped += dropped
        result.removed += removed
        result.files_changed.append(file)
        if not apply:
            continue

        os.makedirs(backup_dir, exist_ok=True)
        shutil.copy2(file, os.path.join(backup_dir, f"{len(result.files_changed):03d}_{os.path.basename(file)}"))
        new_bytes = Mbp(new_entries, mbp.fields).to_bytes()
        tmp = f"{file}.tmp-{os.getpid()}"
        with open(tmp, "wb") as f:
            f.write(new_bytes)
        with open(tmp, "rb") as f:
            written = Mbp.from_bytes(f.read()).entries
        if written != new_entries:
            os.remove(tmp)
            raise MbpFormatError(f"verification failed for {file}")
        os.replace(tmp, file)
        result.backup_dir = backup_dir

    return result


def repair(report: ScanReport, apply: bool = False, backup_root: Optional[str] = None,
           check_running: bool = True) -> RepairResult:
    """Replace dead entries that resolved to exactly one library track; the rest stay untouched."""
    edits: Dict[str, FileEdit] = {}
    for i in report.resolvable:
        if i.kind == "dead":
            edits.setdefault(i.file, FileEdit()).replace[i.path] = i.resolved.path
    return apply_edits(edits, apply, backup_root, check_running)


# ----------------------------------------------------------------------
# Duplicates and library checks (report-only except playlist duplicates)
# ----------------------------------------------------------------------

def song_key(track: LibraryTrack) -> Optional[Tuple[Tuple[str, ...], Tuple[str, ...]]]:
    artist, title = tuple(_tokens(track.artist)), tuple(_tokens(track.title))
    return (artist, title) if artist and title else None


# Music on the M drive is normally already in its proper place, so when one copy of a song is there
# and the others are not, that copy is the one to keep.
PREFERRED_DRIVES = ("m:",)


def on_preferred_drive(path: str) -> bool:
    return norm_path(path)[:2] in PREFERRED_DRIVES


@dataclass
class FileInfo:
    """What tells copies of one song apart, one value per column."""
    album: str = ""
    format: str = ""
    size: str = ""
    date: str = ""

    def as_text(self) -> str:
        return " | ".join(p for p in (self.album, self.format, self.size, self.date) if p)


def describe_file(path: str, track: Optional["LibraryTrack"]) -> FileInfo:
    info = FileInfo(album=track.album if track is not None else "",
                    format=os.path.splitext(path)[1].lstrip(".").upper())
    try:
        st = os.stat(path)
        info.size = f"{st.st_size / 1048576:.1f} MB"
        info.date = time.strftime("%Y-%m-%d", time.localtime(st.st_mtime))
    except OSError:
        info.size = "not found"
    return info


@dataclass
class DuplicateIssue:
    playlist: str
    file: str
    kind: str                     # "exact" = same file more than once, "same_song" = different files of one song
    label: str
    paths: List[str] = field(default_factory=list)   # stored entries involved (same_song: one per distinct file)
    extra: int = 0                # exact: number of redundant copies
    details: List["FileInfo"] = field(default_factory=list)  # same_song: describe_file() for each path


def find_duplicates(playlists_dir: str, index: LibraryIndex,
                    selected: Optional[Iterable[str]] = None) -> List[DuplicateIssue]:
    wanted = set(selected) if selected is not None else None
    out: List[DuplicateIssue] = []
    for file in list_playlist_files(playlists_dir):
        name = os.path.splitext(os.path.basename(file))[0]
        if wanted is not None and name not in wanted:
            continue
        try:
            with open(file, "rb") as f:
                entries = [p for p in read_entries(f.read()) if _FS_PATH.match(p)]
        except OSError:
            continue
        counts: Dict[str, int] = {}
        first_seen: Dict[str, str] = {}
        for p in entries:
            key = norm_path(p)
            counts[key] = counts.get(key, 0) + 1
            first_seen.setdefault(key, p)
        for key, n in counts.items():
            if n > 1:
                t = index.track_for_path(first_seen[key])
                label = f"{t.artist} - {t.title}" if t else os.path.basename(first_seen[key])
                out.append(DuplicateIssue(name, file, "exact", label, [first_seen[key]], n - 1))
        songs: Dict[tuple, List[str]] = {}
        for p in first_seen.values():
            t = index.track_for_path(p)
            sk = song_key(t) if t else None
            if sk:
                songs.setdefault(sk, []).append(p)
        for paths in songs.values():
            if len(paths) > 1:
                t = index.track_for_path(paths[0])
                details = [describe_file(p, index.track_for_path(p)) for p in paths]
                out.append(DuplicateIssue(name, file, "same_song", f"{t.artist} - {t.title}", paths,
                                          details=details))
    return out


def find_missing_library_files(index: LibraryIndex) -> List[LibraryTrack]:
    return sorted(
        (t for t in index.tracks.values() if _FS_PATH.match(t.path or "") and not os.path.exists(t.path)),
        key=lambda t: (t.artist.casefold(), t.title.casefold()))


def find_duplicate_songs(index: LibraryIndex) -> List[List[LibraryTrack]]:
    groups: Dict[tuple, Dict[str, LibraryTrack]] = {}
    for t in index.tracks.values():
        sk = song_key(t) if _FS_PATH.match(t.path or "") else None
        if sk:
            groups.setdefault(sk, {})[norm_path(t.path)] = t
    found = [sorted(g.values(), key=lambda t: t.path.casefold()) for g in groups.values() if len(g) > 1]
    return sorted(found, key=lambda g: (g[0].artist.casefold(), g[0].title.casefold()))


def find_empty_playlists(playlists_dir: str) -> List[str]:
    empty = []
    for file in list_playlist_files(playlists_dir):
        try:
            with open(file, "rb") as f:
                if not Mbp.from_bytes(f.read()).entries:
                    empty.append(os.path.splitext(os.path.basename(file))[0])
        except (OSError, MbpFormatError):
            continue
    return empty


# ----------------------------------------------------------------------
# One scan, and the list of fixes the cleanup tool offers
# ----------------------------------------------------------------------

@dataclass
class FullScan:
    playlists_dir: str
    xml_mtime: float
    newest_playlist_mtime: float
    report: ScanReport
    dupes: List[DuplicateIssue]
    empty: List[str]
    missing: List[LibraryTrack]
    dup_songs: List[List[LibraryTrack]]


def full_scan(xml_path: str, playlists_dir: str, staging_roots: Sequence[str] = ()) -> FullScan:
    index = LibraryIndex.from_xml_path(xml_path)
    files = list_playlist_files(playlists_dir)
    return FullScan(
        playlists_dir=playlists_dir,
        xml_mtime=os.path.getmtime(xml_path),
        newest_playlist_mtime=max((os.path.getmtime(f) for f in files), default=0.0),
        report=scan(playlists_dir, index, staging_roots=staging_roots),
        dupes=find_duplicates(playlists_dir, index),
        empty=find_empty_playlists(playlists_dir),
        missing=find_missing_library_files(index),
        dup_songs=find_duplicate_songs(index),
    )


@dataclass
class FixRow:
    playlist: str
    file: str
    problem: str
    entry: str                 # what the user sees
    fix: str
    action: str                # "replace" | "remove" | "dedupe" | "none"
    path: str = ""             # stored playlist entry the action applies to
    new_path: str = ""         # replace target
    checked: bool = False      # safe fixes start ticked; anything that drops a song starts unticked
    group: str = ""            # same-song group, so every copy of a song can't be removed at once
    label: str = ""            # same-song rows: "Artist - Title"
    detail: Optional["FileInfo"] = None   # same-song rows: album, format, size, date of this file
    recommended: bool = False  # same-song rows: the copy to keep (on the preferred drive)
    header: bool = False       # same-song group heading; carries no action of its own

    @property
    def fixable(self) -> bool:
        return self.action != "none"

    @property
    def fix_text(self) -> str:
        """What the Fix column shows. Same-song copies follow their tick: ticked = removed, else kept."""
        if not self.group:
            return self.fix
        if self.checked:
            return "Remove from this playlist"
        return "Keep this file" + (" (on M: - recommended)" if self.recommended else "")


def build_fix_rows(report: ScanReport, dupes: Sequence[DuplicateIssue]) -> List[FixRow]:
    rows: List[FixRow] = []
    seen = set()
    for i in report.issues:
        key = (i.file, i.path, i.kind)
        if key in seen:
            continue
        seen.add(key)
        if i.kind == "staging":
            rows.append(FixRow(i.playlist, i.file, "In staging folder", i.path,
                               "Finish sorting/importing it first", "none", i.path))
        elif i.resolved:
            rows.append(FixRow(i.playlist, i.file, "Dead entry", i.path,
                               f"Replace with {i.resolved.artist} - {i.resolved.title}",
                               "replace", i.path, i.resolved.path, checked=True))
        else:
            why = f"{i.candidates} possible matches" if i.candidates > 1 else "no matching track"
            rows.append(FixRow(i.playlist, i.file, f"Dead entry ({why})", i.path,
                               "Remove from playlist", "remove", i.path))
    for d in dupes:
        if d.kind == "exact":
            rows.append(FixRow(d.playlist, d.file, f"Same file listed {d.extra + 1} times", d.label,
                               f"Keep the first, remove {d.extra} cop{'y' if d.extra == 1 else 'ies'}",
                               "dedupe", d.paths[0], checked=True))
        else:
            group = f"{d.file}|{d.label}"
            on_preferred = [p for p in d.paths if on_preferred_drive(p)]
            keeper = on_preferred[0] if len(on_preferred) == 1 and len(d.paths) > 1 else None
            hint = "Recommended: keep the M: copy" if keeper else "Tick the copies to remove"
            rows.append(FixRow(d.playlist, d.file, f"Same song, {len(d.paths)} files", d.label, hint, "none",
                               label=d.label, header=True))
            details = list(d.details) + [FileInfo()] * (len(d.paths) - len(d.details))
            for n, (p, detail) in enumerate(zip(d.paths, details), 1):
                rows.append(FixRow(d.playlist, d.file, f"{n} of {len(d.paths)}", p, "", "remove", p,
                                   checked=keeper is not None and p != keeper, group=group, label=d.label,
                                   detail=detail, recommended=p == keeper))

    def rank(problem: str) -> int:
        for n, prefix in enumerate(("Dead entry", "Same file", "Same song", "In staging")):
            if problem.startswith(prefix):
                return n
        return 4

    def order(r: FixRow):
        # A same-song group stays together: heading first, then its files in the order found.
        if r.label:
            return (r.playlist.casefold(), rank("Same song"), r.label.casefold(), 0 if r.header else 1)
        return (r.playlist.casefold(), rank(r.problem), r.entry.casefold(), 0, "")

    rows.sort(key=order)
    return rows


def edits_from_rows(rows: Iterable[FixRow]) -> Dict[str, FileEdit]:
    edits: Dict[str, FileEdit] = {}
    for r in rows:
        if not r.checked or not r.fixable:
            continue
        edit = edits.setdefault(r.file, FileEdit())
        if r.action == "replace":
            edit.replace[r.path] = r.new_path
        elif r.action == "remove":
            edit.remove.add(r.path)
        elif r.action == "dedupe":
            edit.dedupe_paths.add(norm_path(r.path))
    return edits


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["scan", "repair"])
    ap.add_argument("--xml", default=os.path.join("M:\\", DEFAULT_XML_NAME))
    ap.add_argument("--playlists", default="")
    ap.add_argument("--staging", action="append", default=[])
    ap.add_argument("--apply", action="store_true", help="repair: actually write (default is a dry run)")
    args = ap.parse_args(argv)

    playlists_dir = args.playlists or default_playlists_dir(args.xml)
    index = LibraryIndex.from_xml_path(args.xml)
    report = scan(playlists_dir, index, staging_roots=args.staging or default_staging_roots())
    print(format_report(report, limit=500))
    if args.command == "repair":
        try:
            res = repair(report, apply=args.apply)
        except MusicBeeRunningError as e:
            print(f"\n{e}")
            return 2
        mode = "Repaired" if args.apply else "Would repair (dry run)"
        print(f"\n{mode}: {res.replaced} entr(ies) replaced, {res.dropped} duplicate(s) dropped "
              f"in {len(res.files_changed)} file(s).")
        for f, why in res.skipped:
            print(f"  skipped {f}: {why}")
        if res.backup_dir:
            print(f"Backups: {res.backup_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(_main())
