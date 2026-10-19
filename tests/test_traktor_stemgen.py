"""Tests for shared_traktor_stemgen: playlist reading, the plan, Traktor's log and the run loop (with a fake Traktor)."""

import datetime
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_traktor_stemgen as gen  # noqa: E402

NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="4">
<ENTRY TITLE="One" ARTIST="A"><LOCATION DIR="/:M/:" FILE="One.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="12"></INFO></ENTRY>
<ENTRY TITLE="Two" ARTIST="B"><LOCATION DIR="/:M/:" FILE="Two.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="76"></INFO></ENTRY>
<ENTRY TITLE="Three &amp; Co" ARTIST="C"><LOCATION DIR="/:M/:" FILE="Three.flac" VOLUME="Mac"></LOCATION><INFO></INFO></ENTRY>
<ENTRY TITLE="Four" ARTIST="D"><LOCATION DIR="/:M/:" FILE="Four.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="12"></INFO></ENTRY>
</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="3">
<NODE TYPE="PLAYLIST" NAME="Warmup"><PLAYLIST ENTRIES="4" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Three.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:One.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Gone.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Two.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE>
<NODE TYPE="FOLDER" NAME="Sets"><SUBNODES COUNT="2">
<NODE TYPE="PLAYLIST" NAME="Peak"><PLAYLIST ENTRIES="1" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Four.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE>
<NODE TYPE="SMARTLIST" NAME="Smart"><SMARTLIST></SMARTLIST></NODE>
</SUBNODES></NODE>
</SUBNODES></NODE></PLAYLISTS></NML>
"""


def rows(*specs):
    """Row(title, has_stems) shortcuts."""
    return [gen.Row(title, "X", f"{title}.flac", stems) for title, stems in specs]


def test_load_playlists_keeps_order_flags_and_skips_smart_lists(tmp_path):
    nml = tmp_path / "collection.nml"
    nml.write_text(NML, encoding="utf-8")
    playlists = {p.name: p for p in gen.load_playlists(str(nml))}
    assert sorted(playlists) == ["Sets/Peak", "Warmup"]                       # smart list left out
    warmup = playlists["Warmup"]
    assert [r.title for r in warmup.rows] == ["Three & Co", "One", "Two"]     # playlist order, XML unescaped
    assert [r.has_stems for r in warmup.rows] == [False, False, True]
    assert warmup.unknown == 1                                                # 'Gone.flac' is not in the collection


def test_plan_skips_tracks_that_have_stems_duplicates_and_recent_results():
    track_rows = rows(("A", False), ("B", True), ("C", False), ("A", False), ("D", False))
    plan = gen.make_plan(track_rows, skip_existing=True, recent={"C", "A"})
    assert [(i.position, i.skip) for i in plan] == [
        (1, ""),                                    # 'A' is recent but its title is used twice -> not trusted
        (2, "already has stems"),
        (3, "stems created in a recent run"),
        (4, "same track again in this playlist"),
        (5, ""),
    ]
    again = gen.make_plan(track_rows, skip_existing=False, recent={"C"})
    assert [i.skip for i in again] == ["", "", "", "same track again in this playlist", ""]


def test_estimate():
    plan = gen.make_plan(rows(*[(f"T{n}", False) for n in range(24)]))
    assert gen.estimate(plan) == "1 h 00 min"
    assert gen.estimate(gen.make_plan(rows(("A", False), ("B", False)))) == "5 min"


def finalized(title, result="successful: stem file written to \"x.stem.mp4\", \"no warnings issued.\"", elapsed="2m 22s"):
    return f'2026-10-18 21:00:00 - Finalized stem separation process for track "{title}" - result: {result}, elapsed time: {elapsed}\n'


def test_log_watcher_reads_only_new_complete_lines(tmp_path):
    log = tmp_path / "Traktor.log"
    log.write_text(finalized("Old"), encoding="utf-8")
    watcher = gen.LogWatcher(str(log))
    assert watcher.poll() == []                                    # what was there before is ignored

    with open(log, "a", encoding="utf-8") as f:
        f.write(finalized("New"))
        f.write(finalized("Broken", "failed because an exception was thrown (message: \"boom\")", "0m 0s"))
        f.write("2026-10-18 21:00:01 - WARNING: Stem version could not be created for: /x.flac\n")
        f.write('2026-10-18 21:00:02 - Finalized stem separation process for track "Half')   # no newline yet
    results = watcher.poll()
    assert [(r.title, r.ok, r.elapsed) for r in results] == [("New", True, "2m 22s"), ("Broken", False, "0m 0s")]
    assert "boom" in results[1].summary and results[0].summary == "successful"
    assert watcher.poll() == []

    with open(log, "a", encoding="utf-8") as f:
        f.write(' Title" - result: aborted by request, elapsed time: 0m 3s\n')
    assert [(r.title, r.ok) for r in watcher.poll()] == [("Half Title", False)]


def test_log_watcher_survives_a_restarted_log(tmp_path):
    log = tmp_path / "Traktor.log"
    log.write_text("x" * 500 + "\n", encoding="utf-8")
    watcher = gen.LogWatcher(str(log))
    log.write_text(finalized("Fresh"), encoding="utf-8")           # shorter than before -> a new log
    assert [r.title for r in watcher.poll()] == ["Fresh"]


class FakeTraktor:
    """Stands in for Traktor + the keyboard: each Shift+S appends what Traktor would log for the current row."""

    def __init__(self, log_path, titles, outcomes=None, fail_activate_at=None):
        self.log_path, self.titles, self.outcomes = log_path, titles, outcomes or {}
        self.position, self.calls, self.activations = 0, [], 0
        self.fail_activate_at = fail_activate_at

    def activate(self):
        self.activations += 1
        if self.fail_activate_at == self.activations:
            raise gen.DriverError("Traktor is not running.")

    def generate_stems(self):
        self.calls.append(f"generate:{self.titles[self.position]}")
        outcome = self.outcomes.get(self.position, "ok")
        title = self.titles[self.position]
        line = {"ok": finalized(title),
                "fail": finalized(title, "track has unsupported sample rate and and we're unable to downsample)", "0m 0s"),
                "other": finalized("Somebody Else"),
                "silent": ""}[outcome]
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(line)

    def next_track(self):
        self.calls.append("next")
        self.position += 1


class Clock:
    def __init__(self):
        self.now = 0.0

    def sleep(self, seconds):
        self.now += seconds

    def __call__(self):
        return self.now


def run(tmp_path, track_rows, outcomes=None, cancel=None, skip_existing=True, fail_activate_at=None, timeout=900):
    log = tmp_path / "Traktor.log"
    log.write_text("", encoding="utf-8")
    traktor = FakeTraktor(str(log), [r.title for r in track_rows], outcomes, fail_activate_at)
    clock, lines = Clock(), []
    summary = gen.run_plan(gen.make_plan(track_rows, skip_existing), traktor, gen.LogWatcher(str(log)),
                           gen.RunOptions(job_timeout=timeout), lines.append, cancel, clock.sleep, clock)
    return summary, traktor, lines


def test_run_creates_stems_steps_over_skipped_tracks_and_never_moves_past_the_last(tmp_path):
    summary, traktor, lines = run(tmp_path, rows(("A", False), ("B", True), ("C", False)))
    assert summary.complete and [r.title for r in summary.created] == ["A", "C"] and summary.skipped == 1
    # Down arrow after every row except the last, including over the skipped one
    assert traktor.calls == ["generate:A", "next", "next", "generate:C"]
    assert any("[2/3] X - B - skipped (already has stems)" in line for line in lines)


def test_a_failed_track_is_reported_and_the_run_goes_on(tmp_path):
    summary, traktor, _ = run(tmp_path, rows(("A", False), ("B", False)), outcomes={0: "fail"})
    assert summary.complete
    assert [r.title for r in summary.failed] == ["A"] and "unsupported sample rate" in summary.failed[0].summary
    assert [r.title for r in summary.created] == ["B"]


def test_a_result_for_another_track_stops_the_run(tmp_path):
    summary, traktor, _ = run(tmp_path, rows(("A", False), ("B", False)), outcomes={0: "other"})
    assert not summary.complete and "Somebody Else" in summary.stopped and "'A'" in summary.stopped
    assert traktor.calls == ["generate:A"]                          # no further keystrokes


def test_no_result_in_time_stops_the_run(tmp_path):
    summary, traktor, _ = run(tmp_path, rows(("A", False), ("B", False)), outcomes={0: "silent"}, timeout=60)
    assert not summary.complete and "No result from Traktor for 'A'" in summary.stopped
    assert traktor.calls == ["generate:A"]


def test_stop_button_ends_the_run_between_tracks(tmp_path):
    cancel = threading.Event()
    cancel.set()
    summary, traktor, _ = run(tmp_path, rows(("A", False)), cancel=cancel)
    assert summary.stopped == "Stopped." and traktor.calls == []


def test_losing_traktor_stops_the_run_with_the_drivers_message(tmp_path):
    summary, traktor, _ = run(tmp_path, rows(("A", False), ("B", False)), fail_activate_at=3)   # before B's Shift+S... after A's
    assert summary.stopped == "Traktor is not running."
    assert [r.title for r in summary.created] == ["A"]


def test_recent_stem_titles_honours_the_time_window_and_missing_files(tmp_path):
    docs = tmp_path / "Native Instruments"
    here, gone = tmp_path / "Stems" / "A.stem.mp4", tmp_path / "Stems" / "B.stem.mp4"
    here.parent.mkdir()
    here.write_bytes(b"audio")
    log = docs / "Traktor 4.5.1" / "Logs" / "Traktor.log"
    log.parent.mkdir(parents=True)

    def job(when, title, path):
        return f'{when} - Finished stem separation job for track "{title}" - result: successful: stem file stored to "{path}", with offset 0µs, elapsed time: 2m 22s\n'

    log.write_text(job("2026-10-18 20:00:00", "Recent", here) + job("2026-10-10 20:00:00", "Old", here)
                   + job("2026-10-18 20:30:00", "Deleted", gone), encoding="utf-8")
    now = datetime.datetime(2026, 10, 18, 21, 0, 0)
    assert gen.recent_stem_titles(str(docs), within_hours=24, now=now) == {"Recent"}


# ----------------------------------------------------------------------
# The macOS driver (osascript is mocked: no keystroke is ever sent)
# ----------------------------------------------------------------------

def fake_osascript(monkeypatch, replies):
    """replies: list of (returncode, stdout, stderr), consumed in order; returns the scripts that were run."""
    import subprocess
    scripts, queue = [], list(replies)

    def run(cmd, **_kwargs):
        scripts.append(cmd[-1])
        code, out, err = queue.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr(gen.subprocess, "run", run)
    monkeypatch.setattr(gen.time, "sleep", lambda _s: None)
    return scripts


def test_driver_sends_shift_s_and_down_arrow_to_the_traktor_process(monkeypatch):
    scripts = fake_osascript(monkeypatch, [(0, "Traktor Pro 4, NTKDaemon", ""), (0, "", ""), (0, "", ""), (0, "", ""), (0, "", "")])
    keys = gen.MacTraktorKeys()
    keys.generate_stems()
    keys.next_track()
    assert 'process "Traktor Pro 4" to keystroke "s" using {shift down}' in scripts[1]
    assert 'process "Traktor Pro 4" to key code 125' in scripts[2]


def test_driver_explains_blocked_keystrokes_and_refuses_when_traktor_is_not_in_front(monkeypatch):
    fake_osascript(monkeypatch, [(0, "Traktor Pro 4", ""), (1, "", "execution error: osascript is not allowed to send keystrokes. (1002)")])
    try:
        gen.MacTraktorKeys().generate_stems()
        raise AssertionError("expected DriverError")
    except gen.DriverError as e:
        assert "Accessibility" in str(e)

    fake_osascript(monkeypatch, [(0, "Traktor Pro 4", ""), (0, "", ""), (0, "false", "")])   # set frontmost ok, but not in front
    try:
        gen.MacTraktorKeys().activate()
        raise AssertionError("expected DriverError")
    except gen.DriverError as e:
        assert "did not come to the front" in str(e)


def test_driver_reports_traktor_not_running(monkeypatch):
    fake_osascript(monkeypatch, [(0, "", "")])
    try:
        gen.MacTraktorKeys().activate()
        raise AssertionError("expected DriverError")
    except gen.DriverError as e:
        assert "not running" in str(e)

