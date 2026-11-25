"""The Data Manager's editable data map: seeding, normalization, saving,
cascading deletes, setup-gap warnings, and that the panel builds."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

import shared_data_map as dm  # noqa: E402
from ui_pipeline_categories import resolve_tool_ref, all_tool_refs  # noqa: E402


def test_default_map_seeds_from_categories_and_tools_resolve():
    data = dm.default_map()
    keys = {d["key"] for d in data["datasets"]}
    assert {"musicbee", "obsidian", "traktor", "resolume", "kiosk", "kodi"} <= keys
    for ds in data["datasets"]:
        for ref in ds["tools"]:
            assert resolve_tool_ref(ref) is not None, (ds["key"], ref)
    for t in data["transports"]:
        if t["tool"]:
            assert resolve_tool_ref(t["tool"]) is not None
    music = dm.find(data["datasets"], "musicbee")
    assert dm.place_at(music, "workstation")["role"] == "master"


def test_missing_file_gives_default_and_save_roundtrips(tmp_path):
    path = tmp_path / "data_map.json"
    data = dm.load(path)
    assert data["datasets"]
    data["locations"][0]["notes"] = "Main PC, 192.168.1.10"
    dm.save(data, path)
    again = dm.load(path)
    assert again["locations"][0]["notes"] == "Main PC, 192.168.1.10"
    assert not list(tmp_path.glob(".data_map_*"))


def test_corrupt_file_is_reported_not_overwritten(tmp_path):
    path = tmp_path / "data_map.json"
    path.write_text("{not json", encoding="utf-8")
    data = dm.load(path)
    assert "_load_error" in data
    assert path.read_text(encoding="utf-8") == "{not json"


def test_normalize_drops_dangling_references_and_dedupes_keys():
    data = dm.normalize({
        "locations": [{"key": "pc", "name": "PC"}, {"key": "pc", "name": "PC 2"}],
        "transports": [{"key": "st", "name": "Syncthing"}],
        "datasets": [{"name": "Notes", "places": [
            {"location": "pc", "role": "nonsense", "transport": "gone"},
            {"location": "pc", "role": "backup"},
            {"location": "nowhere", "role": "master"},
        ]}],
    })
    assert [l["key"] for l in data["locations"]] == ["pc", "pc_2"]
    places = data["datasets"][0]["places"]
    assert places == [{"location": "pc", "role": "live", "transport": "", "path": "", "notes": ""}]
    assert data["datasets"][0]["key"] == "notes"


def test_removing_location_and_transport_cascades():
    data = dm.default_map()
    dm.remove_transport(data, "onedrive")
    music = dm.find(data["datasets"], "musicbee")
    assert dm.place_at(music, "cloud")["transport"] == ""
    dm.remove_location(data, "cloud")
    assert dm.place_at(music, "cloud") is None
    assert all(l["key"] != "cloud" for l in data["locations"])


def test_warnings():
    assert dm.warnings_for({"places": []})
    ok = {"places": [
        {"location": "pc", "role": "master", "transport": "", "path": "", "notes": ""},
        {"location": "nas", "role": "backup", "transport": "rb", "path": "", "notes": ""},
    ]}
    assert dm.warnings_for(ok) == []
    live_only = {"places": [{"location": "pc", "role": "live", "transport": "st", "path": "", "notes": ""}]}
    assert any("deletions" in w for w in dm.warnings_for(live_only))
    central = {"places": [{"location": "nas", "role": "server", "transport": "", "path": "", "notes": ""},
                          {"location": "pc", "role": "client", "transport": "", "path": "", "notes": ""}]}
    assert dm.warnings_for(central) == ["No backup"]


def test_all_tool_refs_resolve():
    refs = all_tool_refs()
    assert refs
    for ref, label in refs:
        assert resolve_tool_ref(ref) is not None, label


def test_panel_builds_and_saves(tk_window, tmp_path):
    from ui_data_map_panel import DataMapPanel

    rows = []
    path = tmp_path / "data_map.json"
    panel = DataMapPanel(tk_window, lambda parent, *a: rows.append(a), resolve_tool_ref,
                         map_file=path)
    panel.pack()
    panel.update_idletasks()
    assert rows, "selected dataset's tools are drawn"
    ds = panel.data["datasets"][0]
    dm.set_place(ds, {"role": "backup", "transport": "", "path": "", "notes": "x"}, "nas")
    panel._save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert dm.place_at(saved["datasets"][0], "nas")["notes"] == "x"
    panel.destroy()
