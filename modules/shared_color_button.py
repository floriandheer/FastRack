"""
shared_color_button.py
Description: A colored push-button that actually shows its color on macOS.
Classic tk.Button has drawn through native Aqua controls since Tk 8.6/Tk9 on
macOS, which ignore -background/-foreground entirely - the same
`tk.Button(..., bg="green", fg="white")` call that paints a solid green
button on Windows renders as a plain gray button on a Mac. ColorButton is a
styled, clickable Label instead: a Label's background is never
native-rendered on any platform, so the same code paints the same color
everywhere.
"""

import tkinter as tk

DISABLED_FG = "#888888"


class ColorButton(tk.Label):
    """Drop-in replacement for `tk.Button(..., bg=..., fg=...)`: same
    constructor kwargs (text, command, bg, fg, font, width, state) and the
    same `.config(state=tk.NORMAL/tk.DISABLED)` toggling used throughout
    this codebase, but rendered as a Label so the colors actually show up
    on macOS."""

    def __init__(self, parent, text="", command=None, bg="#dddddd", fg="black",
                 font=None, width=None, state=tk.NORMAL, **kwargs):
        self._command = command
        self._bg = bg
        self._fg = fg
        self._state = state

        label_kwargs = dict(kwargs)
        if font is not None:
            label_kwargs["font"] = font
        if width is not None:
            label_kwargs["width"] = width
        label_kwargs.setdefault("bd", 2)
        label_kwargs.setdefault("padx", 6)
        label_kwargs.setdefault("pady", 4)

        super().__init__(parent, text=text, bg=bg, fg=fg, relief=tk.RAISED, **label_kwargs)

        self.bind("<ButtonPress-1>", self._on_press)
        self._apply_state()

    def _on_press(self, _event):
        # Fires on press rather than release, same as most flat/Label-based
        # button recipes - it sidesteps having to work out whether the
        # pointer was still over the widget at release time (fragile across
        # platforms) for a click target that's never a drag source anyway.
        if self._state != tk.NORMAL:
            return
        tk.Label.config(self, relief=tk.SUNKEN)
        self.after(80, lambda: tk.Label.config(self, relief=tk.RAISED))
        if self._command:
            self._command()

    def _apply_state(self):
        if self._state == tk.DISABLED:
            tk.Label.config(self, fg=DISABLED_FG, cursor="arrow")
        else:
            tk.Label.config(self, fg=self._fg, cursor="pointinghand")

    def config(self, **kwargs):
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        if "bg" in kwargs or "background" in kwargs:
            self._bg = kwargs.get("bg", kwargs.get("background"))
        if "fg" in kwargs or "foreground" in kwargs:
            self._fg = kwargs.get("fg", kwargs.get("foreground"))
        tk.Label.config(self, **kwargs)
        self._apply_state()

    configure = config
