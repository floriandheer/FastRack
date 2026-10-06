"""Tests for shared_traktor_bundle (PC -> drive -> laptop transfer)."""

import os
from types import SimpleNamespace
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_traktor_bundle as tb  # noqa: E402

PC_XML = """<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>Music Folder</key><string>file://localhost/C:/Users/flori/Music/DJ%20Library/</string>
<key>Tracks</key><dict>
<key>1</key><dict><key>Track ID</key><integer>1</integer><key>Name</key><string>One</string>
<key>Location</key><string>file://localhost/C:/Users/flori/Music/DJ%20Library/One%20%26%20Two.flac</string></dict>
<key>2</key><dict><key>Track ID</key><integer>2</integer><key>Name</key><string>Gone</string>
<key>Location</key><string>file://localhost/C:/Users/flori/Music/DJ%20Library/Gone.flac</string></dict>
</dict></dict></plist>"""


def make_library(path, names):
    path.mkdir(parents=True, exist_ok=True)
    for n in names:
        (path / n).write_bytes(n.encode())


def locations(xml_path):
    root = ET.parse(xml_path).getroot()
    out = []
    for parent in root.iter():
        kids = list(parent)
        for i, k in enumerate(kids[:-1]):
            if k.tag == "key" and k.text in ("Location", "Music Folder"):
                out.append(kids[i + 1].text)
    return out


def test_path_to_itunes_url_per_platform():
    assert tb.path_to_itunes_url(r"D:\DJ\My Lib\a.flac", "win32") == "file://localhost/D:/DJ/My%20Lib/a.flac"
    assert tb.path_to_itunes_url("/Users/f/DJ Library/a.flac", "darwin") == "file:///Users/f/DJ%20Library/a.flac"


def test_localize_rewrites_for_mac_and_windows(tmp_path):
    src = tmp_path / "pc.xml"
    src.write_text(PC_XML, encoding="utf-8")

    mac = tmp_path / "mac.xml"
    n, missing = tb.localize_itunes_xml(str(src), str(mac), "/Users/flori/Music/DJ Library", "darwin",
                                        available={"One & Two.flac"})
    assert (n, missing) == (2, 1)
    assert locations(mac) == [
        "file:///Users/flori/Music/DJ%20Library/",
        "file:///Users/flori/Music/DJ%20Library/One%20%26%20Two.flac",
        "file:///Users/flori/Music/DJ%20Library/Gone.flac",
    ]

    win = tmp_path / "win.xml"
    tb.localize_itunes_xml(str(src), str(win), r"E:\Laptop\DJ Library", "win32")
    assert locations(win)[1] == "file://localhost/E:/Laptop/DJ%20Library/One%20%26%20Two.flac"


def test_needs_copy_detects_changes(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.write_bytes(b"123")
    assert tb.needs_copy(str(a), str(b))
    b.write_bytes(b"123")
    os.utime(b, (a.stat().st_atime, a.stat().st_mtime))
    assert not tb.needs_copy(str(a), str(b))
    b.write_bytes(b"1234")
    assert tb.needs_copy(str(a), str(b))


def test_export_then_import_round_trip(tmp_path):
    library = tmp_path / "pc" / "DJ Library"
    make_library(library, ["One & Two.flac", "Gone.flac"])
    (library / "_Removed").mkdir()
    (library / "_Removed" / "old.flac").write_bytes(b"x")
    xml = tmp_path / "pc" / "DJ Library.xml"
    xml.write_text(PC_XML, encoding="utf-8")
    playlist = tmp_path / "PlaylistSync_pc.nml"
    playlist.write_text("<NML/>")
    drive = tmp_path / "drive" / "DJ Transfer"

    messages = []
    exp = tb.export_bundle(str(library), str(xml), str(drive), messages.append,
                           playlist_file=str(playlist), machine_name="pc")
    assert exp.music.copied == 2
    assert not (drive / "DJ Library" / "_Removed").exists()
    assert tb.read_manifest(str(drive))["playlist_file"] == "PlaylistSync/PlaylistSync_pc.nml"
    assert "2 track" in tb.describe_bundle(str(drive))

    laptop_lib = tmp_path / "laptop" / "DJ Library"
    laptop_xml = tmp_path / "laptop" / "DJ Library.xml"
    imp = tb.import_bundle(str(drive), str(laptop_lib), str(laptop_xml), messages.append, platform="darwin")
    assert imp.music.copied == 2 and imp.tracks_localized == 2 and imp.tracks_missing == 0
    assert imp.playlist_file == str(drive / "PlaylistSync" / "PlaylistSync_pc.nml")
    from urllib.parse import unquote
    assert unquote(locations(laptop_xml)[1]).endswith(str(laptop_lib).replace("\\", "/") + "/One & Two.flac")

    # A second run changes nothing and backs up the previous XML.
    again = tb.import_bundle(str(drive), str(laptop_lib), str(laptop_xml), messages.append, platform="darwin")
    assert again.music.copied == 0 and again.music.skipped == 2
    assert again.xml_backup and os.path.exists(again.xml_backup)


def test_export_prune_removes_stale_drive_files(tmp_path):
    library = tmp_path / "lib"
    make_library(library, ["keep.flac", "drop.flac"])
    xml = tmp_path / "x.xml"
    xml.write_text(PC_XML, encoding="utf-8")
    drive = tmp_path / "drive"
    tb.export_bundle(str(library), str(xml), str(drive), lambda m: None)
    (library / "drop.flac").unlink()

    kept = tb.export_bundle(str(library), str(xml), str(drive), lambda m: None)
    assert kept.music.pruned == 0 and (drive / "DJ Library" / "drop.flac").exists()
    pruned = tb.export_bundle(str(library), str(xml), str(drive), lambda m: None, prune=True)
    assert pruned.music.pruned == 1 and not (drive / "DJ Library" / "drop.flac").exists()


def test_import_rejects_non_bundle(tmp_path):
    with pytest.raises(FileNotFoundError):
        tb.import_bundle(str(tmp_path), str(tmp_path / "lib"), str(tmp_path / "x.xml"), lambda m: None)


def test_accented_names_survive_unicode_form_differences(tmp_path):
    nfd = "Sira\u0304t.flac"   # as stored on the PC: a + combining macron
    nfc = "Sir\u0101t.flac"    # as a Mac may list it
    library = tmp_path / "lib"
    make_library(library, [nfd])
    xml = tmp_path / "x.xml"
    xml.write_text(PC_XML.replace("Gone.flac", nfd), encoding="utf-8")
    drive = tmp_path / "drive"
    tb.export_bundle(str(library), str(xml), str(drive), lambda m: None)

    messages = []
    imp = tb.import_bundle(str(drive), str(tmp_path / "mac" / "DJ Library"), str(tmp_path / "mac.xml"),
                           messages.append, platform="darwin")
    assert imp.music.copied == 1 and imp.music.errors == 0
    assert not any("after the copy" in m for m in messages)

    # Names that differ only in Unicode form count as the same track.
    assert tb._nfc(nfd) == tb._nfc(nfc)
    _, missing = tb.localize_itunes_xml(str(xml), str(tmp_path / "o.xml"), "/m", "darwin", available={tb._nfc(nfc)})
    assert missing == 1  # only "One & Two.flac" is absent; the accented track is matched


# ---- Traktor Drive Sync: remembered settings and one-click runs ----

import PipelineScript_Audio_TraktorDriveSync as ds  # noqa: E402


def test_settings_are_remembered(tmp_path):
    path = str(tmp_path / "cfg.json")
    first = ds.ConfigManager(path)
    first.update_settings(last_mode="import", export_prune=True, import_bundle="G:/DJ Transfer", bogus=1)
    again = ds.ConfigManager(path).settings
    assert (again.last_mode, again.export_prune, again.import_bundle) == ("import", True, "G:/DJ Transfer")
    assert again.export_playlists is True and again.import_review is True


def test_locate_bundle_prefers_saved_folder(tmp_path):
    assert ds.locate_bundle(str(tmp_path)) == str(tmp_path)


def test_headless_export_then_import_use_saved_settings(tmp_path, monkeypatch):
    library = tmp_path / "pc" / "DJ Library"
    make_library(library, ["a.flac", "b.flac"])
    xml = tmp_path / "pc" / "DJ Library.xml"
    xml.write_text(PC_XML, encoding="utf-8")
    drive = tmp_path / "drive" / "DJ Transfer"
    monkeypatch.setattr(ds, "default_bundle_dir", lambda: "")

    pc = ds.ConfigManager(str(tmp_path / "pc.json"))
    pc.update_settings(export_library=str(library), export_xml=str(xml), export_bundle=str(drive),
                       export_playlists=False)
    assert ds.run_headless_export(pc) is True
    assert pc.settings.last_mode == "export" and (drive / "bundle.json").exists()

    opened = []
    monkeypatch.setattr(ds, "open_playlist_review", lambda *a: opened.append(a))
    laptop = ds.ConfigManager(str(tmp_path / "laptop.json"))
    laptop.update_settings(import_bundle=str(drive), import_library=str(tmp_path / "lap" / "DJ Library"),
                           import_xml=str(tmp_path / "lap" / "DJ Library.xml"))
    assert ds.run_headless_import(laptop) is True
    assert laptop.settings.last_mode == "import"
    assert (tmp_path / "lap" / "DJ Library" / "a.flac").exists() and not opened  # no playlists in this bundle


def test_headless_import_fails_cleanly_without_a_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(ds, "locate_bundle", lambda saved: saved)
    cfg = ds.ConfigManager(str(tmp_path / "c.json"))
    cfg.update_settings(import_bundle=str(tmp_path / "nothing"))
    assert ds.run_headless_import(cfg) is False


def test_copy_succeeds_when_listed_name_will_not_open(tmp_path, monkeypatch):
    """macOS + exFAT: the listing gives the decomposed name but opening it fails; the composed form opens."""
    nfc, nfd = "Sir\u0101t.flac", "Sira\u0304t.flac"
    src = tmp_path / "drive" / "DJ Library"
    make_library(src, [nfc])  # only the composed spelling exists on disk (NTFS keeps the forms apart)
    if (src / nfd).exists():
        pytest.skip("filesystem treats both Unicode forms as the same file")
    real = tb._library_files
    monkeypatch.setattr(tb, "_library_files", lambda folder: [nfd] if folder == str(src) else real(folder))

    stats = tb.sync_folder(str(src), str(tmp_path / "mac" / "DJ Library"), lambda m: None)
    assert (stats.copied, stats.errors) == (1, 0)
    assert tb._nfc(nfc) in {tb._nfc(n) for n in real(str(tmp_path / "mac" / "DJ Library"))}


def test_export_writes_composed_names_and_replaces_decomposed_copies(tmp_path):
    nfd, nfc = "Sira\u0304t.flac", "Sir\u0101t.flac"
    library = tmp_path / "lib"
    make_library(library, [nfd, "plain.flac"])
    if len(os.listdir(library)) != 2:
        pytest.skip("filesystem merges Unicode forms")
    xml = tmp_path / "x.xml"
    xml.write_text(PC_XML, encoding="utf-8")
    drive = tmp_path / "drive"
    music = drive / "DJ Library"
    make_library(music, [nfd])  # an older bundle that holds the decomposed spelling

    result = tb.export_bundle(str(library), str(xml), str(drive), lambda m: None)
    assert sorted(os.listdir(music)) == sorted([nfc, "plain.flac"])
    assert result.music.errors == 0

    again = tb.export_bundle(str(library), str(xml), str(drive), lambda m: None)
    assert again.music.copied == 0 and again.music.skipped == 2


def test_window_save_keeps_a_mode_changed_by_the_hub_switch(tmp_path, monkeypatch, tk_window):
    monkeypatch.setattr(ds, "CONFIG_FILE", str(tmp_path / "cfg.json"))
    monkeypatch.setattr(ds.ConfigManager.__init__, "__defaults__", (str(tmp_path / "cfg.json"),))
    ds.ConfigManager().update_settings(last_mode="export")
    root = tk_window
    try:
        ui = ds.DriveSyncUI(root)
        ds.ConfigManager().update_settings(last_mode="import")  # hub switch flipped while the window is open
        ui._save()                                              # what closing the window does
        assert ds.ConfigManager().settings.last_mode == "import"
        ui._save(last_mode="export")                            # running an export still sets the mode
        assert ds.ConfigManager().settings.last_mode == "export"
    finally:
        pass


# ---- a source file replaced after it was exported must reach the drive and the Mac

def _touch(path, mtime):
    os.utime(path, (mtime, mtime))


def test_source_changed_since_export_compares_modified_dates(tmp_path):
    import PipelineScript_Audio_TraktorSync as ts

    src, dst = tmp_path / "src.flac", tmp_path / "dst.flac"
    src.write_bytes(b"a")
    dst.write_bytes(b"b")
    _touch(src, 1_000_000)
    _touch(dst, 1_000_000)
    assert ts.source_changed_since_export(str(src), str(dst)) is False          # same time: nothing changed
    _touch(src, 1_000_001)
    assert ts.source_changed_since_export(str(src), str(dst)) is False          # within the 2 s file-system slack
    _touch(src, 1_000_100)
    assert ts.source_changed_since_export(str(src), str(dst)) is True           # source modified after the export
    _touch(dst, 1_000_200)
    assert ts.source_changed_since_export(str(src), str(dst)) is False          # export written after the edit
    assert ts.source_changed_since_export(str(src), str(tmp_path / "missing.flac")) is False


def test_export_replaces_a_track_whose_source_was_replaced(tmp_path):
    import PipelineScript_Audio_TraktorSync as ts

    log = []
    stub = SimpleNamespace(append_to_text_widget=lambda widget, text: log.append(text), sync_text=None,
                           status_var=SimpleNamespace(set=lambda s: None), get_track_title=lambda p: "Ghost Rockets")
    source = tmp_path / "M" / "02 Ghost Rockets.flac"
    source.parent.mkdir()
    source.write_bytes(b"CORRUPT")
    _touch(source, 1_000_000)
    library = tmp_path / "DJ Library"

    def sync():
        log.clear()
        ts.PlaylistSyncUI.update_dj_library(stub, [str(source)], str(library), skip_existing=True,
                                            convert_to_flac=True, preserve_album_art=False)
        return "".join(log)

    sync()
    exported = library / "Ghost Rockets.flac"
    assert exported.read_bytes() == b"CORRUPT"

    assert "Files skipped (already exist): 1" in sync() and exported.read_bytes() == b"CORRUPT"   # unchanged: skipped

    source.write_bytes(b"GOOD-NEW")                    # the converted file is replaced on the M drive
    _touch(source, 1_000_500)
    text = sync()
    assert exported.read_bytes() == b"GOOD-NEW"
    assert "Source changed since the last export, replacing: Ghost Rockets.flac" in text
    assert "Files replaced (source changed since export): 1" in text

    assert "Files skipped (already exist): 1" in sync()                                            # and stable afterwards


def test_replaced_file_with_the_same_size_still_reaches_the_drive_and_the_mac(tmp_path):
    library = tmp_path / "DJ Library"
    library.mkdir()
    track = library / "Ghost Rockets.flac"
    track.write_bytes(b"CORRUPT!")
    _touch(track, 1_000_000)
    xml = tmp_path / "DJ Library.xml"
    xml.write_text(PC_XML, encoding="utf-8")
    drive = tmp_path / "drive" / "DJ Transfer"
    mac_library = tmp_path / "mac" / "DJ Library"
    mac_xml = tmp_path / "mac" / "DJ Library.xml"
    logs = []

    tb.export_bundle(str(library), str(xml), str(drive), logs.append)
    tb.import_bundle(str(drive), str(mac_library), str(mac_xml), logs.append, platform="darwin")
    assert (mac_library / "Ghost Rockets.flac").read_bytes() == b"CORRUPT!"

    track.write_bytes(b"GOODFILE")                                      # same size, newer modified date
    _touch(track, 1_000_500)
    logs.clear()
    exp = tb.export_bundle(str(library), str(xml), str(drive), logs.append)
    assert (drive / "DJ Library" / "Ghost Rockets.flac").read_bytes() == b"GOODFILE"
    assert (exp.music.copied, exp.music.updated) == (1, 1)
    assert any("Replaced older version of Ghost Rockets.flac" in m for m in logs)

    logs.clear()
    imp = tb.import_bundle(str(drive), str(mac_library), str(mac_xml), logs.append, platform="darwin")
    assert (mac_library / "Ghost Rockets.flac").read_bytes() == b"GOODFILE"
    assert (imp.music.copied, imp.music.updated) == (1, 1)
    assert tb.copy_summary(imp.music) == "1 copied (1 replaced an older version), 0 unchanged"

    again = tb.import_bundle(str(drive), str(mac_library), str(mac_xml), logs.append, platform="darwin")
    assert (again.music.copied, again.music.skipped) == (0, 1)          # nothing is re-copied once it is current
