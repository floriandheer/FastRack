"""
Sandbox Browser Panel
Author: Florian Dheer
Description: Embedded file/folder browser for the Sandbox category's loose
files. Like the project explorer it offers a list view (lazy tree) and a grid
view (cards, one folder at a time), each with its own size slider. Files get
free-form tags and sibling-file versioning (name_v002.ext: only the latest
version is shown, the side panel lists the rest and can version a document
up; tags apply to every version). Fills the same right-hand panel slot
fastrack_hub.py otherwise gives to ProjectTrackerApp (creative categories) or
InvoiceManager (Business).
"""

import json
import os
import tkinter as tk
from datetime import datetime
from tkinter import ttk, font
from typing import Dict, List, Optional, Tuple

from shared_appdata import get_appdata_path
from shared_logging import get_logger
from ui_theme import COLORS
from sandbox_tag_store import SandboxTagStore
import sandbox_versioning as versioning
from shared_open_path import open_path, reveal_path

logger = get_logger(__name__)

# Sentinel text for the lazy-load placeholder child inserted under every
# unexpanded folder node, so the expand arrow shows without scanning the
# whole subtree up front.
_LOADING_PLACEHOLDER = "…loading…"

# Same dark surfaces the project explorer uses.
_BG = "#0d1117"
_CARD_BG = "#1c2128"
_CARD_BG_SELECTED = "#2d333b"
_ACCENT = "#58a6ff"
_MUTED = "#8b949e"

_GRID_BASE_CARD = 120
_GRID_CARD_PAD = 16
_GRID_MAX_CARDS = 400
_LIST_BASE_ROW = 32
_LIST_BASE_FONT = 10

# (glyph, accent colour) per kind of file; folders get their own.
_FOLDER_STYLE = ("\U0001F4C1", "#d29922")
_KIND_STYLES = {
    "image": ("\U0001F5BC", "#3fb950"),
    "video": ("\U0001F3AC", "#a371f7"),
    "audio": ("\U0001F3B5", "#f778ba"),
    "3d": ("\U0001F9CA", "#f0883e"),
    "document": ("\U0001F4C4", "#58a6ff"),
    "code": ("\U0001F9E9", "#39c5cf"),
    "archive": ("\U0001F5DC", "#8b949e"),
    "other": ("\U0001F4C4", "#6e7681"),
}
_EXT_KINDS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff", ".psd",
              ".svg", ".exr", ".hdr", ".raw", ".cr2", ".nef", ".arw", ".ai", ".xcf"},
    "video": {".mp4", ".mov", ".avi", ".mkv", ".webm", ".wmv", ".prproj"},
    "audio": {".wav", ".mp3", ".flac", ".aif", ".aiff", ".ogg", ".m4a", ".als", ".flp"},
    "3d": {".blend", ".fbx", ".obj", ".stl", ".ma", ".mb", ".c4d", ".max", ".usd", ".usdz",
           ".glb", ".gltf", ".abc", ".hip", ".hipnc", ".spp"},
    "document": {".doc", ".docx", ".pdf", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx",
                 ".csv", ".ppt", ".pptx"},
    "code": {".py", ".js", ".ts", ".html", ".css", ".json", ".ps1", ".bat", ".sh", ".lua",
             ".glsl", ".cs", ".cpp", ".h", ".td", ".toe"},
    "archive": {".zip", ".rar", ".7z", ".tar", ".gz"},
}


def _kind_style(path: str, is_dir: bool) -> Tuple[str, str]:
    if is_dir:
        return _FOLDER_STYLE
    ext = os.path.splitext(path)[1].lower()
    for kind, exts in _EXT_KINDS.items():
        if ext in exts:
            return _KIND_STYLES[kind]
    return _KIND_STYLES["other"]


class SandboxViewSettings:
    """Remembers view mode and the two size sliders between sessions."""

    DEFAULTS = {"view_mode": "list", "list_scale": 100, "grid_scale": 100}

    def __init__(self):
        self.path = get_appdata_path() / "sandbox_settings.json"
        self.data = dict(self.DEFAULTS)
        try:
            if self.path.exists():
                with open(self.path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self.data.update({k: v for k, v in loaded.items() if k in self.DEFAULTS})
        except Exception as e:
            logger.warning(f"Failed to load sandbox view settings: {e}")

    def get(self, key):
        return self.data.get(key, self.DEFAULTS[key])

    def set(self, key, value):
        self.data[key] = value
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save sandbox view settings: {e}")


class SandboxBrowserPanel(tk.Frame):
    def __init__(self, parent, root_path: str, status_callback=None):
        super().__init__(parent, bg=COLORS["bg_primary"])
        self.root_path = root_path
        self.status_callback = status_callback
        self.tag_store = SandboxTagStore()
        self.view_settings = SandboxViewSettings()

        # iid -> absolute path, for both the normal tree and filtered results
        self._iid_paths = {}
        self._filter_after_id = None

        # Single selection shared by list and grid; the side panel follows it.
        self._selected: Optional[str] = None

        # Grid state
        self._grid_dir = root_path
        self._grid_entries: List[dict] = []
        self._grid_cards: List[dict] = []
        self._grid_cols = 1
        self._grid_resize_job = None

        self._build_ui()
        self._apply_view_mode(initial=True)
        self.refresh()

    def _notify(self, message: str, level: str = "info"):
        """Fire the host status callback, but never let it crash us.

        This panel can be constructed very early (e.g. Sandbox was the
        last-selected category, so it's rebuilt while the Hub restores
        session state during its own __init__ — before the Hub's own
        status bar widget exists yet). A status notification is a nice-to-
        have, never worth taking down tree population over.
        """
        if not self.status_callback:
            return
        try:
            self.status_callback(message, level)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_styles(self):
        style = ttk.Style()
        style.configure("Sandbox.Vertical.TScrollbar", background=_CARD_BG, troughcolor=_BG,
                        bordercolor=_BG, lightcolor=_CARD_BG, darkcolor=_CARD_BG,
                        arrowcolor=_MUTED)
        # clam paints a disabled (nothing-to-scroll) bar light grey otherwise
        style.map("Sandbox.Vertical.TScrollbar",
                  background=[("disabled", _CARD_BG), ("active", _CARD_BG_SELECTED)])
        for name, bg in (("Sandbox.Treeview", _BG), ("SandboxSide.Treeview", _BG)):
            style.configure(name, background=bg, foreground="white", fieldbackground=bg,
                            borderwidth=0, rowheight=_LIST_BASE_ROW if name == "Sandbox.Treeview" else 24)
            style.map(name, background=[("selected", _CARD_BG_SELECTED)],
                      foreground=[("selected", "white")])
            style.layout(name, [(f"{name}.treearea", {"sticky": "nswe"})])
        style.configure("Sandbox.Treeview.Heading", background=_CARD_BG, foreground="white",
                        font=("Arial", 10, "bold"), relief="flat", borderwidth=0,
                        lightcolor=_CARD_BG, darkcolor=_CARD_BG, bordercolor=_BG)
        style.map("Sandbox.Treeview.Heading", background=[("active", _CARD_BG_SELECTED)])

    def _build_ui(self):
        self._setup_styles()
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # --- Header ---
        header = tk.Frame(self, bg=COLORS["bg_primary"])
        header.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 10))
        header.columnconfigure(1, weight=1)

        tk.Label(
            header, text="Sandbox", font=font.Font(family="Segoe UI", size=14, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_primary"]
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            header, text=self.root_path, font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"]
        ).grid(row=1, column=0, sticky="w", columnspan=2)

        # --- Top bar: filter / refresh / open in explorer / size / view ---
        top_bar = tk.Frame(self, bg=COLORS["bg_primary"])
        top_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))
        top_bar.columnconfigure(0, weight=1)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._schedule_filter())
        filter_entry = tk.Entry(
            top_bar, textvariable=self.filter_var,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"], relief=tk.FLAT
        )
        filter_entry.grid(row=0, column=0, sticky="ew", ipady=4)
        self.filter_entry = filter_entry
        self._set_placeholder(filter_entry, "Filter by name or tag...")

        refresh_btn = tk.Button(
            top_bar, text="Refresh", command=self.refresh,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2"
        )
        refresh_btn.grid(row=0, column=1, padx=(8, 0))

        explorer_btn = tk.Button(
            top_bar, text="Open in Explorer", command=self._open_in_explorer,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2"
        )
        explorer_btn.grid(row=0, column=2, padx=(8, 0))

        # One size slider that serves whichever view is active (each view
        # keeps its own remembered value), same look as the project explorer.
        scale_frame = tk.Frame(top_bar, bg=COLORS["bg_primary"])
        scale_frame.grid(row=0, column=3, padx=(14, 0))
        tk.Label(scale_frame, text="Size:", bg=COLORS["bg_primary"], fg=_MUTED,
                 font=("Arial", 8)).pack(side=tk.LEFT, padx=(0, 2))
        self.scale_var = tk.IntVar(value=100)
        self._scale_applying = False
        self.scale_slider = tk.Scale(
            scale_frame, from_=50, to=150, orient=tk.HORIZONTAL, variable=self.scale_var,
            command=self._on_scale_changed, bg=COLORS["bg_primary"], fg="white",
            highlightthickness=0, troughcolor=_CARD_BG, activebackground=_ACCENT,
            length=80, showvalue=False, sliderlength=15,
        )
        self.scale_slider.pack(side=tk.LEFT)

        self.view_mode = tk.StringVar(value="list")
        toggle = tk.Frame(top_bar, bg=COLORS["bg_primary"])
        toggle.grid(row=0, column=4, padx=(10, 0))
        self._view_mode_buttons = {}
        for mode, glyph in (("list", "≡"), ("grid", "⊞")):
            btn = tk.Radiobutton(
                toggle, text=glyph, variable=self.view_mode, value=mode,
                bg=_CARD_BG, fg=_MUTED, selectcolor=_ACCENT,
                activebackground=_CARD_BG_SELECTED, activeforeground="white",
                indicatoron=False, padx=10, pady=2, font=("Arial", 12, "bold"),
                borderwidth=0, highlightthickness=0, command=self._switch_view,
            )
            btn.pack(side=tk.LEFT, padx=(0, 2))
            self._view_mode_buttons[mode] = btn

        # --- View (list/grid) + side panel ---
        body = tk.Frame(self, bg=COLORS["bg_primary"])
        body.grid(row=2, column=0, sticky="nsew", padx=15, pady=(0, 15))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2, minsize=300)
        body.rowconfigure(0, weight=1)

        self.view_container = tk.Frame(body, bg=_BG, highlightthickness=0, bd=0)
        self.view_container.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        self._build_list_view()
        self._build_grid_view()
        self._build_tag_editor(body)

    def _build_list_view(self):
        self.list_frame = tk.Frame(self.view_container, bg=_BG, highlightthickness=0, bd=0)

        self.tree = ttk.Treeview(
            self.list_frame, columns=("version", "tags"), show="tree headings",
            style="Sandbox.Treeview", selectmode="browse",
        )
        self.tree.heading("#0", text="Name", anchor=tk.W)
        self.tree.heading("version", text="Version", anchor=tk.W)
        self.tree.heading("tags", text="Tags", anchor=tk.W)
        self.tree.column("#0", width=240, minwidth=120)
        self.tree.column("version", width=70, minwidth=60, stretch=False)
        self.tree.column("tags", width=130, minwidth=60)

        tree_scroll = ttk.Scrollbar(self.list_frame, orient="vertical", command=self.tree.yview,
                                    style="Sandbox.Vertical.TScrollbar")
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Return>", self._on_double_click)
        self.tree.bind("<Alt-Up>", lambda e: self._go_up())
        self.tree.bind("<Control-e>", lambda e: self._reveal_selected())
        self.tree.bind("<Button-3>", self._on_tree_right_click)

    def _build_grid_view(self):
        self.grid_frame = tk.Frame(self.view_container, bg=_BG, highlightthickness=0, bd=0)

        self.crumb_bar = tk.Frame(self.grid_frame, bg=_BG)
        self.crumb_bar.pack(fill=tk.X, pady=(0, 6))

        body = tk.Frame(self.grid_frame, bg=_BG)
        body.pack(fill=tk.BOTH, expand=True)
        self.grid_canvas = tk.Canvas(body, bg=_BG, highlightthickness=0, bd=0, takefocus=True)
        grid_scroll = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self.grid_canvas.yview,
                                    style="Sandbox.Vertical.TScrollbar")
        self.grid_inner = tk.Frame(self.grid_canvas, bg=_BG, highlightthickness=0, bd=0)
        self.grid_canvas.configure(yscrollcommand=grid_scroll.set)
        grid_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.grid_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.grid_window = self.grid_canvas.create_window((0, 0), window=self.grid_inner, anchor="nw")

        self.grid_inner.bind(
            "<Configure>",
            lambda e: self.grid_canvas.configure(scrollregion=self.grid_canvas.bbox("all")))
        self.grid_canvas.bind("<Configure>", self._on_grid_canvas_resize)
        self.grid_canvas.bind("<MouseWheel>", self._on_grid_mousewheel)
        self.grid_inner.bind("<MouseWheel>", self._on_grid_mousewheel)

        self.grid_canvas.bind("<Left>", lambda e: self._grid_move(-1, 0))
        self.grid_canvas.bind("<Right>", lambda e: self._grid_move(1, 0))
        self.grid_canvas.bind("<Up>", lambda e: self._grid_move(0, -1))
        self.grid_canvas.bind("<Down>", lambda e: self._grid_move(0, 1))
        self.grid_canvas.bind("<Return>", lambda e: self.activate_selected())
        self.grid_canvas.bind("<BackSpace>", lambda e: self._grid_go_up())
        self.grid_canvas.bind("<Alt-Up>", lambda e: self._go_up())
        self.grid_canvas.bind("<Control-e>", lambda e: self._reveal_selected())

    def _build_tag_editor(self, parent):
        editor = tk.Frame(parent, bg=COLORS["bg_card"])
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)

        sel_header = tk.Frame(editor, bg=COLORS["bg_card"])
        sel_header.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 4))
        sel_header.columnconfigure(0, weight=1)
        tk.Label(
            sel_header, text="Selected item", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=0, column=0, sticky="w")
        self.reveal_btn = tk.Button(
            sel_header, text="Show in Explorer", command=self._reveal_selected,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"], activeforeground=COLORS["text_primary"],
            relief=tk.FLAT, cursor="hand2", padx=8, pady=1, state=tk.DISABLED,
            font=font.Font(family="Segoe UI", size=8),
        )
        self.reveal_btn.grid(row=0, column=1, sticky="e")

        self.selected_path_var = tk.StringVar(value="Nothing selected")
        tk.Label(
            editor, textvariable=self.selected_path_var, font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"], wraplength=260, justify="left", anchor="w"
        ).grid(row=1, column=0, sticky="ew", padx=12)

        tk.Label(
            editor, text="Tags (comma-separated)", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(16, 4))

        self.tags_var = tk.StringVar()
        self.tags_entry = tk.Entry(
            editor, textvariable=self.tags_var,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"], relief=tk.FLAT, state=tk.DISABLED,
            disabledbackground=COLORS["bg_secondary"], disabledforeground=COLORS["text_secondary"],
        )
        self.tags_entry.grid(row=3, column=0, sticky="ew", padx=12, ipady=4)
        self.tags_entry.bind("<Return>", lambda e: self._save_tags())

        self.save_tags_btn = tk.Button(
            editor, text="Save Tags", command=self._save_tags,
            bg=COLORS["accent"], fg="#ffffff",
            activebackground=COLORS["accent_hover"], relief=tk.FLAT, cursor="hand2",
            state=tk.DISABLED
        )
        self.save_tags_btn.grid(row=4, column=0, sticky="w", padx=12, pady=(8, 16))

        tk.Label(
            editor, text="Existing tags", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=5, column=0, sticky="w", padx=12, pady=(4, 4))

        self.existing_tags_frame = tk.Frame(editor, bg=COLORS["bg_card"])
        self.existing_tags_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))

        self._build_versions_section(editor)

    def _build_versions_section(self, editor):
        editor.rowconfigure(9, weight=1)

        tk.Frame(editor, height=1, bg=COLORS["border"]).grid(
            row=7, column=0, sticky="ew", padx=12, pady=(0, 10))

        header = tk.Frame(editor, bg=COLORS["bg_card"])
        header.grid(row=8, column=0, sticky="ew", padx=12, pady=(0, 6))
        header.columnconfigure(0, weight=1)
        tk.Label(
            header, text="Versions", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=0, column=0, sticky="w")
        self.version_up_btn = tk.Button(
            header, text="Version up", command=self._version_up,
            bg=COLORS["accent"], fg="#ffffff",
            activebackground=COLORS["accent_hover"], activeforeground="#ffffff",
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2, state=tk.DISABLED,
            font=font.Font(family="Segoe UI", size=9),
        )
        self.version_up_btn.grid(row=0, column=1, sticky="e")

        list_frame = tk.Frame(editor, bg=COLORS["bg_card"])
        list_frame.grid(row=9, column=0, sticky="nsew", padx=12, pady=(0, 4))
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        self.versions_tree = ttk.Treeview(
            list_frame, columns=("date", "size"), show="tree", height=5, selectmode="browse",
            style="SandboxSide.Treeview")
        self.versions_tree.column("#0", width=110)
        self.versions_tree.column("date", width=110, anchor="w")
        self.versions_tree.column("size", width=60, anchor="e")
        self.versions_tree.grid(row=0, column=0, sticky="nsew")
        versions_scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.versions_tree.yview,
                                        style="Sandbox.Vertical.TScrollbar")
        versions_scroll.grid(row=0, column=1, sticky="ns")
        self.versions_tree.configure(yscrollcommand=versions_scroll.set)
        self.versions_tree.bind("<Double-Button-1>", self._on_version_double_click)
        self.versions_tree.bind("<Button-3>", self._on_version_right_click)
        self._version_paths = {}  # versions_tree iid -> absolute path

        self.versions_hint_var = tk.StringVar(value="Select a file to see its versions.")
        tk.Label(
            editor, textvariable=self.versions_hint_var, font=font.Font(family="Segoe UI", size=8),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"], wraplength=260,
            justify="left", anchor="w",
        ).grid(row=10, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _set_placeholder(self, entry: tk.Entry, text: str):
        entry.insert(0, text)
        entry.config(fg=COLORS["text_secondary"])

        def on_focus_in(_e):
            if entry.get() == text:
                entry.delete(0, tk.END)
                entry.config(fg=COLORS["text_primary"])

        def on_focus_out(_e):
            if not entry.get():
                entry.insert(0, text)
                entry.config(fg=COLORS["text_secondary"])

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        self._filter_placeholder = text

    # ------------------------------------------------------------------
    # View mode + size
    # ------------------------------------------------------------------

    # --- hooks for the hub's WASD / keyboard navigation -------------------

    def focus_browser(self):
        """Give the active view keyboard focus (hub: D from the left panels)
        and make sure something is selected so arrow keys have a start."""
        if self.view_mode.get() == "grid":
            self.grid_canvas.focus_set()
            if not self._selected and self._grid_entries:
                self._set_selection(self._grid_entries[0]["path"])
        else:
            self.tree.focus_set()
            if not self.tree.selection():
                first = next(iter(self.tree.get_children("")), None)
                if first and first in self._iid_paths:
                    self.tree.selection_set(first)
                    self.tree.focus(first)

    def focus_filter(self):
        """Put the cursor in the filter box (hub: / key)."""
        self.filter_entry.focus_set()
        self.filter_entry.select_range(0, tk.END)

    def activate_selected(self):
        """Enter: open the selected file / step into the selected folder."""
        if self.view_mode.get() == "grid":
            self._grid_activate(self._selected)
        else:
            self._on_double_click(None)
        return "break"

    def _toggle_view_mode(self):
        """Flip between list and grid (bound to the hub's T key)."""
        self.view_mode.set("grid" if self.view_mode.get() == "list" else "list")
        self._switch_view()

    def _apply_view_mode(self, initial: bool = False):
        self.view_mode.set(self.view_settings.get("view_mode"))
        self._apply_scale_for_mode()
        self._show_view(initial=True)

    def _apply_scale_for_mode(self):
        """Point the shared slider at the active view's range and value."""
        grid = self.view_mode.get() == "grid"
        self._scale_applying = True
        try:
            self.scale_slider.config(from_=100 if grid else 50, to=225 if grid else 150)
            self.scale_var.set(self.view_settings.get("grid_scale" if grid else "list_scale"))
        finally:
            self._scale_applying = False
        self._apply_list_scale(self.view_settings.get("list_scale"))

    def _on_scale_changed(self, value):
        if self._scale_applying:
            return
        value = int(float(value))
        if self.view_mode.get() == "grid":
            if value == self.view_settings.get("grid_scale"):
                return
            self.view_settings.set("grid_scale", value)
            self._populate_grid()
        else:
            if value == self.view_settings.get("list_scale"):
                return
            self.view_settings.set("list_scale", value)
            self._apply_list_scale(value)

    def _apply_list_scale(self, value):
        scale = int(value) / 100.0
        style = ttk.Style()
        style.configure("Sandbox.Treeview", rowheight=int(_LIST_BASE_ROW * scale),
                        font=("Arial", max(7, int(_LIST_BASE_FONT * scale))))

    def _switch_view(self):
        self.view_settings.set("view_mode", self.view_mode.get())
        self._apply_scale_for_mode()
        self._show_view()

    def _show_view(self, initial: bool = False):
        view = self.view_mode.get()
        for mode, btn in self._view_mode_buttons.items():
            btn.configure(fg="white" if mode == view else _MUTED)

        if view == "list":
            self.grid_frame.pack_forget()
            self.list_frame.pack(fill=tk.BOTH, expand=True)
            if not initial and self._selected:
                self._reveal_in_tree(self._selected)
            if not initial:
                self.tree.focus_set()
        else:
            self.list_frame.pack_forget()
            self.grid_frame.pack(fill=tk.BOTH, expand=True)
            if self._selected and os.path.exists(self._selected):
                self._grid_dir = os.path.dirname(self._selected)
            if not self._is_inside_root(self._grid_dir):
                self._grid_dir = self.root_path
            self.update_idletasks()
            self._populate_grid()
            if not initial:
                self.grid_canvas.focus_set()

    def _is_inside_root(self, path: str) -> bool:
        root = os.path.normcase(os.path.abspath(self.root_path))
        p = os.path.normcase(os.path.abspath(path))
        return p == root or p.startswith(root + os.sep)

    # ------------------------------------------------------------------
    # Tree population (list view)
    # ------------------------------------------------------------------

    def _active_filter(self) -> str:
        text = self.filter_var.get().strip()
        if text and text != getattr(self, "_filter_placeholder", None):
            return text
        return ""

    def refresh(self):
        """Rebuild the views from disk, respecting any active filter."""
        self.tree.delete(*self.tree.get_children(""))
        self._iid_paths.clear()

        filter_text = self._active_filter()
        if filter_text:
            self._render_filtered(filter_text)
        else:
            self._render_lazy_root()

        if self.view_mode.get() == "grid":
            if not os.path.isdir(self._grid_dir) or not self._is_inside_root(self._grid_dir):
                self._grid_dir = self.root_path
            self._populate_grid()
            # A filter can leave the selected item out of view; drop it so
            # the side panel doesn't describe something that isn't shown.
            if filter_text and self._selected and os.path.normcase(self._selected) not in {
                    os.path.normcase(e["path"]) for e in self._grid_entries}:
                self._set_selection(None)

        self._notify("Sandbox refreshed", "info")

    def _render_lazy_root(self):
        if not os.path.isdir(self.root_path):
            self.tree.insert("", "end", text=f"(missing: {self.root_path})")
            return
        self._populate_children("", self.root_path)

    def _populate_children(self, parent_iid: str, dir_path: str):
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())
            )
        except OSError:
            return
        dirs = [e.path for e in entries if e.is_dir(follow_symlinks=False)]
        files = [e.path for e in entries if not e.is_dir(follow_symlinks=False)]
        for path in dirs:
            self._insert_node(parent_iid, path, True)
        # Only the latest version of each document is listed; older ones
        # appear in the Versions panel.
        for latest_path, count in versioning.collapse_to_latest(files):
            self._insert_node(parent_iid, latest_path, False, count)

    def _version_cell(self, path: str, count: int) -> str:
        """Version column text: blank for single-version files."""
        if count <= 1:
            return ""
        return f"v{versioning.split_version(os.path.basename(path))[1]:03d}"

    def _insert_node(self, parent_iid: str, path: str, is_dir: bool, version_count: int = 1) -> str:
        name = os.path.basename(path) or path
        tags = ", ".join(self.tag_store.get_tags(path))
        prefix = "\U0001F4C1 " if is_dir else "\U0001F4C4 "
        iid = self.tree.insert(
            parent_iid, "end", text=prefix + name,
            values=("" if is_dir else self._version_cell(path, version_count), tags),
        )
        self._iid_paths[iid] = path
        if is_dir:
            self.tree.insert(iid, "end", text=_LOADING_PLACEHOLDER)
        return iid

    def _ensure_loaded(self, iid: str):
        """Replace a folder node's loading placeholder with its children."""
        path = self._iid_paths.get(iid)
        children = self.tree.get_children(iid)
        if path and len(children) == 1 and self.tree.item(children[0], "text") == _LOADING_PLACEHOLDER:
            self.tree.delete(children[0])
            self._populate_children(iid, path)

    def _on_tree_open(self, _event):
        self._ensure_loaded(self.tree.focus())

    def _reveal_in_tree(self, path: str):
        """Expand the tree down to ``path`` and select it (used when
        switching from grid back to list)."""
        if not path:
            return
        rel = os.path.relpath(os.path.dirname(path), self.root_path)
        parts = [] if rel == "." else rel.split(os.sep)
        parent_iid, cur = "", self.root_path
        for part in parts:
            cur = os.path.join(cur, part)
            iid = next((i for i in self.tree.get_children(parent_iid)
                        if os.path.normcase(self._iid_paths.get(i, "")) == os.path.normcase(cur)), None)
            if iid is None:
                return
            self._ensure_loaded(iid)
            self.tree.item(iid, open=True)
            parent_iid = iid
        key = versioning.group_key(path) if os.path.isfile(path) else None
        for iid in self.tree.get_children(parent_iid):
            p = self._iid_paths.get(iid)
            if not p:
                continue
            if os.path.normcase(p) == os.path.normcase(path) or (
                    key and os.path.isfile(p) and versioning.group_key(p) == key):
                self.tree.selection_set(iid)
                self.tree.focus(iid)
                self.tree.see(iid)
                return

    def _tree_iid_for(self, path: str) -> Optional[str]:
        target = os.path.normcase(path)
        for iid, p in self._iid_paths.items():
            if os.path.normcase(p) == target:
                return iid
        return None

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _schedule_filter(self, delay=400):
        if self._filter_after_id is not None:
            try:
                self.after_cancel(self._filter_after_id)
            except Exception:
                pass
        self._filter_after_id = self.after(delay, self.refresh)

    def _find_matches(self, filter_text: str) -> List[Tuple[str, int]]:
        """Documents matching the filter as (latest_path, version_count).
        A document matches if any of its versions does by name or tag; it is
        reported once, as its latest version."""
        needle = filter_text.lower()
        matches = []
        for dirpath, _dirs, filenames in os.walk(self.root_path):
            fulls = [os.path.join(dirpath, n) for n in filenames]
            for latest, count in versioning.collapse_to_latest(fulls):
                family = [f for f in fulls
                          if versioning.group_key(f) == versioning.group_key(latest)]
                hit = any(
                    needle in os.path.basename(f).lower()
                    or any(needle in t.lower() for t in self.tag_store.get_tags(f))
                    for f in family
                )
                if hit:
                    matches.append((latest, count))
        return sorted(matches)

    def _render_filtered(self, filter_text: str):
        found = self._find_matches(filter_text)
        if not found:
            self.tree.insert("", "end", text="(no matches)")
            return
        version_counts = dict(found)
        matches = [path for path, _count in found]

        # Build the minimal set of ancestor folders needed to show each match
        # in context, then insert everything fully expanded. Stops at
        # root_path itself (returns "" = the tree's own root) so the top
        # level here matches the flat top level of the normal lazy view.
        node_iids = {}  # normcased dir path -> iid
        root_norm = os.path.normcase(self.root_path)

        def ensure_dir_node(dir_path: str) -> str:
            norm = os.path.normcase(dir_path)
            if norm == root_norm:
                return ""
            if norm in node_iids:
                return node_iids[norm]
            parent_iid = ensure_dir_node(os.path.dirname(dir_path))
            iid = self.tree.insert(parent_iid, "end", text="\U0001F4C1 " + os.path.basename(dir_path), open=True)
            self._iid_paths[iid] = dir_path
            node_iids[norm] = iid
            return iid

        for match in matches:
            parent_iid = ensure_dir_node(os.path.dirname(match))
            tags = ", ".join(self.tag_store.get_tags(match))
            iid = self.tree.insert(
                parent_iid, "end", text="\U0001F4C4 " + os.path.basename(match),
                values=(self._version_cell(match, version_counts.get(match, 1)), tags),
            )
            self._iid_paths[iid] = match

    # ------------------------------------------------------------------
    # Grid view
    # ------------------------------------------------------------------

    def _grid_card_size(self) -> int:
        return int(_GRID_BASE_CARD * self.view_settings.get("grid_scale") / 100.0)

    def _on_grid_canvas_resize(self, event):
        self.grid_canvas.itemconfig(self.grid_window, width=event.width)
        cols = max(1, event.width // (self._grid_card_size() + _GRID_CARD_PAD))
        if cols != self._grid_cols and self.view_mode.get() == "grid":
            if self._grid_resize_job is not None:
                try:
                    self.after_cancel(self._grid_resize_job)
                except Exception:
                    pass
            self._grid_resize_job = self.after(80, self._populate_grid)

    def _on_grid_mousewheel(self, event):
        try:
            bbox = self.grid_canvas.bbox("all")
            if bbox and (bbox[3] - bbox[1]) > self.grid_canvas.winfo_height():
                self.grid_canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")
        except tk.TclError:
            pass
        return "break"

    def _grid_listing(self) -> Tuple[List[dict], int]:
        """Entries for the grid (dict: path, is_dir, count, subtitle) and
        the total before the card cap."""
        filter_text = self._active_filter()
        entries: List[dict] = []
        if filter_text:
            for path, count in self._find_matches(filter_text):
                rel = os.path.relpath(os.path.dirname(path), self.root_path)
                entries.append({"path": path, "is_dir": False, "count": count,
                                "subtitle": "" if rel == "." else rel})
        else:
            try:
                scanned = sorted(
                    os.scandir(self._grid_dir),
                    key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower()))
            except OSError:
                scanned = []
            for e in scanned:
                if e.is_dir(follow_symlinks=False):
                    entries.append({"path": e.path, "is_dir": True, "count": 1, "subtitle": ""})
            files = [e.path for e in scanned if not e.is_dir(follow_symlinks=False)]
            for latest, count in versioning.collapse_to_latest(files):
                entries.append({"path": latest, "is_dir": False, "count": count, "subtitle": ""})
        return entries[:_GRID_MAX_CARDS], len(entries)

    def _render_breadcrumb(self, filter_text: str, total: int):
        for w in self.crumb_bar.winfo_children():
            w.destroy()

        def crumb(text, path=None, bold=False):
            lbl = tk.Label(self.crumb_bar, text=text, bg=_BG,
                           fg="white" if bold or path is None else _ACCENT,
                           font=("Arial", 9, "bold" if bold else "normal"),
                           cursor="hand2" if path else "")
            if path:
                lbl.bind("<Button-1>", lambda e, p=path: self._grid_open_dir(p))
            lbl.pack(side=tk.LEFT)

        if filter_text:
            crumb(f"Search results for “{filter_text}” ({total})", bold=True)
            return

        up = tk.Label(self.crumb_bar, text="↑ Up", bg=_CARD_BG, fg=_MUTED, padx=8, pady=1,
                      font=("Arial", 9), cursor="hand2")
        up.bind("<Button-1>", lambda e: self._grid_go_up())
        if os.path.normcase(os.path.abspath(self._grid_dir)) != os.path.normcase(os.path.abspath(self.root_path)):
            up.pack(side=tk.LEFT, padx=(0, 10))

        rel = os.path.relpath(self._grid_dir, self.root_path)
        parts = [] if rel == "." else rel.split(os.sep)
        crumb("Sandbox", self.root_path if parts else None, bold=not parts)
        cur = self.root_path
        for i, part in enumerate(parts):
            crumb("  ›  ", None)
            cur = os.path.join(cur, part)
            last = i == len(parts) - 1
            crumb(part, None if last else cur, bold=last)

    def _populate_grid(self):
        if self.view_mode.get() != "grid":
            return
        self._grid_resize_job = None
        scroll_pos = self.grid_canvas.yview()[0]

        for widget in self.grid_inner.winfo_children():
            widget.destroy()
        self._grid_cards = []

        filter_text = self._active_filter()
        entries, total = self._grid_listing()
        self._grid_entries = entries
        self._render_breadcrumb(filter_text, total)

        if not entries:
            tk.Label(self.grid_inner, text="No matches" if filter_text else "This folder is empty",
                     bg=_BG, fg=_MUTED, font=("Arial", 11)).pack(pady=20)
            self._grid_cols = 1
            return

        card_size = self._grid_card_size()
        width = self.grid_canvas.winfo_width()
        self._grid_cols = max(1, width // (card_size + _GRID_CARD_PAD))

        for idx, entry in enumerate(entries):
            self._create_card(entry, idx // self._grid_cols, idx % self._grid_cols, card_size)

        if total > len(entries):
            tk.Label(
                self.grid_inner, bg=_BG, fg=_MUTED, font=("Arial", 9),
                text=f"Showing the first {len(entries)} of {total} items — use the filter to narrow down.",
            ).grid(row=(len(entries) - 1) // self._grid_cols + 1, column=0,
                   columnspan=self._grid_cols, pady=10)

        self._highlight_grid()
        self.grid_canvas.update_idletasks()
        self.grid_canvas.yview_moveto(scroll_pos)

    def _create_card(self, entry: dict, row: int, col: int, card_size: int):
        path, is_dir = entry["path"], entry["is_dir"]
        glyph, accent = _kind_style(path, is_dir)
        scale = card_size / _GRID_BASE_CARD
        name_size = max(7, int(8 * scale))
        small_size = max(6, int(7 * scale))

        card = tk.Frame(self.grid_inner, bg=_CARD_BG, width=card_size, height=card_size,
                        cursor="hand2", highlightthickness=2,
                        highlightbackground=_CARD_BG, highlightcolor=_CARD_BG)
        card.grid(row=row, column=col, padx=_GRID_CARD_PAD // 2 - 2, pady=_GRID_CARD_PAD // 2 - 2)
        card.grid_propagate(False)
        card.pack_propagate(False)
        card.columnconfigure(0, weight=1)
        card.rowconfigure(1, weight=1)

        recolor = [card]
        bar = tk.Frame(card, bg=accent, height=max(3, int(4 * scale)))
        bar.grid(row=0, column=0, sticky="new")

        icon = tk.Label(card, text=glyph, bg=_CARD_BG, fg=accent,
                        font=("Segoe UI Emoji", max(14, int(24 * scale))))
        icon.grid(row=1, column=0, sticky="s", pady=(int(6 * scale), 0))
        recolor.append(icon)

        name = os.path.basename(path)
        name_label = tk.Label(card, text=name, bg=_CARD_BG, fg="white", justify=tk.CENTER,
                              font=("Arial", name_size, "bold"), wraplength=card_size - 12)
        name_label.grid(row=2, column=0, sticky="n", padx=4)
        recolor.append(name_label)

        tags = self.tag_store.get_tags(path)
        if entry.get("subtitle"):
            sub = entry["subtitle"]
        elif tags:
            sub = ", ".join(tags)
        elif not is_dir:
            try:
                sub = datetime.fromtimestamp(os.path.getmtime(path)).strftime("%y-%m-%d")
            except OSError:
                sub = ""
        else:
            sub = ""
        max_chars = max(8, int(18 * scale))
        if len(sub) > max_chars:
            sub = sub[:max_chars - 1] + "…"
        sub_label = tk.Label(card, text=sub, bg=_CARD_BG, fg=_MUTED, font=("Arial", small_size))
        sub_label.grid(row=3, column=0, sticky="s", pady=(0, int(6 * scale)))
        recolor.append(sub_label)

        if not is_dir and entry["count"] > 1:
            badge = tk.Label(card, text=self._version_cell(path, entry["count"]), bg="#1f6feb", fg="white",
                             font=("Arial", max(6, int(7 * scale)), "bold"), padx=4, pady=0)
            badge.place(relx=1.0, y=0, anchor="ne")
            badge.bind("<Button-1>", lambda e, p=path: self._grid_click(p))
            badge.bind("<Double-Button-1>", lambda e, p=path: self._grid_activate(p))
            badge.bind("<MouseWheel>", self._on_grid_mousewheel)
            badge.bind("<Button-3>", lambda e, p=path: self._on_grid_right_click(e, p))

        for w in [card, bar, icon, name_label, sub_label]:
            w.bind("<Button-1>", lambda e, p=path: self._grid_click(p))
            w.bind("<Double-Button-1>", lambda e, p=path: self._grid_activate(p))
            w.bind("<MouseWheel>", self._on_grid_mousewheel)
            w.bind("<Button-3>", lambda e, p=path: self._on_grid_right_click(e, p))

        self._grid_cards.append({"path": path, "widget": card, "recolor": recolor})

    def _highlight_grid(self):
        selected = os.path.normcase(self._selected) if self._selected else None
        for item in self._grid_cards:
            on = selected is not None and os.path.normcase(item["path"]) == selected
            bg = _CARD_BG_SELECTED if on else _CARD_BG
            border = _ACCENT if on else _CARD_BG
            item["widget"].config(highlightbackground=border, highlightcolor=border)
            for w in item["recolor"]:
                w.config(bg=bg)

    def _grid_click(self, path: str):
        self._set_selection(path)
        self.grid_canvas.focus_set()

    def _grid_activate(self, path: Optional[str]):
        """Folder -> navigate into it; file -> open it."""
        if not path:
            return
        if os.path.isdir(path):
            # Deferred: re-populating destroys the widget that fired the event.
            self.after_idle(lambda: self._grid_open_dir(path))
        else:
            self._open_file(path)

    def _grid_open_dir(self, path: str):
        self._grid_dir = path
        self._set_selection(None)
        self.grid_canvas.yview_moveto(0)
        self._populate_grid()
        self.grid_canvas.focus_set()

    def _go_up(self):
        """Alt+Up: one folder level up, in whichever view is showing.
        Grid: open the parent folder (selecting the one we left). List:
        select the parent folder of the selected row. Returns "break" so
        the widget's own Up-arrow handling doesn't also move the cursor."""
        if self.view_mode.get() == "grid":
            self._grid_go_up()
        else:
            sel = self.tree.selection()
            parent = self.tree.parent(sel[0]) if sel else ""
            if parent:
                self.tree.selection_set(parent)
                self.tree.focus(parent)
                self.tree.see(parent)
        return "break"

    def _grid_go_up(self):
        if self._active_filter():
            return
        if os.path.normcase(os.path.abspath(self._grid_dir)) == os.path.normcase(os.path.abspath(self.root_path)):
            return
        came_from = self._grid_dir
        self._grid_dir = os.path.dirname(self._grid_dir)
        self._set_selection(came_from)
        self._populate_grid()
        self._grid_see_selected()

    def _grid_move(self, d_col: int, d_row: int):
        if not self._grid_entries:
            return "break"
        paths = [os.path.normcase(e["path"]) for e in self._grid_entries]
        cur = paths.index(os.path.normcase(self._selected)) if self._selected and \
            os.path.normcase(self._selected) in paths else -1
        if cur < 0:
            new = 0
        else:
            new = max(0, min(len(paths) - 1, cur + d_col + d_row * self._grid_cols))
        self._set_selection(self._grid_entries[new]["path"])
        self._grid_see_selected()
        return "break"

    def _grid_see_selected(self):
        selected = os.path.normcase(self._selected) if self._selected else None
        for item in self._grid_cards:
            if os.path.normcase(item["path"]) == selected:
                card = item["widget"]
                self.grid_canvas.update_idletasks()
                top, bottom = card.winfo_y() - 8, card.winfo_y() + card.winfo_height() + 8
                total = max(1, self.grid_inner.winfo_height())
                view_h = self.grid_canvas.winfo_height()
                y0 = self.grid_canvas.canvasy(0)
                if top < y0:
                    self.grid_canvas.yview_moveto(max(0, top) / total)
                elif bottom > y0 + view_h:
                    self.grid_canvas.yview_moveto((bottom - view_h) / total)
                return

    # ------------------------------------------------------------------
    # Selection / open / tagging
    # ------------------------------------------------------------------

    def _selected_path(self) -> Optional[str]:
        return self._selected

    def _on_select(self, _event):
        sel = self.tree.selection()
        path = self._iid_paths.get(sel[0]) if sel else None
        if not path:
            return
        self._set_selection(path)

    def _set_selection(self, path: Optional[str]):
        """Single entry point for selection changes from either view: updates
        the side panel and the grid highlight."""
        self._selected = path
        if path:
            self.selected_path_var.set(path)
            self.tags_var.set(", ".join(self.tag_store.get_tags(path)))
            self.tags_entry.config(state=tk.NORMAL)
            self.save_tags_btn.config(state=tk.NORMAL)
            self.reveal_btn.config(state=tk.NORMAL)
        else:
            self.selected_path_var.set("Nothing selected")
            self.tags_var.set("")
            self.tags_entry.config(state=tk.DISABLED)
            self.save_tags_btn.config(state=tk.DISABLED)
            self.reveal_btn.config(state=tk.DISABLED)
        self._refresh_existing_tags()
        self._refresh_versions()
        self._highlight_grid()

    def _on_double_click(self, _event):
        iid = self.tree.focus()
        path = self._iid_paths.get(iid)
        if not path:
            return "break"
        if os.path.isdir(path):
            is_open = self.tree.item(iid, "open")
            if not is_open:
                self._ensure_loaded(iid)
                self.tree.item(iid, open=True)
            else:
                self.tree.item(iid, open=False)
        else:
            self._open_file(path)
        return "break"

    def _reveal(self, path: Optional[str]):
        """Open the file manager on ``path``'s folder with it highlighted."""
        if not path:
            return
        if reveal_path(path):
            self._notify(f"Showing {os.path.basename(path)} in file manager", "info")
        else:
            self._notify(f"Could not show {path} in file manager", "error")

    def _reveal_selected(self):
        self._reveal(self._selected)
        return "break"

    def _show_context_menu(self, event, path: str, can_version_up: bool = False):
        menu = tk.Menu(self, tearoff=0, bg=_CARD_BG, fg="white", activebackground=_ACCENT,
                       activeforeground="white", relief=tk.FLAT, bd=1)
        is_dir = os.path.isdir(path)
        menu.add_command(label="Open folder" if is_dir else "Open",
                         command=lambda: self._open_file(path))
        menu.add_command(label="Show in Explorer   Ctrl+E", command=lambda: self._reveal(path))
        menu.add_command(label="Copy path", command=lambda: self._copy_path(path))
        if can_version_up:
            menu.add_separator()
            menu.add_command(label="Version up", command=self._version_up)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _copy_path(self, path: str):
        self.clipboard_clear()
        self.clipboard_append(os.path.normpath(path))
        self._notify("Path copied", "info")

    def _on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        path = self._iid_paths.get(iid) if iid else None
        if not path:
            return
        self.tree.selection_set(iid)
        self.tree.focus(iid)
        self._show_context_menu(event, path, can_version_up=os.path.isfile(path))

    def _on_grid_right_click(self, event, path: str):
        self._set_selection(path)
        self.grid_canvas.focus_set()
        self._show_context_menu(event, path, can_version_up=os.path.isfile(path))
        return "break"

    def _on_version_right_click(self, event):
        iid = self.versions_tree.identify_row(event.y)
        path = self._version_paths.get(iid) if iid else None
        if not path:
            return
        self.versions_tree.selection_set(iid)
        self._show_context_menu(event, path)

    def _open_file(self, path: str):
        if open_path(path):
            self._notify(f"Opened: {os.path.basename(path)}", "info")
        else:
            self._notify(f"Failed to open {path}", "error")

    def _save_tags(self):
        path = self._selected_path()
        if not path:
            return
        tags = [t.strip() for t in self.tags_var.get().split(",")]

        # Tags belong to the document, not one version: every version gets
        # the same list so the tag survives versioning up and filters match
        # whichever version is the latest.
        if os.path.isfile(path):
            targets = [v.path for v in versioning.list_versions(path)] or [path]
        else:
            targets = [path]
        self.tag_store.set_tags_bulk(targets, tags)

        iid = self._tree_iid_for(path)
        if iid:
            self.tree.set(iid, "tags", ", ".join(self.tag_store.get_tags(path)))
        if self.view_mode.get() == "grid":
            self._populate_grid()
        self._refresh_existing_tags()
        count = len(targets)
        suffix = f" ({count} versions)" if count > 1 else ""
        self._notify(f"Tags saved for {os.path.basename(path)}{suffix}", "info")

    # ------------------------------------------------------------------
    # Versions
    # ------------------------------------------------------------------

    @staticmethod
    def _fmt_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KB", "MB", "GB"):
            if value < 1024 or unit == "GB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024

    def _refresh_versions(self):
        """Fill the Versions panel for the selected file (newest first)."""
        self.versions_tree.delete(*self.versions_tree.get_children(""))
        self._version_paths.clear()

        path = self._selected_path()
        if not path or not os.path.isfile(path):
            self.version_up_btn.config(text="Version up", state=tk.DISABLED)
            self.versions_hint_var.set(
                "Select a file to see its versions." if not path
                else "Folders aren't versioned — select a file."
            )
            return

        versions = versioning.list_versions(path)
        for v in versions:
            text = f"{v.label}  (latest)" if v.is_latest else v.label
            iid = self.versions_tree.insert(
                "", "end", text=text,
                values=(v.modified.strftime("%Y-%m-%d %H:%M"), self._fmt_size(v.size)),
            )
            self._version_paths[iid] = v.path

        next_number = (versions[0].number + 1) if versions else 2
        self.version_up_btn.config(text=f"Version up → v{next_number:03d}", state=tk.NORMAL)
        self.versions_hint_var.set(
            "Version up copies the latest version to a new file. "
            "Double-click a version to open it."
        )

    def _on_version_double_click(self, _event):
        iid = self.versions_tree.focus()
        path = self._version_paths.get(iid)
        if path:
            self._open_file(path)

    def _version_up(self):
        path = self._selected_path()
        if not path or not os.path.isfile(path):
            return
        latest = versioning.latest_version(path)
        try:
            new_path = versioning.version_up(path)
        except OSError as e:
            self._notify(f"Version up failed: {e}", "error")
            return

        # The new latest inherits the tags so tag filtering keeps finding
        # the document under the row the tree shows.
        if latest:
            tags = self.tag_store.get_tags(latest.path)
            if tags:
                self.tag_store.set_tags(new_path, tags)

        # Repoint the document's tree row at the new latest instead of
        # rebuilding, so expanded folders and scroll position stay put.
        iid = self._tree_iid_for(path)
        if iid:
            self._iid_paths[iid] = new_path
            count = len(versioning.list_versions(new_path))
            self.tree.item(iid, text="\U0001F4C4 " + os.path.basename(new_path))
            self.tree.set(iid, "version", self._version_cell(new_path, count))
            self.tree.set(iid, "tags", ", ".join(self.tag_store.get_tags(new_path)))

        self._selected = new_path
        self.selected_path_var.set(new_path)
        self._refresh_versions()
        if self.view_mode.get() == "grid":
            self._populate_grid()
        self._notify(f"Created {os.path.basename(new_path)}", "info")

    # ------------------------------------------------------------------
    # Existing-tags chips
    # ------------------------------------------------------------------

    def _refresh_existing_tags(self):
        for widget in self.existing_tags_frame.winfo_children():
            widget.destroy()

        # Counted per document: all versions of a file are one item.
        counts = self.tag_store.all_tags(by_document=True)
        for tag in sorted(counts, key=lambda t: (-counts[t], t.lower()))[:20]:
            btn = tk.Button(
                self.existing_tags_frame, text=f"{tag} ({counts[tag]})",
                command=lambda t=tag: self._append_tag(t),
                bg=COLORS["bg_secondary"], fg=COLORS["text_secondary"],
                activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2",
                font=font.Font(family="Segoe UI", size=8)
            )
            btn.pack(side=tk.LEFT, padx=(0, 4), pady=2)

    def _append_tag(self, tag: str):
        current = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        if tag not in current:
            current.append(tag)
        self.tags_var.set(", ".join(current))

    def _open_in_explorer(self):
        if not open_path(self.root_path):
            self._notify("Failed to open file browser", "error")
