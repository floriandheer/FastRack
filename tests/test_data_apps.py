"""The Data Manager's synced-software cards must point at real tools."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "modules"))

from pipeline_categories import CATEGORIES  # noqa: E402
from ui_pipeline_categories import data_apps  # noqa: E402


def test_every_app_tool_reference_resolves():
    configured = {app["key"]: len(app["tools"]) for app in CATEGORIES["Data"]["apps"]}
    resolved = {app["key"]: len(app["tools"]) for app in data_apps()}
    assert resolved == configured


def test_syncthing_is_a_tool_not_an_app():
    apps = data_apps()
    assert "syncthing" not in {a["key"] for a in apps}
    obsidian = next(a for a in apps if a["key"] == "obsidian")
    assert "open_syncthing" in {t[1] for t in obsidian["tools"]}


def test_tools_use_menu_keys():
    for app in data_apps():
        for category_key, _script_key, subcat_key, _data in app["tools"]:
            assert category_key == category_key.upper()
            assert subcat_key is None or subcat_key == subcat_key.upper()


def test_launch_names_are_in_the_workstation_catalog():
    import pytest
    from workstation_apps import load_apps

    catalog = {a.name for a in load_apps()}
    if not catalog:
        pytest.skip("no workstation_apps catalog on this machine")
    for app in data_apps():
        assert app["launch"] in catalog, app["key"]
