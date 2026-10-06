"""One-click (direct_run) tasks: hub wiring and the mode announcement shown in their console window."""

import logging
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import pipeline_categories as pc  # noqa: E402
import ui_pipeline_categories as ui  # noqa: E402


def _direct_run_specs():
    found = []

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("scripts", "category_scripts") and isinstance(value, list):
                    found.extend(spec for spec in value if spec.get("direct_run"))
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(pc.CATEGORIES)
    return found


def test_mode_switch_rows_know_their_module():
    """The hub's out/in switch finds a tool's saved mode through entry['module']; without it the switch is inert."""
    specs = [spec for spec in _direct_run_specs() if spec.get("mode_switch")]
    assert specs
    for spec in specs:
        assert ui._script_entry(spec)["module"] == spec["module"]


def test_traktor_tasks_are_one_click_tasks_with_existing_scripts():
    entries = {spec["module"]: ui._script_entry(spec) for spec in _direct_run_specs()}
    assert {"PipelineScript_Audio_TraktorSync", "PipelineScript_Audio_TraktorPlaylistSync",
            "PipelineScript_Audio_TraktorDriveSync"} <= set(entries)
    assert all(entries[m]["mode_switch"] for m in entries if m.startswith("PipelineScript_Audio_Traktor"))
    for entry in entries.values():
        assert entry["direct_run"] is True and os.path.isfile(entry["path"])


@pytest.mark.parametrize("module, last_mode, expected", [
    ("PipelineScript_Audio_TraktorPlaylistSync", "import", "IMPORT"),
    ("PipelineScript_Audio_TraktorPlaylistSync", "export", "EXPORT"),
    ("PipelineScript_Audio_TraktorSync", "import", "IMPORT"),
    ("PipelineScript_Audio_TraktorSync", "", "EXPORT"),
    ("PipelineScript_Audio_TraktorDriveSync", "import", "IMPORT"),
    ("PipelineScript_Audio_TraktorDriveSync", "", "EXPORT"),
])
def test_direct_run_announces_the_mode_it_replays(monkeypatch, caplog, module, last_mode, expected):
    mod = __import__(module)
    monkeypatch.setattr(mod, "ConfigManager", lambda: SimpleNamespace(settings=SimpleNamespace(last_mode=last_mode)))
    for name in ("run_headless_import", "run_headless_export", "run_headless_sync"):
        if hasattr(mod, name):
            monkeypatch.setattr(mod, name, lambda *a, **k: True)
    with caplog.at_level(logging.INFO):
        assert mod.run_headless() is True
    assert any(f"Direct run: {expected}" in r.getMessage() for r in caplog.records)
