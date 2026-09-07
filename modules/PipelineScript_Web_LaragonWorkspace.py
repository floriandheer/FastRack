#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PipelineScript_Web_LaragonWorkspace.py
Description: Manage Laragon project junctions and workspace settings
"""

import os
import sys
import json
import shutil
import subprocess
import threading
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
from shared_window_icon import apply_category_icon
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from shared_logging import get_logger, setup_logging as setup_shared_logging
from rak_settings import get_rak_settings, join_native_path

logger = get_logger("laragon_workspace")


# ====================================
# CONFIGURATION
# ====================================

PERSONAL_SITES = ["floriandheer", "hyphen-v", "alles3d"]


class LaragonConfig:
    """Configuration manager for Laragon workspace junctions."""

    def __init__(self):
        app_data = Path.home() / "AppData" / "Local" / "PipelineManager"
        app_data.mkdir(parents=True, exist_ok=True)
        self.config_file = app_data / "laragon_config.json"
        self.config = self._load_config()

    def _default_config(self) -> Dict:
        return {
            "laragon_www_path": r"C:\laragon\www",
            "projects": {}
        }

    def _load_config(self) -> Dict:
        default = self._default_config()
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r', encoding='utf-8') as f:
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
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def save(self):
        self._save()

    def get_www_path(self) -> str:
        return self.config.get("laragon_www_path", r"C:\laragon\www")

    def set_www_path(self, path: str):
        self.config["laragon_www_path"] = path
        self._save()

    def get_projects(self) -> Dict:
        return self.config.get("projects", {})

    def set_project(self, name: str, target: str, category: str):
        if "projects" not in self.config:
            self.config["projects"] = {}
        self.config["projects"][name] = {
            "target": target,
            "category": category
        }
        self._save()

    def remove_project(self, name: str):
        self.config.get("projects", {}).pop(name, None)
        self._save()


# ====================================
# JUNCTION MANAGER
# ====================================

class JunctionManager:
    """Core logic for Windows junction operations."""

    @staticmethod
    def is_junction(path: str) -> bool:
        """Check if a path is a junction point."""
        p = Path(path)
        try:
            return p.is_junction()
        except (OSError, AttributeError):
            # Fallback for older Python
            try:
                return p.is_symlink()
            except OSError:
                return False

    @staticmethod
    def get_junction_target(path: str) -> Optional[str]:
        """Read where a junction points to."""
        try:
            target = os.readlink(path)
            # Strip Windows extended-length path prefix
            if target.startswith("\\\\?\\"):
                target = target[4:]
            return target
        except (OSError, ValueError):
            return None

    @staticmethod
    def scan_www(www_path: str) -> List[Dict]:
        """List all folders in www, classifying each as junction or regular folder."""
        results = []
        if not os.path.isdir(www_path):
            return results

        for entry in sorted(os.listdir(www_path)):
            full_path = os.path.join(www_path, entry)
            if not os.path.isdir(full_path):
                continue

            info = {"name": entry, "path": full_path}
            if JunctionManager.is_junction(full_path):
                info["status"] = "junction"
                info["target"] = JunctionManager.get_junction_target(full_path) or "unknown"
            else:
                info["status"] = "folder"
                info["target"] = ""

            results.append(info)
        return results

    @staticmethod
    def compute_target_path(project_name: str, category: str) -> str:
        """Compute the target path on the work drive for a project."""
        work = get_rak_settings().get_work_drive()
        if category == "personal":
            return join_native_path(work, "Web", "_Personal", project_name, "02_Development")
        else:
            return join_native_path(work, "Web", project_name, "02_Development")

    @staticmethod
    def create_junction(project_name: str, www_path: str, target_path: str,
                        on_output=None) -> Tuple[bool, str]:
        """
        Move project contents from www to target, then create junction.

        Returns (success, message).
        """
        source = os.path.join(www_path, project_name)
        if not os.path.isdir(source):
            return False, f"Source folder not found: {source}"

        if JunctionManager.is_junction(source):
            return False, f"Already a junction: {source}"

        def log(msg):
            if on_output:
                on_output(msg)
            logger.info(msg)

        # Ensure target parent exists
        target_parent = os.path.dirname(target_path)
        os.makedirs(target_parent, exist_ok=True)

        if os.path.exists(target_path):
            return False, f"Target already exists: {target_path}"

        # Move contents: copy then remove original
        log(f"Copying {source} -> {target_path} ...")
        try:
            shutil.copytree(source, target_path)
        except Exception as e:
            return False, f"Failed to copy: {e}"

        # Verify copy
        if not os.path.isdir(target_path):
            return False, "Copy verification failed: target directory missing"

        log("Copy complete. Removing original folder ...")
        try:
            shutil.rmtree(source)
        except Exception as e:
            return False, f"Failed to remove original (files already copied to {target_path}): {e}"

        # Create junction
        log(f"Creating junction: {source} -> {target_path}")
        ok, msg = JunctionManager._mklink_junction(source, target_path)
        if not ok:
            return False, msg

        # Verify
        if not JunctionManager.is_junction(source):
            return False, "Junction creation verification failed"

        actual_target = JunctionManager.get_junction_target(source)
        log(f"Junction verified: {source} -> {actual_target}")
        return True, "Junction created successfully"

    @staticmethod
    def setup_on_new_pc(project_name: str, www_path: str, target_path: str,
                        on_output=None) -> Tuple[bool, str]:
        """For fresh PC: create junction to existing content on work drive."""
        source = os.path.join(www_path, project_name)

        def log(msg):
            if on_output:
                on_output(msg)
            logger.info(msg)

        if not os.path.isdir(target_path):
            return False, f"Target not found on work drive: {target_path}"

        if os.path.exists(source):
            if JunctionManager.is_junction(source):
                actual = JunctionManager.get_junction_target(source)
                if actual and os.path.normcase(os.path.normpath(actual)) == os.path.normcase(os.path.normpath(target_path)):
                    return True, "Junction already exists and points to correct target"
                else:
                    return False, f"Junction exists but points to: {actual}"
            else:
                return False, f"Regular folder exists at {source}. Remove it first or use Link Project."

        log(f"Creating junction: {source} -> {target_path}")
        ok, msg = JunctionManager._mklink_junction(source, target_path)
        if not ok:
            return False, msg

        log("Junction created successfully")
        return True, "Junction created for new PC"

    @staticmethod
    def verify_junction(junction_path: str, expected_target: str) -> Tuple[bool, str]:
        """Check junction exists and points to correct target."""
        if not os.path.exists(junction_path):
            return False, "Path does not exist"

        if not JunctionManager.is_junction(junction_path):
            return False, "Not a junction"

        actual = JunctionManager.get_junction_target(junction_path)
        if actual is None:
            return False, "Cannot read junction target"

        if os.path.normcase(os.path.normpath(actual)) != os.path.normcase(os.path.normpath(expected_target)):
            return False, f"Points to {actual} (expected {expected_target})"

        if not os.path.isdir(junction_path):
            return False, "Junction target is not accessible"

        return True, "Healthy"

    @staticmethod
    def _mklink_junction(link_path: str, target_path: str) -> Tuple[bool, str]:
        """Create a Windows junction using mklink /J."""
        try:
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", link_path, target_path],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode != 0:
                stderr = result.stderr.strip() or result.stdout.strip()
                return False, f"mklink failed: {stderr}"
            return True, "OK"
        except FileNotFoundError:
            return False, "cmd.exe not found (not running on Windows?)"
        except subprocess.TimeoutExpired:
            return False, "mklink timed out"
        except Exception as e:
            return False, f"mklink error: {e}"


# ====================================
# UI COLORS (matching PublishStatic)
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


# ====================================
# SETTINGS DIALOG
# ====================================

class LaragonSettingsDialog:
    """Modal dialog for configuring Laragon workspace settings."""

    def __init__(self, parent: tk.Tk, config: LaragonConfig):
        self.parent = parent
        self.config = config
        self.changed = False

        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Laragon Settings")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.dialog.configure(bg=FORM_COLORS["bg"])

        self._build_ui()

        # Center on parent
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        colors = FORM_COLORS
        pad = {"padx": 15, "pady": 6}

        # --- Laragon www path ---
        www_frame = tk.LabelFrame(self.dialog, text="Laragon www Path", bg=colors["bg"],
                                  fg=colors["fg_dim"], font=("Arial", 9))
        www_frame.pack(fill=tk.X, **pad)

        inner = tk.Frame(www_frame, bg=colors["bg"])
        inner.pack(fill=tk.X, padx=10, pady=8)

        self.www_var = tk.StringVar(value=self.config.get_www_path())
        www_entry = tk.Entry(inner, textvariable=self.www_var,
                             bg=colors["bg_card"], fg=colors["fg"],
                             insertbackground=colors["fg"],
                             font=("Consolas", 9), relief=tk.FLAT)
        www_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        tk.Button(inner, text="Browse", command=self._browse_www,
                  bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT).pack(side=tk.LEFT)

        # --- Buttons ---
        btn_frame = tk.Frame(self.dialog, bg=colors["bg"])
        btn_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy,
                  bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT,
                  padx=15).pack(side=tk.RIGHT, padx=5)

        tk.Button(btn_frame, text="Save", command=self._on_save,
                  bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                  relief=tk.FLAT, padx=15).pack(side=tk.RIGHT)

    def _browse_www(self):
        from tkinter import filedialog
        directory = filedialog.askdirectory(title="Select Laragon www Directory")
        if directory:
            self.www_var.set(directory.replace("/", "\\"))

    def _on_save(self):
        www = self.www_var.get().strip()
        if not www:
            messagebox.showerror("Error", "Laragon www path cannot be empty.",
                                 parent=self.dialog)
            return

        self.config.set_www_path(www)
        self.changed = True
        self.dialog.destroy()


# ====================================
# LINK PROJECT DIALOG
# ====================================

class LinkProjectDialog:
    """Modal dialog for linking a project to the work drive."""

    def __init__(self, parent: tk.Tk, project_name: str, config: LaragonConfig):
        self.parent = parent
        self.project_name = project_name
        self.config = config
        self.result = None  # (target_path, category) or None if cancelled

        self.dialog = tk.Toplevel(parent)
        self.dialog.title(f"Link Project: {project_name}")
        self.dialog.transient(parent)
        self.dialog.grab_set()
        self.dialog.resizable(False, False)
        self.dialog.configure(bg=FORM_COLORS["bg"])

        self._build_ui()

        # Center on parent
        self.dialog.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
        y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
        self.dialog.geometry(f"+{x}+{y}")

    def _build_ui(self):
        colors = FORM_COLORS
        pad = {"padx": 15, "pady": 6}

        # Project name
        name_frame = tk.Frame(self.dialog, bg=colors["bg"])
        name_frame.pack(fill=tk.X, **pad)
        tk.Label(name_frame, text="Project:", bg=colors["bg"], fg=colors["fg_dim"],
                 font=("Arial", 10)).pack(side=tk.LEFT)
        tk.Label(name_frame, text=self.project_name, bg=colors["bg"], fg=colors["fg"],
                 font=("Arial", 10, "bold")).pack(side=tk.LEFT, padx=8)

        # Category selection
        cat_frame = tk.LabelFrame(self.dialog, text="Category", bg=colors["bg"],
                                  fg=colors["fg_dim"], font=("Arial", 9))
        cat_frame.pack(fill=tk.X, **pad)

        self.category_var = tk.StringVar(value="personal" if self.project_name in PERSONAL_SITES else "business")
        for value, label in [("personal", "Personal"), ("business", "Business")]:
            tk.Radiobutton(
                cat_frame, text=label, variable=self.category_var, value=value,
                bg=colors["bg"], fg=colors["fg"], selectcolor=colors["accent"],
                activebackground=colors["bg"], activeforeground=colors["fg"],
                font=("Arial", 10), command=self._update_preview
            ).pack(side=tk.LEFT, padx=15, pady=8)

        # Target path preview
        target_frame = tk.LabelFrame(self.dialog, text="Target Path", bg=colors["bg"],
                                     fg=colors["fg_dim"], font=("Arial", 9))
        target_frame.pack(fill=tk.X, **pad)

        self.target_var = tk.StringVar()
        self.target_entry = tk.Entry(target_frame, textvariable=self.target_var,
                                     bg=colors["bg_card"], fg=colors["fg"],
                                     insertbackground=colors["fg"],
                                     font=("Consolas", 9), relief=tk.FLAT)
        self.target_entry.pack(fill=tk.X, padx=10, pady=8)

        self._update_preview()

        # Buttons
        btn_frame = tk.Frame(self.dialog, bg=colors["bg"])
        btn_frame.pack(fill=tk.X, padx=15, pady=(5, 15))

        tk.Button(btn_frame, text="Cancel", command=self.dialog.destroy,
                  bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT,
                  padx=15).pack(side=tk.RIGHT, padx=5)

        tk.Button(btn_frame, text="Link", command=self._on_link,
                  bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                  relief=tk.FLAT, padx=15).pack(side=tk.RIGHT)

    def _update_preview(self):
        category = self.category_var.get()
        target = JunctionManager.compute_target_path(self.project_name, category)
        self.target_var.set(target)

    def _on_link(self):
        target = self.target_var.get().strip()
        category = self.category_var.get()
        if not target:
            messagebox.showerror("Error", "Target path cannot be empty", parent=self.dialog)
            return
        self.result = (target, category)
        self.dialog.destroy()


# ====================================
# MAIN UI
# ====================================

class LaragonManagerUI:
    """Tkinter GUI for the Laragon Workspace Manager."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Laragon Workspace Manager")
        self.root.geometry("800x650")
        self.root.minsize(700, 550)
        self.root.configure(bg=FORM_COLORS["bg"])

        self.config = LaragonConfig()
        self.operation_running = False

        self._build_ui()
        self._refresh_project_list()

    def _build_ui(self):
        colors = FORM_COLORS

        # Header
        header = tk.Frame(self.root, bg="#2c3e50", height=55)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="Laragon Workspace Manager", font=("Arial", 15, "bold"),
                 fg="white", bg="#2c3e50").place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        # Path + controls row
        path_frame = tk.Frame(self.root, bg=colors["bg"])
        path_frame.pack(fill=tk.X, padx=12, pady=(10, 5))

        tk.Label(path_frame, text="Laragon www:", bg=colors["bg"], fg=colors["fg"],
                 font=("Arial", 10)).pack(side=tk.LEFT)

        self.www_label = tk.Label(path_frame, text=self.config.get_www_path(),
                                  bg=colors["bg_card"], fg=colors["fg"],
                                  font=("Consolas", 9), relief=tk.FLAT, anchor=tk.W,
                                  padx=6, pady=2)
        self.www_label.pack(side=tk.LEFT, padx=8, fill=tk.X, expand=True)

        tk.Button(path_frame, text="Settings", command=self._open_settings,
                  bg=colors["highlight"], fg=colors["fg"], relief=tk.FLAT,
                  padx=10).pack(side=tk.LEFT)

        tk.Button(path_frame, text="Refresh", command=self._refresh_project_list,
                  bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT).pack(side=tk.LEFT, padx=(5, 0))

        # Project list
        list_frame = tk.LabelFrame(self.root, text="Projects in Laragon www", bg=colors["bg"],
                                   fg=colors["fg_dim"], font=("Arial", 9))
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(5, 5))

        # Treeview with columns
        tree_frame = tk.Frame(list_frame, bg=colors["bg"])
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ("status", "target")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8)
        self.tree.heading("status", text="Status")
        self.tree.heading("target", text="Target Path")

        # Insert a name column
        self.tree["columns"] = ("name", "status", "target")
        self.tree.heading("name", text="Project")
        self.tree.heading("status", text="Status")
        self.tree.heading("target", text="Target Path")
        self.tree.column("name", width=150, minwidth=100)
        self.tree.column("status", width=100, minwidth=80)
        self.tree.column("target", width=400, minwidth=200)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Style the treeview
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                        background=colors["bg_card"],
                        foreground=colors["fg"],
                        fieldbackground=colors["bg_card"],
                        font=("Consolas", 9))
        style.configure("Treeview.Heading",
                        background=colors["accent"],
                        foreground=colors["fg"],
                        font=("Arial", 9, "bold"))
        style.map("Treeview",
                  background=[("selected", colors["highlight"])],
                  foreground=[("selected", "white")])

        # Tags for color-coding
        self.tree.tag_configure("linked", foreground=colors["success"])
        self.tree.tag_configure("unlinked", foreground=colors["warning"])

        # Action buttons
        btn_frame = tk.Frame(self.root, bg=colors["bg"])
        btn_frame.pack(fill=tk.X, padx=12, pady=(0, 5))

        self.link_btn = tk.Button(btn_frame, text="Link Project", command=self._link_project,
                                  bg="#27ae60", fg="white", font=("Arial", 10, "bold"),
                                  relief=tk.FLAT, padx=15)
        self.link_btn.pack(side=tk.LEFT)

        self.verify_btn = tk.Button(btn_frame, text="Verify All", command=self._verify_all,
                                    bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT,
                                    padx=15)
        self.verify_btn.pack(side=tk.LEFT, padx=8)

        self.newpc_btn = tk.Button(btn_frame, text="Setup New PC", command=self._setup_new_pc,
                                   bg=colors["accent"], fg=colors["fg"], relief=tk.FLAT,
                                   padx=15)
        self.newpc_btn.pack(side=tk.LEFT)

        # Output log
        output_frame = tk.Frame(self.root, bg=colors["bg"])
        output_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 5))

        self.output_text = scrolledtext.ScrolledText(
            output_frame, wrap=tk.WORD, bg="#0d1117", fg=colors["fg"],
            font=("Consolas", 9), insertbackground=colors["fg"],
            relief=tk.FLAT, state=tk.DISABLED, height=8
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

    def _append_output(self, msg: str, tag: str = None):
        self.output_text.config(state=tk.NORMAL)
        if tag:
            self.output_text.insert(tk.END, msg + "\n", tag)
        else:
            self.output_text.insert(tk.END, msg + "\n")
        self.output_text.see(tk.END)
        self.output_text.config(state=tk.DISABLED)

    def _clear_output(self):
        self.output_text.config(state=tk.NORMAL)
        self.output_text.delete(1.0, tk.END)
        self.output_text.config(state=tk.DISABLED)

    def _open_settings(self):
        dialog = LaragonSettingsDialog(self.root, self.config)
        self.root.wait_window(dialog.dialog)
        if dialog.changed:
            self.www_label.config(text=self.config.get_www_path())
            self._refresh_project_list()

    def _refresh_project_list(self):
        """Scan www directory and update the treeview."""
        www_path = self.config.get_www_path()

        # Clear tree
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not os.path.isdir(www_path):
            self.status_var.set(f"Directory not found: {www_path}")
            return

        projects = JunctionManager.scan_www(www_path)
        config_projects = self.config.get_projects()

        linked_count = 0
        for proj in projects:
            name = proj["name"]
            if proj["status"] == "junction":
                status = "Linked"
                target = proj["target"]
                tag = "linked"
                linked_count += 1
            else:
                status = "Not linked"
                target = ""
                tag = "unlinked"

            self.tree.insert("", tk.END, values=(name, status, target), tags=(tag,))

        total = len(projects)
        self.status_var.set(f"{total} projects found, {linked_count} linked")

    def _get_selected_project(self) -> Optional[str]:
        """Get the name of the selected project in the treeview."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showinfo("Info", "Select a project from the list first.")
            return None
        values = self.tree.item(selection[0], "values")
        return values[0] if values else None

    def _set_buttons_state(self, state: str):
        self.link_btn.config(state=state)
        self.verify_btn.config(state=state)
        self.newpc_btn.config(state=state)

    def _link_project(self):
        """Open link dialog for the selected project."""
        project_name = self._get_selected_project()
        if not project_name:
            return

        www_path = self.config.get_www_path()
        project_path = os.path.join(www_path, project_name)

        if JunctionManager.is_junction(project_path):
            messagebox.showinfo("Already Linked",
                                f"'{project_name}' is already a junction.",
                                parent=self.root)
            return

        dialog = LinkProjectDialog(self.root, project_name, self.config)
        self.root.wait_window(dialog.dialog)

        if dialog.result is None:
            return

        target_path, category = dialog.result

        self._clear_output()
        self._set_buttons_state(tk.DISABLED)
        self.operation_running = True
        self.status_var.set(f"Linking {project_name} ...")

        def run():
            try:
                success, msg = JunctionManager.create_junction(
                    project_name, www_path, target_path,
                    on_output=lambda m: self.root.after(0, self._append_output, m)
                )

                if success:
                    self.config.set_project(project_name, target_path, category)
                    self.root.after(0, self._append_output, f"Done: {msg}", "success")
                    self.root.after(0, lambda: self.status_var.set(f"Linked: {project_name}"))
                else:
                    self.root.after(0, self._append_output, f"Failed: {msg}", "error")
                    self.root.after(0, lambda: self.status_var.set(f"Failed to link {project_name}"))
            except Exception as e:
                logger.exception("Link project failed")
                self.root.after(0, self._append_output, f"Error: {e}", "error")
            finally:
                self.operation_running = False
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))
                self.root.after(0, self._refresh_project_list)

        threading.Thread(target=run, daemon=True).start()

    def _verify_all(self):
        """Check all junctions are healthy."""
        self._clear_output()
        www_path = self.config.get_www_path()
        projects = JunctionManager.scan_www(www_path)
        config_projects = self.config.get_projects()

        all_ok = True
        checked = 0

        for proj in projects:
            if proj["status"] != "junction":
                continue

            name = proj["name"]
            checked += 1

            # Get expected target from config, or use actual target
            expected = config_projects.get(name, {}).get("target", proj["target"])
            ok, msg = JunctionManager.verify_junction(proj["path"], expected)

            if ok:
                self._append_output(f"  {name}: {msg}", "success")
            else:
                self._append_output(f"  {name}: {msg}", "error")
                all_ok = False

        if checked == 0:
            self._append_output("No junctions found to verify.", "info")
        elif all_ok:
            self._append_output(f"\nAll {checked} junctions healthy.", "success")
        else:
            self._append_output(f"\nSome junctions have issues. Check above.", "error")

        self.status_var.set(f"Verified {checked} junctions")

    def _setup_new_pc(self):
        """Recreate junctions from config for a fresh PC setup."""
        config_projects = self.config.get_projects()
        if not config_projects:
            messagebox.showinfo("No Projects",
                                "No projects in configuration. Link projects first on your original PC.",
                                parent=self.root)
            return

        confirm = messagebox.askyesno(
            "Setup New PC",
            f"This will create junctions for {len(config_projects)} configured projects.\n\n"
            "Use this on a fresh Laragon install where the work drive (I:) already has the project files.\n\n"
            "Continue?",
            parent=self.root
        )
        if not confirm:
            return

        self._clear_output()
        self._set_buttons_state(tk.DISABLED)
        self.operation_running = True
        www_path = self.config.get_www_path()

        def run():
            try:
                success_count = 0
                fail_count = 0

                for name, proj_cfg in config_projects.items():
                    target = proj_cfg.get("target", "")
                    if not target:
                        self.root.after(0, self._append_output,
                                        f"  {name}: No target path configured", "error")
                        fail_count += 1
                        continue

                    ok, msg = JunctionManager.setup_on_new_pc(
                        name, www_path, target,
                        on_output=lambda m: self.root.after(0, self._append_output, m)
                    )

                    if ok:
                        self.root.after(0, self._append_output, f"  {name}: {msg}", "success")
                        success_count += 1
                    else:
                        self.root.after(0, self._append_output, f"  {name}: {msg}", "error")
                        fail_count += 1

                summary = f"Setup complete: {success_count} created, {fail_count} failed"
                tag = "success" if fail_count == 0 else "error"
                self.root.after(0, self._append_output, f"\n{summary}", tag)
                self.root.after(0, lambda: self.status_var.set(summary))

            except Exception as e:
                logger.exception("New PC setup failed")
                self.root.after(0, self._append_output, f"Error: {e}", "error")
            finally:
                self.operation_running = False
                self.root.after(0, lambda: self._set_buttons_state(tk.NORMAL))
                self.root.after(0, self._refresh_project_list)

        threading.Thread(target=run, daemon=True).start()

# ====================================
# MAIN
# ====================================

def main():
    setup_shared_logging("laragon_workspace")

    root = tk.Tk()
    apply_category_icon(root)
    LaragonManagerUI(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
