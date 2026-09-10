#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
shared_wordpress.py

Single source of truth for "is this WordPress?" detection. A folder is
considered a WordPress install iff it contains a parseable wp-config.php
(either directly, as in Laragon's www/<site>/, or under the project layout
02_Development/<site>/).

Callers:
  - PipelineScript_Web_DevBackup     (Laragon www scan)
  - PipelineScript_Web_PublishStatic (site discovery)
  - fastrack_project_explorer         (Actions section button filtering)
"""

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional


DEFAULT_LARAGON_PATH = r"C:\laragon"
_DEVBACKUP_CONFIG = (
    Path.home() / "AppData" / "Local" / "PipelineManager" / "web_devbackup_config.json"
)


_DEFINE_RE = re.compile(
    r"""define\s*\(\s*['"](?P<key>[A-Z_]+)['"]\s*,\s*['"](?P<val>[^'"]*)['"]\s*\)\s*;""",
    re.IGNORECASE,
)
_PREFIX_RE = re.compile(
    r"""\$table_prefix\s*=\s*['"](?P<val>[^'"]*)['"]\s*;"""
)


def parse_wp_config(wp_config_path: str) -> Optional[Dict[str, str]]:
    """Parse wp-config.php for DB credentials. Returns dict or None on error
    or when DB_NAME is missing (treated as not a real WP install).
    """
    try:
        with open(wp_config_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except Exception:
        return None

    values = {m.group("key").upper(): m.group("val")
              for m in _DEFINE_RE.finditer(content)}
    if "DB_NAME" not in values:
        return None

    prefix_match = _PREFIX_RE.search(content)
    return {
        "db_name": values.get("DB_NAME", ""),
        "db_user": values.get("DB_USER", "root"),
        "db_password": values.get("DB_PASSWORD", ""),
        "db_host": values.get("DB_HOST", "localhost"),
        "table_prefix": prefix_match.group("val") if prefix_match else "wp_",
    }


def find_wp_config(folder: str) -> Optional[str]:
    """Locate wp-config.php for the given folder.

    Checks, in order:
      1. <folder>/wp-config.php           (Laragon-style: www/<site>/)
      2. <folder>/02_Development/*/wp-config.php  (work-drive project layout)

    Returns the absolute path to wp-config.php, or None if not found.
    """
    if not folder or not os.path.isdir(folder):
        return None

    direct = os.path.join(folder, "wp-config.php")
    if os.path.isfile(direct):
        return direct

    for sub in ("02_Development", "02_development"):
        dev = os.path.join(folder, sub)
        if not os.path.isdir(dev):
            continue
        try:
            for name in os.listdir(dev):
                candidate = os.path.join(dev, name, "wp-config.php")
                if os.path.isfile(candidate):
                    return candidate
        except OSError:
            continue

    return None


def is_wordpress_folder(folder: str) -> bool:
    """True iff folder (or its 02_Development child) is a WordPress install."""
    return find_wp_config(folder) is not None


def get_laragon_www() -> str:
    """Return the Laragon www path, reading the dev-backup config when present
    and falling back to the default install location otherwise. Returns "" when
    the path does not exist on disk so callers can short-circuit cleanly.
    """
    laragon = DEFAULT_LARAGON_PATH
    try:
        if _DEVBACKUP_CONFIG.is_file():
            with open(_DEVBACKUP_CONFIG, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            laragon = cfg.get("laragon_path", DEFAULT_LARAGON_PATH) or DEFAULT_LARAGON_PATH
    except Exception:
        pass
    www = os.path.join(laragon, "www")
    return www if os.path.isdir(www) else ""


def is_wordpress_project(folder: str) -> bool:
    """True iff the project — identified by its on-disk folder — has a
    WordPress install. Checks both the project tree itself and the Laragon
    www/<basename> mirror, because work-drive Web projects typically keep
    only assets/dev backups while the running WP install lives in Laragon.
    """
    if is_wordpress_folder(folder):
        return True
    if not folder:
        return False
    site_name = os.path.basename(os.path.normpath(folder))
    www = get_laragon_www()
    if not www or not site_name:
        return False
    return is_wordpress_folder(os.path.join(www, site_name))
