"""Tests for the Traktor playlist import comparison and export discovery."""

import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import PipelineScript_Audio_TraktorPlaylistSync as tps  # noqa: E402

SRC = tps.MachineProfile(name="pc", volume="C:", dir_prefix="/:Music/:")
DST = tps.MachineProfile(name="laptop", volume="D:", dir_prefix="/:DJ/:")


def node(keys, name="Set", kind="PLAYLIST"):
    entries = "".join(f'<ENTRY><PRIMARYKEY TYPE="TRACK" KEY="{k}"></PRIMARYKEY></ENTRY>' for k in keys)
    el = ET.fromstring(
        f'<NODE TYPE="{kind}" NAME="{name}"><PLAYLIST ENTRIES="{len(keys)}" TYPE="LIST">{entries}</PLAYLIST></NODE>'
    )
    return tps.PlaylistNode((), name, kind, el)


def src_key(name):
    return f"C:/:Music/:{name}"


def dst_key(name):
    return f"D:/:DJ/:{name}"


def test_new_when_no_existing():
    assert tps.compare_playlist(node([src_key("a")]), None, SRC, DST).status == "new"


def test_identical_after_path_rewrite():
    d = tps.compare_playlist(node([src_key("a"), src_key("b")]), node([dst_key("a"), dst_key("b")]), SRC, DST)
    assert d.status == "identical"


def test_changed_counts_added_and_removed():
    d = tps.compare_playlist(node([src_key("a"), src_key("c")]), node([dst_key("a"), dst_key("b")]), SRC, DST)
    assert (d.status, d.added, d.removed) == ("changed", 1, 1)
    assert d.label == "Changed (+1 / -1)"


def test_reordered():
    d = tps.compare_playlist(node([src_key("b"), src_key("a")]), node([dst_key("a"), dst_key("b")]), SRC, DST)
    assert d.status == "reordered"


def test_tracks_outside_source_library_are_ignored():
    d = tps.compare_playlist(node([src_key("a"), "E:/:Elsewhere/:x"]), node([dst_key("a")]), SRC, DST)
    assert d.status == "identical"


def test_copy_node_with_name_leaves_original():
    original = node([src_key("a")], name="Set")
    copy_ = tps.copy_node_with_name(original, "Set (from pc)")
    assert copy_.element.get("NAME") == "Set (from pc)"
    assert original.element.get("NAME") == "Set"


def test_find_latest_export_searches_subfolders(tmp_path):
    old = tmp_path / "PlaylistSync_old.nml"
    sub = tmp_path / "Traktor" / "PlaylistSync"
    sub.mkdir(parents=True)
    new = sub / "PlaylistSync_new.nml"
    old.write_text("x")
    new.write_text("x")
    os.utime(old, (1, 1))
    assert tps.find_latest_export(["", str(tmp_path / "missing"), str(tmp_path)]) == str(new)
    assert tps.find_latest_export([str(tmp_path / "empty")]) is None
