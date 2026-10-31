"""
Sandbox Notes
Author: Florian Dheer
Description: Per-version notes for the Sandbox browser. A note is a plain
text file next to its document, named like the document with a ``.note.txt``
suffix instead of its extension (``report_v002.docx`` -> ``report_v002.note.txt``).
Each version has its own note, so a note describes that version and stays
with it in Explorer. Pure filesystem helpers, no UI.
"""

import os
from typing import Iterable, List

NOTE_SUFFIX = ".note.txt"


def note_path(doc_path: str) -> str:
    """The note file that belongs to ``doc_path`` (it may not exist yet)."""
    return os.path.splitext(doc_path)[0] + NOTE_SUFFIX


def is_note_file(path: str) -> bool:
    return os.path.basename(path).lower().endswith(NOTE_SUFFIX)


def has_note(doc_path: str) -> bool:
    try:
        return os.path.getsize(note_path(doc_path)) > 0
    except OSError:
        return False


def read_note(doc_path: str) -> str:
    try:
        with open(note_path(doc_path), "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def write_note(doc_path: str, text: str) -> bool:
    """Save the note, or remove the file when the note was emptied.

    Returns True when a note file exists afterwards.
    """
    target = note_path(doc_path)
    text = text.rstrip()
    if not text:
        try:
            os.remove(target)
        except FileNotFoundError:
            pass
        return False
    with open(target, "w", encoding="utf-8", newline="\n") as f:
        f.write(text + "\n")
    return True


def hide_notes(file_paths: Iterable[str]) -> List[str]:
    """``file_paths`` without the note files whose document is in the list.

    A note whose document is gone is kept, so it can still be found instead
    of lingering invisibly.
    """
    paths = list(file_paths)
    documents = {
        (os.path.normcase(os.path.dirname(p)), os.path.splitext(os.path.basename(p))[0].lower())
        for p in paths if not is_note_file(p)
    }
    visible = []
    for p in paths:
        if is_note_file(p):
            stem = os.path.basename(p)[:-len(NOTE_SUFFIX)].lower()
            if (os.path.normcase(os.path.dirname(p)), stem) in documents:
                continue
        visible.append(p)
    return visible
