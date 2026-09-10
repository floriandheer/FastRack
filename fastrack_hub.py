#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Pipeline Management System - Professional UI
Author: Florian Dheer
Version: 0.5.0
Description: Main launcher for various pipeline scripts with professional UI
Location: P:\\_Scripts\\fastrack_hub.py
"""

import os
import sys
import tkinter as tk
from tkinter import ttk, font
import datetime
import threading
import ctypes

# Windows taskbar identity — must be set before any Tk window is created so
# pinned shortcut and running window share the same taskbar slot.
APP_USER_MODEL_ID = "floriandheer.fastrack"
if sys.platform == "win32":
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass

ICON_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "assets",
    "Favicon_FlorianDheer.ico",
)

# ====================================
# CONSTANTS AND CONFIGURATION
# ====================================

# Base script directory (relative to this file)
SCRIPT_FILE_DIR = os.path.dirname(os.path.abspath(__file__))
SCRIPTS_DIR = os.path.join(SCRIPT_FILE_DIR, "modules")

# ====================================
# IMPORTS FROM MODULES
# ====================================

sys.path.insert(0, SCRIPTS_DIR)
from shared_logging import get_logger, setup_logging, LOG_DIR
from rack_settings import get_rack_settings, join_native_path
from shared_open_path import open_path
from shared_scrollable_frame import ScrollableFrame

from ui_theme import COLORS, CATEGORY_COLORS, make_flat_button
from ui_pipeline_categories import (
    APP_NAME, APP_VERSION, LOGO_PATH,
    CREATIVE_CATEGORIES, BUSINESS_CATEGORIES, PIPELINE_CATEGORIES
)
from ui_config_manager import ConfigManager
from ui_settings_dialog import SettingsDialog
from ui_script_runner import ScriptRunner
from ui_keyboard_navigator import KeyboardNavigatorMixin
from ui_session_state import SessionState, CHANGE_CATEGORIES, CHANGE_SCOPES

# Get logger reference (configured in main())
logger = get_logger("pipeline")

# Import Project Tracker for embedded use (from top-level file)
from fastrack_project_explorer import ProjectTrackerApp
# Invoice Manager is embedded in the right panel when the Business
# category is selected (in place of the project tracker).
from invoice_manager.app import InvoiceManager
# Sandbox Browser is embedded in the right panel when the Sandbox
# category is selected (in place of the project tracker).
from ui_sandbox_browser import SandboxBrowserPanel


def event_has_shift(event) -> bool:
    """True if a Tk event has the Shift modifier held."""
    return bool(getattr(event, "state", 0) & 0x0001)

# ====================================
# CUSTOM WIDGETS
# ====================================


# ====================================
# PROFESSIONAL GUI IMPLEMENTATION
# ====================================

class ProfessionalPipelineGUI(KeyboardNavigatorMixin):
    """Professional GUI for FastRack."""

    def __init__(self, root):
        """Initialize the FastRack GUI."""
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")

        if os.path.exists(ICON_PATH):
            try:
                self.root.iconbitmap(default=ICON_PATH)
            except tk.TclError:
                pass

        # Open maximized or fullscreen depending on setting.
        try:
            self.root.state('zoomed')
        except tk.TclError:
            # Some window managers (mainly on Linux) don't support the
            # 'zoomed' state; size the window to the screen manually.
            self.root.geometry(
                f"{self.root.winfo_screenwidth()}x{self.root.winfo_screenheight()}+0+0"
            )
        self.root.update()

        # Minimum size — clamped to the maximized window's own size so it
        # can never force the window taller/wider than what the OS just
        # gave us. A hardcoded 1200x800 minsize used to do exactly that on
        # smaller MacBook displays, where the space macOS leaves under the
        # menu bar and above the Dock is shorter than 800px: the window
        # got forced past the bottom of the screen, hiding the tools panel
        # and status log below the visible desktop.
        zoomed_w, zoomed_h = self.root.winfo_width(), self.root.winfo_height()
        if zoomed_w <= 1 or zoomed_h <= 1:
            zoomed_w = self.root.winfo_screenwidth()
            zoomed_h = self.root.winfo_screenheight()
        self.root.minsize(min(1200, zoomed_w), min(800, zoomed_h))

        # Configure root window background
        self.root.configure(bg=COLORS["bg_primary"])

        # Load configuration
        self.config_manager = ConfigManager()

        # Load path configuration
        self.settings = get_rack_settings()

        # Apply fullscreen if configured
        if self.settings.get_start_fullscreen():
            self.root.attributes('-fullscreen', True)

        # Shared session state (single source of truth for category selection
        # and scope filter, subscribed to by both the hub and the embedded
        # project tracker). Seeded from persisted config before the UI is
        # built; individual UI builders push their initial state via
        # set_* calls or update their widgets from session properties.
        saved_scopes = self.config_manager.config.get("filter_scopes")
        if saved_scopes is None:
            # Backward compat with older tracker-owned setting key.
            saved_scopes = ["personal", "client"]
        self.session = SessionState(
            initial_categories=self.config_manager.config.get("last_selected_categories") or [],
            initial_scopes=saved_scopes,
        )
        self.session.add_listener(self._on_session_change)

        # Keyboard navigation state
        self.focused_panel = "categories"  # "categories", "operations", "tools", "tracker"
        self.last_left_panel = "categories"  # Remember last left panel for A key from tracker
        self.panel_before_creation = "categories"  # Remember panel before project creation
        self.category_focus_index = 0      # 0-6: 2x3 grid + Sandbox banner row
        self.operations_focus_index = 0    # 0-1 for BUSINESS, GLOBAL
        self.tools_focus_index = 0         # Index in current tools+actions list
        self.tool_buttons = []             # References to tool buttons for navigation

        # Layout constants for keyboard navigation
        self.CATEGORY_ORDER = ["VISUAL", "REALTIME", "AUDIO", "PHYSICAL", "PHOTO", "WEB", "SANDBOX"]
        self.OPERATIONS_ORDER = ["BUSINESS", "GLOBAL"]
        self.SCOPE_ORDER = ["personal", "client"]
        self.STATUS_ORDER = ["active", "archived", "all"]

        # Panel order for WASD navigation (left panel only)
        self.LEFT_PANEL_ORDER = ["categories", "operations", "tools"]

        # Widget references for focus highlighting (set during layout creation)
        self.cat_grid = None
        self.ops_grid = None
        self.action_buttons_frame = None

        # Setup custom styles
        self.setup_styles()

        # Create main layout
        self.create_layout()

    def setup_styles(self):
        """Setup custom ttk styles for professional look."""
        style = ttk.Style()

        # Force a specific theme to avoid system overrides
        try:
            style.theme_use('clam')  # or 'alt', 'default', 'classic'
        except:
            pass  # If theme doesn't exist, continue with default


        # Configure notebook style with better contrast
        style.configure("Main.TNotebook",
                       background=COLORS["bg_primary"],
                       borderwidth=0,
                       tabmargins=[2, 5, 2, 0])

        # Base tab styling (for unselected tabs - smaller)
        style.configure("Main.TNotebook.Tab",
                       background=COLORS["bg_secondary"],
                       foreground=COLORS["text_secondary"],
                       padding=[15, 8],  # Smaller padding for unselected tabs
                       borderwidth=0,
                       focuscolor="none",
                       font=('Segoe UI', 10, 'normal'))

        # Dynamic styling with different padding for selected vs unselected
        style.map("Main.TNotebook.Tab",
                 background=[('selected', COLORS["tab_active_bg"]),
                            ('!selected', COLORS["bg_secondary"]),
                            ('active', COLORS["bg_hover"])],
                 foreground=[('selected', COLORS["tab_active_fg"]),
                            ('!selected', COLORS["text_secondary"])],
                 padding=[('selected', [22, 12]),  # Bigger padding when selected
                         ('!selected', [15, 8])],  # Smaller padding when not selected
                 expand=[("selected", [1, 1, 1, 0])])

        # Fix the selected tab text visibility with better styling
        style.map("Main.TNotebook.Tab",
                 background=[('selected', COLORS["tab_active_bg"]),
                            ('!selected', COLORS["bg_secondary"]),
                            ('active', COLORS["bg_hover"])],  # Added hover state
                 foreground=[('selected', COLORS["tab_active_fg"]),
                            ('!selected', COLORS["text_secondary"])],
                 expand=[("selected", [1, 1, 1, 0])])

        # Configure scrollbar style
        style.configure("Vertical.TScrollbar",
                       background=COLORS["bg_secondary"],
                       bordercolor=COLORS["bg_secondary"],
                       arrowcolor=COLORS["text_secondary"],
                       troughcolor=COLORS["bg_primary"])

        style.map("Vertical.TScrollbar",
                 background=[("active", COLORS["bg_hover"])])

    def create_layout(self):
        """Create the main layout."""
        # Main container
        self.main_container = tk.Frame(self.root, bg=COLORS["bg_primary"])
        self.main_container.pack(fill=tk.BOTH, expand=True)

        # Create header
        self.create_header()

        # Status bar before main content: same pack-order rule as
        # ScrollableFrame above — packing the expand=True content area
        # first let it claim the whole remaining cavity, squeezing the
        # side=BOTTOM status bar off the window entirely.
        self.create_status_bar()

        # Create main notebook
        self.create_main_notebook()

    def load_logo(self, path, size=(80, 50)):
        """Load an image file and resize it for the logo."""
        try:
            from PIL import Image, ImageTk
            image = Image.open(path)

            # Calculate dimensions to maintain aspect ratio
            orig_width, orig_height = image.size
            aspect_ratio = orig_width / orig_height

            if aspect_ratio > 1:  # Width is greater than height
                new_width = size[0]
                new_height = int(size[0] / aspect_ratio)
            else:  # Height is greater than or equal to width
                new_height = size[1]
                new_width = int(size[1] * aspect_ratio)

            # Make sure we don't exceed our target box
            new_width = min(new_width, size[0])
            new_height = min(new_height, size[1])

            # Resize the image while maintaining aspect ratio
            image = image.resize((new_width, new_height), Image.LANCZOS)
            return ImageTk.PhotoImage(image)

        except ImportError:
            # print() alone is invisible when launched from a double-clicked
            # .app (no attached terminal), so this also goes to the log file
            # the Logs button opens.
            logger.warning(
                "PIL/Pillow not installed for interpreter %s — logo image "
                "disabled, falling back to text. Install with: pip install pillow",
                sys.executable,
            )
            return None
        except Exception as e:
            logger.warning("Error loading logo image from %s: %s", path, e)
            return None

    def create_header(self):
        """Create professional header with logo."""
        header_frame = tk.Frame(self.main_container, bg=COLORS["bg_secondary"], height=90)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        # Inner container for content alignment
        inner_header = tk.Frame(header_frame, bg=COLORS["bg_secondary"])
        inner_header.pack(expand=True, fill=tk.BOTH)

        # Configure grid for proper layout
        inner_header.grid_columnconfigure(1, weight=1)
        inner_header.grid_rowconfigure(0, weight=1)

        # Logo section (left side)
        logo_frame = tk.Frame(inner_header, bg=COLORS["bg_secondary"], width=100)
        logo_frame.grid(row=0, column=0, padx=20, sticky="ns")
        logo_frame.grid_propagate(False)

        # Try to load the logo
        try:
            self.logo_image = self.load_logo(LOGO_PATH, size=(80, 50))
            if self.logo_image:
                logo_label = tk.Label(logo_frame,
                                    image=self.logo_image,
                                    bg=COLORS["bg_secondary"])
                logo_label.pack(pady=20)
            else:
                # Fallback to text logo
                logo_label = tk.Label(logo_frame,
                                    text="FD",
                                    font=font.Font(family="Segoe UI", size=20, weight="bold"),
                                    fg=COLORS["accent"],
                                    bg=COLORS["bg_secondary"])
                logo_label.pack(pady=20)
        except Exception as e:
            # Fallback to text logo
            logo_label = tk.Label(logo_frame,
                                text="FD",
                                font=font.Font(family="Segoe UI", size=20, weight="bold"),
                                fg=COLORS["accent"],
                                bg=COLORS["bg_secondary"])
            logo_label.pack(pady=20)

        # Title section (center-left)
        title_container = tk.Frame(inner_header, bg=COLORS["bg_secondary"])
        title_container.grid(row=0, column=1, sticky="w")

        # Main title
        title_font = font.Font(family="Segoe UI", size=26, weight="bold")
        title_label = tk.Label(title_container,
                              text="FASTRACK",
                              font=title_font,
                              fg=COLORS["text_primary"],
                              bg=COLORS["bg_secondary"])
        title_label.pack(anchor="w")

        # Header buttons (right side of header)
        buttons_frame = tk.Frame(inner_header, bg=COLORS["bg_secondary"])
        buttons_frame.grid(row=0, column=2, sticky="e", padx=28)

        # Button style - sized generously (padx/pady below) since the 1px
        # default in make_flat_button reads as a cramped, low-effort chip
        # when used for a whole row of header-level actions.
        btn_font = font.Font(family="Segoe UI", size=11)
        btn_padding = dict(padx=16, pady=9)

        # Refresh button
        refresh_btn = make_flat_button(
            buttons_frame, "Refresh", self.refresh_projects,
            bg=COLORS["bg_hover"], fg=COLORS["text_primary"],
            hover_bg=COLORS["border"], font=btn_font, **btn_padding,
        )
        refresh_btn.pack(side=tk.LEFT, padx=(0, 8), pady=15)
        self._add_header_hint(refresh_btn, "Refresh Projects (F5)")

        # Open Logs button
        logs_btn = make_flat_button(
            buttons_frame, "Logs", self.open_logs_folder,
            bg=COLORS["bg_hover"], fg=COLORS["text_primary"],
            hover_bg=COLORS["border"], font=btn_font, **btn_padding,
        )
        logs_btn.pack(side=tk.LEFT, padx=8, pady=15)
        self._add_header_hint(logs_btn, "Open Logs Folder (Ctrl+L)")

        # Settings button
        settings_btn = make_flat_button(
            buttons_frame, "Settings", self.open_settings,
            bg=COLORS["bg_hover"], fg=COLORS["text_primary"],
            hover_bg=COLORS["border"], font=btn_font, **btn_padding,
        )
        settings_btn.pack(side=tk.LEFT, padx=8, pady=15)
        self._add_header_hint(settings_btn, "Settings (Ctrl+,)")

        # Help button
        help_btn = make_flat_button(
            buttons_frame, "Help", self.open_help,
            bg=COLORS["bg_hover"], fg=COLORS["text_primary"],
            hover_bg=COLORS["border"], font=btn_font, **btn_padding,
        )
        help_btn.pack(side=tk.LEFT, padx=8, pady=15)
        self._add_header_hint(help_btn, "Keyboard Shortcuts (F1)")

        # Exit button (red) - set apart with a wider gap since it's the one
        # destructive action in the row and shouldn't sit shoulder-to-shoulder
        # with the routine ones next to it.
        exit_btn = make_flat_button(
            buttons_frame, "Exit", self.root.destroy,
            bg="#c0392b", fg="white", hover_bg="#e74c3c", hover_fg="white",
            font=btn_font, **btn_padding,
        )
        exit_btn.pack(side=tk.LEFT, padx=(20, 0), pady=15)

    def create_main_notebook(self):
        """Create the main content area (single unified view, no tabs needed)."""
        # Main container with padding
        main_content = tk.Frame(self.main_container, bg=COLORS["bg_primary"])
        main_content.pack(fill=tk.BOTH, expand=True, padx=30, pady=(15, 10))

        # Setup Project Manager with integrated project tracker, tools, and operations
        self.setup_project_manager(main_content)

    def _compute_sidebar_width(self):
        """Widest possible tool row across every category and subcategory, so
        a label is never clipped no matter which category is selected.
        Measured by building real (never-shown) probe rows via
        _create_tool_button itself, so this can't silently drift out of sync
        with that method's actual layout. Clamped to a sane range so one
        long name can't take over the window."""
        name_font = font.Font(family="Segoe UI", size=10)
        icon_font = font.Font(family="Segoe UI Emoji", size=14)

        probe_parent = tk.Frame(self.root)
        saved_tool_buttons, self.tool_buttons = self.tool_buttons, []

        def probe_chrome(icon_text, name_text, direct_run, mode_switch=False):
            for child in probe_parent.winfo_children():
                child.destroy()
            self._create_tool_button(probe_parent, "GLOBAL", "__probe__", None,
                                      {"name": name_text, "icon": icon_text, "direct_run": direct_run,
                                       "mode_switch": mode_switch})
            probe_parent.update_idletasks()
            row_width = probe_parent.winfo_children()[0].winfo_reqwidth()
            return row_width - name_font.measure(name_text) - icon_font.measure(icon_text)

        chrome_with_gear = probe_chrome("X", "M", True)
        chrome_plain = probe_chrome("X", "M", False)
        chrome_with_gear_and_switch = probe_chrome("X", "M", True, mode_switch=True)

        widest = MIN_SIDEBAR_WIDTH = 280
        for cat in PIPELINE_CATEGORIES.values():
            scripts = list(cat.get("scripts", {}).values())
            for sub in cat.get("subcategories", {}).values():
                scripts.extend(sub.get("scripts", {}).values())
            for script in scripts:
                if script.get("mode_switch"):
                    chrome = chrome_with_gear_and_switch
                elif script.get("direct_run"):
                    chrome = chrome_with_gear
                else:
                    chrome = chrome_plain
                width = chrome + icon_font.measure(script.get("icon", "")) + name_font.measure(script.get("name", ""))
                widest = max(widest, width)

        probe_parent.destroy()
        self.tool_buttons = saved_tool_buttons

        # A row's own reqwidth (above) only covers the row itself. Between
        # left_panel_container's edge and tools_container there's also: the
        # ScrollableFrame's scrollbar (width=16), category_panel_outer's
        # padx=15*2, category_panel's padx=1*2, and tools_section's
        # padx=10*2 - none of which the probe row sees. Plus a small margin
        # for font-metric rounding across platforms.
        outer_chrome = 16 + 30 + 2 + 20
        return max(MIN_SIDEBAR_WIDTH, min(440, widest + outer_chrome + 10))

    def setup_project_manager(self, parent_frame):
        """Setup the unified Project Manager with categories, operations, and tools."""
        # Main container with two columns
        main_container = tk.Frame(parent_frame, bg=COLORS["bg_primary"])
        main_container.pack(fill=tk.BOTH, expand=True)

        # Left panel container - sized to fit the widest tool label across
        # every category (capped) so names are never clipped, rather than a
        # fixed width.
        left_panel_container = tk.Frame(main_container, bg=COLORS["bg_card"], width=self._compute_sidebar_width())
        left_panel_container.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 15), pady=0)
        left_panel_container.pack_propagate(False)

        # Scrollable left panel. The scrollbar stays visible (rather than
        # relying on mouse-wheel/trackpad scrolling alone) so the Tools
        # section is always reachable even when the sidebar's content is
        # taller than the window — mouse-wheel scroll still works too.
        self.left_scroll = ScrollableFrame(left_panel_container, bg=COLORS["bg_card"], show_scrollbar=False)
        self.left_scroll.pack(fill=tk.BOTH, expand=True)
        left_panel = self.left_scroll.get_frame()

        # ═══════════════════════════════════════════════════════════
        # CATEGORIES SECTION (includes scope toggles)
        # ═══════════════════════════════════════════════════════════
        self.categories_section = tk.Frame(left_panel, bg=COLORS["bg_card"])
        self.categories_section.pack(fill=tk.X, padx=15, pady=(15, 10))

        # Categories header (clickable to show all)
        cat_header = tk.Label(
            self.categories_section,
            text="CATEGORIES",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            cursor="hand2"
        )
        cat_header.pack(anchor="w", pady=(0, 8))
        cat_header.bind("<Button-1>", lambda e: self._clear_category_selection())
        cat_header.bind("<Enter>", lambda e: cat_header.configure(fg=COLORS["accent"]))
        cat_header.bind("<Leave>", lambda e: cat_header.configure(fg=COLORS["text_secondary"]))

        # Scope toggles (Personal / Work) now live in the project tracker's
        # controls row, since they filter projects, not category tools. The
        # hub keeps a cached view of the current scope for code that still
        # reads it (folder-path resolution, keyboard shortcuts); writes go
        # through self.session and reach the tracker via its listener.
        self.scope_buttons = {}
        self.current_scopes = set(self.session.scopes)

        # Category buttons grid (2 columns, 3 rows)
        self.cat_grid = tk.Frame(self.categories_section, bg=COLORS["bg_card"])
        self.cat_grid.pack(fill=tk.X)

        # Store category button references (includes both categories and operations)
        self.category_buttons = {}
        # Cached view of categories, kept in sync with self.session via the
        # _on_session_change listener. The session is the source of truth;
        # writes go through it (toggle_category / set_categories).
        self.selected_categories = list(self.session.categories)

        # Category order and data
        category_order = ["VISUAL", "REALTIME", "AUDIO", "PHYSICAL", "PHOTO", "WEB"]

        for idx, category_key in enumerate(category_order):
            if category_key not in CREATIVE_CATEGORIES:
                continue

            category_data = CREATIVE_CATEGORIES[category_key]
            row = idx // 2
            col = idx % 2

            btn = self._create_category_button(self.cat_grid, category_key, category_data)
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        # Sandbox — wide banner button, bottom row of the categories grid
        # (spans both columns). Lives here rather than under Operations
        # since it behaves like a category for selection/WASD purposes:
        # it's part of CATEGORY_ORDER and toggles via self.session.toggle_category
        # like any other category button.
        if "SANDBOX" in BUSINESS_CATEGORIES:
            sandbox_data = BUSINESS_CATEGORIES["SANDBOX"]
            sandbox_btn = self._create_sandbox_button(self.cat_grid, "SANDBOX", sandbox_data)
            sandbox_row = (len(category_order) + 1) // 2
            sandbox_btn.grid(row=sandbox_row, column=0, columnspan=2, padx=4, pady=4, sticky="nsew")

        # Configure grid columns to be equal
        self.cat_grid.columnconfigure(0, weight=1)
        self.cat_grid.columnconfigure(1, weight=1)

        # Separator between Categories and Operations
        separator = tk.Frame(left_panel, bg=COLORS["border"], height=1)
        separator.pack(fill=tk.X, padx=15, pady=(10, 10))

        # ═══════════════════════════════════════════════════════════
        # OPERATIONS SECTION
        # ═══════════════════════════════════════════════════════════
        self.operations_section = tk.Frame(left_panel, bg=COLORS["bg_card"])
        self.operations_section.pack(fill=tk.X, padx=15, pady=(0, 10))

        # Operations header
        ops_header = tk.Label(
            self.operations_section,
            text="OPERATIONS",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        )
        ops_header.pack(anchor="w", pady=(0, 10))

        # Operations buttons grid (2 columns)
        self.ops_grid = tk.Frame(self.operations_section, bg=COLORS["bg_card"])
        self.ops_grid.pack(fill=tk.X)

        # Operations order and data
        operations_order = ["BUSINESS", "GLOBAL"]

        for idx, ops_key in enumerate(operations_order):
            if ops_key not in BUSINESS_CATEGORIES:
                continue

            ops_data = BUSINESS_CATEGORIES[ops_key]
            row = idx // 2
            col = idx % 2

            btn = self._create_category_button(self.ops_grid, ops_key, ops_data)
            btn.grid(row=row, column=col, padx=4, pady=4, sticky="nsew")

        # Configure grid columns to be equal
        self.ops_grid.columnconfigure(0, weight=1)
        self.ops_grid.columnconfigure(1, weight=1)

        # ═══════════════════════════════════════════════════════════
        # SELECTED CATEGORY PANEL (below operations, as separate panel)
        # ═══════════════════════════════════════════════════════════
        # Outer frame with border effect
        self.category_panel_outer = tk.Frame(left_panel, bg=COLORS["border"])

        # Inner panel with padding
        self.category_panel = tk.Frame(self.category_panel_outer, bg=COLORS["bg_secondary"])
        self.category_panel.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        # Directory & Notes buttons container (at bottom, pack first with side=BOTTOM)
        self.notes_button_container = tk.Frame(self.category_panel, bg=COLORS["bg_secondary"])

        # Create persistent notes button
        self._create_persistent_notes_button()

        # Tools section header
        self.tools_header = tk.Label(
            self.category_panel,
            text="TOOLS",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_secondary"]
        )
        self.tools_header.pack(anchor="w", padx=10, pady=(10, 5))

        # Scrollable tools container (takes remaining space)
        self.tools_section = tk.Frame(self.category_panel, bg=COLORS["bg_secondary"])
        self.tools_section.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 5))

        # Tools container (no separate scroll - uses left panel scroll)
        self.tools_container = tk.Frame(self.tools_section, bg=COLORS["bg_secondary"])
        self.tools_container.pack(fill=tk.BOTH, expand=True)

        # ═══════════════════════════════════════════════════════════
        # RIGHT PANEL: Project Tracker (creative categories) OR
        #              Invoice Manager (Business category)
        # ═══════════════════════════════════════════════════════════
        # Both share the same slot — only one is packed at a time;
        # `_apply_categories` swaps based on the active selection.
        self._right_panel_parent = main_container

        self.tracker_panel = tk.Frame(main_container, bg=COLORS["bg_primary"])
        self.tracker_panel.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        # Embed the Project Tracker with status and hint callbacks. Pass
        # the shared session so scope state stays in lockstep.
        self.project_tracker = ProjectTrackerApp(
            self.tracker_panel,
            embedded=True,
            status_callback=self.update_status,
            hint_callback=self._show_hint,
            creation_start_callback=self._on_creation_start,
            creation_done_callback=self._on_project_creation_done,
            creation_cancel_callback=self._return_to_last_panel,
            session=self.session,
        )

        # Invoice Manager panel — created here but built lazily on
        # first Business selection so launch stays fast for users who
        # never visit Business.
        self.invoice_manager_panel = tk.Frame(
            main_container, bg=COLORS["bg_primary"]
        )
        self.invoice_manager = None  # populated by _ensure_invoice_manager
        # Sticky: once construction fails (bad config etc.) we show a
        # placeholder and stop retrying on every tab switch.
        self._invoice_manager_failed = False
        # When the user pops the Invoice Manager out via its topbar
        # button, we destroy the embedded instance and reopen it in
        # this Toplevel; re-clicking Business raises it instead of
        # creating a duplicate embedded copy.
        self._detached_invoice_window = None
        self._detached_invoice_manager = None

        # Sandbox Browser panel — created here but built lazily on first
        # Sandbox selection, same reasoning as the Invoice Manager above.
        self.sandbox_browser_panel = tk.Frame(
            main_container, bg=COLORS["bg_primary"]
        )
        self.sandbox_browser = None  # populated by _ensure_sandbox_browser

        # Restore state from last session (run once at startup)
        self._restore_session_state()

    # ----- right-panel swap helpers ---------------------------------

    def _ensure_invoice_manager(self):
        """Lazy-create the embedded InvoiceManager on first Business select.

        If creation fails (typically a missing/invalid global_invoice
        config.json), show a placeholder in the right panel instead of
        propagating — otherwise startup would crash because session
        restore opens the Business tab automatically.
        """
        if self.invoice_manager is not None or self._invoice_manager_failed:
            return
        try:
            self.invoice_manager = InvoiceManager(
                self.invoice_manager_panel, embedded=True,
                on_detach=self._detach_invoice_manager,
            )
        except Exception as exc:
            logger.exception("Failed to start embedded Invoice Manager")
            self._invoice_manager_failed = True
            self._show_invoice_manager_error(exc)

    def _show_invoice_manager_error(self, exc: Exception):
        """Render a placeholder inside the invoice panel when startup fails."""
        for w in self.invoice_manager_panel.winfo_children():
            w.destroy()
        wrap = tk.Frame(self.invoice_manager_panel, bg=COLORS["bg_primary"])
        wrap.pack(fill=tk.BOTH, expand=True, padx=24, pady=24)
        tk.Label(
            wrap, text="Invoice Manager unavailable",
            bg=COLORS["bg_primary"], fg=COLORS.get("fg_primary", "#fff"),
            font=("Segoe UI", 14, "bold"),
            anchor="w", justify="left",
        ).pack(fill=tk.X, pady=(0, 8))
        tk.Label(
            wrap, text=str(exc),
            bg=COLORS["bg_primary"], fg=COLORS.get("fg_secondary", "#bbb"),
            font=("Consolas", 10),
            wraplength=600, anchor="w", justify="left",
        ).pack(fill=tk.X, pady=(0, 12))
        tk.Label(
            wrap,
            text=("Fix the issue above (typically: copy "
                  "modules/invoice_manager/core/config.json.example into "
                  "%LOCALAPPDATA%\\PipelineManager\\global_invoice\\config.json "
                  "and edit it) then restart the hub."),
            bg=COLORS["bg_primary"], fg=COLORS.get("fg_secondary", "#bbb"),
            font=("Segoe UI", 10),
            wraplength=600, anchor="w", justify="left",
        ).pack(fill=tk.X)

    def _ensure_sandbox_browser(self):
        """Lazy-create the embedded Sandbox Browser on first Sandbox select."""
        if self.sandbox_browser is not None:
            return
        root_path = join_native_path(get_rack_settings().get_work_drive(), "_Sandbox")
        self.sandbox_browser = SandboxBrowserPanel(
            self.sandbox_browser_panel, root_path, status_callback=self.update_status,
        )
        self.sandbox_browser.pack(fill=tk.BOTH, expand=True)

    def _show_sandbox_browser_panel(self):
        """Swap the right-hand slot to the Sandbox Browser."""
        if hasattr(self, 'tracker_panel'):
            self.tracker_panel.pack_forget()
        if hasattr(self, 'invoice_manager_panel'):
            self.invoice_manager_panel.pack_forget()
        self._ensure_sandbox_browser()
        if not self.sandbox_browser_panel.winfo_ismapped():
            self.sandbox_browser_panel.pack(
                side=tk.RIGHT, fill=tk.BOTH, expand=True,
            )
        if self.sandbox_browser is not None:
            self.sandbox_browser.refresh()

    def _show_invoice_manager_panel(self):
        """Swap the right-hand slot to the Invoice Manager.

        If the user has popped the Invoice Manager into its own
        floating window, raise that instead of re-embedding — we never
        want two copies running with separate AppState/monitors.
        """
        if hasattr(self, 'tracker_panel'):
            self.tracker_panel.pack_forget()
        if hasattr(self, 'sandbox_browser_panel'):
            self.sandbox_browser_panel.pack_forget()
        if self._detached_invoice_window is not None \
                and self._detached_invoice_window.winfo_exists():
            self.invoice_manager_panel.pack_forget()
            self._detached_invoice_window.deiconify()
            self._detached_invoice_window.lift()
            self._detached_invoice_window.focus_set()
            return
        self._ensure_invoice_manager()
        if not self.invoice_manager_panel.winfo_ismapped():
            self.invoice_manager_panel.pack(
                side=tk.RIGHT, fill=tk.BOTH, expand=True,
            )

    def _detach_invoice_manager(self):
        """Pop the embedded Invoice Manager out into its own Toplevel.

        Tears down the embedded instance (stopping its WC monitor
        thread) so the floating copy is the sole owner of the
        AppState — otherwise both would poll WooCommerce in parallel.
        """
        # Stop the embedded WC monitor before we drop the reference.
        if self.invoice_manager is not None:
            try:
                monitor = self.invoice_manager.state.wc_monitor()
                if monitor is not None:
                    monitor.stop_monitoring()
            except Exception:
                logger.exception("Failed to stop embedded WC monitor")

        # Destroy embedded widgets and hide the slot.
        for w in self.invoice_manager_panel.winfo_children():
            w.destroy()
        self.invoice_manager_panel.pack_forget()
        self.invoice_manager = None

        # Reopen as a floating Toplevel — standalone path applies its
        # own title/geometry/icon.
        win = tk.Toplevel(self.root)
        self._detached_invoice_window = win
        win.protocol("WM_DELETE_WINDOW", self._on_detached_invoice_closed)
        self._detached_invoice_manager = InvoiceManager(win)
        win.lift()
        win.focus_set()

    def _on_detached_invoice_closed(self):
        """Clean up after the floating Invoice Manager window is closed.

        Stops the WC monitor so its polling thread doesn't outlive
        the window. Next Business click will lazy-create a fresh
        embedded instance via `_ensure_invoice_manager`.
        """
        if self._detached_invoice_manager is not None:
            try:
                monitor = self._detached_invoice_manager.state.wc_monitor()
                if monitor is not None:
                    monitor.stop_monitoring()
            except Exception:
                logger.exception("Failed to stop detached WC monitor")
        win = self._detached_invoice_window
        self._detached_invoice_window = None
        self._detached_invoice_manager = None
        if win is not None:
            try:
                win.destroy()
            except tk.TclError:
                pass

    def _show_tracker_panel(self):
        """Swap the right-hand slot back to the Project Tracker."""
        if hasattr(self, 'invoice_manager_panel'):
            self.invoice_manager_panel.pack_forget()
        if hasattr(self, 'sandbox_browser_panel'):
            self.sandbox_browser_panel.pack_forget()
        if not self.tracker_panel.winfo_ismapped():
            self.tracker_panel.pack(
                side=tk.RIGHT, fill=tk.BOTH, expand=True,
            )

    def _hide_right_panels(self):
        """Hide all right-hand slots (operations-only selection)."""
        if hasattr(self, 'tracker_panel'):
            self.tracker_panel.pack_forget()
        if hasattr(self, 'invoice_manager_panel'):
            self.invoice_manager_panel.pack_forget()
        if hasattr(self, 'sandbox_browser_panel'):
            self.sandbox_browser_panel.pack_forget()

    def _restore_session_state(self):
        """Restore saved state from last session (category, scope, etc.).

        Scopes now live in the shared session (seeded from config at
        construction). If the hub's config has no scope key yet, migrate
        from the tracker's legacy per-tracker settings once.
        """
        if self.config_manager.config.get("filter_scopes") is None and \
                hasattr(self, 'project_tracker') and self.project_tracker is not None:
            legacy = getattr(self.project_tracker, 'filter_scopes', None)
            if legacy:
                self.session.set_scopes(legacy)
                self.config_manager.config["filter_scopes"] = sorted(legacy)
                self.config_manager._save_config()

        self._update_scope_button_styles()

        # Restore last selected categories (multi) from config, falling back
        # to the legacy single-category key.
        saved_list = self.config_manager.config.get("last_selected_categories")
        if saved_list is None:
            legacy = self.config_manager.config.get("last_selected_category")
            saved_list = [legacy] if legacy else []
        valid = [k for k in saved_list if k in PIPELINE_CATEGORIES]
        if valid:
            self.session.set_categories(valid)
            # The session was seeded with these same values at __init__, so
            # set_categories no-ops and listeners never fire. Sync the UI
            # (button styles, tracker filter) explicitly so the saved
            # selection is visibly restored.
            self._apply_categories()
        else:
            # Show all by default (no category selected)
            self._clear_category_selection()

        # Ensure categories panel is focused by default
        self.focused_panel = "categories"
        self.category_focus_index = 0
        self._update_panel_focus()

    def _show_hint(self, text):
        """Show a hint in the status bar header."""
        if hasattr(self, 'header_hint_label'):
            self.header_hint_label.config(text=text)

    def _create_category_button(self, parent, category_key, category_data):
        """Create a square category selection button."""
        color = CATEGORY_COLORS.get(category_key, COLORS["accent"])
        icon = category_data.get("icon", "")
        name = category_data.get("name", category_key)

        # Shortcut mapping for categories
        shortcut_map = {
            "VISUAL": "Shift+V",
            "REALTIME": "Shift+R",
            "AUDIO": "Shift+A",
            "PHYSICAL": "Shift+P",
            "PHOTO": "Shift+H",
            "WEB": "Shift+W",
            "BUSINESS": "Shift+B",
            "GLOBAL": "Shift+G",
        }
        shortcut = shortcut_map.get(category_key, "")

        # Button container (square)
        btn_size = 115
        btn_frame = tk.Frame(
            parent,
            bg=COLORS["bg_secondary"],
            width=btn_size,
            height=btn_size,
            cursor="hand2"
        )
        btn_frame.pack_propagate(False)

        # Content
        content = tk.Frame(btn_frame, bg=COLORS["bg_secondary"])
        content.place(relx=0.5, rely=0.5, anchor="center")

        # Icon
        icon_label = tk.Label(
            content,
            text=icon,
            font=font.Font(family="Segoe UI Emoji", size=24),
            fg=color,
            bg=COLORS["bg_secondary"]
        )
        icon_label.pack()

        # Name
        name_label = tk.Label(
            content,
            text=name,
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_secondary"]
        )
        name_label.pack(pady=(5, 0))

        # Store references
        self.category_buttons[category_key] = {
            "frame": btn_frame,
            "content": content,
            "icon": icon_label,
            "name": name_label,
            "color": color
        }

        # Hover and click effects
        def on_enter(e):
            if category_key not in self.selected_categories:
                btn_frame.configure(bg=COLORS["bg_hover"])
                content.configure(bg=COLORS["bg_hover"])
                icon_label.configure(bg=COLORS["bg_hover"], fg=color)
                name_label.configure(bg=COLORS["bg_hover"])
            # Show shortcut hint
            if shortcut and hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text=f"Shortcut: {shortcut}")

        def on_leave(e):
            if category_key not in self.selected_categories:
                btn_frame.configure(bg=COLORS["bg_secondary"])
                content.configure(bg=COLORS["bg_secondary"])
                icon_label.configure(bg=COLORS["bg_secondary"], fg=color)
                name_label.configure(bg=COLORS["bg_secondary"])
            # Clear shortcut hint
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="")

        def on_click(e):
            # Shift+click: toggle this category in the selection.
            # Plain click: collapse the selection to just this one.
            self.session.toggle_category(category_key, additive=event_has_shift(e))

        for widget in [btn_frame, content, icon_label, name_label]:
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)
            widget.bind("<Button-1>", on_click)

        return btn_frame

    def _create_sandbox_button(self, parent, category_key, category_data):
        """Create the wide, short Sandbox banner button.

        Unlike _create_category_button's square icon-over-name layout (which
        needs ~70px of vertical room), this lays icon and name out side by
        side on one line so it comfortably fits at half that height.
        """
        color = CATEGORY_COLORS.get(category_key, COLORS["accent"])
        icon = category_data.get("icon", "")
        name = category_data.get("name", category_key)

        btn_frame = tk.Frame(parent, bg=COLORS["bg_secondary"], height=58, cursor="hand2")
        btn_frame.pack_propagate(False)

        content = tk.Frame(btn_frame, bg=COLORS["bg_secondary"])
        content.place(relx=0.5, rely=0.5, anchor="center")

        icon_label = tk.Label(
            content,
            text=icon,
            font=font.Font(family="Segoe UI Emoji", size=18),
            fg=color,
            bg=COLORS["bg_secondary"]
        )
        icon_label.pack(side=tk.LEFT)

        name_label = tk.Label(
            content,
            text=name.upper(),
            font=font.Font(family="Segoe UI", size=12, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_secondary"]
        )
        name_label.pack(side=tk.LEFT, padx=(10, 0))

        self.category_buttons[category_key] = {
            "frame": btn_frame,
            "content": content,
            "icon": icon_label,
            "name": name_label,
            "color": color
        }

        def on_enter(e):
            if category_key not in self.selected_categories:
                btn_frame.configure(bg=COLORS["bg_hover"])
                content.configure(bg=COLORS["bg_hover"])
                icon_label.configure(bg=COLORS["bg_hover"], fg=color)
                name_label.configure(bg=COLORS["bg_hover"])

        def on_leave(e):
            if category_key not in self.selected_categories:
                btn_frame.configure(bg=COLORS["bg_secondary"])
                content.configure(bg=COLORS["bg_secondary"])
                icon_label.configure(bg=COLORS["bg_secondary"], fg=color)
                name_label.configure(bg=COLORS["bg_secondary"])

        def on_click(e):
            self.session.toggle_category(category_key, additive=event_has_shift(e))

        for widget in [btn_frame, content, icon_label, name_label]:
            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)
            widget.bind("<Button-1>", on_click)

        return btn_frame

    @property
    def primary_category(self):
        """The last-clicked category in the selection, or None."""
        return self.selected_categories[-1] if self.selected_categories else None

    def _select_category(self, category_key):
        """Select a single category (collapse multi-selection to just this one)."""
        self.session.set_categories([category_key])

    def _on_session_change(self, change):
        """Shared-state listener. Sync cached views and re-render."""
        if change == CHANGE_CATEGORIES:
            self.selected_categories = list(self.session.categories)
            self._apply_categories()
        elif change == CHANGE_SCOPES:
            self.current_scopes = set(self.session.scopes)
            self._apply_scopes()

    def _apply_categories(self):
        """Push the current category selection to the UI (buttons, tools, tracker)."""
        # Update button styles for every category.
        for key, btn_info in self.category_buttons.items():
            color = btn_info["color"]
            if key in self.selected_categories:
                btn_info["frame"].configure(bg=color)
                btn_info["content"].configure(bg=color)
                btn_info["icon"].configure(bg=color, fg="#ffffff")
                btn_info["name"].configure(bg=color, fg="#ffffff")
            else:
                btn_info["frame"].configure(bg=COLORS["bg_secondary"])
                btn_info["content"].configure(bg=COLORS["bg_secondary"])
                btn_info["icon"].configure(bg=COLORS["bg_secondary"], fg=color)
                btn_info["name"].configure(bg=COLORS["bg_secondary"], fg=COLORS["text_primary"])

        # Update tools panel with every selected category.
        self._update_tools_panel(self.selected_categories)

        # Persist selection.
        self.config_manager.config["last_selected_category"] = self.primary_category
        self.config_manager.config["last_selected_categories"] = list(self.selected_categories)
        self.config_manager._save_config()

        # Swap the right panel between Project Tracker (creative
        # categories), Invoice Manager (Business), and the Sandbox
        # Browser (Sandbox). When the selection is operations-only with
        # none of those, everything hides.
        if hasattr(self, 'project_tracker') and self.project_tracker:
            creative_selected = [k for k in self.selected_categories if k in CREATIVE_CATEGORIES]
            business_selected = "BUSINESS" in self.selected_categories
            sandbox_selected = "SANDBOX" in self.selected_categories
            if creative_selected:
                # Creative wins when both creative + business are picked —
                # the tracker is the everyday view; users can still hit
                # the Business button alone to flip to invoices.
                self._show_tracker_panel()
                category_names = [
                    CREATIVE_CATEGORIES.get(k, {}).get("name", k) for k in creative_selected
                ]
                if hasattr(self.project_tracker, 'set_categories'):
                    self.project_tracker.set_categories(category_names)
            elif business_selected:
                self._show_invoice_manager_panel()
            elif sandbox_selected:
                self._show_sandbox_browser_panel()
            else:
                # Operations-only (e.g. Global) or empty — hide all.
                self._hide_right_panels()

    def _clear_category_selection(self):
        """Clear category selection to show all projects.

        Routes state through self.session so the listener updates button
        styles and the tracker. This method still owns the panel show/hide
        (a UX-specific behavior distinct from simply having no categories).
        """
        self.session.clear_categories()

        # Hide the category panel; re-show the project tracker
        # unconditionally (clear = "show me all projects", which is a
        # tracker view, not a Business view).
        if hasattr(self, 'category_panel_outer'):
            self.category_panel_outer.pack_forget()
        self._show_tracker_panel()

    def _set_scope(self, scope: str):
        """Keyboard/shortcut entry point: select a single scope."""
        self.session.set_scopes({scope})

    def _apply_scopes(self):
        """Update scope UI and persist. The tracker subscribes to the same
        session and updates itself; no direct push needed."""
        self._update_scope_button_styles()

        # Persist centrally.
        self.config_manager.config["filter_scopes"] = sorted(self.current_scopes)
        self.config_manager._save_config()

        # Update folder path based on new scope (if a category is selected)
        if hasattr(self, '_folder_category') and self._folder_category:
            self._update_notes_button(self._folder_category)

    def _update_scope_button_styles(self):
        """Update scope button visual states based on current selection."""
        if not hasattr(self, 'scope_buttons'):
            return
        for value, btn in self.scope_buttons.items():
            if value in self.current_scopes:
                # Selected state - highlighted
                btn.configure(bg=COLORS["accent"], fg="white")
            else:
                # Unselected state
                btn.configure(bg=COLORS["bg_secondary"], fg="white")

    def _update_tools_panel(self, category_keys):
        """Update the tools panel to show tools for all selected categories.

        Accepts a list of category keys (ordered; last entry is the primary
        category that drives the notes/folder buttons). For backward compat,
        a single string is also accepted.
        """
        if isinstance(category_keys, str):
            category_keys = [category_keys]

        # Reset tool buttons list for keyboard navigation
        self.tool_buttons = []
        self.tools_focus_index = 0

        # Clear current tools from scrollable container
        for widget in self.tools_container.winfo_children():
            widget.destroy()

        valid_keys = [k for k in category_keys if k in PIPELINE_CATEGORIES]
        if not valid_keys:
            self.category_panel_outer.pack_forget()
            return

        # Show the category panel in the left panel (full height)
        self.category_panel_outer.pack(fill=tk.BOTH, expand=True, padx=15, pady=(15, 15))

        # Collect tools from every selected category. Each tool is tagged with
        # its source category so it keeps the right color accent. Project-
        # context scripts are surfaced in the project detail Actions section
        # instead, so skip them here.
        all_tools = []
        for category_key in valid_keys:
            category_data = PIPELINE_CATEGORIES[category_key]

            scripts = category_data.get("scripts", {})
            for script_key, script_data in scripts.items():
                if script_data.get("context") == "project":
                    continue
                all_tools.append((category_key, script_key, None, script_data))

            subcategories = category_data.get("subcategories", {})
            for subcat_key, subcat_data in subcategories.items():
                subcat_scripts = subcat_data.get("scripts", {})
                for script_key, script_data in subcat_scripts.items():
                    if script_data.get("context") == "project":
                        continue
                    all_tools.append((category_key, script_key, subcat_key, script_data))

        if not all_tools:
            placeholder = tk.Label(
                self.tools_container,
                text="No tools available\nfor this category",
                font=font.Font(family="Segoe UI", size=10),
                fg=COLORS["text_secondary"],
                bg=COLORS["bg_card"],
                justify="center"
            )
            placeholder.pack(expand=True)
        else:
            # Sort by priority within category, then keep categories in selection order.
            cat_order = {k: i for i, k in enumerate(valid_keys)}
            all_tools.sort(key=lambda t: (
                cat_order[t[0]],
                self._get_script_priority(t[1], t[3].get("name", ""))
            ))

            for category_key, script_key, subcat_key, script_data in all_tools:
                self._create_tool_button(
                    self.tools_container,
                    category_key,
                    script_key,
                    subcat_key,
                    script_data
                )

        # Notes/folder buttons target the primary (last-clicked) category only.
        primary = valid_keys[-1]
        if primary == "GLOBAL":
            self.notes_button_container.pack_forget()
        else:
            self.notes_button_container.pack(fill=tk.X, side=tk.BOTTOM, padx=10, pady=(5, 10))
            self._update_notes_button(primary)

    def _get_traktor_playlist_mode(self):
        """Which mode (export/import) direct-run will replay for Sync Traktor
        Playlists - read straight from that script's own saved settings."""
        try:
            import PipelineScript_Audio_TraktorPlaylistSync as traktor_playlist_sync
            mode = traktor_playlist_sync.ConfigManager().settings.last_mode
            return "import" if mode == "import" else "export"
        except Exception as e:
            logger.warning(f"Could not read Traktor Playlist Sync mode: {e}")
            return "export"

    def _set_traktor_playlist_mode(self, mode):
        try:
            import PipelineScript_Audio_TraktorPlaylistSync as traktor_playlist_sync
            traktor_playlist_sync.ConfigManager().update_settings(last_mode=mode)
        except Exception as e:
            logger.warning(f"Could not save Traktor Playlist Sync mode: {e}")

    def _create_mode_switch(self, parent):
        """Two stacked half-height segments acting as a single switch (only
        one selected at a time) - lets you pick which mode Sync Traktor
        Playlists' direct-run will replay (out=export, in=import) without
        opening the full tool. Each option keeps a consistent color
        (out=green, in=red) whether selected or not, so the two stay
        identifiable at a glance."""
        switch_font = font.Font(family="Segoe UI", size=8)
        out_color, in_color = COLORS["success"], COLORS["error"]

        switch_frame = tk.Frame(
            parent, bg=COLORS["bg_card"], cursor="hand2",
            highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"]
        )
        switch_frame.pack(side=tk.LEFT, fill=tk.Y)

        out_label = tk.Label(switch_frame, text="out", font=switch_font)
        out_label.pack(fill=tk.BOTH, expand=True, padx=8, pady=(4, 1))
        in_label = tk.Label(switch_frame, text="in", font=switch_font)
        in_label.pack(fill=tk.BOTH, expand=True, padx=8, pady=(1, 4))

        def refresh(mode):
            if mode == "export":
                out_label.configure(bg=out_color, fg=COLORS["bg_primary"])
                in_label.configure(bg=COLORS["bg_card"], fg=COLORS["text_primary"])
            else:
                out_label.configure(bg=COLORS["bg_card"], fg=COLORS["text_primary"])
                in_label.configure(bg=in_color, fg=COLORS["bg_primary"])

        def select(mode):
            self._set_traktor_playlist_mode(mode)
            refresh(mode)

        refresh(self._get_traktor_playlist_mode())
        out_label.bind("<Button-1>", lambda e: select("export"))
        in_label.bind("<Button-1>", lambda e: select("import"))

    def _create_tool_button(self, parent, category_key, script_key, subcat_key, script_data):
        """Create a professional tool button: an optional settings-gear chip
        (for tools that can run directly from this row) followed by a
        name/arrow chip that fills the rest of the row."""
        color = CATEGORY_COLORS.get(category_key, COLORS["accent"])
        icon = script_data.get("icon", "")
        name = script_data.get("name", script_key)
        has_gear = bool(script_data.get("direct_run"))

        icon_font = font.Font(family="Segoe UI Emoji", size=14)
        name_font = font.Font(family="Segoe UI", size=10)

        # Row frame with left color accent
        btn_frame = tk.Frame(parent, bg=COLORS["bg_secondary"], cursor="hand2")
        btn_frame.pack(fill=tk.X, pady=3)

        accent_bar = tk.Frame(btn_frame, bg=color, width=4)
        accent_bar.pack(side=tk.LEFT, fill=tk.Y)

        content = tk.Frame(btn_frame, bg=COLORS["bg_secondary"])
        content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=10)

        gear_chip = None
        gear_label = None
        if has_gear:
            gear_chip = tk.Frame(
                content, bg=COLORS["bg_card"], cursor="hand2",
                highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"]
            )
            gear_chip.pack(side=tk.LEFT, fill=tk.Y)
            gear_label = tk.Label(gear_chip, text="⚙️", font=icon_font, fg=COLORS["text_secondary"], bg=COLORS["bg_card"])
            gear_label.pack(padx=10, pady=6, fill=tk.Y, expand=True)

            spacer = tk.Frame(content, bg=COLORS["bg_secondary"], width=8)
            spacer.pack(side=tk.LEFT, fill=tk.Y)

        if script_data.get("mode_switch"):
            self._create_mode_switch(content)

            spacer2 = tk.Frame(content, bg=COLORS["bg_secondary"], width=8)
            spacer2.pack(side=tk.LEFT, fill=tk.Y)

        # Name/arrow chip - fills the remaining width of the row
        name_chip = tk.Frame(
            content, bg=COLORS["bg_card"], cursor="hand2",
            highlightthickness=1, highlightbackground=COLORS["border"], highlightcolor=COLORS["border"]
        )
        name_chip.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        icon_label = tk.Label(name_chip, text=icon, font=icon_font, fg=color, bg=COLORS["bg_card"])
        icon_label.pack(side=tk.LEFT, padx=(10, 0), pady=6)

        name_label = tk.Label(name_chip, text=name, font=name_font, fg=COLORS["text_primary"], bg=COLORS["bg_card"], anchor="w")
        name_label.pack(side=tk.LEFT, padx=(8, 0), pady=6, fill=tk.X, expand=True)

        arrow_label = tk.Label(name_chip, text=">", font=name_font, fg=COLORS["text_secondary"], bg=COLORS["bg_card"])
        arrow_label.pack(side=tk.RIGHT, padx=(0, 10), pady=6)

        # Hover effects - each chip highlights independently, since they
        # trigger different actions (gear opens settings, name runs directly)
        def on_gear_enter(e):
            gear_chip.configure(bg=COLORS["bg_hover"])
            gear_label.configure(bg=COLORS["bg_hover"], fg=color)

        def on_gear_leave(e):
            gear_chip.configure(bg=COLORS["bg_card"])
            gear_label.configure(bg=COLORS["bg_card"], fg=COLORS["text_secondary"])

        def on_gear_click(e):
            self.run_script(category_key, script_key, subcat_key)

        if has_gear:
            for widget in [gear_chip, gear_label]:
                widget.bind("<Enter>", on_gear_enter)
                widget.bind("<Leave>", on_gear_leave)
                widget.bind("<Button-1>", on_gear_click)

        def on_name_enter(e):
            name_chip.configure(bg=COLORS["bg_hover"])
            icon_label.configure(bg=COLORS["bg_hover"])
            name_label.configure(bg=COLORS["bg_hover"])
            arrow_label.configure(bg=COLORS["bg_hover"], fg=color)

        def on_name_leave(e):
            name_chip.configure(bg=COLORS["bg_card"])
            icon_label.configure(bg=COLORS["bg_card"])
            name_label.configure(bg=COLORS["bg_card"])
            arrow_label.configure(bg=COLORS["bg_card"], fg=COLORS["text_secondary"])

        def on_name_click(e):
            self.run_script(category_key, script_key, subcat_key,
                             direct_run=script_data.get("direct_run", False))

        for widget in [name_chip, icon_label, name_label, arrow_label]:
            widget.bind("<Enter>", on_name_enter)
            widget.bind("<Leave>", on_name_leave)
            widget.bind("<Button-1>", on_name_click)

        # Store tool button reference for keyboard navigation
        self.tool_buttons.append({
            "name_chip": name_chip,
            "icon_label": icon_label,
            "name_label": name_label,
            "arrow_label": arrow_label,
            "category_key": category_key,
            "script_key": script_key,
            "subcat_key": subcat_key,
            "color": color
        })

    def _create_persistent_notes_button(self):
        """Create persistent Open Directory and Open Notes buttons (called once during setup)."""
        parent = self.notes_button_container

        # Notepad yellow colors (store for hover effects)
        self._notepad_bg = "#FFF9C4"  # Light yellow (notepad color)
        self._notepad_hover = "#E6E0B0"  # Darker yellow for hover (less bright, not more saturated)
        notepad_accent = "#FFD54F"  # Golden accent
        self._notepad_text_color = "#5D4037"  # Brown text for notepad feel

        # Folder button colors (blue theme)
        self._folder_bg = "#E3F2FD"  # Light blue
        self._folder_hover = "#C5D9E8"  # Darker blue for hover (less bright, not more saturated)
        folder_accent = "#2196F3"  # Blue accent
        self._folder_text_color = "#1565C0"  # Dark blue text

        # Store current category for click handler
        self._notes_category = None
        self._folder_path = None
        self._folder_category = None

        # === TOP BUTTON: Open Directory ===
        self._folder_btn_frame = tk.Frame(parent, bg=self._folder_bg, cursor="hand2")
        self._folder_btn_frame.pack(fill=tk.X, pady=(0, 4))

        # Left accent bar (blue)
        folder_accent_bar = tk.Frame(self._folder_btn_frame, bg=folder_accent, width=4)
        folder_accent_bar.pack(side=tk.LEFT, fill=tk.Y)

        # Content area
        self._folder_content = tk.Frame(self._folder_btn_frame, bg=self._folder_bg)
        self._folder_content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=8)

        # Folder icon
        self._folder_icon = tk.Label(
            self._folder_content,
            text="📂",
            font=font.Font(family="Segoe UI Emoji", size=12),
            fg=self._folder_text_color,
            bg=self._folder_bg
        )
        self._folder_icon.pack(side=tk.LEFT)

        # Text label
        self._folder_label = tk.Label(
            self._folder_content,
            text="Open Directory",
            font=font.Font(family="Segoe UI", size=9),
            fg=self._folder_text_color,
            bg=self._folder_bg,
            anchor="w"
        )
        self._folder_label.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        # Folder button hover effects
        def on_folder_enter(e):
            self._folder_btn_frame.configure(bg=self._folder_hover)
            self._folder_content.configure(bg=self._folder_hover)
            self._folder_icon.configure(bg=self._folder_hover)
            self._folder_label.configure(bg=self._folder_hover)
            # Show shortcut hint in status bar
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="Shortcut: G or 0")

        def on_folder_leave(e):
            self._folder_btn_frame.configure(bg=self._folder_bg)
            self._folder_content.configure(bg=self._folder_bg)
            self._folder_icon.configure(bg=self._folder_bg)
            self._folder_label.configure(bg=self._folder_bg)
            # Clear shortcut hint
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="")

        def on_folder_click(e):
            if self._folder_category:
                # Check if project tracker is in archive mode
                is_archive_mode = False
                if hasattr(self, 'project_tracker') and self.project_tracker:
                    if hasattr(self.project_tracker, 'filter_toggles'):
                        statuses = self.project_tracker._get_active_statuses()
                        is_archive_mode = statuses == {"archived"}

                if is_archive_mode:
                    # Open archive directory
                    archive_path = self.settings.get_archive_path(self._folder_category)
                    self.open_folder(archive_path)
                elif self._folder_path:
                    # Open active directory
                    self.open_folder(self._folder_path)

        for widget in [self._folder_btn_frame, self._folder_content, self._folder_icon, self._folder_label]:
            widget.bind("<Enter>", on_folder_enter)
            widget.bind("<Leave>", on_folder_leave)
            widget.bind("<Button-1>", on_folder_click)

        # === BOTTOM BUTTON: Open Notes ===
        self._notes_btn_frame = tk.Frame(parent, bg=self._notepad_bg, cursor="hand2")
        self._notes_btn_frame.pack(fill=tk.X, pady=(0, 5))

        # Left accent bar (golden)
        accent_bar = tk.Frame(self._notes_btn_frame, bg=notepad_accent, width=4)
        accent_bar.pack(side=tk.LEFT, fill=tk.Y)

        # Content area
        self._notes_content = tk.Frame(self._notes_btn_frame, bg=self._notepad_bg)
        self._notes_content.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=8)

        # Notepad icon
        self._notes_icon = tk.Label(
            self._notes_content,
            text="📝",
            font=font.Font(family="Segoe UI Emoji", size=12),
            fg=self._notepad_text_color,
            bg=self._notepad_bg
        )
        self._notes_icon.pack(side=tk.LEFT)

        # Text label (will be updated when category changes)
        self._notes_label = tk.Label(
            self._notes_content,
            text="Open Notes",
            font=font.Font(family="Segoe UI", size=9),
            fg=self._notepad_text_color,
            bg=self._notepad_bg,
            anchor="w"
        )
        self._notes_label.pack(side=tk.LEFT, padx=(6, 0), fill=tk.X, expand=True)

        # Notes button hover effects
        def on_notes_enter(e):
            self._notes_btn_frame.configure(bg=self._notepad_hover)
            self._notes_content.configure(bg=self._notepad_hover)
            self._notes_icon.configure(bg=self._notepad_hover)
            self._notes_label.configure(bg=self._notepad_hover)
            # Show shortcut hint in status bar
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="Shortcut: N or .")

        def on_notes_leave(e):
            self._notes_btn_frame.configure(bg=self._notepad_bg)
            self._notes_content.configure(bg=self._notepad_bg)
            self._notes_icon.configure(bg=self._notepad_bg)
            self._notes_label.configure(bg=self._notepad_bg)
            # Clear shortcut hint
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="")

        def on_notes_click(e):
            if self._notes_category:
                self.open_note(self._notes_category)

        for widget in [self._notes_btn_frame, self._notes_content, self._notes_icon, self._notes_label]:
            widget.bind("<Enter>", on_notes_enter)
            widget.bind("<Leave>", on_notes_leave)
            widget.bind("<Button-1>", on_notes_click)

    def _update_notes_button(self, category_key):
        """Update the notes and folder buttons for the current category."""
        self._notes_category = category_key
        self._notes_label.configure(text=f"{category_key.title()} Notes")

        # Update folder button based on category's folder_path
        category_data = PIPELINE_CATEGORIES.get(category_key, {})
        folder_path = category_data.get('folder_path')

        if folder_path:
            # Categories that support _Personal subfolder
            categories_with_personal = ["visual", "realtime", "web", "photo"]

            # Treat "personal only" (just {"personal"}) as the personal-folder scope.
            personal_only = getattr(self, "current_scopes", set()) == {"personal"}

            # Adjust path based on current scope
            if personal_only and category_key.lower() in categories_with_personal:
                folder_path = os.path.join(folder_path, "_Personal")

            self._folder_path = folder_path
            self._folder_category = category_key
            # Show _Personal in label when in personal scope
            if personal_only and category_key.lower() in categories_with_personal:
                self._folder_label.configure(text=f"{category_key.title()} Personal")
            else:
                self._folder_label.configure(text=f"{category_key.title()} Directory")
            # Re-pack in correct order: folder button first, then notes button
            self._folder_btn_frame.pack(fill=tk.X, pady=(0, 4), before=self._notes_btn_frame)
        else:
            # Hide folder button for categories without folder_path (e.g., Global Tools)
            self._folder_path = None
            self._folder_category = None
            self._folder_btn_frame.pack_forget()

    def setup_grid_layout(self, parent_frame, categories):
        """Setup grid layout for categories."""
        # Create scrollable frame
        scroll_frame = ScrollableFrame(parent_frame, bg=COLORS["bg_primary"])
        scroll_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        # Get the actual frame to add content to
        content_frame = scroll_frame.get_frame()

        # Create grid of category cards
        columns = 3  # Number of columns in grid
        for i, (category_key, category_data) in enumerate(categories.items()):
            row = i // columns
            col = i % columns

            # Create category card
            card = self.create_category_card(content_frame, category_key, category_data)
            card.grid(row=row, column=col, padx=10, pady=10, sticky="nsew")

            # Configure grid weights for proper expansion
            content_frame.grid_columnconfigure(col, weight=1)

    def open_folder(self, folder_path):
        """Open a folder in the OS file browser."""
        try:
            # Special handling for Business category - open current quarter folder
            library_path = join_native_path(get_rack_settings().get_work_drive(), "_LIBRARY")
            if folder_path == library_path:
                # Get current year and quarter
                now = datetime.datetime.now()
                current_year = now.year
                current_quarter = (now.month - 1) // 3 + 1

                # Construct the quarterly folder path
                boekhouding_base = get_rack_settings().get_boekhouding_base()
                folder_path = join_native_path(boekhouding_base, str(current_year), f"Q{current_quarter}")

            if os.path.exists(folder_path):
                open_path(folder_path)
                self.update_status(f"Opened folder: {folder_path}", "info")
            else:
                self.update_status(f"Folder not found: {folder_path}", "warning")
        except Exception as e:
            self.update_status(f"Error opening folder: {e}", "error")

    def open_logs_folder(self):
        """Open the centralized logs folder in the OS file browser."""
        logs_folder = LOG_DIR
        try:
            # Create the folder if it doesn't exist
            os.makedirs(logs_folder, exist_ok=True)
            open_path(logs_folder)
            self.update_status(f"Opened logs folder: {logs_folder}", "info")
        except Exception as e:
            self.update_status(f"Error opening logs folder: {e}", "error")

    def _add_tooltip(self, widget, text):
        """Add a tooltip to a widget."""
        tooltip = None

        def show_tooltip(event):
            nonlocal tooltip
            x, y, _, _ = widget.bbox("insert") if hasattr(widget, 'bbox') else (0, 0, 0, 0)
            x += widget.winfo_rootx() + 25
            y += widget.winfo_rooty() + 25

            tooltip = tk.Toplevel(widget)
            tooltip.wm_overrideredirect(True)
            tooltip.wm_geometry(f"+{x}+{y}")

            label = tk.Label(
                tooltip,
                text=text,
                bg="#333333",
                fg="white",
                relief=tk.SOLID,
                borderwidth=1,
                font=("Segoe UI", 9),
                padx=8,
                pady=4
            )
            label.pack()

        def hide_tooltip(event):
            nonlocal tooltip
            if tooltip:
                tooltip.destroy()
                tooltip = None

        widget.bind("<Enter>", show_tooltip)
        widget.bind("<Leave>", hide_tooltip)

    def _add_header_hint(self, widget, text):
        """Add a hint that shows in the status bar header when hovering over a widget."""
        def show_hint(event):
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text=text)

        def hide_hint(event):
            if hasattr(self, 'header_hint_label'):
                self.header_hint_label.config(text="")

        widget.bind("<Enter>", show_hint, add="+")
        widget.bind("<Leave>", hide_hint, add="+")

    def open_settings(self):
        """Open the settings dialog, or close it if it's already open.

        The dialog no longer closes itself on Save (so you can keep
        tweaking and saving without losing your place) — this toggle is
        how a second press of the Settings button/shortcut closes it.
        """
        existing = getattr(self, '_settings_dialog', None)
        if existing is not None and existing.dialog.winfo_exists():
            existing._cancel()
            return

        prev_on_bottom = self.settings.get_always_on_bottom()
        dialog = self._settings_dialog = SettingsDialog(self.root, self.settings)
        saved = dialog.show()
        self._settings_dialog = None
        if saved:
            self.update_status("Settings saved", "success")
            # Reload path config to reflect changes
            self.settings = get_rack_settings()
            # Apply always-on-bottom change without restart
            new_on_bottom = self.settings.get_always_on_bottom()
            if new_on_bottom != prev_on_bottom:
                from shared_window_zorder import install_keep_on_bottom, uninstall_keep_on_bottom
                if new_on_bottom:
                    install_keep_on_bottom(self.root)
                else:
                    uninstall_keep_on_bottom(self.root)

    def open_help(self):
        """Open the keyboard shortcuts documentation."""
        shortcuts_path = os.path.join(SCRIPT_FILE_DIR, "SHORTCUTS.md")
        try:
            if os.path.exists(shortcuts_path):
                open_path(shortcuts_path)
                self.update_status("Opened keyboard shortcuts documentation", "info")
            else:
                self.update_status(f"Shortcuts file not found: {shortcuts_path}", "error")
        except Exception as e:
            self.update_status(f"Error opening shortcuts: {e}", "error")

    def refresh_projects(self):
        """Delete database and perform fresh import of all projects (F5)."""
        if hasattr(self, 'project_tracker') and self.project_tracker:
            self.project_tracker.refresh_and_import(silent=True)
        else:
            self.update_status("Project Tracker not available", "error")

    def open_note(self, category_key):
        """Open or create a note file for a category."""
        try:
            # Create notes directory if it doesn't exist
            notes_dir = os.path.join(SCRIPT_FILE_DIR, "notes")
            os.makedirs(notes_dir, exist_ok=True)

            # Create note filename based on category key
            note_filename = f"{category_key.lower()}_notes.txt"
            note_path = os.path.join(notes_dir, note_filename)

            # Create the file if it doesn't exist
            if not os.path.exists(note_path):
                with open(note_path, 'w', encoding='utf-8') as f:
                    f.write(f"# Notes for {category_key}\n\n")
                self.update_status(f"Created new note file: {note_filename}", "info")

            # Open the note file with default text editor
            open_path(note_path)
            self.update_status(f"Opened notes: {note_filename}", "info")

        except Exception as e:
            self.update_status(f"Error opening note: {e}", "error")

    def create_category_card(self, parent, category_key, category_data):
        """Create a professional category card."""
        # Main card frame with border
        card_frame = tk.Frame(parent, bg=COLORS["bg_card"], relief=tk.FLAT, bd=0)
        card_frame.configure(highlightbackground=COLORS["border"], highlightthickness=1)

        # Card header with category color
        header_color = CATEGORY_COLORS.get(category_key, COLORS["accent"])
        header_frame = tk.Frame(card_frame, bg=header_color)
        header_frame.pack(fill=tk.X)

        # Make header clickable if folder_path exists (excluding Global Tools)
        folder_path = category_data.get('folder_path')
        if folder_path:
            header_frame.configure(cursor="hand2")

            # Hover effects for clickable header (will be updated later to include notepad icon)
            def on_header_enter(e):
                # Slightly lighten the header color on hover
                lighter_color = self._lighten_color(header_color)
                header_frame.configure(bg=lighter_color)
                header_container.configure(bg=lighter_color)
                header_content.configure(bg=lighter_color)
                text_container.configure(bg=lighter_color)
                icon_label.configure(bg=lighter_color)
                name_label.configure(bg=lighter_color)
                desc_label.configure(bg=lighter_color)

            def on_header_leave(e):
                # Return to original color
                header_frame.configure(bg=header_color)
                header_container.configure(bg=header_color)
                header_content.configure(bg=header_color)
                text_container.configure(bg=header_color)
                icon_label.configure(bg=header_color)
                name_label.configure(bg=header_color)
                desc_label.configure(bg=header_color)

            def on_header_click(e):
                self.open_folder(folder_path)

            header_frame.bind("<Enter>", on_header_enter)
            header_frame.bind("<Leave>", on_header_leave)
            header_frame.bind("<Button-1>", on_header_click)

        # Container frame to hold both header content and notepad icon
        header_container = tk.Frame(header_frame, bg=header_color)
        header_container.pack(fill=tk.X, padx=15, pady=15)

        # If clickable, bind events to header_container too
        if folder_path:
            header_container.configure(cursor="hand2")
            header_container.bind("<Enter>", lambda e: on_header_enter(e))
            header_container.bind("<Leave>", lambda e: on_header_leave(e))
            header_container.bind("<Button-1>", lambda e: on_header_click(e))

        # Category icon and name/description side by side, aligned to the left
        header_content = tk.Frame(header_container, bg=header_color)
        header_content.pack(side=tk.LEFT, anchor="w")  # Align to left

        # If clickable, bind events to header_content too
        if folder_path:
            header_content.configure(cursor="hand2")
            header_content.bind("<Enter>", lambda e: on_header_enter(e))
            header_content.bind("<Leave>", lambda e: on_header_leave(e))
            header_content.bind("<Button-1>", lambda e: on_header_click(e))

        # Icon on the left
        icon_font = font.Font(family="Segoe UI Emoji", size=20)
        icon_label = tk.Label(header_content,
                             text=category_data.get('icon', ''),
                             font=icon_font,
                             fg="#ffffff",
                             bg=header_color)
        icon_label.pack(side=tk.LEFT, anchor="n")  # Anchor to top

        # If clickable, bind events to icon too
        if folder_path:
            icon_label.configure(cursor="hand2")
            icon_label.bind("<Enter>", lambda e: on_header_enter(e))
            icon_label.bind("<Leave>", lambda e: on_header_leave(e))
            icon_label.bind("<Button-1>", lambda e: on_header_click(e))

        # Text container for name and description next to icon
        text_container = tk.Frame(header_content, bg=header_color)
        text_container.pack(side=tk.LEFT, padx=(5, 0), fill=tk.X, expand=True)

        # If clickable, bind events to text container too
        if folder_path:
            text_container.configure(cursor="hand2")
            text_container.bind("<Enter>", lambda e: on_header_enter(e))
            text_container.bind("<Leave>", lambda e: on_header_leave(e))
            text_container.bind("<Button-1>", lambda e: on_header_click(e))

        # Name on top in the text container
        name_font = font.Font(family="Segoe UI", size=13, weight="bold")
        name_label = tk.Label(text_container,
                             text=category_data['name'],
                             font=name_font,
                             fg="#ffffff",
                             bg=header_color,
                             anchor="w")
        name_label.pack(anchor="w", fill=tk.X)

        # If clickable, bind events to name label too
        if folder_path:
            name_label.configure(cursor="hand2")
            name_label.bind("<Enter>", lambda e: on_header_enter(e))
            name_label.bind("<Leave>", lambda e: on_header_leave(e))
            name_label.bind("<Button-1>", lambda e: on_header_click(e))

        # Description underneath the name in the same text container
        desc_font = font.Font(family="Segoe UI", size=9)
        desc_label = tk.Label(text_container,
                             text=category_data["description"],
                             font=desc_font,
                             fg="#ffffff",
                             bg=header_color,
                             justify=tk.LEFT,
                             anchor="w",
                             wraplength=280)
        desc_label.pack(anchor="w", fill=tk.X, pady=(2, 0))

        # If clickable, bind events to description label too
        if folder_path:
            desc_label.configure(cursor="hand2")
            desc_label.bind("<Enter>", lambda e: on_header_enter(e))
            desc_label.bind("<Leave>", lambda e: on_header_leave(e))
            desc_label.bind("<Button-1>", lambda e: on_header_click(e))

        # Notepad icon on the right side with background box
        notepad_container = tk.Frame(header_container, bg=header_color)
        notepad_container.pack(side=tk.RIGHT, anchor="e", padx=(10, 0))

        # Inner frame for the icon with darkened background
        notepad_bg_color = self._darken_color(header_color, 0.3)  # Darken header color by 30%
        notepad_hover_color = self._lighten_color(header_color, 0.15)  # Lighten slightly on hover

        notepad_frame = tk.Frame(notepad_container,
                                bg=notepad_bg_color,
                                cursor="hand2",
                                relief=tk.FLAT,
                                borderwidth=0)
        notepad_frame.pack(padx=2, pady=2)

        notepad_icon_font = font.Font(family="Segoe UI Emoji", size=18)
        notepad_icon = tk.Label(notepad_frame,
                               text="📝",
                               font=notepad_icon_font,
                               fg="#ffffff",
                               bg=notepad_bg_color,
                               cursor="hand2",
                               padx=6,
                               pady=4)
        notepad_icon.pack()

        # Notepad icon hover effects - change background only, no size change
        def on_notepad_enter(e):
            notepad_frame.configure(bg=notepad_hover_color)
            notepad_icon.configure(bg=notepad_hover_color)

        def on_notepad_leave(e):
            notepad_frame.configure(bg=notepad_bg_color)
            notepad_icon.configure(bg=notepad_bg_color)

        def on_notepad_click(e):
            # Stop event propagation to prevent header click
            self.open_note(category_key)
            return "break"

        notepad_frame.bind("<Enter>", on_notepad_enter)
        notepad_frame.bind("<Leave>", on_notepad_leave)
        notepad_frame.bind("<Button-1>", on_notepad_click)

        notepad_icon.bind("<Enter>", on_notepad_enter)
        notepad_icon.bind("<Leave>", on_notepad_leave)
        notepad_icon.bind("<Button-1>", on_notepad_click)

        # Update header hover effects to include notepad container background
        if folder_path:
            original_on_header_enter = on_header_enter
            original_on_header_leave = on_header_leave

            def on_header_enter(e):
                original_on_header_enter(e)
                lighter_color = self._lighten_color(header_color)
                notepad_container.configure(bg=lighter_color)

            def on_header_leave(e):
                original_on_header_leave(e)
                notepad_container.configure(bg=header_color)

            # Rebind the updated functions
            header_frame.unbind("<Enter>")
            header_frame.unbind("<Leave>")
            header_frame.bind("<Enter>", on_header_enter)
            header_frame.bind("<Leave>", on_header_leave)

            # Also update bindings for child widgets
            header_container.unbind("<Enter>")
            header_container.unbind("<Leave>")
            header_container.bind("<Enter>", lambda e: on_header_enter(e))
            header_container.bind("<Leave>", lambda e: on_header_leave(e))

            header_content.unbind("<Enter>")
            header_content.unbind("<Leave>")
            header_content.bind("<Enter>", lambda e: on_header_enter(e))
            header_content.bind("<Leave>", lambda e: on_header_leave(e))

            icon_label.unbind("<Enter>")
            icon_label.unbind("<Leave>")
            icon_label.bind("<Enter>", lambda e: on_header_enter(e))
            icon_label.bind("<Leave>", lambda e: on_header_leave(e))

            text_container.unbind("<Enter>")
            text_container.unbind("<Leave>")
            text_container.bind("<Enter>", lambda e: on_header_enter(e))
            text_container.bind("<Leave>", lambda e: on_header_leave(e))

            name_label.unbind("<Enter>")
            name_label.unbind("<Leave>")
            name_label.bind("<Enter>", lambda e: on_header_enter(e))
            name_label.bind("<Leave>", lambda e: on_header_leave(e))

            desc_label.unbind("<Enter>")
            desc_label.unbind("<Leave>")
            desc_label.bind("<Enter>", lambda e: on_header_enter(e))
            desc_label.bind("<Leave>", lambda e: on_header_leave(e))

        # Card body with scrollable content
        body_frame = tk.Frame(card_frame, bg=COLORS["bg_card"])
        body_frame.pack(fill=tk.BOTH, expand=True)

        # Collect and organize all scripts
        all_scripts = []

        # Direct scripts from category
        scripts = category_data.get("scripts", {})
        for script_key, script_data in scripts.items():
            all_scripts.append({
                'key': script_key,
                'data': script_data,
                'category_key': category_key,
                'subcat_key': None,
                'priority': self._get_script_priority(script_key, script_data['name'])
            })

        # Scripts from subcategories
        subcategories = category_data.get("subcategories", {})
        for subcat_key, subcat_data in subcategories.items():
            subcat_scripts = subcat_data.get("scripts", {})
            for script_key, script_data in subcat_scripts.items():
                all_scripts.append({
                    'key': script_key,
                    'data': script_data,
                    'category_key': category_key,
                    'subcat_key': subcat_key,
                    'subcat_name': subcat_data.get('name', ''),
                    'subcat_icon': subcat_data.get('icon', ''),
                    'priority': self._get_script_priority(script_key, script_data['name'])
                })

        # Sort by priority (folder structure first, then backup, then others)
        all_scripts.sort(key=lambda x: x['priority'])

        # Group scripts by type for display
        folder_scripts = [s for s in all_scripts if s['priority'] == 1]
        backup_scripts = [s for s in all_scripts if s['priority'] == 2]
        other_scripts = [s for s in all_scripts if s['priority'] >= 3]

        # Add folder structure scripts
        if folder_scripts:
            for script in folder_scripts:
                self.create_full_width_script_button(body_frame, script)

        # Add backup scripts
        if backup_scripts:
            for script in backup_scripts:
                self.create_full_width_script_button(body_frame, script)

        # Add other scripts (subcategory headers removed for cleaner UI)
        for script in other_scripts:
            self.create_full_width_script_button(body_frame, script)

        # If no scripts available
        if not all_scripts:
            no_scripts_font = font.Font(family="Segoe UI", size=9, slant="italic")
            no_scripts_label = tk.Label(body_frame,
                                       text="No scripts available yet",
                                       font=no_scripts_font,
                                       fg=COLORS["text_secondary"],
                                       bg=COLORS["bg_card"])
            no_scripts_label.pack(pady=20)

        return card_frame

    def _lighten_color(self, hex_color, factor=0.2):
        """Lighten a hex color by a given factor (0.0 to 1.0)."""
        try:
            # Remove the # if present
            hex_color = hex_color.lstrip('#')

            # Convert to RGB
            rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

            # Lighten each component
            rgb_lightened = tuple(min(255, int(c + (255 - c) * factor)) for c in rgb)

            # Convert back to hex
            return '#{:02x}{:02x}{:02x}'.format(*rgb_lightened)
        except:
            # Return original color if conversion fails
            return hex_color

    def _darken_color(self, hex_color, factor=0.2):
        """Darken a hex color by a given factor (0.0 to 1.0)."""
        try:
            # Remove the # if present
            hex_color = hex_color.lstrip('#')

            # Convert to RGB
            rgb = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

            # Darken each component
            rgb_darkened = tuple(max(0, int(c * (1 - factor))) for c in rgb)

            # Convert back to hex
            return '#{:02x}{:02x}{:02x}'.format(*rgb_darkened)
        except:
            # Return original color if conversion fails
            return hex_color

    def _get_script_priority(self, script_key, script_name):
        """Get priority for script ordering (1=highest priority)."""
        # Check both script key and name for folder structure
        if 'folder_structure' in script_key.lower() or 'folder structure' in script_name.lower():
            return 1
        # Check for backup scripts
        elif 'backup' in script_key.lower() or 'backup' in script_name.lower():
            return 2
        # Formatting utilities sort last
        elif 'format' in script_key.lower() or 'format' in script_name.lower():
            return 4
        else:
            return 3

    def add_subcategory_header(self, parent, icon, name):
        """Add a subcategory header."""
        # Section title
        title_frame = tk.Frame(parent, bg=COLORS["bg_card"])
        title_frame.pack(fill=tk.X, padx=15, pady=(10, 2))

        title_font = font.Font(family="Segoe UI", size=10, weight="bold")
        title_label = tk.Label(title_frame,
                              text=f"{icon} {name}",
                              font=title_font,
                              fg=COLORS["accent"],
                              bg=COLORS["bg_card"])
        title_label.pack(anchor=tk.W)

    def create_full_width_script_button(self, parent, script):
        """Create a full-width script button with name and description."""
        script_data = script['data']

        # Button frame that spans full width
        button_frame = tk.Frame(parent, bg=COLORS["bg_secondary"], cursor="hand2")
        button_frame.pack(fill=tk.X, pady=2, padx=15)

        # Inner content frame for proper text alignment
        content_frame = tk.Frame(button_frame, bg=COLORS["bg_secondary"])
        content_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=8)

        # Add hover effect
        def on_enter(e):
            button_frame.configure(bg=COLORS["bg_hover"])
            content_frame.configure(bg=COLORS["bg_hover"])
            name_label.configure(bg=COLORS["bg_hover"])
            desc_label.configure(bg=COLORS["bg_hover"])

        def on_leave(e):
            button_frame.configure(bg=COLORS["bg_secondary"])
            content_frame.configure(bg=COLORS["bg_secondary"])
            name_label.configure(bg=COLORS["bg_secondary"])
            desc_label.configure(bg=COLORS["bg_secondary"])

        button_frame.bind("<Enter>", on_enter)
        button_frame.bind("<Leave>", on_leave)

        # Script name (left aligned, bold)
        name_font = font.Font(family="Segoe UI", size=10, weight="bold")
        name_label = tk.Label(content_frame,
                            text=f"{script_data.get('icon', '▶')} {script_data['name']}",
                            font=name_font,
                            fg=COLORS["text_primary"],
                            bg=COLORS["bg_secondary"],
                            cursor="hand2",
                            anchor="w",
                            justify=tk.LEFT)
        name_label.pack(anchor="w", fill=tk.X)

        # Script description (left aligned, smaller, secondary color)
        desc_font = font.Font(family="Segoe UI", size=8)
        desc_label = tk.Label(content_frame,
                            text=script_data.get('description', ''),
                            font=desc_font,
                            fg=COLORS["text_secondary"],
                            bg=COLORS["bg_secondary"],
                            cursor="hand2",
                            anchor="w",
                            justify=tk.LEFT,
                            wraplength=280)
        desc_label.pack(anchor="w", fill=tk.X, pady=(2, 0))

        # Make the entire frame clickable
        def run_script_wrapper(e=None):
            self.run_script(script['category_key'], script['key'], script.get('subcat_key'))

        button_frame.bind("<Button-1>", run_script_wrapper)
        content_frame.bind("<Button-1>", run_script_wrapper)
        name_label.bind("<Button-1>", run_script_wrapper)
        desc_label.bind("<Button-1>", run_script_wrapper)

        # Bind hover events to all elements
        content_frame.bind("<Enter>", on_enter)
        content_frame.bind("<Leave>", on_leave)
        name_label.bind("<Enter>", on_enter)
        name_label.bind("<Leave>", on_leave)
        desc_label.bind("<Enter>", on_enter)
        desc_label.bind("<Leave>", on_leave)

    def create_status_bar(self):
        """Create professional status bar."""
        status_container = tk.Frame(self.main_container, bg=COLORS["bg_secondary"])
        status_container.pack(fill=tk.X, side=tk.BOTTOM)

        # Collapsible status area - load saved state from config
        self.status_expanded = self.config_manager.config.get("status_log_expanded", True)

        # Status header bar
        header_bar = tk.Frame(status_container, bg=COLORS["border"], height=1)
        header_bar.pack(fill=tk.X)

        header_frame = tk.Frame(status_container, bg=COLORS["bg_secondary"], height=35)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        # Toggle button
        self.toggle_button = tk.Label(header_frame,
                                     text="▼ Status Log",
                                     font=font.Font(family="Segoe UI", size=10),
                                     fg=COLORS["text_primary"],
                                     bg=COLORS["bg_secondary"],
                                     cursor="hand2")
        self.toggle_button.pack(side=tk.LEFT, padx=30, pady=8)
        self.toggle_button.bind("<Button-1>", self.toggle_status)

        # Hint label for header buttons (right side of status bar)
        self.header_hint_label = tk.Label(header_frame,
                                         text="",
                                         font=font.Font(family="Segoe UI", size=9),
                                         fg=COLORS["text_secondary"],
                                         bg=COLORS["bg_secondary"])
        self.header_hint_label.pack(side=tk.RIGHT, padx=30, pady=8)

        # Status text container
        self.status_text_container = tk.Frame(status_container, bg=COLORS["bg_primary"], height=150)
        self.status_text_container.pack(fill=tk.X, padx=30, pady=(0, 10))
        self.status_text_container.pack_propagate(False)

        # Status text area
        self.status_text = tk.Text(self.status_text_container,
                                  bg=COLORS["bg_primary"],
                                  fg=COLORS["text_primary"],
                                  font=font.Font(family="Consolas", size=9),
                                  relief=tk.FLAT,
                                  wrap=tk.WORD,
                                  height=8,
                                  padx=10,
                                  pady=10)
        # Scrollbar packed before the text widget: a side=LEFT,
        # expand=True widget packed first can crush a fixed-width
        # side=RIGHT sibling packed after it down to ~1px instead of its
        # requested width (same fix as ScrollableFrame above).
        scrollbar = ttk.Scrollbar(self.status_text_container, command=self.status_text.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.status_text.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)
        self.status_text.config(yscrollcommand=scrollbar.set)

        # Configure tags
        self.status_text.tag_configure("error", foreground=COLORS["error"])
        self.status_text.tag_configure("warning", foreground=COLORS["warning"])
        self.status_text.tag_configure("success", foreground=COLORS["success"])
        self.status_text.tag_configure("info", foreground=COLORS["text_primary"])
        self.status_text.tag_configure("timestamp", foreground=COLORS["text_secondary"])

        # Initial message
        self.update_status("FastRack ready", "info")

        # Warn about any missing optional dependencies
        self._check_optional_dependencies()

        # Warn about any configured paths that don't resolve
        self._check_configured_paths()

        # Apply saved collapse state
        if not self.status_expanded:
            self.status_text_container.pack_forget()
            self.toggle_button.config(text="▶ Status Log")

    def _check_configured_paths(self):
        """Validate configured drives/paths; warn in the status log if any are unreachable."""
        checks = [
            ("Active Drive", self.settings.get_work_drive()),
            ("Active Base", self.settings.get_active_base()),
            ("Archive Base", self.settings.get_archive_base()),
            ("Software (NAS)", self.settings.get_mapped_software_path()),
            ("Launchers Path", self.settings.get_launchers_base_path()),
        ]

        missing = []
        for label, value in checks:
            if not value:
                missing.append((label, "(not set)"))
                continue
            ok, msg = self.settings.validate_drive(value)
            if not ok:
                missing.append((label, f"{value} — {msg}"))

        if not missing:
            return

        self.update_status(
            f"{len(missing)} configured path(s) unreachable — open Settings (Ctrl+,) > Paths to fix",
            "warning",
        )
        for label, detail in missing:
            self.update_status(f"  - {label}: {detail}", "warning")
            logger.warning("Configured path unreachable: %s -> %s", label, detail)

    def _check_optional_dependencies(self):
        """Probe optional third-party deps; warn in the status log for any missing."""
        deps = [
            ("PIL", "Pillow", "logo and image processing"),
            ("requests", "requests", "WooCommerce order monitoring"),
            ("yaml", "PyYAML", "invoice template parsing"),
        ]
        if sys.platform == "win32":
            deps.append(("win32com.client", "pywin32", "Outlook integration, shortcut creation"))

        missing = []
        for module_name, pip_name, purpose in deps:
            try:
                __import__(module_name)
            except ImportError:
                missing.append((pip_name, purpose))

        if not missing:
            return

        pip_names = " ".join(p for p, _ in missing)
        self.update_status(
            f"Missing optional dependencies — run: pip install {pip_names}",
            "warning",
        )
        for pip_name, purpose in missing:
            self.update_status(f"  - {pip_name}: needed for {purpose}", "warning")
            logger.warning("Missing optional dependency: %s (%s)", pip_name, purpose)

    def toggle_status(self, event=None):
        """Toggle status area visibility."""
        if self.status_expanded:
            self.status_text_container.pack_forget()
            self.toggle_button.config(text="▶ Status Log")
            self.status_expanded = False
        else:
            self.status_text_container.pack(fill=tk.X, padx=30, pady=(0, 10))
            self.toggle_button.config(text="▼ Status Log")
            self.status_expanded = True

        # Save state to config
        self.config_manager.config["status_log_expanded"] = self.status_expanded
        self.config_manager._save_config()

    def update_status(self, message, status_type="info"):
        """Update the status text widget."""
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")

        self.status_text.insert(tk.END, f"[{timestamp}] ", "timestamp")
        self.status_text.insert(tk.END, f"{message}\n", status_type)
        self.status_text.see(tk.END)

        self.root.update_idletasks()

    def select_category_by_name(self, category_name):
        """Select a category or operation by name (for backwards compatibility)."""
        # Find the category key by name
        for key, data in PIPELINE_CATEGORIES.items():
            if data.get("name", "").lower() == category_name.lower() or key.lower() == category_name.lower():
                self._select_category(key)
                return
        # If not found, clear selection
        self._clear_category_selection()

    def run_script(self, category_key, script_key, subcat_key=None, direct_run=False):
        """Run a script."""
        # Get script data
        category = PIPELINE_CATEGORIES.get(category_key, {})

        if subcat_key:
            script_data = category.get("subcategories", {}).get(subcat_key, {}).get("scripts", {}).get(script_key)
            config_key = f"{subcat_key}_{script_key}"
        else:
            script_data = category.get("scripts", {}).get(script_key)
            config_key = script_key

        if not script_data:
            self.update_status(f"Script not found: {script_key}", "error")
            return

        # Handle URL shortcuts - open in browser
        if "url" in script_data:
            import webbrowser
            webbrowser.open(script_data["url"])
            self.update_status(f"Opened: {script_data['name']}", "info")
            return

        if "path" not in script_data:
            self.update_status(f"Script not found: {script_key}", "error")
            return

        script_path = script_data["path"]

        # Get script configuration
        script_config = self.config_manager.get_script_config(category_key, config_key)

        # Update last run timestamp
        script_config["last_run"] = datetime.datetime.now().isoformat()
        self.config_manager.update_script_config(category_key, config_key, script_config)

        # Run the script
        self.update_status(f"Starting: {script_data['name']}", "info")

        args = list(script_config.get("args", []))
        if direct_run:
            args.append("--auto-run")

        # Run script in a separate thread
        threading.Thread(
            target=lambda: ScriptRunner.run_script(
                script_path,
                args=args,
                env_vars=script_config.get("env_vars", {}),
                callback=self.update_status
            ),
            daemon=True
        ).start()

# ====================================
# MAIN APPLICATION ENTRY POINT
# ====================================

def main():
    """Main application entry point."""
    # Setup logging when the app actually runs (not at import time)
    setup_logging("pipeline")

    root = tk.Tk()

    # Create main application
    app = ProfessionalPipelineGUI(root)

    # Pin the hub to the bottom of the Windows z-order so it behaves
    # like a launcher pane — other apps and standalone module windows
    # always appear above, even when the user clicks on the hub.
    # Toggleable via rack_settings (ui.always_on_bottom).
    if get_rack_settings().get_always_on_bottom():
        from shared_window_zorder import install_keep_on_bottom
        install_keep_on_bottom(root)

    # Bind keyboard shortcuts - existing
    root.bind('<Control-comma>', lambda e: app.open_settings())
    root.bind('<Control-l>', lambda e: app.open_logs_folder())
    root.bind('<F5>', lambda e: app.refresh_projects())
    root.bind('<F1>', lambda e: app.open_help())
    root.bind('<F11>', lambda e: root.attributes('-fullscreen', not root.attributes('-fullscreen')))

    # ====================================
    # FASTRACK KEYBOARD NAVIGATION BINDINGS
    # ====================================

    # Number keys - Global filters (scope 1/2, status 4/5/6)
    root.bind('1', lambda e: app._set_scope("personal") if app._should_handle_keyboard() else None)
    root.bind('2', lambda e: app._set_scope("client") if app._should_handle_keyboard() else None)
    root.bind('4', lambda e: app._toggle_status_filter("active"))
    root.bind('5', lambda e: app._toggle_status_filter("sandbox"))
    root.bind('6', lambda e: app._toggle_status_filter("archived"))

    # Panel navigation (WASD)
    root.bind('w', lambda e: app._nav_panel_up())
    root.bind('W', lambda e: app._nav_panel_up())
    root.bind('s', lambda e: app._nav_panel_down())
    root.bind('S', lambda e: app._nav_panel_down())
    root.bind('a', lambda e: app._nav_panel_left())
    root.bind('A', lambda e: app._nav_panel_left())
    root.bind('d', lambda e: app._nav_panel_right())
    root.bind('D', lambda e: app._nav_panel_right())

    # In-panel navigation (Arrows)
    root.bind('<Up>', lambda e: app._nav_item_up())
    root.bind('<Down>', lambda e: app._nav_item_down())
    root.bind('<Left>', lambda e: app._nav_item_left())
    root.bind('<Right>', lambda e: app._nav_item_right())
    root.bind('<Return>', lambda e: app._on_enter_key())

    # Quick actions
    root.bind('g', lambda e: app._quick_open_folder())
    root.bind('G', lambda e: app._quick_open_folder())
    root.bind('0', lambda e: app._quick_open_folder())  # Alternative for G
    root.bind('n', lambda e: app._quick_open_notes())
    root.bind('N', lambda e: app._quick_open_notes())
    root.bind('.', lambda e: app._quick_open_notes())   # Alternative for N
    root.bind('`', lambda e: app._cycle_scope())
    root.bind('/', lambda e: app._focus_tracker_search())
    root.bind('<Escape>', lambda e: app._on_escape_key())

    # Category quick select (Shift+Key)
    root.bind('<Shift-V>', lambda e: app._quick_select_category('VISUAL'))
    root.bind('<Shift-R>', lambda e: app._quick_select_category('REALTIME'))
    root.bind('<Shift-A>', lambda e: app._quick_select_category('AUDIO'))
    root.bind('<Shift-P>', lambda e: app._quick_select_category('PHYSICAL'))
    root.bind('<Shift-H>', lambda e: app._quick_select_category('PHOTO'))
    root.bind('<Shift-W>', lambda e: app._quick_select_category('WEB'))
    root.bind('<Shift-B>', lambda e: app._quick_select_category('BUSINESS'))
    root.bind('<Shift-G>', lambda e: app._quick_select_category('GLOBAL'))

    # Search shortcut
    root.bind('<Control-f>', lambda e: app._focus_tracker_search())
    root.bind('<Control-F>', lambda e: app._focus_tracker_search())

    # New project shortcut
    root.bind('<Control-n>', lambda e: app._new_project())
    root.bind('<Control-N>', lambda e: app._new_project())

    # Toggle project tracker between list and grid view
    root.bind('t', lambda e: app._toggle_tracker_view())
    root.bind('T', lambda e: app._toggle_tracker_view())

    # Open project deck shortcut
    root.bind('<Control-d>', lambda e: app.open_project_deck())
    root.bind('<Control-D>', lambda e: app.open_project_deck())

    # Start the main loop
    root.mainloop()

if __name__ == "__main__":
    main()
