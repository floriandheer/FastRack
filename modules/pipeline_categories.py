"""
Pipeline Categories — single source of truth.

The ``CATEGORIES`` dict below carries everything the rest of the codebase needs
to know about a category and its subtypes:

  Category-level fields:
      color                — hex used in UI accents and chips
      emoji                — single-char display icon
      display_name         — human label for menus / headers
      description          — one-line description shown in some menus
      work_path_key        — argument for settings.get_work_path() / None
      category_scripts     — script entries shown at the category level in menus
      subtypes             — {key: subtype_dict}; empty for Business / Global

  Script-level fields (entries inside category_scripts / subtype.scripts):
      key, name, module|url, description, icon
      context              — "project" if the tool runs against a selected
                             project (it is hidden from the category Tools panel
                             and surfaced in the project detail Actions section).
                             Omit for category-level / global tools.
      project_types        — optional list of project_type values to restrict a
                             project-context tool further. Empty/missing means
                             "any project under this category".
      applies_when         — optional predicate name (see PROJECT_ACTION_PREDICATES
                             in fastrak_project_explorer). Evaluated against the
                             resolved project folder; the action is hidden when
                             it returns False. Use for distinctions the project
                             schema doesn't capture (e.g. wordpress vs static).

  Subtype-level fields (keys that may be present on each subtype):
      display_name, emoji, description, scripts (menu items)
      title, tree_template, folder_prefix, project_type,
      legacy_project_types, autocomplete_category,
      work_path_category, software_defaults_args, software_chips,
      extra_fields, supports_personal, supports_sandbox,
      creates_specs_file, extension

  Note: the importer's regex patterns (ProjectImporter.PATTERNS) are NOT
  derived from this file — each subtype's parsing logic has multi-pattern
  procedural quirks for legacy folder names that don't fit one regex.

A subtype is "creative" (= shows up in the New Project flow) iff it has a
non-None ``tree_template``. Adding a category or subtype is a one-place edit.
Everything else (registries, color tables, regex tables, archive routing,
menu trees) is derived by the helpers at the bottom of this file.
"""

import os
from typing import Dict, List, Optional, Any, Tuple, Iterable

# ============================================================================
# DATA
# ============================================================================

CATEGORIES: Dict[str, Dict[str, Any]] = {
    "Visual": {
        "color": "#f97316",
        "emoji": "🎬",
        "display_name": "Visual",
        "description": "Visual effects, graphics and animation tools",
        "work_path_key": "Visual",
        "category_scripts": [
            {
                "key": "add_txt_to_metadata",
                "name": "Add Text to Image Metadata",
                "module": "PipelineScript_Visual_AddTxtToMetadata",
                "description": "Add text from matching .txt files to JPEG image metadata",
                "icon": "🏷",
            },
        ],
        "subtypes": {
            "GD": {
                "display_name": "Graphic Design",
                "emoji": "🖼️",
                "title": "Graphic Design Folder Structure",
                "tree_template": "visual_gd_structure.txt",
                "folder_prefix": "GD",
                "project_type": "Visual-Graphic Design",
                "legacy_project_types": ["GD"],
                "autocomplete_category": "Visual",
                "work_path_category": "Visual",
                "software_defaults_args": ("Visual", "GD"),
                "software_chips": [
                    ("Houdini", "houdini", "20.5"),
                    ("Blender", "blender", "4.4"),
                    ("Fusion", "fusion", "19"),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
            "FX": {
                "display_name": "Visual Effects",
                "emoji": "🎬",
                "title": "FX Folder Structure",
                "tree_template": "visual_fx_structure.txt",
                "folder_prefix": "FX",
                "project_type": "Visual-Visual Effects",
                "legacy_project_types": ["FX", "VFX", "Visual-Computer Graphics"],
                "autocomplete_category": "Visual",
                "work_path_category": "Visual",
                "software_defaults_args": ("Visual", "FX"),
                "software_chips": [
                    ("Houdini", "houdini", "20.5"),
                    ("Blender", "blender", "4.4"),
                    ("Fusion", "fusion", "19"),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
            "VJ": {
                "display_name": "Live Video",
                "emoji": "💫",
                "title": "Live Video Folder Structure",
                "tree_template": "visual_vj_structure.txt",
                "folder_prefix": "VJ",
                "project_type": "Visual-Live Video",
                "legacy_project_types": ["VJ", "Visual-VJ"],
                "autocomplete_category": "Visual",
                "work_path_category": "Visual",
                "software_defaults_args": ("Visual", "VJ"),
                "software_chips": [
                    ("Resolume", "resolume", "Arena 7"),
                    ("After Effects", "after_effects", "2024"),
                    ("TouchDesigner", "touchdesigner", ""),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
            "PG": {
                "display_name": "Photogrammetry",
                "emoji": "📸",
                "title": "Photogrammetry Folder Structure",
                "tree_template": "visual_pg_structure.txt",
                "folder_prefix": "PG",
                "project_type": "Visual-Photogrammetry",
                "legacy_project_types": ["PG"],
                "autocomplete_category": "Visual",
                "work_path_category": "Visual",
                "software_defaults_args": ("Visual", "PG"),
                "software_chips": [
                    ("RealityCapture", "reality_capture", ""),
                    ("Houdini", "houdini", "20.5"),
                    ("Blender", "blender", "4.4"),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
        },
    },
    "RealTime": {
        "color": "#06b6d4",
        "emoji": "⚡",
        "display_name": "RealTime",
        "description": "Real-time processing and performance tools",
        "work_path_key": "RealTime",
        "category_scripts": [
            {
                "key": "resolume_sync",
                "name": "Resolume Sync",
                "module": "PipelineScript_RealTime_ResolumeSync",
                "description": "Push/pull Resolume Avenue folder between local PC and NAS",
                "icon": "🎛️",
            },
        ],
        "subtypes": {
            "Godot": {
                "display_name": "Godot",
                "emoji": "🔵",
                "title": "Godot Folder Structure",
                "tree_template": "realtime_godot_structure.txt",
                "folder_prefix": "Godot",
                "project_type": "Godot",
                "legacy_project_types": [],
                "autocomplete_category": "RealTime",
                "work_path_category": "RealTime",
                "software_defaults_args": ("RealTime", "Godot"),
                "software_chips": [("Godot", "godot", "4.3")],
                "extra_fields": [
                    {
                        "kind": "combobox", "name": "platform", "label": "Platform",
                        "choices": ["PC/Desktop", "Mobile", "Web", "Console", "Multi-platform"],
                        "default_key": "platform", "default_fallback": "PC/Desktop",
                        "metadata_key": "platform", "width": 12,
                    },
                    {
                        "kind": "combobox", "name": "renderer", "label": "Renderer",
                        "choices": ["Forward+", "Mobile", "Compatibility"],
                        "default_key": "renderer", "default_fallback": "Forward+",
                        "metadata_key": "renderer", "width": 12,
                    },
                ],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
            "TD": {
                "display_name": "TouchDesigner",
                "emoji": "🟠",
                "title": "TouchDesigner Folder Structure",
                "tree_template": "realtime_touchdesigner_structure.txt",
                "folder_prefix": "TD",
                "project_type": "TD",
                "legacy_project_types": [],
                "autocomplete_category": "RealTime",
                "work_path_category": "RealTime",
                "software_defaults_args": ("RealTime", "TD"),
                "software_chips": [
                    ("TouchDesigner", "touchdesigner", "2023.11760"),
                    ("Python", "python", "3.11"),
                ],
                "extra_fields": [
                    {
                        "kind": "combobox", "name": "resolution", "label": "Resolution",
                        "choices": ["1920x1080", "2560x1440", "3840x2160", "1280x720"],
                        "default_key": "resolution", "default_fallback": "1920x1080",
                        "metadata_key": "resolution", "width": 12,
                    },
                ],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
            "Resolume": {
                "display_name": "Resolume",
                "emoji": "🟣",
                "title": "Resolume Folder Structure",
                "tree_template": "realtime_resolume_structure.txt",
                "folder_prefix": "RS",
                "project_type": "Resolume",
                "legacy_project_types": [],
                "autocomplete_category": "RealTime",
                "work_path_category": "RealTime",
                "software_defaults_args": ("RealTime", "Resolume"),
                "software_chips": [
                    ("Resolume", "resolume", "Arena 7"),
                    ("Houdini", "houdini", "20.5"),
                    ("Blender", "blender", "4.4"),
                    ("Affinity", "affinity", ""),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
        },
    },
    "Audio": {
        "color": "#9333ea",
        "emoji": "🎵",
        "display_name": "Audio",
        "description": "Audio processing tools for DJs and producers",
        "work_path_key": "Audio",
        "category_scripts": [
            {
                "key": "backup_musicbee",
                "name": "Sync to Cloud",
                "module": "PipelineScript_Audio_Backup",
                "description": "Backup MusicBee library to OneDrive, only transferring changed or new files",
                "icon": "☁️",
            },
            {
                "key": "format_genres",
                "name": "Format Genres",
                "module": "PipelineScript_Audio_FormatGenres",
                "description": "Normalize clipboard genre strings to '; '-separated; auto-formats while open",
                "icon": "🏷",
            },
            {
                "key": "format_titles",
                "name": "Format Titles",
                "module": "PipelineScript_Audio_FormatTitles",
                "description": "Fix clipboard track/album titles to iTunes-style title case",
                "icon": "🔠",
            },
        ],
        "subtypes": {
            "DJ": {
                "display_name": "DJ Tools",
                "emoji": "🎧",
                # No tree_template yet — DJ is a script-only subtype until a folder template is wired up
                "title": None,
                "tree_template": None,
                "folder_prefix": "DJ",
                "project_type": "Audio-DJ",
                "legacy_project_types": [],
                "autocomplete_category": "Audio",
                "work_path_category": "Audio",
                "software_defaults_args": ("Audio",),
                "software_chips": [],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": False,
                "creates_specs_file": False,
                "extension": None,
                "scripts": [
                    {
                        "key": "sync_playlists",
                        "name": "Sync to Traktor",
                        "module": "PipelineScript_Audio_TraktorSync",
                        "description": "Synchronize iTunes playlists to Traktor DJ library with WAV conversion",
                        "icon": "💻",
                    },
                    {
                        "key": "traktor_playlist_sync",
                        "name": "Sync Traktor Playlists",
                        "module": "PipelineScript_Audio_TraktorPlaylistSync",
                        "description": "Export native Traktor playlists/smart lists to a portable NML for Import Collection on another machine",
                        "icon": "🗂️",
                    },
                    {
                        "key": "poweramp_sync",
                        "name": "Sync to PowerAmp",
                        "module": "PipelineScript_Audio_PowerAmpSync",
                        "description": "Export MusicBee playlists to M3U8 format for PowerAmp on Android",
                        "icon": "📱",
                    },
                ],
            },
            "PROD": {
                "display_name": "Production",
                "emoji": "🎛️",
                "title": "Audio Production Folder Structure",
                "tree_template": "audio_production_structure.txt",
                "folder_prefix": None,
                "project_type": "Audio-Production",
                "legacy_project_types": ["Audio"],
                "autocomplete_category": "Audio",
                "work_path_category": "Audio",
                "software_defaults_args": ("Audio",),
                "software_chips": [
                    ("Ableton", "ableton", "12"),
                    ("Reaper", "reaper", "7"),
                ],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
        },
    },
    "Physical": {
        "color": "#ec4899",
        "emoji": "🔧",
        "display_name": "Physical",
        "description": "Physical workflow automation",
        "work_path_key": "Physical",
        "category_scripts": [],
        "subtypes": {
            "Physical": {
                "display_name": "3D Print / Physical",
                "emoji": "🖨️",
                "title": "3D Printing Folder Structure",
                "tree_template": "physical_3dprint_structure.txt",
                "folder_prefix": "3DPrint",
                "project_type": "Physical",
                "legacy_project_types": [],
                "autocomplete_category": "Physical",
                "work_path_category": "Physical",
                "software_defaults_args": ("Physical",),
                "software_chips": [
                    ("Houdini", "houdini", "20.5"),
                    ("Blender", "blender", "4.4"),
                    ("FreeCAD", "freecad", ""),
                    ("Alibre", "alibre", ""),
                    ("Affinity", "affinity", ""),
                ],
                "extra_fields": [],
                "supports_personal": False,
                "supports_sandbox": False,
                "creates_specs_file": True,
                "extension": "folder_structure_extensions.physical:PhysicalExtension",
                "scripts": [
                    {
                        "key": "publish_photos_to_webshop",
                        "name": "Publish Photos to Webshop",
                        "module": "PipelineScript_Physical_PublishToWebshop",
                        "description": "Sync .lnk shortcuts from Documentation/Product_Photo/Publish into Web/_Personal/alles3d/products/<name>/photos",
                        "icon": "🔗",
                        "context": "project",
                        "project_types": ["Physical"],
                        "applies_when": "physical_product",
                    },
                ],
            },
        },
    },
    "Photo": {
        "color": "#10b981",
        "emoji": "📷",
        "display_name": "Photo",
        "description": "Photography workflow automation",
        "work_path_key": "Photo",
        "category_scripts": [
            {
                "key": "raw_cleanup",
                "name": "RAW Cleanup",
                "module": "PipelineScript_Photo_RawCleanup",
                "description": "Delete orphaned RAW files that have no matching JPG in the same folder",
                "icon": "🧹",
                "context": "project",
            },
        ],
        "subtypes": {
            "Photo": {
                "display_name": "Photography",
                "emoji": "📸",
                "title": "Photo Folder Structure",
                "tree_template": "photo_structure.txt",
                "folder_prefix": None,
                "project_type": "Photo",
                "legacy_project_types": [],
                "autocomplete_category": None,
                "work_path_category": "Photo",
                "software_defaults_args": None,
                "software_chips": [],
                "extra_fields": [],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": False,
                "extension": "folder_structure_extensions.photo:PhotoExtension",
                "scripts": [],
            },
        },
    },
    "Web": {
        "color": "#eab308",
        "emoji": "🌐",
        "display_name": "Web",
        "description": "Web development and publishing tools",
        "work_path_key": "Web",
        "category_scripts": [
            {
                "key": "backup_laragon",
                "name": "Laragon Workspace Manager",
                "module": "PipelineScript_Web_LaragonWorkspace",
                "description": "Manage Laragon project junctions to work drive",
                "icon": "🔗",
            },
            {
                "key": "vitepress_dev",
                "name": "Dev Server",
                "module": "PipelineScript_Web_DevServer",
                "description": "Run `npm run docs:dev` in 02_Development and open the site in the browser",
                "icon": "▶️",
                "context": "project",
                "applies_when": "vitepress",
            },
            {
                "key": "vitepress_build",
                "name": "Build",
                "module": "PipelineScript_Web_DevServer",
                "description": "Run `npm run docs:build` in 02_Development",
                "icon": "📦",
                "context": "project",
                "applies_when": "vitepress",
                "script_args": ["--build"],
            },
            {
                "key": "publish_static",
                "name": "Publish",
                "module": "PipelineScript_Web_PublishStatic",
                "description": "Upload Staatic exports to FTP, sync DokuWiki, and create dated archives",
                "icon": "🚀",
                "context": "project",
            },
            {
                "key": "devbackup_wordpress",
                "name": "WP Backup",
                "module": "PipelineScript_Web_DevBackup",
                "description": "Backup/restore WordPress dev sites (files + DB) and Laragon environment",
                "icon": "💾",
                "context": "project",
                "applies_when": "wordpress",
            },
        ],
        "subtypes": {
            "Web": {
                "display_name": "Web Development",
                "emoji": "🌐",
                "title": "Web Folder Structure",
                "tree_template": "web_structure.txt",
                "folder_prefix": None,
                "project_type": "Web",
                "legacy_project_types": [],
                "autocomplete_category": "Web",
                "work_path_category": "Web",
                "software_defaults_args": ("Web",),
                "software_chips": [
                    ("Wordpress", "wordpress", "6.7"),
                    ("Bricks", "bricks", "1.12"),
                    ("Vitepress", "vitepress", "1.6"),
                    ("Polygonjs", "polygonjs", "1.8"),
                ],
                "extra_fields": [
                    {
                        "kind": "text", "name": "website", "label": "Website URL",
                        "default_key": "website", "default_fallback": "",
                        "metadata_key": "website_url", "width": 30,
                    },
                ],
                "supports_personal": True,
                "supports_sandbox": True,
                "creates_specs_file": True,
                "extension": None,
                "scripts": [],
            },
        },
    },
    "Business": {
        "color": "#22c55e",
        "emoji": "💼",
        "display_name": "Business",
        "description": "Business and financial management tools",
        "work_path_key": None,
        "category_scripts": [
            {
                "key": "contacts",
                "name": "Contacts",
                "module": "PipelineScript_Business_Contacts",
                "description": "Customer directory — shares the contacts table with the Invoice Manager",
                "icon": "👤",
            },
        ],
        "subtypes": {},
    },
    "Global": {
        "color": "#6b7280",
        "emoji": "🛠️",
        "display_name": "Global Tools",
        "description": "General-purpose utilities",
        "work_path_key": None,
        "category_scripts": [
            {
                "key": "global_cleanup",
                "name": "Global Cleanup",
                "module": "PipelineScript_Global_Cleanup",
                "description": "Clean up temporary files and folders",
                "icon": "🧹",
            },
            {
                "key": "software_sync",
                "name": "Software Config Sync",
                "module": "PipelineScript_Global_SoftwareSync",
                "description": "Auto-detect software versions, back up/restore configs to NAS, and migrate configs to new versions",
                "icon": "🔄",
            },
            {
                "key": "software_launcher",
                "name": "Software Launcher",
                "module": "PipelineScript_Global_SoftwareLauncher",
                "description": "Download, update, and launch portable software tools from GitHub releases",
                "icon": "🚀",
            },
            {
                "key": "homebox",
                "name": "Homebox",
                "url": "http://169.254.132.127:3100/home",
                "description": "Personal inventory management",
                "icon": "📦",
            },
            {
                "key": "inventree",
                "name": "InvenTree",
                "url": "http://169.254.132.127:8080/web/home",
                "description": "Work inventory management",
                "icon": "🏭",
            },
        ],
        "subtypes": {},
    },
    "Sandbox": {
        "color": "#3b82f6",
        "emoji": "✍️",
        "display_name": "Sandbox",
        "description": "Quick, throwaway tools — capture an idea, draft, or note",
        # Resolved via a special case in ui_pipeline_categories._resolve_folder_path
        # (I:\_Sandbox), not the generic per-category work path.
        "work_path_key": None,
        "category_scripts": [
            {
                "key": "quick_write",
                "name": "Quick Write",
                "module": "PipelineScript_Write_QuickWrite",
                "description": "Create a new dated document in I:\\_Sandbox\\Write and open it in LibreOffice Writer",
                "icon": "📝",
            },
            {
                "key": "open_obsidian",
                "name": "Obsidian",
                "module": "PipelineScript_Write_OpenObsidian",
                "description": "Launch Obsidian (reopens your last vault)",
                "icon": "📓",
            },
        ],
        "subtypes": {},
    },
}


# ============================================================================
# DERIVED HELPERS
# ============================================================================

# Categories that own at least one folder-creator subtype
_CREATIVE_KEYS = ("Visual", "RealTime", "Audio", "Physical", "Photo", "Web")


def category(name: str) -> Optional[Dict[str, Any]]:
    """Return the raw category dict, or None if unknown."""
    return CATEGORIES.get(name)


def all_category_keys() -> List[str]:
    return list(CATEGORIES.keys())


def creative_categories() -> List[str]:
    """Categories that show up in the New Project flow."""
    return list(_CREATIVE_KEYS)


def is_creative_category(name: str) -> bool:
    return name in _CREATIVE_KEYS


def is_creative_subtype(category_name: str, subtype: str) -> bool:
    """A subtype is creative iff it has a tree_template wired up."""
    cat = CATEGORIES.get(category_name)
    if not cat:
        return False
    sub = cat.get("subtypes", {}).get(subtype)
    return bool(sub and sub.get("tree_template"))


def get_subtypes(category_name: str) -> List[str]:
    """All subtype keys for a category (including non-creative ones)."""
    cat = CATEGORIES.get(category_name)
    return list(cat.get("subtypes", {}).keys()) if cat else []


def get_creative_subtypes(category_name: str) -> List[str]:
    """Subtype keys that have a folder template wired up."""
    cat = CATEGORIES.get(category_name)
    if not cat:
        return []
    return [k for k, v in cat["subtypes"].items() if v.get("tree_template")]


def has_multiple_creative_subtypes(category_name: str) -> bool:
    return len(get_creative_subtypes(category_name)) > 1


def subtype_manifest(category_name: str, subtype: str) -> Optional[Dict[str, Any]]:
    """Return a flat manifest dict for the folder-creator (subtype + category injected).

    The dict carries both subtype-level fields and the parent category key
    under ``category``, matching the shape that GenericFolderStructureCreator
    expects.
    """
    cat = CATEGORIES.get(category_name)
    if not cat:
        return None
    sub = cat.get("subtypes", {}).get(subtype)
    if not sub:
        return None
    merged = dict(sub)
    merged["key"] = subtype
    merged["category"] = category_name
    merged["icon"] = sub.get("emoji", "")
    return merged


def subtype_display_name(subtype: str) -> str:
    """Look up the display name for a subtype by key (across all categories)."""
    for cat in CATEGORIES.values():
        sub = cat.get("subtypes", {}).get(subtype)
        if sub:
            return sub.get("display_name", subtype)
    return subtype


def category_color(name: str) -> str:
    cat = CATEGORIES.get(name)
    return cat["color"] if cat else "#6b7280"


def category_emoji(name: str) -> str:
    cat = CATEGORIES.get(name)
    return cat["emoji"] if cat else ""


def category_display_name(name: str) -> str:
    cat = CATEGORIES.get(name)
    return cat["display_name"] if cat else name


# ---- project_type lookups (with legacy alias support) ----

def _build_project_type_index() -> Dict[str, Dict[str, Any]]:
    """Map project_type strings (canonical + legacy aliases) to display info."""
    index: Dict[str, Dict[str, Any]] = {}
    for cat_name, cat in CATEGORIES.items():
        for sub_key, sub in cat.get("subtypes", {}).items():
            pt = sub.get("project_type")
            if not pt:
                continue
            entry = {
                "icon": sub.get("emoji", ""),
                "name": sub.get("display_name", sub_key),
                "color": cat["color"],
                "category": cat_name,
                "subtype": sub_key,
                "canonical": pt,
            }
            index[pt] = entry
            for alias in sub.get("legacy_project_types", []):
                # Aliases point at the same display info as their canonical
                index[alias] = entry
    # Also accept the bare category name as a project_type fallback
    for cat_name, cat in CATEGORIES.items():
        if cat_name not in index:
            index[cat_name] = {
                "icon": cat["emoji"],
                "name": cat["display_name"],
                "color": cat["color"],
                "category": cat_name,
                "subtype": None,
                "canonical": cat_name,
            }
    return index


_PROJECT_TYPE_INDEX = _build_project_type_index()


def project_type_info(project_type: str) -> Dict[str, Any]:
    """Return {icon, name, color, category, subtype, canonical} for any
    project_type string (canonical or legacy alias). Falls back to a generic
    entry for unknown types."""
    return _PROJECT_TYPE_INDEX.get(project_type, {
        "icon": "📁",
        "name": project_type,
        "color": "#6b7280",
        "category": None,
        "subtype": None,
        "canonical": project_type,
    })


def archive_category(project_type: str) -> Optional[str]:
    """Map any project_type (canonical or legacy) to its category name."""
    info = _PROJECT_TYPE_INDEX.get(project_type)
    return info["category"] if info else None


def all_creative_subtype_pairs() -> List[Tuple[str, str]]:
    """All (category, subtype) pairs that have a folder template."""
    pairs: List[Tuple[str, str]] = []
    for cat_name, cat in CATEGORIES.items():
        for sub_key, sub in cat.get("subtypes", {}).items():
            if sub.get("tree_template"):
                pairs.append((cat_name, sub_key))
    return pairs


def all_legacy_aliases() -> Dict[str, str]:
    """{legacy_project_type → canonical_project_type}."""
    out: Dict[str, str] = {}
    for cat in CATEGORIES.values():
        for sub in cat.get("subtypes", {}).values():
            pt = sub.get("project_type")
            if not pt:
                continue
            for alias in sub.get("legacy_project_types", []):
                out[alias] = pt
    return out
