"""Tests for shared_traktor_keep and the Traktor-sync pieces that use it."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_traktor_keep as keep  # noqa: E402

DJ = r"C:\Users\flori\Music\DJ Library"

NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="4">
<ENTRY TITLE="Sirāt" ARTIST="Kangding Ray"><LOCATION DIR="/:Users/:flori/:Music/:DJ Library/:" FILE="Sirāt.flac" VOLUME="C:"></LOCATION><ALBUM TITLE="Sirāt OST"></ALBUM></ENTRY>
<ENTRY TITLE="Katharsis" ARTIST="Kangding Ray"><LOCATION DIR="/:Users/:flori/:Music/:DJ Library/:" FILE="Katharsis.flac" VOLUME="C:"></LOCATION></ENTRY>
<ENTRY TITLE="Present" ARTIST="X"><LOCATION DIR="/:Users/:flori/:Music/:DJ Library/:" FILE="Present.flac" VOLUME="C:"></LOCATION></ENTRY>
<ENTRY TITLE="Mac only" ARTIST="X"><LOCATION DIR="/:Users/:flori/:Music/:DJ Library/:" FILE="Mac only.flac" VOLUME=""></LOCATION></ENTRY>
</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="2">
<NODE TYPE="PLAYLIST" NAME="Preparation"><PLAYLIST ENTRIES="2" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:flori/:Music/:DJ Library/:Sirāt.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:flori/:Music/:DJ Library/:Present.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE>
<NODE TYPE="FOLDER" NAME="Sets"><SUBNODES COUNT="1"><NODE TYPE="PLAYLIST" NAME="Opener"><PLAYLIST ENTRIES="2" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="C:/:Users/:flori/:Music/:DJ Library/:sirāt.FLAC"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="/:Users/:flori/:Music/:DJ Library/:Mac only.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE></SUBNODES></NODE>
</SUBNODES></NODE></PLAYLISTS></NML>
"""


@pytest.fixture
def nml(tmp_path):
    path = tmp_path / "collection.nml"
    path.write_text(NML, encoding="utf-8")
    return str(path)


def test_load_refs_only_this_machines_dj_library(nml):
    refs = keep.load_refs(nml, DJ)
    assert sorted(e.file for e in refs.entries) == ["Katharsis.flac", "Present.flac", "Sirāt.flac"]
    assert refs.playlist_refs[keep.norm_name("Sirāt.flac")] == ["Preparation", "Sets / Opener"]
    assert keep.norm_name("Present.flac") in refs.playlist_refs
    assert keep.norm_name("Mac only.flac") not in refs.playlist_refs
    # forward slashes / trailing separator in the configured folder are fine
    assert len(keep.load_refs(nml, "C:/Users/flori/Music/DJ Library/").entries) == 3


def test_missing_referenced_ignores_unreferenced_and_case(nml):
    refs = keep.load_refs(nml, DJ)
    missing = keep.missing_referenced(refs, ["present.FLAC", "Other.flac"])
    # Katharsis is missing but in no Traktor playlist -> not kept; Present exists (case-insensitive)
    assert [e.file for e in missing] == ["Sirāt.flac"]


def test_match_to_library_unique_ambiguous_and_none():
    entries = [
        keep.TraktorEntry("Sirāt.flac", "Sirāt", "Kangding Ray", "Sirāt OST"),
        keep.TraktorEntry("Twin.flac", "Twin", "A", ""),
        keep.TraktorEntry("Nope.flac", "Nope", "Z", ""),
        keep.TraktorEntry("tell me.flac", "Tell Me", "Submotive;Krakota", ""),
    ]
    library = [
        ("1", "Kangding Ray", "Sirāt", "Sirāt OST"),
        ("2", "A", "Twin", "One"),
        ("3", "A", "Twin", "Two"),
        ("4", "Submotive", "Tell Me", ""),
    ]
    matched, unmatched = keep.match_to_library(entries, library)
    assert matched == {keep.norm_name("Sirāt.flac"): "1", keep.norm_name("tell me.flac"): "4"}
    assert {e.file: why for e, why in unmatched} == {"Twin.flac": "2 possible tracks", "Nope.flac": "no matching MusicBee track"}


def test_move_to_quarantine_never_overwrites(tmp_path):
    lib = tmp_path / "DJ Library"
    lib.mkdir()
    for content in (b"one", b"two"):
        f = lib / "a.flac"
        f.write_bytes(content)
        keep.move_to_quarantine(str(f), str(lib), "2026-10-05")
    moved = sorted(p.name for p in (lib / "_Removed" / "2026-10-05").iterdir())
    assert moved == ["a (1).flac", "a.flac"]
    assert not (lib / "a.flac").exists()


def _sync_stub(tmp_path, refs=None, **settings):
    """Just enough of PlaylistSyncUI for delete_removed_tracks / _restore_traktor_referenced."""
    log = []
    cfg = SimpleNamespace(protect_traktor_referenced=True, quarantine_removed=True, traktor_collection_path="")
    for k, v in settings.items():
        setattr(cfg, k, v)
    stub = SimpleNamespace(
        config_manager=SimpleNamespace(settings=cfg), log=log, sync_text="sync", analysis_text="analysis",
        append_to_text_widget=lambda widget, text: log.append(text),
        _get_traktor_refs=lambda dj: refs, _keep_expected={},
        dj_library_var=SimpleNamespace(get=lambda: str(tmp_path / "DJ Library")),
    )
    return stub


def _library_with_export(tmp_path, names, active):
    import urllib.parse
    lib = tmp_path / "DJ Library"
    lib.mkdir()
    for n in names:
        (lib / n).write_bytes(b"x")
    tracks = "".join(
        f"<key>{i}</key><dict><key>Location</key><string>file://localhost/{urllib.parse.quote(str(lib / n).replace(chr(92), '/'))}"
        f"</string></dict>" for i, n in enumerate(active))
    xml = tmp_path / "DJ Library.xml"
    xml.write_text(f"<plist><dict><key>Tracks</key><dict>{tracks}</dict></dict></plist>", encoding="utf-8")
    return lib, str(xml)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_delete_keeps_traktor_referenced_and_quarantines_the_rest(tmp_path):
    import PipelineScript_Audio_TraktorSync as ts

    lib, xml = _library_with_export(tmp_path, ["active.flac", "keepme.flac", "orphan.flac"], ["active.flac"])
    refs = keep.TraktorRefs(playlist_refs={keep.norm_name("KeepMe.flac"): ["Preparation"]})
    stub = _sync_stub(tmp_path, refs)
    deleted = ts.PlaylistSyncUI.delete_removed_tracks(stub, str(lib), xml)
    assert deleted == 1
    assert sorted(p.name for p in lib.iterdir() if p.is_file()) == ["active.flac", "keepme.flac"]
    quarantined = list((lib / "_Removed").rglob("orphan.flac"))
    assert len(quarantined) == 1 and quarantined[0].read_bytes() == b"x"
    assert any("Kept (still in Traktor playlist Preparation): keepme.flac" in m for m in stub.log)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_delete_without_quarantine_or_protection_behaves_like_before(tmp_path):
    import PipelineScript_Audio_TraktorSync as ts

    lib, xml = _library_with_export(tmp_path, ["active.flac", "keepme.flac", "orphan.flac"], ["active.flac"])
    refs = keep.TraktorRefs(playlist_refs={keep.norm_name("keepme.flac"): ["Preparation"]})
    stub = _sync_stub(tmp_path, refs, quarantine_removed=False, protect_traktor_referenced=False)
    assert ts.PlaylistSyncUI.delete_removed_tracks(stub, str(lib), xml) == 2
    assert sorted(p.name for p in lib.iterdir() if p.is_file()) == ["active.flac"]
    assert not (lib / "_Removed").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")
def test_restore_traktor_referenced(tmp_path):
    import PipelineScript_Audio_TraktorSync as ts

    lib = tmp_path / "DJ Library"
    lib.mkdir()
    (lib / "Already.flac").write_bytes(b"x")
    refs = keep.TraktorRefs(
        entries=[
            keep.TraktorEntry("Sirāt.flac", "Sirāt", "Kangding Ray", ""),
            keep.TraktorEntry("Clash.flac", "Clash", "B", ""),
            keep.TraktorEntry("Ghost.flac", "Ghost", "Nobody", ""),
            keep.TraktorEntry("Already.flac", "Already", "A", ""),
            keep.TraktorEntry("Make Me.flac", "Make Me", "Borai", ""),
        ],
        playlist_refs={keep.norm_name(f): ["Preparation"] for f in
                       ["Sirāt.flac", "Clash.flac", "Ghost.flac", "Already.flac", "Make Me.flac"]})
    tracks_metadata = {
        "1": {"Artist": "Kangding Ray", "Name": "Sirāt", "Album": ""},
        "2": {"Artist": "B", "Name": "Clash", "Album": ""},
        "3": {"Artist": "C", "Name": "Clash", "Album": ""},          # same title, already syncing
        "4": {"Artist": "Borai", "Name": "Make Me", "Album": ""},     # already syncing
    }
    paths = {t: rf"M:\Music\{t}.flac" for t in tracks_metadata}
    to_copy = [paths["3"], paths["4"]]
    selected = []
    stub = _sync_stub(tmp_path, refs)
    ts.PlaylistSyncUI._restore_traktor_referenced(stub, tracks_metadata, paths, selected, to_copy)

    assert paths["1"] in to_copy                      # Sirāt restored
    assert paths["2"] not in to_copy                  # title collides with track 3 -> skipped
    assert selected == [{'name': keep.KEEP_PLAYLIST_NAME, 'id': 0, 'track_ids': ['1']}]
    assert stub._keep_expected == {paths["1"]: "Sirāt.flac", paths["4"]: "Make Me.flac"}
    text = "".join(stub.log)
    assert "restoring 1 from MusicBee" in text
    assert "not restoring 'Clash.flac'" in text and "cannot restore 'Ghost.flac'" in text


def test_update_playlist_items_appends_resolved_tracks():
    import PipelineScript_Audio_TraktorSync as ts

    playlist = ET.fromstring(
        "<dict><key>Name</key><string>P</string><key>Playlist Items</key><array>"
        "<dict><key>Track ID</key><integer>1</integer></dict>"
        "<dict><key>Track ID</key><integer>9</integer></dict></array></dict>")
    stub = SimpleNamespace(safe_int_conversion=int)
    count = ts.PlaylistSyncUI.update_playlist_items(stub, playlist, {"1", "2", "3"}, ["2", "1", "7"])
    ids = [item[1].text for item in playlist.find("array")]
    # 9 is not valid -> dropped; 1 not duplicated; 2 appended; 7 not valid -> ignored
    assert ids == ["1", "2"] and count == 2
