"""
Canonical GitHub-dark palette.

shared_form_keyboard.FORM_COLORS and ui_theme.COLORS independently
hardcoded this same set of hex values under two different naming
conventions, so a palette tweak had to be made twice (and could drift,
as invoice_manager/theme.py's override of bg_input already had to work
around). Both now build their dict from these constants instead - their
own key names are kept as-is since many call sites across the codebase
already depend on them. New code should prefer importing straight from
here.
"""

BG_DARKEST = "#0d1117"      # Page/form background
BG_RAISED_1 = "#161b22"     # Input fields, secondary surfaces
BG_RAISED_2 = "#1c2128"     # Focused input, cards
BG_HOVER = "#262c36"        # Hover state

TEXT_PRIMARY = "#f0f6fc"
TEXT_SECONDARY = "#8b949e"  # Labels, hints, placeholders
TEXT_PLACEHOLDER = "#6e7681"

ACCENT = "#58a6ff"          # Focus ring, links, focused border
ACCENT_HOVER = "#79c0ff"
ACCENT_DARK = "#1f6feb"     # Buttons, active elements, active tab bg

BORDER = "#30363d"

SUCCESS = "#3fb950"
WARNING = "#d29922"
ERROR = "#f85149"
