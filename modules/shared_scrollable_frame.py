"""
Shared ScrollableFrame widget - a plain-Tkinter scrollable container with
smooth mouse wheel/trackpad scrolling, used by any tool whose content can
grow taller than the window (or the screen).
"""

import tkinter as tk


class ScrollableFrame(tk.Frame):
    """A scrollable frame widget with smooth mouse wheel scrolling."""

    def __init__(self, parent, bg=None):
        super().__init__(parent, bg=bg)

        # Plain tk.Scrollbar, not ttk: ttk's is a native NSScroller on
        # macOS, which per that OS's own "Show scroll bars" preference
        # stays hidden except during an active scroll gesture.
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0)
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

    def _deactivate_mouse_wheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mouse_wheel(self, event):
        """Handle mouse wheel scrolling."""
        # Check if there's actually content to scroll
        try:
            bbox = self.canvas.bbox("all")
            if bbox is None:
                return "break"

            # Get the current view
            view_height = self.canvas.winfo_height()
            content_height = bbox[3] - bbox[1]

            # Only scroll if content is larger than view
            if content_height <= view_height:
                return "break"

            # Determine scroll direction and amount
            if event.num == 4 or event.delta > 0:
                # Scroll up
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5 or event.delta < 0:
                # Scroll down
                self.canvas.yview_scroll(1, "units")

            return "break"  # Prevent event from propagating
        except:
            return "break"

    def get_frame(self):
        """Get the scrollable frame."""
        return self.scrollable_frame
