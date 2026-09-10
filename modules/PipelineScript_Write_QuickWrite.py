#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Write_QuickWrite.py
Author: Florian Dheer
Description: Create a new dated document in the Sandbox Write folder and open
it in LibreOffice Writer - capture a quick idea with zero setup.
"""

import os
import sys
import datetime
import subprocess

from shared_logging import get_logger, setup_logging as setup_shared_logging
from rack_settings import get_rack_settings, join_native_path
from workstation_apps import load_apps, resolve_exe_path
from shared_open_path import open_path

logger = get_logger("write_quick_write")

DETACHED_PROCESS = 0x00000008 if sys.platform == "win32" else 0
CREATE_NEW_PROCESS_GROUP = 0x00000200 if sys.platform == "win32" else 0

# Minimal valid empty RTF - LibreOffice Writer opens it directly, no
# ODF/zip machinery needed just to get a blank page on disk.
BLANK_RTF = r"{\rtf1\ansi\deff0}"


def main() -> int:
    setup_shared_logging("write_quick_write")
    settings = get_rack_settings()
    write_dir = join_native_path(settings.get_work_drive(), "_Sandbox", "Write")
    os.makedirs(write_dir, exist_ok=True)

    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H-%M-%S")
    file_path = os.path.join(write_dir, f"{timestamp}.rtf")
    with open(file_path, "w", encoding="ascii") as f:
        f.write(BLANK_RTF)
    logger.info(f"Created quick-write file: {file_path}")

    app = next((a for a in load_apps() if a.name == "LibreOffice"), None)
    exe = resolve_exe_path(app) if app else None
    if not exe:
        print(f"Created {file_path} - LibreOffice wasn't found, "
              "opening with the default app instead.", file=sys.stderr)
        open_path(file_path)
        return 0

    subprocess.Popen([exe, file_path], creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                      close_fds=True)
    print(f"Opened {file_path} in LibreOffice Writer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
