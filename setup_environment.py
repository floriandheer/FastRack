#!/usr/bin/env python3
"""
Fastrack - Environment Setup Script
-----------------------------------
Automates pipeline environment provisioning: folder creation, subst drive
mappings with registry persistence, Synology Drive status checks, and
pipeline config generation.

Usage:
    python setup_environment.py [--config PATH] [--dry-run] [--yes] [--step STEP]
"""

import sys
import os
import json
import argparse
import subprocess
from pathlib import Path

# Add modules/ to path (same pattern as fastrack_hub.py)
SCRIPT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(SCRIPT_FILE_DIR, "modules")
sys.path.insert(0, SCRIPTS_DIR)

from shared_logging import setup_logging, get_logger

logger = get_logger("setup_environment")

# ============================================================
# Constants
# ============================================================

STEPS = ("folders", "drives", "synology", "config", "startup")
BANNER_WIDTH = 60


# ============================================================
# Helpers
# ============================================================

def banner(title: str):
    """Print a section banner."""
    print()
    print("=" * BANNER_WIDTH)
    print(f"  {title}")
    print("=" * BANNER_WIDTH)


def status_line(label: str, ok: bool, detail: str = ""):
    """Print a coloured status line."""
    tag = "[OK]" if ok else "[!!]"
    msg = f"  {tag} {label}"
    if detail:
        msg += f" - {detail}"
    print(msg)


def confirm(prompt: str, auto_yes: bool) -> bool:
    """Ask for confirmation unless --yes was passed."""
    if auto_yes:
        print(f"{prompt} (auto-confirmed via --yes)")
        return True
    answer = input(f"{prompt} (y/n): ")
    return answer.strip().lower() in ("y", "yes")


def load_config(config_path: str) -> dict:
    """Load and validate setup_config.json."""
    path = Path(config_path)
    if not path.exists():
        example = Path(SCRIPT_FILE_DIR) / "setup_config.json.example"
        print(f"ERROR: Config file not found: {path}")
        if example.exists():
            print(f"  Copy the example and edit it:")
            print(f"    copy setup_config.json.example setup_config.json")
        sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # Basic validation
    required = ("drive_mappings", "folder_structure", "synology_drive", "pipeline_config")
    missing = [k for k in required if k not in cfg]
    if missing:
        print(f"ERROR: Config missing required keys: {', '.join(missing)}")
        sys.exit(1)

    return cfg


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


def parse_mountvol_letters() -> set:
    """Return the set of drive letters held by an actual volume mount point
    (real disks, removable media, SD readers, recovery partitions). Subst
    mappings are NOT included. Used to detect "letter already taken by
    hardware" cases that would otherwise make `subst` fail silently — this
    is the original-startup-script's `mountvol F: /d` step, automated.

    `mountvol` output format::

            \\\\?\\Volume{GUID}\\
                F:\\
    """
    letters = set()
    try:
        result = subprocess.run(
            ["mountvol"], capture_output=True, text=True, timeout=10
        )
        for line in result.stdout.splitlines():
            s = line.strip()
            # Match "X:\" — single letter + colon + backslash, no other text.
            if len(s) == 3 and s[0].isalpha() and s[1] == ":" and s[2] == "\\":
                letters.add(s[:2].upper())
    except Exception as e:
        logger.warning(f"Could not parse mountvol output: {e}")
    return letters


# ============================================================
# Step 1: Prerequisites
# ============================================================

def step_prerequisites(cfg: dict) -> bool:
    """Verify platform, Python version, and config."""
    banner("Step 1: Prerequisites")

    ok = True

    # Platform (informational only). Steps that need Windows-only mechanics
    # (drive-letter subst, registry persistence, the Windows shortcut) skip
    # themselves individually below - this no longer blocks the rest of setup.
    is_windows = sys.platform == "win32"
    status_line("Platform", True, sys.platform)
    if not is_windows:
        print("  Drive-letter mapping, registry persistence, and the Windows")
        print("  shortcut have no macOS/Linux equivalent and will be skipped.")

    # Python version
    ver = sys.version_info
    py_ok = ver >= (3, 8)
    status_line("Python", py_ok, sys.version.split()[0])
    if not py_ok:
        print("  ERROR: Python 3.8+ required.")
        ok = False

    # Modules importable
    try:
        from rack_settings import RackSettings  # noqa: F401
        status_line("Modules", True, "rack_settings importable")
    except ImportError as e:
        status_line("Modules", False, str(e))
        ok = False

    # Config loaded (already done before this step, but confirm)
    status_line("Config", True, "setup_config.json loaded")

    return ok


# ============================================================
# Step 2: Folder Structure
# ============================================================

def _looks_like_windows_path(path: str) -> bool:
    """True for a drive-letter ('D:\\...') or backslash-style path - the
    shape every path in setup_config.json.example currently uses. Meaningless
    (and not safely creatable) on macOS/Linux."""
    return (len(path) >= 2 and path[1] == ':') or '\\' in path


def step_folders(cfg: dict, dry_run: bool, auto_yes: bool) -> bool:
    """Create folder structure from config."""
    banner("Step 2: Folder Structure")

    fs = cfg["folder_structure"]
    categories = fs.get("categories", [])
    subcategories = fs.get("subcategories", {})
    extra_dirs = fs.get("extra_dirs", [])

    if sys.platform == "win32":
        bases = fs.get("bases", [])
    else:
        # setup_config.json.example's bases are Windows-only paths
        # (D:\_work\...). Off Windows, build the same structure under
        # rack_settings' own platform-aware defaults instead (e.g.
        # ~/_work/Active) rather than trying to create a literal
        # "D:\_work\Active"-named folder.
        try:
            from rack_settings import RackSettings
            settings = RackSettings()
            bases = [settings.get_active_base(), settings.get_archive_base()]
            print(f"  Non-Windows: using platform defaults instead of setup_config.json's paths:")
            for b in bases:
                print(f"    {b}")
        except Exception as e:
            print(f"  ERROR: could not resolve default folders: {e}")
            return False

    # Build full list of directories
    dirs_to_create = []

    for base in bases:
        dirs_to_create.append(base)
        for cat in categories:
            cat_path = os.path.join(base, cat)
            dirs_to_create.append(cat_path)
            for sub in subcategories.get(cat, []):
                dirs_to_create.append(os.path.join(cat_path, sub))

    skipped_extra = 0
    for d in extra_dirs:
        if sys.platform != "win32" and _looks_like_windows_path(d):
            skipped_extra += 1
            continue
        dirs_to_create.append(d)
    if skipped_extra:
        print(f"  Skipping {skipped_extra} Windows-only extra folder(s) from setup_config.json.")

    # Check status
    existing = []
    needed = []
    for d in dirs_to_create:
        if os.path.isdir(d):
            existing.append(d)
        else:
            needed.append(d)

    print(f"  Total directories: {len(dirs_to_create)}")
    print(f"  Already exist:     {len(existing)}")
    print(f"  To create:         {len(needed)}")

    if not needed:
        print("  All directories already exist.")
        return True

    print()
    print("  Directories to create:")
    for d in needed:
        print(f"    {d}")

    if dry_run:
        print("\n  [DRY RUN] No directories created.")
        return True

    if not confirm("\n  Create these directories?", auto_yes):
        print("  Skipped.")
        return True

    created = 0
    for d in needed:
        try:
            os.makedirs(d, exist_ok=True)
            created += 1
            logger.info(f"Created directory: {d}")
        except OSError as e:
            print(f"  ERROR creating {d}: {e}")
            logger.error(f"Failed to create directory {d}: {e}")

    print(f"  Created {created}/{len(needed)} directories.")
    return created == len(needed)


# ============================================================
# Step 3: Drive Mappings (subst + Registry)
# ============================================================

def step_drives(cfg: dict, dry_run: bool, auto_yes: bool) -> bool:
    """Set up subst drive mappings and registry autorun entries."""
    banner("Step 3: Drive Mappings (subst + Registry)")

    if sys.platform != "win32":
        print("  Skipping (Windows-only - drive letters have no macOS/Linux equivalent).")
        return True

    mappings = cfg.get("drive_mappings", [])
    if not mappings:
        print("  No drive mappings configured.")
        return True

    current_subst = parse_subst_output()
    held_letters = parse_mountvol_letters()
    ok = True

    for m in mappings:
        drive = m["drive_letter"].upper()
        target = m["target_path"]
        desc = m.get("description", "")
        reg_name = m.get("registry_name", f"SubstDrive_{drive[0]}")

        print(f"\n  {drive} -> {target}")
        if desc:
            print(f"    ({desc})")

        # --- subst mapping ---
        if drive in current_subst:
            current_target = current_subst[drive]
            if os.path.normcase(os.path.normpath(current_target)) == os.path.normcase(os.path.normpath(target)):
                status_line(f"{drive} subst", True, "already mapped correctly")
            else:
                status_line(f"{drive} subst", False,
                            f"mapped to WRONG target: {current_target}")
                print(f"    Expected: {target}")
                print(f"    Remove manually with: subst {drive} /d")
                ok = False
                continue
        else:
            # Verify the target dir exists. `subst` fails silently here
            # otherwise, which is why M:->D:\music etc. used to die with
            # an empty error message on a fresh PC. We deliberately do
            # NOT auto-create — the user may want to point the mapping
            # at a differently-named folder (e.g. _music vs music). Edit
            # the mapping in Settings -> Drives, or create the folder
            # manually, then re-run this step.
            if not os.path.isdir(target):
                status_line(f"{drive} subst", False,
                            f"target does not exist: {target}")
                print(f"    This mapping has no destination on disk yet.")
                print(f"    Open Settings -> Drives to point it at an existing folder")
                print(f"    or create {target} manually before retrying.")
                ok = False
                continue

            # If the letter is held by a real volume (SD reader, USB slot,
            # recovery partition) `subst` will fail. The original startup
            # .bat did `mountvol F: /d` for exactly this — automate it,
            # but ALWAYS prompt (ignore --yes) because dismounting the
            # wrong letter can take down the system drive.
            if drive in held_letters:
                if not _release_volume_letter(drive, dry_run):
                    ok = False
                    continue
                held_letters.discard(drive)

            if dry_run:
                print(f"    [DRY RUN] Would run: subst {drive} \"{target}\"")
            else:
                if not confirm(f"    Mount {drive} -> {target}?", auto_yes):
                    print("    Skipped.")
                    continue
                try:
                    subprocess.run(
                        ["subst", drive, target],
                        check=True, capture_output=True, text=True, timeout=10
                    )
                    status_line(f"{drive} subst", True, "mounted")
                    logger.info(f"Mounted subst drive: {drive} -> {target}")
                except subprocess.CalledProcessError as e:
                    # `subst` writes its errors to STDOUT, not stderr, so
                    # we have to fall back through both streams to surface
                    # a useful message.
                    msg = ((e.stderr or "").strip()
                           or (e.stdout or "").strip()
                           or f"exit code {e.returncode}")
                    status_line(f"{drive} subst", False, f"subst failed: {msg}")
                    logger.error(f"subst failed for {drive}: {msg}")
                    ok = False
                    continue

        # --- Registry autorun entry ---
        reg_value = f'subst {drive} "{target}"'
        _ok = _ensure_registry_autorun(reg_name, reg_value, dry_run)
        if not _ok:
            ok = False

        # --- Explorer display label (cosmetic — matches Visual Subst) ---
        label = m.get("label", "")
        if label and not _ensure_drive_label(drive, label, dry_run):
            ok = False

    # Verify accessibility
    if not dry_run:
        print()
        for m in mappings:
            drive = m["drive_letter"].upper()
            accessible = os.path.isdir(f"{drive}\\")
            status_line(f"{drive} accessible", accessible)
            if not accessible:
                ok = False

    return ok


def _release_volume_letter(drive: str, dry_run: bool) -> bool:
    """Free a drive letter held by a real volume via `mountvol <drive> /d`,
    so a subsequent `subst` can claim it. Always prompts (ignores --yes)
    because the wrong letter here could detach the system drive."""
    if dry_run:
        print(f"    [DRY RUN] Would run: mountvol {drive} /d")
        return True

    print(f"    {drive} is held by a mounted volume (SD reader, USB slot,")
    print(f"    recovery partition, or similar). It must be released before")
    print(f"    subst can map it.")
    # auto_yes=False on purpose — destructive op deserves an explicit y/N
    # even if the surrounding step ran with --yes.
    if not confirm(f"    Release {drive} via `mountvol {drive} /d`?",
                   auto_yes=False):
        print(f"    Skipped — {drive} remains held; subst will fail.")
        return False

    try:
        subprocess.run(
            ["mountvol", drive, "/d"],
            check=True, capture_output=True, text=True, timeout=10
        )
        status_line(f"{drive} mountvol", True, "released")
        logger.info(f"Released volume letter via mountvol: {drive}")
        return True
    except subprocess.CalledProcessError as e:
        msg = ((e.stderr or "").strip()
               or (e.stdout or "").strip()
               or f"exit code {e.returncode}")
        status_line(f"{drive} mountvol", False, f"mountvol failed: {msg}")
        logger.error(f"mountvol failed for {drive}: {msg}")
        return False


def _ensure_drive_label(drive: str, label: str, dry_run: bool) -> bool:
    """
    Set the Explorer display name for a drive (HKCU DriveIcons). Cosmetic only —
    doesn't touch the underlying volume label, and is the same key Visual Subst
    writes, so labels stay consistent between tools.
    """
    try:
        import winreg
    except ImportError:
        return False

    letter = drive.rstrip(":").upper()
    key_path = (rf"Software\Microsoft\Windows\CurrentVersion\Explorer"
                rf"\DriveIcons\{letter}\DefaultLabel")

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ) as key:
            existing, _ = winreg.QueryValueEx(key, "")
            if existing == label:
                status_line(f"{drive} label", True, f"already '{label}'")
                return True
    except FileNotFoundError:
        pass
    except Exception:
        pass

    if dry_run:
        print(f"    [DRY RUN] Would set Explorer label '{drive}' = {label}")
        return True

    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
        status_line(f"{drive} label", True, f"set to '{label}'")
        logger.info(f"Drive label set: {drive} = {label}")
        return True
    except Exception as e:
        status_line(f"{drive} label", False, str(e))
        logger.error(f"Failed to set drive label {drive}: {e}")
        return False


def _ensure_registry_autorun(name: str, value: str, dry_run: bool) -> bool:
    """Write or verify a HKCU\\...\\Run registry entry."""
    try:
        import winreg
    except ImportError:
        print("    WARNING: winreg not available (not on Windows?)")
        return False

    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"

    # Read existing value
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_READ) as key:
            existing, _ = winreg.QueryValueEx(key, name)
            if existing == value:
                status_line(f"Registry '{name}'", True, "already set")
                return True
            else:
                print(f"    Registry '{name}' has different value:")
                print(f"      Current:  {existing}")
                print(f"      Expected: {value}")
    except FileNotFoundError:
        pass  # Value doesn't exist yet
    except Exception as e:
        print(f"    WARNING: Could not read registry: {e}")

    if dry_run:
        print(f"    [DRY RUN] Would set registry '{name}' = {value}")
        return True

    # Write the value
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0,
                            winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        status_line(f"Registry '{name}'", True, "written")
        logger.info(f"Registry autorun set: {name} = {value}")
        return True
    except Exception as e:
        status_line(f"Registry '{name}'", False, str(e))
        logger.error(f"Failed to write registry {name}: {e}")
        return False


# ============================================================
# Step 4: Synology Drive Check
# ============================================================

def step_synology(cfg: dict, auto_yes: bool = False) -> bool:
    """
    Check Synology Drive installation and sync folder status. When sync tasks
    look missing, print a copy-paste card per task (server / username / local /
    remote / mode / on-demand) and offer to launch the Drive Client GUI.
    Synology Drive has no CLI for creating sync tasks, so this is the closest
    we can get to "config-driven" without poking the app's SQLite DB.
    """
    banner("Step 4: Synology Drive Check")

    if sys.platform != "win32":
        print("  Skipping the automated check (this detection is Windows-only for now).")
        print("  Synology Drive Client for Mac: https://www.synology.com/en-global/dsm/feature/drive")
        return True

    sd_cfg = cfg.get("synology_drive", {})
    conn = sd_cfg.get("connection", {}) or {}
    sync_folders = sd_cfg.get("sync_folders", [])
    ok = True

    # Detect installation
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
    status_line("Synology Drive installed", installed)

    # Check if running
    running = False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq SynologyDrive.exe"],
            capture_output=True, text=True, timeout=10
        )
        running = "SynologyDrive.exe" in result.stdout
    except Exception:
        pass
    status_line("Synology Drive running", running)

    if not installed:
        print("\n  ACTION REQUIRED: Install Synology Drive Client")
        print("    https://www.synology.com/en-global/dsm/feature/drive")
        return False

    # Check sync folders
    print()
    missing = []
    for sf in sync_folders:
        name = sf["name"]
        local = sf.get("local_path") or sf.get("expected_path", "")
        exists = os.path.isdir(local)
        populated = False
        if exists:
            try:
                populated = len(os.listdir(local)) > 0
            except OSError:
                pass

        if exists and populated:
            status_line(f"Sync: {name}", True, f"{local} (populated)")
        elif exists:
            status_line(f"Sync: {name}", False, f"{local} (empty - sync task may be missing)")
            missing.append(sf)
        else:
            status_line(f"Sync: {name}", False, f"{local} (not found)")
            missing.append(sf)

    if not missing:
        return ok

    ok = False
    host = conn.get("host") or "<your NAS host or IP>"
    port = conn.get("port") or 6690
    user = conn.get("username") or "<your username>"

    print("\n  ACTION REQUIRED: Create the following Synology Drive sync tasks.")
    print("  (No CLI exists for this — copy each block into the Drive Client GUI.)")
    print(f"  Password is NOT stored here; enter it once in the GUI and it goes")
    print(f"  into Windows Credential Manager.\n")

    mode_label = {
        "two_way":       "Bidirectional (two-way)",
        "upload_only":   "Upload only (local -> NAS)",
        "download_only": "Download only (NAS -> local)",
    }

    for sf in missing:
        local  = sf.get("local_path") or sf.get("expected_path", "")
        remote = sf.get("remote_path", "<remote path on NAS>")
        mode   = mode_label.get(sf.get("sync_mode", "two_way"), sf.get("sync_mode", "two_way"))
        ondem  = "Yes (Files On-Demand)" if sf.get("on_demand") else "No (full sync)"

        print(f"  ---- {sf['name']} " + "-" * max(0, 60 - len(sf["name"])))
        print(f"    Server:     {host}:{port}")
        print(f"    Username:   {user}")
        print(f"    Local:      {local}")
        print(f"    Remote:     {remote}")
        print(f"    Mode:       {mode}")
        print(f"    On-demand:  {ondem}")
        print()

    if drive_exe and confirm("  Open Synology Drive Client now?", auto_yes):
        try:
            subprocess.Popen([drive_exe], close_fds=True)
            print("  Drive Client launched.")
        except Exception as e:
            print(f"  Could not launch Drive Client: {e}")
            print(f"    Run manually: {drive_exe}")

    return ok


# ============================================================
# Step 5: Pipeline Config Generation
# ============================================================

def step_config(cfg: dict, dry_run: bool) -> bool:
    """Generate / update rack_config.json via RackSettings."""
    banner("Step 5: Pipeline Config Generation")

    try:
        from rack_settings import RackSettings
    except ImportError as e:
        print(f"  ERROR: Cannot import RackSettings: {e}")
        return False

    pc = cfg.get("pipeline_config", {})

    if sys.platform != "win32":
        # setup_config.json.example's pipeline_config values are Windows
        # drive letters / D:\ paths - meaningless (and would corrupt the
        # config) off Windows. Skip applying them and let RackSettings' own
        # platform-aware defaults (~/_work/Active etc.) stand; paths can
        # still be customized anytime via Settings (Ctrl+,).
        print("  Non-Windows: skipping setup_config.json's Windows-specific")
        print("  path overrides - using platform defaults instead.")
        pc = {}

    if dry_run:
        print("  [DRY RUN] Would apply the following settings:")
        for key, val in pc.items():
            print(f"    {key} = {val}")
        return True

    # Instantiate (creates rack_config.json if needed)
    settings = RackSettings()
    print(f"  Config file: {settings.config_path}")

    # Apply overrides
    if "work_drive" in pc:
        settings.set_work_drive(pc["work_drive"])
        print(f"  Set work_drive = {pc['work_drive']}")

    if "active_base" in pc:
        settings.set_active_base(pc["active_base"])
        print(f"  Set active_base = {pc['active_base']}")

    if "archive_base" in pc:
        settings.set_archive_base(pc["archive_base"])
        print(f"  Set archive_base = {pc['archive_base']}")

    if "mapped_software_path" in pc:
        settings.set_mapped_software_path(pc["mapped_software_path"])
        print(f"  Set mapped_software_path = {pc['mapped_software_path']}")

    if "launchers_base_path" in pc:
        settings.set_launchers_base_path(pc["launchers_base_path"])
        print(f"  Set launchers_base_path = {pc['launchers_base_path']}")

    # Validate
    print("\n  Validation:")
    results = settings.validate_all()
    all_ok = True
    for path_name, (valid, msg) in results.items():
        status_line(path_name, valid, msg)
        if not valid:
            all_ok = False

    return all_ok


# ============================================================
# Step 6: Startup Apps Launcher
# ============================================================

def step_startup(cfg: dict, auto_yes: bool = False, dry_run: bool = False) -> bool:
    """Deploy the startup-apps PowerShell launcher under %LOCALAPPDATA% and
    optionally register the FastRack_StartupLauncher scheduled task. Idempotent
    — re-running this step is safe.

    The launcher reads %LOCALAPPDATA%\\PipelineManager\\startup_apps.json,
    which is owned by the FastRack Settings dialog's "Startup Apps" tab.
    """
    banner("Step 6: Startup Apps Launcher")

    if sys.platform != "win32":
        print("  Skipping (Windows-only).")
        return True

    try:
        import startup_apps_manager as sam
    except ImportError as e:
        status_line("startup_apps_manager", False, str(e))
        return False

    # 1. Deploy the script
    if dry_run:
        print(f"  [DRY RUN] Would copy launcher + AHK to "
              f"{sam.deployed_launcher_path().parent}")
    else:
        ok, detail = sam.deploy_launcher_script()
        status_line("Deploy launcher", ok, detail)
        if not ok:
            return False

    # 2. Scheduled task
    if sam.is_task_installed():
        status_line("Scheduled task", True, f"already registered as {sam.TASK_NAME}")
        print("  (Use Settings > Startup Apps > Uninstall to remove it.)")
        return True

    if dry_run:
        print(f"  [DRY RUN] Would register scheduled task {sam.TASK_NAME}")
        return True

    if not confirm(f"\n  Register scheduled task '{sam.TASK_NAME}' to run at logon?",
                   auto_yes):
        print("  Skipped — you can install it later via Settings > Startup Apps.")
        return True

    ok, detail = sam.install_scheduled_task()
    status_line("Scheduled task", ok, detail)
    return ok


# ============================================================
# Step 7: Final Report
# ============================================================

def final_report(results: dict):
    """Print summary of all steps."""
    banner("Setup Complete")

    for step_name, ok in results.items():
        status_line(step_name, ok)

    any_failed = not all(results.values())

    if any_failed:
        print("\n  Some steps had issues. Review the output above.")
    else:
        print("\n  All steps completed successfully.")

    print("\n  Manual steps remaining:")
    step_num = 1
    print(f"    {step_num}. Create Synology Drive sync tasks (if not done)")
    step_num += 1
    if sys.platform == "win32":
        print(f"    {step_num}. Reboot to verify drive persistence via registry")
        step_num += 1
    print(f"    {step_num}. Launch FastRack and verify paths in Settings (Ctrl+,)")
    step_num += 1
    if sys.platform == "win32":
        print(f"    {step_num}. In FastRack > Settings, run 'Install Dependencies'")
        print("       and 'Create Shortcut' if not already done.")
    else:
        print(f"    {step_num}. In FastRack > Settings, run 'Install Dependencies' if not already done.")


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Fastrack - Environment Setup"
    )
    parser.add_argument(
        "--config", default="./setup_config.json",
        help="Path to setup_config.json (default: ./setup_config.json)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would happen without making changes"
    )
    parser.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompts"
    )
    parser.add_argument(
        "--step", choices=STEPS + ("all",), default="all",
        help="Run only one step: folders, drives, synology, config, or all"
    )

    args = parser.parse_args()

    # Set up logging (console only for setup script, file handler deferred)
    setup_logging("setup_environment", include_console=False)

    # Banner
    print("=" * BANNER_WIDTH)
    print("  Fastrack - Environment Setup")
    print("=" * BANNER_WIDTH)
    print(f"  Python:  {sys.version.split()[0]}")
    print(f"  Config:  {args.config}")
    if args.dry_run:
        print("  Mode:    DRY RUN (no changes will be made)")
    if args.step != "all":
        print(f"  Step:    {args.step} only")
    print("=" * BANNER_WIDTH)

    # Load config
    cfg = load_config(args.config)

    run_all = args.step == "all"
    results = {}

    # Step 1: Prerequisites (always runs)
    if run_all or args.step in STEPS:
        ok = step_prerequisites(cfg)
        results["Prerequisites"] = ok
        if not ok and run_all:
            print("\n  Prerequisites not met. Fix the issues above and re-run.")
            sys.exit(1)

    # Step 2: Folders
    if run_all or args.step == "folders":
        results["Folder Structure"] = step_folders(cfg, args.dry_run, args.yes)

    # Step 3: Drives
    if run_all or args.step == "drives":
        results["Drive Mappings"] = step_drives(cfg, args.dry_run, args.yes)

    # Step 4: Synology
    if run_all or args.step == "synology":
        results["Synology Drive"] = step_synology(cfg, args.yes)

    # Step 5: Config
    if run_all or args.step == "config":
        results["Pipeline Config"] = step_config(cfg, args.dry_run)

    # Step 6: Startup Apps Launcher
    if run_all or args.step == "startup":
        results["Startup Apps"] = step_startup(cfg, args.yes, args.dry_run)

    # Step 7: Report
    final_report(results)

    # Windows console keep-alive
    if os.name == "nt" and not sys.stdin.isatty():
        print("\nPress Enter to exit...")
        input()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nSetup cancelled by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        logger.exception("Setup failed")
        if os.name == "nt" and not sys.stdin.isatty():
            print("\nPress Enter to exit...")
            input()
        sys.exit(1)
