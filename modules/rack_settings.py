"""
Rack Settings Module

Centralized settings for FastRack.
Handles paths, software defaults, and other configuration.
"""

import json
import os
import sys
import subprocess
from pathlib import Path
from typing import Dict, Optional, Tuple

from shared_logging import get_logger

logger = get_logger(__name__)


def _get_appdata_path() -> Path:
    """Get the appropriate AppData path for the platform."""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "PipelineManager"
    elif sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PipelineManager"
    else:
        # WSL/Linux: use Windows user profile via /mnt/c
        windows_appdata = Path("/mnt/c/Users")
        if windows_appdata.exists():
            username = os.environ.get("USER", "")
            user_path = windows_appdata / username
            if user_path.exists():
                return user_path / "AppData" / "Local" / "PipelineManager"
        # Fallback to Linux standard location
        return Path.home() / ".local" / "share" / "PipelineManager"


def _is_bare_drive_letter(value: str) -> bool:
    """True if `value` is a Windows drive letter shorthand ('I' or 'I:'),
    as opposed to a full path. Only meaningful on Windows - macOS/Linux have
    no drive-letter concept, so a "work root" there is always a full path."""
    if sys.platform != "win32" or not value:
        return False
    letter = value.rstrip(':')
    return len(letter) == 1 and letter.isalpha()


def join_native_path(base: str, *parts: str) -> str:
    """Join a base path with subpath parts using the current OS's separator.

    Plain os.path.join() mishandles a bare Windows drive letter: on Windows,
    os.path.join("I:", "Visual") == "I:Visual" (drive-relative, no
    separator), not "I:\\Visual" - because Python treats a 2-char drive
    string as already having an implicit anchor. This normalizes that one
    case before delegating to os.path.join, so callers never have to hand-rebuild
    paths with a literal "\\" (which corrupts POSIX paths on macOS/Linux).
    """
    if _is_bare_drive_letter(base):
        base = base + "\\"
    return os.path.join(base, *parts) if parts else base


def _normalize_user_path(path: str) -> str:
    """Normalize a user-entered path's separators for the current OS.

    Unlike the old ``path.replace('/', '\\\\')`` this never runs on
    macOS/Linux, where it would otherwise mangle a genuine POSIX path
    (e.g. "/Users/flori/work") into something invalid. An empty string
    (meaning "unset - use the derived default") is passed through as-is
    rather than becoming os.path.normpath's ".".
    """
    if not path:
        return ""
    if _is_bare_drive_letter(path):
        return path.upper() if path.endswith(':') else path.upper() + ':'
    return os.path.normpath(path)


def _default_work_root() -> str:
    """Default work root: a drive letter on Windows (mapped via VisualSubst
    to active_base), or - since macOS/Linux have no drive-substitution
    equivalent - the same real folder as the active base."""
    if sys.platform == "win32":
        return "I:"
    return _default_active_base()


def _default_active_base() -> str:
    if sys.platform == "win32":
        return "D:\\_work\\Active"
    return str(Path.home() / "_work" / "Active")


def _default_archive_base() -> str:
    if sys.platform == "win32":
        return "D:\\_work\\Archive"
    return str(Path.home() / "_work" / "Archive")


def _default_software_sync() -> Dict[str, str]:
    if sys.platform == "win32":
        return {
            "nas_software_path": "D:\\_work\\_PIPELINE\\Software",
            "mapped_software_path": "P:\\Software",
            "launchers_base_path": "P:\\Launchers",
        }
    home = Path.home()
    return {
        "nas_software_path": "",
        "mapped_software_path": str(home / "_work" / "Software"),
        "launchers_base_path": str(home / "_work" / "Launchers"),
    }


def _load_setup_seed() -> Optional[Dict]:
    """First-run seed for rack_config.json.

    rack_settings is the single runtime reader of pipeline config.
    setup_environment used to be the only way to push values from the
    project-root setup_config.json into rack_config.json; this function lets
    RackSettings pull the same values itself the first time it runs on a new
    machine.

    Returns a dict with optional keys ``drives`` and ``software_sync`` that
    overlay DEFAULT_CONFIG, or None if no seed file exists / nothing to apply.
    """
    seed_path = Path(__file__).resolve().parent.parent / "setup_config.json"
    if not seed_path.exists():
        return None
    try:
        with open(seed_path, "r", encoding="utf-8") as f:
            seed = json.load(f)
    except Exception as e:
        logger.warning(f"Could not read setup seed {seed_path}: {e}")
        return None

    pc = seed.get("pipeline_config", {}) or {}
    if not pc:
        return None

    overrides: Dict = {"drives": {}, "software_sync": {}}
    if "work_drive" in pc:
        overrides["drives"]["work"] = pc["work_drive"]
    if "active_base" in pc:
        overrides["drives"]["active_base"] = pc["active_base"]
    if "archive_base" in pc:
        overrides["drives"]["archive_base"] = pc["archive_base"]
    if "mapped_software_path" in pc:
        overrides["software_sync"]["mapped_software_path"] = pc["mapped_software_path"]
    if "launchers_base_path" in pc:
        overrides["software_sync"]["launchers_base_path"] = pc["launchers_base_path"]

    if not overrides["drives"] and not overrides["software_sync"]:
        return None
    return overrides


class RackSettings:
    """
    Manages settings for FastRack.

    Provides centralized access to paths, software defaults, and settings,
    with support for drive validation and per-category configuration.

    Default configuration:
    - Work root: I:\\ on Windows (mapped via VisualSubst to Active); the
      Active base path directly on macOS/Linux, which have no drive-letter
      substitution equivalent
    - Archive base: D:\\_work\\Archive (or ~/_work/Archive off Windows)

    Categories and their default subpaths:
    - Visual: Visual/ (includes GD, CG, VJ subcategories)
    - RealTime: RealTime/ (includes Godot, TouchDesigner, Resolume subcategories)
    - Audio: Audio/
    - Physical: Physical/
    - Photo: Photo/
    - Web: Web/
    """

    # Default configuration
    DEFAULT_CONFIG = {
        "version": "1.1.0",
        "drives": {
            "work": _default_work_root(),
            "active_base": _default_active_base(),
            "archive_base": _default_archive_base()
        },
        "categories": {
            "Visual": {
                "work_subpath": "Visual",
                "archive_subpath": "Visual",
                "subcategories": ["GD", "CG", "VJ"]
            },
            "RealTime": {
                "work_subpath": "RealTime",
                "archive_subpath": "RealTime",
                "subcategories": ["Godot", "TD", "Resolume"]
            },
            "Audio": {
                "work_subpath": "Audio",
                "archive_subpath": "Audio",
                "subcategories": []
            },
            "Physical": {
                "work_subpath": "Physical",
                "archive_subpath": "Physical",
                "subcategories": []
            },
            "Photo": {
                "work_subpath": "Photo",
                "archive_subpath": "Photo",
                "subcategories": []
            },
            "Web": {
                "work_subpath": "Web",
                "archive_subpath": "Web",
                "subcategories": []
            }
        },
        # UI preferences
        "ui": {
            "start_fullscreen": False,
            # When True, the FastRack hub window stays at the bottom of
            # the Windows z-order — clicking it does not bring it to
            # the foreground, other apps always appear on top.
            # Standalone module windows (Tk Toplevels from subprocess
            # launchers) are unaffected and float above as usual.
            "always_on_bottom": True,
            # Remembered "WxH+X+Y" for the Settings dialog so the user's
            # manual resize sticks across sessions. Empty = use default
            # size (and center on parent).
            "settings_dialog_geometry": ""
        },
        # Global software version defaults (one version per software, used everywhere)
        "software_defaults": {
            "houdini": "20.5",
            "blender": "4.4",
            "fusion": "19",
            "resolume": "Arena 7",
            "after_effects": "2024",
            "touchdesigner": "2023.11760",
            "godot": "4.3",
            "ableton": "12",
            "reaper": "7",
            "traktor": "",
            "freecad": "",
            "alibre": "",
            "affinity": "",
            "python": "3.11",
            "slicer": "Bambu Studio",
            "printer": "Bambu Lab X1 Carbon",
            "platform": "PC/Desktop",
            "renderer": "Forward+",
            "resolution": "1920x1080"
        },
        "software_sync": _default_software_sync(),
        # Business / invoices — authoritative paths for InvoiceManager.
        # Empty strings mean "auto-derive" (see getters): boekhouding_base
        # falls back to <active_base>/_LIBRARY/Boekhouding, invoice_db_path
        # to AppData/PipelineManager/global_invoice/invoices.sqlite, and
        # soffice_path to PATH lookup + common install locations.
        "business": {
            "boekhouding_base": "",
            "invoice_db_path": "",
            "soffice_path": ""
        }
    }

    # Ordered list of categories for consistent UI display
    CATEGORY_ORDER = ["Visual", "RealTime", "Audio", "Physical", "Photo", "Web"]

    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize the pipeline configuration.

        Args:
            config_path: Path to config file. If None, uses default location.
        """
        if config_path is None:
            app_data = _get_appdata_path()
            app_data.mkdir(parents=True, exist_ok=True)
            self.config_path = app_data / "rack_config.json"
        else:
            self.config_path = Path(config_path)

        self.config = self._load_or_create()
        logger.info(f"Configuration loaded: {self.config_path}")

    def _load_or_create(self) -> Dict:
        """Load configuration from file or create default.

        On first run (no rack_config.json yet), the project-root
        setup_config.json is used as a one-time seed for drives and
        software_sync paths. After this initial save, rack_config.json is
        canonical and setup_config.json is no longer consulted at runtime.
        """
        import copy
        try:
            if self.config_path.exists():
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle new fields
                    return self._merge_with_defaults(loaded)
            else:
                seeded = copy.deepcopy(self.DEFAULT_CONFIG)
                seed = _load_setup_seed()
                if seed:
                    if seed.get("drives"):
                        seeded["drives"].update(seed["drives"])
                    if seed.get("software_sync"):
                        seeded["software_sync"].update(seed["software_sync"])
                    logger.info("Config not found; seeded from setup_config.json")
                else:
                    logger.info("Config not found, creating default")
                self._save(seeded)
                return seeded
        except Exception as e:
            logger.error(f"Error loading config: {e}")
            return self.DEFAULT_CONFIG.copy()

    def _merge_with_defaults(self, loaded: Dict) -> Dict:
        """Merge loaded config with defaults to ensure all keys exist."""
        import copy
        result = copy.deepcopy(self.DEFAULT_CONFIG)

        # Update drives
        if "drives" in loaded:
            result["drives"].update(loaded["drives"])

        # Update categories (preserve user customizations)
        if "categories" in loaded:
            for cat, cat_config in loaded["categories"].items():
                if cat in result["categories"]:
                    result["categories"][cat].update(cat_config)
                else:
                    result["categories"][cat] = cat_config

        # Update software_defaults (preserve user customizations)
        if "software_defaults" in loaded:
            loaded_sw = loaded["software_defaults"]
            # Handle flat structure (current format)
            if loaded_sw and not any(isinstance(v, dict) for v in loaded_sw.values()):
                result["software_defaults"].update(loaded_sw)
            else:
                # Legacy nested format: extract values and flatten
                for key, value in loaded_sw.items():
                    if isinstance(value, dict):
                        # Could be a category with subcategories or a flat category
                        for subkey, subvalue in value.items():
                            if isinstance(subvalue, dict):
                                # Nested subcategory - extract software versions
                                result["software_defaults"].update(subvalue)
                            else:
                                result["software_defaults"][subkey] = subvalue
                    else:
                        result["software_defaults"][key] = value

        # Update UI preferences
        if "ui" in loaded:
            result["ui"].update(loaded["ui"])

        # Update software_sync (preserve user customizations)
        if "software_sync" in loaded:
            result["software_sync"].update(loaded["software_sync"])

        # Update business / invoices paths (preserve user customizations)
        if "business" in loaded:
            result["business"].update(loaded["business"])

        # Preserve version from loaded if newer
        if "version" in loaded:
            result["version"] = loaded["version"]

        return result

    def _save(self, config: Optional[Dict] = None):
        """Save configuration to file."""
        if config is None:
            config = self.config

        try:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
            logger.debug("Configuration saved")
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            raise

    def save(self):
        """Public method to save current configuration."""
        self._save()

    # ==================== GETTERS ====================

    def get_start_fullscreen(self) -> bool:
        """Get whether the app should start in borderless fullscreen."""
        return self.config.get("ui", {}).get("start_fullscreen", False)

    def set_start_fullscreen(self, value: bool):
        """Set whether the app should start in borderless fullscreen."""
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config["ui"]["start_fullscreen"] = value
        self._save()

    def get_always_on_bottom(self) -> bool:
        """Whether the FastRack hub stays beneath all other windows."""
        return self.config.get("ui", {}).get("always_on_bottom", True)

    def set_always_on_bottom(self, value: bool):
        """Set whether the FastRack hub stays beneath all other windows."""
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config["ui"]["always_on_bottom"] = value
        self._save()

    def get_settings_dialog_geometry(self) -> str:
        """Remembered "WxH+X+Y" string from the Settings dialog. Empty
        means "use the default + center on parent"."""
        return self.config.get("ui", {}).get("settings_dialog_geometry", "")

    def set_settings_dialog_geometry(self, geo: str):
        """Persist the Settings dialog's current geometry. Pass "" to
        clear (will go back to default size on next open)."""
        if "ui" not in self.config:
            self.config["ui"] = {}
        self.config["ui"]["settings_dialog_geometry"] = geo or ""
        self._save()

    def get_work_drive(self) -> str:
        """Get the work root - a drive letter on Windows (e.g. 'I:'), or a
        full path on macOS/Linux (no drive-substitution equivalent there)."""
        return self.config["drives"]["work"]

    def get_active_base(self) -> str:
        """Get the active base path (e.g., 'D:\\_work\\Active')."""
        return self.config["drives"].get("active_base") or _default_active_base()

    def get_archive_base(self) -> str:
        """Get the archive base path (e.g., 'D:\\_work\\Archive')."""
        return self.config["drives"].get("archive_base") or _default_archive_base()

    def get_mapped_software_path(self) -> str:
        """Get the mapped drive path for software sync (e.g., 'P:\\Software')."""
        return self.config.get("software_sync", {}).get(
            "mapped_software_path") or _default_software_sync()["mapped_software_path"]

    def get_launchers_base_path(self) -> str:
        """Get the base path for software launchers (e.g., 'D:\\_work\\_PIPELINE\\Launchers')."""
        return self.config.get("software_sync", {}).get(
            "launchers_base_path") or _default_software_sync()["launchers_base_path"]

    # ----- Business / invoices paths --------------------------------

    def get_boekhouding_base_explicit(self) -> str:
        """Raw user-set value from rack_config.json — empty if unset."""
        return (self.config.get("business") or {}).get("boekhouding_base", "")

    def get_boekhouding_base(self) -> str:
        """Authoritative bookkeeping root used by InvoiceManager.

        Returns the explicit override if configured, otherwise derives
        ``<active_base>/_LIBRARY/Boekhouding`` so a fresh machine works
        out of the box.
        """
        explicit = self.get_boekhouding_base_explicit()
        if explicit:
            return explicit
        return join_native_path(self.get_active_base(), "_LIBRARY", "Boekhouding")

    def get_invoice_db_path(self) -> str:
        """Override path for the invoice registry SQLite DB.

        Empty string means "use the global_invoice default", which lives
        in AppData under PipelineManager/global_invoice/invoices.sqlite.
        """
        return (self.config.get("business") or {}).get("invoice_db_path", "")

    def get_soffice_path(self) -> str:
        """Explicit LibreOffice ``soffice`` binary path.

        Empty string means "auto-detect from PATH + common locations".
        """
        return (self.config.get("business") or {}).get("soffice_path", "")

    def get_work_path(self, category: str) -> str:
        """
        Get the full work path for a category.

        Args:
            category: Category name (e.g., 'Visual', 'Audio')

        Returns:
            Full path like 'I:\\Visual'
        """
        work_drive = self.get_work_drive()
        cat_config = self.config["categories"].get(category, {})
        subpath = cat_config.get("work_subpath", category)

        return join_native_path(work_drive, subpath)

    def get_active_path(self, category: str) -> str:
        """
        Get the full active base path for a category (real path, not drive letter).

        Args:
            category: Category name (e.g., 'Visual', 'Audio')

        Returns:
            Full path like 'D:\\_work\\Active\\Visual'
        """
        active_base = self.get_active_base()
        cat_config = self.config["categories"].get(category, {})
        subpath = cat_config.get("work_subpath", category)

        return join_native_path(active_base, subpath)

    def get_archive_path(self, category: str) -> str:
        """
        Get the full archive path for a category.

        Args:
            category: Category name (e.g., 'Visual', 'Audio')

        Returns:
            Full path like 'D:\\_work\\Archive\\Visual'
        """
        archive_base = self.get_archive_base()
        cat_config = self.config["categories"].get(category, {})
        subpath = cat_config.get("archive_subpath", category)

        return join_native_path(archive_base, subpath)

    def get_category_config(self, category: str) -> Dict:
        """Get the full configuration for a category."""
        return self.config["categories"].get(category, {})

    def get_all_categories(self) -> Dict:
        """Get all category configurations."""
        return self.config["categories"]

    def get_ordered_categories(self) -> list:
        """Get categories in the defined display order."""
        return self.CATEGORY_ORDER.copy()

    def get_software_defaults(self, category: str = None, subcategory: str = None) -> Dict[str, str]:
        """
        Get software version defaults.

        The category and subcategory parameters are accepted for backwards
        compatibility but ignored - all software versions are global.

        Returns:
            Dict of software names to default versions
        """
        return self.config.get("software_defaults", {})

    # ==================== SETTERS ====================

    def set_work_drive(self, drive: str):
        """
        Set the work root.

        Args:
            drive: A drive letter on Windows (e.g. 'I:' or 'I'), or a full
                path on macOS/Linux (e.g. '/Users/name/_work/Active').
        """
        drive = _normalize_user_path((drive or "").strip())

        self.config["drives"]["work"] = drive
        self._save()
        logger.info(f"Work root set to: {drive}")

    def set_active_base(self, path: str):
        """
        Set the active base path.

        Args:
            path: Active base path (e.g., 'D:\\_work\\Active')
        """
        path = _normalize_user_path(path)

        self.config["drives"]["active_base"] = path
        self._save()
        logger.info(f"Active base set to: {path}")

    def set_archive_base(self, path: str):
        """
        Set the archive base path.

        Args:
            path: Archive base path (e.g., 'D:\\_work\\Archive')
        """
        path = _normalize_user_path(path)

        self.config["drives"]["archive_base"] = path
        self._save()
        logger.info(f"Archive base set to: {path}")

    def set_mapped_software_path(self, path: str):
        """Set the mapped drive path for software sync."""
        path = _normalize_user_path(path)
        if "software_sync" not in self.config:
            self.config["software_sync"] = {}
        self.config["software_sync"]["mapped_software_path"] = path
        self._save()
        logger.info(f"Mapped software path set to: {path}")

    def set_launchers_base_path(self, path: str):
        """Set the base path for software launchers."""
        path = _normalize_user_path(path)
        if "software_sync" not in self.config:
            self.config["software_sync"] = {}
        self.config["software_sync"]["launchers_base_path"] = path
        self._save()
        logger.info(f"Launchers base path set to: {path}")

    def set_boekhouding_base(self, path: str):
        """Set the bookkeeping root path. Empty string restores the
        derived default (<active_base>/_LIBRARY/Boekhouding)."""
        path = _normalize_user_path(path or "")
        if "business" not in self.config:
            self.config["business"] = {}
        self.config["business"]["boekhouding_base"] = path
        self._save()
        logger.info(f"Boekhouding base set to: {path or '(derived from active_base)'}")

    def set_invoice_db_path(self, path: str):
        """Set the invoice registry DB override. Empty restores default."""
        path = _normalize_user_path(path or "")
        if "business" not in self.config:
            self.config["business"] = {}
        self.config["business"]["invoice_db_path"] = path
        self._save()
        logger.info(f"Invoice DB path set to: {path or '(default)'}")

    def set_soffice_path(self, path: str):
        """Set the LibreOffice soffice path. Empty restores autodetect."""
        path = _normalize_user_path(path or "")
        if "business" not in self.config:
            self.config["business"] = {}
        self.config["business"]["soffice_path"] = path
        self._save()
        logger.info(f"soffice path set to: {path or '(autodetect)'}")

    def set_category_paths(self, category: str, work_subpath: str = None,
                          archive_subpath: str = None):
        """
        Set custom subpaths for a category.

        Args:
            category: Category name
            work_subpath: Custom work subdirectory (relative to work drive)
            archive_subpath: Custom archive subdirectory (relative to archive base)
        """
        if category not in self.config["categories"]:
            self.config["categories"][category] = {}

        if work_subpath is not None:
            self.config["categories"][category]["work_subpath"] = work_subpath

        if archive_subpath is not None:
            self.config["categories"][category]["archive_subpath"] = archive_subpath

        self._save()
        logger.info(f"Updated paths for category: {category}")

    def set_software_defaults(self, **software_versions):
        """
        Set software version defaults.

        Args:
            **software_versions: Software name=version pairs (e.g., houdini="20.5")
        """
        if "software_defaults" not in self.config:
            self.config["software_defaults"] = {}

        self.config["software_defaults"].update(software_versions)
        self._save()
        logger.info(f"Updated software defaults: {list(software_versions.keys())}")

    # ==================== VALIDATION ====================

    def validate_drive(self, drive_or_path: str) -> Tuple[bool, str]:
        """
        Validate if a drive/path is accessible.

        Handles:
        - Regular drives (C:, D:)
        - Mapped drives via VisualSubst (I:, P:)
        - Network paths

        Args:
            drive_or_path: Drive letter (e.g., 'I:') or full path

        Returns:
            Tuple of (is_valid, status_message)
        """
        if not drive_or_path:
            return False, "No path configured"

        # Extract drive letter if a "X:" / "X:\..." form was given.
        if len(drive_or_path) >= 2 and drive_or_path[1] == ':':
            drive = drive_or_path[:2].upper()
            check_path = drive_or_path
        elif os.path.isabs(drive_or_path) or drive_or_path.startswith('\\\\'):
            # A genuine full path with no drive-letter notation - a POSIX
            # path on macOS/Linux, or a UNC share. Not a bare drive letter,
            # so leave it untouched rather than mangling it as one below.
            drive = None
            check_path = drive_or_path
        else:
            # Bare drive-letter shorthand, e.g. "I" (Windows-only concept).
            drive = drive_or_path.upper()
            if not drive.endswith(':'):
                drive = f"{drive}:"
            check_path = f"{drive}\\"

        # Convert for WSL if needed
        if sys.platform != "win32":
            check_path = self._to_wsl_path(check_path)

        # Check if path exists
        try:
            path = Path(check_path)
            if path.exists():
                # Additional check: try to list directory to verify access
                try:
                    list(path.iterdir())
                    return True, "Mounted and accessible"
                except PermissionError:
                    return False, "Permission denied"
                except Exception as e:
                    return False, f"Access error: {e}"
            else:
                # Check if it's a subst/mapped drive that might not be mounted
                is_subst = drive is not None and self._is_subst_drive(drive)
                if is_subst:
                    return False, "Mapped drive not mounted (VisualSubst)"
                else:
                    return False, "Drive/path not found"
        except Exception as e:
            return False, f"Validation error: {e}"

    def _is_subst_drive(self, drive: str) -> bool:
        """
        Check if a drive letter is a substituted drive (via subst or VisualSubst).

        Args:
            drive: Drive letter (e.g., 'I:')

        Returns:
            True if it's a subst drive, False otherwise
        """
        if sys.platform != "win32":
            # Can't check subst from WSL directly
            return False

        try:
            result = subprocess.run(
                ['subst'],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Output format: "I:\: => D:\_work\Active"
            drive_upper = drive.upper()
            for line in result.stdout.splitlines():
                if line.startswith(f"{drive_upper}\\:"):
                    return True
            return False
        except Exception:
            return False

    def _to_wsl_path(self, windows_path: str) -> str:
        """Convert Windows path to WSL path."""
        path = windows_path.replace('\\', '/')
        if len(path) >= 2 and path[1] == ':':
            drive = path[0].lower()
            rest = path[2:]
            if rest.startswith('/'):
                rest = rest[1:]
            return f"/mnt/{drive}/{rest}"
        return path

    def validate_work_drive(self) -> Tuple[bool, str]:
        """Validate the configured work drive."""
        return self.validate_drive(self.get_work_drive())

    def validate_archive_base(self) -> Tuple[bool, str]:
        """Validate the configured archive base path."""
        return self.validate_drive(self.get_archive_base())

    def validate_all(self) -> Dict[str, Tuple[bool, str]]:
        """
        Validate all configured paths.

        Returns:
            Dictionary with path names as keys and (is_valid, message) tuples as values
        """
        results = {
            "work_drive": self.validate_work_drive(),
            "archive_base": self.validate_archive_base()
        }

        # Validate each category's work path
        for category in self.CATEGORY_ORDER:
            work_path = self.get_work_path(category)
            results[f"{category}_work"] = self.validate_drive(work_path)

        return results

    # ==================== UTILITIES ====================

    @staticmethod
    def _translate_prefix(path: str, from_base: str, to_base: str) -> str:
        """If `path` lives under `from_base`, rewrite that prefix to `to_base`
        (keeping the rest of the path unchanged); otherwise return `path` as-is.

        Comparison and joining use the current OS's native separator -
        forcing backslash unconditionally here used to corrupt POSIX paths
        on macOS/Linux.
        """
        sep = '\\' if sys.platform == "win32" else '/'
        other_sep = '/' if sep == '\\' else '\\'
        normalized_path = path.replace(other_sep, sep)
        normalized_base = from_base.replace(other_sep, sep)

        if not normalized_base or not normalized_path.lower().startswith(normalized_base.lower()):
            return path

        relative = normalized_path[len(normalized_base):].lstrip(sep)
        return join_native_path(to_base, relative) if relative else to_base

    def convert_to_work_drive_path(self, stored_path: str) -> str:
        """
        Convert a stored D:\\_work\\Active path to the configured work drive path.

        This is useful for opening active project folders when the work drive
        is mapped via VisualSubst to D:\\_work\\Active.

        Args:
            stored_path: Path stored in database (e.g., 'D:\\_work\\Active\\Visual\\Project')

        Returns:
            Path with work drive (e.g., 'I:\\Visual\\Project') or original if not applicable
        """
        return self._translate_prefix(stored_path, self.get_active_base(), self.get_work_drive())

    def to_active_base_path(self, work_path: str) -> str:
        """
        The reverse of convert_to_work_drive_path: rewrite a work-drive path
        to its underlying active-base path (e.g. 'I:\\Web\\site' ->
        'D:\\_work\\Active\\Web\\site').

        Useful where tools misbehave against a subst-mapped drive (e.g.
        npm/pnpm resolving symlinks under node_modules) and need the real
        path instead. On macOS/Linux, work root and active base are the
        same path by default, so this is typically a no-op there.
        """
        return self._translate_prefix(work_path, self.get_work_drive(), self.get_active_base())

    def reset_to_defaults(self):
        """Reset configuration to defaults."""
        import copy
        self.config = copy.deepcopy(self.DEFAULT_CONFIG)
        self._save()
        logger.info("Configuration reset to defaults")

    def get_platform_path(self, windows_path: str) -> Path:
        """
        Convert Windows path to appropriate platform path.

        Args:
            windows_path: Path in Windows format (e.g., 'I:\\Visual')

        Returns:
            Path object appropriate for current platform
        """
        if sys.platform == "win32":
            return Path(windows_path)
        else:
            return Path(self._to_wsl_path(windows_path))

    def to_display_path(self, path: str) -> str:
        """
        Convert internal path to display format.
        Uses forward slashes for cleaner display.

        Args:
            path: Path string

        Returns:
            Display-friendly path string
        """
        return path.replace('\\', '/')


# Singleton instance for easy access
_instance: Optional[RackSettings] = None


def get_rack_settings() -> RackSettings:
    """
    Get the singleton RackSettings instance.

    This provides a convenient way for modules to access settings
    without needing to create their own instance.

    Returns:
        RackSettings singleton instance
    """
    global _instance
    if _instance is None:
        _instance = RackSettings()
    return _instance


# Backwards compatibility aliases
get_path_config = get_rack_settings
get_config = get_rack_settings
PathConfig = RackSettings
PipelineConfig = RackSettings


# Example usage and testing
if __name__ == "__main__":
    settings = RackSettings()

    print("=== Rack Settings ===")
    print(f"Work Drive: {settings.get_work_drive()}")
    print(f"Archive Base: {settings.get_archive_base()}")
    print()

    print("=== Category Paths ===")
    for category in settings.get_ordered_categories():
        work = settings.get_work_path(category)
        archive = settings.get_archive_path(category)
        print(f"{category}:")
        print(f"  Work: {work}")
        print(f"  Archive: {archive}")
    print()

    print("=== Validation ===")
    work_valid, work_msg = settings.validate_work_drive()
    archive_valid, archive_msg = settings.validate_archive_base()
    print(f"Work Drive: {'OK' if work_valid else 'FAIL'} - {work_msg}")
    print(f"Archive Base: {'OK' if archive_valid else 'FAIL'} - {archive_msg}")
