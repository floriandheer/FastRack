"""
UI Pipeline Categories — adapter that produces the legacy menu shape from the
unified pipeline_categories.CATEGORIES tree.

The menu format consumed by fastrack_hub / ui_keyboard_navigator is:

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

from rack_settings import get_rack_settings, join_native_path
from pipeline_categories import CATEGORIES, creative_categories
from shared_appdata import get_appdata_path

# Base script directory (relative to the main pipeline file)
SCRIPT_FILE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SCRIPT_FILE_DIR, "modules")

# Application constants
APP_NAME = "FastRack"
APP_VERSION = "0.5.0"
APP_ICON = None
LOGO_PATH = os.path.join(SCRIPT_FILE_DIR, "assets", "Logo_FlorianDheer_LogoWhite.png")
DEFAULT_CONFIG_PATH = str(get_appdata_path() / "config.json")


def _resolve_folder_path(category_name: str, work_path_key) -> str:
    """Build folder_path for a category. Business uses the _LIBRARY drive root,
    Sandbox uses the _Sandbox drive root (same root the Sandbox Browser panel
    shows), tools-only categories (Data) have no folder, others use settings.get_work_path(work_path_key)."""
    if category_name == "Business":
        return join_native_path(get_rack_settings().get_work_drive(), "_LIBRARY")
    if category_name == "Sandbox":
        return join_native_path(get_rack_settings().get_work_drive(), "_Sandbox")
    if work_path_key is None:
        return ""
    return get_rack_settings().get_work_path(work_path_key)


def _script_entry(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a pipeline_categories script spec into menu format."""
    out: Dict[str, Any] = {
        "name": spec["name"],
        "description": spec.get("description", ""),
        "icon": spec.get("icon", ""),
        "direct_run": spec.get("direct_run", False),
        "mode_switch": spec.get("mode_switch", False),
    }
    if "module" in spec:
        out["path"] = os.path.join(SCRIPTS_DIR, f"{spec['module']}.py")
        # The hub's out/in switch reads and writes this tool's saved mode by module name.
        out["module"] = spec["module"]
    if "url" in spec:
        out["url"] = spec["url"]
    if "context" in spec:
        out["context"] = spec["context"]
    if "project_types" in spec:
        out["project_types"] = list(spec["project_types"])
    if "applies_when" in spec:
        out["applies_when"] = spec["applies_when"]
    if spec.get("show_in_tools") is False:
        out["show_in_tools"] = False
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
            "tools_header": sub.get("tools_header", False),
        }

    entry: Dict[str, Any] = {
        "name": cat["display_name"],
        # The canonical mixed-case key (e.g. "RealTime") pipeline_categories.py
        # and fastrack_project_explorer.py route on. Kept separate from
        # "name" (display text) since the two can now differ - a display
        # rename must not change what callers pass to set_categories().
        "category_key": category_name,
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


def resolve_tool_ref(ref):
    """``(category, subtype or None, script key)`` -> ``(category_key,
    script_key, subcat_key, script_data)`` in the UPPER-cased menu keys
    ``run_script`` expects, or None when the script no longer exists."""
    if not ref or len(ref) != 3:
        return None
    cat_name, sub_key, script_key = ref
    cat_key = str(cat_name).upper()
    sub_menu_key = str(sub_key).upper() if sub_key else None
    cat_entry = PIPELINE_CATEGORIES.get(cat_key, {})
    if sub_menu_key:
        scripts = cat_entry.get("subcategories", {}).get(sub_menu_key, {}).get("scripts", {})
    else:
        scripts = cat_entry.get("scripts", {})
    script_data = scripts.get(script_key)
    if script_data is None:
        return None
    return (cat_key, script_key, sub_menu_key, script_data)


def all_tool_refs():
    """Every script in the menu as ``(ref, label)``, for pickers. ``ref`` is
    ``[CATEGORY, SUBCAT or None, script_key]``; label reads
    "Category › Subtype › Script"."""
    out = []
    for cat_key, cat in PIPELINE_CATEGORIES.items():
        for key, data in cat.get("scripts", {}).items():
            out.append(([cat_key, None, key], f"{cat['name']} › {data['name']}"))
        for sub_key, sub in cat.get("subcategories", {}).items():
            for key, data in sub.get("scripts", {}).items():
                out.append(([cat_key, sub_key, key], f"{cat['name']} › {sub['name']} › {data['name']}"))
    return out


def data_apps():
    """The Data Manager's synced-software cards, resolved to menu entries.

    Returns ``[{key, name, icon, launch, description, tools}]`` where each tool is
    ``(category_key, script_key, subcat_key, script_data)`` in the
    UPPER-cased menu keys ``run_script`` expects. Tool references that
    don't resolve (renamed/removed script) are skipped.
    """
    apps = []
    for app in CATEGORIES.get("Data", {}).get("apps", []):
        tools = [t for t in (resolve_tool_ref(r) for r in app.get("tools", [])) if t]
        apps.append({
            "key": app["key"],
            "name": app.get("name", app["key"]),
            "icon": app.get("icon", ""),
            "launch": app.get("launch"),
            "description": app.get("description", ""),
            "tools": tools,
        })
    return apps
