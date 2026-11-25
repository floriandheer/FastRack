"""
Data Map Panel
Author: Florian Dheer
Description: The Data Manager's right-hand view. A matrix with datasets
(music library, notes vault, Kodi database, ...) as rows and locations
(workstation, laptop, NAS, phone, cloud, ...) as columns. Each cell shows the
role that copy plays and what moves it there. Below it, a detail pane for
the selected dataset: its notes, where each copy lives, setup gaps, and the
tools that act on it (drawn by the hub, so they behave like the sidebar's).

Everything shown is user-configured (shared_data_map, saved to appdata):
click a cell to say what lives there, a column header to edit the location,
a row to see details. "Locations" and "Transports" open list editors.
Replaces the static app cards (ui_data_apps_panel).
"""

import os
import tkinter as tk
from tkinter import font
from typing import Callable, List, Optional

import shared_data_map as dm
from shared_scrollable_frame import ScrollableFrame
from shared_open_path import open_path
from ui_theme import COLORS
from ui_data_map_editors import DatasetDialog, ListManagerDialog, PlaceDialog

ROLE_COLORS = {
    "master": "#e3b341",
    "live": COLORS["success"],
    "mirror": COLORS["accent"],
    "backup": "#a371f7",
    "export": "#db61a2",
    "server": "#39c5cf",
    "client": "#39c5cf",
}

_NAME_COL_MIN = 180
_CELL_MIN = 92

# Selected Dataset side panel: same look and width rules as the tracker's
# Selected Project and the Sandbox's Selected Item panels.
_DETAILS_BG = "#1c2128"
_DETAILS_MIN_WIDTH = 300
_DETAILS_MAX_WIDTH = 440
_DETAILS_WIDTH_FRACTION = 0.3
_DETAILS_RAIL_WIDTH = 34
_DETAILS_PAD = 14


class DataMapPanel(tk.Frame):
    """Configurable overview of personal data.

    ``make_tool_row(parent, category_key, script_key, subcat_key, script_data)``
    draws one hub tool row. ``resolve_tool(ref)`` turns a stored tool ref
    into that 4-tuple (or None). ``launch_app(name)`` starts a catalog app.
    ``app_names()`` and ``tool_refs()`` feed the editors' pickers.
    """

    def __init__(self, parent, make_tool_row: Callable, resolve_tool: Callable,
                 launch_app: Callable = None, app_names: Callable = None,
                 tool_refs: Callable = None, status: Callable = None, accent: str = None,
                 map_file=None, details_collapsed: bool = False,
                 on_details_collapsed: Callable = None, hint: Callable = None):
        super().__init__(parent, bg=COLORS["bg_primary"])
        self._make_tool_row = make_tool_row
        self._resolve_tool = resolve_tool
        self._launch_app = launch_app
        self._app_names = app_names or (lambda: [])
        self._tool_refs = tool_refs or (lambda: [])
        self._status = status or (lambda *_a, **_k: None)
        self._accent = accent or COLORS["accent"]
        self._map_file = map_file
        self.data = dm.load(map_file)
        self.selected: Optional[str] = self.data["datasets"][0]["key"] if self.data["datasets"] else None

        self._on_details_collapsed = on_details_collapsed
        self._hint = hint or (lambda _t: None)
        self._wrap_labels: List[tk.Label] = []

        self._f_title = font.Font(family="Segoe UI", size=14, weight="bold")
        self._f_sub = font.Font(family="Segoe UI", size=9)
        self._f_small = font.Font(family="Segoe UI", size=8)
        self._f_section = font.Font(family="Arial", size=8, weight="bold")
        self._f_cell = font.Font(family="Segoe UI Symbol", size=16)
        self._f_bold = font.Font(family="Segoe UI", size=11, weight="bold")
        self._f_name = font.Font(family="Segoe UI", size=11)
        self._f_body = font.Font(family="Segoe UI", size=10)
        self._f_body_bold = font.Font(family="Segoe UI", size=10, weight="bold")
        self._f_head = font.Font(family="Segoe UI", size=13, weight="bold")
        self._f_emoji = font.Font(family="Segoe UI Emoji", size=14)

        self.details_frame = tk.Frame(self, bg=_DETAILS_BG, width=_DETAILS_MIN_WIDTH)
        self.details_frame.pack(side=tk.RIGHT, fill=tk.Y)
        self.details_frame.pack_propagate(False)
        self._main = tk.Frame(self, bg=COLORS["bg_primary"])
        self._main.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_header()
        self._scroll = ScrollableFrame(self._main, bg=COLORS["bg_primary"])
        self._scroll.pack(fill=tk.BOTH, expand=True, padx=(20, 4), pady=(4, 12))
        self._content = self._scroll.scrollable_frame
        self._build_details_panel(bool(details_collapsed))
        self.refresh()

    # ------------------------------------------------------------ header

    def _build_header(self):
        header = tk.Frame(self._main, bg=COLORS["bg_primary"])
        header.pack(fill=tk.X, padx=20, pady=(18, 4))
        text = tk.Frame(header, bg=COLORS["bg_primary"])
        text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(text, text="Data Map", font=self._f_title, fg=COLORS["text_primary"],
                 bg=COLORS["bg_primary"], anchor="w").pack(fill=tk.X)
        tk.Label(text, text="Where your data lives, what moves it, and the details you'd otherwise forget",
                 font=self._f_sub, fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
                 anchor="w").pack(fill=tk.X)
        bar = tk.Frame(header, bg=COLORS["bg_primary"])
        bar.pack(side=tk.RIGHT, anchor="n")
        for label, command in (("＋ Dataset", self._new_dataset),
                               ("Locations", self._edit_locations),
                               ("Transports", self._edit_transports)):
            self._chip(bar, label, command).pack(side=tk.LEFT, padx=(6, 0))

    def _chip(self, parent, text, command, fg=None, bg=None):
        bg = bg or COLORS["bg_secondary"]
        chip = tk.Label(parent, text=text, font=self._f_sub, fg=fg or COLORS["text_primary"], bg=bg,
                        padx=10, pady=4, cursor="hand2", highlightthickness=1,
                        highlightbackground=COLORS["border"])
        chip.bind("<Button-1>", lambda _e: command())
        chip.bind("<Enter>", lambda _e: chip.configure(bg=COLORS["bg_hover"]))
        chip.bind("<Leave>", lambda _e: chip.configure(bg=bg))
        return chip

    # ----------------------------------------------------------- refresh

    def refresh(self):
        for child in self._content.winfo_children():
            child.destroy()
        if self.data.get("_load_error"):
            tk.Label(self._content, text=f"⚠ {self.data['_load_error']} — showing defaults; "
                                         "saving will replace the file.",
                     font=self._f_sub, fg=COLORS["warning"], bg=COLORS["bg_primary"], anchor="w",
                     wraplength=700, justify="left").pack(fill=tk.X, pady=(0, 8))
        self._build_transport_strip()
        self._build_matrix()
        self._build_legend()
        self._build_detail()

    def _save(self, message="Data map saved"):
        self.data.pop("_load_error", None)
        try:
            dm.save(self.data, self._map_file)
            self.data = dm.load(self._map_file)
            self._status(message, "success")
        except OSError as exc:
            self._status(f"Couldn't save data map: {exc}", "error")
        self.refresh()

    # --------------------------------------------------------- transports

    def _build_transport_strip(self):
        strip = tk.Frame(self._content, bg=COLORS["bg_primary"])
        strip.pack(fill=tk.X, pady=(0, 10))
        tk.Label(strip, text="MOVED BY", font=self._f_small, fg=COLORS["text_secondary"],
                 bg=COLORS["bg_primary"]).pack(side=tk.LEFT, padx=(0, 8))
        if not self.data["transports"]:
            self._chip(strip, "＋ Add a transport", self._edit_transports,
                       fg=COLORS["text_secondary"]).pack(side=tk.LEFT)
        for t in self.data["transports"]:
            used = sum(1 for ds in self.data["datasets"] for p in ds["places"] if p["transport"] == t["key"])
            label = f"{t['icon']} {t['name']}".strip() + (f"  ·{used}" if used else "")
            chip = self._chip(strip, label, lambda k=t["key"]: self._edit_transports(k))
            chip.pack(side=tk.LEFT, padx=(0, 6))
            if t.get("notes"):
                self._hover_status(chip, f"{t['name']}: {t['notes']}")

    def _hover_status(self, widget, text):
        widget.bind("<Enter>", lambda _e: self._status(text.replace("\n", "  ·  ")), add="+")

    # ------------------------------------------------------------- matrix

    def _build_matrix(self):
        locations = self.data["locations"]
        grid = tk.Frame(self._content, bg=COLORS["border"])
        grid.pack(fill=tk.X)
        bg_head = COLORS["bg_secondary"]

        corner = tk.Label(grid, text="DATASET", font=self._f_small, fg=COLORS["text_secondary"],
                          bg=bg_head, anchor="w", padx=10)
        corner.grid(row=0, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
        grid.grid_columnconfigure(0, minsize=_NAME_COL_MIN, weight=2)
        for col, loc in enumerate(locations, start=1):
            grid.grid_columnconfigure(col, minsize=_CELL_MIN, weight=1, uniform="loc")
            head = tk.Frame(grid, bg=bg_head, cursor="hand2")
            head.grid(row=0, column=col, sticky="nsew", padx=(0, 1), pady=(0, 1))
            parts = [head]
            if loc["icon"]:
                parts.append(tk.Label(head, text=loc["icon"], font=self._f_emoji,
                                      fg=COLORS["text_primary"], bg=bg_head))
                parts[-1].pack(pady=(6, 0))
            parts.append(tk.Label(head, text=loc["name"], font=self._f_sub,
                                  fg=COLORS["text_primary"], bg=bg_head))
            parts[-1].pack(pady=(0 if loc["icon"] else 6, 6))
            for w in parts:
                w.bind("<Button-1>", lambda _e, k=loc["key"]: self._edit_locations(k))
                w.bind("<Enter>", lambda _e, ws=parts: [x.configure(bg=COLORS["bg_hover"]) for x in ws])
                w.bind("<Leave>", lambda _e, ws=parts: [x.configure(bg=bg_head) for x in ws])
            tip = loc["name"] + (f" ({loc['address']})" if loc.get("address") else "")
            if loc.get("notes"):
                tip += f": {loc['notes']}"
            self._hover_status(head, tip)
        health_col = len(locations) + 1
        tk.Label(grid, text="SETUP", font=self._f_small, fg=COLORS["text_secondary"], bg=bg_head,
                 padx=10).grid(row=0, column=health_col, sticky="nsew", pady=(0, 1))
        grid.grid_columnconfigure(health_col, minsize=80)

        if not self.data["datasets"]:
            tk.Label(grid, text="No datasets yet — add one with ＋ Dataset", font=self._f_sub,
                     fg=COLORS["text_secondary"], bg=COLORS["bg_primary"], pady=20).grid(
                row=1, column=0, columnspan=health_col + 1, sticky="nsew")
            return

        names = {l["key"]: l["name"] for l in locations}
        for row, ds in enumerate(self.data["datasets"], start=1):
            selected = ds["key"] == self.selected
            row_bg = COLORS["bg_hover"] if selected else COLORS["bg_primary"]
            name = tk.Frame(grid, bg=row_bg, cursor="hand2")
            name.grid(row=row, column=0, sticky="nsew", padx=(0, 1), pady=(0, 1))
            if selected:
                tk.Frame(name, bg=self._accent, width=3).pack(side=tk.LEFT, fill=tk.Y)
            icon = tk.Label(name, text=ds["icon"], font=self._f_emoji, bg=row_bg,
                            fg=COLORS["text_primary"], padx=6)
            icon.pack(side=tk.LEFT)
            label = tk.Label(name, text=ds["name"], font=self._f_bold if selected else self._f_name,
                             bg=row_bg, fg=COLORS["text_primary"], anchor="w")
            label.pack(side=tk.LEFT, fill=tk.X, expand=True, pady=12)
            for w in (name, icon, label):
                w.bind("<Button-1>", lambda _e, k=ds["key"]: self._select(k))
                w.bind("<Double-Button-1>", lambda _e, d=ds: self._edit_dataset(d))

            for col, loc in enumerate(locations, start=1):
                self._build_cell(grid, row, col, ds, loc, row_bg)

            warnings = dm.warnings_for(ds, names)
            text, fg = ("✓", COLORS["success"]) if not warnings else (f"⚠ {len(warnings)}", COLORS["warning"])
            health = tk.Label(grid, text=text, font=self._f_sub, fg=fg, bg=row_bg, cursor="hand2")
            health.grid(row=row, column=health_col, sticky="nsew", pady=(0, 1))
            health.bind("<Button-1>", lambda _e, k=ds["key"]: self._select(k))
            if warnings:
                self._hover_status(health, " · ".join(warnings))

    def _build_cell(self, grid, row, col, ds, loc, row_bg):
        place = dm.place_at(ds, loc["key"])
        cell = tk.Frame(grid, bg=row_bg, cursor="hand2")
        cell.grid(row=row, column=col, sticky="nsew", padx=(0, 1), pady=(0, 1))
        widgets = [cell]
        if place:
            glyph, role_label, _hint = dm.ROLES[place["role"]]
            transport = dm.find(self.data["transports"], place["transport"])
            g = tk.Label(cell, text=glyph, font=self._f_cell, fg=ROLE_COLORS.get(place["role"]), bg=row_bg)
            g.pack(pady=(6, 0))
            sub = transport["name"] if transport else role_label
            s = tk.Label(cell, text=sub, font=self._f_small, fg=COLORS["text_secondary"], bg=row_bg)
            s.pack(pady=(0, 6))
            widgets += [g, s]
            tip = f"{ds['name']} on {loc['name']}: {role_label}"
            if transport:
                tip += f" via {transport['name']}"
            if place["path"]:
                tip += f"  ·  {place['path']}"
            if place["notes"]:
                tip += f"  ·  {place['notes']}"
        else:
            g = tk.Label(cell, text="·", font=self._f_cell, fg=COLORS["border"], bg=row_bg)
            g.pack(expand=True)
            widgets.append(g)
            tip = f"Click to say what {ds['name']} has on {loc['name']}"

        def enter(_e):
            for w in widgets:
                w.configure(bg=COLORS["bg_hover"])
            if not place:
                g.configure(text="＋", fg=COLORS["text_secondary"])
            self._status(tip)

        def leave(_e):
            for w in widgets:
                w.configure(bg=row_bg)
            if not place:
                g.configure(text="·", fg=COLORS["border"])

        for w in widgets:
            w.bind("<Enter>", enter)
            w.bind("<Leave>", leave)
            w.bind("<Button-1>", lambda _e: self._edit_place(ds, loc))

    def _build_legend(self):
        legend = tk.Frame(self._content, bg=COLORS["bg_primary"])
        legend.pack(fill=tk.X, pady=(6, 14))
        for key, (glyph, label, hint) in dm.ROLES.items():
            item = tk.Frame(legend, bg=COLORS["bg_primary"])
            item.pack(side=tk.LEFT, padx=(0, 12))
            tk.Label(item, text=glyph, font=self._f_sub, fg=ROLE_COLORS[key],
                     bg=COLORS["bg_primary"]).pack(side=tk.LEFT)
            lab = tk.Label(item, text=label, font=self._f_small, fg=COLORS["text_secondary"],
                           bg=COLORS["bg_primary"])
            lab.pack(side=tk.LEFT, padx=(3, 0))
            self._hover_status(lab, f"{label}: {hint}")

    # ------------------------------------------------------------- detail

    # ------------------------------------------------- details side panel

    def _build_details_panel(self, collapsed):
        bg = _DETAILS_BG
        self._details_collapsed = collapsed
        self._details_width = None

        def bind_toggle(*widgets, glyph):
            def on_enter(_e):
                glyph.configure(fg="white")
                self._hint("Show/Hide Dataset Panel")

            def on_leave(_e):
                glyph.configure(fg=COLORS["text_secondary"])
                self._hint("")

            for w in widgets:
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda _e: self.toggle_details_panel())
                w.bind("<Enter>", on_enter)
                w.bind("<Leave>", on_leave)

        # Collapsed: a thin rail, clickable anywhere, to bring it back.
        self.details_rail = tk.Frame(self.details_frame, bg=bg)
        expand_glyph = tk.Label(self.details_rail, text="«", bg=bg, fg=COLORS["text_secondary"],
                                font=("Arial", 13))
        expand_glyph.pack(fill=tk.X, pady=(6, 0))
        bind_toggle(self.details_rail, expand_glyph, glyph=expand_glyph)

        self.details_content = tk.Frame(self.details_frame, bg=bg)
        header = tk.Frame(self.details_content, bg=bg)
        header.pack(fill=tk.X, padx=(_DETAILS_PAD, 4), pady=(6, 0))
        tk.Label(header, text="SELECTED DATASET", bg=bg, fg=COLORS["text_secondary"],
                 font=self._f_section).pack(side=tk.LEFT)
        collapse_glyph = tk.Label(header, text="»", bg=bg, fg=COLORS["text_secondary"],
                                  font=("Arial", 13), padx=8)
        collapse_glyph.pack(side=tk.RIGHT)
        bind_toggle(collapse_glyph, glyph=collapse_glyph)
        self._detail_scroll = ScrollableFrame(self.details_content, bg=bg)
        self._detail_scroll.pack(fill=tk.BOTH, expand=True, pady=(4, 8))
        self._detail = self._detail_scroll.scrollable_frame

        self._apply_details_state()
        self.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event):
        if event.widget is self:
            self._apply_details_width(event.width)

    def _wrap(self):
        return (self._details_width or _DETAILS_MIN_WIDTH) - 2 * _DETAILS_PAD - 24

    def _apply_details_width(self, outer_width=None):
        """Size the panel for the view's width (or to the collapsed rail),
        using the tracker's width rules, and re-wrap its text."""
        if self._details_collapsed:
            width = _DETAILS_RAIL_WIDTH
        else:
            if outer_width is None:
                outer_width = self.winfo_width()
            width = max(_DETAILS_MIN_WIDTH,
                        min(_DETAILS_MAX_WIDTH, int(outer_width * _DETAILS_WIDTH_FRACTION)))
        if width == self._details_width:
            return
        self._details_width = width
        self.details_frame.configure(width=width)
        if not self._details_collapsed:
            for label in self._wrap_labels:
                try:
                    label.configure(wraplength=self._wrap() - getattr(label, "indent", 0))
                except tk.TclError:
                    pass

    def _apply_details_state(self):
        if self._details_collapsed:
            self.details_content.pack_forget()
            self.details_rail.pack(fill=tk.BOTH, expand=True)
        else:
            self.details_rail.pack_forget()
            self.details_content.pack(fill=tk.BOTH, expand=True)
        self._details_width = None
        self._apply_details_width()

    def sync_details_collapsed(self, collapsed):
        """Match a folded state set elsewhere (the tracker's panel)."""
        if bool(collapsed) != self._details_collapsed:
            self._details_collapsed = bool(collapsed)
            self._apply_details_state()

    def toggle_details_panel(self):
        """Fold the Selected Dataset panel to a thin rail, or bring it back."""
        self._details_collapsed = not self._details_collapsed
        self._apply_details_state()
        if self._on_details_collapsed:
            self._on_details_collapsed(self._details_collapsed)

    def _build_detail(self):
        for child in self._detail.winfo_children():
            child.destroy()
        self._wrap_labels = []
        bg = _DETAILS_BG
        ds = dm.find(self.data["datasets"], self.selected) if self.selected else None
        inner = tk.Frame(self._detail, bg=bg)
        inner.pack(fill=tk.X, padx=_DETAILS_PAD)
        if ds is None:
            self._wrapped(inner, "Select a dataset in the map.", fg=COLORS["text_secondary"])
            return

        tk.Label(inner, text=f"{ds['icon']}  {ds['name']}".strip(), font=self._f_head,
                 fg=COLORS["text_primary"], bg=bg, anchor="w").pack(fill=tk.X, pady=(6, 0))
        if ds.get("description"):
            self._wrapped(inner, ds["description"], fg=COLORS["text_secondary"], font=self._f_sub)
        actions = tk.Frame(inner, bg=bg)
        actions.pack(fill=tk.X, pady=(8, 0))
        self._chip(actions, "Edit", lambda: self._edit_dataset(ds), bg=bg).pack(side=tk.LEFT)
        if ds.get("app") and self._launch_app:
            self._chip(actions, f"Launch {ds['app']} ↗", lambda: self._launch_app(ds["app"]),
                       fg=self._accent, bg=bg).pack(side=tk.LEFT, padx=(6, 0))

        rows = [r for r in (self._resolve_tool(ref) for ref in ds["tools"]) if r]
        used = {p["transport"] for p in ds["places"] if p["transport"]}
        for t in self.data["transports"]:
            if t["key"] in used and t.get("tool"):
                r = self._resolve_tool(t["tool"])
                if r and all(r[:3] != x[:3] for x in rows):
                    rows.append(r)
        self._section(inner, "TOOLS")
        if not rows:
            self._wrapped(inner, "No tools linked. Add some with Edit.", fg=COLORS["text_secondary"],
                          font=self._f_sub)
        tools = tk.Frame(inner, bg=bg)
        tools.pack(fill=tk.X)
        for category_key, script_key, subcat_key, script_data in rows:
            self._make_tool_row(tools, category_key, script_key, subcat_key, script_data)

        self._section(inner, "WHERE IT LIVES")
        if not ds["places"]:
            self._wrapped(inner, "Not mapped yet. Click a cell in its row.", fg=COLORS["text_secondary"],
                          font=self._f_sub)
        order = [l["key"] for l in self.data["locations"]]
        for place in sorted(ds["places"], key=lambda p: order.index(p["location"])):
            self._place_row(inner, ds, place)

        warnings = dm.warnings_for(ds, {l["key"]: l["name"] for l in self.data["locations"]})
        if warnings:
            self._section(inner, "SETUP GAPS")
            for w in warnings:
                self._wrapped(inner, f"⚠  {w}", fg=COLORS["warning"], font=self._f_sub)

        self._section(inner, "NOTES")
        if ds.get("notes"):
            self._wrapped(inner, ds["notes"])
        else:
            self._wrapped(inner, "Nothing written down yet. Use Edit to note what you'd forget: "
                                 "server addresses, database names, why it's set up this way.",
                          fg=COLORS["text_secondary"], font=self._f_sub)

    def _wrapped(self, parent, text, fg=None, font=None, indent=0):
        label = tk.Label(parent, text=text, font=font or self._f_body, bg=_DETAILS_BG,
                         fg=fg or COLORS["text_primary"], anchor="w", justify="left",
                         wraplength=self._wrap() - indent)
        label.indent = indent
        label.pack(fill=tk.X)
        self._wrap_labels.append(label)
        return label

    def _section(self, parent, text):
        tk.Label(parent, text=text, font=self._f_section, fg=COLORS["text_secondary"],
                 bg=_DETAILS_BG, anchor="w").pack(fill=tk.X, pady=(16, 4))

    def _place_row(self, parent, ds, place):
        bg = _DETAILS_BG
        loc = dm.find(self.data["locations"], place["location"])
        transport = dm.find(self.data["transports"], place["transport"])
        glyph, role_label, _ = dm.ROLES[place["role"]]
        row = tk.Frame(parent, bg=bg, cursor="hand2")
        row.pack(fill=tk.X, pady=(0, 6))
        top = tk.Frame(row, bg=bg)
        top.pack(fill=tk.X)
        g = tk.Label(top, text=glyph, font=self._f_body, fg=ROLE_COLORS[place["role"]], bg=bg, width=2)
        g.pack(side=tk.LEFT)
        name = tk.Label(top, text=f"{loc['icon']} {loc['name']}".strip(), font=self._f_body_bold,
                        fg=COLORS["text_primary"], bg=bg)
        name.pack(side=tk.LEFT)
        body = tk.Frame(row, bg=bg)
        body.pack(fill=tk.X, padx=(24, 0))
        widgets = [row, top, g, name, body]
        widgets.append(self._wrapped(body, role_label + (f" via {transport['name']}" if transport else ""),
                                     fg=COLORS["text_secondary"], font=self._f_sub, indent=24))
        if place["path"]:
            path = self._wrapped(body, place["path"], fg=self._accent, font=self._f_sub, indent=24)
            path.configure(cursor="hand2")
            path.bind("<Button-1>", lambda _e, p=place["path"]: self._open(p))
        if place["notes"]:
            widgets.append(self._wrapped(body, place["notes"], fg=COLORS["text_secondary"],
                                         font=self._f_sub, indent=24))
        for w in widgets:
            w.bind("<Button-1>", lambda _e: self._edit_place(ds, loc))

    def _open(self, path):
        expanded = os.path.expandvars(path)
        if os.path.exists(expanded):
            open_path(expanded)
        else:
            self._status(f"{path} isn't reachable from this machine", "warning")
        return "break"

    # ------------------------------------------------------------ editing

    def _select(self, key):
        self.selected = key
        self.refresh()

    def _edit_place(self, ds, loc):
        result = PlaceDialog(self, ds["name"], loc["name"], dm.place_at(ds, loc["key"]),
                             self.data["transports"]).show()
        if result is None:
            return
        target = dm.find(self.data["datasets"], ds["key"])
        dm.set_place(target, None if result == "remove" else result, loc["key"])
        self.selected = ds["key"]
        self._save(f"{ds['name']} on {loc['name']} saved")

    def _new_dataset(self):
        self._edit_dataset(None)

    def _edit_dataset(self, ds):
        result = DatasetDialog(self, ds, [d["key"] for d in self.data["datasets"]],
                               self._app_names(), self._tool_refs()).show()
        if result is None:
            return
        if result == "delete":
            self.data["datasets"] = [d for d in self.data["datasets"] if d["key"] != ds["key"]]
            self.selected = self.data["datasets"][0]["key"] if self.data["datasets"] else None
            self._save(f"{ds['name']} removed")
            return
        existing = dm.find(self.data["datasets"], result["key"])
        if existing:
            existing.update(result)
        else:
            self.data["datasets"].append(result)
        self.selected = result["key"]
        self._save(f"{result['name']} saved")

    def _edit_locations(self, select_key=None):
        fields = [("name", "Name", "entry", None), ("icon", "Icon", "short", None),
                  ("address", "Address", "entry", None), ("notes", "Notes", "text", None)]
        self._edit_list("locations", "Locations", "Where data can live",
                        "Machines, servers, phones, cloud accounts, drives. They're the matrix's "
                        "columns, in this order. Address is a hostname, IP or URL.",
                        fields, "New location", select_key, dm.remove_location)

    def _edit_transports(self, select_key=None):
        apps = [(n, n) for n in self._app_names()]
        tools = [(tuple(r), l) for r, l in self._tool_refs()]
        fields = [("name", "Name", "entry", None), ("icon", "Icon", "short", None),
                  ("app", "App", "dropdown", (apps, False)),
                  ("tool", "Tool", "dropdown", (tools, True)),
                  ("notes", "Notes", "text", None)]
        self._edit_list("transports", "Transports", "What moves your data",
                        "Sync services, copy jobs, cables, shared databases. A linked tool shows up "
                        "on every dataset that uses this transport.",
                        fields, "New transport", select_key, dm.remove_transport)

    def _edit_list(self, field, title, heading, sub, fields, new_label, select_key, remove):
        items = self.data[field]
        dialog = ListManagerDialog(self, title, heading, sub, items, fields, new_label)
        if select_key:
            index = next((i for i, it in enumerate(items) if it["key"] == select_key), None)
            if index is not None:
                dialog._select(index)
        result = dialog.show()
        if result is None:
            return
        kept = {i["key"] for i in result}
        for item in items:
            if item["key"] not in kept:
                remove(self.data, item["key"])
        self.data[field] = result
        self._save(f"{title} saved")
