#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Create Traktor stems for every track of a playlist.

Traktor has no API for this. Its browser creates the stem file for the selected track on Shift+S, so this walks
the playlist the way a person would: Shift+S, wait for the job, Down arrow, next track. Unlike a fixed delay it
waits on Traktor's own log, which gets one line per finished job:

    <time> - Finalized stem separation process for track "<title>" - result: successful: ..., elapsed time: 2m 22s

The run stops, rather than carrying on blind, when the log names a different track than the one being worked on
(the browser is sorted differently from the playlist), when no result arrives in time, when Traktor loses focus
or when Traktor is gone. Failed tracks (unsupported sample rate, ...) are reported and the run goes on.
"""

import datetime
import glob
import os
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Protocol, Set, Tuple

from shared_traktor_stems import STEM_FLAG

AVERAGE_SECONDS_PER_TRACK = 150     # what a job takes in the logs, for the time estimate only

_FINALIZED = re.compile(r'- Finalized stem separation process for track "(.*?)" - result: '
                        r'(.*?)(?:, elapsed time: (\d+m \d+s))?\s*$')
_FINISHED = re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) - Finished stem separation job for track "(.*?)" - '
                       r'result: successful: stem file stored to "(.*?)"')


# ----------------------------------------------------------------------
# Playlists
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class Row:
    title: str
    artist: str
    file: str
    has_stems: bool          # Traktor's own stem flag on the track

    @property
    def label(self) -> str:
        return f"{self.artist} - {self.title}" if self.artist else self.title


@dataclass
class Playlist:
    name: str                # 'Folder/Sub/Name', as the sync tools show it
    rows: List[Row] = field(default_factory=list)
    unknown: int = 0         # playlist entries whose track is not in the collection


def load_playlists(nml_path: str) -> List[Playlist]:
    """Every ordinary playlist of a collection.nml with its tracks in playlist order. Smart lists are queries
    with no stored track list, so they are left out."""
    root = ET.parse(nml_path).getroot()
    tracks: Dict[str, Row] = {}
    for entry in root.findall("COLLECTION/ENTRY"):
        loc, info = entry.find("LOCATION"), entry.find("INFO")
        if loc is None:
            continue
        key = f"{loc.get('VOLUME', '')}{loc.get('DIR', '')}{loc.get('FILE', '')}"
        try:
            flags = int(info.get("FLAGS", "0")) if info is not None else 0
        except ValueError:
            flags = 0
        tracks[key] = Row(entry.get("TITLE", ""), entry.get("ARTIST", ""), loc.get("FILE", ""), bool(flags & STEM_FLAG))

    playlists: List[Playlist] = []

    def walk(folder: ET.Element, trail: Tuple[str, ...]) -> None:
        subnodes = folder.find("SUBNODES")
        for node in subnodes.findall("NODE") if subnodes is not None else []:
            name, kind = node.get("NAME", ""), node.get("TYPE", "")
            if kind == "FOLDER":
                walk(node, trail + (name,))
            elif kind == "PLAYLIST":
                playlist = Playlist("/".join(trail + (name,)))
                for entry in node.findall("PLAYLIST/ENTRY"):
                    key = entry.find("PRIMARYKEY")
                    row = tracks.get(key.get("KEY", "")) if key is not None else None
                    if row:
                        playlist.rows.append(row)
                    else:
                        playlist.unknown += 1
                playlists.append(playlist)

    top = root.find("PLAYLISTS/NODE")
    if top is not None:
        walk(top, ())
    return playlists


# ----------------------------------------------------------------------
# What needs doing
# ----------------------------------------------------------------------

@dataclass
class PlanItem:
    position: int            # 1-based row in the playlist
    row: Row
    skip: str = ""           # why this track is left alone; empty = create stems


def recent_stem_titles(traktor_docs_dir: str, within_hours: float = 24, now: Optional[datetime.datetime] = None) -> Set[str]:
    """Titles with a successful stem job in the last `within_hours` whose stem file is still there. After an
    interrupted run these are the tracks already done, even though collection.nml is only updated when Traktor saves."""
    now = now or datetime.datetime.now()
    found: Set[str] = set()
    for log in glob.glob(os.path.join(traktor_docs_dir, "Traktor *", "Logs", "Traktor.log")):
        with open(log, "rb") as f:
            text = f.read().decode("utf-8", "replace")
        for line in text.splitlines():
            done = _FINISHED.match(line)
            if not done:
                continue
            when = datetime.datetime.strptime(done.group(1), "%Y-%m-%d %H:%M:%S")
            if (now - when).total_seconds() <= within_hours * 3600 and os.path.isfile(done.group(3)):
                found.add(done.group(2))
    return found


def make_plan(rows: List[Row], skip_existing: bool = True, recent: Optional[Set[str]] = None) -> List[PlanItem]:
    """One item per playlist row, in order: the run has to step over skipped tracks to stay in line with Traktor."""
    titles = [r.title for r in rows]
    recent = recent or set()
    seen: Set[Tuple[str, str]] = set()
    plan = []
    for position, row in enumerate(rows, 1):
        item = PlanItem(position, row)
        if (row.title, row.file) in seen:
            item.skip = "same track again in this playlist"
        elif skip_existing and row.has_stems:
            item.skip = "already has stems"
        elif skip_existing and row.title in recent and titles.count(row.title) == 1:
            item.skip = "stems created in a recent run"
        seen.add((row.title, row.file))
        plan.append(item)
    return plan


def estimate(plan: List[PlanItem]) -> str:
    minutes = round(sum(1 for item in plan if not item.skip) * AVERAGE_SECONDS_PER_TRACK / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


# ----------------------------------------------------------------------
# Traktor's log
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class JobResult:
    title: str
    ok: bool
    detail: str              # e.g. 'successful: stem file written to ...' or 'failed because ...'
    elapsed: str = ""

    @property
    def summary(self) -> str:
        return "successful" if self.ok else self.detail


class LogWatcher:
    """Reads the finished-job lines Traktor appends to its log after the point where the watcher was created."""

    def __init__(self, path: str):
        self.path = path
        self._offset = os.path.getsize(path) if os.path.exists(path) else 0
        self._partial = b""

    def poll(self) -> List[JobResult]:
        try:
            size = os.path.getsize(self.path)
            if size < self._offset:               # log was restarted
                self._offset, self._partial = 0, b""
            with open(self.path, "rb") as f:
                f.seek(self._offset)
                data = f.read()
        except OSError:
            return []
        self._offset += len(data)
        lines = (self._partial + data).split(b"\n")
        self._partial = lines.pop()               # an unfinished last line waits for its newline
        results = []
        for raw in lines:
            match = _FINALIZED.search(raw.decode("utf-8", "replace"))
            if match:
                title, detail, elapsed = match.groups()
                results.append(JobResult(title, detail.startswith("successful"), detail, elapsed or ""))
        return results


# ----------------------------------------------------------------------
# Driving Traktor
# ----------------------------------------------------------------------

class DriverError(Exception):
    pass


class TraktorKeys(Protocol):
    def activate(self) -> None: ...
    def generate_stems(self) -> None: ...
    def next_track(self) -> None: ...


class MacTraktorKeys:
    """Shift+S and Down arrow sent to Traktor through System Events (the same keys the AppleScript used)."""

    def __init__(self):
        self._process: Optional[str] = None

    @staticmethod
    def _osascript(script: str) -> str:
        try:
            done = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise DriverError(f"Could not talk to macOS: {e}")
        if done.returncode != 0:
            err = done.stderr.strip()
            if "1002" in err or "assistive" in err.lower():
                raise DriverError("macOS blocked the keystrokes. Allow the app that runs FastRack (Terminal, Claude, ...) "
                                  "under System Settings > Privacy & Security > Accessibility, then run again.")
            if "-1743" in err or "not authorized" in err.lower():
                raise DriverError("macOS blocked control of System Events. Allow it under System Settings > "
                                  "Privacy & Security > Automation, then run again.")
            raise DriverError(err or "osascript failed")
        return done.stdout.strip()

    def process_name(self) -> str:
        if not self._process:
            names = [n.strip() for n in self._osascript(
                'tell application "System Events" to get name of every application process whose name contains "Traktor"'
            ).split(",") if n.strip()]
            pro = [n for n in names if "pro" in n.lower()]
            if not (pro or names):
                raise DriverError("Traktor is not running.")
            self._process = (pro or names)[0]
        return self._process

    def activate(self) -> None:
        name = self.process_name()
        self._osascript(f'tell application "System Events" to set frontmost of process "{name}" to true')
        time.sleep(0.5)
        if self._osascript(f'tell application "System Events" to get frontmost of process "{name}"') != "true":
            raise DriverError("Traktor did not come to the front - stopping so no keystroke lands in another app.")

    def generate_stems(self) -> None:
        self._osascript(f'tell application "System Events" to tell process "{self.process_name()}" '
                        f'to keystroke "s" using {{shift down}}')

    def next_track(self) -> None:
        self._osascript(f'tell application "System Events" to tell process "{self.process_name()}" to key code 125')


# ----------------------------------------------------------------------
# The run
# ----------------------------------------------------------------------

@dataclass
class RunOptions:
    job_timeout: float = 900     # seconds to wait for one track's result (a 3 min job is normal)
    poll: float = 2.0
    settle: float = 1.0          # pause after bringing Traktor forward / moving the selection


@dataclass
class RunSummary:
    created: List[JobResult] = field(default_factory=list)
    failed: List[JobResult] = field(default_factory=list)
    skipped: int = 0
    stopped: str = ""            # why the run ended early; empty = went through the whole playlist

    @property
    def complete(self) -> bool:
        return not self.stopped


def wait_for_result(watcher: LogWatcher, timeout: float, poll: float, cancel: threading.Event,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> Tuple[str, Optional[JobResult]]:
    """('ok', result) for the first finished job, else ('timeout', None) or ('cancelled', None)."""
    deadline = clock() + timeout
    while True:
        results = watcher.poll()
        if results:
            return "ok", results[0]
        if cancel.is_set():
            return "cancelled", None
        if clock() >= deadline:
            return "timeout", None
        sleep(poll)


def run_plan(plan: List[PlanItem], keys: TraktorKeys, watcher: LogWatcher, options: RunOptions,
             log: Callable[[str], None], cancel: Optional[threading.Event] = None,
             sleep: Callable[[float], None] = time.sleep,
             clock: Callable[[], float] = time.monotonic) -> RunSummary:
    """Walk the playlist from its first row, which must be the selected track in Traktor's browser."""
    cancel = cancel or threading.Event()
    summary = RunSummary()
    total = len(plan)
    try:
        for index, item in enumerate(plan):
            if cancel.is_set():
                summary.stopped = "Stopped."
                break
            tag = f"[{item.position}/{total}] {item.row.label}"
            if item.skip:
                summary.skipped += 1
                log(f"{tag} - skipped ({item.skip})")
            else:
                log(f"{tag} - creating stems...")
                keys.activate()
                sleep(options.settle)
                watcher.poll()                      # drop anything older than this keystroke
                keys.generate_stems()
                status, result = wait_for_result(watcher, options.job_timeout, options.poll, cancel, sleep, clock)
                if status == "cancelled":
                    summary.stopped = f"Stopped while Traktor was working on '{item.row.title}' - it finishes that one by itself."
                    break
                if status == "timeout":
                    summary.stopped = (f"No result from Traktor for '{item.row.title}' after {options.job_timeout / 60:.0f} min. "
                                       f"Was the track selected in Traktor's browser? (Or raise the timeout for a very long track.)")
                    break
                if result.title != item.row.title:
                    summary.stopped = (f"Traktor reported '{result.title}' but '{item.row.title}' was expected next - the "
                                       f"browser isn't in playlist order. Sort the playlist by its # column and start again.")
                    break
                if result.ok:
                    summary.created.append(result)
                    log(f"    done in {result.elapsed or '?'}")
                else:
                    summary.failed.append(result)
                    log(f"    FAILED: {result.summary}")
            if index < total - 1:
                keys.activate()
                sleep(options.settle)
                keys.next_track()
                sleep(options.settle)
    except DriverError as e:
        summary.stopped = str(e)
    return summary
