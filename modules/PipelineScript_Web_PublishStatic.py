#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PipelineScript_Web_PublishStatic.py
Author: Florian Dheer
Version: 1.0.0
Description: Upload Staatic exports to FTP via WinSCP, sync DokuWiki, and create dated archives
"""

import os
import sys
import glob
import json
import shutil
import zipfile
import threading
import subprocess
import tempfile
import time
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
from shared_window_icon import apply_category_icon
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, List
from urllib.parse import quote

# Setup logging using shared utility
from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_wordpress import is_wordpress_folder
from rak_settings import get_rak_settings, join_native_path

# Get logger reference (configured in main())
logger = get_logger("web_publish_static")


# ====================================
# CONFIGURATION
# ====================================

class WebPublishConfig:
    """Configuration manager for static site publishing."""

    def __init__(self):
        app_data = Path.home() / "AppData" / "Local" / "PipelineManager"
        app_data.mkdir(parents=True, exist_ok=True)
        self.config_file = app_data / "web_publish_config.json"
        self.config = self._load_config()

    def _default_config(self) -> Dict:
        return {
            "sites": self._discover_sites(),
            "winscp_path": "",
            "backup_max_per_project": 5
        }

    @staticmethod
    def _detect_laragon_www() -> str:
        """Return Laragon www path if it exists."""
        try:
            from PipelineScript_Web_DevBackup import DevBackupConfig
            return DevBackupConfig().get_www_path()
        except Exception:
            # Fallback to default
            www = r"C:\laragon\www"
            return www if os.path.isdir(www) else ""

    def _discover_sites(self) -> Dict:
        """Scan Web folder for site directories (work at root, personal under _Personal/)."""
        work = get_rak_settings().get_work_drive()
        web_root = join_native_path(work, "Web")
        sites = {}

        if not os.path.isdir(web_root):
            logger.warning(f"Web directory not found: {web_root}")
            return sites

        laragon_www = self._detect_laragon_www()

        # Work sites at Web/ root, personal sites under Web/_Personal/
        scan_targets = [(web_root, False), (os.path.join(web_root, "_Personal"), True)]

        for parent_dir, is_personal in scan_targets:
            if not os.path.isdir(parent_dir):
                continue
            for entry in sorted(os.listdir(parent_dir)):
                site_path = os.path.join(parent_dir, entry)
                if not os.path.isdir(site_path) or entry.startswith("_"):
                    continue
                if entry in sites:
                    continue

                # Find the publish folder (case-insensitive)
                publish_dir = ""
                for name in os.listdir(site_path):
                    if name.lower() == "03_publish":
                        publish_dir = os.path.join(site_path, name)
                        break

                # A site must have a publish folder to be publishable
                if not publish_dir:
                    continue

                # Determine export_dir: look for a subfolder inside publish dir
                export_dir = publish_dir
                subdirs = [d for d in os.listdir(publish_dir)
                           if os.path.isdir(os.path.join(publish_dir, d))]
                if len(subdirs) == 1:
                    export_dir = os.path.join(publish_dir, subdirs[0])

                # Detect dev folder (e.g. VitePress source in 02_Development)
                dev_dir = ""
                for name in os.listdir(site_path):
                    if name.lower() == "02_development":
                        candidate = os.path.join(site_path, name)
                        if os.path.isdir(candidate):
                            dev_dir = candidate
                        break

                # Detect wiki support (brainii folder inside 02_Development)
                has_wiki = False
                wiki_local_dir = ""
                wiki_remote_path = ""
                if dev_dir:
                    brainii_candidate = os.path.join(dev_dir, "brainii")
                    if os.path.isdir(brainii_candidate):
                        has_wiki = True
                        wiki_local_dir = brainii_candidate
                        wiki_remote_path = "/brainii"

                # Detect WordPress via shared helper (Laragon www/<entry>).
                is_wordpress = bool(laragon_www) and is_wordpress_folder(
                    os.path.join(laragon_www, entry)
                )

                sites[entry] = {
                    "label": entry,
                    "is_personal": is_personal,
                    "export_dir": export_dir,
                    "dev_dir": dev_dir,
                    "is_wordpress": is_wordpress,
                    "has_wiki": has_wiki,
                    "wiki_local_dir": wiki_local_dir,
                    "wiki_remote_path": wiki_remote_path,
                    "ftp": {
                        "protocol": "ftp",
                        "host": "",
                        "port": 21,
                        "username": "",
                        "password": "",
                        "remote_path": "/"
                    }
                }

        return sites

    def _load_config(self) -> Dict:
        default = self._default_config()
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
                    loaded = json.load(f)
                self._merge(default, loaded)
                # Remove sites that no longer exist on disk
                discovered = self._discover_sites()
                default["sites"] = {
                    k: v for k, v in default["sites"].items()
                    if k in discovered
                }
                return default
            except Exception as e:
                logger.error(f"Error loading config: {e}")
                return default
        else:
            self._save(default)
            return default

    def _merge(self, default: Dict, loaded: Dict):
        for key, value in loaded.items():
            if key in default and isinstance(value, dict) and isinstance(default[key], dict):
                self._merge(default[key], value)
            else:
                default[key] = value

    def _save(self, config: Optional[Dict] = None):
        if config is None:
            config = self.config
        try:
            self.config_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def save(self):
        self._save()

    def get_site_config(self, site_key: str) -> Dict:
        return self.config.get("sites", {}).get(site_key, {})

    def get_site_keys(self) -> List[str]:
        return list(self.config.get("sites", {}).keys())

    def get_winscp_path(self) -> str:
        return self.config.get("winscp_path", "")

    def set_winscp_path(self, path: str):
        self.config["winscp_path"] = path
        self._save()

    def get_max_backups(self) -> int:
        return self.config.get("backup_max_per_project", 5)

    def set_max_backups(self, value: int):
        self.config["backup_max_per_project"] = value
        self._save()

    def get_last_selected_site(self) -> str:
        return self.config.get("last_selected_site", "")

    def set_last_selected_site(self, site_key: str):
        self.config["last_selected_site"] = site_key
        self._save()


# ====================================
# WINSCP MANAGER
# ====================================

class WinSCPManager:
    """Manages WinSCP binary detection and script execution."""

    COMMON_PATHS = [
        r"C:\Program Files (x86)\WinSCP\winscp.com",
        r"C:\Program Files\WinSCP\winscp.com",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "WinSCP", "winscp.com"),
    ]

    def __init__(self, config: WebPublishConfig):
        self.config = config

    def find_winscp(self) -> Optional[str]:
        """Find winscp.com binary. Returns path or None."""
        # Check configured path first
        configured = self.config.get_winscp_path()
        if configured and os.path.isfile(configured):
            return configured

        # Check common install locations
        for path in self.COMMON_PATHS:
            if os.path.isfile(path):
                self.config.set_winscp_path(path)
                return path

        # Check PATH
        for dir_path in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(dir_path, "winscp.com")
            if os.path.isfile(candidate):
                self.config.set_winscp_path(candidate)
                return candidate

        return None

    def _build_open_command(self, ftp_cfg: Dict) -> str:
        """Build the WinSCP open command string with URL-encoded password."""
        protocol = ftp_cfg.get("protocol", "ftp")
        host = ftp_cfg["host"]
        port = ftp_cfg.get("port", 21)
        user = ftp_cfg["username"]
        password = quote(ftp_cfg["password"], safe="")
        url = f"{protocol}://{user}:{password}@{host}:{port}"
        return f'open "{url}" -passive=on -timeout=60'

    def build_upload_script(self, ftp_cfg: Dict, local_dir: str, remote_path: str,
                            exclude_wiki: bool = False) -> str:
        """Build WinSCP script for uploading (synchronize remote -delete).

        Uses synchronize with -delete to make the remote match the local,
        ensuring a completely fresh upload. Wiki folder is excluded when needed.
        """
        lines = [
            "option batch abort",
            "option confirm off",
            "option reconnecttime 15",
            self._build_open_command(ftp_cfg),
        ]
        # synchronize remote -delete makes the remote match the local,
        # removing any extra files on the server for a fresh upload
        excludes = ""
        if exclude_wiki:
            excludes = ' -filemask="|brainii/"'
        lines.append(f'synchronize remote -delete "{local_dir}" "{remote_path}"{excludes}')
        lines.append("exit")
        return "\n".join(lines)

    def build_wiki_download_script(self, ftp_cfg: Dict, remote_wiki_path: str,
                                   local_wiki_dir: str) -> str:
        """Build WinSCP script for downloading wiki (synchronize local).

        Excludes bulky DokuWiki directories (attic, cache, tmp, locks, index)
        that aren't needed for a local backup.
        """
        exclude = '|*/attic/;*/cache/;*/tmp/;*/locks/;*/index/'
        lines = [
            "option batch abort",
            "option confirm off",
            "option reconnecttime 15",
            self._build_open_command(ftp_cfg),
            f'synchronize local -delete -filemask="{exclude}" "{local_wiki_dir}" "{remote_wiki_path}"',
            "exit",
        ]
        return "\n".join(lines)

    def execute_script(self, winscp_path: str, script_content: str,
                       on_output=None) -> int:
        """Execute a WinSCP script. Returns exit code."""
        # Write script to temp file
        script_file = tempfile.NamedTemporaryFile(
            mode='w', suffix='.txt', delete=False, encoding='utf-8'
        )
        try:
            script_file.write(script_content)
            script_file.close()

            cmd = [winscp_path, "/ini=nul", f"/script={script_file.name}"]
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            for line in iter(process.stdout.readline, ''):
                if on_output:
                    on_output(line.rstrip('\n'))

            return process.wait()
        finally:
            try:
                os.unlink(script_file.name)
            except OSError:
                pass


# ====================================
# PUBLISH WORKFLOW
# ====================================

class PublishWorkflow:
    """Orchestrates the publish pipeline in a background thread."""

    def __init__(self, config: WebPublishConfig, winscp_mgr: WinSCPManager,
                 site_key: str, root: tk.Tk, callbacks: Dict):
        self.config = config
        self.winscp_mgr = winscp_mgr
        self.site_key = site_key
        self.root = root
        self.site_cfg = config.get_site_config(site_key)
        self.has_wiki = self.site_cfg.get("has_wiki", False)
        self.has_dev = bool(self.site_cfg.get("dev_dir", "")
                           and os.path.isdir(self.site_cfg.get("dev_dir", "")))
        self.is_wordpress = self.site_cfg.get("is_wordpress", False)

        # Callbacks: on_step_start(idx, name), on_step_done(idx, success),
        #            on_output(msg), on_complete(success, msg),
        #            prompt_user(title, message, choices) -> str
        self.cb = callbacks

        self._cancel = threading.Event()

    def get_steps(self) -> List[str]:
        if self.has_wiki:
            steps = [
                "Validate",
                "FTP Upload (excl. wiki)",
                "Sync wiki to local",
                "Archive",
            ]
        else:
            steps = [
                "Validate",
                "FTP Upload",
                "Archive",
            ]
        if self.has_dev:
            dev_label = "Dev Backup (WP + DB)" if self.is_wordpress else "Dev Backup"
            steps.append(dev_label)
        return steps

    def cancel(self):
        self._cancel.set()

    def _cancelled(self) -> bool:
        return self._cancel.is_set()

    def _output(self, msg: str):
        self.root.after(0, lambda: self.cb["on_output"](msg))

    def _step_start(self, idx: int, name: str):
        self.root.after(0, lambda: self.cb["on_step_start"](idx, name))

    def _step_done(self, idx: int, success: bool):
        self.root.after(0, lambda: self.cb["on_step_done"](idx, success))

    def _complete(self, success: bool, msg: str):
        self.root.after(0, lambda: self.cb["on_complete"](success, msg))

    def run(self):
        """Main workflow entry point. Call from a thread."""
        try:
            if self.has_wiki:
                self._run_with_wiki()
            else:
                self._run_without_wiki()
        except Exception as e:
            logger.exception("Publish workflow failed")
            self._complete(False, f"Error: {e}")

    def _run_without_wiki(self):
        steps = self.get_steps()
        step = 0

        # Step 0: Validate
        self._step_start(step, steps[step])
        ok = self._validate()
        self._step_done(step, ok)
        if not ok or self._cancelled():
            return
        step += 1

        # Step 1: FTP Upload
        self._step_start(step, steps[step])
        ok = self._ftp_upload(exclude_wiki=False)
        self._step_done(step, ok)
        if not ok or self._cancelled():
            return
        step += 1

        # Step 2: Archive
        self._step_start(step, steps[step])
        ok = self._archive()
        self._step_done(step, ok)
        if not ok:
            return
        step += 1

        # Step 3 (optional): Dev Backup
        if self.has_dev:
            self._step_start(step, steps[step])
            ok = self._archive_dev()
            self._step_done(step, ok)
            if not ok:
                return

        self._complete(True, "Publish completed successfully!")

    def _run_with_wiki(self):
        steps = self.get_steps()
        step = 0

        # Step 0: Validate
        self._step_start(step, steps[step])
        ok = self._validate()
        self._step_done(step, ok)
        if not ok or self._cancelled():
            return
        step += 1

        # Step 1: FTP Upload (excl. wiki)
        self._step_start(step, steps[step])
        ok = self._ftp_upload(exclude_wiki=True)
        self._step_done(step, ok)
        if not ok or self._cancelled():
            return
        step += 1

        # Step 2: Download wiki from server to 02_Development/brainii
        self._step_start(step, steps[step])
        ok = self._download_wiki()
        self._step_done(step, ok)
        if self._cancelled():
            return
        step += 1

        # Step 3: Archive
        self._step_start(step, steps[step])
        ok = self._archive()
        self._step_done(step, ok)
        if not ok:
            return
        step += 1

        # Step 4 (optional): Dev Backup
        if self.has_dev:
            self._step_start(step, steps[step])
            ok = self._archive_dev()
            self._step_done(step, ok)
            if not ok:
                return

        self._complete(True, "Publish completed successfully!")

    # ---- Individual Steps ----

    def _validate(self) -> bool:
        export_dir = self.site_cfg.get("export_dir", "")
        ftp_cfg = self.site_cfg.get("ftp", {})

        if not os.path.isdir(export_dir):
            self._output(f"Export directory not found: {export_dir}")
            self._complete(False, "Validation failed: export directory missing")
            return False

        if not ftp_cfg.get("host") or not ftp_cfg.get("username"):
            self._output("FTP credentials not configured. Open Settings to configure.")
            self._complete(False, "Validation failed: FTP not configured")
            return False

        winscp = self.winscp_mgr.find_winscp()
        if not winscp:
            self._output("WinSCP not found. Install WinSCP or set the path in Settings.")
            self._complete(False, "Validation failed: WinSCP not found")
            return False

        self._output(f"Export dir: {export_dir}")
        self._output(f"WinSCP: {winscp}")
        self._output(f"FTP host: {ftp_cfg['host']}")
        self._output("Validation passed")
        return True

    def _ftp_upload(self, exclude_wiki: bool) -> bool:
        export_dir = self.site_cfg["export_dir"]
        ftp_cfg = self.site_cfg["ftp"]
        remote_path = ftp_cfg.get("remote_path", "/")
        winscp_path = self.winscp_mgr.find_winscp()

        script = self.winscp_mgr.build_upload_script(ftp_cfg, export_dir, remote_path, exclude_wiki)
        self._output(f"Uploading to {ftp_cfg['host']}:{remote_path} ...")
        if exclude_wiki:
            self._output("(excluding /brainii/ directory)")

        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self._output(f"Retry attempt {attempt}/{max_attempts} ...")
            exit_code = self.winscp_mgr.execute_script(
                winscp_path, script, on_output=self._output
            )
            if exit_code == 0:
                self._output("FTP upload completed")
                return True
            if attempt < max_attempts:
                wait_sec = attempt * 10
                self._output(f"FTP upload failed (exit code {exit_code}), retrying in {wait_sec}s ...")
                time.sleep(wait_sec)

        self._output(f"FTP upload failed after {max_attempts} attempts (exit code {exit_code})")
        self._complete(False, f"FTP upload failed (exit code {exit_code})")
        return False

    def _download_wiki(self) -> bool:
        """Download wiki from server into 02_Development/brainii.

        Retries on transient failures. Non-fatal: warns and continues
        if the download cannot be completed so the publish is not blocked.
        """
        wiki_local = self.site_cfg.get("wiki_local_dir", "")
        if not wiki_local:
            self._output("No wiki local path configured, skipping")
            return True

        ftp_cfg = self.site_cfg["ftp"]
        wiki_remote = self.site_cfg.get("wiki_remote_path", "/brainii")
        winscp_path = self.winscp_mgr.find_winscp()

        os.makedirs(wiki_local, exist_ok=True)

        script = self.winscp_mgr.build_wiki_download_script(
            ftp_cfg, wiki_remote, wiki_local
        )

        max_attempts = 3
        exit_code = None
        for attempt in range(1, max_attempts + 1):
            if self._cancelled():
                return False
            if attempt > 1:
                wait_sec = attempt * 10
                self._output(f"Wiki download failed (exit code {exit_code}), "
                             f"retrying in {wait_sec}s ... ({attempt}/{max_attempts})")
                time.sleep(wait_sec)

            self._output(f"Syncing wiki from {wiki_remote} to {os.path.basename(wiki_local)}/ ...")
            exit_code = self.winscp_mgr.execute_script(
                winscp_path, script, on_output=self._output
            )
            if exit_code == 0:
                self._output("Wiki synced to 02_Development/brainii")
                return True

        self._output(f"Wiki download failed after {max_attempts} attempts "
                     f"(exit code {exit_code}) — continuing without wiki sync")
        return True  # Non-fatal: don't block the publish

    def _archive(self) -> bool:
        export_dir = self.site_cfg["export_dir"]
        archive_base = get_rak_settings().get_archive_path("Web")
        if self.site_cfg.get("is_personal", False):
            site_archive_dir = os.path.join(archive_base, "_Personal", self.site_key)
        else:
            site_archive_dir = os.path.join(archive_base, self.site_key)
        os.makedirs(site_archive_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        zip_name = f"pub_{self.site_key}_{timestamp}.zip"
        zip_path = os.path.join(site_archive_dir, zip_name)

        self._output(f"Creating archive: {zip_path}")

        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for dirpath, dirnames, filenames in os.walk(export_dir):
                    for filename in filenames:
                        file_path = os.path.join(dirpath, filename)
                        arcname = os.path.relpath(file_path, export_dir)
                        zf.write(file_path, arcname)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            self._output(f"Archive created: {zip_name} ({size_mb:.1f} MB)")

            # Rotate old backups
            max_keep = self.config.get_max_backups()
            pattern = os.path.join(site_archive_dir, f"pub_{self.site_key}_*.zip")
            backups = sorted(glob.glob(pattern))
            if len(backups) > max_keep:
                for old in backups[:len(backups) - max_keep]:
                    try:
                        os.remove(old)
                        self._output(f"Rotated: {os.path.basename(old)}")
                    except OSError as e:
                        logger.warning(f"Failed to remove {old}: {e}")

            return True
        except Exception as e:
            self._output(f"Archive failed: {e}")
            self._complete(False, f"Archive failed: {e}")
            return False

    def _archive_dev(self) -> bool:
        """Archive the 02_Development folder alongside the publish backup.

        For WordPress sites, delegates to the WordPress dev backup module
        which includes a database dump alongside the site files.
        """
        if self.site_cfg.get("is_wordpress", False):
            return self._archive_dev_wordpress()
        return self._archive_dev_static()

    def _archive_dev_wordpress(self) -> bool:
        """WordPress dev backup: files + database dump via the dev backup module."""
        try:
            from PipelineScript_Web_DevBackup import (
                DevBackupConfig, SiteDiscovery, MysqlManager, BackupManager
            )
        except ImportError as e:
            self._output(f"Cannot import WordPress backup module: {e}")
            self._output("Falling back to static dev backup (no database dump)")
            return self._archive_dev_static()

        wp_config = DevBackupConfig()
        www_path = wp_config.get_www_path()
        site_path = os.path.join(www_path, self.site_key)

        if not os.path.isdir(site_path):
            self._output(f"WordPress site not found in Laragon: {site_path}")
            self._output("Falling back to static dev backup (no database dump)")
            return self._archive_dev_static()

        wp_config_file = os.path.join(site_path, "wp-config.php")
        parsed = SiteDiscovery.parse_wp_config(wp_config_file)
        if not parsed:
            self._output("Could not parse wp-config.php")
            self._output("Falling back to static dev backup (no database dump)")
            return self._archive_dev_static()

        from PipelineScript_Web_DevBackup import WPSite
        wp_site = WPSite(
            name=self.site_key,
            path=site_path,
            wp_config_path=wp_config_file,
            db_name=parsed["db_name"],
            db_user=parsed["db_user"],
            db_password=parsed["db_password"],
            db_host=parsed["db_host"],
            table_prefix=parsed["table_prefix"],
        )

        archive_base = get_rak_settings().get_archive_path("Web")
        if self.site_cfg.get("is_personal", False):
            site_archive_dir = os.path.join(archive_base, "_Personal", self.site_key)
        else:
            site_archive_dir = os.path.join(archive_base, self.site_key)

        mysql_mgr = MysqlManager(wp_config.get_laragon_path())
        exclude = wp_config.get_exclude_dirs()

        self._output(f"WordPress dev backup: {self.site_key} (db: {parsed['db_name']})")

        ok, msg = BackupManager.backup_site(
            wp_site, site_archive_dir, mysql_mgr, exclude,
            on_output=self._output
        )

        if ok:
            max_keep = self.config.get_max_backups()
            pattern = os.path.join(site_archive_dir, f"dev_{self.site_key}_*.zip")
            backups = sorted(glob.glob(pattern))
            if len(backups) > max_keep:
                for old in backups[:len(backups) - max_keep]:
                    try:
                        os.remove(old)
                        self._output(f"Rotated: {os.path.basename(old)}")
                    except OSError as e:
                        logger.warning(f"Failed to remove {old}: {e}")
            self._output(f"WordPress dev backup complete: {msg}")
            return True
        else:
            self._output(f"WordPress dev backup failed: {msg}")
            self._complete(False, f"Dev backup failed: {msg}")
            return False

    def _archive_dev_static(self) -> bool:
        """Static dev backup: zip the 02_Development folder."""
        dev_dir = self.site_cfg.get("dev_dir", "")
        if not dev_dir or not os.path.isdir(dev_dir):
            self._output(f"Dev directory not found: {dev_dir}")
            self._complete(False, "Dev backup failed: directory missing")
            return False

        archive_base = get_rak_settings().get_archive_path("Web")
        if self.site_cfg.get("is_personal", False):
            site_archive_dir = os.path.join(archive_base, "_Personal", self.site_key)
        else:
            site_archive_dir = os.path.join(archive_base, self.site_key)
        os.makedirs(site_archive_dir, exist_ok=True)

        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        zip_name = f"dev_{self.site_key}_{timestamp}.zip"
        zip_path = os.path.join(site_archive_dir, zip_name)

        exclude_dirs = {"node_modules", ".cache", "dist", ".temp", "__pycache__", ".git", ".claude"}

        self._output(f"Creating dev backup: {zip_name}")
        self._output(f"  Source: {dev_dir}")
        self._output(f"  Excluding: {', '.join(sorted(exclude_dirs))}")

        try:
            file_count = 0
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for dirpath, dirnames, filenames in os.walk(dev_dir):
                    dirnames[:] = [d for d in dirnames if d not in exclude_dirs]
                    for filename in filenames:
                        file_path = os.path.join(dirpath, filename)
                        arcname = os.path.relpath(file_path, dev_dir)
                        zf.write(file_path, arcname)
                        file_count += 1

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            self._output(f"Dev backup created: {zip_name} ({file_count} files, {size_mb:.1f} MB)")

            # Rotate old dev backups
            max_keep = self.config.get_max_backups()
            pattern = os.path.join(site_archive_dir, f"dev_{self.site_key}_*.zip")
            backups = sorted(glob.glob(pattern))
            if len(backups) > max_keep:
                for old in backups[:len(backups) - max_keep]:
                    try:
                        os.remove(old)
                        self._output(f"Rotated: {os.path.basename(old)}")
                    except OSError as e:
                        logger.warning(f"Failed to remove {old}: {e}")

            return True
        except Exception as e:
            self._output(f"Dev backup failed: {e}")
            self._complete(False, f"Dev backup failed: {e}")
            return False


# ====================================
# FTP SETTINGS DIALOG
# ====================================

class FTPSettingsDialog:
    """Modal dialog for configuring FTP settings."""

    def __init__(self, parent: tk.Tk, config: WebPublishConfig, winscp_mgr: WinSCPManager,
                 initial_site_key: str = ""):
        self.config = config
        self.winscp_mgr = winscp_mgr
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Publish Settings")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.dialog.geometry("550x590")

        self._build_ui()
        site_keys = self.config.get_site_keys()
        start_key = initial_site_key if initial_site_key in site_keys else site_keys[0]
        self._load_site(start_key)

        # Center on parent
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        pad = {"padx": 10, "pady": 4}

        # Site selector
        top = tk.Frame(self.dialog)
        top.pack(fill=tk.X, **pad)
        tk.Label(top, text="Site:").pack(side=tk.LEFT)
        self.site_var = tk.StringVar()
        site_keys = self.config.get_site_keys()
        labels = {k: self.config.get_site_config(k).get("label", k) for k in site_keys}
        self.site_combo = ttk.Combobox(
            top, textvariable=self.site_var,
            values=[labels[k] for k in site_keys],
            state="readonly", width=30
        )
        self.site_combo.pack(side=tk.LEFT, padx=5)
        self.site_combo.bind("<<ComboboxSelected>>", self._on_site_changed)
        self._site_labels = labels
        self._site_keys = site_keys

        self._clipboard = None
        tk.Button(top, text="Copy Settings", command=self._copy_settings,
                  width=12).pack(side=tk.LEFT, padx=(10, 2))
        self.paste_btn = tk.Button(top, text="Paste Settings", command=self._paste_settings,
                                   width=12, state=tk.DISABLED)
        self.paste_btn.pack(side=tk.LEFT, padx=2)

        # Site label + wiki toggle
        site_opts = tk.Frame(self.dialog)
        site_opts.pack(fill=tk.X, **pad)
        tk.Label(site_opts, text="Display Label:").pack(side=tk.LEFT)
        self.label_var = tk.StringVar()
        tk.Entry(site_opts, textvariable=self.label_var, width=25).pack(side=tk.LEFT, padx=5)
        self.wiki_check_var = tk.BooleanVar()
        tk.Checkbutton(site_opts, text="Has Wiki", variable=self.wiki_check_var,
                       command=self._toggle_wiki_fields).pack(side=tk.RIGHT)

        # FTP frame
        ftp_frame = ttk.LabelFrame(self.dialog, text="FTP Connection")
        ftp_frame.pack(fill=tk.X, **pad)
        ftp_frame.columnconfigure(1, weight=1)

        row = 0
        tk.Label(ftp_frame, text="Protocol:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.protocol_var = tk.StringVar(value="ftp")
        ttk.Combobox(ftp_frame, textvariable=self.protocol_var,
                      values=["ftp", "sftp"], state="readonly", width=10
                      ).grid(row=row, column=1, sticky="w", padx=5, pady=4)

        row += 1
        tk.Label(ftp_frame, text="Host:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.host_var = tk.StringVar()
        tk.Entry(ftp_frame, textvariable=self.host_var, width=35).grid(
            row=row, column=1, sticky="ew", padx=5, pady=4)

        row += 1
        tk.Label(ftp_frame, text="Port:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.port_var = tk.StringVar(value="21")
        tk.Entry(ftp_frame, textvariable=self.port_var, width=8).grid(
            row=row, column=1, sticky="w", padx=5, pady=4)

        row += 1
        tk.Label(ftp_frame, text="Username:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.user_var = tk.StringVar()
        tk.Entry(ftp_frame, textvariable=self.user_var, width=35).grid(
            row=row, column=1, sticky="ew", padx=5, pady=4)

        row += 1
        tk.Label(ftp_frame, text="Password:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.pass_var = tk.StringVar()
        tk.Entry(ftp_frame, textvariable=self.pass_var, show="*", width=35).grid(
            row=row, column=1, sticky="ew", padx=5, pady=4)

        row += 1
        tk.Label(ftp_frame, text="Remote Path:").grid(row=row, column=0, sticky="w", padx=10, pady=4)
        self.remote_var = tk.StringVar(value="/")
        tk.Entry(ftp_frame, textvariable=self.remote_var, width=35).grid(
            row=row, column=1, sticky="ew", padx=5, pady=4)

        # Paths frame
        paths_frame = ttk.LabelFrame(self.dialog, text="Paths")
        paths_frame.pack(fill=tk.X, **pad)
        paths_frame.columnconfigure(1, weight=1)

        tk.Label(paths_frame, text="Export Dir:").grid(row=0, column=0, sticky="w", padx=10, pady=4)
        self.export_var = tk.StringVar()
        tk.Entry(paths_frame, textvariable=self.export_var, width=45).grid(
            row=0, column=1, sticky="ew", padx=5, pady=4)
        tk.Button(paths_frame, text="...", width=3,
                  command=self._browse_export).grid(row=0, column=2, padx=5, pady=4)

        tk.Label(paths_frame, text="Wiki Local:").grid(row=1, column=0, sticky="w", padx=10, pady=4)
        self.wiki_var = tk.StringVar()
        self.wiki_entry = tk.Entry(paths_frame, textvariable=self.wiki_var, width=45)
        self.wiki_entry.grid(row=1, column=1, sticky="ew", padx=5, pady=4)
        self.wiki_browse_btn = tk.Button(paths_frame, text="...", width=3,
                                         command=self._browse_wiki)
        self.wiki_browse_btn.grid(row=1, column=2, padx=5, pady=4)

        tk.Label(paths_frame, text="Wiki Remote:").grid(row=2, column=0, sticky="w", padx=10, pady=4)
        self.wiki_remote_var = tk.StringVar()
        self.wiki_remote_entry = tk.Entry(paths_frame, textvariable=self.wiki_remote_var, width=45)
        self.wiki_remote_entry.grid(row=2, column=1, sticky="ew", padx=5, pady=4)

        # WinSCP frame
        winscp_frame = ttk.LabelFrame(self.dialog, text="WinSCP")
        winscp_frame.pack(fill=tk.X, **pad)
        winscp_frame.columnconfigure(1, weight=1)

        tk.Label(winscp_frame, text="winscp.com:").grid(row=0, column=0, sticky="w", padx=10, pady=4)
        self.winscp_var = tk.StringVar(value=self.config.get_winscp_path())
        tk.Entry(winscp_frame, textvariable=self.winscp_var, width=45).grid(
            row=0, column=1, sticky="ew", padx=5, pady=4)
        tk.Button(winscp_frame, text="...", width=3,
                  command=self._browse_winscp).grid(row=0, column=2, padx=5, pady=4)

        # Backup frame
        backup_frame = ttk.LabelFrame(self.dialog, text="Backup")
        backup_frame.pack(fill=tk.X, **pad)

        max_row = tk.Frame(backup_frame)
        max_row.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(max_row, text="Max backups per project:").pack(side=tk.LEFT)
        self.max_backup_var = tk.StringVar(value=str(self.config.get_max_backups()))
        tk.Spinbox(max_row, from_=1, to=50, textvariable=self.max_backup_var,
                   width=5).pack(side=tk.LEFT, padx=5)

        # Buttons
        btn_frame = tk.Frame(self.dialog)
        btn_frame.pack(fill=tk.X, pady=10, padx=10)

        tk.Button(btn_frame, text="Test Connection", command=self._test_connection).pack(side=tk.LEFT)
        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy).pack(side=tk.RIGHT, padx=5)
        tk.Button(btn_frame, text="Save", command=self._save).pack(side=tk.RIGHT)

    def _on_site_changed(self, event=None):
        label = self.site_var.get()
        for key in self._site_keys:
            if self._site_labels[key] == label:
                self._save_current_to_memory()
                self._load_site(key)
                return

    def _load_site(self, site_key: str):
        self._current_key = site_key
        cfg = self.config.get_site_config(site_key)
        ftp = cfg.get("ftp", {})

        self.site_var.set(self._site_labels.get(site_key, site_key))
        self.label_var.set(cfg.get("label", site_key))
        self.protocol_var.set(ftp.get("protocol", "ftp"))
        self.host_var.set(ftp.get("host", ""))
        self.port_var.set(str(ftp.get("port", 21)))
        self.user_var.set(ftp.get("username", ""))
        self.pass_var.set(ftp.get("password", ""))
        self.remote_var.set(ftp.get("remote_path", "/"))
        self.export_var.set(cfg.get("export_dir", ""))
        self.wiki_var.set(cfg.get("wiki_local_dir", ""))
        self.wiki_remote_var.set(cfg.get("wiki_remote_path", "/brainii"))

        has_wiki = cfg.get("has_wiki", False)
        self.wiki_check_var.set(has_wiki)
        self._toggle_wiki_fields()

    def _save_current_to_memory(self):
        """Save current form values back to config dict (in memory)."""
        key = self._current_key
        site = self.config.config["sites"][key]
        site["label"] = self.label_var.get() or key
        site["has_wiki"] = self.wiki_check_var.get()
        site["export_dir"] = self.export_var.get()
        site["wiki_local_dir"] = self.wiki_var.get()
        site["wiki_remote_path"] = self.wiki_remote_var.get()
        ftp = site.setdefault("ftp", {})
        ftp["protocol"] = self.protocol_var.get()
        ftp["host"] = self.host_var.get()
        try:
            ftp["port"] = int(self.port_var.get())
        except ValueError:
            ftp["port"] = 21
        ftp["username"] = self.user_var.get()
        ftp["password"] = self.pass_var.get()
        ftp["remote_path"] = self.remote_var.get()

    def _save(self):
        self._save_current_to_memory()
        self.config.config["winscp_path"] = self.winscp_var.get()
        try:
            self.config.config["backup_max_per_project"] = int(self.max_backup_var.get())
        except ValueError:
            pass
        self.config.save()
        self.dialog.destroy()

    def _toggle_wiki_fields(self):
        state = tk.NORMAL if self.wiki_check_var.get() else tk.DISABLED
        self.wiki_entry.config(state=state)
        self.wiki_browse_btn.config(state=state)
        self.wiki_remote_entry.config(state=state)

    def _copy_settings(self):
        """Copy current site's FTP settings to clipboard."""
        self._clipboard = {
            "protocol": self.protocol_var.get(),
            "host": self.host_var.get(),
            "port": self.port_var.get(),
            "username": self.user_var.get(),
            "password": self.pass_var.get(),
            "remote_path": self.remote_var.get(),
        }
        self.paste_btn.config(state=tk.NORMAL)

    def _paste_settings(self):
        """Paste copied FTP settings into current site."""
        if not self._clipboard:
            return
        self.protocol_var.set(self._clipboard["protocol"])
        self.host_var.set(self._clipboard["host"])
        self.port_var.set(self._clipboard["port"])
        self.user_var.set(self._clipboard["username"])
        self.pass_var.set(self._clipboard["password"])
        self.remote_var.set(self._clipboard["remote_path"])

    def _browse_export(self):
        path = filedialog.askdirectory(title="Select Export Directory", parent=self.dialog)
        if path:
            self.export_var.set(path)

    def _browse_wiki(self):
        path = filedialog.askdirectory(title="Select Wiki Local Directory", parent=self.dialog)
        if path:
            self.wiki_var.set(path)

    def _browse_winscp(self):
        path = filedialog.askopenfilename(
            title="Select winscp.com",
            filetypes=[("WinSCP Console", "winscp.com"), ("All Files", "*.*")],
            parent=self.dialog
        )
        if path:
            self.winscp_var.set(path)

    def _test_connection(self):
        self._save_current_to_memory()
        ftp_cfg = self.config.get_site_config(self._current_key).get("ftp", {})
        winscp_path = self.winscp_var.get() or self.winscp_mgr.find_winscp()

        if not winscp_path or not os.path.isfile(winscp_path):
            messagebox.showerror("Error", "WinSCP not found", parent=self.dialog)
            return

        if not ftp_cfg.get("host") or not ftp_cfg.get("username"):
            messagebox.showerror("Error", "Host and username are required", parent=self.dialog)
            return

        # Build a simple test script
        open_cmd = self.winscp_mgr._build_open_command(ftp_cfg)
        script = f"option batch abort\noption confirm off\n{open_cmd}\nls\nexit\n"

        try:
            exit_code = self.winscp_mgr.execute_script(winscp_path, script)
            if exit_code == 0:
                messagebox.showinfo("Success", "Connection successful!", parent=self.dialog)
            else:
                messagebox.showerror("Failed",
                                     f"Connection failed (exit code {exit_code})",
                                     parent=self.dialog)
        except Exception as e:
            messagebox.showerror("Error", f"Connection test failed: {e}", parent=self.dialog)


# ====================================
# MAIN UI
# ====================================

FORM_COLORS = {
    "bg": "#1a1a2e",
    "bg_card": "#16213e",
    "fg": "#e0e0e0",
    "fg_dim": "#8888aa",
    "accent": "#0f3460",
    "highlight": "#533483",
    "success": "#2ecc71",
    "warning": "#f39c12",
    "error": "#e74c3c",
    "info": "#3498db",
    "border": "#2a2a4a",
}


class PublishStaticUI:
    """Tkinter GUI for the static site publisher."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Publish Static Site")
        self.root.geometry("800x650")
        self.root.minsize(700, 550)
        self.root.configure(bg=FORM_COLORS["bg"])

        self.config = WebPublishConfig()
        self.winscp_mgr = WinSCPManager(self.config)
        self.workflow = None
        self.publish_running = False
        self.step_labels = []

        self._build_ui()
        self._on_site_changed()

    def _build_ui(self):
        colors = FORM_COLORS

        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=55)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="Publish Static Site", font=("Arial", 15, "bold"),
                 fg="white", bg="#2c3e50").place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Controls frame
        ctrl = tk.Frame(self.root, bg=colors["bg"])
        ctrl.pack(fill=tk.X, padx=12, pady=(10, 5))

        tk.Label(ctrl, text="Site:", bg=colors["bg"], fg=colors["fg"],
                 font=("Arial", 10)).pack(side=tk.LEFT)

        site_keys = self.config.get_site_keys()
        self.site_labels_map = {}
        combo_values = []
        for k in site_keys:
            label = self.config.get_site_config(k).get("label", k)
            self.site_labels_map[label] = k
            combo_values.append(label)

        # Restore last selected site or default to first
        last_site = self.config.get_last_selected_site()
        last_label = self.site_labels_map and next(
            (lbl for lbl, key in self.site_labels_map.items() if key == last_site), None)
        initial = last_label if last_label and last_label in combo_values else (
            combo_values[0] if combo_values else "")
        self.site_var = tk.StringVar(value=initial)
        self.site_combo = ttk.Combobox(ctrl, textvariable=self.site_var,
                                       values=combo_values, state="readonly", width=25)
        self.site_combo.pack(side=tk.LEFT, padx=8)
        self.site_combo.bind("<<ComboboxSelected>>", lambda e: self._on_site_changed())

        self.settings_btn = tk.Button(ctrl, text="Settings", command=self._open_settings,
                                      bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT)
        self.settings_btn.pack(side=tk.RIGHT)

        self.start_btn = tk.Button(ctrl, text="Start Publish", command=self._start_publish,
                                   bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                                   relief=tk.FLAT, padx=15)
        self.start_btn.pack(side=tk.RIGHT, padx=8)

        # Path info
        info_frame = tk.Frame(self.root, bg=colors["bg"])
        info_frame.pack(fill=tk.X, padx=12, pady=(0, 5))

        self.export_label = tk.Label(info_frame, text="Export: -", bg=colors["bg"],
                                     fg=colors["fg_dim"], font=("Arial", 9), anchor="w")
        self.export_label.pack(fill=tk.X)

        self.archive_label = tk.Label(info_frame, text="Archive: -", bg=colors["bg"],
                                      fg=colors["fg_dim"], font=("Arial", 9), anchor="w")
        self.archive_label.pack(fill=tk.X)

        # Steps frame
        steps_outer = tk.LabelFrame(self.root, text="Progress", bg=colors["bg"],
                                    fg=colors["fg_dim"], font=("Arial", 9))
        steps_outer.pack(fill=tk.X, padx=12, pady=(5, 5))

        self.steps_frame = tk.Frame(steps_outer, bg=colors["bg"])
        self.steps_frame.pack(fill=tk.X, padx=8, pady=6)

        # Output text
        output_frame = tk.Frame(self.root, bg=colors["bg"])
        output_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 5))

        self.output_text = scrolledtext.ScrolledText(
            output_frame, wrap=tk.WORD, bg="#0d1117", fg=colors["fg"],
            font=("Consolas", 9), insertbackground=colors["fg"],
            relief=tk.FLAT, state=tk.DISABLED
        )
        self.output_text.pack(fill=tk.BOTH, expand=True)
        self.output_text.tag_configure("error", foreground=colors["error"])
        self.output_text.tag_configure("success", foreground=colors["success"])
        self.output_text.tag_configure("info", foreground=colors["info"])

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        status_bar = tk.Label(self.root, textvariable=self.status_var, bg=colors["border"],
                              fg=colors["fg_dim"], anchor=tk.W, padx=10, font=("Arial", 9))
        status_bar.pack(fill=tk.X, side=tk.BOTTOM)

    def _get_current_site_key(self) -> str:
        return self.site_labels_map.get(self.site_var.get(), "")

    def _on_site_changed(self):
        site_key = self._get_current_site_key()
        if not site_key:
            return
        self.config.set_last_selected_site(site_key)
        cfg = self.config.get_site_config(site_key)
        self.export_label.config(text=f"Export: {cfg.get('export_dir', '-')}")
        archive_base = get_rak_settings().get_archive_path("Web")
        if cfg.get("is_personal", False):
            archive_path = os.path.join(archive_base, "_Personal", site_key)
        else:
            archive_path = os.path.join(archive_base, site_key)
        self.archive_label.config(text=f"Archive: {archive_path}")
        self._build_steps(site_key)

    def _build_steps(self, site_key: str):
        for w in self.steps_frame.winfo_children():
            w.destroy()
        self.step_labels = []

        cfg = self.config.get_site_config(site_key)
        has_wiki = cfg.get("has_wiki", False)
        has_dev = bool(cfg.get("dev_dir", "")
                       and os.path.isdir(cfg.get("dev_dir", "")))
        is_wordpress = cfg.get("is_wordpress", False)

        if has_wiki:
            steps = [
                "Validate",
                "FTP Upload (excl. wiki)",
                "Sync wiki to local",
                "Archive",
            ]
        else:
            steps = ["Validate", "FTP Upload", "Archive"]
        if has_dev:
            dev_label = "Dev Backup (WP + DB)" if is_wordpress else "Dev Backup"
            steps.append(dev_label)

        colors = FORM_COLORS
        for i, step in enumerate(steps):
            frame = tk.Frame(self.steps_frame, bg=colors["bg"])
            frame.pack(fill=tk.X, pady=1)

            icon = tk.Label(frame, text="  ", bg=colors["bg"], fg=colors["fg_dim"],
                            font=("Arial", 9), width=3)
            icon.pack(side=tk.LEFT)

            label = tk.Label(frame, text=f"{i + 1}. {step}", bg=colors["bg"],
                             fg=colors["fg_dim"], font=("Arial", 9), anchor="w")
            label.pack(side=tk.LEFT, fill=tk.X)

            self.step_labels.append((icon, label))

    def _append_output(self, msg: str, tag: str = None):
        self.output_text.config(state=tk.NORMAL)
        if tag:
            self.output_text.insert(tk.END, msg + "\n", tag)
        else:
            self.output_text.insert(tk.END, msg + "\n")
        self.output_text.see(tk.END)
        self.output_text.config(state=tk.DISABLED)

    def _open_settings(self):
        dialog = FTPSettingsDialog(self.root, self.config, self.winscp_mgr,
                                   initial_site_key=self._get_current_site_key())
        self.root.wait_window(dialog.dialog)
        # Reload after settings close (re-scans for new site folders)
        self.config = WebPublishConfig()
        self.winscp_mgr = WinSCPManager(self.config)
        self._refresh_site_list()

    def _refresh_site_list(self):
        """Rebuild the site combo box from current config."""
        site_keys = self.config.get_site_keys()
        self.site_labels_map = {}
        combo_values = []
        for k in site_keys:
            label = self.config.get_site_config(k).get("label", k)
            self.site_labels_map[label] = k
            combo_values.append(label)
        self.site_combo["values"] = combo_values
        if combo_values and self.site_var.get() not in combo_values:
            self.site_var.set(combo_values[0])
        self._on_site_changed()

    def _start_publish(self):
        if self.publish_running:
            return

        site_key = self._get_current_site_key()
        if not site_key:
            return

        self.publish_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.settings_btn.config(state=tk.DISABLED)
        self.site_combo.config(state=tk.DISABLED)

        # Clear output
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        self.output_text.config(state=tk.DISABLED)

        # Reset step icons
        for icon, label in self.step_labels:
            icon.config(text="  ", fg=FORM_COLORS["fg_dim"])
            label.config(fg=FORM_COLORS["fg_dim"])

        callbacks = {
            "on_step_start": self._on_step_start,
            "on_step_done": self._on_step_done,
            "on_output": lambda msg: self._append_output(msg),
            "on_complete": self._on_complete,
        }

        self.workflow = PublishWorkflow(
            self.config, self.winscp_mgr, site_key, self.root, callbacks
        )

        thread = threading.Thread(target=self.workflow.run, daemon=True)
        thread.start()

    def _on_step_start(self, idx: int, name: str):
        if idx < len(self.step_labels):
            icon, label = self.step_labels[idx]
            icon.config(text="...", fg=FORM_COLORS["warning"])
            label.config(fg=FORM_COLORS["fg"])
        self.status_var.set(f"Step {idx + 1}: {name}")

    def _on_step_done(self, idx: int, success: bool):
        if idx < len(self.step_labels):
            icon, label = self.step_labels[idx]
            if success:
                icon.config(text=" ok", fg=FORM_COLORS["success"])
                label.config(fg=FORM_COLORS["success"])
            else:
                icon.config(text=" !", fg=FORM_COLORS["error"])
                label.config(fg=FORM_COLORS["error"])

    def _on_complete(self, success: bool, msg: str):
        self.publish_running = False
        self.start_btn.config(state=tk.NORMAL)
        self.settings_btn.config(state=tk.NORMAL)
        self.site_combo.config(state="readonly")

        if success:
            self._append_output(f"\n{msg}", "success")
            self.status_var.set(msg)
        else:
            self._append_output(f"\n{msg}", "error")
            self.status_var.set(msg)


# ====================================
# MAIN
# ====================================

def main():
    setup_shared_logging("web_publish_static")

    root = tk.Tk()
    apply_category_icon(root)
    ui = PublishStaticUI(root)

    # Pre-select the site matching a project folder passed by the launcher.
    # The user can still pick a different site from the combobox.
    # When the folder name matches a configured site key exactly, kick off
    # the publish workflow automatically so the launcher acts as a one-click.
    if len(sys.argv) > 1 and sys.argv[1]:
        site_key = os.path.basename(os.path.normpath(sys.argv[1]))
        label = next(
            (lbl for lbl, key in ui.site_labels_map.items() if key == site_key),
            None,
        )
        if label:
            ui.site_var.set(label)
            ui._on_site_changed()
            # Defer until the window is mapped so the UI shows progress as it runs.
            root.after(200, ui._start_publish)
            logger.info(f"Auto-starting publish for matched site '{site_key}'")
        else:
            logger.info(f"No matching site for {site_key}; keeping last selection")

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
