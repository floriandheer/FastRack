#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Write_OpenObsidian.py
Author: Florian Dheer
Description: Launch Obsidian (reopens the last-used vault).
"""

import sys
import subprocess

from shared_logging import get_logger, setup_logging as setup_shared_logging
from workstation_apps import load_apps, resolve_exe_path

logger = get_logger("write_open_obsidian")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200


def main() -> int:
    setup_shared_logging("write_open_obsidian")
    app = next((a for a in load_apps() if a.name == "Obsidian"), None)
    exe = resolve_exe_path(app) if app else None
    if not exe:
        print("Obsidian isn't installed (or couldn't be located). "
              "Install it via Software Launcher / winget.", file=sys.stderr)
        return 1

    subprocess.Popen([exe], creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                      close_fds=True)
    print("Obsidian launched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
