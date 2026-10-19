"""The Project Finder's grid-size slider must not freeze the app.

The slider's command rebuilt the whole grid from inside the Scale's own redraw; the rebuild ends in
update_idletasks(), which redraws the Scale, which runs the command again - endlessly on macOS (Tk 9). The grid is
now rebuilt from the event loop, once per burst of slider movement, and a rebuild can never start inside another.
All files live in tmp_path; the real config, project database and tracker settings are never touched.
"""

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

import fastrack_project_explorer as fpe  # noqa: E402
import shared_project_db  # noqa: E402

PROJECTS = 12


@pytest.fixture
def app(tk_window, tmp_path, monkeypatch):
    active, archive = tmp_path / "_work" / "Active", tmp_path / "_work" / "Archive"
    for n in range(PROJECTS):
        (active / "Photo" / "_Personal" / f"2025-10-{n + 1:02d}_Zele_Shoot{n}").mkdir(parents=True)
    archive.mkdir(parents=True)
    settings = SimpleNamespace(
        get_active_base=lambda: str(active), get_archive_base=lambda: str(archive),
        get_work_drive=lambda: str(active), convert_to_work_drive_path=lambda p: p,
        get_work_path=lambda category: str(active / category), get_archive_path=lambda c: str(archive / c),
    )
    monkeypatch.setattr(fpe, "get_rack_settings", lambda: settings)
    monkeypatch.setattr(shared_project_db, "get_rack_settings", lambda: settings)
    monkeypatch.setattr(fpe, "get_appdata_path", lambda: tmp_path)
    real_db = shared_project_db.ProjectDatabase
    monkeypatch.setattr(fpe, "ProjectDatabase", lambda *a, **k: real_db(db_path=str(tmp_path / "db.json")))

    tracker = fpe.ProjectTrackerApp(tk_window, embedded=True)
    tracker.refresh_and_import(silent=True)
    tracker.view_mode.set("grid")
    tracker._switch_view()
    tk_window.update()
    tracker.tmp_path = tmp_path
    return tracker


def settle(window, seconds=0.5):
    end = time.time() + seconds
    while time.time() < end:
        window.update()
        time.sleep(0.01)


def count_rebuilds(app, monkeypatch):
    """Wrap the rebuild; returns a dict with how often it ran and how deeply it nested."""
    seen = {"runs": 0, "depth": 0, "max_depth": 0}
    original = fpe.ProjectTrackerApp._populate_grid_now

    def wrapped(self):
        seen["runs"] += 1
        seen["depth"] += 1
        seen["max_depth"] = max(seen["max_depth"], seen["depth"])
        try:
            return original(self)
        finally:
            seen["depth"] -= 1
    monkeypatch.setattr(fpe.ProjectTrackerApp, "_populate_grid_now", wrapped)
    return seen


def test_the_slider_command_only_schedules_the_rebuild(app, tk_window, monkeypatch):
    seen = count_rebuilds(app, monkeypatch)
    app.grid_scale_value.set(180)

    app._on_grid_scale_changed(180)

    assert seen["runs"] == 0                    # nothing is rebuilt inside the Scale's callback
    settle(tk_window)
    assert seen["runs"] == 1
    assert app._get_scaled_card_size() == 216 and len(app.grid_cards) == PROJECTS
    assert json.loads((app.tmp_path / "tracker_settings.json").read_text(encoding="utf-8"))["grid_scale"] == 180


def test_a_burst_of_slider_steps_is_one_rebuild_with_the_last_value(app, tk_window, monkeypatch):
    seen = count_rebuilds(app, monkeypatch)
    for value in range(100, 226, 5):
        app.grid_scale_value.set(value)
        app._on_grid_scale_changed(value)
        tk_window.update()

    settle(tk_window)

    assert seen["runs"] == 1 and seen["max_depth"] == 1
    assert app._get_scaled_card_size() == 270
    assert json.loads((app.tmp_path / "tracker_settings.json").read_text(encoding="utf-8"))["grid_scale"] == 225


def test_dragging_the_real_slider_does_not_recurse(app, tk_window, monkeypatch):
    tk_window.deiconify()
    tk_window.update()
    seen = count_rebuilds(app, monkeypatch)
    try:
        for value in range(100, 226, 25):
            app.grid_scale_slider.set(value)
            settle(tk_window, 0.15)
        settle(tk_window)
    finally:
        tk_window.withdraw()

    assert seen["max_depth"] == 1 and seen["runs"] <= 8
    assert app._get_scaled_card_size() == int(120 * app.grid_scale_value.get() / 100)
    assert len(app.grid_cards) == PROJECTS


def test_a_rebuild_asked_for_during_a_rebuild_runs_afterwards_not_inside(app, tk_window, monkeypatch):
    runs = {"count": 0, "depth": 0, "max_depth": 0}
    original = fpe.ProjectTrackerApp._populate_grid_now

    def reentrant(self):
        runs["count"] += 1
        runs["depth"] += 1
        runs["max_depth"] = max(runs["max_depth"], runs["depth"])
        try:
            if runs["count"] == 1:
                self._populate_grid()           # e.g. a <Configure> processed by update_idletasks()
            return original(self)
        finally:
            runs["depth"] -= 1
    monkeypatch.setattr(fpe.ProjectTrackerApp, "_populate_grid_now", reentrant)

    app._populate_grid()
    settle(tk_window)

    assert runs["count"] == 2 and runs["max_depth"] == 1       # one extra pass, never nested
    assert len(app.grid_cards) == PROJECTS
