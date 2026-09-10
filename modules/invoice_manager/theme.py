"""Theme + ttk style configuration for the InvoiceManager shell.

Extends shared_form_keyboard.FORM_COLORS with a handful of named
palette entries the new shell uses (sidebar fill, status dots, etc.)
and registers the ttk styles each widget references.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from shared_form_keyboard import FORM_COLORS


# Extended palette — additive to FORM_COLORS, with a few overrides.
#
# Elevation model:
#   bg          page background (darkest)
#   bg_input    form field fill — sunken to page level so it reads as
#               "carved out" of cards (combined with a 1px border).
#   card_bg     raised surface — cards stand above the page.
#   bg_panel    raised surface inside a card (quarter cells, totals box).
PALETTE = {
    **FORM_COLORS,
    # Override the inherited bg_input (#161b22 collided with card_bg).
    "bg_input":         "#0d1117",
    "bg_input_focus":   "#0d1117",

    "sidebar_bg":       "#0a0e14",
    "sidebar_hover":    "#161b22",
    "sidebar_active":   "#1f6feb",
    "topbar_bg":        "#0d1117",
    "card_bg":          "#161b22",
    "card_border":      "#21262d",
    "bg_panel":         "#1c2128",

    # Form labels — brighter than text_dim, dimmer than text.
    "label_fg":         "#c9d1d9",
    # Input borders — visible enough to define the field edge.
    "input_border":     "#3d444d",
    "input_border_focus": "#58a6ff",

    "chip_bg":          "#21262d",
    "chip_active_bg":   "#1f6feb",
    "chip_text":        "#f0f6fc",
    "dot_filed":        "#3fb950",
    "dot_partial":      "#d29922",
    "dot_missing":      "#f85149",
    "dot_neutral":      "#6e7681",
    "danger_bg":        "#b91c1c",
}

# Fonts — bumped a notch across the board for readability.
FONTS = {
    "h1":            ("Segoe UI", 18, "bold"),
    "h2":            ("Segoe UI", 15, "bold"),
    "h3":            ("Segoe UI", 12, "bold"),
    "body":          ("Segoe UI", 11),
    "body_bold":     ("Segoe UI", 11, "bold"),
    "small":         ("Segoe UI", 10),
    "label":         ("Segoe UI", 10, "bold"),    # form field labels
    "mono":          ("Consolas", 11),
    "mono_big":      ("Consolas", 18, "bold"),
    # Sidebar gets its own scale — it's chrome, not body.
    "sidebar_brand":      ("Segoe UI", 18, "bold"),
    "sidebar_sub":        ("Segoe UI", 10),
    "sidebar_nav":        ("Segoe UI", 12),
    "sidebar_nav_bold":   ("Segoe UI", 12, "bold"),
    "sidebar_icon":       ("Segoe UI Emoji", 15),
}


def install_styles(root: tk.Misc, *, set_theme: bool = True) -> None:
    """Register every ttk style the shell + sections rely on.

    Called once at app startup, after the root window exists. When
    InvoiceManager is embedded inside another app (e.g. fastrack_hub),
    pass ``set_theme=False`` so we don't override the host's chosen
    ttk theme — only the named ``InvApp.*`` styles get registered,
    which the host doesn't use.
    """
    C = PALETTE
    style = ttk.Style(root)
    if set_theme:
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

    # Notebook used inside Compose for Items / Expenses
    style.configure("InvApp.TNotebook", background=C["bg"], borderwidth=0)
    style.configure(
        "InvApp.TNotebook.Tab",
        background=C["bg_input"], foreground=C["text"],
        padding=[14, 6], font=FONTS["body_bold"], borderwidth=0,
    )
    style.map(
        "InvApp.TNotebook.Tab",
        background=[("selected", C["sidebar_active"])],
        foreground=[("selected", "#ffffff")],
    )

    # Treeviews — one named style so every section reads the same.
    style.configure(
        "InvApp.Treeview",
        background=C["bg_input"], foreground=C["text"],
        fieldbackground=C["bg_input"], rowheight=26,
        font=FONTS["body"], borderwidth=0,
    )
    style.configure(
        "InvApp.Treeview.Heading",
        background=C["card_border"], foreground=C["text"],
        font=FONTS["body_bold"], borderwidth=0,
    )
    style.map(
        "InvApp.Treeview",
        background=[("selected", C["sidebar_active"])],
        foreground=[("selected", "#ffffff")],
    )

    # Combobox — visible 1px border so the field is distinguishable from
    # the card it sits on; focus ring uses the accent color.
    style.configure(
        "InvApp.TCombobox",
        fieldbackground=C["bg_input"],
        background=C["bg_input"],
        foreground=C["text"],
        arrowcolor=C["text"],
        bordercolor=C["input_border"],
        lightcolor=C["input_border"],
        darkcolor=C["input_border"],
        borderwidth=1,
        padding=4,
    )
    style.map(
        "InvApp.TCombobox",
        bordercolor=[("focus", C["input_border_focus"])],
        lightcolor=[("focus", C["input_border_focus"])],
        darkcolor=[("focus", C["input_border_focus"])],
        fieldbackground=[("readonly", C["bg_input"])],
    )

    # Scrollbar
    style.configure(
        "InvApp.Vertical.TScrollbar",
        background=C["bg_input"], troughcolor=C["bg"],
        borderwidth=0, arrowcolor=C["text"],
    )

    # Separator
    style.configure("InvApp.TSeparator", background=C["card_border"])
