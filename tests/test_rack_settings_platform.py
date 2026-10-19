"""Drive / path settings must behave on macOS and Linux, and stay as they were on Windows.

setup_config.json describes the Windows PC (subst drive letters, D:\\_work\\...). A brand-new rack_config.json on
a Mac used to be seeded from it, so every path helper returned a path relative to the current working directory
(which is how literal 'D:\\_work\\Active' folders appeared inside the repo). These tests simulate the platform with
sys.platform and keep every file in a tmp dir; a fake HOME makes sure nothing is created in the real one.
"""

import json
import logging
import os
import shutil
import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import rack_settings  # noqa: E402
from rack_settings import RackSettings  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="simulates macOS/Linux with POSIX path joining")

WINDOWS_PIPELINE_CONFIG = {
    "work_drive": "I:",
    "active_base": "D:\\_work\\Active",
    "archive_base": "D:\\_work\\Archive",
    "mapped_software_path": "P:\\Software",
    "launchers_base_path": "P:\\Launchers",
}

WINDOWS_RACK_CONFIG = {
    "version": "1.1.0",
    "drives": {"work": "I:", "active_base": "D:\\_work\\Active", "archive_base": "D:\\_work\\Archive"},
    "software_sync": {
        "nas_software_path": "D:\\_work\\_PIPELINE\\Software",
        "mapped_software_path": "P:\\Software",
        "launchers_base_path": "P:\\Launchers",
    },
    "ui": {"start_fullscreen": True},
    "software_defaults": {"blender": "9.9"},
    "categories": {"Visual": {"work_subpath": "Visuals", "archive_subpath": "Visual", "subcategories": ["GD"]}},
}


@pytest.fixture(autouse=True)
def fake_home(tmp_path, monkeypatch):
    """HOME inside tmp_path (platform defaults resolve there) and DEFAULT_CONFIG rebuilt from it."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    defaults = json.loads(json.dumps(RackSettings.DEFAULT_CONFIG))
    defaults["drives"] = {"work": rack_settings._default_work_root(),
                          "active_base": rack_settings._default_active_base(),
                          "archive_base": rack_settings._default_archive_base()}
    defaults["software_sync"] = rack_settings._default_software_sync()
    monkeypatch.setattr(RackSettings, "DEFAULT_CONFIG", defaults)
    monkeypatch.setattr(rack_settings, "_reported_bad_paths", set())
    return home


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def seed_file(tmp_path, monkeypatch):
    """Point _load_setup_seed at a setup_config.json in tmp_path; returns a writer for its pipeline_config."""
    (tmp_path / "modules").mkdir()
    monkeypatch.setattr(rack_settings, "__file__", str(tmp_path / "modules" / "rack_settings.py"))

    def write(pipeline_config):
        (tmp_path / "setup_config.json").write_text(json.dumps({"pipeline_config": pipeline_config}), encoding="utf-8")
    return write


@pytest.fixture
def config_path(tmp_path):
    return tmp_path / "appdata" / "rack_config.json"


def write_config(path, config):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def backups(path):
    return sorted(p for p in path.parent.iterdir() if p.name.startswith(path.name + ".bak"))


def assert_native_posix_path(path):
    assert path.startswith("/"), path
    assert "\\" not in path, path


# ---- (1) the first-run seed ----------------------------------------------------------------------------------


@posix_only
def test_seed_ignores_windows_values_off_windows(mac, seed_file):
    seed_file(WINDOWS_PIPELINE_CONFIG)
    assert rack_settings._load_setup_seed() is None


@posix_only
def test_seed_keeps_the_posix_values_and_drops_the_windows_ones(mac, seed_file):
    seed_file({**WINDOWS_PIPELINE_CONFIG, "active_base": "/Volumes/Work/Active", "launchers_base_path": ""})
    assert rack_settings._load_setup_seed() == {
        "drives": {"active_base": "/Volumes/Work/Active"},
        "software_sync": {},
    }


@posix_only
def test_a_new_config_off_windows_gets_the_platform_defaults(mac, seed_file, config_path):
    seed_file(WINDOWS_PIPELINE_CONFIG)
    settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"] == RackSettings.DEFAULT_CONFIG["drives"]
    assert settings.config["software_sync"] == RackSettings.DEFAULT_CONFIG["software_sync"]
    on_disk = json.loads(config_path.read_text(encoding="utf-8"))
    assert on_disk["drives"] == settings.config["drives"]
    assert_native_posix_path(settings.get_active_path("Visual"))


def test_seed_on_windows_is_passed_through_unchanged(windows, seed_file):
    seed_file({**WINDOWS_PIPELINE_CONFIG, "launchers_base_path": ""})
    assert rack_settings._load_setup_seed() == {
        "drives": {"work": "I:", "active_base": "D:\\_work\\Active", "archive_base": "D:\\_work\\Archive"},
        "software_sync": {"mapped_software_path": "P:\\Software", "launchers_base_path": ""},
    }


def test_new_config_on_windows_is_still_seeded_with_the_windows_values(windows, seed_file, config_path):
    seed_file(WINDOWS_PIPELINE_CONFIG)
    settings = RackSettings(config_path=str(config_path))
    assert settings.config["drives"] == {"work": "I:", "active_base": "D:\\_work\\Active",
                                         "archive_base": "D:\\_work\\Archive"}
    assert settings.config["software_sync"]["mapped_software_path"] == "P:\\Software"
    assert backups(config_path) == []


# ---- (2) the one-time migration of an existing config ----------------------------------------------------------


@posix_only
def test_existing_windows_config_is_repaired_with_a_backup_and_a_log_line(mac, config_path, caplog):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    original_bytes = config_path.read_bytes()

    with caplog.at_level(logging.INFO, logger="rack_settings"):
        settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"] == RackSettings.DEFAULT_CONFIG["drives"]
    assert settings.config["software_sync"] == RackSettings.DEFAULT_CONFIG["software_sync"]
    assert settings.config["software_sync"]["nas_software_path"] == ""
    # everything that was not a Windows path is kept as the user had it
    assert settings.config["ui"]["start_fullscreen"] is True
    assert settings.config["software_defaults"]["blender"] == "9.9"
    assert settings.config["categories"]["Visual"]["work_subpath"] == "Visuals"

    (backup,) = backups(config_path)
    assert backup.read_bytes() == original_bytes
    assert json.loads(config_path.read_text(encoding="utf-8"))["drives"] == settings.config["drives"]
    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "falling back to platform defaults" in messages and "D:\\\\_work\\\\Active" in messages and backup.name in messages


@posix_only
def test_migration_runs_once(mac, config_path):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    RackSettings(config_path=str(config_path))
    repaired_bytes = config_path.read_bytes()

    RackSettings(config_path=str(config_path))

    assert len(backups(config_path)) == 1
    assert config_path.read_bytes() == repaired_bytes


@posix_only
def test_migration_leaves_a_good_posix_config_alone(mac, config_path):
    good = {"version": "1.1.0",
            "drives": {"work": "/Volumes/Work/Active", "active_base": "/Volumes/Work/Active",
                       "archive_base": "/Volumes/Work/Archive"},
            "software_sync": {"mapped_software_path": "/Volumes/Work/Software", "launchers_base_path": ""}}
    write_config(config_path, good)
    before = config_path.read_bytes()

    settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"] == good["drives"]
    assert config_path.read_bytes() == before
    assert backups(config_path) == []


@posix_only
def test_a_windows_work_root_follows_a_valid_custom_active_base(mac, config_path):
    config = json.loads(json.dumps(WINDOWS_RACK_CONFIG))
    config["drives"]["active_base"] = "/Volumes/Work/Active"
    write_config(config_path, config)

    settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"]["active_base"] == "/Volumes/Work/Active"
    assert settings.config["drives"]["work"] == "/Volumes/Work/Active"
    assert settings.config["drives"]["archive_base"] == RackSettings.DEFAULT_CONFIG["drives"]["archive_base"]


@posix_only
def test_failed_backup_keeps_the_file_untouched_but_still_repairs_this_session(mac, config_path, monkeypatch):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    before = config_path.read_bytes()

    def broken_copy(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(shutil, "copy2", broken_copy)

    settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"] == RackSettings.DEFAULT_CONFIG["drives"]
    assert config_path.read_bytes() == before
    assert backups(config_path) == []


@posix_only
def test_an_existing_backup_is_never_overwritten(mac, config_path):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    first = config_path.with_name(config_path.name + ".bak-windows-paths-20000101-000000")
    first.write_text("precious", encoding="utf-8")

    RackSettings(config_path=str(config_path))

    assert first.read_text(encoding="utf-8") == "precious"
    assert len(backups(config_path)) == 2


def test_windows_config_is_never_migrated_on_windows(windows, config_path):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    before = config_path.read_bytes()

    settings = RackSettings(config_path=str(config_path))

    assert settings.config["drives"] == WINDOWS_RACK_CONFIG["drives"]
    assert settings.config["software_sync"] == WINDOWS_RACK_CONFIG["software_sync"]
    assert config_path.read_bytes() == before
    assert backups(config_path) == []


# ---- (3) the path helpers ---------------------------------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("bad", ["I:", "I:\\", "D:\\_work\\Active", "D:/_work/Active", "\\\\nas\\share",
                                 "relative/dir", "_work", "", None])
def test_path_helpers_never_return_a_relative_or_backslash_path_off_windows(mac, config_path, bad):
    settings = RackSettings(config_path=str(config_path))
    settings.config["drives"].update(work=bad, active_base=bad, archive_base=bad)

    active_default = rack_settings._default_active_base()
    archive_default = rack_settings._default_archive_base()
    for category in settings.CATEGORY_ORDER:
        for path in (settings.get_work_path(category), settings.get_active_path(category),
                     settings.get_archive_path(category)):
            assert_native_posix_path(path)
        assert settings.get_work_path(category).startswith(active_default)
        assert settings.get_active_path(category).startswith(active_default)
        assert settings.get_archive_path(category).startswith(archive_default)


@posix_only
def test_a_bad_work_root_falls_back_to_the_configured_active_base(mac, config_path):
    settings = RackSettings(config_path=str(config_path))
    settings.config["drives"].update(work="I:", active_base="/Volumes/Work/Active")

    assert settings.get_work_path("Audio") == "/Volumes/Work/Active/Audio"


@posix_only
def test_good_posix_bases_are_used_as_they_are(mac, config_path, fake_home):
    settings = RackSettings(config_path=str(config_path))
    settings.config["drives"].update(work="/Volumes/Work/Active", active_base="/Volumes/Work/Active",
                                     archive_base="~/Archive")

    assert settings.get_work_path("Visual") == "/Volumes/Work/Active/Visual"
    assert settings.get_active_path("Visual") == "/Volumes/Work/Active/Visual"
    assert settings.get_archive_path("Visual") == f"{fake_home}/Archive/Visual"


@posix_only
def test_backslashes_in_a_category_subpath_become_slashes_off_windows(mac, config_path):
    settings = RackSettings(config_path=str(config_path))
    settings.config["categories"]["Visual"]["work_subpath"] = "Visual\\GD"
    settings.config["categories"]["Visual"]["archive_subpath"] = "Old\\Visual"

    assert settings.get_active_path("Visual").endswith("/Active/Visual/GD")
    assert settings.get_work_path("Visual").endswith("/Visual/GD")
    assert settings.get_archive_path("Visual").endswith("/Archive/Old/Visual")


def test_path_helpers_do_not_fall_back_on_windows(windows, config_path):
    write_config(config_path, WINDOWS_RACK_CONFIG)
    settings = RackSettings(config_path=str(config_path))

    assert settings.get_active_path("Audio") == os.path.join("D:\\_work\\Active", "Audio")
    assert settings.get_archive_path("Audio") == os.path.join("D:\\_work\\Archive", "Audio")
    assert settings.get_work_path("Audio").startswith("I:")
    assert settings.get_work_path("Visual").endswith("Visuals")


# ---- (5) nothing is created on disk --------------------------------------------------------------------------


@posix_only
def test_loading_and_resolving_paths_creates_no_folders(mac, tmp_path, config_path, fake_home, monkeypatch, seed_file):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    seed_file(WINDOWS_PIPELINE_CONFIG)
    write_config(config_path, WINDOWS_RACK_CONFIG)

    settings = RackSettings(config_path=str(config_path))
    RackSettings(config_path=str(tmp_path / "other" / "rack_config.json"))
    for category in settings.CATEGORY_ORDER:
        settings.get_work_path(category)
        settings.get_active_path(category)
        settings.get_archive_path(category)
    settings.validate_all()

    assert list(cwd.iterdir()) == []
    assert list(fake_home.iterdir()) == []


# ---- (4) the Drives tab ----------------------------------------------------------------------------------------


def _drives_tab(parent, tmp_path, monkeypatch, platform):
    """Build the real Drives tab on a stub that supplies only what it needs."""
    import ui_settings_dialog
    monkeypatch.setattr(sys, "platform", platform)
    stub = SimpleNamespace(
        _project_root=lambda: str(tmp_path),
        _drives_load_config=lambda: {"drive_mappings": []},
        _drives_render_list=lambda: None,
        _drives_add_mapping=lambda: None,
        _drives_reload=lambda: None,
        _run_environment_setup=lambda step: None,
        dialog=parent,
    )
    ui_settings_dialog.SettingsDialog._build_drives_tab(stub, parent)
    return stub


def _labels(widget):
    found = [widget] if isinstance(widget, tk.Label) else []
    for child in widget.winfo_children():
        found += _labels(child)
    return found


@posix_only
def test_drives_tab_off_windows_explains_and_disables_the_run_button(tk_window, tmp_path, monkeypatch):
    stub = _drives_tab(tk_window, tmp_path, monkeypatch, "darwin")

    assert stub._drives_run_btn.cget("state") == tk.DISABLED
    note = stub._drives_platform_note
    assert note is not None and note.winfo_manager() == "pack"
    assert "Windows only" in note.cget("text") and "subst" in note.cget("text")


def test_drives_tab_on_windows_is_unchanged(tk_window, tmp_path, monkeypatch):
    stub = _drives_tab(tk_window, tmp_path, monkeypatch, "win32")

    assert stub._drives_run_btn.cget("state") == tk.NORMAL
    assert stub._drives_platform_note is None
    assert not any("Windows only" in str(label.cget("text")) for label in _labels(tk_window))


# ---- software paths: the _PIPELINE layout -------------------------------------------------------------------------


@posix_only
def test_software_defaults_off_windows_mirror_the_windows_pipeline_folder(mac, fake_home):
    assert rack_settings._default_software_sync() == {
        "nas_software_path": "",
        "mapped_software_path": f"{fake_home}/_work/_PIPELINE/Software",
        "launchers_base_path": f"{fake_home}/_work/_PIPELINE/Launchers",
    }


def test_software_defaults_on_windows_are_unchanged(windows):
    assert rack_settings._default_software_sync() == {
        "nas_software_path": "D:\\_work\\_PIPELINE\\Software",
        "mapped_software_path": "P:\\Software",
        "launchers_base_path": "P:\\Launchers",
    }


@posix_only
def test_the_old_software_defaults_move_to_the_pipeline_folder_with_a_backup(mac, config_path, fake_home):
    old = {"version": "1.1.0", "software_sync": {"nas_software_path": "",
                                                 "mapped_software_path": f"{fake_home}/_work/Software",
                                                 "launchers_base_path": f"{fake_home}/_work/Launchers"}}
    write_config(config_path, old)
    original_bytes = config_path.read_bytes()

    settings = RackSettings(config_path=str(config_path))

    assert settings.get_mapped_software_path() == f"{fake_home}/_work/_PIPELINE/Software"
    assert settings.get_launchers_base_path() == f"{fake_home}/_work/_PIPELINE/Launchers"
    (backup,) = backups(config_path)
    assert backup.read_bytes() == original_bytes
    assert not (fake_home / "_work").exists()                       # nothing is created


@posix_only
def test_old_software_folders_that_exist_or_custom_values_are_left_alone(mac, config_path, fake_home):
    (fake_home / "_work" / "Software").mkdir(parents=True)          # set up on purpose
    config = {"version": "1.1.0", "software_sync": {"nas_software_path": "",
                                                    "mapped_software_path": f"{fake_home}/_work/Software",
                                                    "launchers_base_path": "/Volumes/Tools/Launchers"}}
    write_config(config_path, config)
    before = config_path.read_bytes()

    settings = RackSettings(config_path=str(config_path))

    assert settings.get_mapped_software_path() == f"{fake_home}/_work/Software"
    assert settings.get_launchers_base_path() == "/Volumes/Tools/Launchers"
    assert config_path.read_bytes() == before and backups(config_path) == []
