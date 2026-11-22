#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Write_OpenObsidian.py
Author: Florian Dheer
Description: Launch Obsidian (reopens the last-used vault).
"""

import sys

from shared_logging import get_logger, setup_logging as setup_shared_logging
from workstation_apps import launch_app

logger = get_logger("write_open_obsidian")


def main() -> int:
    setup_shared_logging("write_open_obsidian")
    error = launch_app("Obsidian")
    if error:
        print(error, file=sys.stderr)
        return 1
    print("Obsidian launched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
