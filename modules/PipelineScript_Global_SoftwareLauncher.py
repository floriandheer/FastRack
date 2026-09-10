#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Global_SoftwareLauncher.py
Description: Download, update, and launch portable software tools from GitHub releases.
"""

import os
import sys
import json
import zipfile
import datetime
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox
from shared_window_icon import apply_category_icon
from typing import Dict, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError

from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_form_keyboard import FormKeyboardMixin, FORM_COLORS
from rack_settings import RackSettings

logger = get_logger("software_launcher")

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "software_launcher_manifest.json")
GITHUB_API = "https://api.github.com"


def _expand_env(path: str) -> str:
    """Expand environment variables like %APPDATA% and %USERPROFILE%."""
    import re
    result = path
    for var in re.findall(r'%([^%]+)%', path):
        val = os.environ.get(var, '')
        result = result.replace(f'%{var}%', val)
    return os.path.normpath(result)


# ============================================================================
# CORE LOGIC
# ============================================================================

def load_manifest(path: str = MANIFEST_PATH) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_installed_version(tool_cfg: Dict) -> Optional[str]:
    """Read _version.txt from the tool's install_dir."""
    version_file = os.path.join(tool_cfg["install_dir"], "_version.txt")
    if not os.path.isfile(version_file):
        return None
    try:
        with open(version_file, "r", encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return None


def get_latest_release(tool_cfg: Dict) -> Tuple[Optional[str], Optional[str]]:
    """Query GitHub API for the latest release. Returns (tag, asset_url) or (None, None)."""
    repo = tool_cfg["github_repo"]
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    req = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "PipelineLauncher/1.0"})
    try:
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None, None

    tag = data.get("tag_name")
    pattern = tool_cfg["asset_pattern"]
    for asset in data.get("assets", []):
        if pattern in asset.get("name", ""):
            return tag, asset["browser_download_url"]
    return tag, None


def download_and_install(tool_cfg: Dict, asset_url: str, tag: str,
                         progress_cb=None, log_cb=None) -> bool:
    """Download zip asset, extract to install_dir, write _version.txt."""
    install_dir = tool_cfg["install_dir"]
    os.makedirs(install_dir, exist_ok=True)
    zip_path = os.path.join(install_dir, "_download.zip")

    try:
        req = Request(asset_url, headers={"User-Agent": "PipelineLauncher/1.0"})
        with urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 64)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_cb and total:
                        progress_cb(downloaded / total)

        if log_cb:
            log_cb("Extracting...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(install_dir)

        os.remove(zip_path)

        with open(os.path.join(install_dir, "_version.txt"), "w", encoding="utf-8") as f:
            f.write(tag)

        return True
    except Exception as e:
        if log_cb:
            log_cb(f"ERROR: {e}")
        return False


def launch_tool(tool_cfg: Dict) -> bool:
    """Launch the tool's executable detached."""
    exe = os.path.join(tool_cfg["install_dir"], tool_cfg["executable"])
    if not os.path.isfile(exe):
        return False
    CREATE_NEW_PROCESS_GROUP = 0x00000200
    DETACHED_PROCESS = 0x00000008
    subprocess.Popen(
        [exe],
        cwd=tool_cfg["install_dir"],
        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return True


# ============================================================================
# GUI
# ============================================================================

class SoftwareLauncherManager(FormKeyboardMixin):
    """Tkinter GUI for downloading, updating, and launching portable tools."""

    def __init__(self, root, embedded=False, settings=None):
        self.root = root
        self.embedded = embedded
        self.settings = settings or RackSettings()
        self.manifest: Dict = {}
        # tool_key -> {installed, latest, asset_url, status}
        self.tool_info: Dict[str, Dict] = {}

        if not embedded:
            self.root.title("Software Launcher")
            self.root.geometry("900x550")
            self.root.minsize(750, 450)
            self.root.configure(bg=FORM_COLORS["bg"])

        self._load_manifest()
        self._resolve_manifest_paths()
        self._build_form()
        self.root.after(200, self._check_versions)

    # ---- manifest ----
    def _load_manifest(self):
        try:
            self.manifest = load_manifest()
        except Exception as e:
            self.manifest = {}
            logger.error(f"Failed to load manifest: {e}")

    def _resolve_manifest_paths(self):
        """Resolve relative install_dir paths using the launchers base path from settings."""
        base = self.settings.get_launchers_base_path()
        for key, cfg in self.manifest.items():
            install_dir = cfg.get("install_dir", "")
            if not os.path.isabs(install_dir):
                cfg["install_dir"] = os.path.join(base, install_dir)

    # ---- UI ----
    def _build_form(self):
        container = self.root

        # Header
        hdr = tk.Frame(container, bg=FORM_COLORS["accent_dark"], height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Software Launcher", font=("Segoe UI", 13, "bold"),
                 fg=FORM_COLORS["text"], bg=FORM_COLORS["accent_dark"]).pack(side=tk.LEFT, padx=12)

        # Button bar
        btn_bar = tk.Frame(container, bg=FORM_COLORS["bg"])
        btn_bar.pack(fill=tk.X, padx=8, pady=(6, 2))

        btn_style = dict(font=("Segoe UI", 9), bg=FORM_COLORS["bg_input"],
                         fg=FORM_COLORS["text"], activebackground=FORM_COLORS["bg_hover"],
                         activeforeground=FORM_COLORS["text"], bd=0, padx=10, pady=4)

        tk.Button(btn_bar, text="Check Updates", command=self._check_versions, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Install / Update Selected", command=self._install_selected, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Launch Selected", command=self._launch_selected, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Open Folder", command=self._open_folder_selected, **btn_style).pack(side=tk.LEFT, padx=2)

        # Library path row
        self.lib_path_frame = tk.Frame(container, bg=FORM_COLORS["bg"])
        self.lib_path_frame.pack(fill=tk.X, padx=8, pady=(2, 2))
        tk.Label(self.lib_path_frame, text="Library Path:", font=("Segoe UI", 9),
                 fg=FORM_COLORS["text"], bg=FORM_COLORS["bg"]).pack(side=tk.LEFT, padx=(0, 4))
        self.lib_path_var = tk.StringVar()
        self.lib_path_entry = tk.Entry(self.lib_path_frame, textvariable=self.lib_path_var,
                                       state="readonly", readonlybackground=FORM_COLORS["bg_input"],
                                       fg=FORM_COLORS["text"], font=("Consolas", 9), bd=0)
        self.lib_path_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        tk.Button(self.lib_path_frame, text="Copy", command=self._copy_library_path, **btn_style).pack(side=tk.LEFT)
        self.lib_path_frame.pack_forget()

        # Treeview
        tree_frame = tk.Frame(container, bg=FORM_COLORS["bg"])
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        columns = ("tool", "installed", "latest", "status")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                 selectmode="extended", style="Dark.Treeview")
        headings = {"tool": "Tool", "installed": "Installed", "latest": "Latest", "status": "Status"}
        widths = {"tool": 250, "installed": 150, "latest": 150, "status": 200}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=60)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self._configure_tree_style()

        # Log area
        log_frame = tk.LabelFrame(container, text="Log", bg=FORM_COLORS["bg"],
                                  fg=FORM_COLORS["text_dim"], font=("Segoe UI", 9))
        log_frame.pack(fill=tk.X, padx=8, pady=(2, 8))
        self.log_text = tk.Text(log_frame, height=8, bg=FORM_COLORS["bg_input"],
                                fg=FORM_COLORS["text"], insertbackground=FORM_COLORS["text"],
                                font=("Consolas", 9), bd=0, wrap=tk.WORD)
        self.log_text.pack(fill=tk.X, padx=4, pady=4)

    def _configure_tree_style(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Dark.Treeview",
                        background=FORM_COLORS["bg_input"],
                        foreground=FORM_COLORS["text"],
                        fieldbackground=FORM_COLORS["bg_input"],
                        borderwidth=0,
                        font=("Segoe UI", 9))
        style.configure("Dark.Treeview.Heading",
                        background=FORM_COLORS["border"],
                        foreground=FORM_COLORS["text"],
                        font=("Segoe UI", 9, "bold"))
        style.map("Dark.Treeview",
                   background=[("selected", FORM_COLORS["accent_dark"])],
                   foreground=[("selected", FORM_COLORS["text"])])

    # ---- library path ----
    def _on_tree_select(self, event=None):
        keys = self._selected_keys()
        if len(keys) == 1:
            cfg = self.manifest.get(keys[0], {})
            lib_path = cfg.get("library_path", "")
            if lib_path:
                lib_path = _expand_env(lib_path)
        else:
            lib_path = ""
        self.lib_path_var.set(lib_path)
        if lib_path:
            self.lib_path_frame.pack(fill=tk.X, padx=8, pady=(2, 2), before=self.tree.master)
        else:
            self.lib_path_frame.pack_forget()

    def _copy_library_path(self):
        path = self.lib_path_var.get()
        if path:
            self.root.clipboard_clear()
            self.root.clipboard_append(path)
            self._log(f"Copied to clipboard: {path}")

    # ---- logging ----
    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        logger.info(msg)

    # ---- version check ----
    def _check_versions(self):
        self._log("Checking versions...")
        self.tree.delete(*self.tree.get_children())
        self.tool_info.clear()

        def _worker():
            results = {}
            for key, cfg in self.manifest.items():
                installed = get_installed_version(cfg)
                latest, asset_url = get_latest_release(cfg)
                if installed is None:
                    status = "not installed"
                elif latest and installed != latest:
                    status = "update available"
                elif latest:
                    status = "up to date"
                else:
                    status = "installed (offline)"
                results[key] = {
                    "installed": installed,
                    "latest": latest,
                    "asset_url": asset_url,
                    "status": status,
                }
            self.root.after(0, lambda: self._populate_tree(results))

        threading.Thread(target=_worker, daemon=True).start()

    def _populate_tree(self, results: Dict):
        self.tool_info = results
        for key, cfg in self.manifest.items():
            info = results.get(key, {})
            label = cfg.get("label", key)
            installed = info.get("installed") or "-"
            latest = info.get("latest") or "-"
            status = info.get("status", "unknown")

            tag = status.replace(" ", "_")
            self.tree.insert("", tk.END, iid=key, values=(label, installed, latest, status), tags=(tag,))

        self.tree.tag_configure("up_to_date", foreground=FORM_COLORS["success"])
        self.tree.tag_configure("update_available", foreground=FORM_COLORS["warning"])
        self.tree.tag_configure("not_installed", foreground=FORM_COLORS["text_dim"])
        self.tree.tag_configure("installed_(offline)", foreground=FORM_COLORS["text_dim"])

        self._log("Version check complete.")

    # ---- selection helpers ----
    def _selected_keys(self):
        return list(self.tree.selection())

    # ---- install / update ----
    def _install_selected(self):
        keys = self._selected_keys()
        if not keys:
            self._log("No tool selected.")
            return
        for key in keys:
            info = self.tool_info.get(key, {})
            cfg = self.manifest.get(key)
            if not cfg:
                continue
            asset_url = info.get("asset_url")
            tag = info.get("latest")
            if not asset_url or not tag:
                self._log(f"No download available for {cfg.get('label', key)}.")
                continue
            self._log(f"Downloading {cfg['label']} {tag}...")
            self._do_install(key, cfg, asset_url, tag)

    def _do_install(self, key: str, cfg: Dict, asset_url: str, tag: str):
        def _worker():
            ok = download_and_install(
                cfg, asset_url, tag,
                progress_cb=lambda p: self.root.after(0, lambda: self._log(f"  {p:.0%}")),
                log_cb=lambda m: self.root.after(0, lambda: self._log(m)),
            )
            if ok:
                self.root.after(0, lambda: self._log(f"{cfg['label']} {tag} installed."))
                self.root.after(0, self._check_versions)
            else:
                self.root.after(0, lambda: self._log(f"Failed to install {cfg['label']}."))

        threading.Thread(target=_worker, daemon=True).start()

    # ---- launch ----
    def _launch_selected(self):
        keys = self._selected_keys()
        if not keys:
            self._log("No tool selected.")
            return
        for key in keys:
            cfg = self.manifest.get(key)
            if not cfg:
                continue
            if launch_tool(cfg):
                self._log(f"Launched {cfg['label']}.")
            else:
                self._log(f"Executable not found for {cfg['label']}.")

    # ---- open folder ----
    def _open_folder_selected(self):
        keys = self._selected_keys()
        if not keys:
            self._log("No tool selected.")
            return
        for key in keys:
            cfg = self.manifest.get(key)
            if not cfg:
                continue
            install_dir = cfg["install_dir"]
            if os.path.isdir(install_dir):
                os.startfile(install_dir)
            else:
                self._log(f"Folder does not exist: {install_dir}")


# ============================================================================
# STANDALONE ENTRY POINT
# ============================================================================

def main():
    setup_shared_logging("software_launcher")
    root = tk.Tk()
    apply_category_icon(root)
    app = SoftwareLauncherManager(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
