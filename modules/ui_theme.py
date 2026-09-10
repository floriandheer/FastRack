"""
UI Theme - Color constants and theme configuration for FastRack.

CATEGORY_COLORS is derived from pipeline_categories.CATEGORIES so the colors
stay in lockstep with the rest of the codebase. Both TitleCase ("Audio") and
UPPER ("AUDIO") keys are populated for compatibility with legacy callers.
"""

from pipeline_categories import CATEGORIES as _CATEGORIES

# Professional color scheme
COLORS = {
    "bg_primary": "#0d1117",      # GitHub dark background
    "bg_secondary": "#161b22",    # Slightly lighter
    "bg_card": "#1c2128",         # Card background
    "bg_hover": "#262c36",        # Hover state
    "text_primary": "#f0f6fc",    # Main text
    "text_secondary": "#8b949e",  # Secondary text
    "accent": "#58a6ff",          # Bright blue accent
    "accent_hover": "#79c0ff",    # Hover accent
    "accent_dark": "#1f6feb",     # Darker accent
    "success": "#3fb950",
    "warning": "#d29922",
    "error": "#f85149",
    "border": "#30363d",
    "tab_active_bg": "#1f6feb",   # Active tab background
    "tab_active_fg": "#ffffff"    # Active tab text
}

# Category colors — built from the unified pipeline_categories.CATEGORIES.
# Both TitleCase and UPPER keys are exposed so existing callers don't break.
CATEGORY_COLORS = {}
for _name, _cat in _CATEGORIES.items():
    _color = _cat["color"]
    CATEGORY_COLORS[_name] = _color
    CATEGORY_COLORS[_name.upper()] = _color
del _name, _cat, _color
