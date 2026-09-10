#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Audio_FormatTitles.py
Description: Fix track/album title capitalization on the clipboard to iTunes-style
             title case (e.g. "the luring of the beyond" -> "The Luring of the
             Beyond"). One-shot button or live clipboard watching while the window
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

APP_NAME = "Format Titles"
APP_VERSION = "1.0.0"

# Words that stay lowercase unless they open/close the title (or a clause
# after a colon/dash/bracket). Matches the common "iTunes style" title-case
# convention used by most music libraries.
SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "en", "for", "if", "in", "nor",
    "of", "off", "on", "or", "per", "so", "the", "to", "up", "v", "v.", "via",
    "vs", "vs.", "yet",
}

# "feat." / "ft." stay lowercase wherever they land — standard tagging convention.
FEAT_WORDS = {"feat", "feat.", "ft", "ft."}

# Small set of unambiguous music-related acronyms worth forcing uppercase.
ACRONYMS = {
    "dj": "DJ", "mc": "MC", "ep": "EP", "lp": "LP", "uk": "UK", "usa": "USA",
    "vip": "VIP", "edm": "EDM", "bpm": "BPM", "nyc": "NYC", "tv": "TV",
}

ROMAN_NUMERALS = {
    "i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x",
    "xi", "xii", "xiii", "xiv", "xv",
}

OPEN_PUNCT = set("([\"'¿¡")
FORCE_AFTER = set(":-–—")

# Leading/trailing run of non-alphanumeric chars vs. the "core" word between them.
WORD_RE = re.compile(r"^([^A-Za-z0-9]*)(.*?)([^A-Za-z0-9]*)$")

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


def _is_stylized(core: str) -> bool:
    """True for words with deliberate internal casing (iPod, McLaren, DeadMau5)
    that a plain title-case pass would otherwise mangle."""
    if len(core) < 2:
        return False
    has_upper = any(c.isupper() for c in core)
    has_lower = any(c.islower() for c in core)
    if not (has_upper and has_lower):
        return False
    if core[0].isupper() and core[1:].islower():
        return False
    return True


def _cap_segment(seg: str, force_cap: bool) -> str:
    if not seg:
        return seg
    if not force_cap and seg.lower() in SMALL_WORDS:
        return seg.lower()
    return seg[0].upper() + seg[1:].lower()


def _normal_case(core: str, force_cap: bool) -> str:
    if "-" in core:
        parts = core.split("-")
        return "-".join(
            _cap_segment(part, force_cap if idx == 0 else True)
            for idx, part in enumerate(parts)
        )
    return _cap_segment(core, force_cap)


def _smart_word(word: str, force_cap: bool) -> str:
    match = WORD_RE.match(word)
    prefix, core, suffix = match.groups() if match else ("", word, "")
    if not core:
        return word

    core_lower = core.lower()
    if core_lower in FEAT_WORDS:
        processed = core_lower
    elif core_lower in ACRONYMS:
        processed = ACRONYMS[core_lower]
    elif core.isalpha() and core_lower in ROMAN_NUMERALS:
        processed = core.upper()
    elif _is_stylized(core):
        processed = core
    else:
        processed = _normal_case(core, force_cap)
    return prefix + processed + suffix


def _process_line(line: str) -> str:
    words = [w for w in re.split(r"\s+", line.strip()) if w]
    if not words:
        return ""
    n = len(words)
    out: list[str] = []
    prev_raw: Optional[str] = None
    for idx, word in enumerate(words):
        force = idx == 0 or idx == n - 1
        if not force and word[:1] in OPEN_PUNCT:
            force = True
        if not force and prev_raw and prev_raw[-1:] in FORCE_AFTER:
            force = True
        out.append(_smart_word(word, force))
        prev_raw = word
    return " ".join(out)


def normalize(text: str) -> str:
    return "\n".join(_process_line(line) for line in text.split("\n"))


def _looks_titleish(text: str) -> bool:
    """Safety guard for the auto-watcher: only touch clipboard text that plausibly
    is a title/tracklist, so we don't rewrite URLs, paths, or code snippets copied
    for unrelated reasons while the window happens to be open."""
    if not text or len(text) > 300:
        return False
    if "://" in text or "\\" in text:
        return False
    if any(ch in text for ch in "{}<>"):
        return False
    return True


class FormatTitlesApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_NAME)
        self.root.configure(bg=BG)
        self.root.geometry("440x340")
        self.root.minsize(380, 300)

        self.auto_var = tk.BooleanVar(value=False)
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
            outer, text="\U0001f520  Format Titles",
            bg=BG, fg=FG, font=("Segoe UI", 13, "bold"),
        )
        title.pack(anchor="w")

        hint = tk.Label(
            outer,
            text="iTunes-style title case: capitalizes words, lowercases small\n"
                 "words (a, of, the…), keeps stylized casing like iPod or DJ.",
            bg=BG, fg=FG_MUTED, font=("Segoe UI", 9), justify=tk.LEFT,
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
                if (
                    current is not None
                    and current != self._last_clipboard
                    and _looks_titleish(current)
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
    FormatTitlesApp(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
