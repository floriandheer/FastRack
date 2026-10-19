"""Which Environment Setup steps still need action (Settings > General > Environment Setup).

The checks are read-only: they must never create the folders or the config they report on. Windows-only steps
are simulated with sys.platform and fakes for `subst`, Synology and the scheduled task. Nothing here may touch
the real rack_config.json, so HOME and the app-data folder point into tmp_path.
"""

import json
import os
import subprocess
import sys
import tkinter as tk
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

import environment_status as es  # noqa: E402
import setup_environment  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="simulates macOS/Linux with POSIX paths")

CATEGORIES = ["Visual", "Realtime", "Audio"]


def setup_config(**overrides):
    cfg = {
        "drive_mappings": [{"drive_letter": "I:", "target_path": "D:\\_work\\Active"},
                           {"drive_letter": "M:", "target_path": "D:\\music"}],
        "folder_structure": {"bases": ["D:\\_work\\Active", "D:\\_work\\Archive"], "categories": CATEGORIES,
                             "subcategories": {"Visual": ["GD"]}, "extra_dirs": ["D:\\_personal"]},
        "synology_drive": {"sync_folders": [{"name": "Pipeline", "local_path": "D:\\_work\\_PIPELINE"}]},
        "pipeline_config": {"work_drive": "I:", "active_base": "D:\\_work\\Active"},
    }
    cfg.update(overrides)
    return cfg


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    appdata = tmp_path / "appdata"
    home.mkdir()
    appdata.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(es, "get_appdata_path", lambda: appdata)
    return SimpleNamespace(home=home, appdata=appdata)


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()

    def write(cfg):
        (root / "setup_config.json").write_text(json.dumps(cfg), encoding="utf-8")
    write.root = root
    return write


@pytest.fixture
def settings(tmp_path):
    """What RackSettings gives the Folders step on a Mac: real folders under tmp_path."""
    return SimpleNamespace(get_active_base=lambda: str(tmp_path / "work" / "Active"),
                           get_archive_base=lambda: str(tmp_path / "work" / "Archive"))


# ---- the folder plan, shared with the Folders step ----------------------------------------------------------------


@posix_only
def test_folder_plan_off_windows_uses_the_settings_bases_and_skips_windows_extras(mac, settings, tmp_path):
    plan = es.plan_folders(setup_config(), settings)

    active, archive = str(tmp_path / "work" / "Active"), str(tmp_path / "work" / "Archive")
    assert plan.bases == [active, archive] and plan.used_platform_defaults
    assert plan.skipped_extra == 1 and plan.error == ""
    assert plan.dirs == [active, f"{active}/Visual", f"{active}/Visual/GD", f"{active}/Realtime", f"{active}/Audio",
                         archive, f"{archive}/Visual", f"{archive}/Visual/GD", f"{archive}/Realtime",
                         f"{archive}/Audio"]


def test_folder_plan_on_windows_uses_the_configured_bases_and_extras(windows):
    plan = es.plan_folders(setup_config())

    assert plan.bases == ["D:\\_work\\Active", "D:\\_work\\Archive"] and not plan.used_platform_defaults
    assert plan.skipped_extra == 0
    assert plan.dirs[0] == "D:\\_work\\Active" and plan.dirs[-1] == "D:\\_personal"
    assert len(plan.dirs) == 2 * 5 + 1


@posix_only
def test_folder_plan_reports_an_unusable_rack_settings(mac, monkeypatch):
    import rack_settings

    class Broken:
        def __init__(self):
            raise OSError("no config for you")
    monkeypatch.setattr(rack_settings, "RackSettings", Broken)

    plan = es.plan_folders(setup_config())

    assert "no config for you" in plan.error and plan.dirs == []


@posix_only
def test_folders_step_prints_what_it_always_did_and_creates_the_planned_folders(mac, settings, tmp_path, monkeypatch,
                                                                                capsys):
    import rack_settings
    monkeypatch.setattr(rack_settings, "RackSettings", lambda: settings)

    assert setup_environment.step_folders(setup_config(), dry_run=True, auto_yes=True)
    out = capsys.readouterr().out
    assert "Non-Windows: using platform defaults" in out
    assert str(tmp_path / "work" / "Active") in out
    assert "Skipping 1 Windows-only extra folder(s)" in out
    assert "Total directories: 10" in out and "To create:         10" in out
    assert "[DRY RUN] No directories created." in out
    assert not (tmp_path / "work").exists()

    assert setup_environment.step_folders(setup_config(), dry_run=False, auto_yes=True)
    assert (tmp_path / "work" / "Archive" / "Visual" / "GD").is_dir()
    assert (tmp_path / "work" / "Active" / "Audio").is_dir()


def test_setup_environment_shares_the_status_helpers():
    assert setup_environment.STEPS == es.STEPS
    assert setup_environment.parse_subst_output is es.parse_subst_output
    assert setup_environment.plan_folders is es.plan_folders
    assert setup_environment.find_synology_drive is es.find_synology_drive


def test_parse_subst_output(monkeypatch):
    out = "I:\\: => D:\\_work\\Active\nM:\\: => D:\\music\nnot a mapping\n"
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=out))
    assert es.parse_subst_output() == {"I:": "D:\\_work\\Active", "M:": "D:\\music"}


# ---- folders ---------------------------------------------------------------------------------------------------


@posix_only
def test_folders_need_action_until_they_exist(mac, settings, tmp_path):
    cfg = setup_config()
    status = es.check_folders(cfg, settings)
    assert status.state == es.TODO and status.needs_action
    assert "10 of 10 folders missing" in status.detail and str(tmp_path / "work" / "Active") in status.detail

    for d in es.plan_folders(cfg, settings).dirs:
        os.makedirs(d)
    assert es.check_folders(cfg, settings) == es.StepStatus(es.DONE, "all 10 folders exist")

    os.rmdir(tmp_path / "work" / "Archive" / "Visual" / "GD")
    assert "1 of 10 folders missing" in es.check_folders(cfg, settings).detail


@posix_only
def test_home_folder_is_shown_as_a_tilde(mac, isolated):
    home_settings = SimpleNamespace(get_active_base=lambda: str(isolated.home / "_work" / "Active"),
                                    get_archive_base=lambda: str(isolated.home / "_work" / "Archive"))
    assert "starting with ~/_work/Active)" in es.check_folders(setup_config(), home_settings).detail


# ---- config ----------------------------------------------------------------------------------------------------


def test_config_needs_action_until_rack_config_exists(mac, isolated):
    assert es.check_config(setup_config()).state == es.TODO
    (isolated.appdata / "rack_config.json").write_text("{}", encoding="utf-8")
    assert es.check_config(setup_config()).state == es.DONE


def test_config_that_cannot_be_read_needs_action(mac, isolated):
    (isolated.appdata / "rack_config.json").write_text("{ not json", encoding="utf-8")
    status = es.check_config(setup_config())
    assert status.state == es.TODO and "cannot be read" in status.detail


def test_config_on_windows_compares_with_setup_config(windows, isolated):
    rack = isolated.appdata / "rack_config.json"
    rack.write_text(json.dumps({"drives": {"work": "i", "active_base": "d:/_work/active"}}), encoding="utf-8")
    # same paths, written differently ('i' = 'I:', case and separators do not matter on Windows)
    assert es.check_config(setup_config()).state == es.DONE

    rack.write_text(json.dumps({"drives": {"work": "J:", "active_base": "D:\\other"}}), encoding="utf-8")
    status = es.check_config(setup_config())
    assert status.state == es.TODO and "work_drive" in status.detail and "active_base" in status.detail


# ---- Windows-only steps ------------------------------------------------------------------------------------------


def test_windows_only_steps_are_skipped_off_windows(mac, project):
    project(setup_config())
    statuses = es.check_environment(project.root)
    for step in ("drives", "synology", "startup"):
        assert statuses[step].state == es.SKIPPED, step


def test_drives_compare_the_live_subst_table(windows, monkeypatch):
    cfg = setup_config()
    monkeypatch.setattr(es, "parse_subst_output", lambda: {"I:": "d:\\_WORK\\Active\\", "M:": "D:\\music"})
    assert es.check_drives(cfg).state == es.DONE

    monkeypatch.setattr(es, "parse_subst_output", lambda: {"I:": "D:\\elsewhere"})
    status = es.check_drives(cfg)
    assert status.state == es.TODO and "I:" in status.detail and "M:" in status.detail

    assert es.check_drives(setup_config(drive_mappings=[])).state == es.DONE


def test_synology_needs_the_client_and_populated_sync_folders(windows, monkeypatch, tmp_path):
    sync = tmp_path / "sync"
    cfg = setup_config(synology_drive={"sync_folders": [{"name": "Pipeline", "local_path": str(sync)}]})

    monkeypatch.setattr(es, "find_synology_drive", lambda: (None, False))
    assert "not installed" in es.check_synology(cfg).detail

    monkeypatch.setattr(es, "find_synology_drive", lambda: ("C:\\SynologyDrive.exe", True))
    assert es.check_synology(cfg).state == es.TODO            # folder missing
    sync.mkdir()
    status = es.check_synology(cfg)
    assert status.state == es.TODO and "Pipeline" in status.detail   # folder empty
    (sync / "file.txt").write_text("x", encoding="utf-8")
    assert es.check_synology(cfg).state == es.DONE


def test_startup_needs_the_launcher_and_the_scheduled_task(windows, monkeypatch, tmp_path):
    launcher = tmp_path / "StartupLauncher.ps1"
    state = {"task": False}
    fake = types.ModuleType("startup_apps_manager")
    fake.deployed_launcher_path = lambda: launcher
    fake.is_task_installed = lambda: state["task"]
    monkeypatch.setitem(sys.modules, "startup_apps_manager", fake)

    assert "not deployed" in es.check_startup().detail
    launcher.write_text("x", encoding="utf-8")
    assert "not registered" in es.check_startup().detail
    state["task"] = True
    assert es.check_startup().state == es.DONE


# ---- all steps ---------------------------------------------------------------------------------------------------


@posix_only
def test_check_environment_on_a_mac_with_nothing_set_up(mac, project, settings):
    project(setup_config())
    statuses = es.check_environment(project.root, settings=settings)

    assert list(statuses) == list(es.STEPS)
    assert statuses["folders"].state == es.TODO and statuses["config"].state == es.TODO
    assert es.steps_needing_action(statuses) == ["folders", "config"]


def test_a_missing_setup_config_is_reported_for_the_steps_that_need_it(windows, project):
    statuses = es.check_environment(project.root)
    for step in ("folders", "drives", "synology"):
        assert statuses[step].state == es.TODO and "setup_config.json does not exist" in statuses[step].detail


def test_an_unreadable_setup_config_is_unknown_not_an_error(windows, project):
    (project.root / "setup_config.json").write_text("{ nope", encoding="utf-8")
    statuses = es.check_environment(project.root)
    assert statuses["folders"].state == es.UNKNOWN and "cannot be read" in statuses["folders"].detail


def test_a_failing_check_is_reported_as_unknown(mac, project, monkeypatch):
    project(setup_config())

    def boom(cfg):
        raise RuntimeError("disk on fire")
    monkeypatch.setattr(es, "check_config", boom)

    statuses = es.check_environment(project.root, settings=None)
    assert statuses["config"].state == es.UNKNOWN and "disk on fire" in statuses["config"].detail


@posix_only
def test_checking_creates_nothing(mac, project, settings, tmp_path, isolated, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    project(setup_config())
    before = sorted(p.name for p in tmp_path.rglob("*"))

    es.check_environment(project.root, settings=settings)

    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    assert not (isolated.appdata / "rack_config.json").exists()
    assert not (tmp_path / "work").exists()


# ---- the Settings dialog ---------------------------------------------------------------------------------------


STEP_BUTTONS = ("Run All", "Folders", "Drives", "Synology", "Config", "Startup")


def _dialog_with_status_rows(parent):
    """A SettingsDialog without its constructor, holding just the Environment Setup status lines."""
    import ui_settings_dialog as usd
    dlg = usd.SettingsDialog.__new__(usd.SettingsDialog)
    dlg._env_step_labels = {"folders": "Folders", "drives": "Drives", "synology": "Synology", "config": "Config",
                            "startup": "Startup"}
    dlg._env_status_rows = {step: tk.Label(parent, text="…") for step in es.STEPS}
    return usd, dlg


def _widgets(widget):
    found = [widget]
    for child in widget.winfo_children():
        found += _widgets(child)
    return found


def test_steps_that_need_action_are_highlighted_in_their_status_line(tk_window):
    usd, dlg = _dialog_with_status_rows(tk_window)
    dlg._apply_environment_status({
        "folders": es.StepStatus(es.TODO, "10 of 10 folders missing"),
        "drives": es.StepStatus(es.SKIPPED, "Windows only"),
        "synology": es.StepStatus(es.SKIPPED, "Windows only"),
        "config": es.StepStatus(es.DONE, "rack_config.json exists"),
        "startup": es.StepStatus(es.UNKNOWN, "could not check: x"),
    })

    rows = dlg._env_status_rows
    assert rows["folders"].cget("text") == "●  Folders  —  10 of 10 folders missing"
    assert rows["folders"].cget("fg") == usd.COLORS["warning"]
    assert rows["config"].cget("text") == "✓  Config  —  rack_config.json exists"
    assert rows["config"].cget("fg") == usd.COLORS["success"]
    assert rows["drives"].cget("text") == "–  Drives  —  Windows only"
    assert rows["drives"].cget("fg") == usd.COLORS["text_secondary"]
    assert rows["startup"].cget("text").startswith("?  Startup")


def test_a_step_that_was_done_but_needs_action_again_is_highlighted_again(tk_window):
    usd, dlg = _dialog_with_status_rows(tk_window)
    done = {step: es.StepStatus(es.DONE, "") for step in es.STEPS}
    dlg._apply_environment_status(done)
    assert dlg._env_status_rows["folders"].cget("text") == "✓  Folders"

    dlg._apply_environment_status({**done, "folders": es.StepStatus(es.TODO, "1 of 10 folders missing")})
    assert dlg._env_status_rows["folders"].cget("text").startswith("●  Folders")
    assert dlg._env_status_rows["folders"].cget("fg") == usd.COLORS["warning"]


def test_the_step_buttons_keep_their_normal_look(tk_window, monkeypatch, tmp_path):
    """Only the status lines carry the highlight; the Run All / step buttons are never recoloured or relabelled."""
    import time
    import ui_settings_dialog as usd
    statuses = {"folders": es.StepStatus(es.TODO, "14 of 14 folders missing"),
                "drives": es.StepStatus(es.SKIPPED, "Windows only"),
                "synology": es.StepStatus(es.SKIPPED, "Windows only"),
                "config": es.StepStatus(es.DONE, "rack_config.json exists"),
                "startup": es.StepStatus(es.UNKNOWN, "could not check: x")}
    monkeypatch.setattr(es, "check_environment", lambda root, settings=None: statuses)

    dlg = usd.SettingsDialog.__new__(usd.SettingsDialog)
    dlg.dialog = tk_window
    dlg.settings = SimpleNamespace(get_start_fullscreen=lambda: False, get_always_on_bottom=lambda: False)
    dlg._async_q = __import__("queue").Queue()
    dlg._async_pending = 0
    dlg._project_root = lambda: str(tmp_path)
    page = tk.Frame(tk_window)
    dlg._build_general_tab(page)

    deadline = time.time() + 5
    while dlg._env_statuses is None and time.time() < deadline:
        tk_window.update()
        time.sleep(0.02)
    assert dlg._env_statuses == statuses

    buttons = [w for w in _widgets(page) if w.winfo_class() in ("Label", "Button") and w.cget("text") in STEP_BUTTONS]
    assert [b.cget("text") for b in buttons] == list(STEP_BUTTONS)
    for button in buttons:
        assert button.cget("bg") == usd.COLORS["bg_secondary"] and button.cget("fg") == usd.COLORS["text_primary"]
    assert dlg._env_status_rows["folders"].cget("fg") == usd.COLORS["warning"]


@posix_only
def test_on_a_mac_a_setup_step_opens_a_terminal_window(monkeypatch, tmp_path):
    import ui_settings_dialog as usd
    monkeypatch.setattr(sys, "platform", "darwin")
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    dlg = usd.SettingsDialog.__new__(usd.SettingsDialog)

    dlg._launch_in_console("/Users/me/Fast Rack", ["/usr/bin/python3", "/Users/me/Fast Rack/setup_environment.py",
                                                    "--step", "folders"])

    (argv, _), = launched
    assert argv[0] == "open" and argv[1].endswith(".command")
    script = Path(argv[1])
    try:
        body = script.read_text(encoding="utf-8")
        assert os.access(script, os.X_OK)
        assert "cd '/Users/me/Fast Rack'\n" in body
        assert "/usr/bin/python3 '/Users/me/Fast Rack/setup_environment.py' --step folders\n" in body
        assert "read" in body                       # window stays open until Enter
    finally:
        script.unlink()


def test_on_windows_a_setup_step_keeps_its_own_console_window(monkeypatch):
    import ui_settings_dialog as usd
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)
    launched = []
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: launched.append((argv, kw)))
    dlg = usd.SettingsDialog.__new__(usd.SettingsDialog)

    dlg._launch_in_console("C:\\FastRack", ["python.exe", "setup_environment.py", "--step", "drives"])

    assert launched == [(["cmd", "/k", "python.exe", "setup_environment.py", "--step", "drives"],
                         {"cwd": "C:\\FastRack", "creationflags": 16})]


def test_starting_a_step_arms_the_refresh_for_when_the_user_returns(monkeypatch, tmp_path):
    import ui_settings_dialog as usd
    (tmp_path / "setup_environment.py").write_text("", encoding="utf-8")
    dlg = usd.SettingsDialog.__new__(usd.SettingsDialog)
    dlg.dialog = None
    dlg._env_refresh_until = 0.0
    dlg._project_root = lambda: str(tmp_path)
    dlg._ensure_setup_config = lambda root: True
    dlg._console_python = lambda: "python"
    monkeypatch.setattr(usd.messagebox, "askyesno", lambda *a, **k: True)
    started = []
    dlg._launch_in_console = lambda root, argv: started.append(argv)

    dlg._run_environment_setup("folders")

    assert started == [["python", str(tmp_path / "setup_environment.py"), "--step", "folders"]]
    assert dlg._env_refresh_until > 0

    refreshed = []
    dlg._refresh_environment_status = lambda: refreshed.append(1)
    dlg.dialog = SimpleNamespace()
    dlg._on_dialog_focus_in(SimpleNamespace(widget=dlg.dialog))
    dlg._on_dialog_focus_in(SimpleNamespace(widget=object()))       # a child widget: ignored
    assert refreshed == [1]
    dlg._env_refresh_until = 0.0
    dlg._on_dialog_focus_in(SimpleNamespace(widget=dlg.dialog))     # long after: ignored
    assert refreshed == [1]


# ---- opening the Synology Drive download page after the folders / drives ----------------------------------------


def test_the_download_page_is_the_dutch_synology_drive_page():
    assert es.SYNOLOGY_DRIVE_URL == "https://www.synology.com/nl-nl/dsm/feature/drive"
    assert setup_environment.SYNOLOGY_DRIVE_URL == es.SYNOLOGY_DRIVE_URL


def test_synology_client_install_check_per_platform(monkeypatch, tmp_path):
    app = tmp_path / "Synology Drive Client.app"
    monkeypatch.setattr(es, "MAC_SYNOLOGY_APPS", (str(app),))

    monkeypatch.setattr(sys, "platform", "darwin")
    assert es.synology_client_installed() is False
    app.mkdir()
    assert es.synology_client_installed() is True

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(es, "find_synology_drive", lambda: (None, False))
    assert es.synology_client_installed() is False
    monkeypatch.setattr(es, "find_synology_drive", lambda: ("C:\\SynologyDrive.exe", True))
    assert es.synology_client_installed() is True

    monkeypatch.setattr(sys, "platform", "linux")
    assert es.synology_client_installed() is True        # not checked there: never nag


@pytest.fixture
def run_setup(monkeypatch, capsys):
    """Run setup_environment.main() with every step stubbed; returns (browser_urls, steps_run)."""
    urls, ran = [], []
    monkeypatch.setattr(setup_environment.webbrowser, "open", lambda url, *a, **k: urls.append(url) or True)
    monkeypatch.setattr(setup_environment, "setup_logging", lambda *a, **k: None)
    monkeypatch.setattr(setup_environment, "load_config", lambda path: {})
    monkeypatch.setattr(setup_environment, "final_report", lambda results: None)
    monkeypatch.setattr(setup_environment, "step_prerequisites", lambda cfg: True)
    monkeypatch.setattr(setup_environment, "step_folders", lambda *a: ran.append("folders") or True)
    monkeypatch.setattr(setup_environment, "step_drives", lambda *a: ran.append("drives") or True)
    monkeypatch.setattr(setup_environment, "step_synology", lambda *a: ran.append("synology") or True)
    monkeypatch.setattr(setup_environment, "step_config", lambda *a: ran.append("config") or True)
    monkeypatch.setattr(setup_environment, "step_startup", lambda *a: ran.append("startup") or True)
    monkeypatch.setattr(es, "synology_client_installed", lambda: False)
    monkeypatch.setattr(setup_environment, "synology_client_installed", lambda: False)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["setup_environment.py", *args])
        setup_environment.main()
        return urls, ran
    return run


@pytest.mark.parametrize("args", [("--step", "folders"), ("--step", "drives"), ()])
def test_after_the_folders_or_drives_the_download_page_opens_once(run_setup, capsys, args):
    urls, _ = run_setup(*args)
    assert urls == ["https://www.synology.com/nl-nl/dsm/feature/drive"]
    assert "Synology Drive Client is not installed" in capsys.readouterr().out


def test_the_page_stays_closed_when_nothing_was_created_or_it_is_not_needed(run_setup, monkeypatch):
    assert run_setup("--step", "config")[0] == []                       # folders / drives did not run
    assert run_setup("--step", "synology")[0] == []
    assert run_setup("--step", "folders", "--dry-run")[0] == []         # a dry run changes nothing

    monkeypatch.setattr(setup_environment, "synology_client_installed", lambda: True)
    assert run_setup("--step", "folders")[0] == []                      # already installed


def test_the_page_stays_closed_when_the_folders_could_not_be_created(run_setup, monkeypatch):
    monkeypatch.setattr(setup_environment, "step_folders", lambda *a: False)
    assert run_setup("--step", "folders")[0] == []


def test_a_browser_that_cannot_be_opened_does_not_stop_the_setup(run_setup, monkeypatch, capsys):
    def broken(*a, **k):
        raise RuntimeError("no browser")
    monkeypatch.setattr(setup_environment.webbrowser, "open", broken)
    run_setup("--step", "folders")
    assert "Could not open the browser: no browser" in capsys.readouterr().out
