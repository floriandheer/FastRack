"""Publish checks whether the VitePress export is older than the source."""
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

import PipelineScript_Web_PublishStatic as ps  # noqa: E402


def _touch(path: Path, mtime: float, text: str = "x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _site(tmp_path: Path, scripts=None, is_wordpress=False):
    dev = tmp_path / "02_Development"
    export = tmp_path / "03_Publish" / "site"
    dev.mkdir(parents=True)
    (dev / "package.json").write_text(
        json.dumps({"scripts": scripts if scripts is not None else {"docs:build": "vitepress build"}}),
        encoding="utf-8")
    cfg = {"dev_dir": str(dev), "export_dir": str(export), "is_wordpress": is_wordpress}
    return cfg, dev, export


def test_vitepress_site_has_build_dir(tmp_path):
    cfg, dev, _ = _site(tmp_path)
    assert ps.find_build_dir(cfg) == str(dev)


def test_wordpress_and_scriptless_sites_have_no_build_step(tmp_path):
    cfg, _, _ = _site(tmp_path, is_wordpress=True)
    assert ps.find_build_dir(cfg) is None
    assert ps.PublishWorkflow.steps_for(cfg)[0] == "Validate"

    cfg2, _, _ = _site(tmp_path / "b", scripts={"docs:dev": "vitepress dev"})
    assert ps.find_build_dir(cfg2) is None


def test_build_step_comes_first(tmp_path):
    cfg, _, _ = _site(tmp_path)
    assert ps.PublishWorkflow.steps_for(cfg)[:2] == ["Check build is up to date", "Validate"]


def test_missing_export_is_stale(tmp_path):
    _, dev, export = _site(tmp_path)
    stale, reason = ps.check_export_freshness(str(dev), str(export))
    assert stale and "no complete export" in reason


def test_edit_after_build_is_stale(tmp_path):
    _, dev, export = _site(tmp_path)
    now = time.time()
    os.utime(dev / "package.json", (now - 3600, now - 3600))
    _touch(export / "index.html", now - 600)
    _touch(dev / "en" / "page.md", now - 60)
    stale, reason = ps.check_export_freshness(str(dev), str(export))
    assert stale
    assert os.path.join("en", "page.md") in reason


def test_build_after_edit_is_fresh(tmp_path):
    _, dev, export = _site(tmp_path)
    now = time.time()
    os.utime(dev / "package.json", (now - 3600, now - 3600))
    _touch(dev / "index.md", now - 600)
    _touch(export / "index.html", now - 60)
    stale, _ = ps.check_export_freshness(str(dev), str(export))
    assert not stale


def test_noise_folders_do_not_count_as_edits(tmp_path):
    _, dev, export = _site(tmp_path)
    now = time.time()
    os.utime(dev / "package.json", (now - 3600, now - 3600))
    _touch(export / "index.html", now - 600)
    for noise in ("node_modules/x.js", ".obsidian/workspace.json", ".git/index",
                  ".vitepress/cache/deps.json", "brainii/data/pages/a.txt"):
        _touch(dev / noise, now)
    stale, reason = ps.check_export_freshness(str(dev), str(export))
    assert not stale, reason


def test_export_inside_dev_folder_is_ignored(tmp_path):
    _, dev, _ = _site(tmp_path)
    export = dev / "out"
    now = time.time()
    os.utime(dev / "package.json", (now - 3600, now - 3600))
    _touch(export / "index.html", now - 60)
    _touch(export / "assets" / "app.js", now - 60)
    stale, _ = ps.check_export_freshness(str(dev), str(export))
    assert not stale
