"""
Data Apps Panel
Author: Florian Dheer
Description: The Data Manager's right-hand view. One card per piece of
software whose data runs through the sync pipeline (MusicBee, Obsidian, ...),
each listing the tools that move that software's data between devices and
instances. The app list lives in pipeline_categories (Data > "apps"); tool
rows are drawn by the hub so they behave exactly like the sidebar's (gear,
out/in switch, direct run). Fills the same right-hand slot fastrack_hub.py
otherwise gives to the Project Tracker, Invoice Manager or Sandbox Browser.
"""

import tkinter as tk
from tkinter import font
from typing import Callable, List, Dict, Any

from ui_theme import COLORS
from shared_scrollable_frame import ScrollableFrame

# Cards reflow into as many columns as fit at this minimum width.
_MIN_CARD_WIDTH = 360
_CARD_GAP = 12


class DataAppsPanel(tk.Frame):
    """Grid of app cards.

    ``make_tool_row(parent, category_key, script_key, subcat_key, script_data)``
    builds one tool row inside ``parent``. ``launch_app(app)`` starts the
    card's software; it's called when a card header with a ``launch``
    entry is clicked.
    """

    def __init__(self, parent, apps: List[Dict[str, Any]],
                 make_tool_row: Callable, launch_app: Callable = None,
                 accent: str = None):
        super().__init__(parent, bg=COLORS["bg_primary"])
        self._apps = apps
        self._make_tool_row = make_tool_row
        self._launch_app = launch_app
        self._accent = accent or COLORS["accent"]
        self._cards: List[tk.Frame] = []
        self._columns = 0

        self._title_font = font.Font(family="Segoe UI", size=14, weight="bold")
        self._sub_font = font.Font(family="Segoe UI", size=9)
        self._app_icon_font = font.Font(family="Segoe UI Emoji", size=20)
        self._app_name_font = font.Font(family="Segoe UI", size=12, weight="bold")

        header = tk.Frame(self, bg=COLORS["bg_primary"])
        header.pack(fill=tk.X, padx=20, pady=(18, 8))
        tk.Label(header, text="Synced Software", font=self._title_font,
                 fg=COLORS["text_primary"], bg=COLORS["bg_primary"],
                 anchor="w").pack(fill=tk.X)
        tk.Label(header, text="Tools that move each program's data between devices and instances",
                 font=self._sub_font, fg=COLORS["text_secondary"],
                 bg=COLORS["bg_primary"], anchor="w").pack(fill=tk.X)

        self._scroll = ScrollableFrame(self, bg=COLORS["bg_primary"])
        self._scroll.pack(fill=tk.BOTH, expand=True, padx=(20, 4), pady=(4, 12))
        self._grid = self._scroll.scrollable_frame

        if not apps:
            tk.Label(self._grid, text="No synced software configured",
                     font=self._sub_font, fg=COLORS["text_secondary"],
                     bg=COLORS["bg_primary"]).pack(pady=40)
            return

        for app in apps:
            self._cards.append(self._build_card(app))
        self._scroll.canvas.bind("<Configure>", self._on_resize, add="+")
        self._layout(1)

    # ----- building ---------------------------------------------------

    def _build_card(self, app: Dict[str, Any]) -> tk.Frame:
        card = tk.Frame(self._grid, bg=COLORS["bg_secondary"],
                        highlightthickness=1, highlightbackground=COLORS["border"],
                        highlightcolor=COLORS["border"])

        tk.Frame(card, bg=self._accent, height=3).pack(fill=tk.X, side=tk.TOP)

        # Header: padded strip so the hover highlight has some room.
        head = tk.Frame(card, bg=COLORS["bg_secondary"])
        head.pack(fill=tk.X, padx=6, pady=(6, 2))
        inner = tk.Frame(head, bg=COLORS["bg_secondary"])
        inner.pack(fill=tk.X, padx=8, pady=6)
        icon = tk.Label(inner, text=app["icon"], font=self._app_icon_font,
                        fg=COLORS["text_primary"], bg=COLORS["bg_secondary"])
        icon.pack(side=tk.LEFT)
        text = tk.Frame(inner, bg=COLORS["bg_secondary"])
        text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(10, 0))
        name = tk.Label(text, text=app["name"], font=self._app_name_font,
                        fg=COLORS["text_primary"], bg=COLORS["bg_secondary"], anchor="w")
        name.pack(fill=tk.X)
        widgets = [head, inner, icon, text, name]
        if app.get("description"):
            desc = tk.Label(text, text=app["description"], font=self._sub_font,
                            fg=COLORS["text_secondary"], bg=COLORS["bg_secondary"], anchor="w")
            desc.pack(fill=tk.X)
            widgets.append(desc)
        count = len(app["tools"])
        count_text = f"{count} tool{'s' if count != 1 else ''}"
        badge = tk.Label(inner, text=count_text, font=self._sub_font,
                         fg=COLORS["text_secondary"], bg=COLORS["bg_secondary"])
        badge.pack(side=tk.RIGHT, anchor="n")
        widgets.append(badge)

        if app.get("launch") and self._launch_app:
            def on_enter(_e):
                for w in widgets:
                    w.configure(bg=COLORS["bg_hover"])
                badge.configure(text=f"Launch {app['name']} ↗", fg=self._accent)

            def on_leave(_e):
                for w in widgets:
                    w.configure(bg=COLORS["bg_secondary"])
                badge.configure(text=count_text, fg=COLORS["text_secondary"])

            for w in widgets:
                w.configure(cursor="hand2")
                w.bind("<Enter>", on_enter)
                w.bind("<Leave>", on_leave)
                w.bind("<Button-1>", lambda _e, a=app: self._launch_app(a))

        body = tk.Frame(card, bg=COLORS["bg_secondary"])
        body.pack(fill=tk.X, padx=8, pady=(0, 10))
        if not app["tools"]:
            tk.Label(body, text="No tools yet", font=self._sub_font,
                     fg=COLORS["text_secondary"], bg=COLORS["bg_secondary"]).pack(pady=8)
        for category_key, script_key, subcat_key, script_data in app["tools"]:
            self._make_tool_row(body, category_key, script_key, subcat_key, script_data)
        return card

    # ----- layout -----------------------------------------------------

    def _on_resize(self, event):
        fit = (event.width + _CARD_GAP) // (_MIN_CARD_WIDTH + _CARD_GAP)
        columns = max(1, min(fit, len(self._cards)))
        if columns != self._columns:
            self._layout(columns)

    def _layout(self, columns: int):
        self._columns = columns
        for card in self._cards:
            card.grid_forget()
        for col in range(len(self._cards) + 1):
            self._grid.grid_columnconfigure(col, weight=0, uniform="")
        for col in range(columns):
            self._grid.grid_columnconfigure(col, weight=1, uniform="card")
        for index, card in enumerate(self._cards):
            row, col = divmod(index, columns)
            card.grid(row=row, column=col, sticky="new",
                      padx=(0, _CARD_GAP), pady=(0, _CARD_GAP))
