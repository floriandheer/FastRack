"""Lock for the FastRack hub's out/in switch on the Traktor sync tools.

Each tool saves `last_mode` ("export" / "import"), which the hub's switch edits
and its one-click button replays. With `mode_locked` set in the tool's settings,
nothing may change `last_mode` - not the hub's switch, and not running the
other mode from inside the tool's window.
"""

from typing import Any, Dict


def filter_mode_change(settings: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    """`updates` without any `last_mode` change when the mode is (or is being) locked."""
    locked = updates.get("mode_locked", getattr(settings, "mode_locked", False))
    if locked and "last_mode" in updates:
        return {k: v for k, v in updates.items() if k != "last_mode"}
    return updates


# ----------------------------------------------------------------------
# Header widgets (shared by the three tools' dark title bars)
# ----------------------------------------------------------------------

HEADER_BG = "#2c3e50"


def make_header_title(header, title: str, bg: str = HEADER_BG):
    """A centered title in the header; returns the box the lock checkbox goes next to."""
    import tkinter as tk
    box = tk.Frame(header, bg=bg)
    box.place(relx=0.5, rely=0.5, anchor=tk.CENTER)
    tk.Label(box, text=title, font=("Arial", 16, "bold"), fg="white", bg=bg).pack(side=tk.LEFT)
    return box


def add_header_lock(box, config_manager, bg: str = HEADER_BG):
    """Lock checkbox beside the title; saves to the tool's settings as soon as it is toggled.
    Returns the BooleanVar."""
    import tkinter as tk
    var = tk.BooleanVar(value=bool(getattr(config_manager.settings, "mode_locked", False)))
    tk.Checkbutton(
        box, text="\U0001F512 lock out/in", variable=var, font=("Arial", 9),
        command=lambda: config_manager.update_settings(mode_locked=var.get()),
        fg="#ecf0f1", bg=bg, selectcolor="#34495e", activebackground=bg, activeforeground="white",
        highlightthickness=0, bd=0, cursor="hand2",
    ).pack(side=tk.LEFT, padx=(18, 0), pady=(4, 0))
    return var
