"""Modal dialog helpers that anchor a messagebox to a widget's toplevel.

Use ``ask_yes_no(parent, ...)`` / ``show_info(parent, ...)`` /
``show_error(parent, ...)`` anywhere we would have called
``messagebox.askyesno`` etc. ``parent`` can be any widget; we resolve it
to its toplevel internally.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox


def ask_yes_no(parent: tk.Misc, title: str, message: str) -> bool:
    return messagebox.askyesno(title, message, parent=parent.winfo_toplevel())


def show_info(parent: tk.Misc, title: str, message: str) -> None:
    messagebox.showinfo(title, message, parent=parent.winfo_toplevel())


def show_error(parent: tk.Misc, title: str, message: str) -> None:
    messagebox.showerror(title, message, parent=parent.winfo_toplevel())
