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


def run_without_flicker(widget, fn):
    """Run `fn` (a relayout, e.g. collapsing a side panel) with the window's
    painting paused, then repaint it once.

    Collapsing a panel resizes everything next to it, and the project grid
    rebuilds its cards on the next resize event. Windows paints every step
    of that: the neighbouring panel flashes empty and briefly draws at a
    stale size. With WM_SETREDRAW off until the layout has settled, the
    user only sees the final state. Elsewhere `fn` just runs.
    """
    import sys
    if sys.platform != "win32":
        fn()
        return
    import ctypes
    top = widget.winfo_toplevel()
    # Pause the toplevel's client window (a child HWND), not the outer
    # frame window from wm_frame(): WM_SETREDRAW(FALSE) clears WS_VISIBLE,
    # and on the outer window the shell then treats the app as gone for a
    # moment and pops the (auto-hiding / full-screen-suppressed) taskbar up.
    hwnd = top.winfo_id()
    user32 = ctypes.windll.user32
    WM_SETREDRAW = 0x000B
    # RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW | RDW_FRAME
    RDW_FLAGS = 0x0001 | 0x0004 | 0x0080 | 0x0100 | 0x0400

    user32.SendMessageW(hwnd, WM_SETREDRAW, 0, 0)

    def resume():
        try:
            top.update_idletasks()
        finally:
            user32.SendMessageW(hwnd, WM_SETREDRAW, 1, 0)
            user32.RedrawWindow(hwnd, None, None, RDW_FLAGS)

    try:
        fn()
        top.update_idletasks()
    except BaseException:
        resume()
        raise
    # The resize events (and the grid rebuild they trigger) are handled by
    # the event loop after this returns; resume once those have run.
    top.after(0, lambda: top.after_idle(resume))


def make_icon_button(parent, icon, text, command, bg, fg, font,
                     icon_font=("Segoe UI Emoji", 10), hover_bg=None,
                     hover_fg=None, disabled_fg=COLORS["text_secondary"],
                     padx=10, pady=5, icon_width=20, gap=6, state=tk.NORMAL):
    """A flat, left-aligned button with its icon in a fixed-width column.

    Emoji glyphs come in different widths (and some fall back to a narrow
    text glyph), so putting the icon in the label's text ("📦 Archive")
    leaves every label starting at a slightly different x. Here the icon
    sits in a cell exactly `icon_width` pixels wide, so the text of a whole
    stack of these buttons lines up. Behaves like make_flat_button: the
    returned frame accepts .config(state=...), .configure(bg=...) and
    .configure(text=...).
    """
    hover_bg = bg if hover_bg is None else hover_bg
    hover_fg = fg if hover_fg is None else hover_fg

    frame = tk.Frame(parent, bg=bg, cursor="hand2")
    # A 1x1 image + compound makes the label's width/height count in
    # pixels instead of characters, which is what pins the icon column.
    frame._pixel = tk.PhotoImage(width=1, height=1)
    icon_lbl = tk.Label(frame, text=icon, image=frame._pixel, compound="center",
                        width=icon_width, font=icon_font, bg=bg, fg=fg,
                        padx=0, pady=0, bd=0, cursor="hand2")
    icon_lbl.pack(side=tk.LEFT, padx=(padx, gap), pady=pady)
    text_lbl = tk.Label(frame, text=text, font=font, bg=bg, fg=fg, anchor="w",
                        padx=0, pady=0, bd=0, cursor="hand2")
    text_lbl.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, padx), pady=pady)
    parts = (frame, icon_lbl, text_lbl)

    frame._normal_bg = bg
    frame._normal_fg = fg
    frame._state = state

    def paint(bg_now, fg_now, cursor):
        tk.Frame.configure(frame, bg=bg_now, cursor=cursor)
        for w in (icon_lbl, text_lbl):
            w.configure(bg=bg_now, fg=fg_now, cursor=cursor)

    def on_click(event):
        if frame._state != tk.DISABLED:
            command()

    def on_enter(event):
        if frame._state != tk.DISABLED:
            paint(hover_bg, hover_fg, "hand2")

    def on_leave(event):
        if frame._state == tk.DISABLED:
            return
        # Moving between the frame and its own labels fires Leave on the
        # one being left; only un-hover once the pointer is really outside.
        x, y = frame.winfo_pointerxy()
        if frame.winfo_containing(x, y) in parts:
            return
        paint(frame._normal_bg, frame._normal_fg, "hand2")

    for w in parts:
        w.bind("<Button-1>", on_click)
        w.bind("<Enter>", on_enter, add="+")
        w.bind("<Leave>", on_leave, add="+")

    def _configure(*args, **kw):
        if args and isinstance(args[0], dict):
            kw = {**args[0], **kw}
        if "bg" in kw:
            frame._normal_bg = kw.pop("bg")
        if "fg" in kw:
            frame._normal_fg = kw.pop("fg")
        if "text" in kw:
            text_lbl.configure(text=kw.pop("text"))
        if "icon" in kw:
            icon_lbl.configure(text=kw.pop("icon"))
        if "state" in kw:
            frame._state = kw.pop("state")
        if frame._state == tk.DISABLED:
            paint(frame._normal_bg, disabled_fg, "arrow")
        else:
            paint(frame._normal_bg, frame._normal_fg, "hand2")
        if kw:
            tk.Frame.configure(frame, **kw)

    frame.configure = _configure
    frame.config = _configure
    _configure(state=state)
    return frame
