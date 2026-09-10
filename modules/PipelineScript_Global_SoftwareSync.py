#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Global_SoftwareSync.py
Description: Auto-detect software versions, back up and restore configs between
local machines and NAS, and migrate configs to new versions.
"""

import os
import sys
import re
import json
import shutil
import fnmatch
import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from shared_window_icon import apply_category_icon
from typing import Dict, List, Optional, Tuple

from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_form_keyboard import FormKeyboardMixin, FORM_COLORS
from rack_settings import RackSettings

logger = get_logger("software_sync")

MANIFEST_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "software_sync_manifest.json")


# ============================================================================
# CORE LOGIC
# ============================================================================

def _expand_env(path: str) -> str:
    """Expand environment variables like %APPDATA% and %USERPROFILE%."""
    result = path
    for var in re.findall(r'%([^%]+)%', path):
        val = os.environ.get(var, '')
        result = result.replace(f'%{var}%', val)
    return os.path.normpath(result)


def _resolve_path(template: str, version: str, config_dir: str = "") -> str:
    """Expand {version}, {config_dir} and env vars in a path template."""
    s = template.replace("{version}", version).replace("{config_dir}", config_dir)
    return _expand_env(s)


def load_manifest(path: str = MANIFEST_PATH) -> Dict:
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _scan_directory_for_versions(scan_dir: str, pattern: re.Pattern) -> set:
    """Scan a directory for version patterns and return matching versions."""
    versions = set()
    if not os.path.isdir(scan_dir):
        return versions
    for entry in os.listdir(scan_dir):
        m = pattern.search(entry)
        if m:
            versions.add(m.group(1))
    return versions


def scan_installed_versions(sw_cfg: Dict) -> List[str]:
    """Scan install and config directories for detected major.minor versions.

    Checks both install_scan and config_scan locations to find versions.
    This allows detecting configs even when software isn't installed
    in the standard location.
    """
    versions = set()

    # Scan install directory (optional)
    if "install_scan" in sw_cfg:
        scan_dir = _expand_env(sw_cfg["install_scan"])
        pattern = re.compile(sw_cfg["version_pattern"])
        versions.update(_scan_directory_for_versions(scan_dir, pattern))

    # Also scan config directory location (optional, uses its own pattern)
    if "config_scan" in sw_cfg:
        config_scan = sw_cfg["config_scan"]
        scan_dir = _expand_env(config_scan.get("path", ""))
        pattern = re.compile(config_scan.get("pattern", sw_cfg["version_pattern"]))
        versions.update(_scan_directory_for_versions(scan_dir, pattern))

    return sorted(versions, key=lambda v: [int(x) for x in v.split('.')])


def get_config_dir(sw_cfg: Dict, version: str) -> str:
    return _resolve_path(sw_cfg["config_dir"], version)


def detect_new_versions(sw_cfg: Dict) -> List[str]:
    """Return versions that are installed but have no config directory yet."""
    installed = scan_installed_versions(sw_cfg)
    new = []
    for v in installed:
        cfg_dir = get_config_dir(sw_cfg, v)
        if not os.path.isdir(cfg_dir):
            new.append(v)
    return new


def _scan_nas_versions(sw_cfg: Dict) -> List[str]:
    """Scan mapped backup directories for version folders."""
    pattern = re.compile(sw_cfg["version_pattern"])
    versions = set()
    for prof in sw_cfg.get("profiles", []):
        tmpl = prof.get("mapped", "")
        if "{version}" not in tmpl:
            continue
        parent = _expand_env(tmpl.split("{version}")[0].rstrip("/\\"))
        if not os.path.isdir(parent):
            continue
        for entry in os.listdir(parent):
            m = pattern.search(entry)
            if m:
                versions.add(m.group(1))
    return sorted(versions, key=lambda v: [int(x) for x in v.split('.')])


def find_previous_version(sw_cfg: Dict, target_ver: str) -> Optional[str]:
    """Find the highest version below *target_ver* that has a config dir locally
    or a backup on NAS/mapped drive."""
    all_versions = set(scan_installed_versions(sw_cfg)) | set(_scan_nas_versions(sw_cfg))
    target_parts = [int(x) for x in target_ver.split('.')]
    candidates = []
    for v in sorted(all_versions, key=lambda v: [int(x) for x in v.split('.')]):
        parts = [int(x) for x in v.split('.')]
        if parts >= target_parts:
            continue
        cfg_dir = get_config_dir(sw_cfg, v)
        # Accept if local config exists OR any mapped backup exists
        if os.path.isdir(cfg_dir):
            candidates.append(v)
            continue
        for prof in sw_cfg.get("profiles", []):
            mapped = _resolve_path(prof["mapped"], v, cfg_dir)
            if os.path.isdir(mapped):
                candidates.append(v)
                break
    return candidates[-1] if candidates else None


def _newest_mtime(directory: str, patterns: Optional[List[str]] = None) -> Optional[float]:
    """Return the newest mtime of matching files in *directory*, or None."""
    if not os.path.isdir(directory):
        return None
    newest = None
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if patterns and not any(fnmatch.fnmatch(f, p) for p in patterns):
                continue
            t = os.path.getmtime(os.path.join(root, f))
            if newest is None or t > newest:
                newest = t
    return newest


def _copy_tree(src: str, dst: str, patterns: Optional[List[str]] = None) -> int:
    """Copy files from *src* to *dst*. If *patterns* is given only matching files
    are copied. Returns number of files copied."""
    count = 0
    if not os.path.isdir(src):
        return 0
    for root, dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        dst_root = os.path.join(dst, rel) if rel != '.' else dst
        for f in files:
            if patterns and not any(fnmatch.fnmatch(f, p) for p in patterns):
                continue
            os.makedirs(dst_root, exist_ok=True)
            shutil.copy2(os.path.join(root, f), os.path.join(dst_root, f))
            count += 1
    return count


def profile_status(source: str, mapped: str,
                   patterns: Optional[List[str]] = None) -> str:
    """Compare mtimes and return a status string."""
    mt_src = _newest_mtime(source, patterns)
    mt_mapped = _newest_mtime(mapped, patterns)

    if mt_src is None:
        return "missing"
    if mt_mapped is None:
        return "local only"
    diff = mt_src - mt_mapped
    if abs(diff) < 2:
        return "synced"
    return "local newer" if diff > 0 else "NAS newer"


# ============================================================================
# GUI
# ============================================================================

class SoftwareSyncManager(FormKeyboardMixin):
    """Tkinter GUI for software config synchronisation."""

    def __init__(self, root, embedded=False, settings=None):
        self.root = root
        self.embedded = embedded
        self.settings = settings or RackSettings()
        self.manifest: Dict = {}
        self.rows: List[Dict] = []  # treeview row data

        if not embedded:
            self.root.title("Software Config Sync")
            self.root.geometry("1100x650")
            self.root.minsize(900, 500)
            self.root.configure(bg=FORM_COLORS["bg"])

        self._load_manifest()
        self._resolve_manifest_paths()
        self._build_form()
        # initial scan
        self.root.after(200, self._scan_status)

    # ---- manifest ----
    def _load_manifest(self):
        try:
            self.manifest = load_manifest()
        except Exception as e:
            self.manifest = {}
            logger.error(f"Failed to load manifest: {e}")

    def _resolve_manifest_paths(self):
        """Prepend the mapped software base path from settings to relative profile paths."""
        base = self.settings.get_mapped_software_path()
        for sw_key, sw_cfg in self.manifest.items():
            for prof in sw_cfg.get("profiles", []):
                mapped = prof.get("mapped", "")
                if mapped and not os.path.isabs(mapped):
                    prof["mapped"] = os.path.join(base, mapped)

    # ---- UI ----
    def _build_form(self):
        container = self.root

        # Header
        hdr = tk.Frame(container, bg=FORM_COLORS["accent_dark"], height=44)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        tk.Label(hdr, text="Software Config Sync", font=("Segoe UI", 13, "bold"),
                 fg=FORM_COLORS["text"], bg=FORM_COLORS["accent_dark"]).pack(side=tk.LEFT, padx=12)

        # Button bar
        btn_bar = tk.Frame(container, bg=FORM_COLORS["bg"])
        btn_bar.pack(fill=tk.X, padx=8, pady=(6, 2))

        btn_style = dict(font=("Segoe UI", 9), bg=FORM_COLORS["bg_input"],
                         fg=FORM_COLORS["text"], activebackground=FORM_COLORS["bg_hover"],
                         activeforeground=FORM_COLORS["text"], bd=0, padx=10, pady=4)

        tk.Button(btn_bar, text="Refresh", command=self._scan_status, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Push Selected", command=self._backup_selected, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Pull Selected", command=self._restore_selected, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Push All to NAS", command=self._backup_all, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Pull All from NAS", command=self._restore_all, **btn_style).pack(side=tk.LEFT, padx=2)
        tk.Button(btn_bar, text="Migrate", command=self._migrate_new, **btn_style).pack(side=tk.LEFT, padx=2)

        # Treeview
        tree_frame = tk.Frame(container, bg=FORM_COLORS["bg"])
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=4)

        columns = ("software", "version", "profile", "local", "mapped", "status")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings",
                                 selectmode="extended", style="Dark.Treeview")
        headings = {"software": "Software", "version": "Version", "profile": "Profile",
                    "local": "Local", "mapped": "Mapped Drive (NAS)", "status": "Status"}
        widths = {"software": 100, "version": 70, "profile": 90,
                  "local": 200, "mapped": 250, "status": 100}
        for col in columns:
            self.tree.heading(col, text=headings[col])
            self.tree.column(col, width=widths[col], minwidth=50)

        vsb = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

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

    # ---- logging ----
    def _log(self, msg: str):
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        self.log_text.insert(tk.END, f"[{ts}] {msg}\n")
        self.log_text.see(tk.END)
        logger.info(msg)

    # ---- scan / status ----
    def _scan_status(self):
        self.tree.delete(*self.tree.get_children())
        self.rows.clear()

        for sw_key, sw_cfg in self.manifest.items():
            label = sw_cfg.get("label", sw_key)
            installed = scan_installed_versions(sw_cfg)
            new_versions = detect_new_versions(sw_cfg)

            for ver in installed:
                config_dir = get_config_dir(sw_cfg, ver)
                is_new = ver in new_versions

                for prof in sw_cfg.get("profiles", []):
                    patterns = prof.get("patterns")
                    source = _resolve_path(prof["source"], ver, config_dir)
                    mapped = _resolve_path(prof["mapped"], ver, config_dir)

                    if is_new:
                        status = "NEW VERSION"
                    else:
                        status = profile_status(source, mapped, patterns)

                    row = {
                        "sw_key": sw_key, "version": ver, "profile": prof["name"],
                        "source": source, "mapped": mapped,
                        "patterns": patterns, "status": status,
                    }
                    self.rows.append(row)

                    local_short = source if os.path.isdir(source) else "(missing)"
                    mapped_short = mapped if os.path.isdir(mapped) else "(missing)"

                    tag = "new" if is_new else status.replace(" ", "_")
                    self.tree.insert("", tk.END, values=(
                        label, ver, prof["name"], local_short, mapped_short, status
                    ), tags=(tag,))

            if not installed:
                self.tree.insert("", tk.END, values=(
                    label, "-", "-", "(not found)", "", "no install"
                ), tags=("missing",))

        # Tag colours
        self.tree.tag_configure("synced", foreground=FORM_COLORS["success"])
        self.tree.tag_configure("local_newer", foreground=FORM_COLORS["warning"])
        self.tree.tag_configure("NAS_newer", foreground=FORM_COLORS["accent"])
        self.tree.tag_configure("local_only", foreground=FORM_COLORS["warning"])
        self.tree.tag_configure("missing", foreground=FORM_COLORS["error"])
        self.tree.tag_configure("new", foreground="#c9d1d9", background="#1a3a1a")
        self.tree.tag_configure("no_install", foreground=FORM_COLORS["text_dim"])

        self._log(f"Scan complete: {len(self.rows)} profile entries")

    # ---- helpers to map selection -> row data ----
    def _selected_rows(self) -> List[Dict]:
        selected = []
        children = self.tree.get_children()
        for sel_iid in self.tree.selection():
            idx = children.index(sel_iid)
            if idx < len(self.rows):
                selected.append(self.rows[idx])
        return selected

    # ---- backup ----
    def _do_backup(self, row: Dict):
        src = row["source"]
        patterns = row.get("patterns")
        dest = row["mapped"]
        try:
            n = _copy_tree(src, dest, patterns)
            self._log(f"Backed up {n} files: {src} -> {dest}")
        except Exception as e:
            self._log(f"ERROR backing up to {dest}: {e}")

    def _backup_selected(self):
        rows = self._selected_rows()
        if not rows:
            self._log("No rows selected")
            return
        for r in rows:
            if r["status"] == "NEW VERSION":
                self._log(f"Skipping {r['sw_key']} {r['version']} (new version, migrate first)")
                continue
            self._do_backup(r)
        self._scan_status()

    def _backup_all(self):
        for r in self.rows:
            if r["status"] in ("missing", "NEW VERSION", "no install"):
                continue
            self._do_backup(r)
        self._scan_status()

    # ---- restore ----
    def _do_restore(self, row: Dict):
        dst = row["source"]
        patterns = row.get("patterns")
        src = row["mapped"]
        if os.path.isdir(src):
            try:
                n = _copy_tree(src, dst, patterns)
                self._log(f"Restored {n} files: {src} -> {dst}")
                return
            except Exception as e:
                self._log(f"ERROR restoring from {src}: {e}")
        self._log(f"No backup found for {row['sw_key']} {row['version']}/{row['profile']}")

    def _restore_selected(self):
        rows = self._selected_rows()
        if not rows:
            self._log("No rows selected")
            return
        for r in rows:
            self._do_restore(r)
        self._scan_status()

    def _restore_all(self):
        for r in self.rows:
            if r["status"] in ("NEW VERSION", "no install"):
                continue
            self._do_restore(r)
        self._scan_status()

    # ---- migrate ----
    def _migrate_new(self):
        rows = self._selected_rows()
        if not rows:
            self._log("No rows selected.")
            return
        migrated = False
        for row in rows:
            sw_key = row["sw_key"]
            sw_cfg = self.manifest[sw_key]
            target_ver = row["version"]
            profile_name = row["profile"]

            # Find the matching profile definition
            prof = None
            for p in sw_cfg.get("profiles", []):
                if p["name"] == profile_name:
                    prof = p
                    break
            if prof is None:
                continue

            # Find newest previous version that has this profile available
            prev = self._find_previous_for_profile(sw_cfg, prof, target_ver)
            if prev is None:
                self._log(f"No previous version with '{profile_name}' found for {sw_cfg['label']} {target_ver}")
                continue

            if messagebox.askyesno(
                "Migrate Config",
                f"Migrate {sw_cfg['label']} {profile_name} from {prev} to {target_ver}?"
            ):
                self._migrate_profile(sw_cfg, prof, prev, target_ver)
                migrated = True
        if not migrated:
            self._log("Nothing migrated.")
        self._scan_status()

    def _find_previous_for_profile(self, sw_cfg: Dict, prof: Dict, target_ver: str) -> Optional[str]:
        """Find the highest version below target_ver that has this profile locally or on NAS/mapped."""
        all_versions = set(scan_installed_versions(sw_cfg)) | set(_scan_nas_versions(sw_cfg))
        target_parts = [int(x) for x in target_ver.split('.')]
        candidates = []
        for v in sorted(all_versions, key=lambda v: [int(x) for x in v.split('.')]):
            parts = [int(x) for x in v.split('.')]
            if parts >= target_parts:
                continue
            cfg_dir = get_config_dir(sw_cfg, v)
            source = _resolve_path(prof["source"], v, cfg_dir)
            mapped = _resolve_path(prof["mapped"], v, cfg_dir)
            if os.path.isdir(source) or os.path.isdir(mapped):
                candidates.append(v)
        return candidates[-1] if candidates else None

    def _migrate_profile(self, sw_cfg: Dict, prof: Dict, prev: str, new_ver: str):
        """Migrate a single profile from prev version to new_ver."""
        prev_config_dir = get_config_dir(sw_cfg, prev)
        new_config_dir = get_config_dir(sw_cfg, new_ver)
        patterns = prof.get("patterns")

        src = _resolve_path(prof["source"], prev, prev_config_dir)
        dst_local = _resolve_path(prof["source"], new_ver, new_config_dir)
        dst_mapped = _resolve_path(prof["mapped"], new_ver, new_config_dir)

        # Use local source if available, otherwise fall back to mapped drive
        if os.path.isdir(src):
            actual_src = src
        else:
            fallback = _resolve_path(prof["mapped"], prev, prev_config_dir)
            if os.path.isdir(fallback):
                actual_src = fallback
            else:
                self._log(f"  [{prof['name']}] No source found for {prev}, skipping")
                return
            self._log(f"  [{prof['name']}] Using backup: {actual_src}")

        n = _copy_tree(actual_src, dst_local, patterns)
        self._log(f"  [{prof['name']}] Copied {n} files to local config")

        try:
            nc = _copy_tree(dst_local, dst_mapped, patterns)
            self._log(f"  [{prof['name']}] Backed up {nc} files to {dst_mapped}")
        except Exception as e:
            self._log(f"  [{prof['name']}] ERROR backing up to {dst_mapped}: {e}")


# ============================================================================
# STANDALONE ENTRY POINT
# ============================================================================

def main():
    setup_shared_logging("software_sync")
    root = tk.Tk()
    apply_category_icon(root)
    app = SoftwareSyncManager(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
