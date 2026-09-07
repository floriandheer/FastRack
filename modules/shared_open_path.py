"""
Cross-platform "open this file/folder with the OS default handler" helper.

``os.startfile`` only exists on Windows - calling it unguarded on macOS/Linux
raises AttributeError. This centralizes the three-way branch (win32 / darwin
/ else) that several modules were previously duplicating (and in a couple of
cases, not doing at all).
"""

import os
import sys
import subprocess

from shared_logging import get_logger

logger = get_logger(__name__)


def open_path(path: str) -> bool:
    """Open `path` (file or folder) with the platform's default handler.

    Returns True if the OS call was issued, False if the path doesn't exist
    or the call failed. Never raises.
    """
    if not path or not os.path.exists(path):
        return False
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: Windows-only, guarded by the branch above
        elif sys.platform == "darwin":
            subprocess.call(["open", path])
        else:
            subprocess.call(["xdg-open", path])
        return True
    except Exception as e:
        logger.warning(f"Could not open path {path}: {e}")
        return False
