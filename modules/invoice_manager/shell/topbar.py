"""Top bar — title, global year & company filters, debug toggle, reload."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from invoice_manager.state import AppState
from invoice_manager.theme import PALETTE, FONTS
from invoice_manager.widgets.buttons import secondary_button


class TopBar(tk.Frame):
    """Persistent header. Year & company chosen here propagate to every
    section via AppState observers.
    """

    HEIGHT = 56

    def __init__(self, parent: tk.Widget, state: AppState,
                 on_section_reload: Callable[[], None],
                 on_detach: Optional[Callable[[], None]] = None):
        C = PALETTE
        super().__init__(parent, bg=C["topbar_bg"], height=self.HEIGHT)
        self.pack_propagate(False)
        self.state = state
        self._on_section_reload = on_section_reload

        # Section title — set by the app when a section is mounted.
        self.title_var = tk.StringVar(value="Dashboard")
        tk.Label(
            self, textvariable=self.title_var,
            font=FONTS["h1"], fg=C["text"], bg=C["topbar_bg"],
        ).pack(side="left", padx=(20, 0), pady=12)

        # Right side: filters + actions
        right = tk.Frame(self, bg=C["topbar_bg"])
        right.pack(side="right", padx=16, pady=10)

        tk.Label(
            right, text="Year", fg=C["text_dim"], bg=C["topbar_bg"],
            font=FONTS["small"],
        ).pack(side="left", padx=(0, 4))
        self.year_var = tk.StringVar(value=str(state.year))
        self.year_cb = ttk.Combobox(
            right, textvariable=self.year_var,
            values=[str(y) for y in state.available_years()],
            width=6, state="readonly", style="InvApp.TCombobox",
        )
        self.year_cb.pack(side="left", padx=(0, 14))
        self.year_cb.bind("<<ComboboxSelected>>", self._on_year_change)
        # State → UI binding (in addition to UI → state via the combobox event).
        # Keeps the dropdown text correct if year is set from anywhere else.
        state.on_year_change(
            lambda y: self.year_var.set(str(y))
            if self.year_var.get() != str(y) else None
        )

        tk.Label(
            right, text="Company", fg=C["text_dim"], bg=C["topbar_bg"],
            font=FONTS["small"],
        ).pack(side="left", padx=(0, 4))
        self.company_var = tk.StringVar(value=state.company)
        co_values = ["All"] + state.company_keys()
        self.company_cb = ttk.Combobox(
            right, textvariable=self.company_var,
            values=co_values, width=10, state="readonly",
            style="InvApp.TCombobox",
        )
        self.company_cb.pack(side="left", padx=(0, 14))
        self.company_cb.bind("<<ComboboxSelected>>", self._on_company_change)
        state.on_company_change(
            lambda c: self.company_var.set(c)
            if self.company_var.get() != c else None
        )

        secondary_button(right, "↻", on_section_reload, padx=10, pady=3).pack(side="left")

        if on_detach is not None:
            # ⧉ = "pop out into its own window" — only shown when the
            # host (fastrack_hub) embedded us and provided a detach hook.
            detach_btn = secondary_button(
                right, "⧉", on_detach, padx=10, pady=3,
            )
            detach_btn.pack(side="left", padx=(6, 0))

    # ----- API ---------------------------------------------------------

    def set_title(self, text: str) -> None:
        self.title_var.set(text)

    def refresh_years(self) -> None:
        self.year_cb.configure(values=[str(y) for y in self.state.available_years()])

    # ----- handlers ----------------------------------------------------

    def _on_year_change(self, _e=None) -> None:
        try:
            y = int(self.year_var.get())
        except ValueError:
            return
        self.state.set_year(y)

    def _on_company_change(self, _e=None) -> None:
        self.state.set_company(self.company_var.get())
