#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Data_OpenSyncthing.py
Author: Florian Dheer
Description: Open the Syncthing web UI, starting Syncthing first (hidden, in
the background) when it isn't running yet.

Finding syncthing.exe, in order:
  1. the path remembered from an earlier run (appdata syncthing_launcher.json)
  2. the Syncthing entry in setup_config.json (PATH / winget link)
  3. the exe of an already-running syncthing process
  4. ask once with a file picker, then remember the choice
"""

import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
import webbrowser
import xml.etree.ElementTree as ET

from shared_appdata import get_appdata_path
from shared_logging import get_logger, setup_logging as setup_shared_logging
from workstation_apps import load_apps, resolve_exe_path

logger = get_logger("data_open_syncthing")

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
SETTINGS_FILE = os.path.join(str(get_appdata_path()), "syncthing_launcher.json")
START_TIMEOUT = 20  # seconds to wait for the web UI after starting Syncthing


def _load_settings() -> dict:
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_exe(exe: str) -> None:
    settings = _load_settings()
    if settings.get("exe") == exe:
        return
    settings["exe"] = exe
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
    except OSError as exc:
        logger.warning(f"Couldn't remember Syncthing path: {exc}")


def gui_url() -> str:
    """Web UI address from Syncthing's own config.xml (default 127.0.0.1:8384)."""
    address, tls = "127.0.0.1:8384", False
    config = os.path.expandvars(r"%LOCALAPPDATA%\Syncthing\config.xml")
    try:
        gui = ET.parse(config).getroot().find("gui")
        if gui is not None:
            address = (gui.findtext("address") or address).strip()
            tls = gui.get("tls", "false").lower() == "true"
    except (OSError, ET.ParseError):
        pass
    host, _, port = address.rpartition(":")
    if host in ("", "0.0.0.0", "[::]", "::"):
        host = "127.0.0.1"
    return f"{'https' if tls else 'http'}://{host}:{port}/"


def is_running(url: str) -> bool:
    """True when the web UI answers (self-signed TLS on localhost is fine)."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url + "rest/noauth/health", timeout=2, context=context) as resp:
            return resp.status == 200
    except Exception:
        return False


def _running_exe():
    """Path of a running syncthing.exe, if any (Windows only)."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process syncthing -ErrorAction SilentlyContinue | Select-Object -First 1).Path"],
            capture_output=True, text=True, timeout=10, creationflags=CREATE_NO_WINDOW,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out if out and os.path.isfile(out) else None


def find_exe():
    saved = _load_settings().get("exe")
    if saved and os.path.isfile(saved):
        return saved
    app = next((a for a in load_apps() if a.name == "Syncthing"), None)
    exe = (resolve_exe_path(app) if app else None) or _running_exe()
    if exe:
        _save_exe(exe)
        return exe
    return None


def ask_for_exe():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    exe = filedialog.askopenfilename(
        title="Where is syncthing.exe?",
        filetypes=[("Syncthing", "syncthing.exe"), ("Programs", "*.exe")],
    )
    root.destroy()
    if exe and os.path.isfile(exe):
        _save_exe(exe)
        return exe
    return None


def main() -> int:
    setup_shared_logging("data_open_syncthing")
    url = gui_url()

    if is_running(url):
        find_exe()  # learn the exe path while it's running, for next time
        webbrowser.open(url)
        print(f"Syncthing is running - opened {url}")
        return 0

    exe = find_exe() or ask_for_exe()
    if not exe:
        print("Couldn't find syncthing.exe. Install it via Software Launcher / winget, "
              "or pick the exe when asked.", file=sys.stderr)
        return 1

    subprocess.Popen(
        [exe, "serve", "--no-browser"],
        cwd=os.path.dirname(exe),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP, close_fds=True,
    )
    print(f"Starting Syncthing ({exe})...")

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if is_running(url):
            webbrowser.open(url)
            print(f"Syncthing started - opened {url}")
            return 0
        time.sleep(0.5)

    print(f"Syncthing was started but its web UI didn't answer at {url} "
          f"within {START_TIMEOUT}s.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
