"""
Shared Keyboard-First Form Module

Provides keyboard navigation, dark-themed styling, and helper functions
for folder structure creator forms.

Design principles:
- Keyboard-first: Every action accessible via keyboard
- Dark theme: Match main pipeline's GitHub-dark aesthetic
- Efficient layout: Optimize for wide 16:9 screens
"""

import re
import tkinter as tk
from tkinter import ttk, simpledialog
from typing import List, Optional, Callable, Dict

from shared_colors import (
    BG_DARKEST, BG_RAISED_1, BG_RAISED_2, BG_HOVER,
    TEXT_PRIMARY, TEXT_SECONDARY, TEXT_PLACEHOLDER,
    ACCENT, ACCENT_DARK, ACCENT_HOVER,
    BORDER, SUCCESS, WARNING, ERROR,
)


# ==================== COLOR SCHEME ====================
# Matching the main pipeline's GitHub-dark aesthetic

FORM_COLORS = {
    # Backgrounds
    "bg": BG_DARKEST,              # Form background
    "bg_input": BG_RAISED_1,       # Input field background
    "bg_input_focus": BG_RAISED_2, # Focused input background
    "bg_hover": BG_HOVER,          # Hover state

    # Text
    "text": TEXT_PRIMARY,           # Primary text
    "text_dim": TEXT_SECONDARY,     # Labels, hints, placeholders
    "text_placeholder": TEXT_PLACEHOLDER, # Placeholder text

    # Accents
    "accent": ACCENT,              # Focus ring, links
    "accent_dark": ACCENT_DARK,    # Buttons, active elements
    "accent_hover": ACCENT_HOVER,  # Hover state for accents

    # Borders
    "border": BORDER,              # Input borders
    "border_focus": ACCENT,        # Focused border

    # Status
    "success": SUCCESS,
    "warning": WARNING,
    "error": ERROR,
}


# ==================== KEYBOARD NAVIGATION MIXIN ====================

class FormKeyboardMixin:
    """
    Mixin class providing keyboard navigation for folder structure forms.

    Provides:
    - Tab/Shift+Tab navigation through fields
    - Enter to move to next field (in Entry widgets)
    - Ctrl+Enter to create project from anywhere
    - Escape to cancel/close form
    - P key to toggle Personal checkbox (when not in text field)
    - Space to toggle checkbox (when focused)

    Usage:
        class MyForm(FormKeyboardMixin):
            def __init__(self, parent, ...):
                # Build your form
                self._build_form()

                # Then set up keyboard navigation
                self._setup_keyboard_navigation()
    """

    # These should be set by the implementing class
    _focusable_widgets: List[tk.Widget] = []
    _create_btn: Optional[tk.Widget] = None
    _browse_btn: Optional[tk.Widget] = None
    _notes_widget: Optional[tk.Widget] = None
    _personal_checkbox: Optional[tk.Widget] = None
    _personal_var: Optional[tk.BooleanVar] = None

    def _setup_keyboard_navigation(self):
        """
        Initialize keyboard navigation for the form.

        Call this after building the form and setting up:
        - self._focusable_widgets: List of widgets in tab order
        - self._create_btn: The create/submit button
        - self._notes_widget: The notes Text widget (optional)
        - self._personal_checkbox: The personal checkbox widget (optional)
        - self._personal_var: The BooleanVar for personal toggle (optional)
        """
        if not hasattr(self, 'parent') or self.parent is None:
            return

        root = self.parent.winfo_toplevel()

        # Global keyboard shortcuts
        root.bind("<Control-Return>", self._on_ctrl_enter)
        root.bind("<Escape>", self._on_escape)

        # P key for Personal toggle (only when not in text entry)
        root.bind("<p>", self._on_p_key)
        root.bind("<P>", self._on_p_key)

        # Set up navigation for each focusable widget
        for i, widget in enumerate(self._focusable_widgets):
            # Ctrl+Enter on every widget to ensure it fires create_structure
            widget.bind("<Control-Return>", self._on_ctrl_enter)

            # Enter key moves to next field (for Entry widgets)
            if isinstance(widget, (tk.Entry, ttk.Entry)):
                widget.bind("<Return>", lambda e, idx=i: self._focus_next(idx))

            # Track when we enter text fields (for P key filtering)
            widget.bind("<FocusIn>", self._on_focus_in)
            widget.bind("<FocusOut>", self._on_focus_out)

        # Set up Tab navigation for notes widget
        if self._notes_widget:
            self._notes_widget.bind("<Tab>", self._on_notes_tab)
            self._notes_widget.bind("<Shift-Tab>", self._on_notes_shift_tab)

        # Bind Ctrl+Enter on buttons too
        if self._create_btn:
            self._create_btn.bind("<Control-Return>", self._on_ctrl_enter)
        if self._browse_btn:
            self._browse_btn.bind("<Control-Return>", self._on_ctrl_enter)

        # Auto-focus first widget
        if self._focusable_widgets:
            self._focusable_widgets[0].focus_set()

    def _focus_next(self, current_index: int):
        """Move focus to the next widget in the tab order."""
        if current_index + 1 < len(self._focusable_widgets):
            next_widget = self._focusable_widgets[current_index + 1]

            # Skip notes if it's empty and move to create button
            if next_widget == self._notes_widget:
                if hasattr(self._notes_widget, 'get'):
                    content = self._notes_widget.get("1.0", "end-1c").strip()
                    if not content and self._create_btn:
                        self._create_btn.focus_set()
                        return

            next_widget.focus_set()
        elif self._create_btn:
            self._create_btn.focus_set()

        return "break"  # Prevent default Enter behavior

    def _on_ctrl_enter(self, event):
        """Handle Ctrl+Enter to create project."""
        if hasattr(self, 'create_structure') and callable(self.create_structure):
            self.create_structure()
        return "break"

    def _on_escape(self, event):
        """Handle Escape to close/cancel form."""
        if hasattr(self, '_handle_cancel') and callable(self._handle_cancel):
            self._handle_cancel()
        elif hasattr(self, 'on_cancel') and self.on_cancel:
            self.on_cancel()
        return "break"

    def _on_p_key(self, event):
        """Handle P key to toggle Personal checkbox."""
        # Don't toggle if we're in a text field
        if hasattr(self, '_in_text_field') and self._in_text_field:
            return

        # Toggle personal checkbox
        if self._personal_var is not None:
            self._personal_var.set(not self._personal_var.get())

            # Call toggle handler if it exists
            if hasattr(self, 'toggle_personal') and callable(self.toggle_personal):
                self.toggle_personal()

        return "break"

    def _on_focus_in(self, event):
        """Track when focus enters a text field."""
        widget = event.widget
        if isinstance(widget, (tk.Entry, ttk.Entry, tk.Text)):
            self._in_text_field = True

    def _on_focus_out(self, event):
        """Track when focus leaves a text field."""
        self._in_text_field = False

    def _on_notes_tab(self, event):
        """Handle Tab in notes field - move to browse button, then create button."""
        if self._browse_btn:
            self._browse_btn.focus_set()
        elif self._create_btn:
            self._create_btn.focus_set()
        return "break"  # Prevent tab character insertion

    def _on_notes_shift_tab(self, event):
        """Handle Shift+Tab in notes field - move to previous widget."""
        if self._notes_widget and self._focusable_widgets:
            try:
                notes_index = self._focusable_widgets.index(self._notes_widget)
                if notes_index > 0:
                    self._focusable_widgets[notes_index - 1].focus_set()
            except ValueError:
                # Notes widget not in focusable list, just go to last entry
                for widget in reversed(self._focusable_widgets):
                    if isinstance(widget, (tk.Entry, ttk.Entry)):
                        widget.focus_set()
                        break
        return "break"  # Prevent default behavior

    def _collect_focusable_widgets(self):
        """
        Override this method to collect and order focusable widgets.

        Should set:
        - self._focusable_widgets
        - self._create_btn
        - self._notes_widget (optional)
        - self._personal_checkbox (optional)
        - self._personal_var (optional)
        """
        raise NotImplementedError("Subclass must implement _collect_focusable_widgets")


# ==================== STYLED WIDGET HELPERS ====================

def configure_dark_theme(root):
    """
    Configure ttk styles for dark theme.

    Call this once when setting up the main window or embedded frame.
    """
    style = ttk.Style(root)

    # Try to use 'clam' theme as base (works better with custom colors)
    try:
        style.theme_use('clam')
    except tk.TclError:
        pass  # Use default if clam not available

    # Configure TFrame
    style.configure("Dark.TFrame",
                    background=FORM_COLORS["bg"])

    # Configure TLabelframe
    style.configure("Dark.TLabelframe",
                    background=FORM_COLORS["bg"],
                    foreground=FORM_COLORS["text"])
    style.configure("Dark.TLabelframe.Label",
                    background=FORM_COLORS["bg"],
                    foreground=FORM_COLORS["text"])

    # Configure TLabel
    style.configure("Dark.TLabel",
                    background=FORM_COLORS["bg"],
                    foreground=FORM_COLORS["text"])
    style.configure("Dark.Dim.TLabel",
                    background=FORM_COLORS["bg"],
                    foreground=FORM_COLORS["text_dim"])

    # Configure TEntry
    style.configure("Dark.TEntry",
                    fieldbackground=FORM_COLORS["bg_input"],
                    foreground=FORM_COLORS["text"],
                    insertcolor=FORM_COLORS["text"],
                    bordercolor=FORM_COLORS["border"],
                    lightcolor=FORM_COLORS["border"],
                    darkcolor=FORM_COLORS["border"])
    style.map("Dark.TEntry",
              fieldbackground=[("focus", FORM_COLORS["bg_input_focus"])],
              bordercolor=[("focus", FORM_COLORS["border_focus"])])

    # Configure TButton
    style.configure("Dark.TButton",
                    background=FORM_COLORS["accent_dark"],
                    foreground=FORM_COLORS["text"],
                    bordercolor=FORM_COLORS["accent_dark"],
                    padding=(15, 8))
    style.map("Dark.TButton",
              background=[("active", FORM_COLORS["accent"]),
                         ("pressed", FORM_COLORS["accent_dark"])])

    # Configure primary (accent) button
    style.configure("Dark.Primary.TButton",
                    background=FORM_COLORS["accent_dark"],
                    foreground="#ffffff",
                    bordercolor=FORM_COLORS["accent_dark"],
                    padding=(20, 10))
    style.map("Dark.Primary.TButton",
              background=[("active", FORM_COLORS["accent"]),
                         ("pressed", FORM_COLORS["accent_dark"])])

    # Configure TCheckbutton
    style.configure("Dark.TCheckbutton",
                    background=FORM_COLORS["bg"],
                    foreground=FORM_COLORS["text"])
    style.map("Dark.TCheckbutton",
              background=[("active", FORM_COLORS["bg"])])

    # Configure TCombobox
    style.configure("Dark.TCombobox",
                    fieldbackground=FORM_COLORS["bg_input"],
                    background=FORM_COLORS["bg_input"],
                    foreground=FORM_COLORS["text"],
                    arrowcolor=FORM_COLORS["text"],
                    bordercolor=FORM_COLORS["border"])
    style.map("Dark.TCombobox",
              fieldbackground=[("focus", FORM_COLORS["bg_input_focus"])],
              bordercolor=[("focus", FORM_COLORS["border_focus"])])


def create_styled_entry(parent, textvariable=None, width=30, **kwargs) -> tk.Entry:
    """
    Create a dark-themed Entry widget.

    Uses tk.Entry directly for better color control.
    """
    entry = tk.Entry(
        parent,
        textvariable=textvariable,
        width=width,
        bg=FORM_COLORS["bg_input"],
        fg=FORM_COLORS["text"],
        insertbackground=FORM_COLORS["text"],
        highlightbackground=FORM_COLORS["border"],
        highlightcolor=FORM_COLORS["accent"],
        highlightthickness=1,
        relief=tk.FLAT,
        font=("Segoe UI", 10),
        **kwargs
    )

    # Add focus effects
    entry.bind("<FocusIn>", lambda e: e.widget.configure(bg=FORM_COLORS["bg_input_focus"]))
    entry.bind("<FocusOut>", lambda e: e.widget.configure(bg=FORM_COLORS["bg_input"]))

    return entry


def add_name_validation(string_var: tk.StringVar):
    """
    Attach a trace to a StringVar that strips any characters not safe for
    folder names and shell scripts.  Only letters, digits, hyphens and
    underscores are allowed.  Spaces and all other symbols are removed
    as the user types.
    """
    _sanitizing = False  # prevent recursive trace calls

    def _sanitize(*_args):
        nonlocal _sanitizing
        if _sanitizing:
            return
        current = string_var.get()
        cleaned = re.sub(r'[^A-Za-z0-9\-_]', '', current)
        if cleaned != current:
            _sanitizing = True
            pos = None
            # Try to preserve cursor position via any linked entry
            string_var.set(cleaned)
            _sanitizing = False

    string_var.trace_add("write", _sanitize)


def create_styled_text(parent, height=3, **kwargs) -> tk.Text:
    """
    Create a dark-themed Text widget.
    """
    text = tk.Text(
        parent,
        height=height,
        bg=FORM_COLORS["bg_input"],
        fg=FORM_COLORS["text"],
        insertbackground=FORM_COLORS["text"],
        highlightbackground=FORM_COLORS["border"],
        highlightcolor=FORM_COLORS["accent"],
        highlightthickness=1,
        relief=tk.FLAT,
        font=("Segoe UI", 10),
        wrap=tk.WORD,
        **kwargs
    )

    # Add focus effects
    text.bind("<FocusIn>", lambda e: e.widget.configure(bg=FORM_COLORS["bg_input_focus"]))
    text.bind("<FocusOut>", lambda e: e.widget.configure(bg=FORM_COLORS["bg_input"]))

    return text


def create_styled_button(parent, text, command=None, primary=False, **kwargs) -> tk.Button:
    """
    Create a dark-themed Button widget with keyboard support.

    Args:
        parent: Parent widget
        text: Button text (can include shortcut hint like "Create (Ctrl+Enter)")
        command: Button command
        primary: If True, use accent color background
    """
    if primary:
        bg = FORM_COLORS["accent_dark"]
        fg = "#ffffff"
        hover_bg = FORM_COLORS["accent"]
    else:
        bg = FORM_COLORS["bg_input"]
        fg = FORM_COLORS["text"]
        hover_bg = FORM_COLORS["bg_hover"]

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=hover_bg,
        activeforeground=fg,
        highlightbackground=FORM_COLORS["bg"],  # Same as bg when not focused
        highlightcolor=FORM_COLORS["accent"],
        highlightthickness=2,  # Always 2 to prevent layout shift
        relief=tk.FLAT,
        font=("Segoe UI", 10),
        cursor="hand2",
        padx=20,
        pady=8,
        **kwargs
    )

    # Hover effects
    btn.bind("<Enter>", lambda e: e.widget.configure(bg=hover_bg))
    btn.bind("<Leave>", lambda e: e.widget.configure(bg=bg))

    # Focus effects - show/hide focus ring
    def on_focus_in(e):
        btn.configure(highlightbackground=FORM_COLORS["accent"])

    def on_focus_out(e):
        btn.configure(highlightbackground=FORM_COLORS["bg"])

    btn.bind("<FocusIn>", on_focus_in)
    btn.bind("<FocusOut>", on_focus_out)

    # Enter key invokes the button (like Space does by default)
    btn.bind("<Return>", lambda e: btn.invoke())

    return btn


def create_styled_label(parent, text, dim=False, **kwargs) -> tk.Label:
    """
    Create a dark-themed Label widget.
    """
    fg = FORM_COLORS["text_dim"] if dim else FORM_COLORS["text"]

    return tk.Label(
        parent,
        text=text,
        bg=FORM_COLORS["bg"],
        fg=fg,
        font=("Segoe UI", 10),
        **kwargs
    )


def create_styled_checkbox(parent, text="", variable=None, command=None, **kwargs) -> tk.Checkbutton:
    """
    Create a dark-themed Checkbutton widget with focus indicator.
    """
    cb = tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bg=FORM_COLORS["bg"],
        fg=FORM_COLORS["text"],
        activebackground=FORM_COLORS["bg"],
        activeforeground=FORM_COLORS["text"],
        selectcolor=FORM_COLORS["bg_input"],
        highlightthickness=2,
        highlightbackground=FORM_COLORS["bg"],
        highlightcolor=FORM_COLORS["accent"],
        font=("Segoe UI", 10),
        **kwargs
    )

    # Show focus ring when focused
    def on_focus_in(e):
        cb.configure(highlightbackground=FORM_COLORS["accent"])

    def on_focus_out(e):
        cb.configure(highlightbackground=FORM_COLORS["bg"])

    cb.bind("<FocusIn>", on_focus_in)
    cb.bind("<FocusOut>", on_focus_out)

    return cb


def create_styled_frame(parent, **kwargs) -> tk.Frame:
    """
    Create a dark-themed Frame widget.
    """
    return tk.Frame(
        parent,
        bg=FORM_COLORS["bg"],
        highlightthickness=0,
        **kwargs
    )


def create_styled_labelframe(parent, text="", **kwargs) -> tk.LabelFrame:
    """
    Create a dark-themed LabelFrame widget.
    """
    return tk.LabelFrame(
        parent,
        text=text,
        bg=FORM_COLORS["bg"],
        fg=FORM_COLORS["text"],
        font=("Segoe UI", 10),
        highlightthickness=0,
        **kwargs
    )


def create_styled_combobox(parent, textvariable=None, values=None, width=20, **kwargs) -> ttk.Combobox:
    """
    Create a styled Combobox widget.

    Note: Combobox styling is limited in ttk, but we set what we can.
    ttk.Combobox doesn't support fg, bg, font directly - must use ttk.Style.
    """
    # Filter out options that ttk.Combobox doesn't support
    unsupported = ['fg', 'bg', 'font', 'highlightthickness', 'relief',
                   'highlightbackground', 'highlightcolor', 'activebackground',
                   'activeforeground', 'selectcolor', 'insertbackground']
    filtered_kwargs = {k: v for k, v in kwargs.items() if k not in unsupported}

    combo = ttk.Combobox(
        parent,
        textvariable=textvariable,
        values=values or [],
        width=width,
        state="readonly",
        **filtered_kwargs
    )
    return combo


# ==================== LAYOUT HELPERS ====================

def create_form_row(parent, label_text: str, widget_factory: Callable,
                    row: int, label_width: int = 12, **widget_kwargs) -> tuple:
    """
    Create a form row with label and widget.

    Returns:
        Tuple of (label, widget)
    """
    label = create_styled_label(parent, label_text, width=label_width, anchor="e")
    label.grid(row=row, column=0, sticky="e", padx=(10, 5), pady=5)

    widget = widget_factory(parent, **widget_kwargs)
    widget.grid(row=row, column=1, sticky="ew", padx=(5, 10), pady=5)

    return label, widget


def create_horizontal_form(parent, fields: List[dict], start_row: int = 0) -> dict:
    """
    Create a horizontal form layout with multiple fields per row.

    Args:
        parent: Parent widget
        fields: List of field definitions, each with:
            - label: Label text
            - type: 'entry', 'checkbox', 'combobox', 'date'
            - variable: tkinter variable
            - width: Optional width
            - values: For combobox, list of values
            - command: For checkbox, command callback
        start_row: Starting row number

    Returns:
        Dictionary mapping field labels to their widgets
    """
    widgets = {}
    col = 0
    row = start_row

    for field in fields:
        label = field.get('label', '')
        field_type = field.get('type', 'entry')
        var = field.get('variable')
        width = field.get('width', 15)

        # Create label
        lbl = create_styled_label(parent, label)
        lbl.grid(row=row, column=col, sticky="e", padx=(10, 5), pady=5)
        col += 1

        # Create widget based on type
        if field_type == 'entry':
            widget = create_styled_entry(parent, textvariable=var, width=width)
        elif field_type == 'checkbox':
            widget = create_styled_checkbox(
                parent,
                variable=var,
                command=field.get('command')
            )
        elif field_type == 'combobox':
            widget = create_styled_combobox(
                parent,
                textvariable=var,
                values=field.get('values', []),
                width=width
            )
        else:
            widget = create_styled_entry(parent, textvariable=var, width=width)

        widget.grid(row=row, column=col, sticky="w", padx=(0, 15), pady=5)
        widgets[label] = widget
        col += 1

        # Check if we need to wrap to next row (after 3 fields)
        if col >= 6:  # 3 fields = 6 columns (label + widget each)
            col = 0
            row += 1

    return widgets


# ==================== KEYBOARD SHORTCUT HINTS ====================

SHORTCUT_HINTS = {
    "create": "Ctrl+Enter",
    "cancel": "Esc",
    "personal": "P",
    "next": "Tab / Enter",
    "prev": "Shift+Tab",
}


def get_shortcut_hint(action: str) -> str:
    """Get the keyboard shortcut hint for an action."""
    return SHORTCUT_HINTS.get(action, "")


def format_button_with_shortcut(text: str, action: str) -> str:
    """Format button text with shortcut hint."""
    shortcut = get_shortcut_hint(action)
    if shortcut:
        return f"{text} ({shortcut})"
    return text


# ==================== SOFTWARE CHIP WIDGET ====================

class SoftwareChip(tk.Frame):
    """
    Toggle chip for software selection with separate version entry.

    A pill-shaped toggle button with a version textbox next to it.
    Click or Enter toggles the chip. Version textbox is read-only until clicked.
    """

    def __init__(self, parent, name: str, default_version: str = "",
                 on_toggle: Optional[Callable[[str, bool, str], None]] = None, **kwargs):
        """
        Initialize a software chip.

        Args:
            parent: Parent widget
            name: Software name (e.g., "Houdini")
            default_version: Default version string (e.g., "20.5")
            on_toggle: Callback when toggled, receives (name, is_active, version)
        """
        super().__init__(parent, bg=FORM_COLORS["bg"], highlightthickness=0, **kwargs)
        self.name = name
        self.version = tk.StringVar(value=default_version)
        self.active = tk.BooleanVar(value=bool(default_version))
        self.on_toggle = on_toggle

        self._build_chip()

    def _build_chip(self):
        """Build the chip UI with toggle button and version entry."""
        # Toggle button (the chip itself) - always has highlight to prevent layout shift
        self.label = tk.Label(
            self,
            text=self.name,
            cursor="hand2",
            padx=10,
            pady=4,
            font=("Segoe UI", 9),
            relief=tk.FLAT,
            borderwidth=0,
            takefocus=1,
            highlightthickness=2,
            highlightbackground=FORM_COLORS["bg"],  # Same as bg when not focused
            highlightcolor=FORM_COLORS["accent"]
        )
        self.label.pack(side=tk.LEFT)
        self.label.bind("<Button-1>", self._on_click)
        self.label.bind("<Return>", self._on_click)
        self.label.bind("<space>", self._on_click)
        self.label.bind("<Enter>", self._on_enter)
        self.label.bind("<Leave>", self._on_leave)
        self.label.bind("<FocusIn>", self._on_label_focus_in)
        self.label.bind("<FocusOut>", self._on_label_focus_out)

        # Version entry (read-only until clicked, NOT in tab order)
        self.version_entry = tk.Entry(
            self,
            textvariable=self.version,
            width=6,
            font=("Segoe UI", 9),
            bg=FORM_COLORS["bg_input"],
            fg=FORM_COLORS["text_dim"],
            insertbackground=FORM_COLORS["text"],
            highlightthickness=1,
            highlightbackground=FORM_COLORS["border"],
            highlightcolor=FORM_COLORS["accent"],
            relief=tk.FLAT,
            state="readonly",
            readonlybackground=FORM_COLORS["bg_input"],
            takefocus=0  # Skip in tab order - only editable via click
        )
        self.version_entry.pack(side=tk.LEFT, padx=(2, 0))
        self.version_entry.bind("<Button-1>", self._on_version_click)
        self.version_entry.bind("<FocusOut>", self._on_version_focus_out)
        self.version_entry.bind("<Return>", self._on_version_enter)
        self.version_entry.bind("<Escape>", self._on_version_escape)
        self.version.trace_add("write", self._on_version_change)

        self._update_style()

    def _on_click(self, event):
        """Handle click/Enter on chip - toggle active state (not on Ctrl+Enter)."""
        # Don't toggle on Ctrl+Enter — that's the create project shortcut
        if hasattr(event, 'state') and event.state & 0x4:  # Ctrl held
            return
        self.active.set(not self.active.get())
        self._update_style()
        if self.on_toggle:
            self.on_toggle(self.name, self.active.get(), self.version.get())
        return "break"

    def _on_enter(self, event):
        """Handle mouse enter for hover effect."""
        if self.active.get():
            self.label.configure(bg=FORM_COLORS["accent"])
        else:
            self.label.configure(bg=FORM_COLORS["bg_hover"])

    def _on_leave(self, event):
        """Handle mouse leave."""
        self._update_style()

    def _on_label_focus_in(self, event):
        """Handle focus on chip label - show focus ring."""
        self.label.configure(highlightbackground=FORM_COLORS["accent"])

    def _on_label_focus_out(self, event):
        """Handle focus out from chip label."""
        self.label.configure(highlightbackground=FORM_COLORS["bg"])
        self._update_style()

    def _on_version_click(self, event):
        """Make version entry editable when clicked."""
        if self.active.get():
            self.version_entry.configure(state="normal")
            self.version_entry.focus_set()
            self.version_entry.select_range(0, tk.END)

    def _on_version_focus_out(self, event):
        """Make version entry read-only when focus leaves."""
        self.version_entry.configure(state="readonly")

    def _on_version_enter(self, event):
        """Handle Enter in version entry - move focus back to chip label."""
        self.version_entry.configure(state="readonly")
        self.label.focus_set()
        return "break"

    def _on_version_escape(self, event):
        """Handle Escape in version entry - cancel editing."""
        self.version_entry.configure(state="readonly")
        self.label.focus_set()
        return "break"

    def _on_version_change(self, *args):
        """Handle version text change."""
        if self.on_toggle and self.active.get():
            self.on_toggle(self.name, True, self.version.get())

    def _update_style(self):
        """Update chip appearance based on active state."""
        if self.active.get():
            self.label.configure(
                bg=FORM_COLORS["accent_dark"],
                fg="#ffffff",
                highlightbackground=FORM_COLORS["accent_dark"],
                highlightcolor=FORM_COLORS["accent"]
            )
            self.version_entry.configure(
                fg=FORM_COLORS["text"],
                readonlybackground=FORM_COLORS["bg_input"]
            )
        else:
            self.label.configure(
                bg=FORM_COLORS["bg_input"],
                fg=FORM_COLORS["text_dim"],
                highlightbackground=FORM_COLORS["bg_input"],
                highlightcolor=FORM_COLORS["accent"]
            )
            self.version_entry.configure(
                fg=FORM_COLORS["text_dim"],
                readonlybackground=FORM_COLORS["bg_input"]
            )

    def is_active(self) -> bool:
        """Check if chip is active."""
        return self.active.get()

    def get_version(self) -> str:
        """Get the current version."""
        return self.version.get()

    def set_active(self, active: bool, version: str = None):
        """Programmatically set chip state."""
        self.active.set(active)
        if version is not None:
            self.version.set(version)
        self._update_style()

    def get_focusable_widgets(self) -> List:
        """Return list of focusable widgets for Tab navigation (just the toggle label)."""
        return [self.label]


def create_software_chip_row(parent, software_list: List[str],
                             defaults: Optional[Dict[str, str]] = None,
                             on_change: Optional[Callable] = None) -> tuple:
    """
    Create a row of software toggle chips with version entries.

    Args:
        parent: Parent widget
        software_list: List of software names ["Houdini", "Blender", ...]
        defaults: Dict of default versions {"Houdini": "20.5", ...}
                  Software with non-empty versions will be active by default
        on_change: Optional callback when any chip changes

    Returns:
        Tuple of (frame, chips_dict) where chips_dict maps software names to SoftwareChip widgets
    """
    frame = create_styled_frame(parent)
    chips = {}

    for name in software_list:
        version = defaults.get(name, "") if defaults else ""
        chip = SoftwareChip(frame, name, default_version=version, on_toggle=on_change)
        chip.pack(side=tk.LEFT, padx=4, pady=2)
        chips[name] = chip

    return frame, chips


def get_chip_focusable_widgets(chips: Dict[str, 'SoftwareChip']) -> List:
    """
    Get all focusable widgets from a dict of software chips.

    Args:
        chips: Dict mapping software names to SoftwareChip widgets

    Returns:
        List of focusable widgets (labels and version entries)
    """
    widgets = []
    for chip in chips.values():
        widgets.extend(chip.get_focusable_widgets())
    return widgets


def get_active_software(chips: Dict[str, SoftwareChip]) -> Dict[str, str]:
    """
    Get dict of active software with their versions.

    Args:
        chips: Dict mapping software names to SoftwareChip widgets

    Returns:
        Dict of active software names to versions, e.g. {"Houdini": "20.5", "Blender": "4.4"}
    """
    return {
        name: chip.get_version()
        for name, chip in chips.items()
        if chip.is_active()
    }
