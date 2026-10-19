"""Tests for the Traktor Stems tool: its window, its command line and the order of the 'remove everything' clean-up."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import PipelineScript_Audio_TraktorStems as tool  # noqa: E402
import shared_traktor_stems as stems  # noqa: E402

NML = """<?xml version="1.0" encoding="UTF-8" standalone="no" ?>
<NML VERSION="19"><COLLECTION ENTRIES="3">
<ENTRY TITLE="One" ARTIST="A"><LOCATION DIR="/:M/:" FILE="One.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="76" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Two" ARTIST="B"><LOCATION DIR="/:M/:" FILE="Two.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="76" FILESIZE="1"></INFO></ENTRY>
<ENTRY TITLE="Three" ARTIST="C"><LOCATION DIR="/:M/:" FILE="Three.flac" VOLUME="Mac"></LOCATION><INFO FLAGS="12" FILESIZE="1"></INFO></ENTRY>
</COLLECTION>
<PLAYLISTS><NODE TYPE="FOLDER" NAME="$ROOT"><SUBNODES COUNT="1">
<NODE TYPE="PLAYLIST" NAME="Warmup"><PLAYLIST ENTRIES="3" TYPE="LIST">
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:One.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Two.flac"></PRIMARYKEY></ENTRY>
<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="Mac/:M/:Three.flac"></PRIMARYKEY></ENTRY>
</PLAYLIST></NODE>
</SUBNODES></NODE></PLAYLISTS></NML>
"""


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A fake home with a Traktor collection (two linked tracks, 'One' has its stem file, 'Two' does not)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    monkeypatch.setattr(tool, "CONFIG_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(tool, "running_traktor", lambda: [])
    docs = tmp_path / "Native Instruments"
    nml = docs / "Traktor 4.5.1" / "collection.nml"
    nml.parent.mkdir(parents=True)
    nml.write_text(NML, encoding="utf-8")
    stem = tmp_path / "Music" / "Traktor" / "Stems" / "001" / "AAA.stem.mp4"
    stem.parent.mkdir(parents=True)
    stem.write_bytes(b"x" * 2048)
    log = nml.parent / "Logs" / "Traktor.log"
    log.parent.mkdir()
    log.write_text(f'2026-02-26 22:33:58 - Finished stem separation job for track "One" - result: successful: '
                   f'stem file stored to "{stem}", with offset 0µs, elapsed time: 2m 22s\n', encoding="utf-8")
    return nml, stem


def test_window_shows_the_status_and_selecting_a_playlist_does_not_loop(tk_window, setup):
    """Regression: refreshing the playlist list used to re-select the row, which fired the selection event again, forever."""
    nml, _stem = setup
    ui = tool.StemsUI(tk_window, str(nml))

    assert "3 tracks" in ui.headline_var.get() and "2 linked" in ui.headline_var.get() and "1 stem files" in ui.headline_var.get()
    assert "Linked, but no stem file found: 1" in ui.detail_var.get()          # 'Two'
    assert [ui.notebook.tab(t, "text").strip() for t in ui.notebook.tabs()] == ["1  Create stems", "2  Re-link", "3  Clean up"]

    calls, original = [], ui._show_tracks

    def counted():
        calls.append(1)
        if len(calls) > 20:                                    # the bug is back: break the loop so the test can fail
            ui.playlist_tree.unbind("<<TreeviewSelect>>")
        original()

    ui._show_tracks = counted
    ui.playlist_tree.selection_set("0")
    tk_window.update()
    assert len(calls) <= 2
    plans = [ui.track_tree.item(i, "values")[3] for i in ui.track_tree.get_children()]
    assert plans == ["already has stems", "already has stems", "create stems"]

    ui.skip_var.set(False)
    ui._show_tracks()
    assert ui.playlist_tree.selection() == ("0",)
    assert ui.playlist_tree.item("0", "values")[2] in (3, "3")


def test_remove_everything_takes_the_links_off_before_deleting_the_files(setup):
    nml, stem = setup
    seen = []
    real_delete = tool.delete_stem_files

    def spying_delete(files, permanent=False, log=None, **kw):
        seen.append(stems.scan_stem_flags(nml.read_bytes()).flagged)      # links left at the moment files are deleted
        return real_delete(files, permanent=permanent, log=lambda _m: None)

    lines = []
    tool.delete_stem_files = spying_delete
    try:
        message = tool.unlink_everything_and_delete(str(nml), permanent=True, log=lines.append)
    finally:
        tool.delete_stem_files = real_delete
    assert seen == [0]
    assert not stem.exists() and not stem.parent.exists()
    assert stems.scan_stem_flags(nml.read_bytes()).flagged == 0
    assert "Removed 2" in message and "Deleted 1 stem files" in message


def test_a_failure_removing_the_links_stops_before_any_file_is_touched(setup, tmp_path):
    nml, stem = setup
    nml.write_text("not a collection", encoding="utf-8")
    with pytest.raises(ValueError):
        tool.unlink_everything_and_delete(str(nml), permanent=True, log=lambda _m: None)
    assert stem.exists()


def run_cli(*argv):
    parser = tool.build_parser()
    args = parser.parse_args(list(argv))
    return args.run(args)


def test_cli_status_and_previews_change_nothing(setup, capsys):
    nml, stem = setup
    before = nml.read_bytes()
    assert run_cli("--collection", str(nml), "status") == 0
    out = capsys.readouterr().out
    assert "3 tracks" in out and "1 stem files" in out and "stem folder:" in out

    assert run_cli("--collection", str(nml), "unlink") == 0               # default: only links without a stem file
    assert "1 stem links without a stem file would be removed" in capsys.readouterr().out
    assert run_cli("--collection", str(nml), "unlink", "--all") == 0
    assert "2 stem links would be removed" in capsys.readouterr().out
    assert run_cli("--collection", str(nml), "delete") == 0
    assert "would be deleted to the Trash" in capsys.readouterr().out
    assert run_cli("--collection", str(nml), "clean", "--permanent") == 0
    assert "would be deleted permanently" in capsys.readouterr().out

    assert nml.read_bytes() == before and stem.exists()
    assert not list(nml.parent.glob("*.bak-*"))


def test_cli_apply_does_the_work_and_refuses_while_traktor_is_open(setup, capsys, monkeypatch):
    nml, stem = setup
    monkeypatch.setattr(tool, "running_traktor", lambda: ["Traktor Pro 4"])
    assert run_cli("--collection", str(nml), "delete", "--permanent", "--apply") == 1
    assert "Close Traktor first" in capsys.readouterr().out and stem.exists()

    monkeypatch.setattr(tool, "running_traktor", lambda: [])
    assert run_cli("--collection", str(nml), "unlink", "--apply") == 0     # only the broken link ('Two')
    flags = {t.title: t.flags for t in stems.read_tracks(nml.read_bytes())}
    assert flags == {"One": 76, "Two": 12, "Three": 12}

    assert run_cli("--collection", str(nml), "clean", "--permanent", "--apply") == 0
    assert stems.scan_stem_flags(nml.read_bytes()).flagged == 0 and not stem.exists()


def test_cli_lists_playlists_and_previews_a_create_run(setup, capsys):
    nml, _stem = setup
    assert run_cli("--collection", str(nml), "playlists") == 0
    assert "Warmup  (3 tracks, 1 need stems)" in capsys.readouterr().out
    assert run_cli("--collection", str(nml), "create", "Warmup") == 0
    out = capsys.readouterr().out
    assert "1 of 3 tracks need stems" in out and "Nothing sent to Traktor" in out
    assert run_cli("--collection", str(nml), "create", "Nope") == 1
