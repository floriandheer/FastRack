"""Red banner shown above the top bar when a debug session is active."""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional

from invoice_manager.state import AppState
from invoice_manager.theme import PALETTE, FONTS
from shared_color_button import make_button


class DebugBanner(tk.Frame):
    HEIGHT = 34

    def __init__(self, parent: tk.Widget, state: AppState,
                 on_exit_debug: Callable[[], None],
                 on_open_folder: Optional[Callable[[], None]] = None):
        C = PALETTE
        super().__init__(parent, bg=C["danger_bg"], height=self.HEIGHT)
        self.pack_propagate(False)
        self.state = state

        self.text = tk.StringVar(value="")
        tk.Label(
            self, textvariable=self.text, font=FONTS["body_bold"],
            fg="white", bg=C["danger_bg"], anchor="w",
        ).pack(side="left", padx=16, pady=4)

        # Right-side action buttons (pack right-to-left).
        make_button(
            self, text="Exit debug mode", command=on_exit_debug,
            bg="white", fg=C["danger_bg"],
            activebackground="#fee2e2", activeforeground=C["danger_bg"],
            relief=tk.FLAT, font=FONTS["body_bold"],
            cursor="hand2", padx=10, pady=2,
        ).pack(side="right", padx=(6, 14), pady=4)

        if on_open_folder is not None:
            make_button(
                self, text="📂  Open _DEBUG folder", command=on_open_folder,
                bg="white", fg=C["danger_bg"],
                activebackground="#fee2e2", activeforeground=C["danger_bg"],
                relief=tk.FLAT, font=FONTS["body_bold"],
                cursor="hand2", padx=10, pady=2,
            ).pack(side="right", pady=4)

    def refresh(self) -> None:
        """Update visible text and own pack state from AppState."""
        ds = self.state.debug_session
        if ds.is_active():
            self.text.set(
                f"DEBUG MODE ACTIVE — started {ds.started_at or '?'} · "
                f"{len(ds.created_pdfs)} test PDF(s). Exit restores DB."
            )
        else:
            self.text.set("")
