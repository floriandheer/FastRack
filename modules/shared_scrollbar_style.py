"""
Shared scrollbar look - the flat dark bar the Sandbox browser introduced,
used for every ttk scrollbar in FastRack and its tools.

What makes it "clean" is clam's scrollbar with lightcolor/darkcolor set to
the thumb colour (no light bevel) and the border matching the trough.

apply_clean_scrollbars() restyles the default "Vertical.TScrollbar" /
"Horizontal.TScrollbar" in *every* ttk theme, not just the current one: a
lot of tools switch theme (clam, default...) after their window is
created, and ttk style settings are per theme. Themes other than clam get
clam's scrollbar elements copied in, so the bar looks the same everywhere
(and is never the native macOS bar that hides itself).
"""

import tkinter as tk
from tkinter import ttk, scrolledtext

THUMB = "#1c2128"
THUMB_ACTIVE = "#2d333b"
TROUGH = "#0d1117"
ARROW = "#8b949e"

_ORIENTS = {
    "Vertical": ("uparrow", "downarrow", "top", "bottom", "ns"),
    "Horizontal": ("leftarrow", "rightarrow", "left", "right", "ew"),
}
_DONE_FLAG = "::fastrack_clean_scrollbars"


def _layout(orient, prefix):
    first, second, side1, side2, sticky = _ORIENTS[orient]
    base = f"{prefix}{orient}.Scrollbar"
    return [(f"{base}.trough", {"sticky": sticky, "children": [
        (f"{base}.{first}", {"side": side1, "sticky": ""}),
        (f"{base}.{second}", {"side": side2, "sticky": ""}),
        (f"{base}.thumb", {"sticky": "nswe"}),
    ]})]


def _look(trough):
    configure = dict(background=THUMB, troughcolor=trough, bordercolor=trough,
                     lightcolor=THUMB, darkcolor=THUMB, arrowcolor=ARROW)
    # clam paints a disabled (nothing-to-scroll) bar light grey otherwise
    state_map = {key: [("disabled", THUMB), ("active", THUMB_ACTIVE)]
                 for key in ("background", "lightcolor", "darkcolor")}
    return configure, state_map


def apply_clean_scrollbars(master=None):
    """Give every ttk scrollbar in this Tk interpreter the clean look.
    Safe to call repeatedly; the work happens once per interpreter."""
    try:
        style = ttk.Style(master)
        if style.tk.call("info", "exists", _DONE_FLAG):
            return
        style.tk.call("set", _DONE_FLAG, 1)
        configure, state_map = _look(TROUGH)
        for theme in style.theme_names():
            settings = {}
            for orient, (first, second, *_rest) in _ORIENTS.items():
                # clam inherits layouts from "default", so it gets its own
                # (plain) layout too, or it would pick up default's below.
                entry = {"configure": configure, "map": state_map,
                         "layout": _layout(orient, "")}
                if theme != "clam":
                    for part in ("trough", "thumb", first, second):
                        name = f"{orient}.Scrollbar.{part}"
                        try:
                            style.theme_settings(theme, {f"Clean.{name}": {
                                "element create": ("from", "clam", name)}})
                        except tk.TclError:
                            pass  # already created in this theme
                    entry["layout"] = _layout(orient, "Clean.")
                settings[f"{orient}.TScrollbar"] = entry
            try:
                style.theme_settings(theme, settings)
            except tk.TclError:
                pass
    except tk.TclError:
        pass  # never let styling take a window down


def clean_scrollbar_style(master=None, trough=TROUGH, orient="Vertical"):
    """Style name for a clean scrollbar whose trough matches `trough` (use the
    background it sits on). Derived from the default style, so it inherits
    the layout and thumb colours."""
    apply_clean_scrollbars(master)
    if trough.lower() == TROUGH:
        return f"{orient}.TScrollbar"
    name = f"T{trough.lstrip('#').lower()}.{orient}.TScrollbar"
    try:
        style = ttk.Style(master)
        for theme in style.theme_names():
            style.theme_settings(theme, {name: {
                "configure": {"troughcolor": trough, "bordercolor": trough}}})
    except tk.TclError:
        return f"{orient}.TScrollbar"
    return name


class CleanScrolledText(scrolledtext.ScrolledText):
    """scrolledtext.ScrolledText with the clean ttk scrollbar instead of the
    plain tk one it builds itself. Drop-in replacement."""

    def __init__(self, master=None, **kw):
        super().__init__(master, **kw)
        self.vbar.destroy()
        self.vbar = ttk.Scrollbar(self.frame, orient="vertical", command=self.yview,
                                  style=clean_scrollbar_style(self.frame))
        # str(self) is the outer frame here (ScrolledText overrides it), so
        # name the text widget by its own path.
        self.vbar.pack(side=tk.RIGHT, fill=tk.Y, before=self._w)
        self.configure(yscrollcommand=self.vbar.set)
