"""
Per-category window icon helper for module subprocesses.

Each ``PipelineScript_<Category>_*.py`` module calls ``apply_category_icon(root)``
right after creating its Tk root. The helper derives the category from the
calling script's filename prefix and applies ``assets/category_icons/<Category>.ico``
so the module's taskbar/title-bar icon matches its hub category colour.
"""

import os
import sys
import ctypes
from typing import Optional

_ASSETS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "assets",
    "category_icons",
)

_KNOWN_CATEGORIES = {
    "Visual", "RealTime", "Audio", "Physical",
    "Photo", "Web", "Business", "Global",
    "Bookkeeping",
}

# Filename-prefix categories that don't have their own .ico (fall back to a
# semantically-related category icon).
_CATEGORY_FALLBACK = {
    "Bookkeeping": "Business",
}

_HUB_AUMID = "floriandheer.fastrack"


def _category_from_filename(path: str) -> Optional[str]:
    base = os.path.basename(path)
    name, _ = os.path.splitext(base)
    parts = name.split("_")
    if len(parts) >= 3 and parts[0] == "PipelineScript" and parts[1] in _KNOWN_CATEGORIES:
        return parts[1]
    return None


def _detect_category() -> Optional[str]:
    caller = sys.argv[0] if sys.argv and sys.argv[0] else __file__
    return _category_from_filename(caller)


def _set_taskbar_identity(category: str) -> None:
    # Windows taskbar uses the pythonw.exe icon when no AppUserModelID is set,
    # which is why iconbitmap alone wasn't enough. Child processes do not
    # inherit the parent's AUMID, so each module subprocess must set its own
    # before any Tk window is created.
    if sys.platform != "win32":
        return
    try:
        aumid = f"{_HUB_AUMID}.{category.lower()}"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
    except (AttributeError, OSError):
        pass


# Run at import time: modules import this helper before constructing tk.Tk(),
# so the AUMID is in place before the first window is mapped.
_AUTO_CATEGORY = _detect_category()
if _AUTO_CATEGORY:
    _set_taskbar_identity(_AUTO_CATEGORY)


def category_icon_path(category: str) -> Optional[str]:
    resolved = _CATEGORY_FALLBACK.get(category, category)
    path = os.path.join(_ASSETS_DIR, f"{resolved}.ico")
    return path if os.path.exists(path) else None


def apply_category_icon(root, category: Optional[str] = None) -> Optional[str]:
    """Apply the category-coloured .ico to a Tk root window.

    Returns the icon path used, or None if nothing was applied. Silent on
    failure — module windows must keep working if assets are missing.
    """
    try:
        if category is None:
            category = _AUTO_CATEGORY or _detect_category()
        if not category:
            return None
        icon_path = category_icon_path(category)
        if not icon_path:
            return None
        # Set both the default (applies to future toplevels) and the
        # per-window icon so the taskbar picks up the correct HICON.
        root.iconbitmap(default=icon_path)
        try:
            root.iconbitmap(icon_path)
        except Exception:
            pass
        return icon_path
    except Exception:
        return None
