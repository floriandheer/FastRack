"""The shipped app catalog (setup_config.json and its template) - Syncthing in the default tools."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import workstation_apps as wa  # noqa: E402

CATALOGS = ["setup_config.json", "setup_config kopie.json.example.json"]


def _entry(catalog):
    data = json.loads((REPO_ROOT / catalog).read_text(encoding="utf-8"))
    return next(a for a in data["workstation_apps"] if a["name"] == "Syncthing")


@pytest.mark.parametrize("catalog", CATALOGS)
def test_syncthing_is_a_default_tool_installed_through_the_official_winget_package(catalog):
    entry = _entry(catalog)
    assert entry["category"] == "General"                        # General is part of every profile
    assert entry["install_method"] == "winget"
    assert entry["winget_id"] == "Syncthing.Syncthing"           # published by The Syncthing Authors
    assert entry["url"] == "https://syncthing.net/downloads/"    # the download page, when winget is not used
    assert entry["exe"] == "syncthing"
    assert "brew_id" not in entry                                # macOS: download page only


@pytest.mark.parametrize("catalog", CATALOGS)
def test_syncthing_uses_winget_on_windows_and_the_download_page_elsewhere(catalog):
    entry = _entry(catalog)
    app = wa.App(name=entry["name"], categories=[entry["category"]], install_method=entry["install_method"],
                 winget_id=entry["winget_id"], exe=entry["exe"], url=entry["url"],
                 detect_paths=entry.get("detect_paths", []))
    assert app.effective_method("win32") == wa.WINGET
    assert app.effective_method("darwin") == wa.MANUAL           # opens app.url
    assert app.effective_method("linux") == wa.MANUAL


def test_every_profile_that_installs_general_gets_syncthing(monkeypatch):
    data = json.loads((REPO_ROOT / "setup_config.json").read_text(encoding="utf-8"))
    general_profiles = [p["name"] for p in data["workstation_profiles"] if "General" in p["categories"]]
    assert {"Office laptop", "Audio rig", "Everything"} <= set(general_profiles)
    assert len(general_profiles) == len(data["workstation_profiles"])        # all of them
