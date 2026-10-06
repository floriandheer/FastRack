"""The out/in switch lock for the Traktor sync tools (saved in each tool's own settings)."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

from shared_mode_lock import filter_mode_change  # noqa: E402

TOOLS = [
    "PipelineScript_Audio_TraktorSync",
    "PipelineScript_Audio_TraktorPlaylistSync",
    "PipelineScript_Audio_TraktorDriveSync",
]


def test_filter_mode_change():
    assert filter_mode_change(SimpleNamespace(mode_locked=False), {"last_mode": "import"}) == {"last_mode": "import"}
    assert filter_mode_change(SimpleNamespace(mode_locked=True), {"last_mode": "import", "x": 1}) == {"x": 1}
    # Locking and changing in one call: the lock wins. Unlocking and changing: the change goes through.
    assert filter_mode_change(SimpleNamespace(mode_locked=False), {"mode_locked": True, "last_mode": "import"}) == {
        "mode_locked": True}
    assert filter_mode_change(SimpleNamespace(mode_locked=True), {"mode_locked": False, "last_mode": "import"}) == {
        "mode_locked": False, "last_mode": "import"}


@pytest.mark.parametrize("module", TOOLS)
def test_locked_tool_keeps_its_mode(tmp_path, module):
    mod = __import__(module)
    path = str(tmp_path / "cfg.json")
    config = mod.ConfigManager(path)
    config.update_settings(last_mode="export")
    config.update_settings(mode_locked=True)

    config.update_settings(last_mode="import")  # e.g. the hub switch, or running import in the window
    assert mod.ConfigManager(path).settings.last_mode == "export"

    config.update_settings(mode_locked=False)
    config.update_settings(last_mode="import")
    assert mod.ConfigManager(path).settings.last_mode == "import"


@pytest.mark.parametrize("module", TOOLS)
def test_hub_switch_respects_the_lock(tmp_path, monkeypatch, module):
    import fastrack_hub as hub

    mod = __import__(module)
    path = str(tmp_path / "cfg.json")
    real_config = mod.ConfigManager
    monkeypatch.setattr(mod, "ConfigManager", lambda: real_config(path))
    cls = next(v for v in vars(hub).values() if isinstance(v, type) and hasattr(v, "_set_traktor_mode"))
    hub_like = object.__new__(cls)

    assert cls._set_traktor_mode(hub_like, module, "import") is True
    assert cls._get_traktor_mode(hub_like, module) == "import"

    real_config(path).update_settings(mode_locked=True)
    assert cls._is_mode_locked(hub_like, module) is True
    assert cls._set_traktor_mode(hub_like, module, "export") is False
    assert cls._get_traktor_mode(hub_like, module) == "import"


def test_drive_sync_window_saves_the_lock(tmp_path, monkeypatch, tk_window):
    import PipelineScript_Audio_TraktorDriveSync as ds

    path = str(tmp_path / "cfg.json")
    monkeypatch.setattr(ds.ConfigManager.__init__, "__defaults__", (path,))
    root = tk_window
    try:
        ui = ds.DriveSyncUI(root)
        ui.mode_locked.set(True)
        ui.config_manager.update_settings(mode_locked=True)
        ui._save(last_mode="import")  # running import in the window must not move a locked switch
        saved = ds.ConfigManager(path).settings
        assert saved.mode_locked is True and saved.last_mode == ""
    finally:
        pass
