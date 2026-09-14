"""
startup_apps_manager — owner of the startup-apps sidecar config + launcher.

Three responsibilities:

  1. Read/write ``startup_apps.json`` (sidecar to ``rack_config.json`` in
     %LOCALAPPDATA%\\PipelineManager\\). Schema is documented in
     DEFAULT_CONFIG and APP_ENTRY_TEMPLATE below.
  2. Resolve workstation-app names + .lnk shortcuts to launchable .exe
     paths so the PowerShell launcher just consumes ``resolved_path``.
  3. Deploy the launcher script under %LOCALAPPDATA% and register a
     logon-triggered Task Scheduler entry that runs it invisibly.

This module is platform-agnostic where it can be (file I/O, config
normalisation, workstation-app resolution); Windows-only paths are
guarded with ``sys.platform`` checks so the dialog still loads on
WSL/Linux for testing.
"""

from __future__ import annotations

import copy
import ctypes
import json
import os
import shutil
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path
from typing import Optional

from shared_logging import get_logger
from shared_appdata import get_appdata_path

logger = get_logger(__name__)


# ============================================================
# Paths
# ============================================================

_PROJECT_ROOT  = Path(__file__).resolve().parent.parent
_LAUNCHER_SRC  = _PROJECT_ROOT / "tools" / "startup" / "StartupLauncher.ps1"
_AHK_SRC       = _PROJECT_ROOT / "tools" / "startup" / "SendF11.ahk"
_TASK_HELPER_SRC = _PROJECT_ROOT / "tools" / "startup" / "install_task.ps1"

TASK_NAME = "FastRack_StartupLauncher"


def config_path() -> Path:
    return get_appdata_path() / "startup_apps.json"


def deployed_launcher_path() -> Path:
    return get_appdata_path() / "StartupLauncher.ps1"


def deployed_ahk_path() -> Path:
    return get_appdata_path() / "SendF11.ahk"


def deployed_task_helper_path() -> Path:
    return get_appdata_path() / "install_task.ps1"


# ============================================================
# Schema
# ============================================================

DEFAULT_CONFIG: dict = {
    "enabled": False,
    "timing": {
        "app_init_delay_ms": 3000,
        "process_window_timeout_ms": 1500,
        "between_desktops_delay_ms": 1500,
        "move_to_desktop_delay_ms": 60,
        "move_to_monitor_delay_ms": 100,
        "maximize_delay_ms": 12,
        "after_maximize_delay_ms": 100,
        # 15s mirrors the legacy 1_StartupScript_AppsToDesktop.ps1 value.
        # 5s was too short: pythonw apps (FastRack) hadn't shown their
        # main window before the launcher switched back to desktop 1,
        # which placed them on the wrong desktop.
        "final_init_delay_ms": 15000,
    },
    # Process-name remap for apps launched via a stub launcher whose
    # window lives on a different process name. These mirror the
    # hardcoded table from the original 1_StartupScript_AppsToDesktop.ps1
    # so behavior is preserved when imported existing shortcuts run
    # through the new pipeline.
    "process_remap": {
        "msedge_proxy.exe": "msedge",
        "launcher.exe": "SynologyDrive",
        "musicbee.exe": "MusicBee",
        "chrome.exe": "chrome",
        "pythonw.exe": "pythonw",
    },
    # Window-title remap by shortcut basename / app label, for apps
    # whose main window appears under a name unrelated to the process.
    "window_title_remap": {
        "1_floriandheer_pipeline_launcher": "FastRack",
    },
    # Empty = launcher uses its built-in default
    # (C:\Program Files\AutoHotkey\v2\AutoHotkey.exe).
    "ahk_exe": "",
    # Subst drive mappings the launcher applies directly at logon —
    # this is the authoritative way drives get set up under the
    # launcher's control. HKCU\Run subst entries also exist (set up by
    # setup_environment.py's Drives step) as redundancy, but they
    # routinely lose the race against Defender / OneDrive / indexer on
    # cold boot. Auto-populated by the Settings dialog from the
    # Drives tab. Schema: [{"letter": "M:", "target": "D:\\music"}, ...]
    "drive_mappings": [],
    # Drive letters the launcher should *verify* are accessible before
    # launching apps. With drive_mappings handling the subst, this
    # becomes a near-instant sanity check rather than a 30s gamble.
    # Same source of truth as drive_mappings (Drives tab) but stored
    # as a flat letter list for the PS1's simple poll loop.
    "wait_for_drives": [],
    "drive_wait_timeout_ms": 30000,
    # Which virtual desktop the launcher switches back to after placing
    # all apps (FinalInitDelay after the last one, then this). Used to be
    # hardcoded to desktop 1.
    "final_desktop": 1,
    "apps": [],
}

APP_ENTRY_TEMPLATE: dict = {
    "label": "",
    "source": "workstation_app",   # "workstation_app" | "custom_path"
    "workstation_name": "",
    "custom_path": "",
    "resolved_path": "",
    "args": "",
    "monitor": 1,
    "virtual_desktop": 1,
    "position": "maximize",        # "maximize" | "fullscreen" | "free"
    "x_offset": 0,
    "y_offset": 0,
    "launch_order": 0,
    "enabled": True,
    # How long the launcher waits for this app's window to appear before
    # giving up on placing it (per-app since apps vary wildly — a browser
    # shows a window almost instantly, pythonw + Tkinter can take longer).
    "window_timeout_ms": 5000,
}


def new_app_entry(**overrides) -> dict:
    """Return a fresh app entry dict pre-filled with defaults."""
    entry = copy.deepcopy(APP_ENTRY_TEMPLATE)
    entry.update(overrides)
    return entry


# ============================================================
# Config load / save
# ============================================================

def _merge_defaults(loaded: dict) -> dict:
    """Shallow-merge loaded config over DEFAULT_CONFIG so users keep
    working when we add new top-level keys."""
    out = copy.deepcopy(DEFAULT_CONFIG)
    for key, val in loaded.items():
        if key in ("timing", "process_remap", "window_title_remap") and isinstance(val, dict):
            out[key].update(val)
        elif key == "apps" and isinstance(val, list):
            out["apps"] = [_normalize_app_entry(a) for a in val if isinstance(a, dict)]
        else:
            out[key] = val
    return out


def _normalize_app_entry(entry: dict) -> dict:
    """Fill in missing fields from APP_ENTRY_TEMPLATE so the launcher
    always sees a complete record."""
    out = copy.deepcopy(APP_ENTRY_TEMPLATE)
    out.update(entry)
    return out


def load_config() -> dict:
    """Read the sidecar JSON, returning defaults if it's missing or
    unreadable. Never raises."""
    path = config_path()
    if not path.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with path.open("r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s — using defaults", path, exc)
        return copy.deepcopy(DEFAULT_CONFIG)
    if not isinstance(loaded, dict):
        return copy.deepcopy(DEFAULT_CONFIG)
    return _merge_defaults(loaded)


def save_config(cfg: dict) -> None:
    """Write the sidecar JSON atomically (write to .tmp then replace).
    Re-resolves workstation_app paths before writing so the launcher's
    cached ``resolved_path`` always reflects the current install."""
    refresh_resolved_paths(cfg)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
        f.write("\n")
    tmp.replace(path)
    logger.info("startup_apps.json saved (%d apps, enabled=%s)",
                len(cfg.get("apps", [])), cfg.get("enabled"))


# ============================================================
# Workstation-app resolution
# ============================================================

def list_workstation_choices() -> list[dict]:
    """Return one dict per catalog app:
    ``{"name": str, "resolved_path": str|None, "installed": bool}``.

    ``installed`` follows ``workstation_apps.is_installed`` (the same
    truth the Workstation Apps tab shows). ``resolved_path`` is what the
    launcher would actually invoke — can be None even when ``installed``
    is True (e.g. multi-app suites like Affinity, versioned installs
    like DaVinci Resolve). The picker uses both to render a 3-state
    status icon.
    """
    try:
        import workstation_apps as wa
    except ImportError as exc:
        logger.warning("workstation_apps unavailable: %s", exc)
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for app in wa.load_apps():
        if app.name in seen:
            continue
        seen.add(app.name)
        out.append({
            "name": app.name,
            "resolved_path": wa.resolve_exe_path(app),
            "installed": wa.is_installed(app),
        })
    return out


def resolve_workstation_app(name: str) -> Optional[str]:
    """Return the absolute exe path for a workstation-app catalog name,
    or None if the app isn't installed / can't be resolved."""
    if not name:
        return None
    try:
        import workstation_apps as wa
    except ImportError:
        return None
    for app in wa.load_apps():
        if app.name == name:
            return wa.resolve_exe_path(app)
    return None


def resolve_shortcut(lnk_path: str) -> tuple[str, str]:
    """Resolve a .lnk shortcut to (target_path, arguments).

    Uses pywin32's WScript.Shell if available; if not, returns the
    .lnk path itself with empty arguments (the launcher's Start-Process
    can still open .lnk files — only window-by-process-name lookup
    degrades in that case).
    """
    if not lnk_path or not os.path.isfile(lnk_path):
        return lnk_path, ""
    if not lnk_path.lower().endswith(".lnk"):
        return lnk_path, ""
    try:
        from win32com.client import Dispatch  # pywin32
        shell = Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(lnk_path)
        target = (sc.TargetPath or "").strip()
        args   = (sc.Arguments or "").strip()
        if target:
            return target, args
    except ImportError:
        logger.debug("pywin32 not available; storing .lnk path as-is")
    except Exception as exc:
        logger.warning("Failed to resolve shortcut %s: %s", lnk_path, exc)
    return lnk_path, ""


def refresh_resolved_paths(cfg: dict) -> None:
    """Re-resolve every workstation_app entry's ``resolved_path`` so the
    launcher sees current install locations. Custom-path entries are
    left alone (the user owns those)."""
    for app in cfg.get("apps", []):
        if app.get("source") == "workstation_app":
            name = app.get("workstation_name") or app.get("label", "")
            resolved = resolve_workstation_app(name)
            app["resolved_path"] = resolved or ""
        elif app.get("source") == "custom_path":
            path = app.get("custom_path", "")
            # If the user picked a .lnk, follow it once so the launcher
            # has a real exe to point window-finding at. Re-resolve every
            # save so a relocated shortcut catches up.
            if path and path.lower().endswith(".lnk"):
                target, args = resolve_shortcut(path)
                app["resolved_path"] = target
                if not app.get("args"):
                    app["args"] = args
            else:
                app["resolved_path"] = path


# ============================================================
# Monitor enumeration (ctypes — no extra deps)
# ============================================================

def detect_monitors() -> list[dict]:
    """Return ``[{"index", "x", "y", "width", "height", "primary"}]``
    sorted left-to-right, matching the order the PowerShell launcher
    uses ([Screen]::AllScreens | Sort-Object Bounds.X)."""
    if sys.platform != "win32":
        return []

    class _RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

    class _MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD),
                    ("rcMonitor", _RECT),
                    ("rcWork", _RECT),
                    ("dwFlags", wintypes.DWORD)]

    MONITORENUMPROC = ctypes.WINFUNCTYPE(
        ctypes.c_int,
        wintypes.HMONITOR, wintypes.HDC,
        ctypes.POINTER(_RECT), wintypes.LPARAM,
    )
    user32 = ctypes.windll.user32
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(_MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL

    monitors: list[dict] = []

    def _cb(hMonitor, _hdc, _lprc, _lparam):
        mi = _MONITORINFO()
        mi.cbSize = ctypes.sizeof(_MONITORINFO)
        if user32.GetMonitorInfoW(hMonitor, ctypes.byref(mi)):
            r = mi.rcMonitor
            monitors.append({
                "x": r.left, "y": r.top,
                "width": r.right - r.left, "height": r.bottom - r.top,
                "primary": bool(mi.dwFlags & 0x1),  # MONITORINFOF_PRIMARY
            })
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    except Exception as exc:
        logger.warning("EnumDisplayMonitors failed: %s", exc)
        return []

    monitors.sort(key=lambda m: m["x"])
    for i, m in enumerate(monitors, start=1):
        m["index"] = i
    return monitors


# ============================================================
# Deployment + scheduled task
# ============================================================

def _copy_for_windows(src: Path, dst: Path) -> None:
    """Copy a text file into AppData as UTF-8-with-BOM + CRLF.

    Why: Windows PowerShell 5.1 (the default on Win10/11) treats files
    without a BOM as the system codepage (Windows-1252 in most locales).
    Our launcher has Unicode in comments (em-dashes, etc.) — without a
    BOM, those bytes get re-decoded as cp1252 garbage that derails the
    parser further down (typically inside the C# Add-Type here-string,
    where the `@"` opener stops being recognised). LF-only line endings
    compound the issue. Repo files stay LF/no-BOM so git diffs cleanly;
    the deploy step delivers the Windows-friendly bytes the runtime
    actually needs."""
    text = src.read_bytes().decode("utf-8-sig")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("wb") as f:
        f.write(b"\xef\xbb\xbf")  # UTF-8 BOM
        f.write(text.replace("\n", "\r\n").encode("utf-8"))


def deploy_launcher_script() -> tuple[bool, str]:
    """Copy StartupLauncher.ps1, SendF11.ahk, and install_task.ps1 from
    tools/startup/ into %LOCALAPPDATA%\\PipelineManager\\, normalising
    line endings + encoding for Windows PowerShell. Idempotent;
    overwrites every time so a `git pull` ships launcher updates."""
    if not _LAUNCHER_SRC.is_file():
        return False, f"launcher source missing: {_LAUNCHER_SRC}"
    target = deployed_launcher_path()
    try:
        _copy_for_windows(_LAUNCHER_SRC, target)
        if _AHK_SRC.is_file():
            _copy_for_windows(_AHK_SRC, deployed_ahk_path())
        if _TASK_HELPER_SRC.is_file():
            _copy_for_windows(_TASK_HELPER_SRC, deployed_task_helper_path())
    except (OSError, UnicodeDecodeError) as exc:
        return False, f"copy failed: {exc}"
    logger.info("Deployed launcher to %s", target)
    return True, str(target)


def _run_task_helper(action: str, *,
                     script_path: Optional[Path] = None,
                     timeout: int = 30) -> tuple[int, str]:
    """Invoke install_task.ps1 with the given action. Returns
    (exit_code, last_output_line). Always re-deploys the helper first
    so PS-side fixes ship without a separate setup run."""
    if sys.platform != "win32":
        return 2, "Windows-only"
    helper = deployed_task_helper_path()
    if _TASK_HELPER_SRC.is_file():
        try:
            helper.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_TASK_HELPER_SRC, helper)
        except OSError as exc:
            return 2, f"copy failed: {exc}"
    if not helper.is_file():
        return 2, f"helper missing: {helper}"

    cmd = [
        "powershell.exe", "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(helper),
        "-Action", action,
        "-TaskName", TASK_NAME,
    ]
    if script_path is not None:
        cmd += ["-ScriptPath", str(script_path)]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, f"powershell invocation failed: {exc}"
    last_line = (result.stdout or result.stderr or "").strip().splitlines()
    msg = last_line[-1] if last_line else ""
    return result.returncode, msg


def is_task_installed() -> bool:
    """True if the FastRack scheduled task is registered."""
    code, _ = _run_task_helper("query", timeout=10)
    return code == 0


def install_scheduled_task() -> tuple[bool, str]:
    """Register the launcher as a logon-triggered Task Scheduler entry
    via Register-ScheduledTask. Current-user / Limited run level — no
    admin prompt needed. Idempotent: -Force overwrites prior versions."""
    if sys.platform != "win32":
        return False, "Task Scheduler integration is Windows-only."
    script = deployed_launcher_path()
    if not script.is_file():
        ok, detail = deploy_launcher_script()
        if not ok:
            return False, f"could not deploy launcher: {detail}"
    code, msg = _run_task_helper("install", script_path=script)
    if code == 0:
        logger.info("Registered scheduled task %s", TASK_NAME)
        return True, "Scheduled task registered."
    return False, msg or f"helper exited with code {code}"


def uninstall_scheduled_task() -> tuple[bool, str]:
    """Remove the FastRack scheduled task. Idempotent."""
    if sys.platform != "win32":
        return False, "Task Scheduler integration is Windows-only."
    code, msg = _run_task_helper("uninstall", timeout=15)
    if code == 0:
        logger.info("Removed scheduled task %s (%s)", TASK_NAME, msg)
        return True, "Scheduled task removed." if msg == "removed" else "No task to remove."
    return False, msg or f"helper exited with code {code}"


def run_launcher_now() -> tuple[bool, str]:
    """Invoke the deployed launcher manually (the "Test now" button).
    Runs in a visible PowerShell window so the user can watch output —
    the silent scheduled-task path is separate."""
    if sys.platform != "win32":
        return False, "Launcher is Windows-only."
    script = deployed_launcher_path()
    if not script.is_file():
        ok, detail = deploy_launcher_script()
        if not ok:
            return False, f"could not deploy launcher: {detail}"
    try:
        subprocess.Popen(
            [
                "powershell.exe", "-NoExit",
                "-ExecutionPolicy", "Bypass",
                "-NoProfile", "-File", str(script),
            ],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"failed to launch: {exc}"
    return True, f"Launched {script.name} in a new console."


# ============================================================
# Dependency checks
# ============================================================
#
# Three things the launcher leans on, in descending criticality:
#
#   - VirtualDesktop PS module (MScholtes) — required for desktop
#     assignment. Without it, apps still launch + get monitor placement
#     but stay on the current desktop. Pinned to Windows build numbers,
#     so this is the one most likely to break after Windows updates.
#   - AutoHotkey v2 — required only for "fullscreen" position mode (we
#     send F11 via a tiny AHK script). Without it, fullscreen apps stop
#     at maximize.
#   - pywin32 — optional, used to resolve .lnk targets so window-finding
#     by process name works. Without it, custom_path entries that point
#     at .lnk files store the .lnk path verbatim; Start-Process still
#     opens them, but per-process window lookup may miss.

_AHK_DEFAULT_PATH = r"C:\Program Files\AutoHotkey\v2\AutoHotkey.exe"


def _check_virtualdesktop_module() -> dict:
    base = {"name": "VirtualDesktop PS module", "required": True,
            "install_label": "Install via PSGallery",
            "install_action": "virtualdesktop"}
    if sys.platform != "win32":
        return {**base, "ok": True, "detail": "skipped (non-Windows)",
                "install_label": None, "install_action": None}
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command",
             "$m = Get-Module -ListAvailable -Name VirtualDesktop "
             "| Select-Object -First 1; "
             "if ($m) { $m.Version.ToString() } else { '' }"],
            capture_output=True, text=True, timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        ver = (result.stdout or "").strip()
        if ver:
            return {**base, "ok": True, "detail": f"installed (v{ver})",
                    "install_label": "Reinstall"}
        return {**base, "ok": False,
                "detail": "missing — desktop assignment will be skipped"}
    except (OSError, subprocess.SubprocessError) as exc:
        return {**base, "ok": False, "detail": f"check failed: {exc}"}


def _check_autohotkey() -> dict:
    base = {"name": "AutoHotkey v2", "required": False,
            "install_label": "Install via winget",
            "install_action": "autohotkey"}
    if sys.platform != "win32":
        return {**base, "ok": True, "detail": "skipped (non-Windows)",
                "install_label": None, "install_action": None}
    if os.path.isfile(_AHK_DEFAULT_PATH):
        return {**base, "ok": True, "detail": _AHK_DEFAULT_PATH,
                "install_label": None, "install_action": None}
    which = shutil.which("AutoHotkey.exe")
    if which:
        return {**base, "ok": True, "detail": which,
                "install_label": None, "install_action": None}
    return {**base, "ok": False,
            "detail": "missing — required only for 'fullscreen' position mode"}


def _check_pywin32() -> dict:
    base = {"name": "pywin32 (Python)", "required": False,
            "install_label": None, "install_action": None}
    try:
        import importlib.util
        spec = importlib.util.find_spec("win32com")
        if spec is not None:
            return {**base, "ok": True, "detail": "importable"}
        return {**base, "ok": False,
                "detail": "missing — .lnk targets won't be resolved (pip install pywin32)"}
    except Exception as exc:
        return {**base, "ok": False, "detail": f"check failed: {exc}"}


def check_dependencies() -> list[dict]:
    """Return one dict per dependency with keys: ``name``, ``ok``,
    ``detail``, ``required`` (whether a missing one blocks anything
    important), ``install_label`` (button text, or None), and
    ``install_action`` (key to pass to :func:`run_install_action`).

    Safe to call on any platform — non-Windows checks return ``ok=True``
    with detail ``"skipped (non-Windows)"`` so dev/test on WSL doesn't
    surface false negatives.
    """
    return [
        _check_virtualdesktop_module(),
        _check_autohotkey(),
        _check_pywin32(),
    ]


def install_virtualdesktop_module() -> tuple[bool, str]:
    """Install the VirtualDesktop module from PSGallery into the current
    user's PS modules path. Spawns a visible console so PSGallery's
    trust prompt + UAC (rare for current-user scope) are visible."""
    if sys.platform != "win32":
        return False, "Windows-only."
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoExit", "-NoProfile", "-Command",
             "Install-Module -Name VirtualDesktop -Scope CurrentUser "
             "-Force -AllowClobber; "
             "Write-Host ''; Write-Host 'Done. Close this window and "
             "click Recheck in the FastRack settings dialog.'"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return True, "Installer launched in a new console."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"failed to launch: {exc}"


def install_autohotkey() -> tuple[bool, str]:
    """Install AutoHotkey v2 via winget in a new console. Falls back to
    pointing at the download page when winget isn't available."""
    if sys.platform != "win32":
        return False, "Windows-only."
    if not shutil.which("winget"):
        return False, ("winget not available — install AutoHotkey v2 "
                       "from https://www.autohotkey.com/")
    try:
        subprocess.Popen(
            ["cmd", "/k", "winget", "install", "AutoHotkey.AutoHotkey",
             "--accept-source-agreements", "--accept-package-agreements"],
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        return True, "winget launched in a new console."
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"failed to launch: {exc}"


def run_install_action(action: str) -> tuple[bool, str]:
    """Dispatch a dep's ``install_action`` key to the right installer.
    Keeps the UI loosely coupled (it doesn't import the installers
    directly)."""
    handlers = {
        "virtualdesktop": install_virtualdesktop_module,
        "autohotkey": install_autohotkey,
    }
    fn = handlers.get(action)
    if fn is None:
        return False, f"unknown install action: {action}"
    return fn()


# ============================================================
# Import existing Startup folder
# ============================================================

def _legacy_startup_root() -> Path:
    """The custom folder the original P:\\_Script\\startup\\1_*.ps1 reads
    from (C:\\Users\\<me>\\Startup), NOT the canonical Windows Startup
    folder under AppData. Returns even if it doesn't exist — callers
    should check ``.is_dir()`` first."""
    return Path.home() / "Startup"


def find_importable_shortcuts() -> list[dict]:
    """Discover .lnk/.exe entries under <user>\\Startup\\DesktopN\\ and
    return a list of partially-populated app entries ready to splice
    into config. Apps not yet imported (caller decides) — this is
    pure discovery."""
    root = _legacy_startup_root()
    if not root.is_dir():
        return []
    out: list[dict] = []
    order_counter: dict[int, int] = {}
    for sub in sorted(root.iterdir()):
        if not sub.is_dir():
            continue
        name = sub.name
        if not name.lower().startswith("desktop"):
            continue
        try:
            vd = int(name[len("desktop"):])
        except ValueError:
            continue
        order_counter.setdefault(vd, 0)
        for item in sorted(sub.iterdir()):
            if not item.is_file():
                continue
            ext = item.suffix.lower()
            if ext not in (".lnk", ".exe", ".bat", ".url"):
                continue
            target, args = resolve_shortcut(str(item)) if ext == ".lnk" else (str(item), "")
            entry = new_app_entry(
                label=item.stem,
                source="custom_path",
                custom_path=str(item),
                resolved_path=target,
                args=args,
                monitor=1,
                virtual_desktop=vd,
                position="maximize",
                launch_order=order_counter[vd],
                enabled=True,
            )
            out.append(entry)
            order_counter[vd] += 1
    return out


def import_existing_shortcuts(cfg: dict, replace: bool = False) -> int:
    """Import discovered legacy shortcuts into ``cfg["apps"]`` and return
    the count added. With ``replace=True`` existing entries are cleared
    first; otherwise we append (the caller's UI should warn about
    duplicates)."""
    discovered = find_importable_shortcuts()
    if replace:
        cfg["apps"] = discovered
        return len(discovered)
    cfg.setdefault("apps", []).extend(discovered)
    return len(discovered)
