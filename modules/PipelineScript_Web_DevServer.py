#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PipelineScript_Web_DevServer.py
Author: Florian Dheer
Description: Start the VitePress dev server (or run a production build) for a
non-WordPress web project. Spawns a new console window so the user can see
output and stop the server with Ctrl+C; for dev mode, also opens the default
VitePress URL in the browser.

Usage:
    PipelineScript_Web_DevServer.py <project_folder> [--build]

The script looks for <project_folder>/02_Development/package.json and runs
`npm run docs:dev` or `npm run docs:build` in that directory.
"""

import os
import sys
import json
import time
import shutil
import threading
import subprocess
import webbrowser
from pathlib import Path
from typing import Optional

from shared_logging import get_logger, setup_logging as setup_shared_logging
from rak_settings import get_rak_settings

logger = get_logger("web_devserver")

VITEPRESS_DEFAULT_URL = "http://localhost:5173"
BROWSER_OPEN_DELAY_SECONDS = 3.0


def _to_active_base_path(folder: str) -> str:
    """Reverse the work-drive subst mapping: if `folder` lives under the work
    drive (e.g. ``I:\\Web\\...``), rewrite it to the underlying active base
    (e.g. ``D:\\_work\\Active\\Web\\...``). npm/pnpm misbehave on subst drives
    when node_modules contains symlinks, so we always run from the real path."""
    try:
        return get_rak_settings().to_active_base_path(folder)
    except Exception:
        return folder


def _find_dev_folder(project_folder: str) -> Optional[Path]:
    """Return the 02_Development folder if it contains a package.json with a
    docs:dev script, otherwise None."""
    base = Path(project_folder)
    if not base.is_dir():
        return None
    for sub in ("02_Development", "02_development"):
        dev = base / sub
        pkg = dev / "package.json"
        if pkg.is_file():
            try:
                with open(pkg, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if "docs:dev" in (data.get("scripts") or {}):
                    return dev
            except Exception as e:
                logger.warning(f"Could not parse {pkg}: {e}")
    return None


def _open_browser_later(url: str, delay: float):
    """Open `url` in the default browser after `delay` seconds. Runs in a
    background thread so we don't block the console launch."""
    def _open():
        time.sleep(delay)
        try:
            webbrowser.open(url)
            logger.info(f"Opened browser at {url}")
        except Exception as e:
            logger.error(f"Failed to open browser: {e}")

    threading.Thread(target=_open, daemon=True).start()


def _launch_console(dev_folder: Path, npm_script: str, keep_open: bool):
    """Launch `npm run <npm_script>` in a new console window rooted at
    dev_folder. The window stays open after the command exits when
    keep_open is True (so build errors / dev shutdown messages remain
    visible)."""
    title = f"{dev_folder.parent.name} — npm run {npm_script}"

    if sys.platform == "win32":
        flag = "/k" if keep_open else "/c"
        cmd = f'start "{title}" cmd {flag} "cd /d "{dev_folder}" && npm run {npm_script}"'
        logger.info(f"Launching: {cmd}")
        subprocess.Popen(cmd, shell=True)
        return

    # POSIX fallback — best-effort, primary target is Windows.
    npm = shutil.which("npm") or "npm"
    subprocess.Popen([npm, "run", npm_script], cwd=str(dev_folder))


def main():
    setup_shared_logging("web_devserver")

    if len(sys.argv) < 2:
        logger.error("Usage: PipelineScript_Web_DevServer.py <project_folder> [--build]")
        sys.exit(1)

    project_folder = _to_active_base_path(sys.argv[1])
    build_mode = "--build" in sys.argv[2:]
    logger.info(f"Resolved project folder to: {project_folder}")

    dev_folder = _find_dev_folder(project_folder)
    if not dev_folder:
        logger.error(
            f"No 02_Development/package.json with a 'docs:dev' script under {project_folder}"
        )
        sys.exit(1)

    if build_mode:
        logger.info(f"Building site in {dev_folder}")
        _launch_console(dev_folder, "docs:build", keep_open=True)
    else:
        logger.info(f"Starting dev server in {dev_folder}")
        _launch_console(dev_folder, "docs:dev", keep_open=True)
        _open_browser_later(VITEPRESS_DEFAULT_URL, BROWSER_OPEN_DELAY_SECONDS)


if __name__ == "__main__":
    main()
