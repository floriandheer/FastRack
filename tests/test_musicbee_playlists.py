"""Tests for shared_musicbee_playlists (MusicBee .mbp reader/writer, dead-entry scan + repair)."""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import shared_musicbee_playlists as mb  # noqa: E402

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows path semantics")

# Real single-entry playlist as written by MusicBee (header + 1 entry).
SINGLE_ENTRY_PATH = r"M:\Music\Various Artists\Get Physical 7th Anniversary Compilation Pt.1\09 Penny From The Lane.flac"
SINGLE_ENTRY_BYTES = (
    bytes.fromhex("04000000" "120000" "4d3a5c00" "3000" "46616c736500" "00" "2d3200" "0400"
                  "ffffffff" "00000000" "ffffffff" "01000000" "62")
    + SINGLE_ENTRY_PATH.encode("utf-8") + b"\xff\xff\xff\xff"
)


def _track(tid, artist, title, path=""):
    return mb.LibraryTrack(tid, path or rf"M:\Music\{artist}\{title}.flac", artist, title)


# ---------------------------------------------------------------- .mbp format

def test_writer_matches_real_musicbee_bytes():
    assert mb.Mbp([SINGLE_ENTRY_PATH]).to_bytes() == SINGLE_ENTRY_BYTES


def test_reader_roundtrips_real_bytes():
    parsed = mb.Mbp.from_bytes(SINGLE_ENTRY_BYTES)
    assert parsed.entries == [SINGLE_ENTRY_PATH]
    assert parsed.to_bytes() == SINGLE_ENTRY_BYTES


def test_roundtrip_unicode_long_path_and_description():
    long_path = "M:\\Music\\" + "A" * 150 + "\\1-01 Sirāt – Ünïcode.flac"
    original = mb.Mbp([long_path, r"M:\Music\x\y.flac"], ["Makes for a great trip", "", "M:\\", "0", "False", "", "-2"])
    data = original.to_bytes()
    parsed = mb.Mbp.from_bytes(data)
    assert parsed.entries == original.entries
    assert parsed.description == "Makes for a great trip"
    assert parsed.to_bytes() == data


def test_reader_rejects_trailing_bytes():
    with pytest.raises(mb.MbpFormatError):
        mb.Mbp.from_bytes(SINGLE_ENTRY_BYTES + b"\x00")


# -------------------------------------------------------------------- resolve

def make_index():
    return mb.LibraryIndex([
        _track("1", "Oklou", "Endless", r"M:\Music\Oklou\Choke Enough\1-01 Endless.flac"),
        _track("2", "Alarico", "Caresses After Lies"),
        _track("3", "Brunello", "Science Fiction (Original Mix)"),
        _track("4", "Artist", "Home"),
        _track("5", "Artist", "Home (Extended Mix)"),
        _track("6", "Twin", "Same Song"),
        _track("7", "Twin", "Same Song"),
        _track("8", "Sirāt Band", "Ritual"),
    ])


@pytest.mark.parametrize("dead, expected", [
    (r"C:\dl\complete\x\Oklou (2025) choke enough\01. Oklou - endless.flac", "1"),
    (r"C:\dl\complete\y\Alarico-Sonora\02-alarico-caresses_after_lies.flac", "2"),
    (r"C:\dl\complete\z\26. Brunello - Science Fiction (Original Mix).flac", "3"),
    (r"C:\dl\complete\sirat band\03 - Sirat Band - Ritual.flac", "8"),
])
def test_resolve_unique(dead, expected):
    track, n = make_index().resolve(dead)
    assert track is not None and track.track_id == expected and n == 1


def test_resolve_prefers_longest_title():
    track, _ = make_index().resolve(r"C:\dl\Artist - Home (Extended Mix).flac")
    assert track.track_id == "5"


def test_resolve_plain_title_does_not_pick_longer_version():
    track, _ = make_index().resolve(r"C:\dl\Artist - Home.flac")
    assert track.track_id == "4"


def test_resolve_ambiguous_returns_none():
    track, n = make_index().resolve(r"C:\dl\Twin - Same Song.flac")
    assert track is None and n == 2


def test_resolve_never_maps_remix_to_original():
    track, _ = make_index().resolve(r"C:\dl\complete\Oklou - Endless (Someone Remix).flac")
    assert track is None


def test_resolve_no_match():
    assert make_index().resolve(r"C:\dl\Nobody - Nothing.flac") == (None, 0)


def test_merge_track_ids_dedupes_and_keeps_order():
    assert mb.merge_track_ids(["1", "2"], ["2", "3", "3"]) == ["1", "2", "3"]


def test_apply_extra_ids_is_idempotent_and_resets():
    from shared_playlist_doctor_dialog import apply_extra_ids

    data = {"A": {"track_ids": ["1", "2"]}, "B": {"track_ids": ["3"]}}
    apply_extra_ids(data, {"A": ["2", "9"]})
    assert data["A"]["track_ids"] == ["1", "2", "9"] and data["B"]["track_ids"] == ["3"]
    apply_extra_ids(data, {"A": ["2", "9"]})
    assert data["A"]["track_ids"] == ["1", "2", "9"]
    apply_extra_ids(data, {"B": ["4"]})  # a later run with different extras never inherits the old ones
    assert data["A"]["track_ids"] == ["1", "2"] and data["B"]["track_ids"] == ["3", "4"]
    apply_extra_ids(data, {})
    assert data["A"]["track_ids"] == ["1", "2"] and data["B"]["track_ids"] == ["3"]


# ------------------------------------------------------------ scan and repair

@pytest.fixture
def env(tmp_path):
    lib = tmp_path / "lib"
    staging = tmp_path / "staging"
    lib.mkdir()
    staging.mkdir()
    ok_file = lib / "1-01 Endless.flac"
    ok_file.write_bytes(b"x")
    staged_file = staging / "still here.flac"
    staged_file.write_bytes(b"x")
    index = mb.LibraryIndex([
        mb.LibraryTrack("1", str(ok_file), "Oklou", "Endless"),
        mb.LibraryTrack("2", str(lib / "1-02 Blade Bird.flac"), "Oklou", "Blade Bird"),
    ])
    pl_dir = tmp_path / "Playlists"
    (pl_dir / "Mixes").mkdir(parents=True)
    dead_resolvable = r"C:\gone\complete\Oklou - blade bird.flac"
    dead_unresolvable = r"C:\gone\complete\Nobody - Nothing.flac"
    (pl_dir / "A.mbp").write_bytes(mb.Mbp([str(ok_file), dead_resolvable, dead_unresolvable, str(staged_file)]).to_bytes())
    (pl_dir / "Mixes" / "B.mbp").write_bytes(mb.Mbp([dead_resolvable]).to_bytes())
    (pl_dir / "Mixes" / "C.mbp").write_bytes(mb.Mbp([str(lib / "1-02 Blade Bird.flac"), dead_resolvable]).to_bytes())
    return {"pl_dir": str(pl_dir), "index": index, "staging": [str(staging)], "ok_file": str(ok_file),
            "dead_resolvable": dead_resolvable, "dead_unresolvable": dead_unresolvable,
            "blade": str(lib / "1-02 Blade Bird.flac"), "backup": str(tmp_path / "backup"), "staged": str(staged_file)}


@windows_only
def test_scan_classifies_entries_and_walks_subfolders(env):
    report = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    assert report.playlists_scanned == 3
    kinds = sorted((i.playlist, i.kind, bool(i.resolved)) for i in report.issues)
    assert kinds == [
        ("A", "dead", False), ("A", "dead", True), ("A", "staging", False),
        ("B", "dead", True), ("C", "dead", True),
    ]
    assert report.extra_track_ids() == {"A": ["2"], "B": ["2"], "C": ["2"]}


@windows_only
def test_scan_respects_selected_playlists(env):
    report = mb.scan(env["pl_dir"], env["index"], selected={"B"}, staging_roots=env["staging"])
    assert [i.playlist for i in report.issues] == ["B"]


@windows_only
def test_repair_dry_run_writes_nothing(env):
    report = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    before = Path(env["pl_dir"], "A.mbp").read_bytes()
    result = mb.repair(report, apply=False, backup_root=env["backup"])
    assert result.replaced == 2 and result.dropped == 1 and not result.applied
    assert Path(env["pl_dir"], "A.mbp").read_bytes() == before
    assert not os.path.exists(env["backup"])


@windows_only
def test_repair_rewrites_in_place_with_backup_and_dedupe(env):
    report = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    original_a = Path(env["pl_dir"], "A.mbp").read_bytes()
    result = mb.repair(report, apply=True, backup_root=env["backup"], check_running=False)

    a = mb.Mbp.from_bytes(Path(env["pl_dir"], "A.mbp").read_bytes()).entries
    assert a == [env["ok_file"], env["blade"], env["dead_unresolvable"], env["staged"]]
    assert mb.Mbp.from_bytes(Path(env["pl_dir"], "Mixes", "B.mbp").read_bytes()).entries == [env["blade"]]
    # C already had the real track: the dead duplicate is dropped, not added twice
    assert mb.Mbp.from_bytes(Path(env["pl_dir"], "Mixes", "C.mbp").read_bytes()).entries == [env["blade"]]

    assert result.replaced == 2 and result.dropped == 1 and len(result.files_changed) == 3
    backups = [p for p in Path(result.backup_dir).iterdir()]
    assert len(backups) == 3 and original_a in [p.read_bytes() for p in backups]
    assert not list(Path(env["pl_dir"]).rglob("*.tmp-*"))
    # nothing may be written inside the playlists folder besides the playlists themselves
    assert not any(p.is_dir() and p.name.startswith("_") for p in Path(env["pl_dir"]).iterdir())

    # second run: nothing left to resolve
    again = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    assert [i.kind for i in again.issues if i.resolved] == []


@windows_only
def test_repair_refuses_while_musicbee_runs(env, monkeypatch):
    report = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    monkeypatch.setattr(mb, "musicbee_running", lambda: True)
    before = Path(env["pl_dir"], "A.mbp").read_bytes()
    with pytest.raises(mb.MusicBeeRunningError):
        mb.repair(report, apply=True, backup_root=env["backup"])
    assert Path(env["pl_dir"], "A.mbp").read_bytes() == before
    mb.repair(report, apply=False, backup_root=env["backup"])  # dry run is always allowed


@windows_only
def test_repair_skips_file_with_unrecognised_layout(env):
    path = Path(env["pl_dir"], "A.mbp")
    weird = path.read_bytes() + b"\x00"
    path.write_bytes(weird)
    report = mb.scan(env["pl_dir"], env["index"], staging_roots=env["staging"])
    assert any(i.playlist == "A" and i.resolved for i in report.issues)
    result = mb.repair(report, apply=True, backup_root=env["backup"], check_running=False)
    assert path.read_bytes() == weird
    assert [Path(f).name for f, _ in result.skipped] == ["A.mbp"]


# ---------------------------------------------- duplicates, edits, library checks

@pytest.fixture
def dup_env(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    files = {}
    for name in ("a1.flac", "a2.flac", "b.flac", "c.flac"):
        (lib / name).write_bytes(b"x")
        files[name] = str(lib / name)
    index = mb.LibraryIndex([
        mb.LibraryTrack("1", files["a1.flac"], "Avicii", "Wake Me Up", "True"),
        mb.LibraryTrack("2", files["a2.flac"], "Avicii", "Wake Me Up", "Hits"),
        mb.LibraryTrack("3", files["b.flac"], "Other", "Song"),
        mb.LibraryTrack("4", str(lib / "gone.flac"), "Dead", "Link"),
        mb.LibraryTrack("5", "http://stream.example/radio", "", "Radio"),
    ])
    pl = tmp_path / "Playlists"
    pl.mkdir()
    (pl / "Dupes.mbp").write_bytes(mb.Mbp([
        files["b.flac"], files["a1.flac"], files["b.flac"], files["a2.flac"], files["b.flac"], files["c.flac"],
    ]).to_bytes())
    (pl / "Clean.mbp").write_bytes(mb.Mbp([files["b.flac"], files["c.flac"]]).to_bytes())
    (pl / "Empty.mbp").write_bytes(mb.Mbp([]).to_bytes())
    return {"pl": str(pl), "index": index, "files": files, "backup": str(tmp_path / "backup")}


@windows_only
def test_find_duplicates_exact_and_same_song(dup_env):
    found = mb.find_duplicates(dup_env["pl"], dup_env["index"])
    by_kind = {i.kind: i for i in found}
    assert {i.playlist for i in found} == {"Dupes"}
    assert by_kind["exact"].extra == 2 and by_kind["exact"].label == "Other - Song"
    assert by_kind["same_song"].paths == [dup_env["files"]["a1.flac"], dup_env["files"]["a2.flac"]]
    assert by_kind["same_song"].label == "Avicii - Wake Me Up"


@windows_only
def test_apply_edits_dedupe_and_remove(dup_env):
    f = dup_env["files"]
    file = os.path.join(dup_env["pl"], "Dupes.mbp")
    edit = mb.FileEdit(remove={f["a2.flac"]}, dedupe_exact=True)

    dry = mb.apply_edits({file: edit}, apply=False, backup_root=dup_env["backup"])
    assert (dry.removed, dry.dropped, dry.applied) == (1, 2, False)
    assert len(mb.Mbp.from_bytes(Path(file).read_bytes()).entries) == 6

    res = mb.apply_edits({file: edit}, apply=True, backup_root=dup_env["backup"], check_running=False)
    assert (res.removed, res.dropped, len(res.files_changed)) == (1, 2, 1)
    # first copy of b stays in place; second a (a2) removed on request; extra b copies dropped
    assert mb.Mbp.from_bytes(Path(file).read_bytes()).entries == [f["b.flac"], f["a1.flac"], f["c.flac"]]
    assert len(list(Path(res.backup_dir).iterdir())) == 1


@windows_only
def test_library_checks(dup_env):
    missing = mb.find_missing_library_files(dup_env["index"])
    assert [t.title for t in missing] == ["Link"]          # the stream URL is not a file
    groups = mb.find_duplicate_songs(dup_env["index"])
    assert [[t.album for t in g] for g in groups] == [["True", "Hits"]]


@windows_only
def test_find_empty_playlists(dup_env):
    assert mb.find_empty_playlists(dup_env["pl"]) == ["Empty"]


@windows_only
def test_fix_rows_defaults_and_edits(dup_env, tmp_path):
    f = dup_env["files"]
    pl = Path(dup_env["pl"])
    dead = r"C:\gone\complete\Other - Song.flac"
    (pl / "WithDead.mbp").write_bytes(mb.Mbp([dead, r"C:\gone\complete\Nobody - Nothing.flac", f["c.flac"]]).to_bytes())
    report = mb.scan(dup_env["pl"], dup_env["index"])
    dupes = mb.find_duplicates(dup_env["pl"], dup_env["index"])
    rows = mb.build_fix_rows(report, dupes)

    by = {(r.playlist, r.problem.split(" (")[0].split(" listed")[0]): r for r in rows}
    assert by[("WithDead", "Dead entry")].checked is True          # resolvable replace: safe default
    assert [r.checked for r in rows if r.problem.startswith("Same file")] == [True]
    assert not any(r.checked for r in rows if r.problem.startswith("Same song"))
    assert not any(r.checked for r in rows if r.action == "remove")  # dropping a song is always opt-in

    # tick one same-song file and the unresolved dead entry; the rest keeps its defaults
    for r in rows:
        if r.action == "remove" and (r.path == f["a2.flac"] or "Nobody" in r.path):
            r.checked = True
    edits = mb.edits_from_rows(rows)
    res = mb.apply_edits(edits, apply=True, backup_root=dup_env["backup"], check_running=False)
    assert (res.replaced, res.removed) == (1, 2) and res.dropped == 2
    assert mb.Mbp.from_bytes((pl / "WithDead.mbp").read_bytes()).entries == [f["b.flac"], f["c.flac"]]
    assert mb.Mbp.from_bytes((pl / "Dupes.mbp").read_bytes()).entries == [f["b.flac"], f["a1.flac"], f["c.flac"]]


@windows_only
def test_run_preflight_without_window_resolves_in_memory(env, caplog):
    import xml.etree.ElementTree as ET
    from types import SimpleNamespace
    import shared_playlist_doctor_dialog as dlg

    blade = env["blade"]
    root = ET.fromstring(
        "<plist><dict><key>Tracks</key><dict>"
        f"<key>2</key><dict><key>Track ID</key><integer>2</integer><key>Name</key><string>Blade Bird</string>"
        f"<key>Artist</key><string>Oklou</string><key>Location</key><string>file://localhost/{blade.replace(chr(92), '/')}</string></dict>"
        "</dict></dict></plist>")
    settings = SimpleNamespace(playlist_doctor_enabled=True, musicbee_playlists_dir=env["pl_dir"], staging_roots=env["staging"])
    xml_path = os.path.join(os.path.dirname(env["pl_dir"]), "iTunes Music Library.xml")

    # parent=None would crash on any Tk call: proves no window or cursor change is attempted
    extras = dlg.run_preflight(None, root, xml_path, ["A", "B", "C"], settings, interactive=False)
    assert extras == {"A": ["2"], "B": ["2"], "C": ["2"]}
    before = Path(env["pl_dir"], "A.mbp").read_bytes()
    assert Path(env["pl_dir"], "A.mbp").read_bytes() == before        # nothing written
    assert not os.path.exists(env["backup"])
