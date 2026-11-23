"""Tests for the startup-apps launcher (tools/startup/StartupLauncher.ps1)
and its config owner (modules/startup_apps_manager.py).

The launcher is PowerShell, so its decision logic - which window belongs
to which launched app - is kept in side-effect-free functions that the
script defines before doing anything. These tests dot-source the script
with ``-DefineOnly`` in one Windows PowerShell process and feed those
functions synthetic process tables / window lists. No app is launched and
no window is touched.

The cases mirror what the real logon log showed going wrong: apps started
through a stub (py.exe, a .bat, msedge_proxy.exe) were looked up by the
stub's process name, never found, and burned their whole timeout.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import startup_apps_manager as sam  # noqa: E402

LAUNCHER = REPO_ROOT / "tools" / "startup" / "StartupLauncher.ps1"
SEND_F11 = REPO_ROOT / "tools" / "startup" / "SendF11.ahk"

PROCESS_REMAP = {"msedge_proxy.exe": "msedge", "launcher.exe": "SynologyDrive"}

# tools/* is gitignored, so a fresh clone has no launcher source to test.
needs_launcher_source = pytest.mark.skipif(
    not (LAUNCHER.is_file() and SEND_F11.is_file()),
    reason="tools/startup/ launcher source not present in this checkout",
)

_DRIVER = r"""
param([string]$Launcher, [string]$CasesPath)
$ErrorActionPreference = 'Stop'
. $Launcher -DefineOnly

function ConvertTo-IntSet($items) {
    $set = New-Object 'System.Collections.Generic.HashSet[int]'
    foreach ($i in @($items)) { if ($null -ne $i) { [void]$set.Add([int]$i) } }
    return ,$set
}
function ConvertTo-Table($obj) {
    $t = @{}
    if ($obj) { foreach ($p in $obj.PSObject.Properties) { $t[$p.Name.ToLower()] = [string]$p.Value } }
    return $t
}

$cases = Get-Content $CasesPath -Raw -Encoding UTF8 | ConvertFrom-Json
$out = @{}
foreach ($c in $cases) {
    switch ($c.fn) {
        'names' {
            $out[$c.id] = @(Get-ExpectedProcessNames -Path $c.path -ProcessRemap (ConvertTo-Table $c.remap))
        }
        'tree' {
            $parents = @{}
            foreach ($p in $c.parents.PSObject.Properties) { $parents[[int]$p.Name] = [int]$p.Value }
            $set = Get-DescendantPids -RootPid $c.root -ParentMap $parents -BaselinePids (ConvertTo-IntSet $c.baseline)
            $out[$c.id] = @($set | Sort-Object)
        }
        'select' {
            $spec = @{ Title = [string]$c.spec.Title; ProcessNames = @($c.spec.ProcessNames); Hints = @($c.spec.Hints) }
            $tree = if ($null -ne $c.tree) { ConvertTo-IntSet $c.tree } else { $null }
            $foreign = if ($null -ne $c.foreign) { ConvertTo-IntSet $c.foreign } else { $null }
            $pick = Select-AppWindow -Spec $spec -Windows @($c.windows) -TreePids $tree -ForeignPids $foreign -NoHints:([bool]$c.noHints)
            if ($pick) {
                $out[$c.id] = @{ hwnd = $pick.Window.Hwnd; rule = $pick.Rule; provisional = $pick.Provisional }
            } else {
                $out[$c.id] = $null
            }
        }
        'existing' {
            $spec = @{ Label = [string]$c.spec.Label; Title = [string]$c.spec.Title; ProcessNames = @($c.spec.ProcessNames); Hints = @($c.spec.Hints) }
            $pick = Select-ExistingAppWindow -Spec $spec -Windows @($c.windows)
            if ($pick) { $out[$c.id] = @{ hwnd = $pick.Window.Hwnd; rule = $pick.Rule } } else { $out[$c.id] = $null }
        }
        'spec' {
            $s = Get-AppSpec -AppEntry $c.app -ProcessRemap (ConvertTo-Table $c.remap) -WindowTitleRemap (ConvertTo-Table $c.titles)
            $out[$c.id] = @{ label = $s.Label; title = $s.Title; names = @($s.ProcessNames); hints = @($s.Hints) }
        }
        'wait' {
            $out[$c.id] = Get-AppWaitMs -AppEntry $c.app -FloorMs $c.floor
        }
    }
}
$out | ConvertTo-Json -Depth 6 -Compress
"""


def _win(hwnd, pid, proc, title, cls="SomeWindowClass", framed=True, area=1000):
    return {"Hwnd": hwnd, "Pid": pid, "ProcessName": proc, "ClassName": cls,
            "Title": title, "Framed": framed, "Area": area}


def _spec(names=(), hints=(), title=""):
    return {"Title": title, "ProcessNames": list(names), "Hints": list(hints)}


# One PowerShell start-up for the whole module: every case is declared here
# and looked up by id in the tests below.
CASES = [
    # --- Get-ExpectedProcessNames -------------------------------------
    {"id": "names_plain", "fn": "names", "remap": PROCESS_REMAP,
     "path": r"C:\Program Files\Mozilla Thunderbird\thunderbird.exe"},
    {"id": "names_remap", "fn": "names", "remap": PROCESS_REMAP,
     "path": r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge_proxy.exe"},
    {"id": "names_pyw", "fn": "names", "remap": PROCESS_REMAP,
     "path": r"C:\Users\me\AppData\Local\Programs\Python\Launcher\pyw.EXE"},
    {"id": "names_bat", "fn": "names", "remap": PROCESS_REMAP,
     "path": "D:/tools/Track/run_track.bat"},

    # --- Get-DescendantPids -------------------------------------------
    # pyw.exe (100) -> pythonw.exe (101): the window lives in the child.
    {"id": "tree_py_launcher", "fn": "tree", "root": 100, "baseline": [1, 4],
     "parents": {"1": 0, "4": 0, "100": 4, "101": 100, "200": 4}},
    # run.bat: cmd.exe (300) has already exited and is gone from the table;
    # its children still name it as parent.
    {"id": "tree_dead_middle", "fn": "tree", "root": 300, "baseline": [1],
     "parents": {"1": 0, "301": 300, "302": 301}},
    # PID 500 existed before launch, so "parent = 100" on it is a recycled
    # PID, not a child of what we started.
    {"id": "tree_recycled_pid", "fn": "tree", "root": 100, "baseline": [500],
     "parents": {"100": 4, "500": 100, "501": 500}},
    {"id": "tree_no_root", "fn": "tree", "root": 0, "baseline": [],
     "parents": {"100": 4}},

    # --- Select-AppWindow ---------------------------------------------
    # FastRack via pyw.exe: found through the tree, even though another
    # pythonw window (Track) matches by name and comes first.
    {"id": "sel_tree_beats_name", "fn": "select", "tree": [100, 101],
     "spec": _spec(names=["pyw", "pythonw", "python"], hints=["Fastrack"]),
     "windows": [_win(11, 900, "pythonw", "Track"),
                 _win(12, 101, "pythonw", "FastRack v0.5.0")]},
    # ...and a frameless (self-fullscreen) tree window still beats a framed
    # name-only match; it is just flagged provisional.
    {"id": "sel_frameless_tree", "fn": "select", "tree": [100, 101],
     "spec": _spec(names=["pythonw"]),
     "windows": [_win(11, 900, "pythonw", "Track"),
                 _win(12, 101, "pythonw", "FastRack v0.5.0", framed=False)]},
    # Edge PWA: msedge_proxy hands off to the running browser, so only the
    # process name can match.
    {"id": "sel_name", "fn": "select", "tree": [700],
     "spec": _spec(names=["msedge"], hints=["2_Wiki", "msedge_proxy"]),
     "windows": [_win(21, 42, "obsidian", "Vault - Obsidian"),
                 _win(22, 43, "msedge", "Wiki")]},
    # Two python apps launched together: the other app's window is not
    # ours to take by name.
    {"id": "sel_foreign_blocked", "fn": "select", "tree": [100], "foreign": [100, 900, 901],
     "spec": _spec(names=["pythonw"]),
     "windows": [_win(11, 901, "pythonw", "Track")]},
    {"id": "sel_foreign_own_tree_ok", "fn": "select", "tree": [900, 901], "foreign": [100, 900, 901],
     "spec": _spec(names=[]),
     "windows": [_win(11, 901, "pythonw", "Track")]},
    # .bat: the flashing console is provisional, the GUI child is solid.
    {"id": "sel_gui_over_console", "fn": "select", "tree": [300, 301],
     "spec": _spec(hints=["run_track"]),
     "windows": [_win(31, 300, "cmd", r"C:\WINDOWS\system32\cmd.exe", cls="ConsoleWindowClass"),
                 _win(32, 301, "pythonw", "Track")]},
    {"id": "sel_console_only", "fn": "select", "tree": [300],
     "spec": _spec(hints=["run_track"]),
     "windows": [_win(31, 300, "cmd", r"C:\WINDOWS\system32\cmd.exe", cls="ConsoleWindowClass")]},
    # Console app hosted by Windows Terminal: not in the tree, wrong process
    # name - recognised by its tab title.
    {"id": "sel_terminal_title", "fn": "select", "tree": [400],
     "spec": _spec(names=["syncthing"], hints=["syncthing"]),
     "windows": [_win(41, 77, "windowsterminal", r"C:\tools\syncthing.exe",
                      cls="CASCADIA_HOSTING_WINDOW_CLASS")]},
    # ...but a browser tab that merely mentions the app is NOT its window.
    {"id": "sel_browser_tab_ignored", "fn": "select", "tree": [400],
     "spec": _spec(names=["syncthing"], hints=["syncthing"]),
     "windows": [_win(42, 78, "firefox", "workstation01 | Syncthing - Mozilla Firefox",
                      cls="MozillaWindowClass")]},
    # Main window over a small reminder dialog of the same process.
    {"id": "sel_larger_wins", "fn": "select", "tree": [9836],
     "spec": _spec(names=["thunderbird"]),
     "windows": [_win(51, 9836, "thunderbird", "1 reminder", cls="MozillaDialogClass", area=120_000),
                 _win(52, 9836, "thunderbird", "Inbox - Mozilla Thunderbird", area=3_600_000)]},
    # window_title_remap is authoritative when present.
    {"id": "sel_title_remap", "fn": "select", "tree": [100],
     "spec": _spec(names=["pythonw"], title="FastRack"),
     "windows": [_win(61, 100, "pythonw", "Track"),
                 _win(62, 555, "pythonw", "FastRack v0.5.0")]},
    {"id": "sel_title_remap_no_match", "fn": "select", "tree": [100],
     "spec": _spec(names=["pythonw"], title="FastRack"),
     "windows": [_win(61, 100, "pythonw", "Track")]},
    # Pre-existing windows (app was already running): name only, no hints.
    {"id": "sel_existing_no_hints", "fn": "select", "noHints": True,
     "spec": _spec(names=["musicbee"], hints=["syncthing"]),
     "windows": [_win(71, 77, "windowsterminal", "syncthing", cls="CASCADIA_HOSTING_WINDOW_CLASS"),
                 _win(72, 26780, "musicbee", "MusicBee")]},
    {"id": "sel_nothing", "fn": "select", "tree": [100],
     "spec": _spec(names=["obsidian"]), "windows": []},

    # --- Select-ExistingAppWindow (-Arrange) --------------------------
    # The real FastRack window is fullscreen (frameless) and bigger; the
    # "Track" pythonw window must not be taken for it.
    {"id": "ex_fullscreen_named", "fn": "existing",
     "spec": dict(_spec(names=["pyw", "pythonw", "python"], hints=["Fastrack", "pyw"]), Label="Fastrack"),
     "windows": [_win(11, 900, "pythonw", "Track", area=892_004),
                 _win(12, 901, "pythonw", "FastRack v0.5.0", framed=False, area=3_686_400)]},
    # "2_Wiki" matches the Edge app window titled "Wiki".
    {"id": "ex_digit_prefix", "fn": "existing",
     "spec": dict(_spec(names=["msedge"], hints=["2_Wiki", "msedge_proxy"]), Label="2_Wiki"),
     "windows": [_win(21, 40, "msedge", "Some page - Microsoft Edge", area=9_000_000),
                 _win(22, 41, "msedge", "Wiki", area=3_000_000)]},
    {"id": "ex_single_candidate", "fn": "existing",
     "spec": dict(_spec(names=["thunderbird"], hints=["mail"]), Label="mail"),
     "windows": [_win(31, 50, "thunderbird", "Inbox - Mozilla Thunderbird")]},
    {"id": "ex_ambiguous", "fn": "existing",
     "spec": dict(_spec(names=["pythonw"], hints=["Fastrack"]), Label="Fastrack"),
     "windows": [_win(41, 60, "pythonw", "Track"), _win(42, 61, "pythonw", "Tool")]},
    {"id": "ex_title_remap", "fn": "existing",
     "spec": dict(_spec(names=["pythonw"], title="FastRack"), Label="launcher"),
     "windows": [_win(51, 70, "pythonw", "Track"), _win(52, 71, "pythonw", "FastRack v0.5.0")]},

    # --- Get-AppSpec / Get-AppWaitMs ----------------------------------
    {"id": "spec_lnk_to_pyw", "fn": "spec", "remap": PROCESS_REMAP,
     "titles": {"1_floriandheer_pipeline_launcher": "Pipeline Manager"},
     "app": {"label": "Fastrack", "custom_path": "D:/x/Fastrack.lnk",
             "resolved_path": r"C:\Py\Launcher\pyw.EXE"}},
    {"id": "spec_title_remap", "fn": "spec", "remap": PROCESS_REMAP,
     "titles": {"fastrack": "FastRack"},
     "app": {"label": "Fastrack", "resolved_path": r"C:\Py\Launcher\pyw.EXE"}},
    {"id": "wait_floor", "fn": "wait", "floor": 30000, "app": {"window_timeout_ms": 5000}},
    {"id": "wait_app_raises", "fn": "wait", "floor": 30000, "app": {"window_timeout_ms": 90000}},
    {"id": "wait_missing_field", "fn": "wait", "floor": 30000, "app": {"label": "old entry"}},
]


@pytest.fixture(scope="module")
def ps(tmp_path_factory):
    """Run every case through Windows PowerShell 5.1 - the interpreter the
    scheduled task actually uses - and return {case id: result}."""
    exe = shutil.which("powershell.exe")
    if sys.platform != "win32" or not exe:
        pytest.skip("needs Windows PowerShell")
    if not LAUNCHER.is_file():
        pytest.skip("tools/startup/ launcher source not present in this checkout")
    work = tmp_path_factory.mktemp("startup_launcher")
    driver = work / "driver.ps1"
    driver.write_text(_DRIVER, encoding="utf-8-sig")
    cases = work / "cases.json"
    cases.write_text(json.dumps(CASES), encoding="utf-8")
    result = subprocess.run(
        [exe, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(driver),
         "-Launcher", str(LAUNCHER), "-CasesPath", str(cases)],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout.strip().splitlines()[-1])


def _as_list(value):
    """ConvertTo-Json collapses one-element arrays to a scalar and empty
    ones to null."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


# ============================================================
# Expected process names
# ============================================================

def test_plain_exe_is_looked_up_by_its_own_name(ps):
    assert _as_list(ps["names_plain"]) == ["thunderbird"]


def test_process_remap_replaces_the_stub_name(ps):
    assert _as_list(ps["names_remap"]) == ["msedge"]


def test_py_launcher_also_expects_python_children(ps):
    assert _as_list(ps["names_pyw"]) == ["pyw", "pythonw", "python"]


def test_batch_file_has_no_process_name_of_its_own(ps):
    assert _as_list(ps["names_bat"]) == []


# ============================================================
# Process tree
# ============================================================

def test_tree_follows_py_launcher_to_its_child(ps):
    assert _as_list(ps["tree_py_launcher"]) == [100, 101]


def test_tree_survives_an_exited_intermediate_process(ps):
    assert _as_list(ps["tree_dead_middle"]) == [300, 301, 302]


def test_tree_ignores_processes_that_predate_the_launch(ps):
    assert _as_list(ps["tree_recycled_pid"]) == [100]


def test_tree_is_empty_without_a_root_pid(ps):
    assert _as_list(ps["tree_no_root"]) == []


# ============================================================
# Window selection
# ============================================================

def test_window_of_launched_process_tree_beats_same_named_process(ps):
    assert ps["sel_tree_beats_name"] == {"hwnd": 12, "rule": "process tree", "provisional": False}


def test_frameless_tree_window_still_wins_but_is_provisional(ps):
    assert ps["sel_frameless_tree"] == {"hwnd": 12, "rule": "process tree", "provisional": True}


def test_handed_off_app_is_found_by_process_name(ps):
    assert ps["sel_name"] == {"hwnd": 22, "rule": "process name", "provisional": False}


def test_another_launched_apps_window_is_not_claimed_by_name(ps):
    assert ps["sel_foreign_blocked"] is None


def test_own_tree_window_is_claimed_even_when_listed_as_foreign(ps):
    assert ps["sel_foreign_own_tree_ok"]["hwnd"] == 11


def test_gui_window_wins_over_the_batch_files_console(ps):
    assert ps["sel_gui_over_console"] == {"hwnd": 32, "rule": "process tree", "provisional": False}


def test_lone_console_is_only_a_provisional_match(ps):
    assert ps["sel_console_only"] == {"hwnd": 31, "rule": "process tree", "provisional": True}


def test_terminal_hosted_console_app_is_found_by_tab_title(ps):
    assert ps["sel_terminal_title"] == {"hwnd": 41, "rule": "console title", "provisional": True}


def test_browser_tab_mentioning_the_app_is_not_its_window(ps):
    assert ps["sel_browser_tab_ignored"] is None


def test_larger_window_wins_between_equal_matches(ps):
    assert ps["sel_larger_wins"]["hwnd"] == 52


def test_arrange_finds_fullscreen_window_by_its_title(ps):
    assert ps["ex_fullscreen_named"] == {"hwnd": 12, "rule": "process name + title"}


def test_arrange_matches_label_without_its_number_prefix(ps):
    assert ps["ex_digit_prefix"]["hwnd"] == 22


def test_arrange_takes_a_lone_window_of_the_process(ps):
    assert ps["ex_single_candidate"] == {"hwnd": 31, "rule": "process name"}


def test_arrange_leaves_ambiguous_windows_alone(ps):
    assert ps["ex_ambiguous"] is None


def test_arrange_honours_title_remap(ps):
    assert ps["ex_title_remap"] == {"hwnd": 52, "rule": "title"}


def test_title_remap_is_authoritative(ps):
    assert ps["sel_title_remap"] == {"hwnd": 62, "rule": "title", "provisional": False}
    assert ps["sel_title_remap_no_match"] is None


def test_existing_window_fallback_uses_process_name_not_hints(ps):
    assert ps["sel_existing_no_hints"]["hwnd"] == 72


def test_no_windows_means_no_match(ps):
    assert ps["sel_nothing"] is None


# ============================================================
# Spec + wait bound
# ============================================================

def test_spec_ignores_title_remap_for_a_different_label(ps):
    spec = ps["spec_lnk_to_pyw"]
    assert spec["label"] == "Fastrack"
    assert spec["title"] == ""
    assert _as_list(spec["names"]) == ["pyw", "pythonw", "python"]
    assert _as_list(spec["hints"]) == ["Fastrack", "pyw"]


def test_spec_picks_up_title_remap_case_insensitively(ps):
    assert ps["spec_title_remap"]["title"] == "FastRack"


def test_window_wait_has_a_floor_the_app_can_only_raise(ps):
    assert ps["wait_floor"] == 30000
    assert ps["wait_app_raises"] == 90000
    assert ps["wait_missing_field"] == 30000


# ============================================================
# Static guards: nothing that makes noise or hops desktops
# ============================================================

def _code_lines(path: Path, comment: str) -> str:
    return "\n".join(
        line for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith(comment)
    )


@needs_launcher_source
def test_ahk_helper_never_activates_a_window():
    """WinActivate's failure path synthesizes Alt presses - the only
    simulated input the startup path ever produced."""
    code = _code_lines(SEND_F11, ";")
    assert "WinActivate" not in code
    assert re.search(r"^\s*Send\b", code, re.MULTILINE) is None
    assert "ControlSend" in code


@needs_launcher_source
def test_launcher_does_not_simulate_input_or_sleep_blindly():
    code = _code_lines(LAUNCHER, "#")
    assert "keybd_event" not in code and "SendInput" not in code
    for legacy in ("AppInitDelay", "BetweenDesktopsDelay", "FinalInitDelay"):
        assert legacy not in code


@needs_launcher_source
def test_fullscreen_key_is_sent_before_the_window_leaves_the_desktop():
    """Edge/Chrome ignore F11 once their window is in the background or on
    another virtual desktop, so the order inside the maximize/fullscreen
    branch is: activate, F11, verify, and only then move."""
    code = LAUNCHER.read_text(encoding="utf-8")
    branch = code[code.index("default {", code.index("function Set-AppWindow")):]
    activate = branch.index("[FastRackWin]::Activate(")
    send = branch.index("$ahkScript")
    verify = branch.index("IsFullscreenOn", send)
    move = branch.index("Move-AppToDesktop", send)
    assert activate < send < verify < move


@needs_launcher_source
def test_launcher_switches_desktop_exactly_once():
    code = _code_lines(LAUNCHER, "#")
    assert len(re.findall(r"\bSwitch-Desktop\b", code)) == 1


# ============================================================
# Config compatibility (modules/startup_apps_manager.py)
# ============================================================

LEGACY_CONFIG = {
    "enabled": True,
    "timing": {
        "app_init_delay_ms": 3000,
        "between_desktops_delay_ms": 3200,
        "final_init_delay_ms": 3500,
        "browser_timeout_ms": 2500,
    },
    "final_desktop": 2,
    "apps": [{"label": "Obsidian", "source": "custom_path",
              "custom_path": "C:/apps/Obsidian.exe",
              "resolved_path": "C:/apps/Obsidian.exe",
              "virtual_desktop": 2, "position": "maximize"}],
}


def test_legacy_config_loads_and_gains_the_wait_floor(tmp_path, monkeypatch):
    path = tmp_path / "startup_apps.json"
    path.write_text(json.dumps(LEGACY_CONFIG), encoding="utf-8")
    monkeypatch.setattr(sam, "config_path", lambda: path)

    cfg = sam.load_config()

    assert cfg["timing"]["min_window_wait_ms"] == 30000
    # Old keys are carried along untouched, not dropped or rejected.
    assert cfg["timing"]["between_desktops_delay_ms"] == 3200
    assert cfg["final_desktop"] == 2
    app = cfg["apps"][0]
    assert app["position"] == "maximize"
    assert app["window_timeout_ms"] == 5000   # filled from the template
    assert app["monitor"] == 1


def test_save_round_trips_a_legacy_config(tmp_path, monkeypatch):
    path = tmp_path / "startup_apps.json"
    path.write_text(json.dumps(LEGACY_CONFIG), encoding="utf-8")
    monkeypatch.setattr(sam, "config_path", lambda: path)

    cfg = sam.load_config()
    cfg["timing"]["min_window_wait_ms"] = 45000
    sam.save_config(cfg)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["timing"]["min_window_wait_ms"] == 45000
    assert saved["timing"]["final_init_delay_ms"] == 3500
    assert saved["apps"][0]["resolved_path"] == "C:/apps/Obsidian.exe"
