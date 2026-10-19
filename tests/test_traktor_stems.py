"""Tests for shared_traktor_stems: clearing Traktor's per-track "has stems" flag in collection.nml."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_traktor_stems as stems  # noqa: E402

HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n<NML VERSION="19"><HEAD COMPANY="www.native-instruments.com" PROGRAM="Traktor"></HEAD>\n<COLLECTION ENTRIES="5">\n'
TAIL = "</COLLECTION>\n</NML>\n"


def entry(title, info_attrs):
    return (f'<ENTRY TITLE="{title}" ARTIST="X"><LOCATION DIR="/:Music/:" FILE="{title}.flac" VOLUME="Macintosh HD"></LOCATION>'
            f'<INFO {info_attrs}></INFO></ENTRY>\n')


COLLECTION = (
    HEAD
    + entry("Stemmed", 'BITRATE="900000" GENRE="Techno" FLAGS="76" FILESIZE="1"')
    + entry("StemmedAnalyzedOff", 'BITRATE="900000" FLAGS="72" FILESIZE="1"')
    + entry("OnlyStemBit", 'BITRATE="900000" FLAGS="64"')
    + entry("Plain", 'BITRATE="900000" FLAGS="12" FILESIZE="1"')
    # '>' and a FLAGS look-alike inside a quoted value must not confuse the tag scan
    + entry("Tricky", 'COMMENT="a &gt; b > c FLAGS=&quot;76&quot;" FLAGS="76" FILESIZE="1"')
    + TAIL
)


def flags_of(xml_text):
    root = ET.fromstring(xml_text)
    return {e.get("TITLE"): e.find("INFO").get("FLAGS") for e in root.iter("ENTRY")}


def test_scan_counts_flagged_tracks():
    scan = stems.scan_stem_flags(COLLECTION.encode())
    assert (scan.tracks, scan.flagged) == (5, 4)


def test_strip_clears_only_the_stem_bit():
    new, changed = stems.strip_stem_flags(COLLECTION.encode())
    assert changed == 4
    assert flags_of(new.decode()) == {
        "Stemmed": "12",              # 76 -> 12, like Traktor's own "Remove stems"
        "StemmedAnalyzedOff": "8",    # 72 -> 8
        "OnlyStemBit": None,          # nothing left -> Traktor omits the attribute
        "Plain": "12",                # untouched
        "Tricky": "12",
    }


def test_strip_changes_nothing_but_flags():
    new, _ = stems.strip_stem_flags(COLLECTION.encode())
    before, after = ET.fromstring(COLLECTION), ET.fromstring(new.decode())
    for a, b in zip(before.iter(), after.iter()):
        assert a.tag == b.tag
        ignore = {"FLAGS"} if a.tag == "INFO" else set()
        assert {k: v for k, v in a.attrib.items() if k not in ignore} == {k: v for k, v in b.attrib.items() if k not in ignore}
    assert stems.scan_stem_flags(new).flagged == 0
    # second pass is a no-op
    assert stems.strip_stem_flags(new) == (new, 0)


def test_clear_stem_flag_on_parsed_entry():
    root = ET.fromstring(COLLECTION)
    entries = {e.get("TITLE"): e for e in root.iter("ENTRY")}
    assert stems.clear_stem_flag(entries["Stemmed"]) is True
    assert entries["Stemmed"].find("INFO").get("FLAGS") == "12"
    assert stems.clear_stem_flag(entries["OnlyStemBit"]) is True
    assert "FLAGS" not in entries["OnlyStemBit"].find("INFO").attrib
    assert stems.clear_stem_flag(entries["Plain"]) is False


def test_clean_collection_dry_run_writes_nothing(tmp_path):
    nml = tmp_path / "collection.nml"
    nml.write_text(COLLECTION, encoding="utf-8")
    result = stems.clean_collection(str(nml), apply=False)
    assert (result.tracks, result.flagged, result.applied) == (5, 4, False)
    assert nml.read_text(encoding="utf-8") == COLLECTION
    assert [p.name for p in tmp_path.iterdir()] == ["collection.nml"]


def test_clean_collection_apply_backs_up_and_rewrites(tmp_path):
    nml = tmp_path / "collection.nml"
    nml.write_text(COLLECTION, encoding="utf-8")
    result = stems.clean_collection(str(nml), apply=True)
    assert result.applied and result.flagged == 4
    assert Path(result.backup_path).read_text(encoding="utf-8") == COLLECTION
    assert stems.scan_stem_flags(nml.read_bytes()).flagged == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(["collection.nml", Path(result.backup_path).name])


def test_clean_collection_without_stems_leaves_file_and_makes_no_backup(tmp_path):
    clean = HEAD + entry("Plain", 'FLAGS="12"') + TAIL
    nml = tmp_path / "collection.nml"
    nml.write_text(clean, encoding="utf-8")
    result = stems.clean_collection(str(nml), apply=True)
    assert (result.flagged, result.applied, result.backup_path) == (0, False, None)
    assert [p.name for p in tmp_path.iterdir()] == ["collection.nml"]


def test_clean_collection_rejects_other_files(tmp_path):
    other = tmp_path / "notes.xml"
    other.write_text("<root><INFO FLAGS=\"76\"></INFO></root>", encoding="utf-8")
    with pytest.raises(ValueError):
        stems.clean_collection(str(other), apply=True)
    assert "76" in other.read_text(encoding="utf-8")


def test_playlist_import_does_not_copy_stem_links_to_the_other_machine():
    import PipelineScript_Audio_TraktorPlaylistSync as tps  # noqa: E402

    src = tps.MachineProfile(name="mac", volume="Macintosh HD", dir_prefix="/:Music/:")
    dst = tps.MachineProfile(name="pc", volume="C:", dir_prefix="/:DJ/:")
    src_key = "Macintosh HD/:Music/:Stemmed.flac"

    export_entry = ET.fromstring(
        '<ENTRY TITLE="Stemmed" ARTIST="X"><LOCATION DIR="/:Music/:" FILE="Stemmed.flac" VOLUME="Macintosh HD"></LOCATION>'
        '<INFO BITRATE="900000" FLAGS="76"></INFO></ENTRY>'
    )
    playlist = ET.fromstring(
        f'<NODE TYPE="PLAYLIST" NAME="Set"><PLAYLIST ENTRIES="1" TYPE="LIST">'
        f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{src_key}"></PRIMARYKEY></ENTRY></PLAYLIST></NODE>'
    )
    collection = ET.fromstring('<COLLECTION ENTRIES="0"></COLLECTION>')
    stats = tps.ImportStats()

    tps._merge_playlist_entries(playlist, {src_key: export_entry}, collection, {}, src, dst, stats)

    copied = collection.find("ENTRY")
    assert copied.find("LOCATION").get("VOLUME") == "C:"            # the existing path rewrite still happens
    assert copied.find("INFO").get("FLAGS") == "12"                 # 76 -> 12: no stem claim on the new machine
    assert stats.stem_links_removed == 1
    assert export_entry.find("INFO").get("FLAGS") == "76"           # the source entry itself is not touched


# ----------------------------------------------------------------------
# Restoring links for stem files that still exist
# ----------------------------------------------------------------------

def entry_at(title, info_attrs, artist="X", file=None):
    file = file or f"{title}.flac"
    return (f'<ENTRY TITLE="{title}" ARTIST="{artist}"><LOCATION DIR="/:Music/:" FILE="{file}" VOLUME="Macintosh HD">'
            f'</LOCATION><INFO {info_attrs}></INFO></ENTRY>\n')


BACKUP = (
    HEAD
    + entry_at("Keep Me", 'BITRATE="1" FLAGS="76" FILESIZE="1"')
    + entry_at("No Flags Attr", 'BITRATE="1" FLAGS="64" FILESIZE="1"')
    + entry_at("Twin", 'FLAGS="76" FILESIZE="1"', artist="A", file="Twin A.flac")
    + entry_at("Twin", 'FLAGS="76" FILESIZE="1"', artist="B", file="Twin B.flac")
    + entry_at("Never Stemmed", 'FLAGS="12" FILESIZE="1"')
    + entry_at("Already", 'FLAGS="76" FILESIZE="1"')
    + entry_at("Fish &amp; Chips", 'FLAGS="76" FILESIZE="1"')
    + TAIL
)
CURRENT = (   # what removing the links left: the stem bit gone everywhere (Already was re-linked by hand)
    HEAD
    + entry_at("Keep Me", 'BITRATE="1" FLAGS="12" FILESIZE="1"')
    + entry_at("No Flags Attr", 'BITRATE="1" FILESIZE="1"')
    + entry_at("Twin", 'FLAGS="12" FILESIZE="1"', artist="A", file="Twin A.flac")
    + entry_at("Twin", 'FLAGS="12" FILESIZE="1"', artist="B", file="Twin B.flac")
    + entry_at("Never Stemmed", 'FLAGS="12" FILESIZE="1"')
    + entry_at("Already", 'FLAGS="76" FILESIZE="1"')
    + entry_at("Fish &amp; Chips", 'FLAGS="12" FILESIZE="1"')
    + TAIL
)
STEMS = {
    "Keep Me": ["/Stems/001/A.stem.mp4"],
    "No Flags Attr": ["/Stems/002/B.stem.mp4"],
    "Twin": ["/Stems/003/C.stem.mp4"],             # two tracks share the title -> skipped
    "Never Stemmed": ["/Stems/004/D.stem.mp4"],    # file exists but the backup never had the link -> left alone
    "Already": ["/Stems/005/E.stem.mp4"],
    "Fish & Chips": ["/Stems/006/F.stem.mp4"],     # the NML spells it &amp;; the log has the plain text
    "Renamed Since": ["/Stems/007/G.stem.mp4"],    # no track with that title any more -> skipped
}


def test_read_tracks_decodes_attributes_and_skips_playlist_entries():
    nml = CURRENT.replace("</COLLECTION>", "</COLLECTION><PLAYLISTS><NODE><ENTRY><PRIMARYKEY KEY=\"x\"></PRIMARYKEY></ENTRY></NODE></PLAYLISTS>")
    tracks = stems.read_tracks(nml.encode())
    assert [t.title for t in tracks] == ["Keep Me", "No Flags Attr", "Twin", "Twin", "Never Stemmed", "Already", "Fish & Chips"]
    assert [t.flags for t in tracks] == [12, 0, 12, 12, 12, 76, 12]
    assert tracks[2].key == ("/:Music/:", "Twin A.flac") and tracks[2].label == "A - Twin"
    data = nml.encode()
    assert data[slice(*tracks[0].info)].startswith(b"<INFO BITRATE")


def test_plan_restore_sorts_every_title_into_one_bucket():
    plan = stems.plan_restore(CURRENT.encode(), BACKUP.encode(), STEMS)
    assert sorted(t.title for t in plan.to_flag) == ["Fish & Chips", "Keep Me", "No Flags Attr"]
    assert [t.title for t in plan.already_flagged] == ["Already"]
    assert [t.title for t in plan.not_flagged_in_backup] == ["Never Stemmed"]
    assert [(title, len(tracks)) for title, tracks in plan.ambiguous] == [("Twin", 2)]
    assert plan.unmatched == [("Renamed Since", "/Stems/007/G.stem.mp4")]


def test_a_stem_file_claimed_by_two_titles_is_ambiguous():
    shared = {"Keep Me": ["/Stems/001/A.stem.mp4"], "Never Stemmed": ["/Stems/001/A.stem.mp4"]}
    plan = stems.plan_restore(CURRENT.encode(), BACKUP.encode(), shared)
    assert not plan.to_flag and sorted(t for t, _ in plan.ambiguous) == ["Keep Me", "Never Stemmed"]


def test_add_stem_flags_sets_the_bit_and_changes_nothing_else():
    data = CURRENT.encode()
    keys = {("/:Music/:", "Keep Me.flac"), ("/:Music/:", "No Flags Attr.flac")}
    new, changed = stems.add_stem_flags(data, keys)
    assert changed == 2
    assert flags_of(new.decode()) == {
        "Keep Me": "76",            # 12 -> 76
        "No Flags Attr": "64",      # attribute was missing: added
        "Twin": "12", "Never Stemmed": "12", "Already": "76", "Fish & Chips": "12",
    }
    assert b'<INFO BITRATE="1" FLAGS="64" FILESIZE="1">' in new     # inserted where Traktor writes it
    before, after = ET.fromstring(CURRENT), ET.fromstring(new.decode())
    for a, b in zip(before.iter(), after.iter()):
        ignore = {"FLAGS"} if a.tag == "INFO" else set()
        assert {k: v for k, v in a.attrib.items() if k not in ignore} == {k: v for k, v in b.attrib.items() if k not in ignore}
    assert stems.add_stem_flags(new, keys) == (new, 0)               # already set -> no-op


def test_strip_then_restore_returns_the_original_flags():
    original = BACKUP.encode()
    stripped, _ = stems.strip_stem_flags(original)
    keys = {t.key for t in stems.read_tracks(original) if t.has_stems}
    restored, changed = stems.add_stem_flags(stripped, keys)
    assert changed == len(keys)
    assert flags_of(restored.decode()) == flags_of(BACKUP)


def test_restore_collection_preview_then_apply(tmp_path):
    nml, bak = tmp_path / "collection.nml", tmp_path / "collection.nml.bak-2026-10-18_202445"
    nml.write_text(CURRENT, encoding="utf-8")
    bak.write_text(BACKUP, encoding="utf-8")

    preview = stems.restore_collection(str(nml), str(bak), STEMS, apply=False)
    assert not preview.applied and len(preview.plan.to_flag) == 3
    assert nml.read_text(encoding="utf-8") == CURRENT

    result = stems.restore_collection(str(nml), str(bak), STEMS, apply=True)
    assert result.applied and Path(result.backup_path).read_text(encoding="utf-8") == CURRENT
    assert flags_of(nml.read_text(encoding="utf-8"))["Keep Me"] == "76"
    assert stems.restore_collection(str(nml), str(bak), STEMS, apply=True).applied is False   # nothing left to do


def test_surviving_stems_reads_logs_and_ignores_files_that_are_gone(tmp_path):
    docs = tmp_path / "Native Instruments"
    here, gone = tmp_path / "Stems" / "001" / "A.stem.mp4", tmp_path / "Stems" / "002" / "B.stem.mp4"
    here.parent.mkdir(parents=True)
    here.write_bytes(b"audio")
    for version, lines in {"Traktor 4.4.2": [
        f'2026-02-26 22:33:58 - Finished stem separation job for track "Keep Me" - result: successful: stem file stored to "{here}", with offset 0µs',
        f'2026-02-26 22:34:58 - Finished stem separation job for track "Keep Me" - result: successful: stem file stored to "{here}", with offset 0µs',
        f'2026-02-26 22:35:58 - Finished stem separation job for track "Lost" - result: successful: stem file stored to "{gone}", with offset 0µs',
        '2026-02-26 22:36:58 - Finalized stem separation process for track "Keep Me" - result: failed because an exception was thrown',
    ]}.items():
        log = docs / version / "Logs" / "Traktor.log"
        log.parent.mkdir(parents=True)
        log.write_text("\n".join(lines), encoding="utf-8")
    assert stems.surviving_stems(str(docs)) == {"Keep Me": [str(here)]}


def test_find_stem_backup_skips_backups_without_stem_links(tmp_path):
    import os
    nml = tmp_path / "collection.nml"
    nml.write_text(CURRENT, encoding="utf-8")
    old = tmp_path / "collection.nml.bak-2026-10-18_202445"
    new = tmp_path / "collection.nml.bak-2026-10-19_090000"
    old.write_text(BACKUP, encoding="utf-8")                        # has links
    new.write_text(HEAD + entry_at("X", 'FLAGS="12"') + TAIL, encoding="utf-8")   # newer, but already clean
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))
    assert stems.find_stem_backup(str(nml)) == str(old)
