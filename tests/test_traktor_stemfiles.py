"""Tests for shared_traktor_stemfiles (finding and clearing stem files) and the link audit it reports on."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_traktor_stemfiles as files  # noqa: E402
import shared_traktor_stems as stems  # noqa: E402


def make(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def test_find_only_returns_stem_files_of_the_exact_shape(tmp_path):
    store, temp = tmp_path / "Stems", tmp_path / "Temp"
    wanted = [make(store / "012" / "A.stem.mp4", 5), make(store / "013" / "B.stem.mp4", 7), make(temp / "Song.stem.mp4", 3)]
    make(store / "012" / "notes.txt")                              # not a stem file
    make(store / "abc" / "C.stem.mp4")                             # folder is not numbered
    make(store / "D.stem.mp4")                                     # wrong depth for a store
    make(store / "015" / "deeper" / "F.stem.mp4")                  # too deep
    make(temp / "sub" / "Y.stem.mp4")                              # too deep for the temp folder
    outside = make(tmp_path / "elsewhere" / "real.stem.mp4")
    os.symlink(outside, store / "014" / "link.stem.mp4") if (store / "014").mkdir() is None else None
    os.symlink(tmp_path / "elsewhere", store / "016")              # a symlinked 'numbered' folder

    found = files.find_stem_files([(str(store), 2), (str(temp), 1), (str(store), 2)])   # a repeated root counts once
    assert [f.path for f in found] == sorted(str(p) for p in wanted)
    assert sum(f.size for f in found) == 15


def test_default_roots_use_the_usual_places_and_whatever_the_log_says(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    (tmp_path / "Music" / "Traktor" / "Stems").mkdir(parents=True)
    (tmp_path / "Library" / "Application Support" / "Native Instruments" / "Traktor" / "StemFilesTempDir").mkdir(parents=True)
    custom = tmp_path / "Disk" / "MyStems"
    custom.mkdir(parents=True)
    docs = tmp_path / "Native Instruments"
    log = docs / "Traktor 4.5.1" / "Logs" / "Traktor.log"
    log.parent.mkdir(parents=True)
    log.write_text(f'2026-02-26 22:33:58 - Finished stem separation job for track "T" - result: successful: stem file '
                   f'stored to "{custom}/013/N2X3DIDILQZLBBRIPWSQA0O4ONMA.stem.mp4", with offset 0\n'
                   f'... stored to "{tmp_path}/Gone/Stems/013/ID.stem.mp4"\n', encoding="utf-8")

    roots = files.default_stem_roots(str(docs))
    assert (str(tmp_path / "Music" / "Traktor" / "Stems"), 2) in roots
    assert (str(custom), 2) in roots
    assert any(depth == 1 and path.endswith("StemFilesTempDir") for path, depth in roots)
    assert not any("Gone" in path for path, _ in roots)           # folders that do not exist are left out


def test_permanent_delete_removes_only_the_listed_stem_files_and_empty_numbered_folders(tmp_path):
    store = tmp_path / "Stems"
    a, b = make(store / "001" / "A.stem.mp4", 4), make(store / "002" / "B.stem.mp4", 6)
    keep_other = make(store / "002" / "keep.txt")                  # folder 002 is not empty afterwards
    result = files.delete_stem_files(files.find_stem_files([(str(store), 2)]), permanent=True)
    assert (result.removed, result.size, result.permanent, result.failed) == (2, 10, True, [])
    assert not a.exists() and not b.exists()
    assert not (store / "001").exists()                            # emptied -> removed
    assert keep_other.exists() and (store / "002").exists()        # not empty -> left alone
    assert store.exists()


def test_delete_never_touches_anything_that_is_not_a_plain_stem_file(tmp_path):
    victim = make(tmp_path / "important.mp4")
    linked = tmp_path / "link.stem.mp4"
    os.symlink(victim, linked)
    result = files.delete_stem_files([files.StemFile(str(victim), 10), files.StemFile(str(linked), 10),
                                      files.StemFile(str(tmp_path / "missing.stem.mp4"), 0)], permanent=True)
    assert result.removed == 0 and victim.exists() and linked.is_symlink()


def test_a_file_that_cannot_be_deleted_is_reported_and_the_rest_still_go(tmp_path, monkeypatch):
    store = tmp_path / "Stems"
    bad, good = make(store / "001" / "A.stem.mp4"), make(store / "001" / "B.stem.mp4")
    real_remove = os.remove

    def remove(path):
        if path == str(bad):
            raise PermissionError("locked")
        real_remove(path)

    monkeypatch.setattr(files.os, "remove", remove)
    lines = []
    result = files.delete_stem_files(files.find_stem_files([(str(store), 2)]), permanent=True, log=lines.append)
    assert result.removed == 1 and [p for p, _ in result.failed] == [str(bad)] and not good.exists() and bad.exists()
    assert any("locked" in line for line in lines)


def test_trash_mode_hands_the_paths_to_the_trash_function(tmp_path, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "darwin")
    store, bin_ = tmp_path / "Stems", tmp_path / "Bin"
    bin_.mkdir()
    make(store / "001" / "A.stem.mp4", 4)
    make(store / "002" / "B.stem.mp4", 6)
    moved = []

    def fake_trash(paths):
        for p in paths:
            moved.append(p)
            os.replace(p, bin_ / os.path.basename(p))

    result = files.delete_stem_files(files.find_stem_files([(str(store), 2)]), permanent=False, trash=fake_trash)
    assert (result.removed, result.size, result.permanent) == (2, 10, False)
    assert len(moved) == 2 and sorted(os.listdir(bin_)) == ["A.stem.mp4", "B.stem.mp4"]
    assert not (store / "001").exists() and not (store / "002").exists()


def test_trash_is_refused_where_it_is_not_set_up(tmp_path, monkeypatch):
    monkeypatch.setattr(files.sys, "platform", "win32")
    with pytest.raises(files.CleanupError, match="permanent"):
        files.delete_stem_files([files.StemFile(str(make(tmp_path / "A.stem.mp4")), 10)], permanent=False)


def test_finder_trash_batches_quotes_and_explains_a_blocked_finder():
    scripts = []

    def run(cmd, **_kw):
        scripts.append(cmd[-1])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    paths = [f'/Users/me/Stems/{n:03d}/Say "Hi" \\ {n}.stem.mp4' for n in range(5)]
    files.trash_with_finder(paths, chunk=2, run=run)
    assert len(scripts) == 3 and scripts[0].startswith('tell application "Finder" to delete {POSIX file "')
    assert 'Say \\"Hi\\" \\\\ 0.stem.mp4' in scripts[0]

    def blocked(cmd, **_kw):
        return subprocess.CompletedProcess(cmd, 1, "", "execution error: Not authorized to send Apple events to Finder. (-1743)")

    with pytest.raises(files.CleanupError, match="Automation"):
        files.trash_with_finder(paths, run=blocked)


NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="4">
<ENTRY TITLE="Linked Fine" ARTIST="A"><LOCATION DIR="/:M/:" FILE="a.flac" VOLUME="V"></LOCATION><INFO FLAGS="76" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Linked Broken" ARTIST="B"><LOCATION DIR="/:M/:" FILE="b.flac" VOLUME="V"></LOCATION><INFO FLAGS="76" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Not Linked" ARTIST="C"><LOCATION DIR="/:M/:" FILE="c.flac" VOLUME="V"></LOCATION><INFO FLAGS="12" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Twin" ARTIST="D"><LOCATION DIR="/:M/:" FILE="d1.flac" VOLUME="V"></LOCATION><INFO FLAGS="76" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Twin" ARTIST="E"><LOCATION DIR="/:M/:" FILE="d2.flac" VOLUME="V"></LOCATION><INFO FLAGS="12" FILESIZE="1"></INFO></ENTRY>
</COLLECTION></NML>
"""
STEMS = {"Linked Fine": ["/s/1.stem.mp4"], "Not Linked": ["/s/2.stem.mp4"], "Twin": ["/s/3.stem.mp4"]}


def test_audit_tells_working_links_from_broken_ones():
    audit = stems.audit_links(NML.encode(), STEMS)
    assert [t.title for t in audit.linked] == ["Linked Fine", "Linked Broken", "Twin"]
    assert [t.title for t in audit.broken] == ["Linked Broken"]            # linked, no stem file known
    assert [t.title for t in audit.unsure] == ["Twin"]                     # title shared: can't tell
    assert [t.title for t in audit.files_not_linked] == ["Not Linked"]


def test_remove_only_the_broken_links(tmp_path):
    nml = tmp_path / "collection.nml"
    nml.write_text(NML, encoding="utf-8")

    preview = stems.clean_collection(str(nml), apply=False, only_missing_in=STEMS)
    assert (preview.tracks, preview.flagged, preview.applied) == (5, 1, False)

    done = stems.clean_collection(str(nml), apply=True, only_missing_in=STEMS)
    assert done.applied and done.flagged == 1 and Path(done.backup_path).read_text(encoding="utf-8") == NML
    flags = {t.title + t.file: t.flags for t in stems.read_tracks(nml.read_bytes())}
    assert flags["Linked Brokenb.flac"] == 12                              # cleared
    assert flags["Linked Finea.flac"] == 76 and flags["Twind1.flac"] == 76  # working / undecidable links stay

    everything = stems.clean_collection(str(nml), apply=True)               # no filter: the old behaviour
    assert everything.flagged == 2 and stems.scan_stem_flags(nml.read_bytes()).flagged == 0


def test_collection_status_reports_links_files_and_size(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    docs = tmp_path / "Native Instruments"
    nml = docs / "Traktor 4.5.1" / "collection.nml"
    nml.parent.mkdir(parents=True)
    nml.write_text(NML, encoding="utf-8")
    one = make(tmp_path / "Music" / "Traktor" / "Stems" / "001" / "A.stem.mp4", 2048)
    log = docs / "Traktor 4.5.1" / "Logs" / "Traktor.log"
    log.parent.mkdir()
    log.write_text(f'2026-02-26 22:33:58 - Finished stem separation job for track "Linked Fine" - result: successful: '
                   f'stem file stored to "{one}", with offset 0µs, elapsed time: 2m 22s\n', encoding="utf-8")

    status = files.collection_status(str(nml))
    assert (status.tracks, status.linked, status.files, status.size) == (5, 3, 1, 2048)
    assert "5 tracks" in status.headline and "3 linked" in status.headline and "2.0 KB" in status.headline
    assert "Linked, but no stem file found: 2" in status.detail            # 'Linked Broken' and 'Twin' (not in the log)
