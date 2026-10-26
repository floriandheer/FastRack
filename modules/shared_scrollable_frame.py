"""
Shared ScrollableFrame widget - a plain-Tkinter scrollable container with
smooth mouse wheel/trackpad scrolling, used by any tool whose content can
grow taller than the window (or the screen).
"""

import tkinter as tk
from tkinter import ttk


def safe_bind_touchpad_scroll(binder, *args):
    """Call a Tk bind method (bind/bind_class/bind_all/unbind_all) with the
    <TouchpadScroll> sequence, silently no-oping if this Tk build predates
    Tk 9's TIP 684 and doesn't know the event at all - still the case for
    the Tk 8.6 that ships with Windows Python builds, which raises
    "bad event type or keysym" the instant it's bound. Trackpad scroll then
    simply falls back to whatever <MouseWheel>/<Button-4/5> handling
    already exists."""
    try:
        binder(*args)
    except tk.TclError:
        pass


def touchpad_scroll_units(widget, event):
    """Direction of a <TouchpadScroll> event as -1 (view up), 1 (view down)
    or 0 (no vertical movement / unreadable). %D packs signed X/Y deltas
    into one int; unpack with the Tcl helper Tk ships for exactly this
    rather than hand-rolling the bit-shifting ourselves."""
    try:
        _dx, dy = widget.tk.call('tk::PreciseScrollDeltas', event.delta)
        dy = int(dy)
    except (tk.TclError, ValueError):
        return 0
    if dy == 0:
        return 0
    return -1 if dy > 0 else 1


def scroll_canvas_if_room(canvas, units):
    """Scroll `canvas` by `units` (positive = down) only if its content is
    actually taller than the visible view. Always returns "break" so it can
    be returned straight from a Tk event handler."""
    try:
        bbox = canvas.bbox("all")
        if bbox is None:
            return "break"
        if bbox[3] - bbox[1] <= canvas.winfo_height():
            return "break"
        canvas.yview_scroll(units, "units")
    except tk.TclError:
        pass
    return "break"


# Widgets whose Tk class bindings already scroll them on wheel/trackpad. An
# `all`-tag handler runs after those, so without this check a scroll over
# one inside a scrollable canvas would move both.
_SELF_SCROLLING_CLASSES = ("Text", "Listbox", "Treeview")


def scrolls_itself(widget):
    """True if `widget` is a Text/Listbox/Treeview with content to scroll."""
    try:
        return (widget.winfo_class() in _SELF_SCROLLING_CLASSES
                and tuple(widget.yview()) != (0.0, 1.0))
    except (tk.TclError, AttributeError):
        return False


def enable_canvas_scroll(canvas, hover=None):
    """Mouse wheel + trackpad scrolling for a hand-built scrollable canvas
    (a tk.Canvas with an embedded frame), matching ScrollableFrame: active
    while the pointer is over `hover` (default: the canvas itself - pass its
    container when the canvas shares it with a scrollbar or other widgets).
    Bound globally for the duration of the hover, so it keeps working for
    content added later without rebinding.

    Tk 9 sends a two-finger trackpad scroll as <TouchpadScroll>, not
    <MouseWheel>, so a canvas that only binds <MouseWheel> ignores the
    trackpad entirely (see ScrollableFrame._activate_mouse_wheel)."""
    hover = hover if hover is not None else canvas
    state = {"active": False}

    def on_wheel(event):
        if scrolls_itself(event.widget):
            return None
        if event.num == 4 or event.delta > 0:
            return scroll_canvas_if_room(canvas, -1)
        if event.num == 5 or event.delta < 0:
            return scroll_canvas_if_room(canvas, 1)
        return "break"

    def on_touchpad(event):
        if scrolls_itself(event.widget):
            return None
        units = touchpad_scroll_units(canvas, event)
        return scroll_canvas_if_room(canvas, units) if units else "break"

    def activate(_event=None):
        state["active"] = True
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind_all(seq, on_wheel)
        safe_bind_touchpad_scroll(canvas.bind_all, "<TouchpadScroll>", on_touchpad)

    def deactivate(_event=None):
        if not state["active"]:
            return
        state["active"] = False
        for seq in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.unbind_all(seq)
        safe_bind_touchpad_scroll(canvas.unbind_all, "<TouchpadScroll>")

    def on_leave(event):
        # Moving from `hover` onto one of its own children also fires
        # <Leave> on it; only deactivate once the pointer is really outside.
        try:
            under = str(hover.winfo_containing(event.x_root, event.y_root))
            if under == str(hover) or under.startswith(str(hover) + "."):
                return
        except (tk.TclError, KeyError):
            pass
        deactivate()

    hover.bind("<Enter>", activate, add="+")
    hover.bind("<Leave>", on_leave, add="+")
    canvas.bind("<Destroy>", deactivate, add="+")


class ScrollableFrame(tk.Frame):
    """A scrollable frame widget with smooth mouse wheel scrolling."""

    def __init__(self, parent, bg=None, show_scrollbar=True, scrollbar_style=None):
        """`scrollbar_style` names a ttk scrollbar style (e.g. the app's
        themed "Vertical.TScrollbar") so the bar matches the app's other
        scrollbars. Left unset, a plain tk.Scrollbar is used."""
        super().__init__(parent, bg=bg)

        # Plain tk.Scrollbar by default, not ttk: ttk's is a native
        # NSScroller on macOS, which per that OS's own "Show scroll bars"
        # preference stays hidden except during an active scroll gesture.
        # Callers that force a non-native ttk theme (clam) can opt into the
        # themed ttk bar via scrollbar_style instead.
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.scrollbar = None
        if show_scrollbar:
            if scrollbar_style:
                self.scrollbar = ttk.Scrollbar(
                    self, orient="vertical", command=self.canvas.yview,
                    style=scrollbar_style,
                )
            else:
                self.scrollbar = tk.Scrollbar(
                    self, orient="vertical", command=self.canvas.yview, width=16,
                )
        self.scrollable_frame = tk.Frame(self.canvas, bg=bg)

        # Never itemconfig the embedded window's height explicitly — leave
        # it at its natural size so it keeps growing/shrinking as content
        # changes later (e.g. tool buttons on a category switch). Pinning
        # it once (even just to fill a short initial view) freezes the
        # scrollregion at that size forever, making anything added
        # afterward unreachable by any scroll method.
        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_window = self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        if self.scrollbar is not None:
            self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.bind(
            '<Configure>',
            lambda e: self.canvas.itemconfig(self.canvas_window, width=e.width)
        )

        # Bind mouse wheel directly to canvas (simpler approach)
        self._bind_mouse_wheel()

        # Pack the scrollbar BEFORE the canvas: pack reserves space for
        # widgets in the order they're packed, so an expand=True widget
        # (the canvas) packed first can claim the whole cavity before a
        # fixed-width sibling packed after it gets a turn — measured here
        # as the scrollbar collapsing to 1px wide when the order was
        # reversed.
        if self.scrollbar is not None:
            self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

    def _bind_mouse_wheel(self):
        """Scroll on wheel/trackpad input while the pointer is over this
        widget. Bound globally (bind_all) only for the duration of the
        hover, rather than on each child individually, so it keeps working
        for content added later without needing to be rebound."""
        self.bind("<Enter>", self._activate_mouse_wheel)
        self.bind("<Leave>", self._deactivate_mouse_wheel)

    def _activate_mouse_wheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mouse_wheel)
        self.canvas.bind_all("<Button-4>", self._on_mouse_wheel)
        self.canvas.bind_all("<Button-5>", self._on_mouse_wheel)
        # Tk 9's TIP 684: a two-finger trackpad scroll no longer generates
        # <MouseWheel> at all (that's now mouse-wheel-only) - it generates a
        # separate <TouchpadScroll> event instead, on Windows and macOS.
        # Without this, trackpad scrolling silently does nothing anywhere
        # <MouseWheel> used to handle it, while a real mouse wheel (or
        # dragging the scrollbar thumb directly) still works fine. Still
        # guarded by safe_bind_touchpad_scroll though, since plenty of
        # installs (e.g. Windows' own python.org builds) still ship Tk 8.6,
        # which doesn't know this event and raises TclError on the bind
        # itself - that Tk just keeps using <MouseWheel> for the trackpad
        # too, so no fallback handling is needed here.
        safe_bind_touchpad_scroll(self.canvas.bind_all, "<TouchpadScroll>", self._on_touchpad_scroll)

    def _deactivate_mouse_wheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")
        safe_bind_touchpad_scroll(self.canvas.unbind_all, "<TouchpadScroll>")

    def _scroll_if_room(self, units):
        """Scroll by `units` (positive = down) only if the content is
        actually taller than the visible view."""
        return scroll_canvas_if_room(self.canvas, units)

    def _on_mouse_wheel(self, event):
        """Handle a real mouse wheel (Tk 9's <MouseWheel> is mouse-only;
        trackpads use <TouchpadScroll> - see _on_touchpad_scroll)."""
        if event.num == 4 or event.delta > 0:
            return self._scroll_if_room(-1)
        elif event.num == 5 or event.delta < 0:
            return self._scroll_if_room(1)
        return "break"

    def _on_touchpad_scroll(self, event):
        """Handle a two-finger trackpad scroll (Tk 9's <TouchpadScroll>,
        TIP 684)."""
        units = touchpad_scroll_units(self.canvas, event)
        return self._scroll_if_room(units) if units else "break"

    def get_frame(self):
        """Get the scrollable frame."""
        return self.scrollable_frame
