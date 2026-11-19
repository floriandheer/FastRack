"""Single-letter form shortcuts (P, S, O, D, J) must not eat keystrokes typed into text fields."""

import sys
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

from shared_form_keyboard import FormKeyboardMixin  # noqa: E402


class _Form(FormKeyboardMixin):
    def __init__(self, parent):
        self.parent = parent
        self._in_text_field = False
        self._personal_var = tk.BooleanVar(master=parent, value=False)


def _event(widget):
    return SimpleNamespace(widget=widget)


def test_entry_nested_in_a_frame_counts_as_text_field(tk_window):
    # The client field is an AutocompleteEntry: a Frame wrapping the real Entry.
    wrapper = tk.Frame(tk_window)
    entry = tk.Entry(wrapper)
    form = _Form(tk_window)
    assert form._is_typing_in_text_field(_event(entry))
    assert form._on_p_key(_event(entry)) is None  # key passes through to the entry
    assert form._personal_var.get() is False


def test_shortcut_still_fires_outside_text_fields(tk_window):
    form = _Form(tk_window)
    check = tk.Checkbutton(tk_window)
    assert not form._is_typing_in_text_field(_event(check))
    assert form._on_p_key(_event(check)) == "break"
    assert form._personal_var.get() is True


def test_physical_letters_pass_through_while_typing(tk_window):
    from folder_structure_extensions import physical

    form = _Form(tk_window)
    ext = physical.PhysicalExtension.__new__(physical.PhysicalExtension)
    ext.creator = form
    toggled = []
    ext._toggle_type = toggled.append
    entry = tk.Entry(tk.Frame(tk_window))
    for handler in (ext._on_o, ext._on_d, ext._on_j):
        assert handler(_event(entry)) is None
    assert toggled == []
