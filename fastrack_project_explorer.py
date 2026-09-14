#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project Tracker

Standalone GUI for managing active projects, clients, and archives.
Provides overview of all projects, search/filter, archive/unarchive, and import functionality.

Can be run standalone or embedded as a tab in FastRack.
"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, simpledialog
import tkinter.font as tkfont
import os
import sys
import threading
from pathlib import Path
import re
import shutil
import json
import webbrowser
from datetime import datetime
from typing import List, Dict, Optional

# Add modules to path (modules folder is a subdirectory)
SCRIPT_DIR = Path(__file__).parent
MODULES_DIR = SCRIPT_DIR / "modules"
sys.path.insert(0, str(MODULES_DIR))

from shared_logging import get_logger, setup_logging
from shared_project_db import ProjectDatabase
from shared_wordpress import is_wordpress_project
from rack_settings import get_rack_settings
from shared_appdata import get_appdata_path
from shared_creator_registry import (
    CREATIVE_CATEGORIES,
    get_subtypes_for_category, get_subtype_display_name,
    has_multiple_subtypes, get_creator_class, is_creative_category
)
from pipeline_categories import (
    category_color, project_type_info, archive_category as archive_category_for,
)
from ui_pipeline_categories import PIPELINE_CATEGORIES
from ui_theme import make_flat_button
from ui_project_deck import ProjectDeckWindow
from invoice_manager.wc_monitor import sanitize_filename
from invoice_manager.order_project_linker import (
    resolve_or_migrate_project_details, resolve_or_fetch_order_details, resolve_or_fetch_invoice,
    project_details_exists, get_website_url, set_website_url,
)

# Module name for logging (must match setup_logging call)
MODULE_NAME = "project_tracker"

# Get logger reference (configured in main())
logger = get_logger(MODULE_NAME)


# Predicate registry for the `applies_when` script field (see
# pipeline_categories.py). Each predicate receives (project_dict, folder_path)
# and returns True iff the corresponding action button should be shown.
def _has_vitepress_dev_script(folder: str) -> bool:
    """True iff <folder>/02_Development/package.json declares a docs:dev
    script and the project isn't a WordPress install. Used to surface
    Dev Server / Build buttons under VitePress-style projects (wiki, hobi)."""
    import json as _json
    if not folder or is_wordpress_project(folder):
        return False
    for sub in ("02_Development", "02_development"):
        pkg = Path(folder) / sub / "package.json"
        if not pkg.is_file():
            continue
        try:
            with open(pkg, "r", encoding="utf-8") as f:
                data = _json.load(f)
            if "docs:dev" in (data.get("scripts") or {}):
                return True
        except Exception:
            continue
    return False


PROJECT_ACTION_PREDICATES = {
    "wordpress": lambda project, folder: is_wordpress_project(folder),
    "vitepress": lambda project, folder: _has_vitepress_dev_script(folder),
    "physical_product": lambda project, folder: (
        project.get("metadata", {}).get("physical_subtype", "") == "Product"
        or bool(project.get("metadata", {}).get("is_product"))
    ),
}


def event_has_shift(event) -> bool:
    """True if a Tk event has the Shift modifier held."""
    return bool(getattr(event, "state", 0) & 0x0001)

def _get_platform_path(windows_path: str) -> Path:
    """
    Convert Windows path to appropriate platform path.
    On WSL/Linux, converts 'D:\\folder' to '/mnt/d/folder'.
    On Windows, returns the path unchanged.
    """
    if sys.platform == "win32":
        return Path(windows_path)

    # WSL/Linux: convert Windows path to /mnt/ path
    # Handle both D:\folder and D:/folder formats
    path_str = windows_path.replace("\\", "/")

    # Check for drive letter pattern (e.g., "D:/", "I:/")
    if len(path_str) >= 2 and path_str[1] == ":":
        drive_letter = path_str[0].lower()
        rest_of_path = path_str[2:]  # Skip "D:"
        if rest_of_path.startswith("/"):
            rest_of_path = rest_of_path[1:]
        return Path(f"/mnt/{drive_letter}/{rest_of_path}")

    return Path(windows_path)


# Category colors, project type info, and archive routing all live in
# pipeline_categories.py. Use category_color()/project_type_info()/
# archive_category_for() instead of inlining constants here.


class TrackerSettings:
    """Manages UI settings persistence for Project Tracker."""

    DEFAULT_SETTINGS = {
        "view_mode": "list",
        "list_scale": 100,
        "grid_scale": 100,
        "filter_statuses": ["active"],
        "file_manager": "",  # Empty = system default, or path like "C:\\Program Files\\fman\\fman.exe"
    }

    def __init__(self):
        self.settings_path = get_appdata_path() / "tracker_settings.json"
        self.settings = self._load()

    def _load(self) -> Dict:
        """Load settings from file or return defaults."""
        try:
            if self.settings_path.exists():
                with open(self.settings_path, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle new settings
                    return {**self.DEFAULT_SETTINGS, **loaded}
        except Exception as e:
            logger.warning(f"Failed to load tracker settings: {e}")
        return self.DEFAULT_SETTINGS.copy()

    def save(self):
        """Save settings to file."""
        try:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.settings_path, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=2)
            logger.debug("Tracker settings saved")
        except Exception as e:
            logger.warning(f"Failed to save tracker settings: {e}")

    def get(self, key: str, default=None):
        """Get a setting value."""
        return self.settings.get(key, default)

    def set(self, key: str, value):
        """Set a setting value and save."""
        self.settings[key] = value
        self.save()


class ArchiveManager:
    """Handles archiving and unarchiving of project folders."""

    @staticmethod
    def _get_archive_dir(project_type: str, is_personal: bool, metadata: Dict = None) -> Path:
        """Get the archive directory for a project type."""
        settings = get_rack_settings()
        archive_category = archive_category_for(project_type) or "Other"
        archive_path_str = settings.get_archive_path(archive_category)
        archive_dir = _get_platform_path(archive_path_str)

        is_sandbox = metadata.get("is_sandbox", False) if metadata else False

        if is_sandbox:
            archive_dir = archive_dir / "_Sandbox"
        elif is_personal:
            archive_dir = archive_dir / "_Personal"
        elif project_type == "Physical" and metadata:
            # Preserve Physical subdirectory (Order, Product, Project)
            subtype = metadata.get("physical_subtype", "")
            if subtype:
                archive_dir = archive_dir / subtype

        return archive_dir

    @staticmethod
    def _get_active_dir(project_type: str, is_personal: bool, metadata: Dict = None) -> Path:
        """Get the active directory for a project type."""
        settings = get_rack_settings()
        archive_category = archive_category_for(project_type) or "Other"
        work_path_str = settings.get_work_path(archive_category)
        active_dir = _get_platform_path(work_path_str)

        is_sandbox = metadata.get("is_sandbox", False) if metadata else False

        if is_sandbox:
            active_dir = active_dir / "_Sandbox"
        elif is_personal:
            active_dir = active_dir / "_Personal"
        elif project_type == "Physical" and metadata:
            # Preserve Physical subdirectory (Order, Product, Project)
            subtype = metadata.get("physical_subtype", "")
            if subtype:
                active_dir = active_dir / subtype

        return active_dir

    @staticmethod
    def archive_project(project: Dict, db: ProjectDatabase) -> bool:
        """
        Archive a project by moving it to the archive directory.

        Args:
            project: Project dictionary
            db: ProjectDatabase instance

        Returns:
            True if successful, False otherwise
        """
        try:
            source_path = Path(project["path"])

            if not source_path.exists():
                messagebox.showerror(
                    "Folder Not Found",
                    f"Project folder does not exist:\n{source_path}"
                )
                return False

            # Determine archive category
            project_type = project.get("project_type", "")

            # Check if this is a personal project
            metadata = project.get("metadata", {})
            is_personal = metadata.get("is_personal", False)

            # Build archive path using RackSettings
            archive_dir = ArchiveManager._get_archive_dir(project_type, is_personal, metadata)
            archive_dir.mkdir(parents=True, exist_ok=True)

            archive_path = archive_dir / source_path.name

            # Check if archive path already exists
            if archive_path.exists():
                response = messagebox.askyesno(
                    "Archive Conflict",
                    f"Archive location already exists:\n{archive_path}\n\n"
                    "Do you want to overwrite it?"
                )
                if not response:
                    return False

                # Remove existing archive folder
                shutil.rmtree(archive_path)

            # Move folder to archive
            logger.info(f"Archiving: {source_path} -> {archive_path}")
            shutil.move(str(source_path), str(archive_path))

            # Update database
            db.archive_project(project["id"], str(archive_path))

            messagebox.showinfo(
                "Success",
                f"Project archived successfully to:\n{archive_path}"
            )

            logger.info(f"Successfully archived project: {project['id']}")
            return True

        except Exception as e:
            logger.error(f"Failed to archive project: {e}")
            messagebox.showerror(
                "Archive Failed",
                f"Failed to archive project:\n{str(e)}"
            )

            # Rollback: try to move folder back if it was moved
            try:
                if 'archive_path' in locals() and archive_path.exists() and not source_path.exists():
                    shutil.move(str(archive_path), str(source_path))
                    logger.info("Rolled back archive operation")
            except Exception as rollback_error:
                logger.error(f"Rollback failed: {rollback_error}")

            return False

    @staticmethod
    def unarchive_project(project: Dict, db: ProjectDatabase) -> bool:
        """
        Unarchive a project by moving it back to the active directory.

        The active path is computed from project type and personal status.

        Args:
            project: Project dictionary
            db: ProjectDatabase instance

        Returns:
            True if successful, False otherwise
        """
        try:
            source_path = Path(project["path"])

            if not source_path.exists():
                messagebox.showerror(
                    "Folder Not Found",
                    f"Archived folder does not exist:\n{source_path}"
                )
                return False

            # Compute the active path based on project type
            project_type = project.get("project_type", "")
            metadata = project.get("metadata", {})
            is_personal = metadata.get("is_personal", False)

            # Get the active directory for this project type
            active_dir = ArchiveManager._get_active_dir(project_type, is_personal, metadata)
            active_path = active_dir / source_path.name

            # Check if active path is already occupied
            if active_path.exists():
                response = messagebox.askyesnocancel(
                    "Path Conflict",
                    f"Active location is occupied:\n{active_path}\n\n"
                    "Yes: Rename and restore\n"
                    "No: Cancel"
                )

                if response is None or not response:
                    return False

                # Find available name with counter
                counter = 1
                base_name = active_path.name
                parent = active_path.parent

                while active_path.exists():
                    new_name = f"{base_name}_restored_{counter}"
                    active_path = parent / new_name
                    counter += 1

                logger.info(f"Using alternate path: {active_path}")

            # Ensure parent directory exists
            active_path.parent.mkdir(parents=True, exist_ok=True)

            # Move folder to active
            logger.info(f"Unarchiving: {source_path} -> {active_path}")
            shutil.move(str(source_path), str(active_path))

            # Update database — status goes back to "active"; the sandbox
            # flag (if any) lives in metadata and is preserved automatically.
            db.unarchive_project(project["id"], str(active_path))

            messagebox.showinfo(
                "Success",
                f"Project restored successfully to:\n{active_path}"
            )

            logger.info(f"Successfully unarchived project: {project['id']}")
            return True

        except Exception as e:
            logger.error(f"Failed to unarchive project: {e}")
            messagebox.showerror(
                "Unarchive Failed",
                f"Failed to unarchive project:\n{str(e)}"
            )

            # Rollback: try to move folder back if it was moved
            try:
                if 'active_path' in locals() and active_path.exists() and not source_path.exists():
                    shutil.move(str(active_path), str(source_path))
                    logger.info("Rolled back unarchive operation")
            except Exception as rollback_error:
                logger.error(f"Rollback failed: {rollback_error}")

            return False


class RenameManager:
    """Renames a project's display name and its on-disk folder.

    WooCommerce Order-type entries are special-cased: DocumentManager's
    folder-matching (wc_monitor.py's _find_by_order_number) only recognizes
    Order folders ending in "_<order_number>", so a renamed Order folder
    must keep that trailing suffix or the next poll would fail to find it
    and create a duplicate. Never overwrites an existing folder — if the
    computed target path already exists, the rename is refused.
    """

    @staticmethod
    def _resolve_folder(project: Dict) -> Path:
        """Return the on-disk folder Path for a project, preferring the
        work-drive path for active projects. Mirrors
        ProjectTrackerApp._resolve_project_folder."""
        stored_path = project.get("path", "")
        status = project.get("status", "active")
        if status != "active":
            return Path(stored_path)
        try:
            settings = get_rack_settings()
            folder = settings.convert_to_work_drive_path(stored_path)
            if not Path(folder).exists():
                folder = stored_path
            return Path(folder)
        except Exception:
            return Path(stored_path)

    @staticmethod
    def is_order_linked(project: Dict) -> Optional[str]:
        """Return the WooCommerce order number if this row's folder must
        keep a trailing _<order_number> suffix on rename, else None."""
        metadata = project.get("metadata", {}) or {}
        if metadata.get("physical_subtype") == "Order" and metadata.get("woo_order_number"):
            return str(metadata["woo_order_number"])
        return None

    @staticmethod
    def build_new_folder_name(project: Dict, new_display_name: str, current_folder_name: str) -> str:
        """Compute the new on-disk folder name for a rename.

        Never guesses: raises ValueError if it can't derive a safe rename
        from the data available, so the caller can surface the problem
        instead of silently producing a wrong or duplicate-prone name.
        """
        safe_name = sanitize_filename(new_display_name).strip()
        if not safe_name:
            raise ValueError("New name is empty after removing invalid characters.")

        order_number = RenameManager.is_order_linked(project)
        if order_number:
            # Rebuild from stable DB fields rather than string-slicing the
            # old "Order_<num>" name, which has no literal trace of a human
            # name in it to anchor a replace against.
            date = project.get("date_created") or datetime.now().strftime("%Y-%m-%d")
            client = project.get("client_name", "")
            parts = [p for p in (date, client, safe_name, order_number) if p]
            return "_".join(parts)

        # Generic case: replace the trailing project-name segment in place,
        # preserving whatever date/prefix/client segments already exist.
        old_display_name = project.get("project_name", "")
        if old_display_name and current_folder_name.endswith(old_display_name):
            prefix = current_folder_name[: -len(old_display_name)]
            return f"{prefix}{safe_name}"

        raise ValueError(
            f"Can't safely compute a new folder name: {old_display_name!r} "
            f"is not the trailing segment of {current_folder_name!r}.\n\n"
            "Rename the folder manually in Explorer, then refresh."
        )

    @staticmethod
    def rename_project(project: Dict, new_display_name: str, db: ProjectDatabase) -> bool:
        """
        Rename a project's folder on disk (if needed) and update the database.

        Args:
            project: Project dictionary
            new_display_name: New human-readable name
            db: ProjectDatabase instance

        Returns:
            True if successful, False otherwise
        """
        try:
            source_path = RenameManager._resolve_folder(project)

            if not source_path.exists():
                messagebox.showerror(
                    "Folder Not Found",
                    f"Project folder does not exist:\n{source_path}"
                )
                return False

            try:
                new_folder_name = RenameManager.build_new_folder_name(
                    project, new_display_name, source_path.name
                )
            except ValueError as e:
                messagebox.showerror("Rename Failed", str(e))
                return False

            if new_folder_name == source_path.name:
                # Only the display name changed — no disk move needed.
                db.rename_project(project["id"], new_display_name)
                logger.info(
                    f"Renamed project {project['id']} (label only, "
                    "folder name unchanged)"
                )
                return True

            target_path = source_path.parent / new_folder_name

            if target_path.exists():
                messagebox.showerror(
                    "Rename Conflict",
                    f"A folder already exists at:\n{target_path}\n\n"
                    "Choose a different name."
                )
                return False

            logger.info(f"Renaming: {source_path} -> {target_path}")
            source_path.rename(target_path)

            db.rename_project(
                project["id"], new_display_name,
                new_path=str(target_path),
                new_base_directory=project.get("base_directory"),
            )

            logger.info(f"Successfully renamed project: {project['id']}")
            return True

        except Exception as e:
            logger.error(f"Failed to rename project: {e}")
            messagebox.showerror("Rename Failed", f"Failed to rename project:\n{str(e)}")
            return False


class ProjectImporter:
    """Handles importing existing projects from filesystem."""

    # Regex patterns for parsing folder names
    PATTERNS = {
        "GD": r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$',
        "FX": r'^(\d{4}-\d{2}-\d{2})_(?:FX|CG)_([^_]+)_(.+)$',
        "Physical": r'^(\d{4}-\d{2}-\d{2})_3DPrint_([^_]+)_(.+)$',
        "Godot": r'^(\d{4}-\d{2}-\d{2})_Godot_([^_]+)_(.+)$',
        "TD": r'^(\d{4}-\d{2}-\d{2})_TD_([^_]+)_(.+)$',
        "Resolume": r'^(\d{4}-\d{2}-\d{2})_RS_([^_]+)_(.+)$',
        "Audio": r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$',
        "Photo": r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$'
    }

    @classmethod
    def _build_scan_directories(cls):
        """Build scan directories from settings."""
        settings = get_rack_settings()
        active_base = settings.get_active_base()
        archive_base = settings.get_archive_base()

        active = {
            # Visual
            "Visual": _get_platform_path(active_base + r"\Visual"),
            "Visual_Personal": _get_platform_path(active_base + r"\Visual\_Personal"),
            "Visual_Sandbox": _get_platform_path(active_base + r"\Visual\_Sandbox"),
            # Audio
            "Audio_InProgress": _get_platform_path(active_base + r"\Audio\01_Prod\01_InProgress"),
            "Audio_Finished": _get_platform_path(active_base + r"\Audio\01_Prod\03_Finished"),
            "Audio_Personal": _get_platform_path(active_base + r"\Audio\_Personal"),
            # Physical (scan each subdirectory separately)
            "Physical_Order": _get_platform_path(active_base + r"\Physical\Order"),
            "Physical_Product": _get_platform_path(active_base + r"\Physical\Product"),
            "Physical_Project": _get_platform_path(active_base + r"\Physical\Project"),
            "Physical_Personal": _get_platform_path(active_base + r"\Physical\_Personal"),
            # RealTime
            "RealTime": _get_platform_path(active_base + r"\RealTIme"),  # Note: folder has typo "RealTIme"
            "RealTime_Personal": _get_platform_path(active_base + r"\RealTIme\_Personal"),
            "RealTime_Sandbox": _get_platform_path(active_base + r"\RealTIme\_Sandbox"),
            # Photo
            "Photo": _get_platform_path(active_base + r"\Photo"),
            "Photo_Personal": _get_platform_path(active_base + r"\Photo\_Personal"),
            "Photo_Sandbox": _get_platform_path(active_base + r"\Photo\_Sandbox"),
            # Web
            "Web": _get_platform_path(active_base + r"\Web"),
            "Web_Personal": _get_platform_path(active_base + r"\Web\_Personal"),
        }

        archive = {
            # Visual
            "Visual": _get_platform_path(archive_base + r"\Visual"),
            "Visual_Personal": _get_platform_path(archive_base + r"\Visual\_Personal"),
            "Visual_Sandbox": _get_platform_path(archive_base + r"\Visual\_Sandbox"),
            # Audio
            "Audio_Personal": _get_platform_path(archive_base + r"\Audio\_Personal"),
            # Physical (mirror active subdirectory structure)
            "Physical_Order": _get_platform_path(archive_base + r"\Physical\Order"),
            "Physical_Product": _get_platform_path(archive_base + r"\Physical\Product"),
            "Physical_Project": _get_platform_path(archive_base + r"\Physical\Project"),
            "Physical_Personal": _get_platform_path(archive_base + r"\Physical\_Personal"),
            # RealTime
            "RealTime": _get_platform_path(archive_base + r"\RealTime"),
            "RealTime_Personal": _get_platform_path(archive_base + r"\RealTime\_Personal"),
            "RealTime_Sandbox": _get_platform_path(archive_base + r"\RealTime\_Sandbox"),
            # Photo
            "Photo": _get_platform_path(archive_base + r"\Photo"),
            "Photo_Personal": _get_platform_path(archive_base + r"\Photo\_Personal"),
            "Photo_Sandbox": _get_platform_path(archive_base + r"\Photo\_Sandbox"),
            # Web
            "Web": _get_platform_path(archive_base + r"\Web"),
            "Web_Personal": _get_platform_path(archive_base + r"\Web\_Personal"),
        }

        return active, archive

    @classmethod
    def scan_and_import(cls, db: ProjectDatabase, status_callback=None) -> Dict:
        """
        Scan filesystem and import existing projects from both active and archive.

        Args:
            db: ProjectDatabase instance
            status_callback: Optional callback function for status updates

        Returns:
            Dictionary with import statistics
        """
        stats = {"scanned": 0, "imported": 0, "skipped": 0, "errors": 0}

        # Get path config for converting active paths to work drive
        try:
            settings = get_rack_settings()
        except Exception:
            settings = None

        # First, collect all folders to import
        folders_to_process = []

        # Build scan directories from settings
        scan_dirs_active, scan_dirs_archive = cls._build_scan_directories()

        # Scan active directories
        for category, base_dir in scan_dirs_active.items():
            if not base_dir.exists():
                continue

            is_sandbox_dir = "_Sandbox" in category

            if status_callback:
                label = "sandbox" if is_sandbox_dir else "active"
                status_callback(f"Scanning {label} {category}...")

            try:
                for item in base_dir.iterdir():
                    if not item.is_dir():
                        continue
                    if item.name.startswith('_') or item.name.startswith('.'):
                        continue

                    parsed = cls._parse_folder_name(item.name, category)
                    if parsed:
                        # Convert to work drive path for active projects
                        stored_path = str(item)
                        stored_base = str(base_dir)
                        if settings:
                            stored_path = settings.convert_to_work_drive_path(stored_path)
                            stored_base = settings.convert_to_work_drive_path(stored_base)

                        folders_to_process.append({
                            "path": stored_path,
                            "base_dir": stored_base,
                            "scanned_path": str(item),  # Keep original for duplicate check
                            "parsed": parsed,
                            "status": "active",
                            "is_sandbox_origin": is_sandbox_dir,
                        })
            except Exception:
                pass

        # Scan archive directories
        for category, base_dir in scan_dirs_archive.items():
            if not base_dir.exists():
                continue

            if status_callback:
                status_callback(f"Scanning archive {category}...")

            try:
                for item in base_dir.iterdir():
                    if not item.is_dir():
                        continue
                    if item.name.startswith('_') or item.name.startswith('.'):
                        continue

                    parsed = cls._parse_folder_name(item.name, category)
                    if parsed:
                        folders_to_process.append({
                            "path": str(item),
                            "base_dir": str(base_dir),
                            "parsed": parsed,
                            "status": "archived",
                            "is_sandbox_origin": "_Sandbox" in category
                        })
            except Exception:
                pass

        # Now process collected folders
        if status_callback:
            status_callback(f"Importing {len(folders_to_process)} projects...")

        for folder_info in folders_to_process:
            stats["scanned"] += 1

            try:
                # Check if already exists (check both stored path and original scanned path)
                stored_path = folder_info["path"]
                scanned_path = folder_info.get("scanned_path", stored_path)

                if db.get_project_by_path(stored_path) or db.get_project_by_path(scanned_path):
                    stats["skipped"] += 1
                    continue

                # Register new project (don't save yet - batch at end)
                parsed = folder_info["parsed"]
                db.register_project({
                    "client_name": parsed["client"],
                    "project_name": parsed["project"],
                    "project_type": parsed["type"],
                    "date_created": parsed["date"],
                    "path": stored_path,
                    "base_directory": folder_info["base_dir"],
                    "status": folder_info.get("status", "active"),
                    "notes": "",
                    "metadata": {
                        "is_personal": parsed.get("is_personal", False),
                        "is_sandbox": folder_info.get("is_sandbox_origin", False),
                        "location": parsed.get("location", ""),
                        "physical_subtype": parsed.get("physical_subtype", ""),
                        "woo_order_number": parsed.get("woo_order_number", ""),
                    }
                }, auto_save=False)
                stats["imported"] += 1

            except Exception:
                stats["errors"] += 1

        # Save once at the end
        if stats["imported"] > 0:
            db.save()

        return stats

    @classmethod
    def _parse_folder_name(cls, folder_name: str, category: str) -> Optional[Dict]:
        """
        Parse folder name to extract project information.

        Args:
            folder_name: Folder name to parse
            category: Category from SCAN_DIRECTORIES

        Returns:
            Dictionary with date, client, project, type or None if not parseable
        """
        # Check if this is a personal or sandbox project
        is_personal = "_Personal" in category
        is_sandbox = "_Sandbox" in category

        # Extract physical subtype (Order, Product, Project) if present
        physical_subtype = ""
        if category.startswith("Physical_") and not is_personal:
            physical_subtype = category.split("_", 1)[1]  # "Order", "Product", "Project"

        base_category = category.replace("_Personal", "").replace("_Sandbox", "")
        # Normalize Physical subtypes back to "Physical" for pattern matching
        if base_category.startswith("Physical"):
            base_category = "Physical"

        # Visual: Try VJ pattern first, then VFX (CG_), then GD pattern
        if base_category == "Visual":
            # VJ pattern with client: YYYY-MM-DD_VJ_Client_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_VJ_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "VJ",
                    "is_personal": is_personal
                }
            # VJ pattern without client: YYYY-MM-DD_VJ_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_VJ_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "VJ",
                    "is_personal": True
                }
            # FX pattern: YYYY-MM-DD_FX_Client_Project (also matches legacy _CG_)
            match = re.match(cls.PATTERNS["FX"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "FX",
                    "is_personal": is_personal
                }
            # FX/CG pattern without client: YYYY-MM-DD_FX_Project (for personal projects)
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(?:FX|CG)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "FX",
                    "is_personal": True
                }
            # PG pattern with client: YYYY-MM-DD_PG_Client_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_PG_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "PG",
                    "is_personal": is_personal
                }
            # PG pattern without client: YYYY-MM-DD_PG_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_PG_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "PG",
                    "is_personal": True
                }
            # GD pattern with explicit prefix and client: YYYY-MM-DD_GD_Client_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_GD_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "GD",
                    "is_personal": is_personal
                }
            # GD pattern with explicit prefix, no client: YYYY-MM-DD_GD_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_GD_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "GD",
                    "is_personal": True
                }
            # GD pattern (legacy, no type prefix): YYYY-MM-DD_Client_Project
            match = re.match(cls.PATTERNS["GD"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "GD",
                    "is_personal": is_personal
                }

        # Physical: 3DPrint, Order, or Technical pattern
        elif base_category == "Physical":
            # New WC order pattern: YYYY-MM-DD_ClientCamel_OrderNumber
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_([A-Za-z][A-Za-z0-9]*)_(\d+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": match.group(2),
                    "project": f"Order_{match.group(3)}",
                    "type": "Physical",
                    "is_personal": False,
                    "physical_subtype": physical_subtype or "Order",
                    "woo_order_number": match.group(3),
                }
            # Renamed WC order pattern (via the app's Rename action):
            # YYYY-MM-DD_ClientCamel_FriendlyName_OrderNumber. Must come
            # after the strict 3-segment pattern above so unrenamed
            # canonical folders keep matching that one first.
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_([A-Za-z][A-Za-z0-9]*)_(.+)_(\d+)$', folder_name)
            if match and physical_subtype in ("Order", ""):
                return {
                    "date": match.group(1),
                    "client": match.group(2),
                    "project": match.group(3),
                    "type": "Physical",
                    "is_personal": False,
                    "physical_subtype": physical_subtype or "Order",
                    "woo_order_number": match.group(4),
                }
            # Legacy WC order pattern: Order_{order_number}_{customer_name}
            match = re.match(r'^Order_(\d+)_(.+)$', folder_name)
            if match:
                return {
                    "date": "",
                    "client": match.group(2),
                    "project": f"Order_{match.group(1)}",
                    "type": "Physical",
                    "is_personal": False,
                    "physical_subtype": physical_subtype or "Order",
                    "woo_order_number": match.group(1),
                }
            # 3DPrint pattern with client: YYYY-MM-DD_3DPrint_Client_Project
            match = re.match(cls.PATTERNS["Physical"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "Physical",
                    "is_personal": is_personal,
                    "physical_subtype": physical_subtype
                }
            # 3DPrint pattern without client: YYYY-MM-DD_3DPrint_Project
            # Product folders use this format with "alles3d" as implicit client
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_3DPrint_(.+)$', folder_name)
            if match:
                if physical_subtype == "Product":
                    return {
                        "date": match.group(1),
                        "client": "alles3d",
                        "project": match.group(2),
                        "type": "Physical",
                        "is_personal": False,
                        "physical_subtype": "Product"
                    }
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "Physical",
                    "is_personal": True,
                    "physical_subtype": physical_subtype
                }
            # Architecture pattern: YYYY-MM-DD_Arch_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_Arch_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "Physical",
                    "is_personal": True,
                    "physical_subtype": physical_subtype
                }
            # Technical pattern (older archive format)
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_Technical_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "Physical",
                    "is_personal": True,
                    "physical_subtype": physical_subtype
                }

        # RealTime: Try various patterns
        elif base_category == "RealTime":
            # Full FX/CG pattern: YYYY-MM-DD_FX_Client_Project (also matches legacy _CG_)
            match = re.match(cls.PATTERNS["FX"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "RealTime",
                    "is_personal": is_personal
                }
            # Simple FX/CG pattern: YYYY-MM-DD_FX_ProjectName (no client)
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(?:FX|CG)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "RealTime",
                    "is_personal": True
                }
            # Godot_ pattern
            match = re.match(cls.PATTERNS["Godot"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "Godot",
                    "is_personal": is_personal
                }
            # TD_ pattern
            match = re.match(cls.PATTERNS["TD"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "TD",
                    "is_personal": is_personal
                }
            # RS_ (Resolume) pattern
            match = re.match(cls.PATTERNS["Resolume"], folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "Resolume",
                    "is_personal": is_personal
                }
            # Simple: YYYY-MM-DD_ProjectName (no client)
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal",
                    "project": match.group(2),
                    "type": "RealTime",
                    "is_personal": True
                }

        # Audio: Date_Client_Project or Date_ProjectName
        elif base_category.startswith("Audio"):
            # Try with client first: YYYY-MM-DD_Client_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "Audio",
                    "is_personal": is_personal
                }
            # Simple: YYYY-MM-DD_ProjectName
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else "Audio",
                    "project": match.group(2),
                    "type": "Audio",
                    "is_personal": is_personal
                }

        # Photo: Date_Location_Activity
        elif base_category == "Photo":
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else "Photo",
                    "project": match.group(3),
                    "location": match.group(2),
                    "type": "Photo",
                    "is_personal": is_personal
                }
            # Fallback: no location separator found
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else "Photo",
                    "project": match.group(2),
                    "location": "",
                    "type": "Photo",
                    "is_personal": is_personal
                }

        # Web: Date_Client_Project (client) or just ProjectName (personal)
        elif base_category == "Web":
            # Client project: YYYY-MM-DD_Client_Project
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_([^_]+)_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else match.group(2),
                    "project": match.group(3),
                    "type": "Web",
                    "is_personal": is_personal
                }
            # Date_Project (no client)
            match = re.match(r'^(\d{4}-\d{2}-\d{2})_(.+)$', folder_name)
            if match:
                return {
                    "date": match.group(1),
                    "client": "Personal" if is_personal else "Web",
                    "project": match.group(2),
                    "type": "Web",
                    "is_personal": is_personal
                }
            # No date - personal project (just folder name)
            if is_personal:
                from datetime import date
                return {
                    "date": date.today().isoformat(),
                    "client": "Personal",
                    "project": folder_name,
                    "type": "Web",
                    "is_personal": True
                }

        return None


class ProjectTrackerApp:
    """
    Project Tracker application.

    Can run as standalone window or be embedded in a parent frame.
    Use embedded=True when integrating into another application (like FastRack).
    """

    def __init__(self, root_or_frame, embedded=False, status_callback=None, hint_callback=None, creation_start_callback=None, creation_done_callback=None, creation_cancel_callback=None, session=None):
        """
        Initialize the Project Tracker.

        Args:
            root_or_frame: Either a Tk root window (standalone) or a Frame (embedded)
            embedded: If True, skip window configuration and header
            status_callback: Optional callback function for status messages (message, status_type)
            hint_callback: Optional callback function for showing keyboard hints (hint_text)
            creation_start_callback: Optional callback when project creation starts (FAB clicked)
            creation_done_callback: Optional callback when project creation completes successfully
            creation_cancel_callback: Optional callback when project creation is cancelled
        """
        self.embedded = embedded
        self.status_callback = status_callback
        self.hint_callback = hint_callback
        self.creation_start_callback = creation_start_callback
        self.creation_done_callback = creation_done_callback
        self.creation_cancel_callback = creation_cancel_callback
        # Shared session (supplied by the hub in embedded mode; owns the
        # authoritative scope filter). When None, the tracker falls back to
        # its own persisted settings for scopes.
        self.session = session

        if embedded:
            # Embedded mode: root_or_frame is the parent frame
            self.root = root_or_frame.winfo_toplevel()
            self.parent = root_or_frame
        else:
            # Standalone mode: root_or_frame is the Tk root
            self.root = root_or_frame
            self.parent = root_or_frame
            self.root.title("Project Tracker")
            self.root.geometry("1000x700")
            self.root.minsize(800, 600)

        # Initialize database
        self.db = ProjectDatabase()

        # Initialize settings
        self.settings = TrackerSettings()

        # Current selection
        self.selected_project = None
        # Selected categories (set). Empty = no category filter (show all).
        self.selected_categories = set()
        self.tree_item_to_project = {}  # Map tree items to project data
        self._project_decks: Dict[str, "ProjectDeckWindow"] = {}  # Open decks, keyed by project id

        # Path tracking for clipboard copy
        self._current_active_path = None
        self._current_raw_path = None

        # Column sorting and ordering (load from settings)
        self.sort_column = self.settings.get("sort_column", "date")
        self.sort_reverse = self.settings.get("sort_reverse", True)  # True = descending (newest first)
        self.column_order = self.settings.get("column_order", ["date", "client", "project"])

        # Column drag state
        self._drag_column = None
        self._drag_start_x = None

        # Grid view selection tracking
        self.grid_selected_index = -1  # Last selected (for details panel + keyboard nav anchor)
        self.grid_selected_indices = set()  # All selected indices (for multi-select)
        self.grid_cards = []  # List of card frames for keyboard navigation
        self.grid_projects = []  # List of projects in grid order
        self.grid_cols = 1  # Current number of columns

        # Filter status (load from settings)
        # Status filter toggles — multiple can be active at once
        saved_statuses = self.settings.get("filter_statuses", None)
        # Backwards compat: old single "filter_status" setting
        if saved_statuses is None:
            old = self.settings.get("filter_status", "active")
            saved_statuses = ["active", "sandbox", "archived"] if old == "all" else [old]
        self.filter_toggles = {
            "active": tk.BooleanVar(value="active" in saved_statuses),
            "sandbox": tk.BooleanVar(value="sandbox" in saved_statuses),
            "archived": tk.BooleanVar(value="archived" in saved_statuses),
        }

        # Scope filter — set of enabled scopes (subset of {"personal", "client"}).
        # Empty set is allowed and means "show nothing for scope". Session
        # wins when present; otherwise load from the tracker's own settings.
        if self.session is not None:
            self.filter_scopes = set(self.session.scopes)
            self.session.add_listener(self._on_session_change)
        else:
            saved_scopes = self.settings.get("filter_scopes", None)
            if saved_scopes is None:
                # Backward compat with old single "filter_scope" string setting.
                old = self.settings.get("filter_scope", "all")
                saved_scopes = ["personal", "client"] if old == "all" else [old]
            self.filter_scopes = {s for s in saved_scopes if s in ("personal", "client")}
        self.scope_buttons = {}

        # Search query
        self.search_query = tk.StringVar()
        self.search_query.trace_add('write', self._on_search_changed)

        # Category buttons storage
        self.category_buttons = {}

        # Creation panel state management
        self.view_state = "PROJECT_LIST"  # "PROJECT_LIST", "SUBTYPE_SELECTION", "CREATION_FORM"
        self.creation_panel = None  # Frame for creation panel
        self.active_creator = None  # Current embedded creator instance
        self.fab_button = None  # Floating action button

        # Subtype selection state (for keyboard navigation)
        self.subtype_buttons = []
        self.subtype_selected_index = 0
        self.subtype_category = None

        # Build UI
        self._build_ui()

        # Apply saved settings after UI is built
        self._apply_saved_settings()

        # Load projects
        self.refresh_project_list()

        logger.info("Project Tracker initialized")

    def _build_ui(self):
        """Build the user interface."""
        if not self.embedded:
            # Header (only for standalone mode)
            self._build_header()

        # Main content area
        main_frame = tk.Frame(self.parent, bg="#0d1117", highlightthickness=0, bd=0)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Left panel (project list)
        self._build_left_panel(main_frame)

        # Right panel (details)
        self._build_right_panel(main_frame)

    def _build_header(self):
        """Build header section."""
        header_frame = tk.Frame(self.root, bg="#2c3e50", height=60)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        title_label = tk.Label(
            header_frame,
            text="📊 Project Tracker",
            font=("Arial", 16, "bold"),
            fg="white",
            bg="#2c3e50"
        )
        title_label.pack(side=tk.LEFT, padx=20, pady=15)

    def _build_left_panel(self, parent):
        """Build left panel with category buttons (standalone mode only)."""
        # When embedded, skip entirely - categories handled by main pipeline UI
        if self.embedded:
            return

        # Full left panel with category buttons (standalone mode)
        left_frame = tk.Frame(parent, width=200, bg="#0d1117")
        left_frame.pack(side=tk.LEFT, fill=tk.Y, pady=5, padx=(5, 0))
        left_frame.pack_propagate(False)

        # Title - clickable to show all projects
        title_label = tk.Label(left_frame, text="Categories", bg="#0d1117", fg="white",
                              font=("Arial", 12, "bold"), cursor="hand2")
        title_label.pack(pady=(10, 15), padx=10)
        title_label.bind('<Button-1>', lambda e: self._clear_category_selection())

        # Category buttons frame
        buttons_frame = tk.Frame(left_frame, bg="#0d1117")
        buttons_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        from pipeline_categories import creative_categories, category_emoji
        categories = [
            (name, category_emoji(name), category_color(name))
            for name in creative_categories()
        ]

        # Create category buttons in grid (2 columns)
        for idx, (name, icon, color) in enumerate(categories):
            row = idx // 2
            col = idx % 2

            # Use highlightthickness for selection border (doesn't shift layout)
            btn_frame = tk.Frame(buttons_frame, bg=color, relief=tk.FLAT, cursor="hand2",
                                highlightthickness=3, highlightbackground="#0d1117")
            btn_frame.grid(row=row, column=col, padx=2, pady=5, sticky="nsew")

            # Store reference
            self.category_buttons[name] = {"frame": btn_frame, "color": color}

            # Icon and name
            icon_label = tk.Label(btn_frame, text=icon, bg=color, fg="white", font=("Arial", 24))
            icon_label.pack(pady=(10, 2))

            name_label = tk.Label(btn_frame, text=name, bg=color, fg="white", font=("Arial", 9, "bold"))
            name_label.pack(pady=(0, 2))

            # Count label
            count_label = tk.Label(btn_frame, text="(0)", bg=color, fg="white", font=("Arial", 8))
            count_label.pack(pady=(0, 10))

            # Store count label
            self.category_buttons[name]["count_label"] = count_label

            # Click handler — Shift+click toggles in selection (multi-select).
            def make_click_handler(cat_name):
                return lambda e: self._select_category(
                    cat_name, additive=event_has_shift(e)
                )

            click_handler = make_click_handler(name)

            for widget in (btn_frame, icon_label, name_label, count_label):
                widget.bind('<Button-1>', click_handler)

        # Configure grid weights
        buttons_frame.columnconfigure(0, weight=1)
        buttons_frame.columnconfigure(1, weight=1)

        # Spacer
        tk.Frame(left_frame, bg="#0d1117", height=20).pack()

        # Bottom buttons
        import_btn = make_flat_button(
            left_frame, "📥 Import", self._import_projects,
            bg="#238636", fg="white", font=("Arial", 9, "bold"), pady=8,
        )
        import_btn.pack(fill=tk.X, padx=10, pady=5)

        refresh_btn = make_flat_button(
            left_frame, "🔄 Refresh", self.refresh_project_list,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            pady=6,
        )
        refresh_btn.pack(fill=tk.X, padx=10, pady=5)

    def _build_right_panel(self, parent):
        """Build right panel with project list and details."""
        right_frame = tk.Frame(parent, bg="#0d1117", highlightthickness=0, bd=0)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=(0, 5), pady=5)

        # Top: Search and filter
        controls_frame = tk.Frame(right_frame, bg="#0d1117")
        controls_frame.pack(fill=tk.X, padx=10, pady=10)

        # Search
        search_label = tk.Label(controls_frame, text="Search:", bg="#0d1117", fg="white", font=("Arial", 9))
        search_label.grid(row=0, column=0, sticky=tk.W, pady=5)

        self.search_entry = tk.Entry(controls_frame, textvariable=self.search_query, bg="#1c2128", fg="white",
                               insertbackground="white", font=("Arial", 10), width=30)
        self.search_entry.grid(row=0, column=1, sticky=tk.W, padx=(5, 15), pady=5)

        # Search hint on hover
        def on_search_enter(e):
            if self.hint_callback:
                self.hint_callback("Shortcut: Ctrl+F or /")

        def on_search_leave(e):
            if self.hint_callback:
                self.hint_callback("")

        search_label.bind("<Enter>", on_search_enter)
        search_label.bind("<Leave>", on_search_leave)
        self.search_entry.bind("<Enter>", on_search_enter)
        self.search_entry.bind("<Leave>", on_search_leave)

        # Scope toggle buttons (Personal / Work) — moved from the hub so the
        # project-filtering controls live next to the projects they affect.
        scope_frame = tk.Frame(controls_frame, bg="#0d1117")
        scope_frame.grid(row=0, column=2, sticky=tk.W, padx=(15, 0), pady=5)

        for idx, (value, text, shortcut) in enumerate(
            [("personal", "Personal", "1"), ("client", "Work", "2")]
        ):
            btn = tk.Label(
                scope_frame,
                text=text,
                font=("Arial", 9),
                fg="white",
                bg="#1c2128",
                padx=12,
                pady=4,
                cursor="hand2",
            )
            btn.pack(side=tk.LEFT, padx=(0, 2))

            def make_click(v):
                def on_click(e):
                    # Shift+click: toggle (multi-select).
                    # Plain click: select only this scope.
                    if self.session is not None:
                        self.session.toggle_scope(v, additive=event_has_shift(e))
                    else:
                        if event_has_shift(e):
                            new = set(self.filter_scopes)
                            if v in new:
                                new.discard(v)
                            else:
                                new.add(v)
                        else:
                            new = {v}
                        self.set_scopes(new)
                return on_click

            def make_enter(v, b, s):
                def on_enter(e):
                    if v not in self.filter_scopes:
                        b.configure(bg="#2d333b")
                    if self.hint_callback:
                        self.hint_callback(f"Shortcut: {s}")
                return on_enter

            def make_leave(v, b):
                def on_leave(e):
                    if v not in self.filter_scopes:
                        b.configure(bg="#1c2128")
                    if self.hint_callback:
                        self.hint_callback("")
                return on_leave

            btn.bind("<Button-1>", make_click(value))
            btn.bind("<Enter>", make_enter(value, btn, shortcut))
            btn.bind("<Leave>", make_leave(value, btn))

            self.scope_buttons[value] = btn

        self._update_scope_button_styles()

        # Filter buttons frame. Active/Archive are status toggles; Sandbox is
        # an orthogonal flag, so it sits in its own group separated by a thin
        # vertical divider to make the distinction visible at a glance.
        filter_frame = tk.Frame(controls_frame, bg="#0d1117")
        filter_frame.grid(row=0, column=3, sticky=tk.W, padx=(15, 0), pady=5)

        # Status group (Active, Archive)
        status_group = tk.Frame(filter_frame, bg="#0d1117")
        status_group.pack(side=tk.LEFT)

        # Store filter buttons for styling updates
        self.filter_buttons = {}

        # Sandbox uses an amber accent when selected to reinforce that it's
        # a distinct flag, not a status — matches the list-view sandbox tag.
        # Amber instead of purple so it doesn't clash with the Audio category
        # color.
        SANDBOX_SELECTED_BG = "#b45309"
        SANDBOX_SELECTED_FG = "#ffffff"
        SANDBOX_BASE_BG = "#1c2128"
        SANDBOX_HOVER_BG = "#3a2c14"

        def create_filter_toggle(parent, text, value, shortcut):
            """Create a filter toggle button (can be independently on/off)."""
            is_sandbox_btn = value == "sandbox"
            btn = tk.Label(
                parent,
                text=text,
                font=("Arial", 9),
                fg="white",
                bg=SANDBOX_BASE_BG,
                padx=12,
                pady=4,
                cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=(0, 2))

            def on_click(e):
                # Sandbox is an extra modifier on top of the Active/Archive
                # status toggles, so it's always additive — clicking it just
                # flips its own state and leaves the rest alone. Shift+click
                # on any toggle is also additive (multi-select). A plain click
                # on Active/Archive collapses the *status* selection to that
                # one, but preserves Sandbox.
                if is_sandbox_btn or event_has_shift(e):
                    var = self.filter_toggles[value]
                    var.set(not var.get())
                else:
                    for k, v in self.filter_toggles.items():
                        if k == "sandbox":
                            continue
                        v.set(k == value)
                self._on_filter_changed()

            def on_enter(e):
                if not self.filter_toggles[value].get():
                    btn.configure(bg=SANDBOX_HOVER_BG if is_sandbox_btn else "#2d333b")
                # Show shortcut hint
                if self.hint_callback:
                    self.hint_callback(f"Toggle: {shortcut}")

            def on_leave(e):
                if not self.filter_toggles[value].get():
                    btn.configure(bg=SANDBOX_BASE_BG)
                # Clear shortcut hint
                if self.hint_callback:
                    self.hint_callback("")

            btn.bind("<Button-1>", on_click)
            btn.bind("<Enter>", on_enter)
            btn.bind("<Leave>", on_leave)

            self.filter_buttons[value] = btn
            return btn

        create_filter_toggle(status_group, "Active", "active", "4")
        create_filter_toggle(status_group, "Archive", "archived", "6")

        # Sandbox flag toggle — same row as the status toggles, with a small
        # gap (matching the gap between Work and Active) to visually group it
        # apart without a label or divider.
        tk.Frame(filter_frame, bg="#0d1117", width=15).pack(side=tk.LEFT)
        create_filter_toggle(filter_frame, "Sandbox", "sandbox", "5")

        # Update initial button styling
        self._update_filter_button_styles()

        # Project count
        self.count_label = tk.Label(controls_frame, text="Projects (0)", bg="#0d1117", fg="#8b949e",
                                    font=("Arial", 9))
        self.count_label.grid(row=0, column=4, sticky=tk.E, padx=(15, 0), pady=5)

        controls_frame.columnconfigure(4, weight=1)

        # Physical subtype filter pills (row 2, hidden by default)
        self.physical_subtype_frame = tk.Frame(controls_frame, bg="#0d1117")
        self.filter_physical_subtype = tk.StringVar(value="all")
        self.physical_subtype_buttons = {}

        subtype_label = tk.Label(
            self.physical_subtype_frame, text="Type:", bg="#0d1117", fg="#8b949e",
            font=("Arial", 9)
        )
        subtype_label.pack(side=tk.LEFT, padx=(0, 5))

        for value, text in [("all", "All"), ("Order", "Order"), ("Product", "Product"), ("Project", "Project")]:
            btn = tk.Label(
                self.physical_subtype_frame,
                text=text,
                font=("Arial", 9),
                fg="white",
                bg="#1c2128",
                padx=10,
                pady=3,
                cursor="hand2"
            )
            btn.pack(side=tk.LEFT, padx=(0, 2))

            def make_click(v):
                def on_click(e):
                    self.filter_physical_subtype.set(v)
                    self._update_physical_subtype_styles()
                    self.refresh_project_list()
                return on_click

            def make_enter(v, b):
                def on_enter(e):
                    if self.filter_physical_subtype.get() != v:
                        b.configure(bg="#2d333b")
                return on_enter

            def make_leave(v, b):
                def on_leave(e):
                    if self.filter_physical_subtype.get() != v:
                        b.configure(bg="#1c2128")
                return on_leave

            btn.bind("<Button-1>", make_click(value))
            btn.bind("<Enter>", make_enter(value, btn))
            btn.bind("<Leave>", make_leave(value, btn))
            self.physical_subtype_buttons[value] = btn

        self._update_physical_subtype_styles()

        # Middle: Project list header with view toggle
        list_header = tk.Frame(right_frame, bg="#0d1117")
        list_header.pack(fill=tk.X, padx=10, pady=(5, 5))

        list_label = tk.Label(list_header, text="Projects", bg="#0d1117", fg="white",
                            font=("Arial", 10, "bold"))
        list_label.pack(side=tk.LEFT)

        # View toggle buttons
        self.view_mode = tk.StringVar(value="list")

        # Toggle uses accent blue (#58a6ff) as the selected background so the
        # active view is unmistakable; unselected sits on a slightly raised
        # surface (#1c2128) so both buttons still read as clickable.
        grid_btn = tk.Radiobutton(list_header, text="⊞", variable=self.view_mode, value="grid",
                                  bg="#1c2128", fg="#8b949e", selectcolor="#58a6ff",
                                  activebackground="#2d333b", activeforeground="white",
                                  indicatoron=False, padx=10, pady=3, font=("Arial", 12, "bold"),
                                  borderwidth=0, highlightthickness=0,
                                  command=self._switch_view)
        grid_btn.pack(side=tk.RIGHT, padx=2)

        list_btn = tk.Radiobutton(list_header, text="≡", variable=self.view_mode, value="list",
                                  bg="#1c2128", fg="#8b949e", selectcolor="#58a6ff",
                                  activebackground="#2d333b", activeforeground="white",
                                  indicatoron=False, padx=10, pady=3, font=("Arial", 12, "bold"),
                                  borderwidth=0, highlightthickness=0,
                                  command=self._switch_view)
        list_btn.pack(side=tk.RIGHT, padx=2)

        # Track the buttons so _switch_view can refresh fg colour on the
        # selected one (Radiobutton's selectcolor only affects bg).
        self._view_mode_buttons = {"grid": grid_btn, "list": list_btn}

        def _view_btn_hint(text):
            def on_enter(e):
                if self.hint_callback:
                    self.hint_callback(text)
            def on_leave(e):
                if self.hint_callback:
                    self.hint_callback("")
            return on_enter, on_leave

        grid_enter, grid_leave = _view_btn_hint("Grid View: T")
        grid_btn.bind("<Enter>", grid_enter, add="+")
        grid_btn.bind("<Leave>", grid_leave, add="+")

        list_enter, list_leave = _view_btn_hint("List View: T")
        list_btn.bind("<Enter>", list_enter, add="+")
        list_btn.bind("<Leave>", list_leave, add="+")

        # Separate scale sliders for list and grid views
        self.list_scale_value = tk.IntVar(value=100)
        self.grid_scale_value = tk.IntVar(value=100)

        # List scale slider (shown when list view active)
        self.list_scale_frame = tk.Frame(list_header, bg="#0d1117")
        list_scale_label = tk.Label(self.list_scale_frame, text="Size:", bg="#0d1117", fg="#8b949e",
                                   font=("Arial", 8))
        list_scale_label.pack(side=tk.LEFT, padx=(0, 2))
        self.list_scale_slider = tk.Scale(self.list_scale_frame, from_=50, to=150, orient=tk.HORIZONTAL,
                                         variable=self.list_scale_value, command=self._on_list_scale_changed,
                                         bg="#0d1117", fg="white", highlightthickness=0,
                                         troughcolor="#1c2128", activebackground="#58a6ff",
                                         length=80, showvalue=False, sliderlength=15)
        self.list_scale_slider.pack(side=tk.LEFT)
        self.list_scale_frame.pack(side=tk.RIGHT, padx=(10, 2))

        # Grid scale slider (shown when grid view active)
        self.grid_scale_frame = tk.Frame(list_header, bg="#0d1117")
        grid_scale_label = tk.Label(self.grid_scale_frame, text="Size:", bg="#0d1117", fg="#8b949e",
                                   font=("Arial", 8))
        grid_scale_label.pack(side=tk.LEFT, padx=(0, 2))
        self.grid_scale_slider = tk.Scale(self.grid_scale_frame, from_=100, to=225, orient=tk.HORIZONTAL,
                                         variable=self.grid_scale_value, command=self._on_grid_scale_changed,
                                         bg="#0d1117", fg="white", highlightthickness=0,
                                         troughcolor="#1c2128", activebackground="#58a6ff",
                                         length=80, showvalue=False, sliderlength=15)
        self.grid_scale_slider.pack(side=tk.LEFT)
        # Grid slider hidden by default (list view is default)

        # Container for both views
        self.view_container = tk.Frame(right_frame, bg="#0d1117", highlightthickness=0, bd=0)
        self.view_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        # === LIST VIEW ===
        self.list_frame = tk.Frame(self.view_container, bg="#0d1117", highlightthickness=0, bd=0)

        # Wrapper frame to hide any white edges from treeview
        tree_wrapper = tk.Frame(self.list_frame, bg="#0d1117", highlightthickness=0, bd=0)
        tree_wrapper.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(tree_wrapper)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview styling - dark theme to match container
        self.tree_style = ttk.Style()
        # Use 'clam' theme for better control over colors
        try:
            self.tree_style.theme_use('clam')
        except:
            pass

        # Style scrollbars to be dark
        self.tree_style.configure("Vertical.TScrollbar",
                       background="#1c2128",
                       troughcolor="#0d1117",
                       bordercolor="#0d1117",
                       arrowcolor="#8b949e")

        # Base row height and font size (scaled later)
        self.base_row_height = 32
        self.base_list_font_size = 10

        self.tree_style.configure("Dark.Treeview",
                       background="#0d1117",
                       foreground="white",
                       fieldbackground="#0d1117",
                       borderwidth=0,
                       rowheight=self.base_row_height)
        self.tree_style.configure("Dark.Treeview.Heading",
                       background="#1c2128",
                       foreground="white",
                       font=("Arial", 10, "bold"))
        self.tree_style.map("Dark.Treeview",
                 background=[("selected", "#2d333b")],
                 foreground=[("selected", "white")])
        # Remove the border/focus highlight
        self.tree_style.layout("Dark.Treeview", [('Dark.Treeview.treearea', {'sticky': 'nswe'})])

        self.project_tree = ttk.Treeview(
            tree_wrapper,
            columns=("date", "client", "project"),
            displaycolumns=self.column_order,
            show="headings",
            yscrollcommand=scrollbar.set,
            selectmode="extended",
            style="Dark.Treeview"
        )

        # Tag styling for archived and sandbox projects in list view.
        # sandbox_archived = archived-styled row (grey) rendered italic so an
        # archived-sandbox project is distinguishable from a plain archive.
        self.project_tree.tag_configure("archived", foreground="#8b949e")
        self.project_tree.tag_configure("sandbox", foreground="#fbbf24")
        _tree_font = tkfont.nametofont("TkDefaultFont").copy()
        _tree_font.configure(slant="italic")
        self.project_tree.tag_configure(
            "sandbox_archived", foreground="#8b949e", font=_tree_font
        )

        # Column display names
        self.column_names = {"date": "Date", "client": "Client", "project": "Project"}

        # Configure columns with sort click handlers - equal widths, all stretch equally
        for col in ["date", "client", "project"]:
            self.project_tree.heading(col, text=self._get_column_header(col), anchor=tk.W,
                                      command=lambda c=col: self._on_column_click(c))
            self.project_tree.column(col, width=150, minwidth=80, stretch=True)

        self.project_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.project_tree.yview)

        # Bind column header drag events (use add=True to preserve default selection behavior)
        self.project_tree.bind('<Button-1>', self._on_tree_click, add=True)
        self.project_tree.bind('<B1-Motion>', self._on_tree_drag, add=True)
        self.project_tree.bind('<ButtonRelease-1>', self._on_tree_release, add=True)

        # Store project IDs mapped to tree items
        self.tree_item_to_project = {}
        self.project_tree.bind('<<TreeviewSelect>>', self._on_project_selected)
        self.project_tree.bind('<Return>', self._on_enter_key)
        self.project_tree.bind('<Double-1>', self._on_enter_key)
        # Override the Treeview's native arrow handling so we don't double-move
        # when the global root binding also fires _nav_item_up/down.
        self.project_tree.bind('<Up>', self._on_list_up)
        self.project_tree.bind('<Down>', self._on_list_down)

        # === GRID VIEW ===
        self.grid_frame = tk.Frame(self.view_container, bg="#0d1117", highlightthickness=0, bd=0)

        # Canvas with scrollbar for grid
        self.grid_canvas = tk.Canvas(self.grid_frame, bg="#0d1117", highlightthickness=0, bd=0, takefocus=True)
        grid_scrollbar = ttk.Scrollbar(self.grid_frame, orient=tk.VERTICAL, command=self.grid_canvas.yview)
        self.grid_inner = tk.Frame(self.grid_canvas, bg="#0d1117", highlightthickness=0, bd=0)

        self.grid_canvas.configure(yscrollcommand=grid_scrollbar.set)
        grid_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.grid_window = self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")

        # Bind resize and scroll events
        self.grid_inner.bind("<Configure>", lambda e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        self.grid_canvas.bind("<Configure>", self._on_grid_canvas_resize)

        # Keyboard navigation for grid view
        self.grid_canvas.bind('<Left>', self._on_grid_left)
        self.grid_canvas.bind('<Right>', self._on_grid_right)
        self.grid_canvas.bind('<Up>', self._on_grid_up)
        self.grid_canvas.bind('<Down>', self._on_grid_down)
        self.grid_canvas.bind('<Return>', self._on_enter_key)
        # Mousewheel binding - different on Windows vs Linux
        self.grid_canvas.bind_all("<MouseWheel>", self._on_grid_mousewheel)
        self.grid_canvas.bind_all("<Button-4>", self._on_grid_mousewheel)  # Linux scroll up
        self.grid_canvas.bind_all("<Button-5>", self._on_grid_mousewheel)  # Linux scroll down

        # Show list view by default
        self.list_frame.pack(fill=tk.BOTH, expand=True)

        # Bottom: Project details (store reference for hiding during creation)
        self.details_frame = tk.Frame(right_frame, bg="#1c2128")
        self.details_frame.pack(fill=tk.X, padx=10, pady=10)

        details_header = tk.Frame(self.details_frame, bg="#1c2128")
        details_header.pack(fill=tk.X, padx=10, pady=(10, 5))

        details_title = tk.Label(details_header, text="Selected Project", bg="#1c2128", fg="white",
                                font=("Arial", 10, "bold"))
        details_title.pack(side=tk.LEFT, anchor=tk.W)

        # Deck button - styled like the FAB (fixed-size square, centered
        # glyph, hover state) rather than a normal flat text button, since
        # it's a primary quick-access action just like "New Project".
        deck_btn_size = 36
        deck_btn_bg = "#1f6feb"
        deck_btn_bg_hover = "#388bfd"
        deck_btn_bg_disabled = "#21262d"

        self.deck_btn = tk.Frame(
            details_header,
            width=deck_btn_size,
            height=deck_btn_size,
            bg=deck_btn_bg,
            cursor="hand2",
        )
        self.deck_btn.pack_propagate(False)
        self.deck_btn.pack(side=tk.RIGHT)

        self.deck_btn_label = tk.Label(
            self.deck_btn,
            text="🗂",
            font=("Segoe UI Emoji", 16),
            bg=deck_btn_bg,
            fg="white",
            cursor="hand2",
        )
        self.deck_btn_label.place(relx=0.5, rely=0.5, anchor="center")

        self._deck_btn_state = tk.DISABLED

        def _deck_btn_set_state(state):
            self._deck_btn_state = state
            bg = deck_btn_bg if state == tk.NORMAL else deck_btn_bg_disabled
            self.deck_btn.configure(bg=bg)
            self.deck_btn_label.configure(bg=bg)
        self._set_deck_btn_state = _deck_btn_set_state
        _deck_btn_set_state(tk.DISABLED)

        def _deck_btn_click(e):
            if self._deck_btn_state == tk.NORMAL:
                self.open_deck_for_selected()
        self.deck_btn.bind("<ButtonRelease-1>", _deck_btn_click)
        self.deck_btn_label.bind("<ButtonRelease-1>", _deck_btn_click)

        def _deck_btn_enter(e):
            if self._deck_btn_state == tk.NORMAL:
                self.deck_btn.configure(bg=deck_btn_bg_hover)
                self.deck_btn_label.configure(bg=deck_btn_bg_hover)
            if self.hint_callback:
                self.hint_callback("Open Deck: Ctrl+D")

        def _deck_btn_leave(e):
            _deck_btn_set_state(self._deck_btn_state)
            if self.hint_callback:
                self.hint_callback("")

        self.deck_btn.bind("<Enter>", _deck_btn_enter)
        self.deck_btn.bind("<Leave>", _deck_btn_leave)
        self.deck_btn_label.bind("<Enter>", _deck_btn_enter)
        self.deck_btn_label.bind("<Leave>", _deck_btn_leave)

        # Two-column body: project info + status actions on the left,
        # project-context Actions sitting right next to it. Keeps the panel
        # compact instead of growing vertically when several actions apply.
        # The left column takes its natural width; the right column sits
        # directly beside it with a small gap. Trailing space stays empty so
        # the columns stay grouped on the left.
        details_body = tk.Frame(self.details_frame, bg="#1c2128")
        details_body.pack(fill=tk.X)
        details_left = tk.Frame(details_body, bg="#1c2128")
        details_left.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
        details_right = tk.Frame(details_body, bg="#1c2128")
        details_right.pack(side=tk.LEFT, fill=tk.Y)

        # Detail labels
        self.detail_labels = {}
        detail_fields = [
            ("Client", "client_name"),
            ("Project", "project_name"),
            ("Order #", "order_number"),
            ("Location", "location"),
            ("Type", "project_type"),
            ("Date", "date_created"),
        ]

        details_grid = tk.Frame(details_left, bg="#1c2128")
        details_grid.pack(fill=tk.X, padx=10, pady=5)

        for i, (label, key) in enumerate(detail_fields):
            tk.Label(details_grid, text=f"{label}:", bg="#1c2128", fg="#8b949e",
                    font=("Arial", 8)).grid(row=i, column=0, sticky=tk.W, pady=2)

            value_label = tk.Label(details_grid, text="-", bg="#1c2128", fg="white",
                                  font=("Arial", 9))
            value_label.grid(row=i, column=1, sticky=tk.W, pady=2, padx=(10, 0))

            self.detail_labels[key] = value_label

        # Path section with clickable copy-to-clipboard
        path_section = tk.Frame(details_left, bg="#1c2128")
        path_section.pack(fill=tk.X, padx=10, pady=(5, 0))

        # Active Path (only shown for active projects)
        self.active_path_frame = tk.Frame(path_section, bg="#1c2128")
        self.active_path_frame.pack(fill=tk.X, pady=2)

        tk.Label(self.active_path_frame, text="Active Path:", bg="#1c2128", fg="#8b949e",
                font=("Arial", 8), width=10, anchor="w").pack(side=tk.LEFT)

        self.active_path_label = tk.Label(
            self.active_path_frame, text="-", bg="#1c2128", fg="#58a6ff",
            font=("Arial", 9), cursor="hand2", anchor="w"
        )
        self.active_path_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
        self.active_path_label.bind("<Button-1>", lambda e: self._open_path_in_explorer("active"))
        self.active_path_label.bind("<Button-3>", lambda e: self._copy_path_to_clipboard("active"))
        self.active_path_label.bind("<Enter>", lambda e: self.active_path_label.configure(fg="#79c0ff"))
        self.active_path_label.bind("<Leave>", lambda e: self.active_path_label.configure(fg="#58a6ff"))

        # RAW Path (always shown)
        raw_path_frame = tk.Frame(path_section, bg="#1c2128")
        raw_path_frame.pack(fill=tk.X, pady=2)

        tk.Label(raw_path_frame, text="RAW Path:", bg="#1c2128", fg="#8b949e",
                font=("Arial", 8), width=10, anchor="w").pack(side=tk.LEFT)

        self.raw_path_label = tk.Label(
            raw_path_frame, text="-", bg="#1c2128", fg="#58a6ff",
            font=("Arial", 9), cursor="hand2", anchor="w"
        )
        self.raw_path_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
        self.raw_path_label.bind("<Button-1>", lambda e: self._open_path_in_explorer("raw"))
        self.raw_path_label.bind("<Button-3>", lambda e: self._copy_path_to_clipboard("raw"))
        self.raw_path_label.bind("<Enter>", lambda e: self.raw_path_label.configure(fg="#79c0ff"))
        self.raw_path_label.bind("<Leave>", lambda e: self.raw_path_label.configure(fg="#58a6ff"))

        # Website URL — read from/written to the project's own
        # Project_Details.txt (a "Website: <url>" line), normally filled in
        # once at project creation; that file is the single source of
        # truth, so hand-editing it there works too. Left-click opens it,
        # or prompts to set one if it's still empty; right-click always
        # opens the edit prompt.
        website_frame = tk.Frame(path_section, bg="#1c2128")
        website_frame.pack(fill=tk.X, pady=2)

        tk.Label(website_frame, text="Website:", bg="#1c2128", fg="#8b949e",
                font=("Arial", 8), width=10, anchor="w").pack(side=tk.LEFT)

        self.website_label = tk.Label(
            website_frame, text="-", bg="#1c2128", fg="#58a6ff",
            font=("Arial", 9), cursor="hand2", anchor="w"
        )
        self.website_label.pack(side=tk.LEFT, padx=(10, 0), fill=tk.X, expand=True)
        self.website_label.bind("<Button-1>", lambda e: self._open_website())
        self.website_label.bind("<Button-3>", lambda e: self._edit_website_url())
        self.website_label.bind("<Enter>", lambda e: self.website_label.configure(fg="#79c0ff"))
        self.website_label.bind("<Leave>", lambda e: self.website_label.configure(fg="#58a6ff"))

        # Action buttons
        button_frame = tk.Frame(details_left, bg="#1c2128")
        button_frame.pack(fill=tk.X, padx=10, pady=(10, 10))

        self.open_btn = make_flat_button(
            button_frame, "📂 Open Folder", self._open_folder,
            bg="#238636", fg="white", hover_bg="#2ea043", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.open_btn.pack(side=tk.LEFT, padx=(0, 5))

        self.rename_btn = make_flat_button(
            button_frame, "✏️ Rename", self._rename_project,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.rename_btn.pack(side=tk.LEFT, padx=5)

        self.archive_btn = make_flat_button(
            button_frame, "📦 Archive", self._archive_project,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.archive_btn.pack(side=tk.LEFT, padx=5)

        self.unarchive_btn = make_flat_button(
            button_frame, "📤 Un-Archive", self._unarchive_project,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.unarchive_btn.pack(side=tk.LEFT, padx=5)

        self.promote_btn = make_flat_button(
            button_frame, "🚀 Promote to Active", self._promote_project,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.promote_btn.pack(side=tk.LEFT, padx=5)

        # Append a timestamped, project-tagged entry to the category-level
        # notes/<category>_notes.txt scratchpad. Per-project notes still live
        # in the project DB; this button is for the running category log.
        self.log_note_btn = make_flat_button(
            button_frame, "📝 Log to Notes", self._log_to_category_notes,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, padx=15, pady=6,
        )
        self.log_note_btn.pack(side=tk.LEFT, padx=5)

        # Documents section, in the right column, to the left of Actions.
        # Project Details lives in _LIBRARY for any category; Order Details /
        # Invoice only apply once a project is linked to a WooCommerce order
        # (Physical). Unlike Actions, this section is always shown — each
        # button is individually enabled/disabled per selection in
        # _display_project_details / _clear_details, never hidden as a group.
        documents_frame = tk.Frame(details_right, bg="#1c2128")
        documents_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 16))
        documents_header = tk.Label(
            documents_frame,
            text="DOCUMENTS",
            bg="#1c2128",
            fg="#8b949e",
            font=("Arial", 8, "bold"),
            anchor="w",
        )
        documents_header.pack(fill=tk.X, pady=(8, 4))
        documents_container = tk.Frame(documents_frame, bg="#1c2128")
        documents_container.pack(fill=tk.X, pady=(0, 10))

        self.project_details_btn = make_flat_button(
            documents_container, "📄 Project Details", self._open_project_details,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, anchor="w", padx=15, pady=6,
        )
        self.project_details_btn.pack(side=tk.TOP, fill=tk.X, pady=2)

        self.order_details_btn = make_flat_button(
            documents_container, "🧾 Order Details", self._open_order_details,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, anchor="w", padx=15, pady=6,
        )
        self.order_details_btn.pack(side=tk.TOP, fill=tk.X, pady=2)

        self.invoice_btn = make_flat_button(
            documents_container, "💳 Invoice", self._open_invoice,
            bg="#1c2128", fg="white", hover_bg="#262c36", font=("Arial", 9),
            state=tk.DISABLED, anchor="w", padx=15, pady=6,
        )
        self.invoice_btn.pack(side=tk.TOP, fill=tk.X, pady=2)

        # Project-context Actions section, in the right column, to the right
        # of Documents. Populated dynamically in _display_project_details
        # based on the selected project's project_type. Hidden when no
        # project is selected or no actions match. Buttons stack vertically
        # here so the panel stays compact horizontally instead of growing
        # taller.
        self.actions_frame = tk.Frame(details_right, bg="#1c2128")
        self.actions_header = tk.Label(
            self.actions_frame,
            text="ACTIONS",
            bg="#1c2128",
            fg="#8b949e",
            font=("Arial", 8, "bold"),
            anchor="w",
        )
        self.actions_header.pack(fill=tk.X, pady=(8, 4))
        self.actions_container = tk.Frame(self.actions_frame, bg="#1c2128")
        self.actions_container.pack(fill=tk.BOTH, expand=True, pady=(0, 10))
        # Buttons created on the fly per project; track them so we can clear.
        self._action_buttons = []

        # Store reference to right frame for FAB positioning
        self.right_frame = right_frame

        # Create the Floating Action Button (FAB) - positioned over view_container
        self._create_fab_button()

    def _create_fab_button(self):
        """Create the floating action button for project creation."""
        # Create FAB as a fixed-size frame with label for precise control
        fab_size = 44  # Square size in pixels

        self.fab_button = tk.Frame(
            self.view_container,
            width=fab_size,
            height=fab_size,
            bg="#3fb950",
            cursor="hand2"
        )
        self.fab_button.pack_propagate(False)  # Prevent resizing based on content

        # Label with big plus sign centered in the frame
        self.fab_label = tk.Label(
            self.fab_button,
            text="+",
            font=("Arial", 34, "bold"),
            bg="#3fb950",
            fg="white",
            cursor="hand2"
        )
        self.fab_label.place(relx=0.5, rely=0.5, anchor="center")

        # Bind click events to both frame and label (use ButtonRelease for reliability)
        self.fab_button.bind("<ButtonRelease-1>", lambda e: self._on_fab_clicked())
        self.fab_label.bind("<ButtonRelease-1>", lambda e: self._on_fab_clicked())

        # Hover effects
        def on_enter(e):
            self.fab_button.configure(bg="#2ea043")
            self.fab_label.configure(bg="#2ea043")
            if self.hint_callback:
                self.hint_callback("Create New Project: Ctrl+N")

        def on_leave(e):
            self.fab_button.configure(bg="#3fb950")
            self.fab_label.configure(bg="#3fb950")
            if self.hint_callback:
                self.hint_callback("")

        self.fab_button.bind("<Enter>", on_enter)
        self.fab_button.bind("<Leave>", on_leave)
        self.fab_label.bind("<Enter>", on_enter)
        self.fab_label.bind("<Leave>", on_leave)

        # Position in bottom-right corner of view_container (above details panel)
        self.fab_button.place(relx=0.97, rely=0.97, anchor="se")

        # Update FAB visibility based on current category
        self._update_fab_visibility()

    def _sole_creative_category(self):
        """Return the single selected creative category, or None if not exactly one."""
        creative = [c for c in self.selected_categories if is_creative_category(c)]
        if len(creative) == 1 and len(self.selected_categories) == 1:
            return creative[0]
        return None

    def _update_fab_visibility(self):
        """Show/hide FAB based on current category and view state."""
        if self.fab_button is None:
            return

        # Hide FAB when in creation mode
        if self.view_state != "PROJECT_LIST":
            self.fab_button.place_forget()
            return

        # Show FAB only when exactly one creative category is selected.
        if self._sole_creative_category() is not None:
            self.fab_button.place(relx=0.97, rely=0.97, anchor="se")
            self.fab_button.lift()  # Ensure FAB stays on top
            self.fab_label.lift()   # Also lift the label
        else:
            self.fab_button.place_forget()

    def _on_fab_clicked(self):
        """Handle FAB button click."""
        # Prevent opening multiple panels - only allow one structure panel at a time
        if self.view_state != "PROJECT_LIST":
            return

        category = self._sole_creative_category()
        if category is None:
            messagebox.showinfo(
                "Select Category",
                "Please select exactly one creative category before creating a project."
            )
            return

        # Notify parent that creation is starting
        if self.creation_start_callback:
            self.creation_start_callback()

        # Check if category has multiple subtypes
        if has_multiple_subtypes(category):
            self._show_subtype_selection(category)
        else:
            # Single subtype - open form directly
            subtypes = get_subtypes_for_category(category)
            if subtypes:
                self._open_creation_form(category, subtypes[0])

    def _show_subtype_selection(self, category):
        """Show panel with subtype selection buttons."""
        self.view_state = "SUBTYPE_SELECTION"
        self._update_fab_visibility()

        # Hide the view container (list/grid views) and details panel
        self.view_container.pack_forget()
        self.details_frame.pack_forget()

        # Create selection panel
        self.creation_panel = tk.Frame(self.right_frame, bg="#1c2128")
        self.creation_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Header with back button and category color
        header_color = category_color(category) or "#1c2128"
        header = tk.Frame(self.creation_panel, bg=header_color, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        back_btn = make_flat_button(
            header, "< Back", self._close_creation_panel,
            bg=header_color, fg="white", hover_fg="#cccccc", font=("Arial", 10),
        )
        back_btn.pack(side=tk.LEFT, padx=10, pady=10)

        header_label = tk.Label(
            header,
            text=f"Create New {category} Project",
            font=("Arial", 14, "bold"),
            fg="white",
            bg=header_color
        )
        header_label.pack(pady=15)

        # Buttons container - horizontal layout
        buttons_frame = tk.Frame(self.creation_panel, bg="#1c2128")
        buttons_frame.pack(expand=True, pady=30)

        # Store subtype buttons for keyboard navigation
        self.subtype_buttons = []
        self.subtype_selected_index = 0
        self.subtype_category = category

        # Create buttons for each subtype - horizontally spaced
        subtypes = get_subtypes_for_category(category)
        for idx, subtype in enumerate(subtypes):
            display_name = get_subtype_display_name(subtype)
            btn = make_flat_button(
                buttons_frame, display_name,
                lambda s=subtype: self._open_creation_form(category, s),
                bg="#238636", fg="white", hover_bg="#2ea043", font=("Arial", 12),
                padx=20, pady=10,
            )
            btn.pack(side=tk.LEFT, padx=10)
            self.subtype_buttons.append({"button": btn, "subtype": subtype})

        # Highlight first button by default
        self._update_subtype_selection()

        # Bind keyboard navigation to the creation panel
        self.creation_panel.bind('<Left>', self._on_subtype_left)
        self.creation_panel.bind('<Right>', self._on_subtype_right)
        self.creation_panel.bind('<Return>', self._on_subtype_enter)
        self.creation_panel.focus_set()

    def _update_subtype_selection(self):
        """Update visual highlighting for subtype selection."""
        for idx, item in enumerate(self.subtype_buttons):
            if idx == self.subtype_selected_index:
                # Highlighted - darker green with border
                item["button"].configure(bg="#2ea043", highlightbackground="white", highlightthickness=2)
            else:
                # Normal green
                item["button"].configure(bg="#238636", highlightthickness=0)

    def _on_subtype_left(self, event):
        """Navigate left in subtype selection."""
        if self.subtype_buttons and self.subtype_selected_index > 0:
            self.subtype_selected_index -= 1
            self._update_subtype_selection()
        return "break"  # Stop event propagation

    def _on_subtype_right(self, event):
        """Navigate right in subtype selection."""
        if self.subtype_buttons and self.subtype_selected_index < len(self.subtype_buttons) - 1:
            self.subtype_selected_index += 1
            self._update_subtype_selection()
        return "break"  # Stop event propagation

    def _on_subtype_enter(self, event):
        """Select current subtype and open creation form."""
        if self.subtype_buttons and 0 <= self.subtype_selected_index < len(self.subtype_buttons):
            subtype = self.subtype_buttons[self.subtype_selected_index]["subtype"]
            self._open_creation_form(self.subtype_category, subtype)
        return "break"  # Stop event propagation

    def _open_creation_form(self, category, subtype):
        """Open the embedded creation form for a specific subtype."""
        self.view_state = "CREATION_FORM"
        self._update_fab_visibility()

        # Clear any existing creation panel (subtype selection)
        if self.creation_panel:
            self.creation_panel.destroy()
            self.creation_panel = None

        # Hide the view container (list/grid views) and details panel
        self.view_container.pack_forget()
        self.details_frame.pack_forget()

        # Create form panel
        self.creation_panel = tk.Frame(self.right_frame, bg="#1c2128")
        self.creation_panel.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Header with back button
        header_color = category_color(category) or "#1c2128"
        header = tk.Frame(self.creation_panel, bg=header_color, height=50)
        header.pack(fill=tk.X)
        header.pack_propagate(False)

        back_btn = make_flat_button(
            header, "< Back", self._close_creation_panel,
            bg=header_color, fg="white", hover_fg="#cccccc", font=("Arial", 10),
        )
        back_btn.pack(side=tk.LEFT, padx=10, pady=10)

        display_name = get_subtype_display_name(subtype)
        header_label = tk.Label(
            header,
            text=f"New {display_name} Project",
            font=("Arial", 14, "bold"),
            fg="white",
            bg=header_color
        )
        header_label.pack(pady=15)

        # Form container frame
        form_container = tk.Frame(self.creation_panel, bg="#0d1117")
        form_container.pack(fill=tk.BOTH, expand=True)

        # Get the creator class and instantiate it in embedded mode
        creator_class = get_creator_class(category, subtype)
        if creator_class:
            try:
                self.active_creator = creator_class(
                    form_container,
                    embedded=True,
                    on_project_created=self._on_project_created,
                    on_cancel=self._close_creation_panel,
                    project_db=self.db
                )
            except Exception as e:
                logger.error(f"Failed to create embedded form: {e}")
                messagebox.showerror("Error", f"Failed to open creation form: {e}")
                self._close_creation_panel()
        else:
            logger.error(f"Creator class not found for {category}/{subtype}")
            messagebox.showerror("Error", f"Creator not found for {category}/{subtype}")
            self._close_creation_panel()

    def _on_project_created(self, project_data):
        """Handle successful project creation from embedded form."""
        logger.info(f"Project created: {project_data.get('project_name')}")

        # Close creation panel without notifying cancel
        self._close_creation_panel(notify_cancel=False)

        # Show success message
        self._update_status(f"Created project: {project_data.get('project_name')}")

        # Register project in database (centralized for all categories)
        if self.db and project_data:
            try:
                project_id = self.db.register_project(project_data)
                logger.info(f"Registered project in database: {project_id}")
            except Exception as e:
                logger.error(f"Failed to register project in database: {e}")

        # Notify parent that creation is done — parent handles
        # filter switching, refresh, and project selection
        if self.creation_done_callback:
            self.creation_done_callback(project_data)

    def _get_category_for_type(self, project_type: str) -> Optional[str]:
        """Map a project_type string to its tracker category."""
        if project_type in ("GD", "FX", "VFX", "VJ") or project_type.startswith("Visual-"):
            return "Visual"
        elif project_type == "Audio":
            return "Audio"
        elif project_type == "Physical":
            return "Physical"
        elif project_type in ("Godot", "TD", "Resolume", "RealTime"):
            return "RealTime"
        elif project_type == "Photo":
            return "Photo"
        elif project_type == "Web":
            return "Web"
        return None

    def _select_project_by_path(self, path: str):
        """Select a project by its path in whichever view is active.

        Matches by folder name (basename) since paths may differ in format
        between WSL (/mnt/d/...) and Windows (D:\\...) representations.
        """
        if not path:
            return
        # Match by folder name to avoid WSL vs Windows path format issues
        target_basename = os.path.basename(path.replace('\\', '/').rstrip('/'))
        logger.debug(f"_select_project_by_path: looking for basename '{target_basename}'")

        if self.view_mode.get() == "grid":
            for idx, project in enumerate(self.grid_projects):
                project_path = project.get('path', '').replace('\\', '/')
                if os.path.basename(project_path.rstrip('/')) == target_basename:
                    logger.debug(f"_select_project_by_path: found at grid index {idx}")
                    self.grid_selected_index = idx
                    self._select_grid_card(idx)
                    return
            logger.debug(f"_select_project_by_path: not found in {len(self.grid_projects)} grid projects")
        else:
            for item_id, project in self.tree_item_to_project.items():
                project_path = project.get('path', '').replace('\\', '/')
                if os.path.basename(project_path.rstrip('/')) == target_basename:
                    self.project_tree.selection_set(item_id)
                    self.project_tree.see(item_id)
                    self.selected_project = project
                    self._display_project_details(project)
                    return

    def _close_creation_panel(self, notify_cancel=True):
        """Close the creation panel and return to project list.

        Args:
            notify_cancel: If True, notify parent that creation was cancelled.
                          Set to False when called after successful creation.
        """
        # Clean up creator
        self.active_creator = None

        # Clean up subtype selection state
        self.subtype_buttons = []
        self.subtype_selected_index = 0
        self.subtype_category = None

        # Destroy creation panel
        if self.creation_panel:
            self.creation_panel.destroy()
            self.creation_panel = None

        # Reset state
        self.view_state = "PROJECT_LIST"

        # Show the view container and details panel again (in correct order)
        self.view_container.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        self.details_frame.pack(fill=tk.X, padx=10, pady=10)

        # Update FAB visibility
        self._update_fab_visibility()

        # Notify parent of cancellation
        if notify_cancel and self.creation_cancel_callback:
            self.creation_cancel_callback()

    def set_category(self, category_name: Optional[str]):
        """Single-category setter (kept for backward compat)."""
        self.set_categories([category_name] if category_name else [])

    def set_categories(self, category_names):
        """Set selected categories (public method for external callers like the hub)."""
        # If in creation mode, close it first
        if self.view_state != "PROJECT_LIST":
            self._close_creation_panel(notify_cancel=False)

        self.selected_categories = {n for n in (category_names or []) if n}

        # Update category button styles if in standalone mode
        for name, btn_info in self.category_buttons.items():
            self._update_category_card_style(btn_info, name)

        self._update_fab_visibility()
        self._update_physical_subtype_visibility()
        self.refresh_project_list()

    def _apply_saved_settings(self):
        """Apply saved settings after UI is built."""
        # Apply scale values
        saved_list_scale = self.settings.get("list_scale", 100)
        saved_grid_scale = self.settings.get("grid_scale", 100)

        self.list_scale_value.set(saved_list_scale)
        self.grid_scale_value.set(saved_grid_scale)

        # Apply list scale to treeview
        scale = saved_list_scale / 100.0
        new_row_height = int(self.base_row_height * scale)
        new_font_size = max(7, int(self.base_list_font_size * scale))
        self.tree_style.configure("Dark.Treeview", rowheight=new_row_height,
                                  font=("Arial", new_font_size))

        # Apply view mode
        saved_view_mode = self.settings.get("view_mode", "list")
        self.view_mode.set(saved_view_mode)

        # Switch to the saved view (without saving again)
        if saved_view_mode == "grid":
            self.list_frame.pack_forget()
            self.list_scale_frame.pack_forget()
            self.grid_frame.pack(fill=tk.BOTH, expand=True)
            self.grid_scale_frame.pack(side=tk.RIGHT, padx=(10, 2))

        # Sync toggle-button fg with the active view (selectcolor only swaps bg).
        if hasattr(self, "_view_mode_buttons"):
            for mode, btn in self._view_mode_buttons.items():
                btn.configure(fg="white" if mode == saved_view_mode else "#8b949e")

        # Update filter button styles based on saved filter
        self._update_filter_button_styles()

        logger.debug(f"Applied saved settings: view={saved_view_mode}, list_scale={saved_list_scale}, grid_scale={saved_grid_scale}")

    def _update_status(self, message: str, status_type: str = "info"):
        """Log status message to GUI and file logger."""
        logger.info(message)
        if self.status_callback:
            self.status_callback(message, status_type)

    def _select_category(self, category_name, *, additive=False):
        """Handle category button selection.

        Plain selection collapses to a single category. When ``additive`` is
        True (Shift+click), toggle this category in the existing set.
        """
        # If in creation mode, close it first
        if self.view_state != "PROJECT_LIST":
            self._close_creation_panel(notify_cancel=False)

        if additive:
            if category_name in self.selected_categories:
                self.selected_categories.discard(category_name)
            else:
                self.selected_categories.add(category_name)
        else:
            self.selected_categories = {category_name}

        # Update all button styles
        for name, btn_info in self.category_buttons.items():
            self._update_category_card_style(btn_info, name)

        self._update_fab_visibility()
        self._update_physical_subtype_visibility()
        self.refresh_project_list()

    def _update_category_card_style(self, btn_info, name):
        """Update category button styling based on selection state."""
        frame = btn_info["frame"]

        if name in self.selected_categories:
            # Selected style - white highlight border
            frame.configure(highlightbackground="white")
        else:
            # Unselected style - blend with background
            frame.configure(highlightbackground="#0d1117")

    def _clear_category_selection(self):
        """Clear category selection to show all projects."""
        # If in creation mode, close it first
        if self.view_state != "PROJECT_LIST":
            self._close_creation_panel(notify_cancel=False)

        self.selected_categories = set()

        # Update all button styles
        for name, btn_info in self.category_buttons.items():
            self._update_category_card_style(btn_info, name)

        self._update_fab_visibility()
        self._update_physical_subtype_visibility()
        self.refresh_project_list()

    def refresh_project_list(self):
        """Refresh the project list from database based on selected category."""
        # Clear tree
        for item in self.project_tree.get_children():
            self.project_tree.delete(item)

        # Enabled filter toggles. "active"/"archived" are statuses;
        # "sandbox" is an orthogonal flag (metadata.is_sandbox).
        enabled = self._get_active_statuses()

        # Get search query
        query = self.search_query.get().strip()

        # The search pool needs archived rows whenever archive OR sandbox is
        # on, since an archived-sandbox project is surfaced by either toggle.
        include_archived = "archived" in enabled or "sandbox" in enabled
        if query:
            projects = self.db.search_projects(query, include_archived=include_archived)
        else:
            projects = self.db.get_all_projects(status="all")

        # Filter by toggles:
        #   active  → status=="active" AND NOT is_sandbox
        #   archived → status=="archived"
        #   sandbox → is_sandbox (any status)
        if enabled:
            want_active = "active" in enabled
            want_archived = "archived" in enabled
            want_sandbox = "sandbox" in enabled
            def _matches(p):
                is_sandbox = p.get("metadata", {}).get("is_sandbox", False)
                status = p.get("status")
                if want_sandbox and is_sandbox:
                    return True
                if want_archived and status == "archived":
                    return True
                if want_active and status == "active" and not is_sandbox:
                    return True
                return False
            projects = [p for p in projects if _matches(p)]
        else:
            projects = []

        # Filter by scope (personal/client). Sandbox projects follow the same
        # dichotomy as regular projects — a sandbox folder without a client
        # name is personal, one with a client (e.g. Polysense) is a client
        # test project — so the scope filter applies to them too.
        scopes = self.filter_scopes
        def _is_personal(p):
            return (p.get("client_name", "").lower() == "personal" or
                    p.get("metadata", {}).get("is_personal", False))
        if not scopes:
            projects = []
        elif scopes == {"personal"}:
            projects = [p for p in projects if _is_personal(p)]
        elif scopes == {"client"}:
            projects = [p for p in projects if not _is_personal(p)]
        # else: scopes == {"personal", "client"} → no filter

        # Group projects by category
        from pipeline_categories import creative_categories, category_emoji
        categories = {
            name: {
                "icon": category_emoji(name),
                "projects": [],
                "color": category_color(name),
            }
            for name in creative_categories()
        }

        # Categorize all projects via the unified archive_category lookup
        for project in projects:
            project_type = project.get("project_type", "")
            cat = archive_category_for(project_type)
            if cat in categories:
                categories[cat]["projects"].append(project)

        # Update category button counts
        for cat_name, cat_info in categories.items():
            if cat_name in self.category_buttons:
                btn_info = self.category_buttons[cat_name]
                count = len(cat_info["projects"])
                btn_info["count_label"].config(text=f"({count})")

        # Filter by selected categories (union). Empty set = show all.
        if self.selected_categories:
            category_projects = []
            for name in self.selected_categories:
                category_projects.extend(categories.get(name, {}).get("projects", []))
        else:
            category_projects = []
            for cat_info in categories.values():
                category_projects.extend(cat_info["projects"])

        # Filter by physical subtype if applicable (only meaningful when Physical
        # is the single selected category).
        physical_subtype = self.filter_physical_subtype.get()
        if (self.selected_categories == {"Physical"}
                and physical_subtype != "all"):
            category_projects = [
                p for p in category_projects
                if p.get("metadata", {}).get("physical_subtype", "") == physical_subtype
            ]

        # Sort by selected column
        sort_key_map = {
            "date": lambda p: p.get("date_created", ""),
            "client": lambda p: p.get("client_name", "").lower(),
            "project": lambda p: p.get("project_name", "").lower()
        }
        sort_key = sort_key_map.get(self.sort_column, sort_key_map["date"])
        category_projects.sort(key=sort_key, reverse=self.sort_reverse)

        # Clear the project ID mapping
        self.tree_item_to_project = {}

        # Populate tree with flat list
        for project in category_projects:
            project_type = project.get("project_type", "")
            type_info = project_type_info(project_type)

            # Get display values
            date_str = project.get("date_created", "")
            client_name = project.get("client_name", "")
            project_name = project.get('project_name', '')

            # Insert into tree. Sandbox is a flag, not a status. An
            # archived-sandbox row gets the sandbox_archived tag (italic
            # grey); a plain active-sandbox row gets the sandbox tag.
            status = project.get("status")
            is_sandbox = project.get("metadata", {}).get("is_sandbox", False)
            if status == "archived" and is_sandbox:
                row_tags = ("sandbox_archived",)
            elif is_sandbox:
                row_tags = ("sandbox",)
            else:
                row_tags = (status,)
            item_id = self.project_tree.insert(
                "",
                tk.END,
                values=(date_str, client_name, project_name),
                tags=row_tags
            )

            # Store mapping from tree item to project
            self.tree_item_to_project[item_id] = project

        # Update count label (total and selected)
        count = len(category_projects)
        selected = len(self.project_tree.selection())
        if selected > 0:
            self.count_label.config(text=f"{selected}/{count}")
        else:
            self.count_label.config(text=f"{count}")

        if self.selected_categories:
            cat_label = ", ".join(sorted(self.selected_categories))
            logger.debug(f"Refreshed project list: {count} projects in {cat_label}")
        else:
            logger.debug(f"Refreshed project list: {count} projects")

        # Also refresh grid view if it's currently visible
        if self.view_mode.get() == "grid":
            self._populate_grid()

    def _switch_view(self):
        """Switch between list and grid view."""
        view = self.view_mode.get()

        # Save view mode preference
        self.settings.set("view_mode", view)

        # Refresh button fg so the active view's icon is white on the accent
        # bg while the inactive one stays muted (selectcolor only swaps bg).
        if hasattr(self, "_view_mode_buttons"):
            for mode, btn in self._view_mode_buttons.items():
                btn.configure(fg="white" if mode == view else "#8b949e")

        if view == "list":
            self.grid_frame.pack_forget()
            self.grid_scale_frame.pack_forget()
            self.list_frame.pack(fill=tk.BOTH, expand=True)
            self.list_scale_frame.pack(side=tk.RIGHT, padx=(10, 2))
            self.project_tree.focus_set()
            # Restore selection from selected_project
            if self.selected_project:
                for item_id, project in self.tree_item_to_project.items():
                    if project.get("id") == self.selected_project.get("id"):
                        self.project_tree.selection_set(item_id)
                        self.project_tree.see(item_id)
                        break
        else:
            self.list_frame.pack_forget()
            self.list_scale_frame.pack_forget()
            self.grid_frame.pack(fill=tk.BOTH, expand=True)
            self.grid_scale_frame.pack(side=tk.RIGHT, padx=(10, 2))
            self._populate_grid()
            # Set focus for keyboard navigation
            self.grid_canvas.focus_set()
            # Restore selection from selected_project
            if self.selected_project:
                for idx, project in enumerate(self.grid_projects):
                    if project.get("id") == self.selected_project.get("id"):
                        self.grid_selected_index = idx
                        self._select_grid_card(idx)
                        break
            elif self.grid_projects:
                # No selection, select first
                self.grid_selected_index = 0
                self._select_grid_card(0)

    def _on_list_scale_changed(self, value):
        """Handle list scale slider change - update row height and font size."""
        scale = int(value) / 100.0  # Convert to multiplier (0.5 - 1.5)
        new_row_height = int(self.base_row_height * scale)
        new_font_size = max(7, int(self.base_list_font_size * scale))
        self.tree_style.configure("Dark.Treeview", rowheight=new_row_height,
                                  font=("Arial", new_font_size))
        # Save scale preference
        self.settings.set("list_scale", int(value))

    def _on_grid_scale_changed(self, value):
        """Handle grid scale slider change - repopulate grid."""
        # Save scale preference
        self.settings.set("grid_scale", int(value))
        if self.view_mode.get() == "grid":
            self._populate_grid()

    def _get_scaled_card_size(self) -> int:
        """Get card size based on current grid scale value."""
        base_card_size = 120
        scale = self.grid_scale_value.get() / 100.0
        return int(base_card_size * scale)

    def _on_grid_canvas_resize(self, event):
        """Handle grid canvas resize to adjust inner frame width and repopulate."""
        self.grid_canvas.itemconfig(self.grid_window, width=event.width)
        # Repopulate grid when resized to adjust number of columns
        if self.view_mode.get() == "grid" and self.tree_item_to_project:
            self._populate_grid()

    def _on_grid_mousewheel(self, event):
        """Handle mousewheel scrolling for grid view."""
        if self.view_mode.get() != "grid":
            return

        # Check if there's content to scroll
        try:
            bbox = self.grid_canvas.bbox("all")
            if bbox is None:
                return "break"

            # Get view and content dimensions
            view_height = self.grid_canvas.winfo_height()
            content_height = bbox[3] - bbox[1]

            # Only scroll if content is larger than view
            if content_height <= view_height:
                return "break"

            # Handle both Windows (MouseWheel) and Linux (Button-4/5) events
            if event.num == 4 or (hasattr(event, 'delta') and event.delta > 0):
                # Scroll up
                self.grid_canvas.yview_scroll(-1, "units")
            elif event.num == 5 or (hasattr(event, 'delta') and event.delta < 0):
                # Scroll down
                self.grid_canvas.yview_scroll(1, "units")

            return "break"
        except:
            return "break"

    def _populate_grid(self):
        """Populate the grid view with project cards."""
        # Reset grid tracking BEFORE destroying widgets
        self.grid_cards = []
        self.grid_projects = []
        self.grid_selected_indices = set()

        # Clear existing grid items
        for widget in self.grid_inner.winfo_children():
            widget.destroy()

        # Get current projects (use the same data as list view)
        projects = list(self.tree_item_to_project.values())

        if not projects:
            no_projects = tk.Label(self.grid_inner, text="No projects found",
                                  bg="#0d1117", fg="#8b949e", font=("Arial", 11))
            no_projects.pack(pady=20)
            self.grid_selected_index = -1
            return

        # Calculate number of columns based on canvas width and scaled card size
        canvas_width = self.grid_canvas.winfo_width()
        card_size = self._get_scaled_card_size()
        padding = 16  # Total padding between cards
        self.grid_cols = max(1, canvas_width // (card_size + padding))

        # Create grid of project cards - use constant spacing (no weight distribution)
        for idx, project in enumerate(projects):
            row = idx // self.grid_cols
            col = idx % self.grid_cols

            card = self._create_project_card(project, row, col, card_size)
            self.grid_cards.append(card)
            self.grid_projects.append(project)

        # Restore selection if valid, otherwise select first
        if self.grid_selected_index >= len(self.grid_projects):
            self.grid_selected_index = 0 if self.grid_projects else -1

        # Highlight current selection
        if self.grid_selected_index >= 0:
            self._highlight_grid_card(self.grid_selected_index)

        # Ensure FAB stays on top after grid population
        if self.fab_button and self.view_state == "PROJECT_LIST":
            self.fab_button.lift()
            self.fab_label.lift()

    def _create_project_card(self, project: Dict, row: int, col: int, card_size: int = 120):
        """Create a single square project card for grid view."""
        project_type = project.get("project_type", "")
        type_info = project_type_info(project_type)
        status = project.get("status", "active")

        # Scale factor for fonts based on card size (120 is base)
        font_scale = card_size / 120.0
        name_size = max(7, int(8 * font_scale))
        small_size = max(6, int(7 * font_scale))

        # Truncation length for subtitle
        client_max = max(6, int(10 * font_scale))

        # Colors - dim for archived projects, tint for sandbox. Sandbox is a
        # flag, not a status — an archived project can also be sandbox.
        accent_color = type_info.get("color", "#8b949e")
        is_archived = status == "archived"
        is_sandbox_flag = bool(project.get("metadata", {}).get("is_sandbox", False))
        is_sandbox = is_sandbox_flag and not is_archived
        was_sandbox = is_sandbox_flag
        card_bg = "#1c2128"
        title_fg = "white"
        sub_fg = "#8b949e"
        bar_height = max(3, int(4 * font_scale))

        # Card frame - fixed square size
        card = tk.Frame(self.grid_inner, bg=card_bg, relief=tk.FLAT,
                       width=card_size, height=card_size, cursor="hand2")
        card.grid(row=row, column=col, padx=8, pady=8)
        card.grid_propagate(False)  # Keep fixed size
        card.pack_propagate(False)  # Keep fixed size

        # Use grid layout: accent bar top, title fills middle, then variant
        # (for Web projects), subtitle, and date pinned to bottom.
        card.columnconfigure(0, weight=1)
        card.rowconfigure(0, weight=0)  # accent bar - fixed
        card.rowconfigure(1, weight=1)  # title - expands to fill available space
        card.rowconfigure(2, weight=0)  # variant text (Web only)
        card.rowconfigure(3, weight=0)  # subtitle - fixed at bottom
        card.rowconfigure(4, weight=0)  # date - fixed at bottom

        # Store default bg on card for highlight restore
        card._card_bg = card_bg

        # Colored accent bar at top - muted for archived, vibrant for active
        if is_archived:
            # Darken the accent color by blending towards dark grey
            r, g, b = int(accent_color[1:3], 16), int(accent_color[3:5], 16), int(accent_color[5:7], 16)
            bar_color = f"#{r//3:02x}{g//3:02x}{b//3:02x}"
        else:
            bar_color = accent_color
        accent_bar = tk.Frame(card, bg=bar_color, height=bar_height)
        accent_bar._is_accent_bar = True
        accent_bar.grid(row=0, column=0, sticky="new")
        if is_archived:
            # Archive badge: thicker strip overlapping the right side of the accent bar
            badge_height = max(10, int(12 * font_scale))
            badge_font_size = max(5, int(5 * font_scale))
            archive_badge = tk.Label(card, text="archived", bg="#da3633", fg="#ffd7d5",
                                     font=("Arial", badge_font_size),
                                     padx=3, pady=0)
            archive_badge._is_accent_bar = True  # skip during highlight recolor
            archive_badge.place(relx=1.0, y=0, anchor="ne")
            # If this archived project was a sandbox project, stack a sandbox
            # badge directly beneath the archive badge.
            if was_sandbox:
                sandbox_badge = tk.Label(card, text="sandbox", bg="#b45309", fg="#ffffff",
                                         font=("Arial", badge_font_size),
                                         padx=3, pady=0)
                sandbox_badge._is_accent_bar = True
                sandbox_badge.place(relx=1.0, y=badge_height + 1, anchor="ne")
        elif is_sandbox:
            # Sandbox badge
            badge_font_size = max(5, int(5 * font_scale))
            sandbox_badge = tk.Label(card, text="sandbox", bg="#b45309", fg="#ffffff",
                                     font=("Arial", badge_font_size),
                                     padx=3, pady=0)
            sandbox_badge._is_accent_bar = True  # skip during highlight recolor
            sandbox_badge.place(relx=1.0, y=0, anchor="ne")

        # Project name - allow up to 3 lines of wrapping
        project_name = project.get("project_name", "")
        name_label = tk.Label(card, text=project_name, bg=card_bg, fg=title_fg,
                             font=("Arial", name_size, "bold"), wraplength=card_size-10,
                             justify=tk.CENTER)
        name_label.grid(row=1, column=0, sticky="n", pady=(int(8 * font_scale), 0))

        # Web variant indicator: plain small text above the subtitle so the
        # user sees WordPress vs. Static at a glance without an extra badge.
        if type_info.get("category") == "Web":
            try:
                folder = self._resolve_project_folder(project)
                is_wp = is_wordpress_project(folder)
            except Exception:
                is_wp = False
            variant_text = "WordPress" if is_wp else "Static"
            variant_fg = "#58a6ff" if is_wp else sub_fg
            variant_label = tk.Label(card, text=variant_text, bg=card_bg,
                                     fg=variant_fg, font=("Arial", small_size))
            variant_label.grid(row=2, column=0, sticky="s")

        # Subtitle - location for Photo, client name for others
        if project_type == "Photo":
            location = project.get("metadata", {}).get("location", "")
            subtitle = f"📍 {location}" if location else ""
        else:
            subtitle = project.get("client_name", "")
        subtitle_display = subtitle[:client_max]
        if len(subtitle) > client_max:
            subtitle_display += "..."
        client_label = tk.Label(card, text=subtitle_display, bg=card_bg, fg=sub_fg,
                               font=("Arial", small_size))
        client_label.grid(row=3, column=0, sticky="s")

        # Date - compact format, pinned to bottom
        date_str = project.get("date_created", "")
        if len(date_str) > 7:
            date_str = date_str[2:]  # Remove century: 2025-12-29 -> 25-12-29
        date_label = tk.Label(card, text=date_str, bg=card_bg,
                             fg=sub_fg, font=("Arial", small_size))
        date_label.grid(row=4, column=0, sticky="s", pady=(0, int(6 * font_scale)))

        # Bind click event to all card elements
        # Store index in closure for click handler
        card_index = len(self.grid_cards)  # Current index (before this card is added)

        def on_card_click(e, p=project, idx=card_index):
            ctrl = e.state & 0x4   # Ctrl held
            shift = e.state & 0x1  # Shift held

            if ctrl:
                # Toggle this card in selection
                if idx in self.grid_selected_indices:
                    self.grid_selected_indices.discard(idx)
                    if self.grid_selected_indices:
                        self.grid_selected_index = max(self.grid_selected_indices)
                    else:
                        self.grid_selected_index = -1
                else:
                    self.grid_selected_indices.add(idx)
                    self.grid_selected_index = idx
            elif shift and self.grid_selected_index >= 0:
                # Range select from last selected to this card
                start = min(self.grid_selected_index, idx)
                end = max(self.grid_selected_index, idx)
                self.grid_selected_indices = set(range(start, end + 1))
                self.grid_selected_index = idx
            else:
                # Normal click - single select
                self.grid_selected_indices = {idx}
                self.grid_selected_index = idx

            # Update details for last clicked
            self.selected_project = p
            self._display_project_details(p)
            self._highlight_grid_cards()
            self._update_grid_count_label()
            # Set focus to canvas for keyboard navigation
            self.grid_canvas.focus_set()

        def on_card_double_click(e, p=project, idx=card_index):
            self.grid_selected_index = idx
            self.grid_selected_indices = {idx}
            self.selected_project = p
            self._open_folder()

        card.bind("<Button-1>", on_card_click)
        card.bind("<Double-1>", on_card_double_click)
        for child in card.winfo_children():
            child.bind("<Button-1>", on_card_click)
            child.bind("<Double-1>", on_card_double_click)
            for grandchild in child.winfo_children():
                grandchild.bind("<Button-1>", on_card_click)
                grandchild.bind("<Double-1>", on_card_double_click)

        return card

    def _update_grid_count_label(self):
        """Update count label based on grid selection."""
        total = len(self.grid_projects)
        selected = len(self.grid_selected_indices)
        if selected > 0:
            self.count_label.config(text=f"{selected}/{total}")
        else:
            self.count_label.config(text=f"{total}")

    def _highlight_grid_cards(self):
        """Highlight all selected cards and unhighlight others."""
        for i, card in enumerate(self.grid_cards):
            if not card.winfo_exists():
                continue
            try:
                is_selected = i in self.grid_selected_indices
                default_bg = card._card_bg if hasattr(card, '_card_bg') else "#1c2128"
                bg = "#2d333b" if is_selected else default_bg
                card.configure(bg=bg)
                for child in card.winfo_children():
                    # Skip accent bar - it keeps its category color
                    if hasattr(child, '_is_accent_bar'):
                        continue
                    child.configure(bg=bg)
                    for grandchild in child.winfo_children():
                        grandchild.configure(bg=bg)
            except tk.TclError:
                continue

        # Scroll to make last selected card visible
        if self.grid_selected_index >= 0:
            self._scroll_to_grid_card(self.grid_selected_index)

    def _scroll_to_grid_card(self, index: int):
        """Scroll to make card at index visible."""
        if index >= 0 and index < len(self.grid_cards):
            card = self.grid_cards[index]
            if not card.winfo_exists():
                return
            try:
                self.grid_canvas.update_idletasks()
                card_y = card.winfo_y()
                card_height = card.winfo_height()
                canvas_height = self.grid_canvas.winfo_height()
                bbox = self.grid_canvas.bbox("all")
                if bbox:
                    content_height = bbox[3] - bbox[1]
                    if content_height > canvas_height:
                        scroll_top = self.grid_canvas.yview()[0] * content_height
                        scroll_bottom = scroll_top + canvas_height
                        if card_y < scroll_top:
                            self.grid_canvas.yview_moveto(card_y / content_height)
                        elif card_y + card_height > scroll_bottom:
                            self.grid_canvas.yview_moveto((card_y + card_height - canvas_height) / content_height)
            except tk.TclError:
                pass

    def _highlight_grid_card(self, index: int):
        """Highlight the card at given index and unhighlight others (single select)."""
        self.grid_selected_indices = {index} if index >= 0 else set()
        self._highlight_grid_cards()
        self._update_grid_count_label()

    def _on_grid_left(self, event):
        """Navigate left in grid."""
        if not self.grid_projects:
            return "break"
        if self.grid_selected_index > 0:
            self.grid_selected_index -= 1
            self._select_grid_card(self.grid_selected_index)
        return "break"

    def _on_grid_right(self, event):
        """Navigate right in grid."""
        if not self.grid_projects:
            return "break"
        if self.grid_selected_index < len(self.grid_projects) - 1:
            self.grid_selected_index += 1
            self._select_grid_card(self.grid_selected_index)
        return "break"

    def _on_grid_up(self, event):
        """Navigate up in grid (previous row)."""
        if not self.grid_projects:
            return "break"
        new_index = self.grid_selected_index - self.grid_cols
        if new_index >= 0:
            self.grid_selected_index = new_index
            self._select_grid_card(self.grid_selected_index)
        return "break"

    def _on_grid_down(self, event):
        """Navigate down in grid (next row)."""
        if not self.grid_projects:
            return "break"
        new_index = self.grid_selected_index + self.grid_cols
        if new_index < len(self.grid_projects):
            self.grid_selected_index = new_index
            self._select_grid_card(self.grid_selected_index)
        return "break"

    def _select_grid_card(self, index: int):
        """Select card at index and update details."""
        if 0 <= index < len(self.grid_projects):
            self.selected_project = self.grid_projects[index]
            self._display_project_details(self.selected_project)
            self._highlight_grid_card(index)

    def _on_list_up(self, event=None):
        """Navigate up in list view (previous tree item)."""
        if not self.tree_item_to_project:
            return "break"
        items = self.project_tree.get_children()
        if not items:
            return "break"
        selection = self.project_tree.selection()
        if not selection:
            target = items[0]
        else:
            target = self.project_tree.prev(selection[0]) or selection[0]
        self.project_tree.selection_set(target)
        self.project_tree.focus(target)
        self.project_tree.see(target)
        return "break"

    def _on_list_down(self, event=None):
        """Navigate down in list view (next tree item)."""
        if not self.tree_item_to_project:
            return "break"
        items = self.project_tree.get_children()
        if not items:
            return "break"
        selection = self.project_tree.selection()
        if not selection:
            target = items[0]
        else:
            target = self.project_tree.next(selection[0]) or selection[0]
        self.project_tree.selection_set(target)
        self.project_tree.focus(target)
        self.project_tree.see(target)
        return "break"

    def _toggle_view_mode(self):
        """Flip between list and grid view (bound to T)."""
        new_view = "grid" if self.view_mode.get() == "list" else "list"
        self.view_mode.set(new_view)
        self._switch_view()

    def _on_enter_key(self, event):
        """Handle Enter key to open selected project folder."""
        if self.selected_project:
            self._open_folder()
        return "break"

    def _on_search_changed(self, *args):
        """Handle search query change."""
        self.refresh_project_list()

    def _on_filter_changed(self, event=None):
        """Handle filter status change."""
        # Save filter preference
        self.settings.set("filter_statuses", list(self._get_active_statuses()))
        # Update button styles
        self._update_filter_button_styles()
        self.refresh_project_list()

    def _get_active_statuses(self) -> set:
        """Get the set of currently enabled status filters."""
        return {k for k, v in self.filter_toggles.items() if v.get()}

    def _update_filter_button_styles(self):
        """Update filter button visual states based on current toggles.

        Active / Archive use the blue accent when on. Sandbox uses its own
        orange accent to reinforce that it's an orthogonal flag rather than
        another status option.
        """
        if not hasattr(self, 'filter_buttons'):
            return
        for value, btn in self.filter_buttons.items():
            is_on = self.filter_toggles[value].get()
            if value == "sandbox":
                if is_on:
                    btn.configure(bg="#b45309", fg="#ffffff")
                else:
                    btn.configure(bg="#1c2128", fg="white")
            else:
                if is_on:
                    btn.configure(bg="#58a6ff", fg="white")
                else:
                    btn.configure(bg="#1c2128", fg="white")

    def _update_physical_subtype_styles(self):
        """Update physical subtype button visual states."""
        if not hasattr(self, 'physical_subtype_buttons'):
            return
        current = self.filter_physical_subtype.get()
        for value, btn in self.physical_subtype_buttons.items():
            if value == current:
                btn.configure(bg="#58a6ff", fg="white")
            else:
                btn.configure(bg="#1c2128", fg="white")

    def _update_physical_subtype_visibility(self):
        """Show/hide physical subtype pills based on category and scope."""
        if not hasattr(self, 'physical_subtype_frame'):
            return
        # Physical pills are only meaningful when Physical is the single selection.
        is_physical = self.selected_categories == {"Physical"}
        # Show whenever client scope is enabled (alone or alongside personal).
        is_work_or_all = "client" in self.filter_scopes

        if is_physical and is_work_or_all:
            self.physical_subtype_frame.grid(row=1, column=0, columnspan=5, sticky=tk.W, pady=(2, 0))
        else:
            self.physical_subtype_frame.grid_forget()
            # Reset filter when hidden so it doesn't silently filter
            if self.filter_physical_subtype.get() != "all":
                self.filter_physical_subtype.set("all")
                self._update_physical_subtype_styles()

    def set_scopes(self, scopes):
        """Set the scope filter set (called from external UI like pipeline).

        When wired to a session, writes through the session (which then
        notifies the listener and updates our cached filter_scopes). Without
        a session, updates the cache directly.
        """
        new_scopes = {s for s in scopes if s in ("personal", "client")}
        if self.session is not None:
            self.session.set_scopes(new_scopes)
        else:
            self.filter_scopes = new_scopes
            self._on_scope_changed()

    def _on_session_change(self, change):
        """Listener for the shared SessionState. Only scopes flow in via
        session today; categories are pushed from the hub through
        `set_categories`."""
        from ui_session_state import CHANGE_SCOPES
        if change == CHANGE_SCOPES:
            self.filter_scopes = set(self.session.scopes)
            self._on_scope_changed(persist=False)

    def _on_scope_changed(self, event=None, persist=True):
        """Handle scope filter change."""
        # Persist scope preference as a list, only when we own persistence
        # (no session). When a session drives us, persistence lives there.
        if persist and self.session is None:
            self.settings.set("filter_scopes", sorted(self.filter_scopes))
        # Update button styles (if buttons exist in this UI)
        self._update_scope_button_styles()
        self._update_physical_subtype_visibility()
        self.refresh_project_list()

    def _update_scope_button_styles(self):
        """Update scope button visual states based on current selection."""
        if not hasattr(self, 'scope_buttons') or not self.scope_buttons:
            return
        for value, btn in self.scope_buttons.items():
            if value in self.filter_scopes:
                # Selected state - highlighted
                btn.configure(bg="#58a6ff", fg="white")
            else:
                # Unselected state
                btn.configure(bg="#1c2128", fg="white")

    def _get_column_header(self, col: str) -> str:
        """Get column header text with sort indicator if applicable."""
        name = self.column_names.get(col, col)
        if col == self.sort_column:
            indicator = " ▼" if self.sort_reverse else " ▲"
            return name + indicator
        return name

    def _update_column_headers(self):
        """Update all column headers to reflect current sort state."""
        for col in ["date", "client", "project"]:
            self.project_tree.heading(col, text=self._get_column_header(col))

    def _on_column_click(self, col: str):
        """Handle column header click for sorting."""
        if col == self.sort_column:
            # Toggle sort direction
            self.sort_reverse = not self.sort_reverse
        else:
            # New column, default to descending for date, ascending for others
            self.sort_column = col
            self.sort_reverse = (col == "date")

        # Save settings
        self.settings.set("sort_column", self.sort_column)
        self.settings.set("sort_reverse", self.sort_reverse)

        # Update headers and refresh
        self._update_column_headers()
        self.refresh_project_list()

    def _get_column_at_x(self, x: int) -> str:
        """Get the column ID at the given x coordinate."""
        # Get column widths in display order
        total = 0
        for col in self.column_order:
            width = self.project_tree.column(col, "width")
            if x < total + width:
                return col
            total += width
        return None

    def _on_tree_click(self, event):
        """Handle click on treeview - detect header clicks for dragging."""
        region = self.project_tree.identify_region(event.x, event.y)
        if region == "heading":
            col = self._get_column_at_x(event.x)
            if col:
                self._drag_column = col
                self._drag_start_x = event.x

    def _on_tree_drag(self, event):
        """Handle dragging on treeview header."""
        if self._drag_column and self._drag_start_x is not None:
            # Check if we've moved enough to consider it a drag
            if abs(event.x - self._drag_start_x) > 20:
                # Change cursor to indicate dragging
                self.project_tree.configure(cursor="fleur")

    def _on_tree_release(self, event):
        """Handle mouse release - complete column reorder if dragging."""
        if self._drag_column and self._drag_start_x is not None:
            drag_distance = event.x - self._drag_start_x

            # Only reorder if we dragged far enough
            if abs(drag_distance) > 20:
                target_col = self._get_column_at_x(event.x)
                if target_col and target_col != self._drag_column:
                    # Reorder columns
                    new_order = self.column_order.copy()
                    old_idx = new_order.index(self._drag_column)
                    new_idx = new_order.index(target_col)

                    # Move the column
                    new_order.pop(old_idx)
                    new_order.insert(new_idx, self._drag_column)

                    self.column_order = new_order
                    self.project_tree["displaycolumns"] = self.column_order

                    # Save settings
                    self.settings.set("column_order", self.column_order)

        # Reset drag state
        self._drag_column = None
        self._drag_start_x = None
        self.project_tree.configure(cursor="")

    def _on_project_selected(self, event):
        """Handle project selection."""
        selection = self.project_tree.selection()

        # Update selection count in count label
        total = len(self.tree_item_to_project)
        selected = len(selection)
        if selected > 0:
            self.count_label.config(text=f"{selected}/{total}")
        else:
            self.count_label.config(text=f"{total}")

        if not selection:
            self.selected_project = None
            self._clear_details()
            return

        # Get project from our mapping (show details for last selected)
        item = selection[-1]
        project = self.tree_item_to_project.get(item)

        if not project:
            return

        self.selected_project = project
        self._display_project_details(project)

    def _clear_details(self):
        """Clear detail panel."""
        for label in self.detail_labels.values():
            label.config(text="-")

        # Clear path labels and tracking variables
        self.active_path_label.config(text="-")
        self.raw_path_label.config(text="-")
        self.website_label.config(text="-")
        self._current_active_path = None
        self._current_raw_path = None
        self.active_path_frame.pack(fill=tk.X, pady=2)  # Show by default

        self.open_btn.config(state=tk.DISABLED)
        self.rename_btn.config(state=tk.DISABLED)
        self._set_deck_btn_state(tk.DISABLED)
        self.archive_btn.config(state=tk.DISABLED)
        self.unarchive_btn.config(state=tk.DISABLED)
        self.promote_btn.config(state=tk.DISABLED)
        self.log_note_btn.config(state=tk.DISABLED)
        self.project_details_btn.config(state=tk.DISABLED)
        self.order_details_btn.config(state=tk.DISABLED)
        self.invoice_btn.config(state=tk.DISABLED)

        # Hide and clear the project-context Actions section.
        self._populate_actions_section(None)

    def _display_project_details(self, project: Dict):
        """Display project details in right panel."""
        # Update detail labels
        for key, label in self.detail_labels.items():
            if key == "location":
                location = project.get("metadata", {}).get("location", "")
                if location:
                    value = f"📍 {location}"
                else:
                    value = "-"
            elif key == "order_number":
                order_num = project.get("metadata", {}).get("woo_order_number", "")
                value = f"#{order_num}" if order_num else "-"
            elif key == "project_type":
                raw_type = project.get(key, "")
                type_info = project_type_info(raw_type)
                value = type_info["name"]
                # For Web projects, annotate the type with the WP/static variant
                # so the user can see at a glance which action set will apply.
                if type_info.get("category") == "Web":
                    folder = self._resolve_project_folder(project)
                    variant = "WordPress" if is_wordpress_project(folder) else "Static"
                    value = f"{value} — {variant}"
            else:
                value = project.get(key, "")

            label.config(text=str(value))

        # Get status and stored path
        status = project.get("status", "active")
        stored_path = project.get("path", "")

        # Store paths for clipboard copy
        self._current_raw_path = stored_path
        self._current_active_path = None

        # Handle paths based on project status
        if status == "active":
            # Show Active Path frame
            self.active_path_frame.pack(fill=tk.X, pady=2)

            # Get the active path (work drive version)
            try:
                settings = get_rack_settings()
                active_path = settings.convert_to_work_drive_path(stored_path)
                self._current_active_path = active_path
                self.active_path_label.config(text=active_path)
            except Exception:
                self.active_path_label.config(text=stored_path)
                self._current_active_path = stored_path

            # RAW path is the stored D:\ path or derive it from active path
            # If stored path is already the work drive path, convert back to D:\ path
            work_drive = settings.get_work_drive().upper()
            active_base = settings.get_active_base()
            if stored_path.upper().startswith(work_drive):
                raw_path = active_base + stored_path[len(work_drive):]
            else:
                raw_path = stored_path
            self._current_raw_path = raw_path
            self.raw_path_label.config(text=raw_path)
        else:
            # Archived: Keep Active Path frame visible but blank
            self.active_path_frame.pack(fill=tk.X, pady=2)
            self.active_path_label.config(text="-")
            self._current_active_path = None
            self.raw_path_label.config(text=stored_path)
            self._current_raw_path = stored_path

        website_url = get_website_url(project)
        self.website_label.config(text=website_url if website_url else "Click to set")

        # Enable buttons
        self.open_btn.config(state=tk.NORMAL)
        self.rename_btn.config(state=tk.NORMAL)
        self._set_deck_btn_state(tk.NORMAL)

        # Project Details is available for any project, any category, but
        # only once a details file actually exists for it. Order Details /
        # Invoice only apply once a project is linked to a WooCommerce
        # order (metadata.woo_order_number/woo_order_id).
        self.project_details_btn.config(state=tk.NORMAL if project_details_exists(project) else tk.DISABLED)
        has_order_link = bool(project.get("metadata", {}).get("woo_order_number"))
        self.order_details_btn.config(state=tk.NORMAL if has_order_link else tk.DISABLED)
        self.invoice_btn.config(state=tk.NORMAL if has_order_link else tk.DISABLED)

        # Enable archive/unarchive/promote based on status. Promote is offered
        # for active projects whose sandbox flag is set, to clear the flag.
        is_sandbox_flag = bool(self.selected_project.get("metadata", {}).get("is_sandbox", False))
        if status == "active":
            self.archive_btn.config(state=tk.NORMAL)
            self.unarchive_btn.config(state=tk.DISABLED)
            self.promote_btn.config(state=tk.NORMAL if is_sandbox_flag else tk.DISABLED)
        elif status == "archived":
            self.archive_btn.config(state=tk.DISABLED)
            self.unarchive_btn.config(state=tk.NORMAL)
            self.promote_btn.config(state=tk.DISABLED)

        # Populate the project-context Actions section based on project_type.
        self._populate_actions_section(project)

        # The note-log button is enabled whenever a project is selected; the
        # target category file is derived from the project's archive_category.
        self.log_note_btn.config(state=tk.NORMAL)

    def _resolve_project_folder(self, project: Dict) -> str:
        """Return the on-disk folder for a project, preferring the work-drive
        path for active projects and falling back to the stored path."""
        stored_path = project.get("path", "")
        status = project.get("status", "active")
        if status != "active":
            return stored_path
        try:
            settings = get_rack_settings()
            folder = settings.convert_to_work_drive_path(stored_path)
            if not Path(folder).exists():
                folder = stored_path
            return folder
        except Exception:
            return stored_path

    def _project_actions(self, project: Dict):
        """Return [(category_key, script_key, subcat_key, script_data)] for
        every project-context script applicable to the given project."""
        if not project:
            return []
        project_type = project.get("project_type", "")
        archive_cat = archive_category_for(project_type)
        if not archive_cat:
            return []
        cat_key = archive_cat.upper()
        cat_data = PIPELINE_CATEGORIES.get(cat_key, {})

        # Resolve once; predicate lookups below are memoized per-call so any
        # given applies_when key only fires its filesystem probe once even when
        # several actions share it.
        folder = self._resolve_project_folder(project)
        predicate_cache: Dict[str, bool] = {}

        def _check_applies_when(name: str) -> bool:
            if name not in predicate_cache:
                fn = PROJECT_ACTION_PREDICATES.get(name)
                if fn is None:
                    logger.warning(f"Unknown applies_when predicate: {name!r}")
                    predicate_cache[name] = True  # fail-open: show the button
                else:
                    try:
                        predicate_cache[name] = bool(fn(project, folder))
                    except Exception as e:
                        logger.warning(f"applies_when {name!r} raised: {e}")
                        predicate_cache[name] = True
            return predicate_cache[name]

        def _matches(script_data):
            if script_data.get("context") != "project":
                return False
            allowed = script_data.get("project_types") or []
            if allowed and project_type not in allowed:
                return False
            predicate = script_data.get("applies_when")
            if predicate and not _check_applies_when(predicate):
                return False
            return True

        actions = []
        for script_key, script_data in cat_data.get("scripts", {}).items():
            if _matches(script_data):
                actions.append((cat_key, script_key, None, script_data))
        for subcat_key, subcat_data in cat_data.get("subcategories", {}).items():
            for script_key, script_data in subcat_data.get("scripts", {}).items():
                if _matches(script_data):
                    actions.append((cat_key, script_key, subcat_key, script_data))
        return actions

    def _populate_actions_section(self, project: Optional[Dict]):
        """Rebuild the Actions section for the selected project, or hide it."""
        for btn in self._action_buttons:
            btn.destroy()
        self._action_buttons = []

        actions = self._project_actions(project) if project else []
        # Visit Website isn't a category script — it's a fixed action shown
        # for every Web project (the URL lives in the project's own
        # Project_Details.txt, via the Website field above), so it's
        # appended here rather than going through _project_actions. Shown
        # even before a URL is set: pressing it then prompts for one.
        project_category = archive_category_for(project.get("project_type", "")) if project else None
        show_website_btn = project_category == "Web"
        if not actions and not show_website_btn:
            self.actions_frame.pack_forget()
            return

        self.actions_frame.pack(side=tk.LEFT, fill=tk.Y, padx=0)

        for category_key, script_key, subcat_key, script_data in actions:
            color = category_color(category_key.capitalize()) or "#238636"
            label = f"{script_data.get('icon', '')} {script_data.get('name', script_key)}".strip()
            btn = make_flat_button(
                self.actions_container, label,
                lambda sd=script_data: self._run_project_action(sd),
                bg=color, fg="white", font=("Arial", 9),
                padx=15, pady=6, anchor="w",
            )
            btn.pack(side=tk.TOP, fill=tk.X, pady=2)
            self._action_buttons.append(btn)

        if show_website_btn:
            website_btn = make_flat_button(
                self.actions_container, "🌐 Visit Website", self._open_website,
                bg="#ea580c", fg="white", font=("Arial", 9),
                padx=15, pady=6, anchor="w",
            )
            website_btn.pack(side=tk.TOP, fill=tk.X, pady=2)
            self._action_buttons.append(website_btn)

    def _log_to_category_notes(self):
        """Append a timestamped, project-tagged line to the category notes
        scratchpad (notes/<category>_notes.txt). Per-project notes in the DB
        remain canonical; this is for cheap running-log entries on the
        category, e.g. "tried X on ProjectY today"."""
        if not self.selected_project:
            return

        project_type = self.selected_project.get("project_type", "")
        archive_cat = archive_category_for(project_type) or "Global"
        notes_dir = SCRIPT_DIR / "notes"
        notes_dir.mkdir(parents=True, exist_ok=True)
        note_path = notes_dir / f"{archive_cat.lower()}_notes.txt"

        client = self.selected_project.get("client_name", "?")
        project_name = self.selected_project.get("project_name", "?")
        prompt = (
            f"Append a note to {note_path.name}\n"
            f"Project: {client} / {project_name}"
        )
        text = simpledialog.askstring("Log to category notes", prompt, parent=self.root)
        if text is None:
            return
        text = text.strip()
        if not text:
            return

        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"# Notes for {archive_cat}\n\n"
        line = f"[{ts}] {client} / {project_name} — {text}\n"
        try:
            new_file = not note_path.exists()
            with open(note_path, "a", encoding="utf-8") as f:
                if new_file:
                    f.write(header)
                f.write(line)
            self._update_status(f"Logged to {note_path.name}")
            logger.info(f"Appended to {note_path}: {line.rstrip()}")
        except Exception as e:
            logger.error(f"Failed to log to {note_path}: {e}")
            messagebox.showerror("Log failed", f"Could not write note:\n{e}")

    def open_deck_for_selected(self):
        """Open (or focus) the quick-actions deck for the selected project."""
        project = self.selected_project
        if not project:
            return
        key = project.get("id")
        existing = self._project_decks.get(key)
        if existing is not None and existing.winfo_exists():
            existing.focus_deck()
            return
        deck = ProjectDeckWindow(
            self.root, dict(project), tracker_app=self,
            offset_index=len(self._project_decks),
            on_close=lambda k=key: self._project_decks.pop(k, None),
        )
        self._project_decks[key] = deck

    def _run_project_action(self, script_data: Dict):
        """Run a project-context script for the selected project."""
        if not self.selected_project:
            return
        self._run_project_action_for(self.selected_project, script_data)

    def _run_project_action_for(self, project: Dict, script_data: Dict):
        """Run a project-context script, passing the given project's folder
        as the first positional argument."""
        script_path = script_data.get("path")
        if not script_path:
            logger.warning(f"Project action has no path: {script_data.get('name')}")
            return

        import subprocess
        folder = self._resolve_project_folder(project)
        extra = script_data.get("script_args") or []
        args = [sys.executable, script_path, folder, *extra]
        try:
            subprocess.Popen(args)
            logger.info(f"Launched {script_data.get('name')} for {folder}")
            self._update_status(f"Launched: {script_data.get('name')}")
        except Exception as e:
            logger.error(f"Failed to launch {script_data.get('name')}: {e}")
            messagebox.showerror("Action failed", f"Could not start tool:\n{e}")

    def _copy_path_to_clipboard(self, path_type: str):
        """Copy the specified path to clipboard."""
        if path_type == "active" and self._current_active_path:
            path = self._current_active_path
        elif path_type == "raw" and self._current_raw_path:
            path = self._current_raw_path
        else:
            return

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self._update_status(f"Copied: {path}")
            logger.debug(f"Copied path to clipboard: {path}")
        except Exception as e:
            logger.error(f"Failed to copy to clipboard: {e}")

    def _get_path_for_type(self, path_type: str) -> Optional[str]:
        """Get the path string for a given path type."""
        if path_type == "active" and self._current_active_path:
            return self._current_active_path
        elif path_type == "raw" and self._current_raw_path:
            return self._current_raw_path
        return None

    def _open_path_in_explorer(self, path_type: str):
        """Open the specified path in the system file explorer."""
        path_str = self._get_path_for_type(path_type)
        if not path_str:
            return

        path = Path(path_str)

        # On WSL, convert to Windows path for Windows file managers
        try:
            custom_file_manager = self.settings.get("file_manager", "")

            if custom_file_manager:
                import subprocess
                open_path = str(path)

                if sys.platform != "win32" and custom_file_manager.lower().endswith(".exe"):
                    if open_path.startswith("/mnt/"):
                        parts = open_path.split("/")
                        if len(parts) >= 3:
                            drive_letter = parts[2].upper()
                            rest = "/".join(parts[3:])
                            open_path = f"{drive_letter}:\\{rest}".replace("/", "\\")

                subprocess.Popen([custom_file_manager, open_path])
            elif sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')

            self._update_status(f"Opened: {path}")
            logger.info(f"Opened path from label: {path}")

        except Exception as e:
            logger.error(f"Failed to open path: {e}")
            messagebox.showerror("Error", f"Failed to open folder:\n{str(e)}")

    def _open_website(self):
        """Open the selected project's live website URL in the default
        browser, or prompt to set one if it hasn't been set yet. The URL
        lives in the project's own Project_Details.txt (a "Website: <url>"
        line) — normally filled in at project creation, but this is the
        fallback for projects that don't have one yet."""
        if not self.selected_project:
            return
        url = get_website_url(self.selected_project)
        if url:
            webbrowser.open(url)
            self._update_status(f"Opened: {url}")
        else:
            self._edit_website_url()

    def _edit_website_url(self):
        """Set, change, or clear the selected project's live website URL by
        rewriting the "Website: <url>" line in Project_Details.txt — that
        file is the single source of truth for this field, so hand-editing
        it there also works and is picked up the next time it's read."""
        if not self.selected_project:
            return
        project = self.selected_project
        current = get_website_url(project)
        new_url = simpledialog.askstring(
            "Website URL",
            "Live website URL (leave blank to clear):",
            initialvalue=current,
            parent=self.root,
        )
        if new_url is None:
            return
        new_url = new_url.strip()
        if new_url == current:
            return
        set_website_url(project, new_url)
        self._display_project_details(project)

    def _open_folder(self):
        """Open project folder in file explorer."""
        if not self.selected_project:
            return
        self._open_project_folder(self.selected_project)

    def _open_project_folder(self, project: Dict):
        """Open the given project's folder in file explorer."""
        # Get path from project
        stored_path = project["path"]
        status = project.get("status", "active")

        # For active projects, convert to configured work drive path
        if status == "active":
            try:
                settings = get_rack_settings()
                open_path = settings.convert_to_work_drive_path(stored_path)
                path = Path(open_path)

                # If converted path doesn't exist, fall back to stored path
                if not path.exists():
                    logger.debug(f"Work drive path not found, using stored path: {stored_path}")
                    path = Path(stored_path)
            except Exception as e:
                logger.warning(f"Path conversion failed, using stored path: {e}")
                path = Path(stored_path)
        else:
            # Archived projects use stored path directly
            path = Path(stored_path)

        if not path.exists():
            messagebox.showerror(
                "Folder Not Found",
                f"Project folder does not exist:\n{path}"
            )
            return

        try:
            # Check for custom file manager setting
            custom_file_manager = self.settings.get("file_manager", "")

            if custom_file_manager:
                # Use custom file manager
                import subprocess
                open_path = str(path)

                # If running in WSL with a Windows file manager, convert path to Windows format
                if sys.platform != "win32" and custom_file_manager.lower().endswith(".exe"):
                    # Convert /mnt/d/... to D:\...
                    if open_path.startswith("/mnt/"):
                        parts = open_path.split("/")
                        if len(parts) >= 3:
                            drive_letter = parts[2].upper()
                            rest = "/".join(parts[3:])
                            open_path = f"{drive_letter}:\\{rest}".replace("/", "\\")

                subprocess.Popen([custom_file_manager, open_path])
            elif sys.platform == "win32":
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')

            logger.info(f"Opened folder: {path}")

        except Exception as e:
            logger.error(f"Failed to open folder: {e}")
            messagebox.showerror("Error", f"Failed to open folder:\n{str(e)}")


    def _rename_project(self):
        """Rename the selected project's display name and, on disk, its folder."""
        if not self.selected_project:
            return

        project = self.selected_project
        current_name = project.get("project_name", "")
        order_number = RenameManager.is_order_linked(project)

        new_name = simpledialog.askstring(
            "Rename Project",
            "New project name:",
            initialvalue=current_name,
            parent=self.root,
        )
        if new_name is None:
            return
        new_name = new_name.strip()
        if not new_name or new_name == current_name:
            return

        # For WooCommerce-order-linked projects, show the computed folder
        # name (with the order number appended) before doing anything, so
        # the order number's presence is never a silent side effect.
        if order_number:
            try:
                folder = RenameManager._resolve_folder(project)
                new_folder_name = RenameManager.build_new_folder_name(
                    project, new_name, folder.name
                )
            except ValueError as e:
                messagebox.showerror("Rename Failed", str(e))
                return
            response = messagebox.askyesno(
                "Confirm Rename",
                f"This project is linked to WooCommerce order #{order_number}.\n\n"
                f"The folder will be renamed to:\n{new_folder_name}\n\n"
                "The order number stays in the folder name so future "
                "WooCommerce syncs still recognize it. Continue?"
            )
            if not response:
                return

        self._update_status("Renaming project...")
        success = RenameManager.rename_project(project, new_name, self.db)

        if success:
            self._update_status("Project renamed successfully")
            self.refresh_project_list()
            self._clear_details()
        else:
            self._update_status("Rename failed")

    def _open_document(self, path: Path):
        """Open a single file with the OS-default application."""
        try:
            if sys.platform == "win32":
                os.startfile(str(path))
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
            logger.info(f"Opened document: {path}")
        except Exception as e:
            logger.error(f"Failed to open document {path}: {e}")
            messagebox.showerror("Error", f"Failed to open file:\n{str(e)}")

    def _open_project_details(self):
        """Open (or migrate-then-open) the selected project's own details
        file. Works for any category — pure local file I/O, no network."""
        if not self.selected_project:
            return
        try:
            path = resolve_or_migrate_project_details(self.selected_project)
        except Exception as e:
            logger.error(f"Failed to resolve project details: {e}")
            messagebox.showerror("Project Details", f"Could not open project details:\n{str(e)}")
            return
        if not path:
            messagebox.showinfo("Project Details", "No project details file found for this project.")
            return
        self._open_document(path)

    def _fetch_and_open_document(self, fetch_fn, title: str, not_found_message: str):
        """Run fetch_fn(project) — may hit the network — in a background
        thread, then open the resulting path on the main thread. Shared by
        Order Details and Invoice, which both fetch from WooCommerce."""
        project = self.selected_project
        if not project:
            return

        def on_done(path=None, error=None):
            self._update_status("")
            if error is not None:
                messagebox.showerror(title, f"Could not open {title.lower()}:\n{error}")
            elif not path:
                messagebox.showinfo(title, not_found_message)
            else:
                self._open_document(path)

        def go():
            try:
                path = fetch_fn(project, db=self.db)
            except Exception as e:
                logger.error(f"Failed to resolve {title.lower()}: {e}")
                self.root.after(0, lambda: on_done(error=str(e)))
                return
            self.root.after(0, lambda: on_done(path=path))

        threading.Thread(target=go, daemon=True).start()

    def _open_order_details(self):
        """Open the selected project's linked WooCommerce order details,
        fetching them in the background if not already cached locally."""
        self._update_status("Fetching order details...")
        self._fetch_and_open_document(
            resolve_or_fetch_order_details, "Order Details",
            "No order details available for this project (order not "
            "reachable, or WooCommerce credentials aren't configured)."
        )

    def _open_invoice(self):
        """Open the selected project's linked WooCommerce invoice,
        downloading and caching it in the background if not already local."""
        self._update_status("Fetching invoice...")
        self._fetch_and_open_document(
            resolve_or_fetch_invoice, "Invoice",
            "No invoice available for this project yet (not generated "
            "on WooCommerce, or credentials aren't configured)."
        )

    def _archive_project(self):
        """Archive the selected project."""
        if not self.selected_project:
            return

        # Confirm
        response = messagebox.askyesno(
            "Confirm Archive",
            f"Archive this project?\n\n"
            f"Client: {self.selected_project.get('client_name')}\n"
            f"Project: {self.selected_project.get('project_name')}\n\n"
            f"The folder will be moved to {get_rack_settings().get_archive_base()}"
        )

        if not response:
            return

        # Archive
        self._update_status("Archiving project...")
        success = ArchiveManager.archive_project(self.selected_project, self.db)

        if success:
            self._update_status("Project archived successfully")
            self.refresh_project_list()
            self._clear_details()
        else:
            self._update_status("Archive failed")

    def _unarchive_project(self):
        """Un-archive the selected project."""
        if not self.selected_project:
            return

        # Confirm
        response = messagebox.askyesno(
            "Confirm Un-Archive",
            f"Restore this project from archive?\n\n"
            f"Client: {self.selected_project.get('client_name')}\n"
            f"Project: {self.selected_project.get('project_name')}\n\n"
            f"The folder will be moved back to its original location."
        )

        if not response:
            return

        # Un-archive
        self._update_status("Un-archiving project...")
        success = ArchiveManager.unarchive_project(self.selected_project, self.db)

        if success:
            self._update_status("Project restored successfully")
            self.refresh_project_list()
            self._clear_details()
        else:
            self._update_status("Un-archive failed")

    def _promote_project(self):
        """Promote a sandbox project to non-sandbox active.

        Sandbox is now a metadata flag (not a status), so promotion means
        clearing metadata.is_sandbox and moving the folder out of _Sandbox.
        Only offered for active projects that currently carry the flag.
        """
        if not self.selected_project:
            return

        project = self.selected_project
        metadata = project.get("metadata", {})
        if project.get("status") != "active" or not metadata.get("is_sandbox", False):
            return

        project_type = project.get("project_type", "")
        is_personal = metadata.get("is_personal", False)

        # Build the active directory (without _Sandbox)
        promote_metadata = {**metadata, "is_sandbox": False}
        active_dir = ArchiveManager._get_active_dir(project_type, is_personal, promote_metadata)

        response = messagebox.askyesno(
            "Promote to Active",
            f"Move this sandbox project to active?\n\n"
            f"Client: {project.get('client_name')}\n"
            f"Project: {project.get('project_name')}\n\n"
            f"The folder will be moved to:\n{active_dir}"
        )

        if not response:
            return

        try:
            source_path = Path(project["path"])

            # Try work drive path if source doesn't exist directly
            if not source_path.exists():
                try:
                    settings = get_rack_settings()
                    converted = settings.convert_to_work_drive_path(str(source_path))
                    alt_path = Path(converted)
                    if not alt_path.exists():
                        # Try platform path conversion
                        alt_path = _get_platform_path(str(source_path))
                    if alt_path.exists():
                        source_path = alt_path
                except Exception:
                    source_path = _get_platform_path(str(source_path))

            if not source_path.exists():
                messagebox.showerror(
                    "Folder Not Found",
                    f"Sandbox folder does not exist:\n{source_path}"
                )
                return

            active_dir.mkdir(parents=True, exist_ok=True)
            active_path = active_dir / source_path.name

            if active_path.exists():
                overwrite = messagebox.askyesno(
                    "Path Conflict",
                    f"Active location already exists:\n{active_path}\n\n"
                    "Do you want to overwrite it?"
                )
                if not overwrite:
                    return
                shutil.rmtree(active_path)

            self._update_status("Promoting to active...")
            logger.info(f"Promoting: {source_path} -> {active_path}")
            shutil.move(str(source_path), str(active_path))

            # Update database: clear sandbox flag, update path. Status is
            # already "active" — sandbox is a flag, not a separate status.
            metadata["is_sandbox"] = False
            project["metadata"] = metadata
            project["path"] = str(active_path)
            project["updated_at"] = datetime.now().isoformat()
            self.db.save()

            messagebox.showinfo(
                "Success",
                f"Project promoted to active:\n{active_path}"
            )

            logger.info(f"Successfully promoted project: {project['id']}")
            self._update_status("Project promoted to active")
            self.refresh_project_list()
            self._clear_details()

        except Exception as e:
            logger.error(f"Failed to promote project: {e}")
            messagebox.showerror(
                "Promote Failed",
                f"Failed to promote project:\n{str(e)}"
            )
            self._update_status("Promote failed")

    def _save_notes(self):
        """Save project notes."""
        if not self.selected_project:
            return

        notes = self.notes_text.get(1.0, tk.END).strip()

        try:
            self.db.update_project_notes(self.selected_project["id"], notes)
            self._update_status("Notes saved")
            logger.info(f"Saved notes for project: {self.selected_project['id']}")

            # Update selected project
            self.selected_project["notes"] = notes

        except Exception as e:
            logger.error(f"Failed to save notes: {e}")
            messagebox.showerror("Error", f"Failed to save notes:\n{str(e)}")

    def refresh_and_import(self, silent: bool = False):
        """
        Delete database and perform fresh import of all projects.

        Args:
            silent: If True, skip confirmation/result dialogs (used for F5 refresh)
        """
        if not silent:
            response = messagebox.askyesno(
                "Import Projects",
                "This will delete the existing database and perform a fresh import\n"
                "of all projects from your project directories.\n\n"
                "Continue?"
            )
            if not response:
                return

        self._update_status("Refreshing projects...")
        self.root.update_idletasks()

        try:
            # Delete existing database file
            if self.db.db_path.exists():
                os.remove(self.db.db_path)
                logger.info(f"Deleted old database: {self.db.db_path}")

            # Reinitialize database (creates fresh empty database)
            self.db = ProjectDatabase()

            # Run fresh import
            stats = ProjectImporter.scan_and_import(self.db, None)

            # Refresh list
            self.refresh_project_list()
            self._update_status(f"Imported {stats['imported']} projects", "success")

            # Show results dialog only if not silent
            if not silent:
                messagebox.showinfo(
                    "Import Complete",
                    f"Fresh import completed:\n\n"
                    f"Scanned: {stats['scanned']} folders\n"
                    f"Imported: {stats['imported']} projects\n"
                    f"Errors: {stats['errors']}"
                )

        except Exception as e:
            logger.error(f"Failed to import projects: {e}")
            if not silent:
                messagebox.showerror("Import Failed", f"Failed to import projects:\n{str(e)}")
            else:
                self._update_status(f"Import failed: {str(e)}", "error")

    def _import_projects(self):
        """Import existing projects from filesystem (with dialogs)."""
        self.refresh_and_import(silent=False)


def main():
    """Main entry point."""
    # Initialize logging first! Use MODULE_NAME to match get_logger() call
    setup_logging(MODULE_NAME)
    logger.info("Starting Project Tracker")

    root = tk.Tk()
    app = ProjectTrackerApp(root)
    root.bind('<Control-d>', lambda e: app.open_deck_for_selected())
    root.bind('<Control-D>', lambda e: app.open_deck_for_selected())

    try:
        root.mainloop()
    except KeyboardInterrupt:
        logger.info("Project Tracker closed by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise


if __name__ == "__main__":
    main()
