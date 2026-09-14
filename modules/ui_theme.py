"""
UI Theme - Color constants and theme configuration for FastRack.

CATEGORY_COLORS is derived from pipeline_categories.CATEGORIES so the colors
stay in lockstep with the rest of the codebase. Both TitleCase ("Audio") and
UPPER ("AUDIO") keys are populated for compatibility with legacy callers.
"""

import tkinter as tk

from pipeline_categories import CATEGORIES as _CATEGORIES
from shared_colors import (
    BG_DARKEST, BG_RAISED_1, BG_RAISED_2, BG_HOVER,
    TEXT_PRIMARY, TEXT_SECONDARY,
    ACCENT, ACCENT_HOVER, ACCENT_DARK,
    SUCCESS, WARNING, ERROR, BORDER,
)

# Professional color scheme
COLORS = {
    "bg_primary": BG_DARKEST,      # GitHub dark background
    "bg_secondary": BG_RAISED_1,   # Slightly lighter
    "bg_card": BG_RAISED_2,        # Card background
    "bg_hover": BG_HOVER,          # Hover state
    "text_primary": TEXT_PRIMARY,  # Main text
    "text_secondary": TEXT_SECONDARY, # Secondary text
    "accent": ACCENT,              # Bright blue accent
    "accent_hover": ACCENT_HOVER,  # Hover accent
    "accent_dark": ACCENT_DARK,    # Darker accent
    "success": SUCCESS,
    "warning": WARNING,
    "error": ERROR,
    "border": BORDER,
    "tab_active_bg": ACCENT_DARK,  # Active tab background
    "tab_active_fg": "#ffffff"     # Active tab text
}

# Category colors — built from the unified pipeline_categories.CATEGORIES.
# Both TitleCase and UPPER keys are exposed so existing callers don't break.
CATEGORY_COLORS = {}
for _name, _cat in _CATEGORIES.items():
    _color = _cat["color"]
    CATEGORY_COLORS[_name] = _color
    CATEGORY_COLORS[_name.upper()] = _color
del _name, _cat, _color


def make_flat_button(parent, text, command, bg, fg, font, hover_bg=None,
                      hover_fg=None, disabled_fg=COLORS["text_secondary"],
                      padx=1, pady=1, state=tk.NORMAL, anchor=None, **extra):
    """A tk.Label styled and bound to behave like a flat, colored button.

    Classic tk.Button ignores bg/activebackground on macOS's Aqua theme —
    it always renders with the native button face regardless of those
    options, which on this app's dark theme left buttons showing pale text
    on a pale native background. tk.Label is a plain Tk-drawn widget rather
    than a native control, so its colors render correctly on every
    platform.

    Supports the same .config(state=tk.NORMAL/tk.DISABLED) toggling that
    callers already do on tk.Button, plus .configure(bg=...) to recolor it
    (e.g. a "selected" highlight) — both are intercepted on the returned
    widget so existing call sites don't need to change.
    """
    hover_bg = bg if hover_bg is None else hover_bg
    hover_fg = fg if hover_fg is None else hover_fg

    kwargs = dict(text=text, font=font, bg=bg, fg=fg, cursor="hand2",
                  padx=padx, pady=pady)
    if anchor is not None:
        kwargs["anchor"] = anchor
    kwargs.update(extra)
    btn = tk.Label(parent, **kwargs)

    btn._normal_bg = bg
    btn._normal_fg = fg
    btn._state = state

    def on_click(event):
        if btn._state == tk.DISABLED:
            return
        command()

    def on_enter(event):
        if btn._state == tk.DISABLED:
            return
        tk.Label.configure(btn, bg=hover_bg, fg=hover_fg)

    def on_leave(event):
        if btn._state == tk.DISABLED:
            return
        tk.Label.configure(btn, bg=btn._normal_bg, fg=btn._normal_fg)

    btn.bind("<Button-1>", on_click)
    btn.bind("<Enter>", on_enter, add="+")
    btn.bind("<Leave>", on_leave, add="+")

    def _configure(*args, **kw):
        if args and isinstance(args[0], dict):
            kw = {**args[0], **kw}
        if "state" in kw:
            btn._state = kw.pop("state")
            if btn._state == tk.DISABLED:
                tk.Label.configure(btn, fg=disabled_fg, cursor="arrow")
            else:
                tk.Label.configure(btn, fg=btn._normal_fg, cursor="hand2")
        if "bg" in kw:
            btn._normal_bg = kw["bg"]
        if "fg" in kw and btn._state != tk.DISABLED:
            btn._normal_fg = kw["fg"]
        if kw:
            tk.Label.configure(btn, **kw)

    btn.configure = _configure
    btn.config = _configure

    if state == tk.DISABLED:
        _configure(state=tk.DISABLED)

    return btn
