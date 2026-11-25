"""Audiobook discovery and selection for the Sync to PowerAmp tool."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

import PipelineScript_Audio_PowerAmpSync as pa  # noqa: E402


def _touch(path, size=10):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "Audiobooks"
    _touch(root / "Cormac McCarthy" / "The Road" / "01.mp3")
    _touch(root / "Cormac McCarthy" / "The Road" / "02.mp3")
    _touch(root / "Cormac McCarthy" / "The Road" / "cover.jpg")
    _touch(root / "Cormac McCarthy" / "author.jpg")                       # not a book
    _touch(root / "Homer" / "The Odyssey" / "CD1" / "01.mp3")
    _touch(root / "Homer" / "The Odyssey" / "CD2" / "01.mp3")
    _touch(root / "Series" / "Saga" / "Book 1" / "book1.m4b")              # nested series: one book per folder
    _touch(root / "Series" / "Saga" / "Book 2" / "book2.m4b")
    _touch(root / "Loose Book.m4b")
    return root


def test_find_audiobooks_one_entry_per_book(library):
    books = {b.rel: b for b in pa.find_audiobooks(str(library))}
    assert sorted(books) == ["Cormac McCarthy/The Road", "Homer/The Odyssey", "Loose Book.m4b",
                             "Series/Saga/Book 1", "Series/Saga/Book 2"]
    road = books["Cormac McCarthy/The Road"]
    assert road.author == "Cormac McCarthy" and road.title == "The Road"
    assert len(road.files) == 3                                           # cover art comes along
    assert len(books["Homer/The Odyssey"].files) == 2                     # discs stay one book
    assert books["Loose Book.m4b"].author == ""


def test_audiobook_owns_only_its_own_files(library):
    road = next(b for b in pa.find_audiobooks(str(library)) if b.title == "The Road")
    assert road.owns("Cormac McCarthy/The Road/01.mp3")
    assert not road.owns("Cormac McCarthy/The Road 2/01.mp3")
    assert not road.owns("Cormac McCarthy/author.jpg")


def test_find_audiobooks_missing_root():
    assert pa.find_audiobooks("") == []
    assert pa.find_audiobooks(r"Z:\does\not\exist") == []


def test_old_settings_without_new_fields_still_load():
    s = pa.SyncSettings.from_dict({"extras_mirror": True, "audiobooks_source_path": "M:\\Audiobooks"})
    assert s.audiobooks_selected is None and s.audiobooks_mirror is None and s.extras_mirror is True


# ------------------------------------------------------------------ window

@pytest.fixture
def app(tmp_path, library, monkeypatch, tk_window):
    config = tmp_path / "config.json"                                     # never the real settings file
    monkeypatch.setattr(pa.ConfigManager.__init__, "__defaults__", (str(config),))
    monkeypatch.setattr(pa.PowerAmpSyncApp, "_find_adb", lambda self: None)
    a = pa.PowerAmpSyncApp(tk_window)
    a.audiobooks_source_var.set(str(library))
    a._refresh_audiobooks()
    return a


def test_tabs_are_split(app):
    tabs = [app.content_notebook.tab(t, "text") for t in app.content_notebook.tabs()]
    assert any(t.endswith("Audiobooks") for t in tabs)
    assert any(t.endswith("Podcasts") for t in tabs)
    assert not any("&" in t for t in tabs)


def test_all_books_ticked_until_a_choice_is_saved(app):
    assert app.config_manager.settings.audiobooks_selected is None
    assert all(app.audiobook_checked.values()) and len(app.audiobook_checked) == 5


def test_ticks_are_remembered_and_new_books_start_unticked(app, library):
    app._set_all_audiobooks(False)
    app._set_audiobook_checked("Homer/The Odyssey", True)
    assert app.config_manager.settings.audiobooks_selected == ["Homer/The Odyssey"]
    assert "1 of 5" in app.audiobook_summary_var.get()

    _touch(library / "New Author" / "New Book" / "01.mp3")
    app.audiobook_checked.clear()                                         # as after a restart
    app._refresh_audiobooks()
    assert app.audiobook_checked["Homer/The Odyssey"] is True
    assert app.audiobook_checked["New Author/New Book"] is False
    assert app.audiobook_checked["Cormac McCarthy/The Road"] is False


def test_sync_copies_only_ticked_books_and_mirror_removes_unticked(app, tmp_path, monkeypatch):
    dest = tmp_path / "phone"
    _touch(dest / "Cormac McCarthy" / "The Road" / "01.mp3")             # on the device from an earlier sync
    app.sync_target_var.set("local")
    app.audiobooks_dest_var.set(str(dest))
    app.audiobooks_mirror_var.set(True)
    app._set_all_audiobooks(False)
    app._set_audiobook_checked("Homer/The Odyssey", True)

    monkeypatch.setattr(pa.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(pa.messagebox, "showinfo", lambda *a, **k: None)
    done = []
    monkeypatch.setattr(app, "_sync_complete", lambda msg="": done.append(msg))
    started = []
    monkeypatch.setattr(pa.threading, "Thread",
                        lambda target, daemon=None: type("T", (), {"start": lambda s: started.append(target)})())
    app._start_sync_audiobooks()
    started[0]()
    app.root.update()

    assert (dest / "Homer" / "The Odyssey" / "CD1" / "01.mp3").exists()
    assert not (dest / "Cormac McCarthy").exists()                        # unticked book and its empty folders
    assert not (dest / "Loose Book.m4b").exists()
    assert done
