"""Action bar shown directly under a tool window's header.

One universal Save Settings button on the left (same look in every tool), and
the tool's main buttons on the right. A tool with Export / Import tabs gives
each tab its own group of buttons; the bar shows the group of the selected tab.
"""

import tkinter as tk
from tkinter import ttk

from shared_color_button import ColorButton

SAVE_BG, SAVE_FG = "#2980b9", "white"   # the one Save Settings colour, for every tool
SAVE_WIDTH = 15


class ActionBar(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self._left = ttk.Frame(self)
        self._left.grid(row=0, column=0, sticky="w", padx=10, pady=8)
        self._right = ttk.Frame(self)
        self._right.grid(row=0, column=1, sticky="e", padx=10, pady=8)
        ttk.Separator(self).grid(row=1, column=0, columnspan=2, sticky="ew")
        self._groups = {}

    def add_save_button(self, command) -> ColorButton:
        button = ColorButton(self._left, text="Save Settings", command=command, width=SAVE_WIDTH,
                             bg=SAVE_BG, fg=SAVE_FG, font=("", 9, "bold"))
        button.pack(side=tk.LEFT)
        return button

    def group(self, key: str) -> ttk.Frame:
        """The frame to create a tab's main buttons in (pack them side=LEFT)."""
        if key not in self._groups:
            self._groups[key] = ttk.Frame(self._right)
        return self._groups[key]

    def show(self, key: str) -> None:
        for frame in self._groups.values():
            frame.pack_forget()
        if key in self._groups:
            self._groups[key].pack(side=tk.RIGHT)

    def follow(self, notebook: ttk.Notebook, keys) -> None:
        """Show the group matching the selected tab (keys are in tab order)."""
        def sync(_event=None):
            try:
                self.show(keys[notebook.index("current")])
            except (tk.TclError, IndexError):
                pass

        notebook.bind("<<NotebookTabChanged>>", sync, add="+")
        sync()
