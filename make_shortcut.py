#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate a desktop launcher for fastrak_hub.py next to this script: a
Windows .lnk shortcut, or a minimal macOS .app bundle.

Resolves all paths relative to the script's own location, so it works on any
PC where the floriandheer repo is cloned. Run once after setup; pin the
result to the taskbar/Dock.
"""

import os
import sys
import shutil
import subprocess


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TARGET_SCRIPT = os.path.join(SCRIPT_DIR, "fastrak_hub.py")
ICON_PATH = os.path.join(SCRIPT_DIR, "assets", "Favicon_FlorianDheer.ico")
SHORTCUT_PATH = os.path.join(SCRIPT_DIR, "Fastrak.lnk")
APP_BUNDLE_PATH = os.path.join(SCRIPT_DIR, "Fastrak.app")
APP_USER_MODEL_ID = "floriandheer.fastrak"


def find_pythonw():
    """Locate pythonw.exe in the active interpreter's folder, then on PATH."""
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(candidate):
        return candidate
    on_path = shutil.which("pythonw.exe") or shutil.which("pythonw")
    if on_path:
        return on_path
    raise FileNotFoundError("pythonw.exe not found — install Python or add it to PATH.")


def build_shortcut(pythonw_exe: str) -> None:
    """Invoke PowerShell's WScript.Shell to write the .lnk."""
    if not os.path.exists(TARGET_SCRIPT):
        raise FileNotFoundError(f"Missing target script: {TARGET_SCRIPT}")

    icon_line = (
        f'$s.IconLocation = "{ICON_PATH},0";' if os.path.exists(ICON_PATH) else ""
    )

    ps = (
        f'$w = New-Object -ComObject WScript.Shell;'
        f'$s = $w.CreateShortcut("{SHORTCUT_PATH}");'
        f'$s.TargetPath = "{pythonw_exe}";'
        f'$s.Arguments = \'"{TARGET_SCRIPT}"\';'
        f'$s.WorkingDirectory = "{SCRIPT_DIR}";'
        f'{icon_line}'
        f'$s.Description = "Fastrak Pipeline Hub";'
        f'$s.WindowStyle = 1;'
        f'$s.Save();'
    )

    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps],
        check=True,
    )

    # Stamp the AppUserModelID onto the .lnk so the pinned shortcut groups
    # under the same taskbar slot as the running window.
    stamp = (
        f'$bytes = [System.IO.File]::ReadAllBytes("{SHORTCUT_PATH}");'
        f'[System.IO.File]::WriteAllBytes("{SHORTCUT_PATH}", $bytes);'
        f'$shell = New-Object -ComObject Shell.Application;'
        f'$folder = $shell.Namespace("{SCRIPT_DIR}");'
        f'$item = $folder.ParseName("Fastrak.lnk");'
    )
    # The IPropertyStore stamping requires a helper; skip silently if not available.
    # (Pinning still works without it; AppUserModelID is set inside fastrak_hub.py.)


def build_mac_app() -> None:
    """Build a minimal .app bundle that launches fastrak_hub.py.

    No visible Terminal window - matches the Windows .lnk's "just opens
    the GUI" experience, and can be dragged to the Dock or opened from
    Finder/Spotlight like any other Mac app.
    """
    if not os.path.exists(TARGET_SCRIPT):
        raise FileNotFoundError(f"Missing target script: {TARGET_SCRIPT}")

    contents_dir = os.path.join(APP_BUNDLE_PATH, "Contents")
    macos_dir = os.path.join(contents_dir, "MacOS")
    os.makedirs(macos_dir, exist_ok=True)

    # sys.executable — the interpreter actually running this script — not a
    # fresh `which python3` lookup, so the launcher uses the exact
    # environment the user already installed requirements.txt into.
    launcher_path = os.path.join(macos_dir, "Fastrak")
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(
            "#!/bin/bash\n"
            f'cd "{SCRIPT_DIR}"\n'
            f'exec "{sys.executable}" "{TARGET_SCRIPT}"\n'
        )
    os.chmod(launcher_path, 0o755)

    plist_path = os.path.join(contents_dir, "Info.plist")
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
            '<plist version="1.0">\n'
            '<dict>\n'
            '    <key>CFBundleName</key>\n'
            '    <string>Fastrak</string>\n'
            '    <key>CFBundleExecutable</key>\n'
            '    <string>Fastrak</string>\n'
            '    <key>CFBundleIdentifier</key>\n'
            '    <string>com.floriandheer.fastrak</string>\n'
            '    <key>CFBundlePackageType</key>\n'
            '    <string>APPL</string>\n'
            '    <key>CFBundleShortVersionString</key>\n'
            '    <string>1.0</string>\n'
            '    <key>NSHighResolutionCapable</key>\n'
            '    <true/>\n'
            '</dict>\n'
            '</plist>\n'
        )
    # No .icns yet — Pillow can't write that format and there's no macOS
    # box handy to run iconutil, so this ships with the generic app icon
    # for now. A real icon is a follow-up, not a blocker.


def main() -> int:
    try:
        if sys.platform == "win32":
            pythonw_exe = find_pythonw()
            build_shortcut(pythonw_exe)
            print(f"Created: {SHORTCUT_PATH}")
            print(f"  Target: {pythonw_exe} \"{TARGET_SCRIPT}\"")
            print(f"  Icon:   {ICON_PATH if os.path.exists(ICON_PATH) else '(default)'}")
            print("Right-click the .lnk and choose 'Pin to taskbar' or 'Pin to Start'.")
        elif sys.platform == "darwin":
            build_mac_app()
            print(f"Created: {APP_BUNDLE_PATH}")
            print("Drag Fastrak.app to the Dock, or double-click it from Finder.")
            print("First launch: right-click -> Open (it's unsigned, so Gatekeeper")
            print("needs one-time approval - after that, double-click works normally).")
        else:
            print("No desktop-shortcut generator for this platform yet.", file=sys.stderr)
            return 1
    except (FileNotFoundError, subprocess.CalledProcessError, OSError) as exc:
        print(f"Failed to create shortcut: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
