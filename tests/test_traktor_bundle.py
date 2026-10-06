"""Tests for shared_traktor_bundle (PC -> drive -> laptop transfer)."""

import os
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
