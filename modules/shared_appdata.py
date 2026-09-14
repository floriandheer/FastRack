"""
Shared AppData Path Resolution

Single source of truth for the per-user PipelineManager AppData root that
every module (rack_settings, shared_project_db, sandbox_tag_store,
startup_apps_manager, invoice_manager, the pipeline scripts, install.py,
...) used to derive independently. Several of those copies drifted and
never got a macOS branch added, so on a Mac they'd land in different
folders. Kept dependency-free (stdlib only) so shared_logging - which
rack_settings itself imports - can use it too without a circular import.
"""

import os
import sys
from pathlib import Path


def get_appdata_path() -> Path:
    """Root PipelineManager folder for this platform/user.

    - Windows:       %LOCALAPPDATA%\\PipelineManager
    - macOS:         ~/Library/Application Support/PipelineManager
    - WSL:           the Windows user's AppData\\Local\\PipelineManager,
                     so native Windows runs and WSL runs share one folder
    - Linux (no WSL): ~/.local/share/PipelineManager
    """
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "PipelineManager"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "PipelineManager"
    windows_users = Path("/mnt/c/Users")
    if windows_users.exists():
        username = os.environ.get("USER", "")
        user_path = windows_users / username
        if user_path.exists():
            return user_path / "AppData" / "Local" / "PipelineManager"
    return Path.home() / ".local" / "share" / "PipelineManager"
