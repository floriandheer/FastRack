"""Tests for shared_console_runner: when the per-task console window closes or waits."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_console_runner as cr  # noqa: E402


@pytest.fixture
def keys(monkeypatch):
    """Fake keyboard + clock. keys.queue holds pending key presses; keys.waits counts blocking reads."""
    state = SimpleNamespace(queue=[], waits=0, now=0.0, ran=[])

    def getwch():
        state.waits += 1
        return state.queue.pop(0) if state.queue else "x"

    monkeypatch.setattr(cr, "msvcrt", SimpleNamespace(kbhit=lambda: bool(state.queue), getwch=getwch))
    monkeypatch.setattr(cr, "_set_title", lambda title: state.ran.append(("title", title)))
    monkeypatch.setattr(cr.time, "time", lambda: state.now)
    monkeypatch.setattr(cr.time, "sleep", lambda s: setattr(state, "now", state.now + s))
    return state


def _exit_code(monkeypatch, code):
    monkeypatch.setattr(cr.subprocess, "call", lambda cmd: code)


def test_success_closes_after_countdown_without_waiting(keys, monkeypatch, capsys):
    _exit_code(monkeypatch, 0)
    assert cr.run("Sync", 10, ["python", "x.py"]) == 0
    assert keys.waits == 0 and keys.now >= 10
    assert "Closing in 10s" in capsys.readouterr().out


def test_key_press_during_countdown_keeps_window_open(keys, monkeypatch):
    _exit_code(monkeypatch, 0)
    keys.queue = ["a"]
    cr.run("Sync", 10, ["python", "x.py"])
    assert keys.waits == 2          # the key that stopped the countdown + the final "press any key"
    assert keys.now < 10


def test_failure_always_waits_for_a_key(keys, monkeypatch, capsys):
    _exit_code(monkeypatch, 3)
    assert cr.run("Sync", 10, ["python", "x.py"]) == 3
    assert keys.waits == 1
    assert "FAILED (exit code 3)" in capsys.readouterr().out


def test_zero_closes_immediately_and_negative_waits(keys, monkeypatch):
    _exit_code(monkeypatch, 0)
    cr.run("Sync", 0, ["python", "x.py"])
    assert keys.waits == 0 and keys.now == 0
    cr.run("Sync", -1, ["python", "x.py"])
    assert keys.waits == 1


def test_start_failure_is_reported_as_failure(keys, monkeypatch, capsys):
    def boom(cmd):
        raise OSError("no such interpreter")
    monkeypatch.setattr(cr.subprocess, "call", boom)
    assert cr.run("Sync", 10, ["nope"]) == 1
    assert "Could not start the task" in capsys.readouterr().out and keys.waits == 1


def test_main_passes_title_delay_and_script_args(monkeypatch):
    seen = {}
    monkeypatch.setattr(cr, "run", lambda title, close_after, command: seen.update(t=title, c=close_after, cmd=command) or 0)
    assert cr.main(["FastRack - Traktor Music Sync", "10", "task.py", "--auto-run"]) == 0
    assert seen == {"t": "FastRack - Traktor Music Sync", "c": 10.0, "cmd": [sys.executable, "task.py", "--auto-run"]}
    assert cr.main(["only-a-title"]) == 2
