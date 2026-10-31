"""Per-version notes: sidecar naming, hiding, and the Sandbox browser's note editor."""

import sys
import tkinter as tk
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import sandbox_notes as notes  # noqa: E402


def _touch(path: Path, text: str = "x") -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_note_sits_next_to_the_document_with_a_note_suffix(tmp_path):
    doc = str(tmp_path / "report_v002.docx")
    assert Path(notes.note_path(doc)).name == "report_v002.note.txt"
    assert Path(notes.note_path(str(tmp_path / "essay.odt"))).name == "essay.note.txt"
    assert notes.is_note_file("report_v002.note.txt") and not notes.is_note_file("report.txt")


def test_write_read_and_remove_when_emptied(tmp_path):
    doc = _touch(tmp_path / "a.docx")
    assert notes.read_note(doc) == "" and not notes.has_note(doc)
    assert notes.write_note(doc, "second draft\nfix intro\n\n") is True
    assert notes.has_note(doc) and notes.read_note(doc) == "second draft\nfix intro\n"
    assert (tmp_path / "a.note.txt").exists()
    assert notes.write_note(doc, "   \n") is False
    assert not (tmp_path / "a.note.txt").exists()
    assert notes.write_note(doc, "") is False        # nothing to remove is fine


def test_each_version_has_its_own_note(tmp_path):
    v1, v2 = _touch(tmp_path / "a.docx"), _touch(tmp_path / "a_v002.docx")
    notes.write_note(v1, "first")
    notes.write_note(v2, "second")
    assert notes.read_note(v1) == "first\n" and notes.read_note(v2) == "second\n"


def test_hide_notes_keeps_documents_and_orphans(tmp_path):
    doc = _touch(tmp_path / "a.docx")
    txt = _touch(tmp_path / "plain.txt")
    own = _touch(tmp_path / "a.note.txt")
    orphan = _touch(tmp_path / "gone.note.txt")        # its document was deleted
    assert notes.hide_notes([doc, txt, own, orphan]) == [doc, txt, orphan]


def test_a_txt_document_and_its_note_do_not_get_confused(tmp_path):
    doc = _touch(tmp_path / "log.txt")
    note = _touch(tmp_path / "log.note.txt")
    assert notes.hide_notes([doc, note]) == [doc]


# ---- the browser panel ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def panel(tmp_path, monkeypatch, tk_window):
    import ui_sandbox_browser as ui
    root = tk_window
    from sandbox_tag_store import SandboxTagStore
    store_path = tmp_path / "tags.json"
    monkeypatch.setattr(ui, "SandboxTagStore", lambda: SandboxTagStore(str(store_path)))
    monkeypatch.setattr(ui, "SandboxViewSettings", lambda: _FixedSettings())
    docs = tmp_path / "sandbox"
    docs.mkdir()
    _touch(docs / "essay.odt")
    _touch(docs / "essay_v002.odt")
    p = ui.SandboxBrowserPanel(root, str(docs))
    p.pack()
    root.update()
    yield p, docs


class _FixedSettings:
    def get(self, key):
        return {"view_mode": "list", "list_scale": 100, "grid_scale": 100}.get(key, 100)

    def set(self, key, value):
        pass


def _type_note(p, text):
    p.note_text.delete("1.0", tk.END)
    p.note_text.insert("1.0", text)
    p.note_text.edit_modified(True)


def test_editing_a_note_saves_it_next_to_that_version(panel):
    p, docs = panel
    latest = str(docs / "essay_v002.odt")
    p._set_selection(latest)
    assert p.note_title_var.get() == "Note for v002"

    _type_note(p, "Rewrote the ending.")
    p._flush_note()

    assert (docs / "essay_v002.note.txt").read_text(encoding="utf-8") == "Rewrote the ending.\n"
    assert not (docs / "essay.note.txt").exists()
    assert "\u270E" in p.tree.item(p._tree_iid_for(latest), "text")


def test_switching_version_saves_the_first_and_loads_the_other(panel):
    p, docs = panel
    p._set_selection(str(docs / "essay_v002.odt"))
    _type_note(p, "about v2")

    first = [iid for iid, path in p._version_paths.items() if path.endswith("essay.odt")][0]
    p.versions_tree.selection_set(first)
    p.update()

    assert (docs / "essay_v002.note.txt").read_text(encoding="utf-8") == "about v2\n"
    assert p.note_title_var.get() == "Note for v001"
    assert p.note_text.get("1.0", "end-1c") == ""


def test_note_files_are_not_listed_as_documents(panel):
    p, docs = panel
    (docs / "essay_v002.note.txt").write_text("hello", encoding="utf-8")
    p.refresh()
    names = [p.tree.item(i, "text") for i in p.tree.get_children("")]
    assert len(names) == 1 and "essay_v002.odt" in names[0] and "note" not in names[0].lower().replace("\u270e", "")


def test_filter_finds_a_document_by_its_note_text(panel):
    p, docs = panel
    (docs / "essay.note.txt").write_text("needs a stronger conclusion", encoding="utf-8")
    assert [Path(path).name for path, _ in p._find_matches("stronger")] == ["essay_v002.odt"]


def test_version_up_starts_with_an_empty_note(panel):
    p, docs = panel
    p._set_selection(str(docs / "essay_v002.odt"))
    _type_note(p, "keep me")
    p._flush_note()

    p._version_up()

    assert (docs / "essay_v003.odt").exists() and not (docs / "essay_v003.note.txt").exists()
    assert p.note_title_var.get() == "Note for v003" and p.note_text.get("1.0", "end-1c") == ""
    assert (docs / "essay_v002.note.txt").exists()
