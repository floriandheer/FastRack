"""The readable pop-ups used by the MusicBee cleanup tool."""

import sys
import tkinter as tk
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_dialogs as dialogs  # noqa: E402


@pytest.fixture
def root(tk_window):
    return tk_window


def _run(root, press, **options):
    """Open a dialog, let `press(dialog)` act on it, return the dialog's result."""
    dialog = dialogs.Dialog(root, "Test", "Heading", **options)
    dialog.after(30, lambda: press(dialog))
    root.wait_window(dialog)
    return dialog.result


CONFIRM = dict(buttons=(("Cancel", "cancel"), ("Apply 2 fixes", "apply")), default="apply", cancel="cancel")


def test_button_press_returns_its_id(root):
    assert _run(root, lambda d: d.press("apply"), **CONFIRM) == "apply"
    assert _run(root, lambda d: d.press("cancel"), **CONFIRM) == "cancel"


def test_enter_confirms_and_escape_or_closing_cancels(root):
    assert _run(root, lambda d: d.on_enter(), **CONFIRM) == "apply"
    assert _run(root, lambda d: d.on_escape(), **CONFIRM) == "cancel"     # Escape and the window's close button
    d = dialogs.Dialog(root, "t", "h", **CONFIRM)
    assert d.bind("<Return>") and d.bind("<KP_Enter>") and d.bind("<Escape>")
    d.destroy()


def test_default_ok_dialog_returns_ok(root):
    assert _run(root, lambda d: d.press("ok")) == "ok"
    assert _run(root, lambda d: d.on_escape()) == "ok"                     # single OK button: Escape also closes


def test_all_parts_render_and_the_list_and_detail_hold_their_text(root):
    items = [("Deep House   (2 changes)", "head"), ("Remove this copy of A - B:  C:\a.flac", "line"),
             ("note", "note")]
    seen = {}

    def inspect(d):
        texts = [w for w in _walk(d) if isinstance(w, tk.Text)]
        seen["texts"] = [t.get("1.0", "end").strip() for t in texts]
        seen["labels"] = [w.cget("text") for w in _walk(d) if w.winfo_class() in ("TLabel",)]
        d.press("close")

    _run(root, inspect, kind="success", text="Body sentence.", facts=[("Replaced", "2"), ("Removed", "1")],
         items=items, items_title="What will change", detail="Traceback line", footnote="Backups: C:\b",
         actions=(("Open", lambda: None),), buttons=(("Close", "close"),))
    assert any("Remove this copy of A - B" in t for t in seen["texts"]) and "Traceback line" in seen["texts"]
    assert {"Heading", "Body sentence.", "Replaced", "2", "What will change", "Backups: C:\b"} <= set(seen["labels"])


def _walk(widget):
    for child in widget.winfo_children():
        yield child
        yield from _walk(child)
