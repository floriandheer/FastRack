"""
Read-only status of the Environment Setup steps (setup_environment.py).

The Settings dialog uses this to mark which steps still need action. Nothing
here creates, changes or deletes anything. Each check mirrors what its step
would do, so a step reads "done" exactly when running it again would change
nothing. The folder plan and the `subst` / Synology lookups are shared with
setup_environment.py so the two cannot drift apart.
"""

import json
import ntpath
import os
import posixpath
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from shared_appdata import get_appdata_path
from shared_logging import get_logger

logger = get_logger(__name__)

# Same order and names as setup_environment.STEPS.
STEPS = ("folders", "drives", "synology", "config", "startup")

SYNOLOGY_DRIVE_URL = "https://www.synology.com/nl-nl/dsm/feature/drive"

DONE = "done"          # running the step again would change nothing
TODO = "todo"          # the step still has something to do
SKIPPED = "skipped"    # does not apply on this operating system
UNKNOWN = "unknown"    # the check itself failed


@dataclass(frozen=True)
class StepStatus:
    state: str
    detail: str = ""

    @property
    def needs_action(self) -> bool:
        return self.state == TODO


# ============================================================
# Pieces shared with setup_environment.py
# ============================================================

def looks_like_windows_path(path: str) -> bool:
    """True for a drive-letter ('D:\\...') or backslash-style path - the
    shape every path in setup_config.json.example currently uses. Meaningless
    (and not safely creatable) on macOS/Linux."""
    return (len(path) >= 2 and path[1] == ':') or '\\' in path


@dataclass
class FolderPlan:
    dirs: List[str]                      # every directory the Folders step wants to exist
    bases: List[str]
    skipped_extra: int = 0               # Windows-only extra_dirs left out off Windows
    used_platform_defaults: bool = False  # bases came from RackSettings, not setup_config.json
    error: str = ""                      # set when the platform defaults could not be resolved


def plan_folders(cfg: dict, settings=None) -> FolderPlan:
    """The directories the Folders step creates, from setup_config.json's
    folder_structure. Off Windows its bases (D:\\_work\\...) cannot exist, so
    the same structure is planned under rack_settings' own bases instead (e.g.
    ~/_work/Active). `settings` is a RackSettings to read those from; None
    builds one, which creates rack_config.json if it is missing."""
    fs = cfg["folder_structure"]
    categories = fs.get("categories", [])
    subcategories = fs.get("subcategories", {})
    extra_dirs = fs.get("extra_dirs", [])
    used_platform_defaults = False

    if sys.platform == "win32":
        bases = fs.get("bases", [])
    else:
        try:
            if settings is None:
                from rack_settings import RackSettings
                settings = RackSettings()
            bases = [settings.get_active_base(), settings.get_archive_base()]
            used_platform_defaults = True
        except Exception as e:
            return FolderPlan(dirs=[], bases=[], error=str(e))

    dirs = []
    for base in bases:
        dirs.append(base)
        for cat in categories:
            cat_path = os.path.join(base, cat)
            dirs.append(cat_path)
            for sub in subcategories.get(cat, []):
                dirs.append(os.path.join(cat_path, sub))

    skipped_extra = 0
    for d in extra_dirs:
        if sys.platform != "win32" and looks_like_windows_path(d):
            skipped_extra += 1
            continue
        dirs.append(d)

    return FolderPlan(dirs=dirs, bases=list(bases), skipped_extra=skipped_extra,
                      used_platform_defaults=used_platform_defaults)


def parse_subst_output() -> dict:
    """
    Parse `subst` output and return {drive_letter: target_path}.

    Output format:  I:\\: => D:\\_work\\Active
    """
    mapping = {}
    try:
        result = subprocess.run(
            ["subst"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            # e.g. "I:\: => D:\_work\Active"
            parts = line.split(" => ", 1)
            if len(parts) == 2:
                drive = parts[0].rstrip("\\:")  # "I:\:" -> "I"
                drive = drive.strip()
                if len(drive) == 1:
                    drive = f"{drive.upper()}:"
                target = parts[1].strip()
                mapping[drive] = target
    except Exception as e:
        logger.warning(f"Could not parse subst output: {e}")
    return mapping


def find_synology_drive():
    """(path to SynologyDrive.exe or None, whether Synology Drive Client
    looks installed). Windows install locations."""
    install_paths = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"),
                     "Synology", "SynologyDrive"),
        os.path.join(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     "Synology", "SynologyDrive"),
        os.path.join(os.path.expanduser("~"), "AppData", "Local",
                     "SynologyDrive"),
    ]
    drive_exe = None
    for p in install_paths:
        candidate = os.path.join(p, "SynologyDrive.exe")
        if os.path.isfile(candidate):
            drive_exe = candidate
            break
    installed = drive_exe is not None or any(os.path.isdir(p) for p in install_paths)
    return drive_exe, installed


# Where Synology's installer puts the Mac client.
MAC_SYNOLOGY_APPS = (
    "/Applications/Synology Drive Client.app",
    os.path.join(os.path.expanduser("~"), "Applications", "Synology Drive Client.app"),
)


def synology_client_installed() -> bool:
    """Whether Synology Drive Client looks installed on this Windows PC or Mac.
    Other systems are not checked, so they count as installed (nothing to nag about)."""
    if sys.platform == "win32":
        return find_synology_drive()[1]
    if sys.platform == "darwin":
        return any(os.path.isdir(p) for p in MAC_SYNOLOGY_APPS)
    return True


# ============================================================
# Per-step checks
# ============================================================

def _short(path: str) -> str:
    """Abbreviate the home folder as ~ for display."""
    home = str(Path.home())
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


def _same_path(a, b) -> bool:
    """Compare two configured paths the way RackSettings stores them: case-
    and separator-insensitive on Windows, and 'I' equals 'I:'."""
    if not isinstance(a, str) or not isinstance(b, str):
        return False

    flavour = ntpath if sys.platform == "win32" else posixpath

    def norm(value: str) -> str:
        value = value.strip()
        if len(value.rstrip(":")) == 1 and value.rstrip(":").isalpha():
            return value.rstrip(":").upper() + ":"
        return flavour.normcase(flavour.normpath(value))
    return norm(a) == norm(b)


def check_folders(cfg: dict, settings=None) -> StepStatus:
    plan = plan_folders(cfg, settings)
    if plan.error:
        return StepStatus(UNKNOWN, f"could not resolve the folders: {plan.error}")
    missing = [d for d in plan.dirs if not os.path.isdir(d)]
    if not missing:
        return StepStatus(DONE, f"all {len(plan.dirs)} folders exist")
    return StepStatus(
        TODO, f"{len(missing)} of {len(plan.dirs)} folders missing "
              f"(starting with {_short(missing[0])})")


def check_drives(cfg: dict) -> StepStatus:
    if sys.platform != "win32":
        return StepStatus(SKIPPED, "Windows only - there are no drive letters on this system")
    mappings = cfg.get("drive_mappings", [])
    if not mappings:
        return StepStatus(DONE, "no drive mappings configured")
    current = parse_subst_output()
    not_mapped = []
    for m in mappings:
        drive = m["drive_letter"].upper()
        mapped_to = current.get(drive)
        if mapped_to is None or not _same_path(mapped_to, m["target_path"]):
            not_mapped.append(drive)
    if not_mapped:
        return StepStatus(TODO, "not mapped (or mapped elsewhere): " + ", ".join(not_mapped))
    return StepStatus(DONE, f"{len(mappings)} drive mapping(s) active")


def check_synology(cfg: dict) -> StepStatus:
    if sys.platform != "win32":
        return StepStatus(SKIPPED, "not checked on this system (Windows only for now)")
    _exe, installed = find_synology_drive()
    if not installed:
        return StepStatus(TODO, "Synology Drive Client is not installed")
    missing = []
    for sf in (cfg.get("synology_drive") or {}).get("sync_folders", []):
        local = sf.get("local_path") or sf.get("expected_path", "")
        try:
            populated = os.path.isdir(local) and len(os.listdir(local)) > 0
        except OSError:
            populated = False
        if not populated:
            missing.append(sf.get("name") or local)
    if missing:
        return StepStatus(TODO, "sync folder missing or empty: " + ", ".join(missing))
    return StepStatus(DONE, "installed, sync folders populated")


def check_config(cfg: dict, config_path: Optional[Path] = None) -> StepStatus:
    path = Path(config_path) if config_path else get_appdata_path() / "rack_config.json"
    if not path.is_file():
        return StepStatus(TODO, "rack_config.json has not been created yet")
    try:
        with open(path, "r", encoding="utf-8") as f:
            current = json.load(f)
    except (OSError, ValueError) as e:
        return StepStatus(TODO, f"rack_config.json cannot be read ({e})")
    if sys.platform != "win32":
        # The step skips setup_config.json's Windows paths off Windows, so
        # having the file is all there is to it.
        return StepStatus(DONE, "rack_config.json exists")
    pc = cfg.get("pipeline_config") or {}
    wanted = {
        "work_drive": ("drives", "work"),
        "active_base": ("drives", "active_base"),
        "archive_base": ("drives", "archive_base"),
        "mapped_software_path": ("software_sync", "mapped_software_path"),
        "launchers_base_path": ("software_sync", "launchers_base_path"),
    }
    differs = [key for key, (section, name) in wanted.items()
               if key in pc and not _same_path(pc[key], (current.get(section) or {}).get(name))]
    if differs:
        return StepStatus(TODO, "differs from setup_config.json: " + ", ".join(differs))
    return StepStatus(DONE, "matches setup_config.json")


def check_startup() -> StepStatus:
    if sys.platform != "win32":
        return StepStatus(SKIPPED, "Windows only")
    import startup_apps_manager as sam
    if not sam.deployed_launcher_path().is_file():
        return StepStatus(TODO, "startup launcher is not deployed")
    if not sam.is_task_installed():
        return StepStatus(TODO, "logon scheduled task is not registered")
    return StepStatus(DONE, "launcher deployed, scheduled task registered")


# ============================================================
# All steps
# ============================================================

def _load_setup_config(project_root) -> tuple:
    """(cfg, problem). `problem` is a StepStatus for steps that need
    setup_config.json when it is missing or unreadable, else None."""
    path = Path(project_root) / "setup_config.json"
    if not path.is_file():
        return {}, StepStatus(TODO, "setup_config.json does not exist yet "
                                    "(running a step offers to create it)")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except (OSError, ValueError) as e:
        return {}, StepStatus(UNKNOWN, f"setup_config.json cannot be read ({e})")


def check_environment(project_root, settings=None) -> Dict[str, StepStatus]:
    """Status of every Environment Setup step, keyed by step name (STEPS
    order). Never raises: a check that fails reports UNKNOWN. May take a
    moment on Windows (subst, scheduled-task query), so call it off the UI
    thread. `settings` is the RackSettings whose folders the Folders step
    uses off Windows."""
    cfg, cfg_problem = _load_setup_config(project_root)
    windows = sys.platform == "win32"

    def run(step):
        if step == "folders":
            return cfg_problem or check_folders(cfg, settings)
        if step == "drives":
            return cfg_problem if (cfg_problem and windows) else check_drives(cfg)
        if step == "synology":
            return cfg_problem if (cfg_problem and windows) else check_synology(cfg)
        if step == "config":
            return check_config(cfg)
        return check_startup()

    statuses = {}
    for step in STEPS:
        try:
            statuses[step] = run(step)
        except Exception as e:  # noqa: BLE001 - a broken check must not break the dialog
            logger.warning("Environment check '%s' failed: %s", step, e)
            statuses[step] = StepStatus(UNKNOWN, f"could not check: {e}")
    return statuses


def steps_needing_action(statuses: Dict[str, StepStatus]) -> List[str]:
    return [step for step in STEPS if statuses.get(step) and statuses[step].needs_action]
