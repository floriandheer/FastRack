#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Photo_OpenExportIrfanView.py
Author: Florian Dheer
Description: Open the first picture of a photo project's _export folder in
IrfanView (the rest of the folder can then be browsed with the arrow keys).
Headless - no window of its own; problems (no _export folder, no pictures,
IrfanView not installed) are reported in a message box.

Usage:
    PipelineScript_Photo_OpenExportIrfanView.py <project_folder>
"""

import re
import sys
import subprocess
from pathlib import Path
from typing import Optional

from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_message import show_message
from workstation_apps import App, resolve_exe_path

logger = get_logger("photo_open_export")

EXPORT_FOLDER_NAME = "_export"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".gif", ".webp",
    ".heic", ".heif", ".avif", ".psd", ".jxl",
}

# Not on PATH after a normal install, so resolve_exe_path falls through to
# these folders and then to the registry uninstall entry.
IRFANVIEW = App(
    name="IrfanView",
    exe="i_view64",
    detect_paths=[
        r"%ProgramFiles%\IrfanView",
        r"%ProgramFiles(x86)%\IrfanView",
    ],
)


def _find_export_folder(project_folder: str) -> Optional[Path]:
    """Return <project_folder>/_export, matching the name case-insensitively,
    or None when the project has no such folder."""
    base = Path(project_folder)
    if not base.is_dir():
        return None
    try:
        for entry in base.iterdir():
            if entry.is_dir() and entry.name.lower() == EXPORT_FOLDER_NAME:
                return entry
    except OSError as e:
        logger.warning(f"Could not list {base}: {e}")
    return None


def _natural_key(name: str):
    """Sort key that orders IMG_2 before IMG_10, like Explorer does."""
    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name.lower())]


def _first_image(folder: Path) -> Optional[Path]:
    """First picture (by name) directly inside `folder`, or None."""
    try:
        images = [
            e for e in folder.iterdir()
            if e.is_file() and e.suffix.lower() in IMAGE_EXTENSIONS
        ]
    except OSError as e:
        logger.warning(f"Could not list {folder}: {e}")
        return None
    return min(images, key=lambda e: _natural_key(e.name), default=None)


def main():
    setup_shared_logging("photo_open_export")

    if len(sys.argv) < 2:
        logger.error("Usage: PipelineScript_Photo_OpenExportIrfanView.py <project_folder>")
        sys.exit(1)

    project_folder = sys.argv[1]
    export_folder = _find_export_folder(project_folder)
    if export_folder is None:
        logger.error(f"No {EXPORT_FOLDER_NAME} folder in {project_folder}")
        show_message("Open _export", f"This project has no {EXPORT_FOLDER_NAME} folder:\n{project_folder}")
        sys.exit(1)

    first_image = _first_image(export_folder)
    if first_image is None:
        logger.info(f"No pictures in {export_folder}")
        show_message("Open _export", f"The {EXPORT_FOLDER_NAME} folder has no pictures:\n{export_folder}", error=False)
        return

    exe = resolve_exe_path(IRFANVIEW)
    if not exe:
        logger.error("IrfanView executable not found")
        show_message(
            "Open _export",
            "IrfanView was not found.\n\nInstall it from the Workstation Apps tab in Settings "
            "(or winget install IrfanSkiljan.IrfanView).",
        )
        sys.exit(1)

    try:
        subprocess.Popen([exe, str(first_image)])
        logger.info(f"Opened {first_image} in IrfanView ({exe})")
    except OSError as e:
        logger.error(f"Failed to launch IrfanView: {e}")
        show_message("Open _export", f"Could not start IrfanView:\n{e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
