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

make_button() / make_toggle_button() are the drop-in factories for code that
used to call tk.Button / tk.Radiobutton(indicatoron=False) with colors: they
hand back the stock Tk widget everywhere it already works (Windows, Linux) and
a ColorButton / ColorRadioButton only on macOS, so nothing changes off the Mac.
"""

import sys
import tkinter as tk

DISABLED_FG = "#888888"
POINTER_CURSOR = "pointinghand" if sys.platform == "darwin" else "hand2"

# True where classic tk.Button ignores bg/fg and a Label-based stand-in is
# needed. Module-level (read at call time) so tests can force either path.
USE_COLOR_BUTTON = sys.platform == "darwin"

_PRESS_FLASH_MS = 80


class ColorButton(tk.Label):
    """Drop-in replacement for `tk.Button(..., bg=..., fg=...)`: same
    constructor kwargs (text, command, bg, fg, font, width, state, relief,
    activebackground, activeforeground, disabledforeground, ...) and the same
    `.config(state=tk.NORMAL/tk.DISABLED)` toggling used throughout this
    codebase, but rendered as a Label so the colors actually show up on
    macOS. Also supports `.invoke()`, `.flash()`, `.config(command=...)`,
    `cget("state"/"command")` and `activebackground`/`activeforeground`
    hover colors.

    A Label's own highlight ring is drawn at the wrong size by Aqua Tk (stray
    dark L-shaped lines inside the button), so `highlightthickness` is not
    handed to the Label: the thickness is added to padx/pady and the ring is
    drawn with four thin frames instead, colored `highlightcolor` while the
    button has the keyboard focus and `highlightbackground` otherwise."""

    def __init__(self, parent, text="", command=None, bg="#dddddd", fg="black",
                 font=None, width=None, state=tk.NORMAL, relief=tk.RAISED,
                 activebackground=None, activeforeground=None,
                 disabledforeground=None, **kwargs):
        self._command = command
        self._bg = bg
        self._fg = fg
        self._state = state
        self._relief = relief
        self._active_bg = activebackground
        self._active_fg = activeforeground
        self._disabled_fg = disabledforeground or DISABLED_FG
        self._hover = False
        self._relief_job = None

        label_kwargs = dict(kwargs)
        self._ring_size = int(label_kwargs.pop("highlightthickness", 0) or 0)
        self._ring_idle = label_kwargs.pop("highlightbackground", None)
        self._ring_focus = label_kwargs.pop("highlightcolor", None)
        if font is not None:
            label_kwargs["font"] = font
        if width is not None:
            label_kwargs["width"] = width
        if "bd" not in label_kwargs and "borderwidth" not in label_kwargs:
            label_kwargs["bd"] = 2
        label_kwargs["padx"] = label_kwargs.get("padx", 6) + self._ring_size
        label_kwargs["pady"] = label_kwargs.get("pady", 4) + self._ring_size

        super().__init__(parent, text=text, bg=bg, fg=fg, relief=relief, **label_kwargs)
        if self._ring_size:
            self._build_ring(parent)

        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<space>", lambda _event: self.invoke())
        if activebackground is not None or activeforeground is not None:
            self.bind("<Enter>", self._on_enter, add="+")
            self.bind("<Leave>", self._on_leave, add="+")
        self._apply_state()

    def _on_press(self, _event):
        # Fires on press rather than release, same as most flat/Label-based
        # button recipes - it sidesteps having to work out whether the
        # pointer was still over the widget at release time (fragile across
        # platforms) for a click target that's never a drag source anyway.
        if self._state != tk.NORMAL:
            return
        self.flash()
        self._activate()

    def _activate(self):
        if self._command:
            return self._command()
        return None

    def invoke(self):
        """Run the command as a click would (no-op while disabled)."""
        if self._state != tk.NORMAL:
            return None
        return self._activate()

    def flash(self):
        """Brief pressed look; restores the configured relief afterwards."""
        tk.Label.config(self, relief=tk.SUNKEN)
        if self._relief_job is not None:
            self.after_cancel(self._relief_job)
        self._relief_job = self.after(_PRESS_FLASH_MS, self._release_flash)

    def _release_flash(self):
        self._relief_job = None
        try:
            tk.Label.config(self, relief=self._relief)
        except tk.TclError:
            pass  # widget went away inside the command (e.g. a Close button)

    def _build_ring(self, parent):
        size = self._ring_size
        if self._ring_idle is None:
            try:
                self._ring_idle = parent.cget("bg")
            except tk.TclError:
                self._ring_idle = self._bg
        if self._ring_focus is None:
            self._ring_focus = self._ring_idle
        self._ring = [tk.Frame(self, bd=0, highlightthickness=0) for _ in range(4)]
        top, bottom, left, right = self._ring
        # bordermode=outside: coordinates start at the widget's outer edge,
        # not inside its bd, so the ring is the outermost layer as in Tk.
        edge = {"bordermode": "outside"}
        top.place(x=0, y=0, relwidth=1, height=size, **edge)
        bottom.place(x=0, rely=1.0, y=-size, relwidth=1, height=size, **edge)
        left.place(x=0, y=0, relheight=1, width=size, **edge)
        right.place(relx=1.0, x=-size, y=0, relheight=1, width=size, **edge)
        for frame in self._ring:
            frame.bind("<ButtonPress-1>", self._on_press)
        self.bind("<FocusIn>", lambda _e: self._paint_ring(), add="+")
        self.bind("<FocusOut>", lambda _e: self._paint_ring(), add="+")
        self._paint_ring()

    def _paint_ring(self):
        if not self._ring_size:
            return
        try:
            focused = self.focus_get() is self
        except (KeyError, tk.TclError):
            focused = False
        color = self._ring_focus if focused else self._ring_idle
        for frame in self._ring:
            frame.configure(bg=color)

    def _on_enter(self, _event):
        self._hover = True
        self._apply_state()

    def _on_leave(self, _event):
        self._hover = False
        self._apply_state()

    def _current_bg(self):
        if self._hover and self._state == tk.NORMAL and self._active_bg:
            return self._active_bg
        return self._bg

    def _apply_state(self):
        if self._state == tk.DISABLED:
            tk.Label.config(self, bg=self._current_bg(), fg=self._disabled_fg, cursor="arrow")
            return
        fg = self._active_fg if (self._hover and self._active_fg) else self._fg
        tk.Label.config(self, bg=self._current_bg(), fg=fg, cursor=POINTER_CURSOR)

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs = {**cnf, **kwargs}
        if not kwargs:
            return tk.Label.config(self)
        if "state" in kwargs:
            self._state = kwargs.pop("state")
        if "command" in kwargs:
            self._command = kwargs.pop("command")
        if "bg" in kwargs or "background" in kwargs:
            self._bg = kwargs.get("bg", kwargs.get("background"))
        if "fg" in kwargs or "foreground" in kwargs:
            self._fg = kwargs.get("fg", kwargs.get("foreground"))
        if "activebackground" in kwargs:
            self._active_bg = kwargs.pop("activebackground")
        if "activeforeground" in kwargs:
            self._active_fg = kwargs.pop("activeforeground")
        if "disabledforeground" in kwargs:
            self._disabled_fg = kwargs.pop("disabledforeground") or DISABLED_FG
        if "relief" in kwargs:
            self._relief = kwargs["relief"]
        ring_changed = False
        if "highlightbackground" in kwargs:
            self._ring_idle = kwargs.pop("highlightbackground")
            ring_changed = True
        if "highlightcolor" in kwargs:
            self._ring_focus = kwargs.pop("highlightcolor")
            ring_changed = True
        kwargs.pop("highlightthickness", None)  # fixed at construction, see class docstring
        if kwargs:
            tk.Label.config(self, **kwargs)
        self._apply_state()
        if ring_changed:
            self._paint_ring()
        return None

    configure = config

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "command":
            return self._command
        if key == "highlightthickness":
            return self._ring_size
        if key == "highlightbackground":
            return self._ring_idle
        if key == "highlightcolor":
            return self._ring_focus
        return tk.Label.cget(self, key)

    __getitem__ = cget

    def destroy(self):
        if self._relief_job is not None:
            try:
                self.after_cancel(self._relief_job)
            except tk.TclError:
                pass
            self._relief_job = None
        super().destroy()


class ColorRadioButton(ColorButton):
    """Drop-in for `tk.Radiobutton(..., indicatoron=False, selectcolor=...)`.
    A native Aqua radio drawn as a push-button ignores selectcolor, so the
    selected segment looks identical to the others; this one repaints itself
    with `selectcolor` while its variable holds `value`."""

    def __init__(self, parent, variable=None, value=None, selectcolor=None, **kwargs):
        self._variable = variable
        self._value = value
        self._select_bg = selectcolor
        self._trace_id = None
        kwargs.pop("indicatoron", None)
        super().__init__(parent, **kwargs)
        if variable is not None:
            self._trace_id = variable.trace_add("write", self._on_variable)

    def _is_selected(self):
        if self._variable is None:
            return False
        try:
            return str(self._variable.get()) == str(self._value)
        except tk.TclError:
            return False

    def _current_bg(self):
        if self._select_bg and self._is_selected():
            return self._select_bg
        return super()._current_bg()

    def _on_variable(self, *_args):
        if self.winfo_exists():
            self._apply_state()

    def _activate(self):
        if self._variable is not None:
            self._variable.set(self._value)
        return super()._activate()

    def config(self, cnf=None, **kwargs):
        if cnf:
            kwargs = {**cnf, **kwargs}
        if "selectcolor" in kwargs:
            self._select_bg = kwargs.pop("selectcolor")
            if not kwargs:
                self._apply_state()
                return None
        return super().config(**kwargs)

    configure = config

    def select(self):
        if self._variable is not None:
            self._variable.set(self._value)

    def destroy(self):
        if self._trace_id is not None:
            try:
                self._variable.trace_remove("write", self._trace_id)
            except (tk.TclError, ValueError):
                pass
            self._trace_id = None
        super().destroy()


def make_button(parent, **kwargs):
    """`tk.Button(parent, **kwargs)` that also shows its bg/fg on macOS:
    stock tk.Button where colors work, ColorButton where they don't."""
    if USE_COLOR_BUTTON:
        return ColorButton(parent, **kwargs)
    return tk.Button(parent, **kwargs)


def make_toggle_button(parent, **kwargs):
    """`tk.Radiobutton(parent, indicatoron=False, **kwargs)` (a segmented
    on/off look) whose selectcolor also shows on macOS."""
    if USE_COLOR_BUTTON:
        return ColorRadioButton(parent, **kwargs)
    kwargs["indicatoron"] = False
    return tk.Radiobutton(parent, **kwargs)
