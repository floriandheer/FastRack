"""Flat button helpers with hover state.

Tk's stock Button is fine but verbose to style per call site; these
wrappers bake the InvoiceManager look-and-feel.
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from invoice_manager.theme import PALETTE, FONTS
from shared_color_button import make_button


def secondary_button(parent: tk.Widget, text: str, command: Callable,
                     **kwargs) -> tk.Button:
    """Default flat button — bg_input fill, text foreground."""
    C = PALETTE
    btn = make_button(
        parent, text=text, command=command,
        bg=C["bg_input"], fg=C["text"],
        activebackground=C["bg_hover"], activeforeground=C["text"],
        relief=tk.FLAT, font=FONTS["body"],
        cursor="hand2", padx=kwargs.pop("padx", 12), pady=kwargs.pop("pady", 4),
        **kwargs,
    )
    _bind_hover(btn, C["bg_input"], C["bg_hover"])
    return btn


def primary_button(parent: tk.Widget, text: str, command: Callable,
                   **kwargs) -> tk.Button:
    """Accent-coloured button for the dominant action on a screen."""
    C = PALETTE
    btn = make_button(
        parent, text=text, command=command,
        bg=C["sidebar_active"], fg="white",
        activebackground=C["accent_hover"], activeforeground="white",
        relief=tk.FLAT, font=FONTS["body_bold"],
        cursor="hand2", padx=kwargs.pop("padx", 14), pady=kwargs.pop("pady", 5),
        **kwargs,
    )
    _bind_hover(btn, C["sidebar_active"], C["accent_hover"])
    return btn


def danger_button(parent: tk.Widget, text: str, command: Callable,
                  **kwargs) -> tk.Button:
    C = PALETTE
    btn = make_button(
        parent, text=text, command=command,
        bg=C["danger_bg"], fg="white",
        activebackground="#dc2626", activeforeground="white",
        relief=tk.FLAT, font=FONTS["body_bold"],
        cursor="hand2", padx=kwargs.pop("padx", 12), pady=kwargs.pop("pady", 4),
        **kwargs,
    )
    _bind_hover(btn, C["danger_bg"], "#dc2626")
    return btn


def _bind_hover(btn: tk.Button, normal_bg: str, hover_bg: str) -> None:
    btn.bind("<Enter>", lambda e: e.widget.configure(bg=hover_bg))
    btn.bind("<Leave>", lambda e: e.widget.configure(bg=normal_bg))


def link_label(parent: tk.Widget, text: str, command: Callable,
               **kwargs) -> tk.Label:
    """Underline-style action label for low-emphasis actions."""
    C = PALETTE
    lbl = tk.Label(
        parent, text=text, fg=C["accent"], bg=kwargs.pop("bg", C["bg"]),
        cursor="hand2", font=FONTS["body"], **kwargs,
    )
    lbl.bind("<Button-1>", lambda e: command())
    lbl.bind("<Enter>", lambda e: e.widget.configure(fg=C["accent_hover"]))
    lbl.bind("<Leave>", lambda e: e.widget.configure(fg=C["accent"]))
    return lbl
