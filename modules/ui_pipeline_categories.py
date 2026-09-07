"""
UI Pipeline Categories — adapter that produces the legacy menu shape from the
unified pipeline_categories.CATEGORIES tree.

The menu format consumed by fastrak_hub / ui_keyboard_navigator is:

    {
        "AUDIO": {
            "name": "Audio",
            "description": "...",
            "icon": "🎵",
            "folder_path": "...",
            "scripts": {key: {name, path, description, icon}, ...},
            "subcategories": {KEY: {name, icon, scripts: {...}}, ...},
        },
        ...
    }

This module builds that shape at import time from pipeline_categories.CATEGORIES.
Anything category-related — colors, names, scripts, subtypes — should be edited
in pipeline_categories.py, not here.
"""

import os
from typing import Dict, Any

from rak_settings import get_rak_settings, join_native_path
from pipeline_categories import CATEGORIES, creative_categories

# Base script directory (relative to the main pipeline file)
SCRIPT_FILE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SCRIPT_FILE_DIR, "modules")

# Application constants
APP_NAME = "Pipeline Manager"
APP_VERSION = "0.5.0"
APP_ICON = None
LOGO_PATH = os.path.join(SCRIPT_FILE_DIR, "assets", "Logo_FlorianDheer_LogoWhite.png")
DEFAULT_CONFIG_PATH = os.path.join(
    os.path.expanduser("~"), "AppData", "Local", "PipelineManager", "config.json"
)


def _resolve_folder_path(category_name: str, work_path_key) -> str:
    """Build folder_path for a category. Business uses the _LIBRARY drive root,
    Sandbox uses the _Sandbox drive root (same root the Sandbox Browser panel
    shows), Global has no folder, others use settings.get_work_path(work_path_key)."""
    if category_name == "Business":
        return join_native_path(get_rak_settings().get_work_drive(), "_LIBRARY")
    if category_name == "Sandbox":
        return join_native_path(get_rak_settings().get_work_drive(), "_Sandbox")
    if work_path_key is None:
        return ""
    return get_rak_settings().get_work_path(work_path_key)


def _script_entry(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a pipeline_categories script spec into menu format."""
    out: Dict[str, Any] = {
        "name": spec["name"],
        "description": spec.get("description", ""),
        "icon": spec.get("icon", ""),
    }
    if "module" in spec:
        out["path"] = os.path.join(SCRIPTS_DIR, f"{spec['module']}.py")
    if "url" in spec:
        out["url"] = spec["url"]
    if "context" in spec:
        out["context"] = spec["context"]
    if "project_types" in spec:
        out["project_types"] = list(spec["project_types"])
    if "applies_when" in spec:
        out["applies_when"] = spec["applies_when"]
    if "script_args" in spec:
        out["script_args"] = list(spec["script_args"])
    return out


def _scripts_dict(script_list) -> Dict[str, Dict[str, Any]]:
    return {spec["key"]: _script_entry(spec) for spec in (script_list or [])}


def _build_menu_entry(category_name: str, cat: Dict[str, Any]) -> Dict[str, Any]:
    subcategories: Dict[str, Dict[str, Any]] = {}
    for sub_key, sub in cat.get("subtypes", {}).items():
        subcategories[sub_key.upper()] = {
            "name": sub.get("display_name", sub_key),
            "icon": sub.get("emoji", ""),
            "scripts": _scripts_dict(sub.get("scripts", [])),
        }

    entry: Dict[str, Any] = {
        "name": cat["display_name"],
        "description": cat.get("description", ""),
        "icon": cat["emoji"],
        "scripts": _scripts_dict(cat.get("category_scripts", [])),
        "subcategories": subcategories,
    }
    folder_path = _resolve_folder_path(category_name, cat.get("work_path_key"))
    if folder_path:
        entry["folder_path"] = folder_path
    return entry


def _build_menu():
    """Build the legacy menu shape with UPPER-cased category keys."""
    creative: Dict[str, Dict[str, Any]] = {}
    business: Dict[str, Dict[str, Any]] = {}
    creative_set = set(creative_categories())
    for name, cat in CATEGORIES.items():
        entry = _build_menu_entry(name, cat)
        target = creative if name in creative_set else business
        target[name.upper()] = entry
    return creative, business


CREATIVE_CATEGORIES, BUSINESS_CATEGORIES = _build_menu()
PIPELINE_CATEGORIES = {**CREATIVE_CATEGORIES, **BUSINESS_CATEGORIES}
