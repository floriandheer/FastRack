#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_FormatGenres.py
Description: Normalize messy genre strings on the clipboard to a `; `-separated
             format. One-shot button or live clipboard watching while the window
             is open — designed to live next to a music player for quick tagging.
Author: Florian Dheer
Version: 1.0.0
"""
from __future__ import annotations

import re
import sys
import tkinter as tk
from typing import Optional

from shared_window_icon import apply_category_icon

APP_NAME = "Format Genres"
APP_VERSION = "1.0.0"

# Split on comma, slash, pipe, or semicolon — with any surrounding whitespace.
SPLIT_RE = re.compile(r"\s*[,/|;]\s*")
# Collapse internal whitespace runs (e.g. "Deep   Tech" -> "Deep Tech").
WS_RE = re.compile(r"\s+")

POLL_INTERVAL_MS = 250

# Mini-theme (kept self-contained so the popup matches the Audio category).
BG = "#1c2128"
BG_DARKER = "#161b22"
FG = "#f0f6fc"
FG_MUTED = "#8b949e"
ACCENT = "#9333ea"          # Audio category color
ACCENT_HOVER = "#a855f7"
SUCCESS = "#3fb950"
BORDER = "#30363d"


def normalize(text: str) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for raw in SPLIT_RE.split(text):
        cleaned = WS_RE.sub(" ", raw).strip()
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key in seen:
            continue
        seen.add(key)
        parts.append(cleaned)
    return "; ".join(parts)


class FormatGenresApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.geometry("440x340")
        self.root.minsize(380, 300)

        self.auto_var = tk.BooleanVar(value=True)
        self.topmost_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="Ready")

        # Tracks the clipboard contents we last touched so the auto-watcher
        # ignores its own writes and doesn't re-format clean text on every poll.
        self._last_clipboard: Optional[str] = None

        self._build_ui()
        self._apply_topmost()
        self._poll_clipboard()

    # ---------- UI ----------

    def _build_ui(self) -> None:
        outer = tk.Frame(self.root, bg=BG, padx=14, pady=12)
        outer.pack(fill=tk.BOTH, expand=True)

        title = tk.Label(
            outer, text="🎵  Format Genres",
            bg=BG, fg=FG, font=("Segoe UI", 13, "bold"),
        )
        title.pack(anchor="w")

        hint = tk.Label(
            outer,
            text="Splits on , / | ;  →  joins with ;  →  dedupes case-insensitively.",
            bg=BG, fg=FG_MUTED, font=("Segoe UI", 9),
        )
        hint.pack(anchor="w", pady=(0, 10))

        btn = tk.Button(
            outer, text="Format Clipboard Now",
            bg=ACCENT, fg="#ffffff",
            activebackground=ACCENT_HOVER, activeforeground="#ffffff",
            font=("Segoe UI", 11, "bold"),
            relief="flat", bd=0, padx=12, pady=10,
            cursor="hand2", command=self._format_now,
        )
        btn.pack(fill=tk.X, pady=(0, 10))

        auto = tk.Checkbutton(
            outer, text="Auto-format clipboard while window is open",
            variable=self.auto_var,
            bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
            selectcolor=BG_DARKER, font=("Segoe UI", 9),
        )
        auto.pack(anchor="w")

        topmost = tk.Checkbutton(
            outer, text="Keep window on top",
            variable=self.topmost_var,
            bg=BG, fg=FG, activebackground=BG, activeforeground=FG,
            selectcolor=BG_DARKER, font=("Segoe UI", 9),
            command=self._apply_topmost,
        )
        topmost.pack(anchor="w", pady=(0, 10))

        tk.Label(
            outer, text="Last formatted:",
            bg=BG, fg=FG_MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w")

        self.preview = tk.Text(
            outer, height=4, wrap=tk.WORD,
            bg=BG_DARKER, fg=FG, insertbackground=FG,
            relief="flat", bd=0, highlightthickness=1,
            highlightbackground=BORDER, highlightcolor=BORDER,
            font=("Consolas", 10), padx=8, pady=6,
        )
        self.preview.pack(fill=tk.BOTH, expand=True, pady=(2, 8))
        self._set_preview("")

        tk.Label(
            outer, textvariable=self.status_var,
            bg=BG, fg=FG_MUTED, font=("Segoe UI", 9), anchor="w",
        ).pack(fill=tk.X)

    def _apply_topmost(self) -> None:
        try:
            self.root.attributes("-topmost", bool(self.topmost_var.get()))
        except tk.TclError:
            pass

    def _set_preview(self, text: str) -> None:
        self.preview.configure(state=tk.NORMAL)
        self.preview.delete("1.0", tk.END)
        if text:
            self.preview.insert("1.0", text)
        self.preview.configure(state=tk.DISABLED)

    def _set_status(self, msg: str, ok: bool = False) -> None:
        self.status_var.set(msg)

    # ---------- clipboard ----------

    def _read_clipboard(self) -> Optional[str]:
        try:
            return self.root.clipboard_get()
        except tk.TclError:
            return None

    def _write_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        # Force the clipboard contents to survive after this app exits / loses focus.
        self.root.update()

    def _format_now(self) -> None:
        current = self._read_clipboard()
        if current is None:
            self._set_status("Clipboard is empty or not text")
            return
        formatted = normalize(current)
        if not formatted:
            self._set_status("Nothing to format")
            return
        if formatted == current:
            self._set_status("Clipboard already formatted ✓")
        else:
            self._write_clipboard(formatted)
            self._set_status("Formatted ✓")
        self._last_clipboard = formatted
        self._set_preview(formatted)

    def _poll_clipboard(self) -> None:
        try:
            if self.auto_var.get():
                current = self._read_clipboard()
                # Only act when the clipboard changed since we last looked AND
                # contains a recognized separator — otherwise we'd clobber any
                # plain text the user copied for an unrelated reason.
                if (
                    current is not None
                    and current != self._last_clipboard
                    and SPLIT_RE.search(current)
                ):
                    formatted = normalize(current)
                    if formatted and formatted != current:
                        self._write_clipboard(formatted)
                        self._set_preview(formatted)
                        self._set_status("Auto-formatted ✓")
                        self._last_clipboard = formatted
                    else:
                        self._last_clipboard = current
                elif current is not None:
                    self._last_clipboard = current
        finally:
            self.root.after(POLL_INTERVAL_MS, self._poll_clipboard)


def main() -> int:
    root = tk.Tk()
    apply_category_icon(root)
    FormatGenresApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
