"""Readable pop-up dialogs, in place of tkinter.messagebox.

A heading that says what this is about, a plain sentence of explanation, an optional table of counts, an
optional scrollable list (e.g. exactly what is about to change), technical details in a selectable block, and
buttons named for what they do ("Apply 7 fixes" rather than "Yes").
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable, Iterable, Optional, Sequence, Tuple

from shared_color_button import ColorButton

KIND_STYLE = {                       # accent colour, glyph
    "info": ("#2980b9", "i"),
    "question": ("#2980b9", "?"),
    "success": ("#27ae60", "✓"),
    "warning": ("#e67e22", "!"),
    "error": ("#c0392b", "×"),
}
HEADING_FONT = ("Segoe UI", 13, "bold")
BODY_FONT = ("Segoe UI", 10)
SMALL_FONT = ("Segoe UI", 9)
MONO_FONT = ("Consolas", 9)

# (text, style) lines for the list: "head" = bold group title, "line" = normal, "note" = grey
Item = Tuple[str, str]


class Dialog(tk.Toplevel):
    """Modal dialog. After wait_window(), `.result` holds the id of the button pressed (or the cancel id)."""

    def __init__(self, parent, title: str, heading: str, *, text: str = "",
                 facts: Sequence[Tuple[str, str]] = (), items: Sequence[Item] = (), items_title: str = "",
                 detail: str = "", footnote: str = "", kind: str = "info",
                 buttons: Sequence[Tuple[str, str]] = (("OK", "ok"),), default: Optional[str] = None,
                 cancel: Optional[str] = None, actions: Sequence[Tuple[str, Callable[[], None]]] = (),
                 width: int = 640):
        super().__init__(parent)
        self.result: Optional[str] = cancel if cancel is not None else (buttons[-1][1] if buttons else None)
        self._default = default if default is not None else (buttons[0][1] if buttons else None)
        self._cancel = self.result
        accent, glyph = KIND_STYLE.get(kind, KIND_STYLE["info"])

        self.title(title)
        self.resizable(True, True)
        self.minsize(width, 0)
        wrap = width - 130

        outer = ttk.Frame(self, padding=(18, 16, 18, 14))
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        badge = tk.Canvas(outer, width=46, height=46, highlightthickness=0)
        badge.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 14))
        badge.create_oval(2, 2, 44, 44, fill=accent, outline=accent)
        badge.create_text(23, 23, text=glyph, fill="white", font=("Segoe UI", 18, "bold"))

        body = ttk.Frame(outer)
        body.grid(row=0, column=1, rowspan=2, sticky="nsew")
        body.columnconfigure(0, weight=1)
        row = 0
        ttk.Label(body, text=heading, font=HEADING_FONT, wraplength=wrap, justify="left").grid(
            row=row, column=0, sticky="w")
        row += 1
        if text:
            ttk.Label(body, text=text, font=BODY_FONT, wraplength=wrap, justify="left").grid(
                row=row, column=0, sticky="w", pady=(8, 0))
            row += 1

        if facts:
            table = ttk.Frame(body)
            table.grid(row=row, column=0, sticky="ew", pady=(12, 0))
            table.columnconfigure(0, weight=1)
            for n, (label, value) in enumerate(facts):
                ttk.Separator(table).grid(row=2 * n, column=0, columnspan=2, sticky="ew")
                ttk.Label(table, text=label, font=BODY_FONT, wraplength=wrap - 80, justify="left").grid(
                    row=2 * n + 1, column=0, sticky="w", pady=5)
                ttk.Label(table, text=value, font=("Segoe UI", 10, "bold")).grid(
                    row=2 * n + 1, column=1, sticky="e", padx=(16, 4))
            ttk.Separator(table).grid(row=2 * len(facts), column=0, columnspan=2, sticky="ew")
            row += 1

        if items:
            if items_title:
                ttk.Label(body, text=items_title, font=("Segoe UI", 10, "bold")).grid(
                    row=row, column=0, sticky="w", pady=(14, 3))
                row += 1
            box = ttk.Frame(body)
            box.grid(row=row, column=0, sticky="nsew")
            box.columnconfigure(0, weight=1)
            body.rowconfigure(row, weight=1)
            view = tk.Text(box, wrap="word", height=min(max(len(items), 3), 12), font=SMALL_FONT, relief=tk.FLAT,
                           borderwidth=1, highlightthickness=1, highlightbackground="#c8ccd0", padx=8, pady=6,
                           cursor="arrow")
            view.tag_configure("head", font=("Segoe UI", 9, "bold"), spacing1=6)
            view.tag_configure("line", lmargin1=12, lmargin2=12)
            view.tag_configure("note", foreground="#6b7280", lmargin1=12, lmargin2=12)
            for text_line, style in items:
                view.insert(tk.END, text_line + "\n", style if style in ("head", "line", "note") else "line")
            view.configure(state=tk.DISABLED)
            ys = ttk.Scrollbar(box, orient="vertical", command=view.yview)
            view.configure(yscrollcommand=ys.set)
            view.grid(row=0, column=0, sticky="nsew")
            ys.grid(row=0, column=1, sticky="ns")
            row += 1

        if detail:
            block = tk.Text(body, wrap="word", height=min(detail.count("\n") + 2, 6), font=MONO_FONT, relief=tk.FLAT,
                            highlightthickness=1, highlightbackground="#c8ccd0", background="#f5f5f5", padx=8, pady=6)
            block.insert("1.0", detail)
            block.configure(state=tk.DISABLED)          # still selectable and copyable
            block.grid(row=row, column=0, sticky="ew", pady=(12, 0))
            row += 1

        if footnote:
            ttk.Label(body, text=footnote, font=SMALL_FONT, foreground="#6b7280", wraplength=wrap,
                      justify="left").grid(row=row, column=0, sticky="w", pady=(12, 0))
            row += 1

        bar = ttk.Frame(outer)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        bar.columnconfigure(1, weight=1)
        left = ttk.Frame(bar)
        left.grid(row=0, column=0, sticky="w")
        for label, callback in actions:
            ttk.Button(left, text=label, command=callback).pack(side=tk.LEFT, padx=(0, 8))
        right = ttk.Frame(bar)
        right.grid(row=0, column=1, sticky="e")
        self._buttons = {}
        for label, button_id in buttons:
            if button_id == self._default:
                button = ColorButton(right, text=label, command=lambda b=button_id: self.press(b), bg=accent,
                                     fg="white", font=("Segoe UI", 10, "bold"), padx=16, pady=5)
            else:
                button = ttk.Button(right, text=label, command=lambda b=button_id: self.press(b))
            button.pack(side=tk.LEFT, padx=(8, 0))
            self._buttons[button_id] = button

        self.bind("<Return>", self.on_enter)
        self.bind("<KP_Enter>", self.on_enter)
        self.bind("<Escape>", self.on_escape)
        self.protocol("WM_DELETE_WINDOW", self.on_escape)

        self.update_idletasks()
        self._center_on(parent)
        try:
            self.transient(parent)
        except tk.TclError:
            pass
        self.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def on_enter(self, _event=None) -> None:
        self.press(self._default)

    def on_escape(self, _event=None) -> None:
        self.press(self._cancel)

    def press(self, button_id: Optional[str]) -> None:
        if button_id is not None:
            self.result = button_id
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _center_on(self, parent) -> None:
        width, height = self.winfo_reqwidth(), self.winfo_reqheight()
        try:
            px, py = parent.winfo_rootx(), parent.winfo_rooty()
            pw, ph = parent.winfo_width(), parent.winfo_height()
            x, y = px + max((pw - width) // 2, 0), py + max((ph - height) // 3, 0)
        except tk.TclError:
            x = y = 100
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")


def show_dialog(parent, title: str, heading: str, **options) -> Optional[str]:
    """Show a Dialog and wait for it; returns the id of the button pressed."""
    dialog = Dialog(parent, title, heading, **options)
    parent.wait_window(dialog)
    return dialog.result


def show_error(parent, title: str, heading: str, text: str = "", **options) -> None:
    show_dialog(parent, title, heading, text=text, kind="error", **options)


def show_warning(parent, title: str, heading: str, text: str = "", **options) -> None:
    show_dialog(parent, title, heading, text=text, kind="warning", **options)


def show_info(parent, title: str, heading: str, text: str = "", **options) -> None:
    show_dialog(parent, title, heading, text=text, kind=options.pop("kind", "info"), **options)
