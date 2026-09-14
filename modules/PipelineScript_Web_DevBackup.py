#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PipelineScript_Web_DevBackup.py
Author: Florian Dheer
Description: Backup and restore WordPress dev sites (files + database) from
             the Laragon www directory. Also backs up the Laragon environment
             (etc/, usr/) as a separate, portable snapshot.

Dev backups are portable to any LAMP/LEMP host — they contain the full site
files plus a mysqldump. The environment backup is Laragon-specific.
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
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
from shared_window_icon import apply_category_icon
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from shared_logging import get_logger, setup_logging as setup_shared_logging
from shared_wordpress import parse_wp_config as _shared_parse_wp_config
from rack_settings import get_rack_settings
from shared_appdata import get_appdata_path

logger = get_logger("web_devbackup")


# ====================================
# CONSTANTS
# ====================================

DEFAULT_EXCLUDE_DIRS = {".git", "node_modules", "vendor", "__pycache__", ".cache", ".tmp"}
DEFAULT_LARAGON_PATH = r"C:\laragon"

# Sites under these names are treated as personal (archived to _Personal/).
PERSONAL_SITES = {"floriandheer", "hyphen-v", "alles3d"}


# ====================================
# CONFIGURATION
# ====================================

class DevBackupConfig:
    """Configuration manager for WordPress dev backups."""

    def __init__(self):
        app_data = get_appdata_path()
        app_data.mkdir(parents=True, exist_ok=True)
        self.config_file = app_data / "web_devbackup_config.json"
        self.config = self._load_config()

    def _default_config(self) -> Dict:
        archive_root = ""
        try:
            archive_root = get_rack_settings().get_archive_path("Web")
        except Exception as e:
            logger.warning(f"Could not resolve default archive path: {e}")

        return {
            "laragon_path": DEFAULT_LARAGON_PATH,
            "backup_root": archive_root,
            "backup_max_per_site": 5,
            "backup_max_env": 3,
            "backup_exclude_dirs": sorted(DEFAULT_EXCLUDE_DIRS),
            "last_selected_site": "",
        }

    def _load_config(self) -> Dict:
        default = self._default_config()
        if self.config_file.exists():
            try:
                with open(self.config_file, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                self._merge(default, loaded)
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
            with open(self.config_file, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def save(self):
        self._save()

    # --- accessors ---
    def get_laragon_path(self) -> str:
        return self.config.get("laragon_path", DEFAULT_LARAGON_PATH)

    def set_laragon_path(self, path: str):
        self.config["laragon_path"] = path
        self._save()

    def get_www_path(self) -> str:
        return os.path.join(self.get_laragon_path(), "www")

    def get_backup_root(self) -> str:
        root = self.config.get("backup_root", "")
        if not root:
            try:
                root = get_rack_settings().get_archive_path("Web")
            except Exception:
                root = ""
        return root

    def set_backup_root(self, path: str):
        self.config["backup_root"] = path
        self._save()

    @staticmethod
    def is_personal_site(site: str) -> bool:
        return site.lower() in PERSONAL_SITES

    def get_site_backup_dir(self, site: str) -> str:
        root = self.get_backup_root()
        if self.is_personal_site(site):
            return os.path.join(root, "_Personal", site)
        return os.path.join(root, site)

    def get_env_backup_dir(self) -> str:
        return os.path.join(self.get_backup_root(), "_env")

    def get_max_per_site(self) -> int:
        return self.config.get("backup_max_per_site", 5)

    def set_max_per_site(self, v: int):
        self.config["backup_max_per_site"] = v
        self._save()

    def get_max_env(self) -> int:
        return self.config.get("backup_max_env", 3)

    def set_max_env(self, v: int):
        self.config["backup_max_env"] = v
        self._save()

    def get_exclude_dirs(self) -> List[str]:
        return self.config.get("backup_exclude_dirs", sorted(DEFAULT_EXCLUDE_DIRS))

    def set_exclude_dirs(self, dirs: List[str]):
        self.config["backup_exclude_dirs"] = dirs
        self._save()

    def get_last_selected_site(self) -> str:
        return self.config.get("last_selected_site", "")

    def set_last_selected_site(self, site: str):
        self.config["last_selected_site"] = site
        self._save()


# ====================================
# WORDPRESS SITE DISCOVERY
# ====================================

class WPSite:
    """A discovered WordPress site."""

    def __init__(self, name: str, path: str, wp_config_path: str,
                 db_name: str, db_user: str, db_password: str,
                 db_host: str, table_prefix: str):
        self.name = name
        self.path = path
        self.wp_config_path = wp_config_path
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_host = db_host
        self.table_prefix = table_prefix

    def __repr__(self):
        return f"WPSite({self.name}, db={self.db_name})"


class SiteDiscovery:
    """Finds WordPress sites by scanning the Laragon www folder."""

    @classmethod
    def parse_wp_config(cls, wp_config_path: str) -> Optional[Dict[str, str]]:
        """Parse wp-config.php for DB credentials. Returns dict or None on error.

        Thin wrapper around shared_wordpress.parse_wp_config so legacy callers
        (and tests) that reach for SiteDiscovery.parse_wp_config keep working.
        """
        return _shared_parse_wp_config(wp_config_path)

    @classmethod
    def discover(cls, www_path: str) -> List[WPSite]:
        """Scan www path, return list of WordPress sites (by wp-config.php presence)."""
        sites = []
        if not os.path.isdir(www_path):
            logger.warning(f"www path does not exist: {www_path}")
            return sites

        for entry in sorted(os.listdir(www_path)):
            site_path = os.path.join(www_path, entry)
            if not os.path.isdir(site_path):
                continue

            wp_config = os.path.join(site_path, "wp-config.php")
            if not os.path.isfile(wp_config):
                continue

            parsed = cls.parse_wp_config(wp_config)
            if not parsed:
                logger.info(f"Skipping {entry}: wp-config.php could not be parsed")
                continue

            sites.append(WPSite(
                name=entry,
                path=site_path,
                wp_config_path=wp_config,
                db_name=parsed["db_name"],
                db_user=parsed["db_user"],
                db_password=parsed["db_password"],
                db_host=parsed["db_host"],
                table_prefix=parsed["table_prefix"],
            ))

        return sites


# ====================================
# MYSQL MANAGER
# ====================================

class MysqlManager:
    """Detects mysqldump / mysql binaries in the Laragon bin tree, runs dumps and restores."""

    def __init__(self, laragon_path: str):
        self.laragon_path = laragon_path
        self._mysqldump_path: Optional[str] = None
        self._mysql_path: Optional[str] = None

    def _find_bin(self, name: str) -> Optional[str]:
        """Search <laragon>\\bin\\mysql\\*\\bin\\<name>.exe."""
        pattern = os.path.join(self.laragon_path, "bin", "mysql", "*", "bin", f"{name}.exe")
        matches = sorted(glob.glob(pattern))
        if matches:
            # Prefer highest version (sort order)
            return matches[-1]
        # Fallback: search PATH
        for dir_path in os.environ.get("PATH", "").split(os.pathsep):
            candidate = os.path.join(dir_path, f"{name}.exe")
            if os.path.isfile(candidate):
                return candidate
        return None

    def get_mysqldump(self) -> Optional[str]:
        if self._mysqldump_path is None:
            self._mysqldump_path = self._find_bin("mysqldump")
        return self._mysqldump_path

    def get_mysql(self) -> Optional[str]:
        if self._mysql_path is None:
            self._mysql_path = self._find_bin("mysql")
        return self._mysql_path

    @staticmethod
    def _write_defaults_file(site: WPSite) -> str:
        """Write a temp [client] defaults file to avoid passing password on CLI.
        Returns path to temp file (caller must delete)."""
        fd, path = tempfile.mkstemp(prefix="wpbackup_", suffix=".cnf", text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write("[client]\n")
                f.write(f"user={site.db_user}\n")
                f.write(f"password={site.db_password}\n")
                f.write(f"host={site.db_host}\n")
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
        return path

    def dump_database(self, site: WPSite, output_sql_path: str,
                      on_output=None) -> Tuple[bool, str]:
        """Dump the site database to a .sql file. Returns (success, message)."""
        mysqldump = self.get_mysqldump()
        if not mysqldump:
            return False, f"mysqldump.exe not found under {self.laragon_path}\\bin\\mysql"

        defaults_file = None
        try:
            defaults_file = self._write_defaults_file(site)
            cmd = [
                mysqldump,
                f"--defaults-extra-file={defaults_file}",
                "--single-transaction",
                "--routines",
                "--triggers",
                "--events",
                "--add-drop-database",
                "--databases",
                site.db_name,
            ]
            if on_output:
                on_output(f"  Dumping database '{site.db_name}' ...")

            with open(output_sql_path, "wb") as out:
                proc = subprocess.Popen(
                    cmd, stdout=out, stderr=subprocess.PIPE
                )
                _, stderr = proc.communicate()

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return False, f"mysqldump failed: {err}"

            size_mb = os.path.getsize(output_sql_path) / (1024 * 1024)
            return True, f"DB dump OK ({size_mb:.2f} MB)"

        except Exception as e:
            return False, f"Dump error: {e}"
        finally:
            if defaults_file:
                try:
                    os.unlink(defaults_file)
                except OSError:
                    pass

    def restore_database(self, site: WPSite, sql_path: str,
                         on_output=None) -> Tuple[bool, str]:
        """Restore a .sql file into the site's database (replaces existing data)."""
        mysql = self.get_mysql()
        if not mysql:
            return False, f"mysql.exe not found under {self.laragon_path}\\bin\\mysql"

        defaults_file = None
        try:
            defaults_file = self._write_defaults_file(site)
            cmd = [
                mysql,
                f"--defaults-extra-file={defaults_file}",
            ]
            if on_output:
                on_output(f"  Importing SQL into '{site.db_name}' ...")

            with open(sql_path, "rb") as inp:
                proc = subprocess.Popen(
                    cmd, stdin=inp,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE
                )
                _, stderr = proc.communicate()

            if proc.returncode != 0:
                err = stderr.decode("utf-8", errors="replace").strip()
                return False, f"mysql import failed: {err}"

            return True, "DB restore OK"

        except Exception as e:
            return False, f"Restore error: {e}"
        finally:
            if defaults_file:
                try:
                    os.unlink(defaults_file)
                except OSError:
                    pass


# ====================================
# BACKUP & RESTORE
# ====================================

# Name inside the zip for the SQL dump
SQL_ARCNAME = "__db__.sql"


class BackupManager:
    """Creates and rotates dev_* and env_* backup archives."""

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d_%H%M%S")

    @staticmethod
    def _zip_directory(zf: zipfile.ZipFile, source_dir: str,
                       arc_prefix: str, exclude_dirs: set,
                       on_output=None) -> int:
        """Walk source_dir and add files to zf under arc_prefix. Returns file count."""
        count = 0
        source_abs = os.path.abspath(source_dir)
        for root, dirs, files in os.walk(source_abs):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for f in files:
                full = os.path.join(root, f)
                rel = os.path.relpath(full, source_abs)
                arcname = os.path.join(arc_prefix, rel) if arc_prefix else rel
                try:
                    zf.write(full, arcname)
                    count += 1
                except (OSError, PermissionError) as e:
                    if on_output:
                        on_output(f"    skip {rel}: {e}")
        return count

    @classmethod
    def backup_site(cls, site: WPSite, archive_dir: str,
                    mysql_mgr: MysqlManager, exclude_dirs: List[str],
                    on_output=None) -> Tuple[bool, str]:
        """Create dev_<site>_<ts>.zip containing site files + SQL dump."""
        def log(m):
            if on_output:
                on_output(m)
            logger.info(m)

        if not os.path.isdir(site.path):
            return False, f"Site path not accessible: {site.path}"

        os.makedirs(archive_dir, exist_ok=True)
        ts = cls._timestamp()
        zip_name = f"dev_{site.name}_{ts}.zip"
        zip_path = os.path.join(archive_dir, zip_name)

        # Dump DB to temp file first
        sql_tmp = None
        try:
            fd, sql_tmp = tempfile.mkstemp(prefix=f"wpdump_{site.name}_", suffix=".sql")
            os.close(fd)

            ok, msg = mysql_mgr.dump_database(site, sql_tmp, on_output=log)
            if not ok:
                return False, msg
            log(f"  {msg}")

            exclude_set = set(exclude_dirs)
            log(f"  Zipping files (excluding: {', '.join(sorted(exclude_set))}) ...")

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zf:
                # Files go under "files/" prefix; SQL dump sits at zip root
                file_count = cls._zip_directory(
                    zf, site.path, "files", exclude_set, on_output=log
                )
                zf.write(sql_tmp, SQL_ARCNAME)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            msg = f"{zip_name} ({file_count} files + DB, {size_mb:.1f} MB)"
            log(f"  Created: {msg}")
            return True, msg

        except Exception as e:
            logger.exception("Site backup failed")
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            return False, f"Backup error: {e}"
        finally:
            if sql_tmp and os.path.exists(sql_tmp):
                try:
                    os.unlink(sql_tmp)
                except OSError:
                    pass

    @classmethod
    def backup_environment(cls, laragon_path: str, archive_dir: str,
                           on_output=None) -> Tuple[bool, str]:
        """Zip <laragon>\\etc and <laragon>\\usr into env_laragon_<ts>.zip."""
        def log(m):
            if on_output:
                on_output(m)
            logger.info(m)

        etc_dir = os.path.join(laragon_path, "etc")
        usr_dir = os.path.join(laragon_path, "usr")

        if not os.path.isdir(etc_dir) and not os.path.isdir(usr_dir):
            return False, f"Neither etc nor usr found in {laragon_path}"

        os.makedirs(archive_dir, exist_ok=True)
        ts = cls._timestamp()
        zip_name = f"env_laragon_{ts}.zip"
        zip_path = os.path.join(archive_dir, zip_name)

        try:
            total = 0
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED,
                                 allowZip64=True) as zf:
                if os.path.isdir(etc_dir):
                    log("  Zipping etc/ ...")
                    total += cls._zip_directory(zf, etc_dir, "etc", set(), on_output=log)
                if os.path.isdir(usr_dir):
                    log("  Zipping usr/ ...")
                    total += cls._zip_directory(zf, usr_dir, "usr", set(), on_output=log)

            size_mb = os.path.getsize(zip_path) / (1024 * 1024)
            msg = f"{zip_name} ({total} files, {size_mb:.1f} MB)"
            log(f"  Created: {msg}")
            return True, msg

        except Exception as e:
            logger.exception("Env backup failed")
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except OSError:
                    pass
            return False, f"Env backup error: {e}"

    @staticmethod
    def rotate(archive_dir: str, pattern: str, max_keep: int, on_output=None):
        """Keep the newest max_keep files matching pattern in archive_dir."""
        backups = sorted(glob.glob(os.path.join(archive_dir, pattern)))
        if len(backups) > max_keep:
            for old in backups[:len(backups) - max_keep]:
                try:
                    os.remove(old)
                    msg = f"Rotated (removed old): {os.path.basename(old)}"
                    logger.info(msg)
                    if on_output:
                        on_output(f"  {msg}")
                except OSError as e:
                    logger.warning(f"Failed to remove {old}: {e}")

    @staticmethod
    def list_site_backups(archive_dir: str, site: str) -> List[str]:
        if not os.path.isdir(archive_dir):
            return []
        return sorted(
            glob.glob(os.path.join(archive_dir, f"dev_{site}_*.zip")),
            reverse=True,
        )

    @staticmethod
    def list_env_backups(archive_dir: str) -> List[str]:
        if not os.path.isdir(archive_dir):
            return []
        return sorted(
            glob.glob(os.path.join(archive_dir, "env_laragon_*.zip")),
            reverse=True,
        )


class RestoreManager:
    """Restores site and environment backups."""

    @classmethod
    def restore_site(cls, site: WPSite, zip_path: str,
                     mysql_mgr: MysqlManager, on_output=None) -> Tuple[bool, str]:
        """Extract files/ over site path and import SQL. Destructive."""
        def log(m):
            if on_output:
                on_output(m)
            logger.info(m)

        if not os.path.isfile(zip_path):
            return False, f"Backup not found: {zip_path}"
        if not os.path.isdir(site.path):
            return False, f"Site target not accessible: {site.path}"

        tmp_dir = tempfile.mkdtemp(prefix=f"wprestore_{site.name}_")
        try:
            log(f"  Extracting {os.path.basename(zip_path)} ...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                if SQL_ARCNAME not in names:
                    return False, f"Archive has no {SQL_ARCNAME} — not a valid site backup"
                zf.extractall(tmp_dir)

            sql_path = os.path.join(tmp_dir, SQL_ARCNAME)
            files_src = os.path.join(tmp_dir, "files")
            if not os.path.isdir(files_src):
                return False, "Archive missing 'files/' folder"

            # Import SQL first — if it fails, files aren't touched yet
            log("  Importing database ...")
            ok, msg = mysql_mgr.restore_database(site, sql_path, on_output=log)
            if not ok:
                return False, f"DB restore failed, files NOT touched: {msg}"
            log(f"  {msg}")

            # Overlay files: copy extracted files over site path
            log(f"  Copying files over {site.path} ...")
            copied = 0
            for root, dirs, files in os.walk(files_src):
                rel_root = os.path.relpath(root, files_src)
                dest_root = site.path if rel_root == "." else os.path.join(site.path, rel_root)
                os.makedirs(dest_root, exist_ok=True)
                for f in files:
                    src = os.path.join(root, f)
                    dst = os.path.join(dest_root, f)
                    try:
                        shutil.copy2(src, dst)
                        copied += 1
                    except (OSError, PermissionError) as e:
                        log(f"    skip {f}: {e}")

            return True, f"Restored {copied} files + database"

        except Exception as e:
            logger.exception("Site restore failed")
            return False, f"Restore error: {e}"
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError:
                pass

    @classmethod
    def restore_environment(cls, laragon_path: str, zip_path: str,
                            on_output=None) -> Tuple[bool, str]:
        """Extract etc/ and usr/ from env zip into the Laragon install."""
        def log(m):
            if on_output:
                on_output(m)
            logger.info(m)

        if not os.path.isfile(zip_path):
            return False, f"Backup not found: {zip_path}"
        if not os.path.isdir(laragon_path):
            return False, f"Laragon path not found: {laragon_path}"

        tmp_dir = tempfile.mkdtemp(prefix="laragonenv_")
        try:
            log(f"  Extracting {os.path.basename(zip_path)} ...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_dir)

            copied = 0
            for sub in ("etc", "usr"):
                src = os.path.join(tmp_dir, sub)
                dst = os.path.join(laragon_path, sub)
                if not os.path.isdir(src):
                    continue
                log(f"  Copying {sub}/ ...")
                for root, dirs, files in os.walk(src):
                    rel_root = os.path.relpath(root, src)
                    dest_root = dst if rel_root == "." else os.path.join(dst, rel_root)
                    os.makedirs(dest_root, exist_ok=True)
                    for f in files:
                        s = os.path.join(root, f)
                        d = os.path.join(dest_root, f)
                        try:
                            shutil.copy2(s, d)
                            copied += 1
                        except (OSError, PermissionError) as e:
                            log(f"    skip {f}: {e}")

            return True, f"Restored {copied} env files (restart Laragon to apply)"

        except Exception as e:
            logger.exception("Env restore failed")
            return False, f"Env restore error: {e}"
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except OSError:
                pass


# ====================================
# UI
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


class SettingsDialog:
    def __init__(self, parent: tk.Tk, config: DevBackupConfig):
        self.parent = parent
        self.config = config
        self.changed = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Dev Backup Settings")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.dialog.configure(bg=FORM_COLORS["bg"])

        self._build_ui()
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        c = FORM_COLORS
        pad = {"padx": 15, "pady": 6}

        # Laragon path
        lf = tk.LabelFrame(self.dialog, text="Laragon Install Path",
                           bg=c["bg"], fg=c["fg_dim"], font=("Arial", 9))
        lf.pack(fill=tk.X, **pad)
        row = tk.Frame(lf, bg=c["bg"])
        row.pack(fill=tk.X, padx=10, pady=8)
        self.laragon_var = tk.StringVar(value=self.config.get_laragon_path())
        tk.Entry(row, textvariable=self.laragon_var, width=50,
                 bg=c["bg_card"], fg=c["fg"], insertbackground=c["fg"],
                 font=("Consolas", 9), relief=tk.FLAT).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tk.Button(row, text="Browse", command=self._browse_laragon,
                  bg=c["accent"], fg=c["fg"], relief=tk.FLAT).pack(side=tk.LEFT)

        # Backup root
        bf = tk.LabelFrame(self.dialog, text="Backup Root Directory",
                           bg=c["bg"], fg=c["fg_dim"], font=("Arial", 9))
        bf.pack(fill=tk.X, **pad)
        row = tk.Frame(bf, bg=c["bg"])
        row.pack(fill=tk.X, padx=10, pady=8)
        self.root_var = tk.StringVar(value=self.config.get_backup_root())
        tk.Entry(row, textvariable=self.root_var, width=50,
                 bg=c["bg_card"], fg=c["fg"], insertbackground=c["fg"],
                 font=("Consolas", 9), relief=tk.FLAT).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        tk.Button(row, text="Browse", command=self._browse_root,
                  bg=c["accent"], fg=c["fg"], relief=tk.FLAT).pack(side=tk.LEFT)

        # Retention
        rf = tk.LabelFrame(self.dialog, text="Retention",
                           bg=c["bg"], fg=c["fg_dim"], font=("Arial", 9))
        rf.pack(fill=tk.X, **pad)

        row = tk.Frame(rf, bg=c["bg"])
        row.pack(fill=tk.X, padx=10, pady=(8, 4))
        tk.Label(row, text="Max backups per site:",
                 bg=c["bg"], fg=c["fg"], font=("Arial", 10)).pack(side=tk.LEFT)
        self.max_site_var = tk.StringVar(value=str(self.config.get_max_per_site()))
        tk.Spinbox(row, from_=1, to=50, textvariable=self.max_site_var, width=5,
                   bg=c["bg_card"], fg=c["fg"], insertbackground=c["fg"],
                   font=("Consolas", 10), relief=tk.FLAT,
                   buttonbackground=c["accent"]).pack(side=tk.LEFT, padx=8)

        row = tk.Frame(rf, bg=c["bg"])
        row.pack(fill=tk.X, padx=10, pady=4)
        tk.Label(row, text="Max environment backups:",
                 bg=c["bg"], fg=c["fg"], font=("Arial", 10)).pack(side=tk.LEFT)
        self.max_env_var = tk.StringVar(value=str(self.config.get_max_env()))
        tk.Spinbox(row, from_=1, to=30, textvariable=self.max_env_var, width=5,
                   bg=c["bg_card"], fg=c["fg"], insertbackground=c["fg"],
                   font=("Consolas", 10), relief=tk.FLAT,
                   buttonbackground=c["accent"]).pack(side=tk.LEFT, padx=8)

        # Exclude dirs
        row = tk.Frame(rf, bg=c["bg"])
        row.pack(fill=tk.X, padx=10, pady=(4, 8))
        tk.Label(row, text="Exclude dirs:",
                 bg=c["bg"], fg=c["fg"], font=("Arial", 10)).pack(side=tk.LEFT)
        self.excl_var = tk.StringVar(value=", ".join(self.config.get_exclude_dirs()))
        tk.Entry(row, textvariable=self.excl_var,
                 bg=c["bg_card"], fg=c["fg"], insertbackground=c["fg"],
                 font=("Consolas", 9), relief=tk.FLAT).pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=8)

        # Buttons
        btn = tk.Frame(self.dialog, bg=c["bg"])
        btn.pack(fill=tk.X, padx=15, pady=(5, 15))
        tk.Button(btn, text="Cancel", command=self.dialog.destroy,
                  bg=c["accent"], fg=c["fg"], relief=tk.FLAT,
                  padx=15).pack(side=tk.RIGHT, padx=5)
        tk.Button(btn, text="Save", command=self._on_save,
                  bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                  relief=tk.FLAT, padx=15).pack(side=tk.RIGHT)

    def _browse_laragon(self):
        d = filedialog.askdirectory(title="Select Laragon Install Folder")
        if d:
            self.laragon_var.set(d.replace("/", "\\"))

    def _browse_root(self):
        d = filedialog.askdirectory(title="Select Backup Root Directory")
        if d:
            self.root_var.set(d.replace("/", "\\"))

    def _on_save(self):
        lp = self.laragon_var.get().strip()
        br = self.root_var.get().strip()
        if not lp or not br:
            messagebox.showerror("Error", "Paths cannot be empty.",
                                 parent=self.dialog)
            return
        try:
            ms = int(self.max_site_var.get())
            me = int(self.max_env_var.get())
            if ms < 1 or me < 1:
                raise ValueError
        except ValueError:
            messagebox.showerror("Error", "Retention must be positive integers.",
                                 parent=self.dialog)
            return

        excl = [d.strip() for d in self.excl_var.get().split(",") if d.strip()]

        self.config.set_laragon_path(lp)
        self.config.set_backup_root(br)
        self.config.set_max_per_site(ms)
        self.config.set_max_env(me)
        self.config.set_exclude_dirs(excl)
        self.changed = True
        self.dialog.destroy()


class RestorePickerDialog:
    """Pick a backup zip from a list."""

    def __init__(self, parent: tk.Tk, title: str, backups: List[str]):
        self.parent = parent
        self.selected: Optional[str] = None

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(title)
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.configure(bg=FORM_COLORS["bg"])
        self.dialog.geometry("600x380")

        c = FORM_COLORS
        tk.Label(self.dialog, text="Select a backup to restore:",
                 bg=c["bg"], fg=c["fg"], font=("Arial", 10)).pack(
            anchor=tk.W, padx=12, pady=(12, 4))

        list_frame = tk.Frame(self.dialog, bg=c["bg"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=4)

        self.listbox = tk.Listbox(list_frame, bg=c["bg_card"], fg=c["fg"],
                                  selectbackground=c["highlight"],
                                  selectforeground="white",
                                  font=("Consolas", 9), relief=tk.FLAT,
                                  activestyle="none")
        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL,
                           command=self.listbox.yview)
        self.listbox.configure(yscrollcommand=sb.set)
        self.listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self.backups = backups
        for b in backups:
            size_mb = os.path.getsize(b) / (1024 * 1024)
            mtime = datetime.fromtimestamp(os.path.getmtime(b)).strftime("%Y-%m-%d %H:%M")
            self.listbox.insert(tk.END, f"{os.path.basename(b)}   [{size_mb:.1f} MB, {mtime}]")
        if backups:
            self.listbox.selection_set(0)

        btn = tk.Frame(self.dialog, bg=c["bg"])
        btn.pack(fill=tk.X, padx=12, pady=(4, 12))
        tk.Button(btn, text="Cancel", command=self.dialog.destroy,
                  bg=c["accent"], fg=c["fg"], relief=tk.FLAT,
                  padx=15).pack(side=tk.RIGHT, padx=5)
        tk.Button(btn, text="Restore", command=self._on_pick,
                  bg="#c0392b", fg="white", font=("Arial", 10, "bold"),
                  relief=tk.FLAT, padx=15).pack(side=tk.RIGHT)

    def _on_pick(self):
        sel = self.listbox.curselection()
        if not sel:
            return
        self.selected = self.backups[sel[0]]
        self.dialog.destroy()


class DevBackupUI:
    """Main Tk UI for WordPress dev backups."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("WordPress Dev Backup")
        self.root.geometry("880x680")
        self.root.minsize(780, 600)
        self.root.configure(bg=FORM_COLORS["bg"])

        self.config = DevBackupConfig()
        self.operation_running = False
        self.sites: List[WPSite] = []

        self._build_ui()
        self._refresh_sites()

    def _build_ui(self):
        c = FORM_COLORS

        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=55)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="WordPress Dev Backup",
                 font=("Arial", 15, "bold"),
                 fg="white", bg="#2c3e50").place(
            relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Top path bar
        top = tk.Frame(self.root, bg=c["bg"])
        top.pack(fill=tk.X, padx=12, pady=(10, 5))
        tk.Label(top, text="Laragon www:", bg=c["bg"], fg=c["fg"],
                 font=("Arial", 10)).pack(side=tk.LEFT)
        self.www_label = tk.Label(top, text=self.config.get_www_path(),
                                  bg=c["bg_card"], fg=c["fg"],
                                  font=("Consolas", 9), relief=tk.FLAT,
                                  anchor=tk.W, padx=6, pady=2)
        self.www_label.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)
        tk.Button(top, text="Settings", command=self._open_settings,
                  bg=c["highlight"], fg=c["fg"], relief=tk.FLAT,
                  padx=10).pack(side=tk.LEFT)
        tk.Button(top, text="Refresh", command=self._refresh_sites,
                  bg=c["accent"], fg=c["fg"], relief=tk.FLAT).pack(
            side=tk.LEFT, padx=(5, 0))

        # Backup root display
        root_row = tk.Frame(self.root, bg=c["bg"])
        root_row.pack(fill=tk.X, padx=12, pady=(0, 5))
        tk.Label(root_row, text="Backup root:", bg=c["bg"], fg=c["fg_dim"],
                 font=("Arial", 9)).pack(side=tk.LEFT)
        self.root_label = tk.Label(root_row, text=self.config.get_backup_root(),
                                   bg=c["bg"], fg=c["fg_dim"],
                                   font=("Consolas", 9), anchor=tk.W)
        self.root_label.pack(side=tk.LEFT, padx=6, fill=tk.X, expand=True)

        # Site list
        lf = tk.LabelFrame(self.root, text="Discovered WordPress Sites",
                           bg=c["bg"], fg=c["fg_dim"], font=("Arial", 9))
        lf.pack(fill=tk.BOTH, expand=True, padx=12, pady=(5, 5))

        tf = tk.Frame(lf, bg=c["bg"])
        tf.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.tree = ttk.Treeview(tf,
                                 columns=("name", "db", "path", "backups", "last"),
                                 show="headings", height=8)
        self.tree.heading("name", text="Site")
        self.tree.heading("db", text="Database")
        self.tree.heading("path", text="Path")
        self.tree.heading("backups", text="# Backups")
        self.tree.heading("last", text="Last Backup")
        self.tree.column("name", width=130, minwidth=100)
        self.tree.column("db", width=120, minwidth=80)
        self.tree.column("path", width=300, minwidth=150)
        self.tree.column("backups", width=80, minwidth=60, anchor=tk.CENTER)
        self.tree.column("last", width=140, minwidth=100)

        sb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        # Treeview style
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background=c["bg_card"],
                        foreground=c["fg"], fieldbackground=c["bg_card"],
                        font=("Consolas", 9))
        style.configure("Treeview.Heading", background=c["accent"],
                        foreground=c["fg"], font=("Arial", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", c["highlight"])],
                  foreground=[("selected", "white")])

        # Buttons row 1: site actions
        br = tk.Frame(self.root, bg=c["bg"])
        br.pack(fill=tk.X, padx=12, pady=(0, 4))

        self.backup_sel_btn = tk.Button(br, text="Backup Selected",
                                        command=self._backup_selected,
                                        bg="#2980b9", fg="white",
                                        font=("Arial", 10, "bold"),
                                        relief=tk.FLAT, padx=15)
        self.backup_sel_btn.pack(side=tk.LEFT)

        self.backup_all_btn = tk.Button(br, text="Backup All",
                                        command=self._backup_all,
                                        bg="#2980b9", fg="white",
                                        relief=tk.FLAT, padx=15)
        self.backup_all_btn.pack(side=tk.LEFT, padx=8)

        self.restore_btn = tk.Button(br, text="Restore Selected",
                                     command=self._restore_selected,
                                     bg="#c0392b", fg="white",
                                     font=("Arial", 10, "bold"),
                                     relief=tk.FLAT, padx=15)
        self.restore_btn.pack(side=tk.LEFT, padx=(20, 0))

        # Buttons row 2: env
        br2 = tk.Frame(self.root, bg=c["bg"])
        br2.pack(fill=tk.X, padx=12, pady=(0, 6))

        tk.Label(br2, text="Laragon environment:",
                 bg=c["bg"], fg=c["fg_dim"], font=("Arial", 9)).pack(side=tk.LEFT)
        self.env_backup_btn = tk.Button(br2, text="Backup Environment",
                                        command=self._backup_env,
                                        bg=c["accent"], fg=c["fg"],
                                        relief=tk.FLAT, padx=15)
        self.env_backup_btn.pack(side=tk.LEFT, padx=8)
        self.env_restore_btn = tk.Button(br2, text="Restore Environment",
                                         command=self._restore_env,
                                         bg=c["accent"], fg=c["fg"],
                                         relief=tk.FLAT, padx=15)
        self.env_restore_btn.pack(side=tk.LEFT)

        # Output log
        of = tk.Frame(self.root, bg=c["bg"])
        of.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 5))
        self.output = scrolledtext.ScrolledText(
            of, wrap=tk.WORD, bg="#0d1117", fg=c["fg"],
            font=("Consolas", 9), insertbackground=c["fg"],
            relief=tk.FLAT, state=tk.DISABLED, height=10)
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.tag_configure("error", foreground=c["error"])
        self.output.tag_configure("success", foreground=c["success"])
        self.output.tag_configure("info", foreground=c["info"])
        self.output.tag_configure("warn", foreground=c["warning"])

        # Status bar
        self.status_var = tk.StringVar(value="Ready")
        tk.Label(self.root, textvariable=self.status_var,
                 bg=c["border"], fg=c["fg_dim"],
                 anchor=tk.W, padx=10, font=("Arial", 9)).pack(
            fill=tk.X, side=tk.BOTTOM)

    # --- UI helpers ---
    def _append(self, msg: str, tag: str = None):
        self.output.config(state=tk.NORMAL)
        if tag:
            self.output.insert(tk.END, msg + "\n", tag)
        else:
            self.output.insert(tk.END, msg + "\n")
        self.output.see(tk.END)
        self.output.config(state=tk.DISABLED)

    def _clear(self):
        self.output.config(state=tk.NORMAL)
        self.output.delete(1.0, tk.END)
        self.output.config(state=tk.DISABLED)

    def _set_busy(self, busy: bool):
        state = tk.DISABLED if busy else tk.NORMAL
        for b in (self.backup_sel_btn, self.backup_all_btn, self.restore_btn,
                  self.env_backup_btn, self.env_restore_btn):
            b.config(state=state)
        self.operation_running = busy

    def _safe_append(self, msg: str, tag: str = None):
        self.root.after(0, self._append, msg, tag)

    def _open_settings(self):
        d = SettingsDialog(self.root, self.config)
        self.root.wait_window(d.dialog)
        if d.changed:
            self.www_label.config(text=self.config.get_www_path())
            self.root_label.config(text=self.config.get_backup_root())
            self._refresh_sites()

    # --- site list ---
    def _refresh_sites(self):
        www = self.config.get_www_path()
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not os.path.isdir(www):
            self.status_var.set(f"www path not found: {www}")
            self.sites = []
            return

        self.sites = SiteDiscovery.discover(www)
        for s in self.sites:
            backup_dir = self.config.get_site_backup_dir(s.name)
            backups = BackupManager.list_site_backups(backup_dir, s.name)
            last = ""
            if backups:
                last = datetime.fromtimestamp(
                    os.path.getmtime(backups[0])).strftime("%Y-%m-%d %H:%M")
            self.tree.insert("", tk.END, values=(
                s.name, s.db_name, s.path, len(backups), last))

        self.status_var.set(f"{len(self.sites)} WordPress site(s) found")

    def _get_selected_site(self) -> Optional[WPSite]:
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Info", "Select a site from the list first.",
                                parent=self.root)
            return None
        name = self.tree.item(sel[0], "values")[0]
        for s in self.sites:
            if s.name == name:
                return s
        return None

    # --- backup actions ---
    def _backup_selected(self):
        site = self._get_selected_site()
        if not site:
            return
        self._run_backup([site])

    def _backup_all(self):
        if not self.sites:
            messagebox.showinfo("No Sites",
                                "No WordPress sites discovered.",
                                parent=self.root)
            return
        self._run_backup(list(self.sites))

    def _run_backup(self, sites: List[WPSite]):
        self._clear()
        self._set_busy(True)
        self.status_var.set("Backing up ...")

        mysql_mgr = MysqlManager(self.config.get_laragon_path())
        if not mysql_mgr.get_mysqldump():
            self._append(
                f"mysqldump.exe not found under {self.config.get_laragon_path()}\\bin\\mysql",
                "error")
            self._set_busy(False)
            self.status_var.set("mysqldump missing")
            return

        exclude = self.config.get_exclude_dirs()
        max_site = self.config.get_max_per_site()

        def run():
            ok_count = 0
            fail_count = 0
            for site in sites:
                self._safe_append(f"\n=== {site.name} (db: {site.db_name}) ===", "info")
                archive_dir = self.config.get_site_backup_dir(site.name)
                ok, msg = BackupManager.backup_site(
                    site, archive_dir, mysql_mgr, exclude,
                    on_output=lambda m: self._safe_append(m))
                if ok:
                    BackupManager.rotate(
                        archive_dir, f"dev_{site.name}_*.zip", max_site,
                        on_output=lambda m: self._safe_append(m))
                    self._safe_append(f"  OK: {msg}", "success")
                    ok_count += 1
                else:
                    self._safe_append(f"  FAIL: {msg}", "error")
                    fail_count += 1

            summary = f"\nBackup complete: {ok_count} ok, {fail_count} failed"
            self._safe_append(summary,
                              "success" if fail_count == 0 else "error")
            self.root.after(0, lambda: self.status_var.set(summary.strip()))
            self.root.after(0, lambda: self._set_busy(False))
            self.root.after(0, self._refresh_sites)

        threading.Thread(target=run, daemon=True).start()

    # --- restore site ---
    def _restore_selected(self):
        site = self._get_selected_site()
        if not site:
            return

        archive_dir = self.config.get_site_backup_dir(site.name)
        backups = BackupManager.list_site_backups(archive_dir, site.name)
        if not backups:
            messagebox.showinfo("No Backups",
                                f"No backups found for {site.name}.",
                                parent=self.root)
            return

        picker = RestorePickerDialog(
            self.root, f"Restore {site.name}", backups)
        self.root.wait_window(picker.dialog)
        if not picker.selected:
            return

        confirm = messagebox.askyesno(
            "Confirm Restore",
            f"This will OVERWRITE the site at:\n  {site.path}\n\n"
            f"and REPLACE the '{site.db_name}' database "
            f"with the contents of:\n  {os.path.basename(picker.selected)}\n\n"
            "This cannot be undone (unless you have a more recent backup).\n\n"
            "Continue?",
            parent=self.root, icon="warning")
        if not confirm:
            return

        self._clear()
        self._set_busy(True)
        self.status_var.set(f"Restoring {site.name} ...")

        mysql_mgr = MysqlManager(self.config.get_laragon_path())
        backup_path = picker.selected

        def run():
            self._safe_append(f"Restoring {site.name} from {os.path.basename(backup_path)} ...", "info")
            ok, msg = RestoreManager.restore_site(
                site, backup_path, mysql_mgr,
                on_output=lambda m: self._safe_append(m))
            if ok:
                self._safe_append(f"OK: {msg}", "success")
                self.root.after(0, lambda: self.status_var.set(f"Restored {site.name}"))
            else:
                self._safe_append(f"FAIL: {msg}", "error")
                self.root.after(0, lambda: self.status_var.set(f"Restore failed"))
            self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=run, daemon=True).start()

    # --- env backup / restore ---
    def _backup_env(self):
        self._clear()
        self._set_busy(True)
        self.status_var.set("Backing up environment ...")

        laragon = self.config.get_laragon_path()
        archive_dir = self.config.get_env_backup_dir()
        max_env = self.config.get_max_env()

        def run():
            self._safe_append(f"Backing up Laragon env from {laragon} ...", "info")
            ok, msg = BackupManager.backup_environment(
                laragon, archive_dir,
                on_output=lambda m: self._safe_append(m))
            if ok:
                BackupManager.rotate(
                    archive_dir, "env_laragon_*.zip", max_env,
                    on_output=lambda m: self._safe_append(m))
                self._safe_append(f"OK: {msg}", "success")
                self.root.after(0, lambda: self.status_var.set("Env backup OK"))
            else:
                self._safe_append(f"FAIL: {msg}", "error")
                self.root.after(0, lambda: self.status_var.set("Env backup failed"))
            self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=run, daemon=True).start()

    def _restore_env(self):
        archive_dir = self.config.get_env_backup_dir()
        backups = BackupManager.list_env_backups(archive_dir)
        if not backups:
            messagebox.showinfo("No Backups",
                                "No environment backups found.",
                                parent=self.root)
            return

        picker = RestorePickerDialog(
            self.root, "Restore Laragon Environment", backups)
        self.root.wait_window(picker.dialog)
        if not picker.selected:
            return

        laragon = self.config.get_laragon_path()
        confirm = messagebox.askyesno(
            "Confirm Env Restore",
            f"This will overwrite files under:\n  {laragon}\\etc\n  {laragon}\\usr\n\n"
            f"with contents from:\n  {os.path.basename(picker.selected)}\n\n"
            "Laragon must be stopped before restoring. Continue?",
            parent=self.root, icon="warning")
        if not confirm:
            return

        self._clear()
        self._set_busy(True)
        self.status_var.set("Restoring environment ...")
        backup_path = picker.selected

        def run():
            self._safe_append(f"Restoring env from {os.path.basename(backup_path)} ...", "info")
            ok, msg = RestoreManager.restore_environment(
                laragon, backup_path,
                on_output=lambda m: self._safe_append(m))
            if ok:
                self._safe_append(f"OK: {msg}", "success")
                self.root.after(0, lambda: self.status_var.set("Env restored"))
            else:
                self._safe_append(f"FAIL: {msg}", "error")
                self.root.after(0, lambda: self.status_var.set("Env restore failed"))
            self.root.after(0, lambda: self._set_busy(False))

        threading.Thread(target=run, daemon=True).start()


# ====================================
# MAIN
# ====================================

def main():
    setup_shared_logging("web_devbackup")
    root = tk.Tk()
    apply_category_icon(root)
    ui = DevBackupUI(root)

    # Pre-select the site row matching a project folder passed by the launcher.
    # The user can still click a different row to switch sites.
    if len(sys.argv) > 1 and sys.argv[1]:
        site_name = os.path.basename(os.path.normpath(sys.argv[1]))
        for item in ui.tree.get_children():
            if ui.tree.item(item, "values")[0] == site_name:
                ui.tree.selection_set(item)
                ui.tree.focus(item)
                ui.tree.see(item)
                break

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
