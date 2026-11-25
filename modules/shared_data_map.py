"""
Data Map - the Data Manager's user-editable picture of where personal data
lives and how it moves.

Three lists, all edited from the Data Manager UI and saved to
``<appdata>/data_map.json``:

  locations   the places data can live: workstation, laptop, NAS, phone,
              cloud, a USB drive ... (the matrix's columns)
  transports  what moves data between them: Syncthing, Synology Drive,
              OneDrive/rclone, robocopy, ADB, a network database ...
  datasets    a piece of data owned by an app (music library, notes vault,
              Kodi database ...) with, per location, the role that copy
              plays, the transport that gets it there, its path and notes.
              These are the matrix's rows.

Every item has a free ``notes`` field. The point is to write down the facts
that are easy to forget: the NAS address, which database Kodi points at,
why Resolume isn't on continuous sync.

The first load seeds the map from pipeline_categories' Data > "apps" so it
isn't empty. After that, the JSON file is the source of truth.
"""

import json
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from shared_appdata import get_appdata_path

MAP_VERSION = 1

# role key -> (glyph, label, hint). The order here is the order in pickers.
ROLES: Dict[str, Tuple[str, str, str]] = {
    "master": ("★", "Source of truth", "The copy everything else is made from"),
    "live": ("⇄", "Live sync", "Kept in sync continuously, both ways"),
    "mirror": ("⇅", "Manual push/pull", "Copied on demand; you pick which side wins"),
    "backup": ("⛁", "Backup", "One-way safety copy"),
    "export": ("→", "Export", "Converted or derived copy (other format/paths)"),
    "server": ("●", "Central server", "Hosts the shared database/library"),
    "client": ("○", "Client", "Reads and writes the central server"),
}


def map_path() -> Path:
    return get_appdata_path() / "data_map.json"


# ---------------------------------------------------------------- seeding

_SEED_LOCATIONS = [
    {"key": "workstation", "name": "Workstation", "icon": "🖥️"},
    {"key": "laptop", "name": "Laptop", "icon": "💻"},
    {"key": "nas", "name": "NAS", "icon": "🗄️"},
    {"key": "phone", "name": "Phone", "icon": "📱"},
    {"key": "cloud", "name": "Cloud", "icon": "☁️"},
]

_SEED_TRANSPORTS = [
    {"key": "syncthing", "name": "Syncthing", "icon": "🔁",
     "tool": ["DATA", None, "open_syncthing"]},
    {"key": "synology_drive", "name": "Synology Drive", "icon": "🗄️"},
    {"key": "onedrive", "name": "OneDrive (rclone)", "icon": "☁️"},
    {"key": "robocopy", "name": "Robocopy push/pull", "icon": "⇅"},
    {"key": "adb", "name": "ADB / USB", "icon": "🔌"},
    {"key": "drive_bundle", "name": "USB drive bundle", "icon": "💾"},
]

# What the existing sync scripts already tell us, so the first matrix isn't
# blank. Keyed by the Data > "apps" key.
_SEED_PLACES = {
    "musicbee": [
        {"location": "workstation", "role": "master", "path": "M:\\"},
        {"location": "cloud", "role": "backup", "transport": "onedrive", "path": "onedrive:_Music"},
        {"location": "phone", "role": "export", "transport": "adb",
         "notes": "Opus files + M3U8 playlists for PowerAmp"},
    ],
    "obsidian": [
        {"location": "workstation", "role": "live", "transport": "syncthing"},
    ],
    "traktor": [
        {"location": "workstation", "role": "master"},
        {"location": "laptop", "role": "export", "transport": "drive_bundle",
         "notes": "Paths are rewritten for the other machine on import"},
    ],
    "resolume": [
        {"location": "workstation", "role": "master",
         "path": "%USERPROFILE%\\Documents\\Resolume Avenue"},
        {"location": "nas", "role": "mirror", "transport": "robocopy",
         "path": "I:\\Realtime\\_LIBRARY\\Resolume Avenue",
         "notes": "Not on continuous sync: Resolume rewrites its files often and two-way sync conflicts"},
    ],
}

_SEED_EXTRA_DATASETS = [
    {"key": "kodi", "name": "Kodi library", "icon": "📺", "app": "Kodi",
     "description": "Media library database"},
]


def _seed_datasets() -> List[Dict[str, Any]]:
    from pipeline_categories import CATEGORIES

    datasets = []
    for app in CATEGORIES.get("Data", {}).get("apps", []):
        datasets.append({
            "key": app["key"],
            "name": app.get("name", app["key"]),
            "icon": app.get("icon", ""),
            "app": app.get("launch") or "",
            "description": app.get("description", ""),
            "tools": [[c.upper(), s.upper() if s else None, k] for c, s, k in app.get("tools", [])],
            "places": deepcopy(_SEED_PLACES.get(app["key"], [])),
        })
    datasets.extend(deepcopy(_SEED_EXTRA_DATASETS))
    return datasets


def default_map() -> Dict[str, Any]:
    return normalize({
        "version": MAP_VERSION,
        "locations": deepcopy(_SEED_LOCATIONS),
        "transports": deepcopy(_SEED_TRANSPORTS),
        "datasets": _seed_datasets(),
    })


# ---------------------------------------------------------- normalization

def slugify(text: str, taken=()) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", (text or "").lower()).strip("_") or "item"
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


def _tool_ref(raw) -> Optional[List]:
    if not isinstance(raw, (list, tuple)) or len(raw) != 3 or not raw[0] or not raw[2]:
        return None
    cat, sub, key = raw
    return [str(cat).upper(), str(sub).upper() if sub else None, str(key)]


def _str(item, field) -> str:
    value = item.get(field, "")
    return value if isinstance(value, str) else ""


def normalize(data: Any) -> Dict[str, Any]:
    """Coerce whatever is on disk into the shape the UI relies on: every
    field present, keys unique, and places only pointing at locations and
    transports that exist."""
    data = data if isinstance(data, dict) else {}
    out: Dict[str, Any] = {"version": MAP_VERSION, "locations": [], "transports": [], "datasets": []}

    def unique_items(raw_list, fields):
        items, taken = [], set()
        for raw in raw_list if isinstance(raw_list, list) else []:
            if not isinstance(raw, dict):
                continue
            item = {f: _str(raw, f) for f in fields}
            item["name"] = item["name"] or item["key"] or "Unnamed"
            item["key"] = slugify(item["key"] or item["name"], taken)
            taken.add(item["key"])
            items.append((item, raw))
        return items

    for item, _raw in unique_items(data.get("locations"), ("key", "name", "icon", "address", "notes")):
        out["locations"].append(item)
    for item, raw in unique_items(data.get("transports"), ("key", "name", "icon", "app", "notes")):
        item["tool"] = _tool_ref(raw.get("tool"))
        out["transports"].append(item)

    location_keys = {l["key"] for l in out["locations"]}
    transport_keys = {t["key"] for t in out["transports"]}
    for item, raw in unique_items(data.get("datasets"),
                                  ("key", "name", "icon", "app", "description", "notes")):
        tools = []
        for ref in raw.get("tools", []) if isinstance(raw.get("tools"), list) else []:
            ref = _tool_ref(ref)
            if ref and ref not in tools:
                tools.append(ref)
        item["tools"] = tools
        places, seen = [], set()
        for place in raw.get("places", []) if isinstance(raw.get("places"), list) else []:
            if not isinstance(place, dict):
                continue
            loc = _str(place, "location")
            if loc not in location_keys or loc in seen:
                continue
            seen.add(loc)
            role = _str(place, "role")
            transport = _str(place, "transport")
            places.append({
                "location": loc,
                "role": role if role in ROLES else "live",
                "transport": transport if transport in transport_keys else "",
                "path": _str(place, "path"),
                "notes": _str(place, "notes"),
            })
        item["places"] = places
        out["datasets"].append(item)
    return out


# --------------------------------------------------------------- load/save

def load(path: Optional[Path] = None) -> Dict[str, Any]:
    """The saved map, or the seeded default when there's none yet. A file
    that can't be read is left alone (not overwritten) and the default is
    returned with ``_load_error`` set so the UI can say so."""
    path = Path(path) if path else map_path()
    if not path.exists():
        return default_map()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return normalize(json.load(fh))
    except (OSError, ValueError) as exc:
        data = default_map()
        data["_load_error"] = f"Couldn't read {path}: {exc}"
        return data


def save(data: Dict[str, Any], path: Optional[Path] = None) -> Path:
    """Write the map atomically (temp file + replace)."""
    path = Path(path) if path else map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = normalize(data)
    fd, tmp = tempfile.mkstemp(prefix=".data_map_", suffix=".json", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


# ----------------------------------------------------------------- helpers

def find(items: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    return next((i for i in items if i.get("key") == key), None)


def place_at(dataset: Dict[str, Any], location_key: str) -> Optional[Dict[str, Any]]:
    return next((p for p in dataset.get("places", []) if p["location"] == location_key), None)


def set_place(dataset: Dict[str, Any], place: Optional[Dict[str, Any]], location_key: str) -> None:
    """Add/replace the dataset's place at ``location_key``; None removes it.
    Places keep the location column order."""
    places = [p for p in dataset.get("places", []) if p["location"] != location_key]
    if place is not None:
        places.append(dict(place, location=location_key))
    dataset["places"] = places


def remove_location(data: Dict[str, Any], key: str) -> None:
    data["locations"] = [l for l in data["locations"] if l["key"] != key]
    for ds in data["datasets"]:
        ds["places"] = [p for p in ds["places"] if p["location"] != key]


def remove_transport(data: Dict[str, Any], key: str) -> None:
    data["transports"] = [t for t in data["transports"] if t["key"] != key]
    for ds in data["datasets"]:
        for p in ds["places"]:
            if p["transport"] == key:
                p["transport"] = ""


def warnings_for(dataset: Dict[str, Any], location_names: Optional[Dict[str, str]] = None) -> List[str]:
    """Plain-language gaps in how a dataset is set up. Cheap and config-only:
    nothing here touches the disk or the network."""
    names = location_names or {}
    places = dataset.get("places", [])
    if not places:
        return ["Not mapped yet: click a cell to say where this data lives"]
    roles = [p["role"] for p in places]
    out = []
    if roles.count("master") > 1:
        out.append("More than one source of truth")
    has_central = "server" in roles or "client" in roles
    if "client" in roles and "server" not in roles:
        out.append("Clients but no central server")
    if not has_central and "master" not in roles and "live" not in roles:
        out.append("No source of truth")
    if "backup" not in roles:
        out.append("No backup: live sync copies deletions too" if "live" in roles else "No backup")
    for p in places:
        if p["role"] != "master" and not p["transport"] and p["role"] not in ("server", "client"):
            out.append(f"How does it get to {names.get(p['location'], p['location'])}? No transport set")
    return out
