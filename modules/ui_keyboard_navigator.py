"""
UI Keyboard Navigator - Keyboard navigation system for FastRack.

Provides WASD panel navigation, arrow key item navigation, and keyboard shortcuts
as a mixin class that can be added to the main GUI.
"""

import tkinter as tk
from tkinter import ttk

from ui_theme import COLORS
from ui_pipeline_categories import PIPELINE_CATEGORIES


class KeyboardNavigatorMixin:
    """Mixin class providing keyboard navigation for ProfessionalPipelineGUI.

    Expects the host class to have:
        - self.root: tk.Tk
        - self.focused_panel: str
        - self.last_left_panel: str
        - self.panel_before_creation: str
        - self.category_focus_index: int
        - self.operations_focus_index: int
        - self.tools_focus_index: int
        - self.tool_buttons: list
        - self.CATEGORY_ORDER: list
        - self.OPERATIONS_ORDER: list
        - self.SCOPE_ORDER: list
        - self.STATUS_ORDER: list
        - self.LEFT_PANEL_ORDER: list
        - self.selected_categories: list[str] (ordered; last = primary)
        - self.cat_grid, self.ops_grid: tk.Frame
        - self.tools_section: tk.Frame
        - self.tracker_panel: tk.Frame
        - self._folder_category, self._folder_path, self._notes_category: str
        - self.current_scopes: set[str]
        - self.settings: RackSettings
        - self.project_tracker: ProjectTrackerApp
        - self._select_category(key): method
        - self._set_scope(scope): method
        - self.open_folder(path): method
        - self.open_note(category): method
        - self.run_script(...): method
        - self.header_hint_label: tk.Label
    """

    _CATEGORY_GRID_COLS = 2

    def _category_grid_rows(self):
        """Row layout of the categories grid: pairs of indices, 2 per row,
        except a trailing solo item (e.g. Sandbox) which gets its own
        single-column row."""
        order = self.CATEGORY_ORDER
        cols = self._CATEGORY_GRID_COLS
        return [
            list(range(i, min(i + cols, len(order))))
            for i in range(0, len(order), cols)
        ]

    def _category_row_col(self, rows):
        """(row, col) of the currently focused category within `rows`."""
        for r, indices in enumerate(rows):
            if self.category_focus_index in indices:
                return r, indices.index(self.category_focus_index)
        return 0, 0

    def _should_handle_keyboard(self):
        """Check if keyboard shortcuts should be handled (not when typing in text fields)."""
        focused = self.root.focus_get()
        # Don't handle shortcuts when focused on text input widgets
        return not isinstance(focused, (tk.Entry, tk.Text, ttk.Entry))

    def _nav_panel_up(self):
        """Navigate up between panels (W key)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "tracker":
            # From tracker, W goes to left panel (categories)
            self.focused_panel = "categories"
            self._update_panel_focus()
            self.root.focus_set()
        elif self.focused_panel in self.LEFT_PANEL_ORDER:
            idx = self.LEFT_PANEL_ORDER.index(self.focused_panel)
            if idx > 0:
                self.focused_panel = self.LEFT_PANEL_ORDER[idx - 1]
            else:
                # Wrap to bottom: tools if category selected, otherwise operations
                if self.selected_categories:
                    self.focused_panel = "tools"
                else:
                    self.focused_panel = self.LEFT_PANEL_ORDER[-1]
            self._update_panel_focus()

    def _nav_panel_down(self):
        """Navigate down between panels (S key)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "tracker":
            # From tracker, S goes to tools (bottom of left panel)
            if self.selected_categories:
                self.focused_panel = "tools"
            else:
                # No category selected, go to operations (Business/Global)
                self.focused_panel = "operations"
            self._update_panel_focus()
            self.root.focus_set()
        elif self.focused_panel in self.LEFT_PANEL_ORDER:
            idx = self.LEFT_PANEL_ORDER.index(self.focused_panel)
            if idx < len(self.LEFT_PANEL_ORDER) - 1:
                # Skip tools panel if no category is selected
                next_panel = self.LEFT_PANEL_ORDER[idx + 1]
                if next_panel == "tools" and not self.selected_categories:
                    # Wrap to top
                    self.focused_panel = self.LEFT_PANEL_ORDER[0]
                else:
                    self.focused_panel = next_panel
            else:
                # Wrap to top
                self.focused_panel = self.LEFT_PANEL_ORDER[0]
            self._update_panel_focus()

    def _nav_panel_left(self):
        """Navigate to left panel from tracker (A key)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "tracker":
            # Return to last selected left panel
            self.focused_panel = self.last_left_panel
            self._update_panel_focus()
            # Remove focus from tracker's grid canvas so arrow keys work on left panel
            self.root.focus_set()

    def _nav_panel_right(self):
        """Navigate to project tracker from left panel (D key)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel in self.LEFT_PANEL_ORDER:
            # Remember current left panel before switching to tracker
            self.last_left_panel = self.focused_panel
            self.focused_panel = "tracker"
            self._update_panel_focus()
            self._focus_tracker_widget()

    def _focus_tracker_widget(self):
        """Give focus to whichever tracker widget matches the active view."""
        tracker = getattr(self, 'project_tracker', None)
        if tracker is None:
            return
        view = tracker.view_mode.get() if hasattr(tracker, 'view_mode') else "grid"
        if view == "list" and hasattr(tracker, 'project_tree'):
            tracker.project_tree.focus_set()
        elif hasattr(tracker, 'grid_canvas'):
            tracker.grid_canvas.focus_set()

    def _nav_item_up(self):
        """Navigate up within current panel (Up arrow)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "categories":
            # Grid: up moves to the same column in the previous row (clamped
            # for the ragged trailing Sandbox row).
            rows = self._category_grid_rows()
            row, col = self._category_row_col(rows)
            if row > 0:
                prev_row = rows[row - 1]
                self.category_focus_index = prev_row[min(col, len(prev_row) - 1)]
                self._select_focused_category()
        elif self.focused_panel == "operations":
            # 2x1 grid: no up movement (single row)
            pass
        elif self.focused_panel == "tools":
            # Tools + folder + notes (folder = len(tools), notes = len(tools)+1)
            if self.tools_focus_index > 0:
                self.tools_focus_index -= 1
                self._update_item_focus()
        elif self.focused_panel == "tracker":
            tracker = getattr(self, 'project_tracker', None)
            if tracker is not None:
                if tracker.view_mode.get() == "list":
                    tracker._on_list_up(None)
                else:
                    tracker._on_grid_up(None)

    def _nav_item_down(self):
        """Navigate down within current panel (Down arrow)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "categories":
            # Grid: down moves to the same column in the next row (clamped
            # for the ragged trailing Sandbox row).
            rows = self._category_grid_rows()
            row, col = self._category_row_col(rows)
            if row < len(rows) - 1:
                next_row = rows[row + 1]
                self.category_focus_index = next_row[min(col, len(next_row) - 1)]
                self._select_focused_category()
        elif self.focused_panel == "operations":
            # 2x1 grid: no down movement (single row)
            pass
        elif self.focused_panel == "tools":
            # Tools + folder + notes (folder = len(tools), notes = len(tools)+1)
            max_index = len(self.tool_buttons) + 1  # +2 items (folder, notes), last index = len+1
            if self.tools_focus_index < max_index:
                self.tools_focus_index += 1
                self._update_item_focus()
        elif self.focused_panel == "tracker":
            tracker = getattr(self, 'project_tracker', None)
            if tracker is not None:
                if tracker.view_mode.get() == "list":
                    tracker._on_list_down(None)
                else:
                    tracker._on_grid_down(None)

    def _nav_item_left(self):
        """Navigate left within current panel (Left arrow)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "categories":
            # Flat order: left moves by 1 (crosses rows, including Sandbox)
            if self.category_focus_index > 0:
                self.category_focus_index -= 1
                self._select_focused_category()
        elif self.focused_panel == "operations":
            # 2x1 grid: left moves by 1
            if self.operations_focus_index > 0:
                self.operations_focus_index -= 1
                self._select_focused_operation()
        elif self.focused_panel == "tracker":
            tracker = getattr(self, 'project_tracker', None)
            # Left/Right are meaningless in the 1-D list view; only forward
            # to the grid handler when grid view is active.
            if tracker is not None and tracker.view_mode.get() != "list":
                tracker._on_grid_left(None)

    def _nav_item_right(self):
        """Navigate right within current panel (Right arrow)."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "categories":
            # Flat order: right moves by 1 (crosses rows, including Sandbox)
            if self.category_focus_index < len(self.CATEGORY_ORDER) - 1:
                self.category_focus_index += 1
                self._select_focused_category()
        elif self.focused_panel == "operations":
            # 2x1 grid: right moves by 1
            if self.operations_focus_index < len(self.OPERATIONS_ORDER) - 1:
                self.operations_focus_index += 1
                self._select_focused_operation()
        elif self.focused_panel == "tracker":
            tracker = getattr(self, 'project_tracker', None)
            if tracker is not None and tracker.view_mode.get() != "list":
                tracker._on_grid_right(None)

    def _select_focused_category(self):
        """Select the currently focused category (auto-select on navigation)."""
        if 0 <= self.category_focus_index < len(self.CATEGORY_ORDER):
            category_key = self.CATEGORY_ORDER[self.category_focus_index]
            self._select_category(category_key)

    def _select_focused_operation(self):
        """Select the currently focused operation (auto-select on navigation)."""
        if 0 <= self.operations_focus_index < len(self.OPERATIONS_ORDER):
            ops_key = self.OPERATIONS_ORDER[self.operations_focus_index]
            self._select_category(ops_key)

    def _on_enter_key(self):
        """Handle Enter key to activate focused item."""
        if not self._should_handle_keyboard():
            return
        if self.focused_panel == "categories":
            # Already auto-selected, but Enter can confirm
            pass
        elif self.focused_panel == "operations":
            # Already auto-selected, but Enter can confirm
            pass
        elif self.focused_panel == "tools":
            if 0 <= self.tools_focus_index < len(self.tool_buttons):
                # Run the focused tool
                tool = self.tool_buttons[self.tools_focus_index]
                self.run_script(tool["category_key"], tool["script_key"], tool["subcat_key"])
            elif self.tools_focus_index == len(self.tool_buttons):
                # Open folder
                self._quick_open_folder()
            elif self.tools_focus_index == len(self.tool_buttons) + 1:
                # Open notes
                self._quick_open_notes()
        elif self.focused_panel == "tracker":
            if hasattr(self, 'project_tracker'):
                self.project_tracker._on_enter_key(None)

    def _quick_select_category(self, category_key):
        """Quick select a category via Shift+Letter."""
        if not self._should_handle_keyboard():
            return
        if category_key in PIPELINE_CATEGORIES:
            self._select_category(category_key)
            # Update focus index
            if category_key in self.CATEGORY_ORDER:
                self.focused_panel = "categories"
                self.category_focus_index = self.CATEGORY_ORDER.index(category_key)
            elif category_key in self.OPERATIONS_ORDER:
                self.focused_panel = "operations"
                self.operations_focus_index = self.OPERATIONS_ORDER.index(category_key)
            self._update_panel_focus()

    def _quick_open_folder(self):
        """Quick open current category's folder (G key)."""
        if not self._should_handle_keyboard():
            return
        if self._folder_category and self._folder_path:
            # Check if project tracker is in archive mode
            is_archive_mode = False
            if hasattr(self, 'project_tracker') and self.project_tracker:
                if hasattr(self.project_tracker, 'filter_toggles'):
                    statuses = self.project_tracker._get_active_statuses()
                    is_archive_mode = statuses == {"archived"}

            if is_archive_mode:
                archive_path = self.settings.get_archive_path(self._folder_category)
                self.open_folder(archive_path)
            else:
                self.open_folder(self._folder_path)

    def _quick_open_notes(self):
        """Quick open current category's notes (N key)."""
        if not self._should_handle_keyboard():
            return
        if self._notes_category:
            self.open_note(self._notes_category)

    def _cycle_scope(self):
        """Cycle through scope options: personal → client → both (backtick key)."""
        if not self._should_handle_keyboard():
            return
        scopes = set(getattr(self, "current_scopes", set()))
        both = {"personal", "client"}
        if scopes == {"personal"}:
            next_scopes = {"client"}
        elif scopes == {"client"}:
            next_scopes = both
        else:
            next_scopes = {"personal"}
        # Route through shared session so hub + tracker stay in lockstep.
        session = getattr(self, "session", None)
        if session is not None:
            session.set_scopes(next_scopes)
        else:
            self.current_scopes = next_scopes
            self._apply_scopes()

    def _toggle_status_filter(self, status):
        """Toggle project tracker status filter (4/5/6 keys)."""
        if not self._should_handle_keyboard():
            return
        if hasattr(self, 'project_tracker') and self.project_tracker:
            var = self.project_tracker.filter_toggles.get(status)
            if var:
                var.set(not var.get())
                self.project_tracker._on_filter_changed()

    def _focus_tracker_search(self):
        """Focus the project tracker search field (/ key)."""
        if not self._should_handle_keyboard():
            return
        if hasattr(self, 'project_tracker') and hasattr(self.project_tracker, 'search_entry'):
            self.project_tracker.search_entry.focus_set()

    def _toggle_tracker_view(self):
        """Toggle the project tracker between list and grid view (T key)."""
        if not self._should_handle_keyboard():
            return
        tracker = getattr(self, 'project_tracker', None)
        if tracker is None or not hasattr(tracker, '_toggle_view_mode'):
            return
        tracker._toggle_view_mode()
        # Keep arrow-key navigation working by re-focusing the now-visible
        # view's widget when the user is already in the tracker panel.
        if self.focused_panel == "tracker":
            self._focus_tracker_widget()

    def open_project_deck(self):
        """Open/focus the quick-actions deck for the tracker's selected project (Ctrl+D)."""
        if not self._should_handle_keyboard():
            return
        tracker = getattr(self, 'project_tracker', None)
        if tracker is not None and hasattr(tracker, 'open_deck_for_selected'):
            tracker.open_deck_for_selected()

    def _new_project(self):
        """Create new project for current category (Ctrl+N)."""
        if hasattr(self, 'project_tracker') and self.project_tracker:
            # Launch the creation flow (callback will save current panel state)
            self.project_tracker._on_fab_clicked()
            # Switch focus to tracker for arrow key navigation in subtype selection
            if self.project_tracker.view_state != "PROJECT_LIST":
                self.focused_panel = "tracker"
                self._update_panel_focus()

    def _on_escape_key(self):
        """Handle Escape key - close creation panel if open."""
        # Check if project tracker is in creation mode - close that
        # The cancel callback (_return_to_last_panel) handles focus restoration
        if hasattr(self, 'project_tracker') and self.project_tracker:
            if self.project_tracker.view_state != "PROJECT_LIST":
                self.project_tracker._close_creation_panel()

    def _on_creation_start(self):
        """Called when project creation starts (FAB clicked or Ctrl+N)."""
        # Remember current panel before switching to creation mode
        self.panel_before_creation = self.focused_panel
        if self.focused_panel in self.LEFT_PANEL_ORDER:
            self.last_left_panel = self.focused_panel

    def _return_to_last_panel(self):
        """Return focus to the panel we were on before project creation."""
        self.focused_panel = self.panel_before_creation
        self._update_panel_focus()
        # Set appropriate focus based on panel type
        if self.focused_panel == "tracker":
            self._focus_tracker_widget()
        else:
            self.root.focus_set()

    def _on_project_creation_done(self, project_data=None):
        """Called after successful project creation. Switch filters, refresh, focus tracker, select project."""
        if not hasattr(self, 'project_tracker') or not self.project_tracker:
            return

        tracker = self.project_tracker

        # 1. Reload DB to pick up the project registered by the creation form
        tracker.db.reload()

        # 2. Ensure the right status toggle is on so the new project is visible
        project_status = project_data.get("status", "active") if project_data else "active"
        target_toggle = project_status if project_status in tracker.filter_toggles else "active"
        if not tracker.filter_toggles[target_toggle].get():
            tracker.filter_toggles[target_toggle].set(True)
            tracker._update_filter_button_styles()

        # 3. Switch scope to match the new project (personal vs work)
        if project_data:
            is_personal = (
                project_data.get('client_name', '').lower() == 'personal' or
                project_data.get('metadata', {}).get('is_personal', False)
            )
            target_scope = "personal" if is_personal else "client"
            # Only switch if the target scope isn't already enabled.
            current_scopes = getattr(self, "current_scopes", set())
            if target_scope not in current_scopes:
                self._set_scope(target_scope)

        # 4. Set category filter to match the new project (only if a filter is
        # already active and the new project isn't already covered by it).
        if project_data:
            project_type = project_data.get('project_type', '')
            category = tracker._get_category_for_type(project_type)
            current_cats = getattr(tracker, "selected_categories", set())
            if category and current_cats and category not in current_cats:
                # Route through session when available so hub button styling
                # and tools panel stay in sync. In standalone (no session)
                # fall back to the tracker-local cache update.
                session = getattr(self, "session", None)
                if session is not None:
                    # Session owns hub-side category keys; resolve the display
                    # name back to a key via the pipeline category map.
                    from ui_pipeline_categories import CREATIVE_CATEGORIES
                    match_key = next(
                        (k for k, v in CREATIVE_CATEGORIES.items()
                         if v.get("name") == category),
                        None,
                    )
                    if match_key is not None:
                        session.set_categories([match_key])
                    else:
                        tracker.selected_categories = {category}
                        for name, btn_info in tracker.category_buttons.items():
                            tracker._update_category_card_style(btn_info, name)
                else:
                    tracker.selected_categories = {category}
                    for name, btn_info in tracker.category_buttons.items():
                        tracker._update_category_card_style(btn_info, name)

        # 5. Force geometry update so grid canvas has valid dimensions
        self.root.update_idletasks()

        # 6. Refresh project list (populates grid_projects)
        tracker.refresh_project_list()

        # 6. Focus tracker panel
        self.focused_panel = "tracker"
        self._update_panel_focus()
        self._focus_tracker_widget()

        # 7. Select the newly created project
        if project_data and project_data.get('path'):
            tracker._select_project_by_path(project_data['path'])

    def _update_panel_focus(self):
        """Update visual focus indicator for panels."""
        # Clear all item focus first
        self._clear_all_item_focus()

        # Update panel focus indicators (subtle left accent bar)
        self._update_panel_indicator()

        # Update item focus for current panel
        self._update_item_focus()

        # Update status bar hint
        self._update_keyboard_hint()

    def _update_panel_indicator(self):
        """Update subtle focus indicator for panels (left accent bar on grids only)."""
        # Create/update focus indicator bars if they don't exist
        if not hasattr(self, '_panel_indicators'):
            self._panel_indicators = {}

        # Only show indicators on the actual grids/sections
        grids = [
            ("cat_grid", self.cat_grid, "categories", COLORS["bg_card"]),
            ("ops_grid", self.ops_grid, "operations", COLORS["bg_card"]),
            ("tools_section", self.category_panel_outer if hasattr(self, 'category_panel_outer') else None, "tools", COLORS["border"]),
            ("tracker_panel", self.tracker_panel if hasattr(self, 'tracker_panel') else None, "tracker", COLORS["bg_primary"]),
        ]

        for grid_name, grid_widget, panel_name, bg_color in grids:
            if grid_widget is None:
                continue

            # Check if indicator exists and is still valid
            indicator_valid = (
                grid_name in self._panel_indicators and
                self._panel_indicators[grid_name].winfo_exists()
            )

            # Create indicator if it doesn't exist or was destroyed
            if not indicator_valid:
                indicator = tk.Frame(grid_widget, bg=bg_color, width=3)
                indicator.place(x=0, y=0, relheight=1.0)
                self._panel_indicators[grid_name] = indicator

            # Update indicator color based on focus
            indicator = self._panel_indicators[grid_name]
            try:
                if panel_name == self.focused_panel:
                    indicator.configure(bg=COLORS["accent"])
                else:
                    # Hide by matching background
                    indicator.configure(bg=bg_color)
            except tk.TclError:
                # Widget was destroyed, remove from cache
                del self._panel_indicators[grid_name]

    def _clear_all_item_focus(self):
        """Clear focus highlighting from all items (reset to normal background)."""
        # Clear tool focus - reset to normal background
        for tool in self.tool_buttons:
            tool["frame"].configure(bg=COLORS["bg_secondary"])
            tool["content"].configure(bg=COLORS["bg_secondary"])
            tool["icon_label"].configure(bg=COLORS["bg_secondary"])
            tool["name_label"].configure(bg=COLORS["bg_secondary"])
            tool["arrow_label"].configure(bg=COLORS["bg_secondary"])
        # Clear folder/notes button focus
        if hasattr(self, '_folder_btn_frame'):
            self._folder_btn_frame.configure(bg=self._folder_bg)
            self._folder_content.configure(bg=self._folder_bg)
            self._folder_icon.configure(bg=self._folder_bg)
            self._folder_label.configure(bg=self._folder_bg)
        if hasattr(self, '_notes_btn_frame'):
            self._notes_btn_frame.configure(bg=self._notepad_bg)
            self._notes_content.configure(bg=self._notepad_bg)
            self._notes_icon.configure(bg=self._notepad_bg)
            self._notes_label.configure(bg=self._notepad_bg)

    def _update_item_focus(self):
        """Update visual focus indicator for items within panels (darken focused item)."""
        if self.focused_panel == "categories":
            # Categories auto-select, no extra focus needed
            pass

        elif self.focused_panel == "operations":
            # Operations auto-select, no extra focus needed
            pass

        elif self.focused_panel == "tools":
            # Tools + folder + notes - darken focused item background
            for idx, tool in enumerate(self.tool_buttons):
                if idx == self.tools_focus_index:
                    tool["frame"].configure(bg=COLORS["bg_hover"])
                    tool["content"].configure(bg=COLORS["bg_hover"])
                    tool["icon_label"].configure(bg=COLORS["bg_hover"])
                    tool["name_label"].configure(bg=COLORS["bg_hover"])
                    tool["arrow_label"].configure(bg=COLORS["bg_hover"], fg=tool["color"])
                else:
                    tool["frame"].configure(bg=COLORS["bg_secondary"])
                    tool["content"].configure(bg=COLORS["bg_secondary"])
                    tool["icon_label"].configure(bg=COLORS["bg_secondary"])
                    tool["name_label"].configure(bg=COLORS["bg_secondary"])
                    tool["arrow_label"].configure(bg=COLORS["bg_secondary"], fg=COLORS["text_secondary"])

            # Folder button highlight
            if hasattr(self, '_folder_btn_frame'):
                if self.tools_focus_index == len(self.tool_buttons):
                    self._folder_btn_frame.configure(bg=self._folder_hover)
                    self._folder_content.configure(bg=self._folder_hover)
                    self._folder_icon.configure(bg=self._folder_hover)
                    self._folder_label.configure(bg=self._folder_hover)
                else:
                    self._folder_btn_frame.configure(bg=self._folder_bg)
                    self._folder_content.configure(bg=self._folder_bg)
                    self._folder_icon.configure(bg=self._folder_bg)
                    self._folder_label.configure(bg=self._folder_bg)

            # Notes button highlight
            if hasattr(self, '_notes_btn_frame'):
                if self.tools_focus_index == len(self.tool_buttons) + 1:
                    self._notes_btn_frame.configure(bg=self._notepad_hover)
                    self._notes_content.configure(bg=self._notepad_hover)
                    self._notes_icon.configure(bg=self._notepad_hover)
                    self._notes_label.configure(bg=self._notepad_hover)
                else:
                    self._notes_btn_frame.configure(bg=self._notepad_bg)
                    self._notes_content.configure(bg=self._notepad_bg)
                    self._notes_icon.configure(bg=self._notepad_bg)
                    self._notes_label.configure(bg=self._notepad_bg)

    def _update_keyboard_hint(self):
        """Update status bar with keyboard hints based on focused panel."""
        hints = {
            "categories": "Arrows: navigate | Shift+Letter: quick select | S: operations | D: tracker",
            "operations": "Left/Right: navigate | W: categories | S: tools | D: tracker",
            "tools": "Up/Down: navigate | Enter: run | G: folder | N: notes | W/S: panels",
            "tracker": "Arrows: navigate | Enter: open | T: list/grid | A: left panel | /: search",
        }
        if hasattr(self, 'header_hint_label'):
            self.header_hint_label.config(text=hints.get(self.focused_panel, ""))
