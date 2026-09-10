"""
Shared ScrollableFrame widget - a plain-Tkinter scrollable container with
smooth mouse wheel/trackpad scrolling, used by any tool whose content can
grow taller than the window (or the screen).
"""

import tkinter as tk


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


class ScrollableFrame(tk.Frame):
    """A scrollable frame widget with smooth mouse wheel scrolling."""

    def __init__(self, parent, bg=None, show_scrollbar=True):
        super().__init__(parent, bg=bg)

        # Plain tk.Scrollbar, not ttk: ttk's is a native NSScroller on
        # macOS, which per that OS's own "Show scroll bars" preference
        # stays hidden except during an active scroll gesture.
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
        self.scrollbar = None
        if show_scrollbar:
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
        try:
            bbox = self.canvas.bbox("all")
            if bbox is None:
                return "break"
            view_height = self.canvas.winfo_height()
            content_height = bbox[3] - bbox[1]
            if content_height <= view_height:
                return "break"
            self.canvas.yview_scroll(units, "units")
            return "break"
        except tk.TclError:
            return "break"

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
        TIP 684). %D packs signed X/Y deltas into one int; unpack with the
        Tcl helper Tk ships for exactly this rather than hand-rolling the
        bit-shifting ourselves."""
        try:
            _dx, dy = self.canvas.tk.call('tk::PreciseScrollDeltas', event.delta)
            dy = int(dy)
        except tk.TclError:
            return "break"
        if dy == 0:
            return "break"
        return self._scroll_if_room(-1 if dy > 0 else 1)

    def get_frame(self):
        """Get the scrollable frame."""
        return self.scrollable_frame
