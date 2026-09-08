"""
UI Settings Dialog - Multi-tab settings window for the Pipeline Manager.
"""

import json
import os
import shutil
import subprocess
import sys
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, font

from shared_logging import get_logger
from rak_settings import RakSettings
from ui_theme import COLORS, CATEGORY_COLORS

logger = get_logger("pipeline")


class SettingsDialog:
    """Settings dialog for configuring pipeline paths and preferences."""

    def __init__(self, parent, settings: RakSettings):
        """
        Initialize the settings dialog.

        Args:
            parent: Parent window
            settings: RakSettings instance
        """
        self.parent = parent
        self.settings = settings
        self.result = False  # True if saved

        # Create dialog window
        self.dialog = tk.Toplevel(parent)
        self.dialog.title("Rak Settings")
        # Default sized for the Startup Apps tab (deps panel + monitors
        # strip + apps list + actions row + add footer stack tall).
        self.dialog.geometry("800x900")
        self.dialog.minsize(700, 600)
        self.dialog.configure(bg=COLORS["bg_primary"])

        # Non-modal: Save keeps this window open (see _save()), and the
        # caller's Settings button/shortcut toggles it closed on a second
        # press — both need the main window to stay clickable, so no
        # grab_set() here.
        self.dialog.transient(parent)
        # X-button on title bar = treat as Cancel (also persists geometry).
        self.dialog.protocol("WM_DELETE_WINDOW", self._cancel)

        # Restore the user's last size/position if they previously
        # resized; otherwise center the default size on the parent.
        saved_geo = settings.get_settings_dialog_geometry()
        self.dialog.update_idletasks()
        if saved_geo:
            self.dialog.geometry(saved_geo)
        else:
            x = parent.winfo_x() + (parent.winfo_width() - self.dialog.winfo_width()) // 2
            y = parent.winfo_y() + (parent.winfo_height() - self.dialog.winfo_height()) // 2
            self.dialog.geometry(f"+{x}+{y}")

        # Store original values for cancel
        self._original_work_drive = settings.get_work_drive()
        self._original_archive_base = settings.get_archive_base()

        # Build UI
        self._build_ui()

        # Validate on open
        self._validate_paths()

    def _build_ui(self):
        """Build the settings dialog UI."""
        # Header
        header_frame = tk.Frame(self.dialog, bg=COLORS["bg_secondary"], height=60)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        header_label = tk.Label(
            header_frame,
            text="Rak Settings",
            font=font.Font(family="Segoe UI", size=16, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_secondary"]
        )
        header_label.pack(side=tk.LEFT, padx=20, pady=15)

        # Notebook for tabs
        style = ttk.Style()
        style.configure("Settings.TNotebook", background=COLORS["bg_primary"])
        style.configure("Settings.TNotebook.Tab",
                       background=COLORS["bg_secondary"],
                       foreground=COLORS["text_primary"],
                       padding=[15, 8],
                       width=20)
        style.map("Settings.TNotebook.Tab",
                 background=[("selected", COLORS["accent_dark"])],
                 foreground=[("selected", "#ffffff")],
                 padding=[("selected", [15, 8])])

        # === BUTTON ROW (pack first so it always stays visible at bottom) ===
        self._build_button_row()

        self.notebook = ttk.Notebook(self.dialog, style="Settings.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=20, pady=10)

        # === GENERAL TAB ===
        general_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(general_tab, text="General")
        self._build_general_tab(general_tab)

        # === PATHS TAB ===
        paths_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(paths_tab, text="Paths")
        self._build_paths_tab(paths_tab)

        # === DRIVES TAB ===
        drives_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(drives_tab, text="Drives")
        self._build_drives_tab(drives_tab)

        # === SOFTWARE TAB ===
        software_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(software_tab, text="Software Defaults")
        self._build_software_tab(software_tab)

        # === STARTUP APPS TAB ===
        startup_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(startup_tab, text="Startup Apps")
        self._build_startup_apps_tab(startup_tab)

        # === WORKSTATION APPS TAB ===
        apps_tab = tk.Frame(self.notebook, bg=COLORS["bg_primary"])
        self.notebook.add(apps_tab, text="Workstation Apps")
        self._build_workstation_apps_tab(apps_tab)

    def _build_general_tab(self, parent):
        """Build the General settings tab."""
        content_frame = tk.Frame(parent, bg=COLORS["bg_primary"])
        content_frame.pack(fill=tk.BOTH, expand=True, pady=10)

        section = tk.LabelFrame(
            content_frame,
            text=" Window ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        section.pack(fill=tk.X, padx=20, pady=(0, 15))

        self.start_fullscreen_var = tk.BooleanVar(
            value=self.settings.get_start_fullscreen()
        )
        cb = tk.Checkbutton(
            section,
            text="Start in fullscreen (borderless)",
            variable=self.start_fullscreen_var,
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            selectcolor=COLORS["bg_secondary"],
            activebackground=COLORS["bg_card"],
            activeforeground=COLORS["text_primary"]
        )
        cb.pack(anchor="w")

        self.always_on_bottom_var = tk.BooleanVar(
            value=self.settings.get_always_on_bottom()
        )
        tk.Checkbutton(
            section,
            text="Keep window behind all others (stick to background)",
            variable=self.always_on_bottom_var,
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            selectcolor=COLORS["bg_secondary"],
            activebackground=COLORS["bg_card"],
            activeforeground=COLORS["text_primary"]
        ).pack(anchor="w")

        # Setup & Maintenance section
        setup_section = tk.LabelFrame(
            content_frame,
            text=" Setup & Maintenance ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        setup_section.pack(fill=tk.X, padx=20, pady=(0, 15))

        setup_row = tk.Frame(setup_section, bg=COLORS["bg_card"])
        setup_row.pack(fill=tk.X, pady=5)

        tk.Button(
            setup_row,
            text="Install Dependencies",
            command=self._install_dependencies,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=6
        ).pack(side=tk.LEFT)

        tk.Button(
            setup_row,
            text="Create Shortcut",
            command=self._create_shortcut,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=6
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Environment Setup: one button per step in setup_environment.py.
        # "Run All" preserves the original behaviour; the per-step buttons
        # launch the script with --step <name> so you can re-test or
        # re-apply a single piece (e.g. just remap drives) on a PC that
        # is already partially set up.
        tk.Label(
            setup_section,
            text="Environment Setup:",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
        ).pack(anchor="w", pady=(10, 2))

        env_row = tk.Frame(setup_section, bg=COLORS["bg_card"])
        env_row.pack(fill=tk.X, pady=2)

        # (button_label, step_name) — step matches setup_environment.STEPS,
        # plus "all" which the script also accepts.
        env_steps = (
            ("Run All",  "all"),
            ("Folders",  "folders"),
            ("Drives",   "drives"),
            ("Synology", "synology"),
            ("Config",   "config"),
            ("Startup",  "startup"),
        )
        for label, step in env_steps:
            tk.Button(
                env_row,
                text=label,
                command=lambda s=step: self._run_environment_setup(s),
                bg=COLORS["bg_secondary"],
                fg=COLORS["text_primary"],
                font=font.Font(family="Segoe UI", size=10),
                relief=tk.FLAT,
                cursor="hand2",
                padx=12,
                pady=6,
            ).pack(side=tk.LEFT, padx=(0, 6))

        tk.Label(
            setup_section,
            text=(
                "Install Dependencies: runs install_dependencies.py via pip in a new console.\n"
                "Create Shortcut: regenerates Fastrak.lnk (Windows) or Fastrak.app (macOS).\n"
                "Environment Setup steps (all run in a new console):\n"
                "  Run All  - everything below in one pass\n"
                "  Folders  - create Active/Archive/category dirs from setup_config.json\n"
                "  Drives   - subst mappings + HKCU autorun + Explorer drive labels (Windows only)\n"
                "  Synology - check Drive Client install + sync folder status (Windows only)\n"
                "  Config   - generate/update rak_config.json from setup_config.json\n"
                "  Startup  - deploy startup-apps launcher + scheduled task (Windows only)"
            ),
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            justify="left"
        ).pack(anchor="w", pady=(6, 0))

    def _project_root(self):
        """Return the directory containing fastrak_hub.py."""
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _console_python(self):
        """Return python.exe (not pythonw.exe) so the spawned console gets working stdio."""
        exe = sys.executable
        base = os.path.basename(exe).lower()
        if base.startswith("pythonw"):
            candidate = os.path.join(os.path.dirname(exe), "python.exe")
            if os.path.isfile(candidate):
                return candidate
        return exe

    def _ensure_setup_config(self, project_root):
        """Make sure setup_config.json exists; offer to copy from .example if not."""
        config_path = os.path.join(project_root, "setup_config.json")
        if os.path.isfile(config_path):
            return True

        example_path = os.path.join(project_root, "setup_config.json.example")
        if not os.path.isfile(example_path):
            messagebox.showerror(
                "Config Missing",
                "setup_config.json not found, and no setup_config.json.example "
                "exists to copy from.",
                parent=self.dialog,
            )
            return False

        if not messagebox.askyesno(
            "Create setup_config.json?",
            "setup_config.json doesn't exist yet.\n\n"
            "Copy setup_config.json.example to setup_config.json now? "
            "You can edit it afterwards to match this PC.",
            parent=self.dialog,
        ):
            return False

        try:
            shutil.copy(example_path, config_path)
            logger.info("Copied setup_config.json.example -> setup_config.json")
            return True
        except OSError as e:
            messagebox.showerror(
                "Copy Failed",
                f"Could not create setup_config.json:\n{e}",
                parent=self.dialog,
            )
            return False

    # Per-step blurbs for the confirmation dialog. Keys match
    # setup_environment.STEPS plus "all".
    _ENV_STEP_BLURBS = {
        "all":      "create folders, configure drive mappings, check Synology "
                    "Drive status, generate the pipeline config, and deploy the "
                    "startup-apps launcher",
        "folders":  "create the Active/Archive/category folder structure defined "
                    "in setup_config.json",
        "drives":   "set up subst drive mappings, persist them via HKCU autorun, "
                    "and write Explorer drive labels",
        "synology": "check Synology Drive Client installation and report sync "
                    "folder status (no changes are made automatically)",
        "config":   "generate or update rak_config.json from setup_config.json's "
                    "pipeline_config section",
        "startup":  "deploy the startup-apps PowerShell launcher and register the "
                    "logon scheduled task",
    }

    def _run_environment_setup(self, step="all"):
        """Launch setup_environment.py in a new console window, optionally
        scoped to a single step. ``step`` is one of "all" or any value in
        setup_environment.STEPS."""
        project_root = self._project_root()
        script_path = os.path.join(project_root, "setup_environment.py")

        if not os.path.isfile(script_path):
            messagebox.showerror(
                "Setup Script Not Found",
                f"Could not locate:\n{script_path}",
                parent=self.dialog,
            )
            return

        if not self._ensure_setup_config(project_root):
            return

        blurb = self._ENV_STEP_BLURBS.get(step, self._ENV_STEP_BLURBS["all"])
        title = "Run Environment Setup" if step == "all" \
            else f"Run Environment Setup — {step}"
        if not messagebox.askyesno(
            title,
            f"This will {blurb} on this PC.\n\n"
            "The script runs interactively in a new console window — you can answer "
            "prompts there. Continue?",
            parent=self.dialog,
        ):
            return

        exe = self._console_python()
        extra_args = ["--step", step] if step != "all" else []
        try:
            if sys.platform == "win32":
                # Wrap with `cmd /k` so the console stays open after the script
                # exits, even if it fails fast (otherwise the window closes
                # before you can read it).
                subprocess.Popen(
                    ["cmd", "/k", exe, script_path] + extra_args,
                    cwd=project_root,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                subprocess.Popen([exe, script_path] + extra_args, cwd=project_root)
            logger.info("Launched setup_environment.py (step=%s) in new console", step)
        except Exception as e:
            logger.exception("Failed to launch setup_environment.py")
            messagebox.showerror(
                "Launch Failed",
                f"Could not start setup script:\n{e}",
                parent=self.dialog,
            )

    def _install_dependencies(self):
        """Launch install_dependencies.py in a new console window."""
        project_root = self._project_root()
        script_path = os.path.join(project_root, "install_dependencies.py")

        if not os.path.isfile(script_path):
            messagebox.showerror(
                "Installer Not Found",
                f"Could not locate:\n{script_path}",
                parent=self.dialog,
            )
            return

        if not messagebox.askyesno(
            "Install Dependencies",
            "This will install the Python packages required by the pipeline "
            "(Pillow, pdfplumber, invoice2data, etc.) via pip.\n\n"
            "The installer runs interactively in a new console window — you can "
            "answer prompts there. Continue?",
            parent=self.dialog,
        ):
            return

        exe = self._console_python()
        try:
            if sys.platform == "win32":
                # `cmd /k` keeps the console open so install output stays readable.
                subprocess.Popen(
                    ["cmd", "/k", exe, script_path],
                    cwd=project_root,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            else:
                subprocess.Popen([exe, script_path], cwd=project_root)
            logger.info("Launched install_dependencies.py in new console")
        except Exception as e:
            logger.exception("Failed to launch install_dependencies.py")
            messagebox.showerror(
                "Launch Failed",
                f"Could not start dependency installer:\n{e}",
                parent=self.dialog,
            )

    def _create_shortcut(self):
        """Run make_shortcut.py to (re)generate the desktop launcher
        (Fastrak.lnk on Windows, Fastrak.app on macOS)."""
        if sys.platform not in ("win32", "darwin"):
            messagebox.showwarning(
                "Not Supported",
                "No desktop-shortcut generator for this platform yet.",
                parent=self.dialog,
            )
            return

        project_root = self._project_root()
        script_path = os.path.join(project_root, "make_shortcut.py")
        shortcut_name = "Fastrak.lnk" if sys.platform == "win32" else "Fastrak.app"

        if not os.path.isfile(script_path):
            messagebox.showerror(
                "make_shortcut.py Not Found",
                f"Could not locate:\n{script_path}",
                parent=self.dialog,
            )
            return

        shortcut_path = os.path.join(project_root, shortcut_name)
        if os.path.exists(shortcut_path):
            if not messagebox.askyesno(
                "Regenerate Shortcut?",
                f"{shortcut_name} already exists in:\n{project_root}\n\nReplace it?",
                parent=self.dialog,
            ):
                return

        exe = self._console_python()
        try:
            result = subprocess.run(
                [exe, script_path],
                cwd=project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            messagebox.showerror(
                "Shortcut Creation Timed Out",
                "make_shortcut.py did not finish within 30 seconds.",
                parent=self.dialog,
            )
            return
        except Exception as e:
            logger.exception("Failed to run make_shortcut.py")
            messagebox.showerror(
                "Launch Failed",
                f"Could not run make_shortcut.py:\n{e}",
                parent=self.dialog,
            )
            return

        if result.returncode == 0:
            logger.info("Shortcut created: %s", shortcut_path)
            tip = ("Right-click it and choose 'Pin to taskbar' or 'Pin to Start'."
                   if sys.platform == "win32" else
                   "Drag it to the Dock, or double-click from Finder (first launch "
                   "needs right-click -> Open, since it's unsigned).")
            messagebox.showinfo(
                "Shortcut Created",
                f"{shortcut_name} created at:\n{shortcut_path}\n\n{tip}",
                parent=self.dialog,
            )
        else:
            err = (result.stderr or result.stdout or "").strip() or "Unknown error"
            logger.error("make_shortcut.py failed: %s", err)
            messagebox.showerror(
                "Shortcut Creation Failed",
                f"make_shortcut.py exited with code {result.returncode}.\n\n{err}",
                parent=self.dialog,
            )

    def _build_paths_tab(self, parent):
        """Build the Paths settings tab."""
        canvas = tk.Canvas(parent, bg=COLORS["bg_primary"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        content_frame = tk.Frame(canvas, bg=COLORS["bg_primary"])

        content_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        win_id_paths = canvas.create_window((0, 0), window=content_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e, wid=win_id_paths: canvas.itemconfig(wid, width=e.width))

        # Enable mousewheel scrolling when mouse is over the canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=10)

        # === DRIVE CONFIGURATION SECTION ===
        drive_section = tk.LabelFrame(
            content_frame,
            text=" Drive Configuration ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        drive_section.pack(fill=tk.X, padx=20, pady=(0, 15))

        # Active Drive row
        work_frame = tk.Frame(drive_section, bg=COLORS["bg_card"])
        work_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            work_frame,
            text="Active Drive:",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.work_drive_var = tk.StringVar(value=self.settings.get_work_drive())
        work_entry = tk.Entry(
            work_frame,
            textvariable=self.work_drive_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        work_entry.pack(side=tk.LEFT, padx=(0, 10))
        work_entry.bind('<KeyRelease>', lambda e: self._validate_paths())

        self.work_status_label = tk.Label(
            work_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            width=25,
            anchor="w"
        )
        self.work_status_label.pack(side=tk.LEFT)

        # Help text for work drive
        tk.Label(
            drive_section,
            text="Mapped via VisualSubst to your active projects directory",
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # Active Base row
        active_frame = tk.Frame(drive_section, bg=COLORS["bg_card"])
        active_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            active_frame,
            text="Active Base:",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.active_base_var = tk.StringVar(value=self.settings.get_active_base())
        active_entry = tk.Entry(
            active_frame,
            textvariable=self.active_base_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        active_entry.pack(side=tk.LEFT, padx=(0, 10))
        active_entry.bind('<KeyRelease>', lambda e: self._validate_paths())

        browse_active_btn = tk.Button(
            active_frame,
            text="Browse",
            command=self._browse_active,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        )
        browse_active_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.active_status_label = tk.Label(
            active_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            width=20,
            anchor="w"
        )
        self.active_status_label.pack(side=tk.LEFT)

        tk.Label(
            drive_section,
            text="Real path the active drive maps to (e.g. D:\\_work\\Active)",
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # Archive Base row
        archive_frame = tk.Frame(drive_section, bg=COLORS["bg_card"])
        archive_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            archive_frame,
            text="Archive Base:",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.archive_base_var = tk.StringVar(value=self.settings.get_archive_base())
        archive_entry = tk.Entry(
            archive_frame,
            textvariable=self.archive_base_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        archive_entry.pack(side=tk.LEFT, padx=(0, 10))
        archive_entry.bind('<KeyRelease>', lambda e: self._validate_paths())

        browse_btn = tk.Button(
            archive_frame,
            text="Browse",
            command=self._browse_archive,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        )
        browse_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.archive_status_label = tk.Label(
            archive_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            width=20,
            anchor="w"
        )
        self.archive_status_label.pack(side=tk.LEFT)

        tk.Label(
            drive_section,
            text="Root directory for completed and archived projects",
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # === SOFTWARE TOOLS SECTION ===
        tools_section = tk.LabelFrame(
            content_frame,
            text=" Software Tools ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        tools_section.pack(fill=tk.X, padx=20, pady=(0, 15))

        # Mapped Software Path row
        mapped_sw_frame = tk.Frame(tools_section, bg=COLORS["bg_card"])
        mapped_sw_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            mapped_sw_frame,
            text="Software (NAS):",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.mapped_sw_var = tk.StringVar(value=self.settings.get_mapped_software_path())
        mapped_sw_entry = tk.Entry(
            mapped_sw_frame,
            textvariable=self.mapped_sw_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        mapped_sw_entry.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(
            mapped_sw_frame,
            text="Browse",
            command=lambda: self._browse_to_var(self.mapped_sw_var, "Select Software Sync Directory"),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.mapped_sw_status_label = tk.Label(
            mapped_sw_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            width=20,
            anchor="w"
        )
        self.mapped_sw_status_label.pack(side=tk.LEFT)

        tk.Label(
            tools_section,
            text="Mapped drive path for software config sync (e.g. P:\\Software)",
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # Launchers Base Path row
        launchers_frame = tk.Frame(tools_section, bg=COLORS["bg_card"])
        launchers_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            launchers_frame,
            text="Launchers Path:",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.launchers_var = tk.StringVar(value=self.settings.get_launchers_base_path())
        launchers_entry = tk.Entry(
            launchers_frame,
            textvariable=self.launchers_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        launchers_entry.pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(
            launchers_frame,
            text="Browse",
            command=lambda: self._browse_to_var(self.launchers_var, "Select Launchers Directory"),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.launchers_status_label = tk.Label(
            launchers_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            width=20,
            anchor="w"
        )
        self.launchers_status_label.pack(side=tk.LEFT)

        tk.Label(
            tools_section,
            text="Base path for portable software launchers",
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"]
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # === BUSINESS / INVOICING SECTION ===
        business_section = tk.LabelFrame(
            content_frame,
            text=" Business / Invoicing ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        business_section.pack(fill=tk.X, padx=20, pady=(0, 15))

        # Boekhouding base row
        boek_frame = tk.Frame(business_section, bg=COLORS["bg_card"])
        boek_frame.pack(fill=tk.X, pady=5)

        tk.Label(
            boek_frame,
            text="Boekhouding Path:",
            font=font.Font(family="Segoe UI", size=10),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            width=15,
            anchor="w"
        ).pack(side=tk.LEFT)

        self.boekhouding_base_var = tk.StringVar(value=self.settings.get_boekhouding_base_explicit())
        boek_entry = tk.Entry(
            boek_frame,
            textvariable=self.boekhouding_base_var,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
            width=30
        )
        boek_entry.pack(side=tk.LEFT, padx=(0, 10))
        boek_entry.bind('<KeyRelease>', lambda e: self._validate_paths())

        tk.Button(
            boek_frame,
            text="Browse",
            command=lambda: self._browse_to_var(self.boekhouding_base_var, "Select Boekhouding/Invoices Directory"),
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        ).pack(side=tk.LEFT, padx=(0, 10))

        tk.Button(
            boek_frame,
            text="Use default",
            command=self._clear_boekhouding_base,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=10
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.boekhouding_status_label = tk.Label(
            boek_frame,
            text="",
            font=font.Font(family="Segoe UI", size=9),
            bg=COLORS["bg_card"],
            anchor="w"
        )
        self.boekhouding_status_label.pack(side=tk.LEFT)

        tk.Label(
            business_section,
            text=("Root folder where invoices are filed/linked from (Invoice Manager, "
                  "WooCommerce order linking). Leave blank to use the default: "
                  "<Active Base>\\_LIBRARY\\Boekhouding"),
            font=font.Font(family="Segoe UI", size=9, slant="italic"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            wraplength=600,
            justify="left",
        ).pack(anchor="w", padx=(15 * 10, 0), pady=(0, 5))

        # === CATEGORY PATHS SECTION ===
        paths_section = tk.LabelFrame(
            content_frame,
            text=" Category Paths ",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_card"],
            padx=15,
            pady=10
        )
        paths_section.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 15))

        # Header row
        header_row = tk.Frame(paths_section, bg=COLORS["bg_card"])
        header_row.pack(fill=tk.X, pady=(0, 5))

        tk.Label(
            header_row,
            text="Category",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            width=12,
            anchor="w"
        ).pack(side=tk.LEFT)

        tk.Label(
            header_row,
            text="Active Path",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            width=25,
            anchor="w"
        ).pack(side=tk.LEFT, padx=(10, 0))

        tk.Label(
            header_row,
            text="Archive Path",
            font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"],
            bg=COLORS["bg_card"],
            width=30,
            anchor="w"
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Separator
        sep = tk.Frame(paths_section, bg=COLORS["border"], height=1)
        sep.pack(fill=tk.X, pady=5)

        # Category rows
        self.category_labels = {}
        for category in self.settings.get_ordered_categories():
            row = tk.Frame(paths_section, bg=COLORS["bg_card"])
            row.pack(fill=tk.X, pady=2)

            # Category name with color indicator
            cat_color = CATEGORY_COLORS.get(category.upper(), COLORS["text_primary"])
            cat_label = tk.Label(
                row,
                text=f"  {category}",
                font=font.Font(family="Segoe UI", size=10),
                fg=cat_color,
                bg=COLORS["bg_card"],
                width=12,
                anchor="w"
            )
            cat_label.pack(side=tk.LEFT)

            # Work path (read-only, computed from drive + subpath)
            work_path = self.settings.get_work_path(category)
            work_label = tk.Label(
                row,
                text=work_path.replace('\\', '/'),
                font=font.Font(family="Consolas", size=9),
                fg=COLORS["text_primary"],
                bg=COLORS["bg_secondary"],
                width=25,
                anchor="w",
                padx=5
            )
            work_label.pack(side=tk.LEFT, padx=(10, 0))

            # Archive path
            archive_path = self.settings.get_archive_path(category)
            archive_label = tk.Label(
                row,
                text=archive_path.replace('\\', '/'),
                font=font.Font(family="Consolas", size=9),
                fg=COLORS["text_primary"],
                bg=COLORS["bg_secondary"],
                width=30,
                anchor="w",
                padx=5
            )
            archive_label.pack(side=tk.LEFT, padx=(10, 0))

            self.category_labels[category] = {
                "work": work_label,
                "archive": archive_label
            }

            # Show subcategories if any
            cat_config = self.settings.get_category_config(category)
            subcats = cat_config.get("subcategories", [])
            if subcats:
                for subcat in subcats:
                    sub_row = tk.Frame(paths_section, bg=COLORS["bg_card"])
                    sub_row.pack(fill=tk.X, pady=1)

                    tk.Label(
                        sub_row,
                        text=f"    {subcat}",
                        font=font.Font(family="Segoe UI", size=9),
                        fg=COLORS["text_secondary"],
                        bg=COLORS["bg_card"],
                        width=12,
                        anchor="w"
                    ).pack(side=tk.LEFT)

                    tk.Label(
                        sub_row,
                        text="(inherits)",
                        font=font.Font(family="Segoe UI", size=9, slant="italic"),
                        fg=COLORS["text_secondary"],
                        bg=COLORS["bg_card"]
                    ).pack(side=tk.LEFT, padx=(10, 0))

    # ============================================================
    # Drives tab
    # ============================================================
    #
    # Editor for setup_config.json's drive_mappings list. Edits here
    # save to setup_config.json (NOT rak_config.json); the actual
    # subst / HKCU autorun / Explorer-label writes happen when you run
    # Environment Setup > Drives. This tab edits intent; the Drives
    # step applies it. The "Run Drives step now" button below is a
    # shortcut to that same step.

    def _build_drives_tab(self, parent):
        """Build the Drives configuration tab."""
        self._drives_config_path = os.path.join(
            self._project_root(), "setup_config.json"
        )
        self._drives_cfg = self._drives_load_config()

        # Header explanation
        intro = (
            "Drive mappings applied by Environment Setup > Drives. Each row "
            "defines a `subst` mapping: a virtual drive letter pointing at an "
            "existing folder on disk. Edits save to setup_config.json — click "
            "'Run Drives step now' below to apply them."
        )
        tk.Label(
            parent, text=intro,
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
            justify="left", anchor="w", wraplength=720,
        ).pack(anchor="w", padx=20, pady=(10, 6))

        # Actions row (above the list so it stays in view)
        actions = tk.Frame(parent, bg=COLORS["bg_primary"])
        actions.pack(fill=tk.X, padx=20, pady=(0, 6))

        tk.Button(
            actions, text="+ Add mapping",
            command=self._drives_add_mapping,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT)

        tk.Button(
            actions, text="Run Drives step now",
            command=lambda: self._run_environment_setup("drives"),
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT, padx=(10, 0))

        tk.Button(
            actions, text="Reload from disk",
            command=self._drives_reload,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT, padx=(10, 0))

        # Scrollable list of rows
        list_frame = tk.LabelFrame(
            parent, text=" Drive mappings ",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
            padx=8, pady=6,
        )
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        canvas = tk.Canvas(list_frame, bg=COLORS["bg_card"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self._drives_list_inner = tk.Frame(canvas, bg=COLORS["bg_card"])

        self._drives_list_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id = canvas.create_window(
            (0, 0), window=self._drives_list_inner, anchor="nw"
        )
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind(
            "<Configure>",
            lambda e, wid=win_id: canvas.itemconfig(wid, width=e.width),
        )

        def _on_mw(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mw))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._drives_render_list()

    def _drives_load_config(self):
        """Read setup_config.json into a dict. If the file is missing,
        seed from setup_config.json.example so the user sees the
        canonical mapping list; if neither exists, return a skeleton
        with an empty drive_mappings list."""
        path = self._drives_config_path
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                logger.error("Could not load setup_config.json: %s", e)
                messagebox.showerror(
                    "setup_config.json error",
                    f"Could not parse setup_config.json:\n{e}\n\n"
                    "Drives tab will open empty; fix the file on disk "
                    "and click 'Reload from disk' to recover.",
                    parent=self.dialog,
                )
                return {"drive_mappings": []}

        example = os.path.join(
            self._project_root(), "setup_config.json.example"
        )
        if os.path.isfile(example):
            try:
                with open(example, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (OSError, json.JSONDecodeError):
                pass
        return {"drive_mappings": []}

    def _drives_reload(self):
        """Discard in-memory edits and re-read from disk."""
        if not messagebox.askyesno(
            "Reload drive mappings?",
            "Reload from setup_config.json on disk?\n\n"
            "Any unsaved changes in this tab will be lost.",
            parent=self.dialog,
        ):
            return
        self._drives_cfg = self._drives_load_config()
        self._drives_render_list()

    def _drives_render_list(self):
        """Rebuild every row from self._drives_cfg["drive_mappings"]."""
        for child in self._drives_list_inner.winfo_children():
            child.destroy()
        self._drives_row_vars = []

        mappings = self._drives_cfg.get("drive_mappings", []) or []

        # Header row
        hdr = tk.Frame(self._drives_list_inner, bg=COLORS["bg_card"])
        hdr.pack(fill=tk.X, padx=2, pady=(0, 4))
        for text, width in [
            ("Letter", 5), ("Target Path", 30), ("Label", 12),
            ("Description", 24), ("OK", 3),
        ]:
            tk.Label(
                hdr, text=text, width=width, anchor="w",
                font=font.Font(family="Segoe UI", size=9, weight="bold"),
                fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
            ).pack(side=tk.LEFT)

        if not mappings:
            tk.Label(
                self._drives_list_inner,
                text=("No drive mappings configured. Click '+ Add mapping' "
                      "to create one."),
                font=font.Font(family="Segoe UI", size=9, slant="italic"),
                fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
            ).pack(anchor="nw", padx=4, pady=8)
            return

        for idx, m in enumerate(mappings):
            self._drives_build_row(idx, m)

    def _drives_build_row(self, idx, mapping):
        row = tk.Frame(self._drives_list_inner, bg=COLORS["bg_card"])
        row.pack(fill=tk.X, padx=2, pady=1)

        letter_var = tk.StringVar(value=mapping.get("drive_letter", ""))
        target_var = tk.StringVar(value=mapping.get("target_path", ""))
        label_var  = tk.StringVar(value=mapping.get("label", ""))
        desc_var   = tk.StringVar(value=mapping.get("description", ""))

        # Wire changes back into the in-memory cfg so _save picks them
        # up without an extra Apply step — same pattern the Startup
        # Apps tab uses.
        def _bind(var, key):
            def _cb(*_a, key=key, var=var, idx=idx):
                try:
                    self._drives_cfg["drive_mappings"][idx][key] = var.get()
                except (IndexError, KeyError):
                    pass
                if key == "target_path":
                    self._drives_update_status(idx, var.get())
            var.trace_add("write", _cb)
        _bind(letter_var, "drive_letter")
        _bind(target_var, "target_path")
        _bind(label_var,  "label")
        _bind(desc_var,   "description")

        tk.Entry(
            row, textvariable=letter_var, width=5,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
        ).pack(side=tk.LEFT, padx=(2, 4))

        tk.Entry(
            row, textvariable=target_var, width=30,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
        ).pack(side=tk.LEFT, padx=(0, 2))

        tk.Button(
            row, text="...",
            command=lambda v=target_var: self._drives_browse_target(v),
            bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT, cursor="hand2", padx=4, pady=0, width=2,
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Entry(
            row, textvariable=label_var, width=12,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        tk.Entry(
            row, textvariable=desc_var, width=24,
            font=font.Font(family="Segoe UI", size=10),
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"],
        ).pack(side=tk.LEFT, padx=(0, 4))

        status_lbl = tk.Label(
            row, width=3, anchor="center",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            bg=COLORS["bg_card"],
        )
        status_lbl.pack(side=tk.LEFT)

        tk.Button(
            row, text="×",
            command=lambda i=idx: self._drives_remove(i),
            bg=COLORS["bg_card"], fg="#ef4444",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            relief=tk.FLAT, cursor="hand2", width=2, padx=0, pady=0,
        ).pack(side=tk.RIGHT, padx=(2, 0))

        self._drives_row_vars.append({
            "letter": letter_var, "target": target_var,
            "label":  label_var,  "description": desc_var,
            "status_lbl": status_lbl,
        })
        self._drives_update_status(idx, target_var.get())

    def _drives_update_status(self, idx, target_path):
        """Refresh the OK/missing indicator for a row when target_path
        changes (or when the row is built)."""
        try:
            lbl = self._drives_row_vars[idx]["status_lbl"]
        except (IndexError, KeyError):
            return
        if target_path and os.path.isdir(target_path):
            lbl.config(text="✓", fg="#22c55e")
        else:
            lbl.config(text="✗", fg="#ef4444")

    def _drives_browse_target(self, var):
        """Open folder browser to pick a target path for a mapping."""
        current = var.get()
        initial_dir = current if os.path.isdir(current) else None
        folder = filedialog.askdirectory(
            parent=self.dialog,
            title="Select drive mapping target folder",
            initialdir=initial_dir,
        )
        if folder:
            var.set(folder.replace("/", "\\"))

    def _drives_add_mapping(self):
        """Append a blank mapping and rebuild the list."""
        mappings = self._drives_cfg.setdefault("drive_mappings", [])
        mappings.append({
            "drive_letter": "",
            "target_path":  "",
            "label":        "",
            "description":  "",
        })
        self._drives_render_list()

    def _drives_remove(self, idx):
        """Remove a mapping by index and rebuild."""
        mappings = self._drives_cfg.get("drive_mappings", [])
        if not (0 <= idx < len(mappings)):
            return
        removed = mappings.pop(idx)
        logger.info("Removed drive mapping: %s -> %s",
                    removed.get("drive_letter"), removed.get("target_path"))
        self._drives_render_list()

    def _drives_save_config(self):
        """Write self._drives_cfg back to setup_config.json. Normalizes
        drive_letter to upper-case "X:" form and target_path to
        backslashes to match the file's existing style. Drops fully
        blank rows. Preserves all other top-level keys
        (folder_structure, synology_drive, workstation_apps, etc.)
        because we round-tripped the whole file. Raises OSError on
        write failure so the caller can surface it."""
        mappings = self._drives_cfg.get("drive_mappings", []) or []
        cleaned = []
        for m in mappings:
            letter = (m.get("drive_letter") or "").strip().upper()
            # Accept "M", "m", "M:", "M:\" etc -> "M:"
            letter = letter.rstrip("\\").rstrip(":")
            if letter:
                letter = f"{letter[0]}:"
            target = (m.get("target_path") or "").strip().replace("/", "\\")
            entry = {
                "drive_letter": letter,
                "target_path":  target,
                "label":        (m.get("label") or "").strip(),
                "description":  (m.get("description") or "").strip(),
            }
            # Skip totally blank rows so they don't pollute the file.
            if not entry["drive_letter"] and not entry["target_path"]:
                continue
            # Preserve any extras (registry_name, custom keys) from
            # the original mapping.
            for k, v in m.items():
                if k not in entry:
                    entry[k] = v
            cleaned.append(entry)

        self._drives_cfg["drive_mappings"] = cleaned

        with open(self._drives_config_path, "w", encoding="utf-8") as f:
            json.dump(self._drives_cfg, f, indent=2, ensure_ascii=False)
            f.write("\n")
        logger.info("Wrote drive_mappings (%d entries) to %s",
                    len(cleaned), self._drives_config_path)

    def _build_software_tab(self, parent):
        """Build the Software Defaults settings tab."""
        # Scrollable frame for software settings
        canvas = tk.Canvas(parent, bg=COLORS["bg_primary"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=canvas.yview)
        scrollable_frame = tk.Frame(canvas, bg=COLORS["bg_primary"])

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )

        win_id_sw = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e, wid=win_id_sw: canvas.itemconfig(wid, width=e.width))

        # Enable mousewheel scrolling when mouse is over the canvas
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1*(event.delta/120)), "units")

        def _bind_mousewheel(event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_mousewheel(event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_mousewheel)
        canvas.bind("<Leave>", _unbind_mousewheel)

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, pady=10)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, pady=10)

        # Store software entry widgets for saving
        self.software_entries = {}

        # Get current software defaults (flat dict)
        software_defaults = self.settings.get_software_defaults()

        # Software grouped by section for display (vertical layout)
        software_sections = [
            ("3D", ["houdini", "blender", "freecad", "alibre", "slicer", "printer"]),
            ("2D", ["affinity"]),
            ("FX", ["fusion", "after_effects"]),
            ("RealTime", ["godot", "resolume", "touchdesigner", "python", "platform", "renderer", "resolution"]),
            ("Audio", ["ableton", "reaper", "traktor"]),
        ]

        for section_label, software_list in software_sections:
            section = tk.LabelFrame(
                scrollable_frame,
                text=f" {section_label} ",
                font=font.Font(family="Segoe UI", size=11, weight="bold"),
                fg=COLORS["text_primary"],
                bg=COLORS["bg_card"],
                padx=15,
                pady=10
            )
            section.pack(fill=tk.X, padx=20, pady=(0, 15))

            for software in software_list:
                # Skip if already has an entry (e.g. touchdesigner appears in both)
                if software in self.software_entries:
                    continue

                current_value = software_defaults.get(software, "")
                label_text = software.replace("_", " ").title()

                row = tk.Frame(section, bg=COLORS["bg_card"])
                row.pack(fill=tk.X, pady=2)

                tk.Label(
                    row,
                    text=f"{label_text}:",
                    font=font.Font(family="Segoe UI", size=10),
                    fg=COLORS["text_secondary"],
                    bg=COLORS["bg_card"],
                    width=18,
                    anchor="w"
                ).pack(side=tk.LEFT)

                var = tk.StringVar(value=current_value)
                entry = tk.Entry(
                    row,
                    textvariable=var,
                    font=font.Font(family="Segoe UI", size=10),
                    bg=COLORS["bg_secondary"],
                    fg=COLORS["text_primary"],
                    insertbackground=COLORS["text_primary"],
                    width=20
                )
                entry.pack(side=tk.LEFT)

                self.software_entries[software] = var

    # ============================================================
    # Startup Apps tab
    # ============================================================
    #
    # Editor for the data-driven launcher in tools/startup/StartupLauncher.ps1.
    # The sidecar JSON at %LOCALAPPDATA%\PipelineManager\startup_apps.json is
    # owned by modules/startup_apps_manager.py — this tab is the user-facing
    # surface. Saves go through sam.save_config() in _save(); the scheduled-
    # task install/uninstall buttons mutate Windows state immediately (not
    # tied to the Save button) because they're irreversible-ish operations
    # the user should opt into explicitly.

    _STARTUP_POSITIONS = ("maximize", "fullscreen", "free")
    _STARTUP_DESKTOP_CHOICES = (1, 2, 3, 4, 5, 6)

    @staticmethod
    def _startup_make_toggle(parent, var, on_text="ON", off_text="OFF"):
        """Replacement for tk.Checkbutton — the default indicator reads
        poorly against the dark theme. This button flips between a vivid
        green (on) and muted grey (off) so state is obvious at a glance.
        Tracks the BooleanVar two-way so programmatic changes update the
        visual."""
        btn = tk.Button(
            parent,
            font=font.Font(family="Segoe UI", size=8, weight="bold"),
            relief=tk.FLAT, cursor="hand2", width=3, padx=4, pady=2,
            borderwidth=0, highlightthickness=0,
        )

        def render():
            if var.get():
                btn.config(text=on_text, bg="#22c55e", fg="white",
                           activebackground="#16a34a", activeforeground="white")
            else:
                btn.config(text=off_text, bg="#374151", fg="#d1d5db",
                           activebackground="#4b5563", activeforeground="white")

        def toggle():
            var.set(not var.get())

        btn.config(command=toggle)
        render()
        var.trace_add("write", lambda *_a: render())
        return btn

    def _startup_build_timing_controls(self, parent):
        """Controls for the launcher's two desktop-switch delays plus
        where it lands when done (StartupLauncher.ps1):
          - line ~536 ``Start-Sleep -Milliseconds $T.BetweenDesktopsDelay``
            — pause between each desktop while placing apps.
          - line ~541 ``Start-Sleep -Milliseconds $T.FinalInitDelay`` then
            ``Switch-Desktop`` to ``final_desktop`` — pause + which
            desktop the launcher returns to once every app is placed
            (used to be hardcoded to desktop 1).
        All three bind the same way per-row app vars do (trace_add ->
        write straight into self._startup_cfg) so the global Save button
        picks them up with no extra sync step."""
        timing = self._startup_cfg.setdefault("timing", {})

        row1 = tk.Frame(parent, bg=COLORS["bg_card"])
        row1.pack(fill=tk.X, anchor="w")

        tk.Label(
            row1, text="Delay between desktop switches:",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

        self._startup_between_desktops_var = tk.IntVar(
            value=int(timing.get("between_desktops_delay_ms", 1500))
        )

        def _on_between_change(*_a):
            try:
                self._startup_cfg["timing"]["between_desktops_delay_ms"] = \
                    max(0, int(self._startup_between_desktops_var.get()))
            except (ValueError, tk.TclError):
                pass
        self._startup_between_desktops_var.trace_add("write", _on_between_change)

        ttk.Spinbox(
            row1, from_=0, to=10000, increment=100, width=8,
            textvariable=self._startup_between_desktops_var,
        ).pack(side=tk.LEFT, padx=(6, 4))

        tk.Label(
            row1, text="ms  (pause after placing a desktop's apps, before switching to the next)",
            font=font.Font(family="Segoe UI", size=8),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

        row2 = tk.Frame(parent, bg=COLORS["bg_card"])
        row2.pack(fill=tk.X, anchor="w", pady=(4, 0))

        tk.Label(
            row2, text="Before returning to the landing desktop:",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

        self._startup_final_delay_var = tk.IntVar(
            value=int(timing.get("final_init_delay_ms", 15000))
        )

        def _on_final_delay_change(*_a):
            try:
                self._startup_cfg["timing"]["final_init_delay_ms"] = \
                    max(0, int(self._startup_final_delay_var.get()))
            except (ValueError, tk.TclError):
                pass
        self._startup_final_delay_var.trace_add("write", _on_final_delay_change)

        ttk.Spinbox(
            row2, from_=0, to=60000, increment=500, width=8,
            textvariable=self._startup_final_delay_var,
        ).pack(side=tk.LEFT, padx=(6, 4))

        tk.Label(
            row2, text="ms   Land on desktop:",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT, padx=(10, 0))

        self._startup_final_desktop_var = tk.IntVar(
            value=int(self._startup_cfg.get("final_desktop", 1))
        )

        def _on_final_desktop_change(*_a):
            try:
                self._startup_cfg["final_desktop"] = max(1, int(self._startup_final_desktop_var.get()))
            except (ValueError, tk.TclError):
                pass
        self._startup_final_desktop_var.trace_add("write", _on_final_desktop_change)

        ttk.Combobox(
            row2, textvariable=self._startup_final_desktop_var,
            values=list(self._STARTUP_DESKTOP_CHOICES),
            state="readonly", width=4,
        ).pack(side=tk.LEFT, padx=(4, 4))

        tk.Label(
            row2, text="(after every app is placed — was always desktop 1 before)",
            font=font.Font(family="Segoe UI", size=8),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

    def _build_startup_apps_tab(self, parent):
        try:
            import startup_apps_manager as sam  # noqa: WPS433
        except ImportError as e:
            tk.Label(
                parent,
                text=f"startup_apps_manager module not available:\n{e}",
                font=font.Font(family="Segoe UI", size=10),
                fg=COLORS["text_secondary"],
                bg=COLORS["bg_primary"],
                justify="left",
            ).pack(padx=20, pady=20, anchor="nw")
            return

        self._sam = sam
        self._startup_cfg = sam.load_config()
        self._startup_monitors = sam.detect_monitors()
        self._startup_row_vars = []  # parallel to cfg["apps"]
        self._startup_workstation_choices = sam.list_workstation_choices()

        # ----- Top: master toggle -----
        top = tk.Frame(parent, bg=COLORS["bg_primary"])
        top.pack(fill=tk.X, padx=20, pady=(10, 4))

        self._startup_enabled_var = tk.BooleanVar(
            value=bool(self._startup_cfg.get("enabled", False))
        )
        # Toggle button + plain label gives a clearer on/off signal than
        # a default Checkbutton's tiny indicator on a dark background.
        self._startup_make_toggle(top, self._startup_enabled_var).pack(side=tk.LEFT)
        tk.Label(
            top,
            text="  Enable startup app launcher  (read by the scheduled task on logon)",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_primary"],
        ).pack(side=tk.LEFT)

        # ----- Dependencies panel -----
        # Compact view of what the launcher needs to work — kept in this
        # tab (not a separate Dependencies tab) because every dep here
        # exists solely to enable this feature.
        self._startup_deps_frame = tk.LabelFrame(
            parent, text=" Dependencies ",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
            padx=10, pady=6,
        )
        self._startup_deps_frame.pack(fill=tk.X, padx=20, pady=(6, 4))
        self._startup_render_deps()

        # ----- Actions -----
        # Boxless — sits between Dependencies and Desktop Transition
        # Timing rather than in its own panel. Even gaps between buttons
        # (padx=(0, 8), none trailing) since there's no frame padding to
        # rely on here. Colors carry meaning: green/red for the task
        # toggle mirror the per-app ON/OFF style below, blue marks the
        # primary "try it" action, amber flags the one action that can
        # overwrite the app list, and grey is reserved for the truly
        # inert utility.
        actions_frame = tk.Frame(parent, bg=COLORS["bg_primary"])
        actions_frame.pack(fill=tk.X, padx=20, pady=(4, 4))

        self._startup_task_btn = tk.Button(
            actions_frame, text="...",
            command=self._startup_toggle_task,
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        )
        self._startup_task_btn.pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            actions_frame, text="Test now",
            command=self._startup_test_now,
            bg=COLORS["accent_dark"], fg="#ffffff",
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT, padx=(0, 8))

        tk.Button(
            actions_frame, text="Refresh paths",
            command=self._startup_refresh_paths,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT, padx=(0, 8))

        self._startup_import_btn = tk.Button(
            actions_frame, text="Import legacy Startup folder",
            command=self._startup_import_legacy,
            bg=COLORS["warning"], fg=COLORS["bg_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        )
        self._startup_import_btn.pack(side=tk.LEFT)

        # ----- Desktop transition timing -----
        timing_frame = tk.LabelFrame(
            parent, text=" Desktop Transition Timing ",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
            padx=10, pady=6,
        )
        timing_frame.pack(fill=tk.X, padx=20, pady=(6, 4))
        self._startup_build_timing_controls(timing_frame)

        # ----- Monitor strip -----
        mon_frame = tk.Frame(parent, bg=COLORS["bg_primary"])
        mon_frame.pack(fill=tk.X, padx=20, pady=(4, 4))

        if not self._startup_monitors:
            mon_text = "Monitors: detection unavailable (non-Windows or display API failed)"
        else:
            parts = []
            for m in self._startup_monitors:
                tag = " primary" if m["primary"] else ""
                parts.append(
                    f"  [{m['index']}] {m['width']}x{m['height']} @ ({m['x']},{m['y']}){tag}"
                )
            mon_text = "Monitors:" + "  ".join(parts)
        tk.Label(
            mon_frame, text=mon_text,
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
            anchor="w", justify="left",
        ).pack(anchor="w")

        # ----- Add-app footer -----
        # Packed BEFORE the apps list with side=tk.BOTTOM so the apps
        # list (which expands) eats the slack ABOVE it. Same trick the
        # bottom Save/Cancel button row uses — without it the footer
        # gets pushed off-screen whenever the dialog is shorter than
        # the unconstrained app list would be.
        add_frame = tk.Frame(parent, bg=COLORS["bg_primary"])
        add_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=(4, 10))

        # ----- Apps list (scrollable, takes remaining vertical space) -----
        list_frame = tk.LabelFrame(
            parent, text=" Apps ",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
            padx=8, pady=6,
        )
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(4, 4))

        canvas = tk.Canvas(list_frame, bg=COLORS["bg_card"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=canvas.yview)
        self._startup_list_inner = tk.Frame(canvas, bg=COLORS["bg_card"])

        self._startup_list_inner.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id = canvas.create_window((0, 0), window=self._startup_list_inner, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e, wid=win_id: canvas.itemconfig(wid, width=e.width))

        def _on_mw(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mw))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        # Plain-text indicator (no icons): show the name as-is when the
        # app resolves to a launchable exe, append "(not installed)"
        # otherwise. Matches the pre-checkmark presentation.
        choice_labels = []
        self._startup_choice_map = {}  # label -> workstation name
        for choice in self._startup_workstation_choices:
            name = choice["name"]
            if choice["resolved_path"]:
                label = name
            else:
                label = f"{name}  (not installed)"
            choice_labels.append(label)
            self._startup_choice_map[label] = name

        tk.Label(
            add_frame, text="Add workstation app:",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
        ).pack(side=tk.LEFT)

        self._startup_add_combo = ttk.Combobox(
            add_frame, values=choice_labels, state="readonly", width=32,
        )
        self._startup_add_combo.pack(side=tk.LEFT, padx=(6, 4))

        tk.Button(
            add_frame, text="Add",
            command=self._startup_add_workstation,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
        ).pack(side=tk.LEFT)

        tk.Label(
            add_frame, text="    or    ",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
        ).pack(side=tk.LEFT)

        tk.Button(
            add_frame, text="Browse for file...",
            command=self._startup_browse_custom,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
        ).pack(side=tk.LEFT)

        # Initial render
        self._startup_update_task_button()
        self._startup_render_list()

    # ---------- Dependencies panel ----------

    def _startup_render_deps(self):
        """Re-run the dep check and rebuild the panel. Called on tab
        build and from the Recheck button."""
        for child in self._startup_deps_frame.winfo_children():
            child.destroy()
        deps = self._sam.check_dependencies()

        # Header with Recheck button on the right
        hdr = tk.Frame(self._startup_deps_frame, bg=COLORS["bg_card"])
        hdr.pack(fill=tk.X, pady=(0, 4))
        n_ok = sum(1 for d in deps if d["ok"])
        n_req_missing = sum(1 for d in deps if d["required"] and not d["ok"])
        summary = f"{n_ok}/{len(deps)} present"
        if n_req_missing:
            summary += f" — {n_req_missing} required missing"
        tk.Label(
            hdr, text=summary,
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["warning"] if n_req_missing else COLORS["text_secondary"],
            bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)
        tk.Button(
            hdr, text="Recheck",
            command=self._startup_render_deps,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=9),
            relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
        ).pack(side=tk.RIGHT)

        for dep in deps:
            self._startup_build_dep_row(dep)

    def _startup_build_dep_row(self, dep):
        row = tk.Frame(self._startup_deps_frame, bg=COLORS["bg_card"])
        row.pack(fill=tk.X, pady=1)

        ok = dep["ok"]
        icon = "✓" if ok else ("!" if not dep["required"] else "✗")
        icon_color = (
            "#22c55e" if ok
            else ("#f59e0b" if not dep["required"] else "#ef4444")
        )

        # Install button on the right first so the detail label can fill
        # the remaining space and wrap cleanly.
        if dep.get("install_label") and dep.get("install_action") and not ok:
            tk.Button(
                row, text=dep["install_label"],
                command=lambda a=dep["install_action"]: self._startup_run_install(a),
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                font=font.Font(family="Segoe UI", size=9),
                relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            ).pack(side=tk.RIGHT, padx=(4, 0))
        elif dep.get("install_label") and dep.get("install_action") and ok:
            # Already installed but offer a Reinstall path (useful for the
            # VirtualDesktop module after a Windows update breaks it).
            tk.Button(
                row, text=dep["install_label"],
                command=lambda a=dep["install_action"]: self._startup_run_install(a),
                bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
                font=font.Font(family="Segoe UI", size=9),
                relief=tk.FLAT, cursor="hand2", padx=10, pady=2,
            ).pack(side=tk.RIGHT, padx=(4, 0))

        tk.Label(
            row, text=icon, width=2,
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=icon_color, bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

        name_text = dep["name"]
        if dep["required"] and not ok:
            name_text += "  (required)"
        tk.Label(
            row, text=name_text, width=28, anchor="w",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
        ).pack(side=tk.LEFT)

        tk.Label(
            row, text=dep.get("detail", ""),
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
            anchor="w", justify="left",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

    def _startup_run_install(self, action):
        ok, detail = self._sam.run_install_action(action)
        if ok:
            messagebox.showinfo(
                "Installer launched",
                detail + "\n\nClick Recheck once it's done to refresh the status.",
                parent=self.dialog,
            )
        else:
            messagebox.showerror("Could not launch installer", detail, parent=self.dialog)

    # ---------- Apps list rendering ----------

    def _startup_render_list(self):
        """Rebuild every row from the in-memory cfg. Called whenever the
        list changes (add/remove/reorder/import)."""
        for child in self._startup_list_inner.winfo_children():
            child.destroy()
        self._startup_row_vars = []

        apps = self._startup_cfg.get("apps", [])
        if not apps:
            tk.Label(
                self._startup_list_inner,
                text="No startup apps configured. Add one from the picker below, "
                     "or click Import to pull in existing shortcuts.",
                font=font.Font(family="Segoe UI", size=9, slant="italic"),
                fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
                wraplength=600, justify="left",
            ).pack(anchor="nw", padx=4, pady=8)
            return

        # Header row
        hdr = tk.Frame(self._startup_list_inner, bg=COLORS["bg_card"])
        hdr.pack(fill=tk.X, padx=2, pady=(0, 4))
        for text, width in [
            ("On", 3), ("Label", 22), ("Monitor", 8), ("Desktop", 8),
            ("Position", 12), ("Timeout", 9), ("Order", 8),
        ]:
            tk.Label(
                hdr, text=text, width=width, anchor="w",
                font=font.Font(family="Segoe UI", size=9, weight="bold"),
                fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
            ).pack(side=tk.LEFT)

        monitor_count = max(1, len(self._startup_monitors) or 1)
        monitor_choices = list(range(1, max(monitor_count, 1) + 1))

        for idx, app in enumerate(apps):
            self._startup_build_row(idx, app, monitor_choices)

    def _startup_build_row(self, idx, app, monitor_choices):
        row = tk.Frame(self._startup_list_inner, bg=COLORS["bg_card"])
        row.pack(fill=tk.X, padx=2, pady=1)

        enabled_var = tk.BooleanVar(value=bool(app.get("enabled", True)))
        monitor_var = tk.IntVar(value=int(app.get("monitor") or 1))
        desktop_var = tk.IntVar(value=int(app.get("virtual_desktop") or 1))
        position_var = tk.StringVar(value=str(app.get("position") or "maximize"))
        timeout_var = tk.IntVar(value=int(app.get("window_timeout_ms") or 5000))

        # Wire each var so changes flow into the cfg immediately. Saves
        # pick up the latest cfg in _save() with no extra wiring.
        def _bind(var, key, cast):
            def _cb(*_a, key=key, cast=cast, var=var, idx=idx):
                try:
                    self._startup_cfg["apps"][idx][key] = cast(var.get())
                except (ValueError, IndexError):
                    pass
            var.trace_add("write", _cb)
        _bind(enabled_var, "enabled", bool)
        _bind(monitor_var, "monitor", int)
        _bind(desktop_var, "virtual_desktop", int)
        _bind(position_var, "position", str)
        _bind(timeout_var, "window_timeout_ms", int)

        self._startup_row_vars.append({
            "enabled": enabled_var, "monitor": monitor_var,
            "desktop": desktop_var, "position": position_var,
            "timeout": timeout_var,
        })

        self._startup_make_toggle(row, enabled_var).pack(side=tk.LEFT, padx=(2, 6))

        label = app.get("label") or "(unnamed)"
        path = app.get("resolved_path") or app.get("custom_path") or ""
        path_ok = bool(path) and os.path.isfile(path)
        label_color = COLORS["text_primary"] if path_ok else "#f59e0b"  # amber when missing
        tooltip = path or "(unresolved path)"
        lbl = tk.Label(
            row, text=label, width=22, anchor="w",
            font=font.Font(family="Segoe UI", size=10),
            fg=label_color, bg=COLORS["bg_card"],
        )
        lbl.pack(side=tk.LEFT)
        # Lightweight "tooltip": click the label to print path to status — keep simple
        lbl.bind("<Button-1>", lambda e, p=tooltip: messagebox.showinfo("Path", p, parent=self.dialog))

        ttk.Combobox(
            row, textvariable=monitor_var, values=monitor_choices,
            state="readonly", width=4,
        ).pack(side=tk.LEFT, padx=(4, 6))

        ttk.Combobox(
            row, textvariable=desktop_var, values=list(self._STARTUP_DESKTOP_CHOICES),
            state="readonly", width=4,
        ).pack(side=tk.LEFT, padx=(4, 6))

        ttk.Combobox(
            row, textvariable=position_var, values=list(self._STARTUP_POSITIONS),
            state="readonly", width=10,
        ).pack(side=tk.LEFT, padx=(4, 6))

        # How long the launcher waits for this app's window before giving
        # up on placing it — was a hardcoded 2500ms (browsers) / 5000ms
        # (everything else) guess by path pattern; now per-app, 5000ms
        # default for every app regardless of type.
        ttk.Spinbox(
            row, from_=500, to=30000, increment=500, width=7,
            textvariable=timeout_var,
        ).pack(side=tk.LEFT, padx=(4, 6))

        # Order is implicit (list index within its desktop); show order
        # number for clarity but the buttons drive it.
        order_label = tk.Label(
            row, text=str(app.get("launch_order", 0)), width=4, anchor="w",
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
        )
        order_label.pack(side=tk.LEFT, padx=(4, 4))

        # Right-side action buttons
        tk.Button(
            row, text="×", command=lambda i=idx: self._startup_remove(i),
            bg=COLORS["bg_card"], fg="#ef4444",
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            relief=tk.FLAT, cursor="hand2", width=2, padx=0, pady=0,
        ).pack(side=tk.RIGHT, padx=(2, 0))
        tk.Button(
            row, text="↓", command=lambda i=idx: self._startup_move(i, +1),
            bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", width=2, padx=0, pady=0,
        ).pack(side=tk.RIGHT)
        tk.Button(
            row, text="↑", command=lambda i=idx: self._startup_move(i, -1),
            bg=COLORS["bg_card"], fg=COLORS["text_secondary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", width=2, padx=0, pady=0,
        ).pack(side=tk.RIGHT)

    # ---------- List mutations ----------

    def _startup_remove(self, idx):
        try:
            removed = self._startup_cfg["apps"].pop(idx)
        except IndexError:
            return
        self._startup_renumber_orders()
        self._startup_render_list()
        logger.info("Removed startup app: %s", removed.get("label"))

    def _startup_move(self, idx, delta):
        apps = self._startup_cfg.get("apps", [])
        new_idx = idx + delta
        if new_idx < 0 or new_idx >= len(apps):
            return
        apps[idx], apps[new_idx] = apps[new_idx], apps[idx]
        self._startup_renumber_orders()
        self._startup_render_list()

    def _startup_renumber_orders(self):
        """Renumber launch_order within each virtual_desktop based on
        list position. Keeps the JSON tidy + the launcher deterministic."""
        per_desktop_counter: dict = {}
        for app in self._startup_cfg.get("apps", []):
            vd = int(app.get("virtual_desktop") or 1)
            per_desktop_counter.setdefault(vd, 0)
            app["launch_order"] = per_desktop_counter[vd]
            per_desktop_counter[vd] += 1

    def _startup_add_workstation(self):
        label = self._startup_add_combo.get()
        if not label:
            return
        name = self._startup_choice_map.get(label)
        if not name:
            return
        sam = self._sam
        resolved = sam.resolve_workstation_app(name) or ""
        entry = sam.new_app_entry(
            label=name,
            source="workstation_app",
            workstation_name=name,
            resolved_path=resolved,
            monitor=1,
            virtual_desktop=1,
            position="maximize",
            launch_order=len(self._startup_cfg.get("apps", [])),
            enabled=True,
        )
        self._startup_cfg.setdefault("apps", []).append(entry)
        self._startup_renumber_orders()
        self._startup_render_list()
        self._startup_add_combo.set("")

    def _startup_browse_custom(self):
        path = filedialog.askopenfilename(
            parent=self.dialog,
            title="Pick an .exe, .lnk, .bat, or .url to launch at startup",
            filetypes=[
                ("Launchable", "*.exe *.lnk *.bat *.url *.cmd *.vbs"),
                ("All files", "*.*"),
            ],
        )
        if not path:
            return
        sam = self._sam
        label = os.path.splitext(os.path.basename(path))[0]
        if path.lower().endswith(".lnk"):
            target, args = sam.resolve_shortcut(path)
        else:
            target, args = path, ""
        entry = sam.new_app_entry(
            label=label,
            source="custom_path",
            custom_path=path,
            resolved_path=target,
            args=args,
            monitor=1,
            virtual_desktop=1,
            position="maximize",
            launch_order=len(self._startup_cfg.get("apps", [])),
            enabled=True,
        )
        self._startup_cfg.setdefault("apps", []).append(entry)
        self._startup_renumber_orders()
        self._startup_render_list()

    def _startup_import_legacy(self):
        sam = self._sam
        discovered = sam.find_importable_shortcuts()
        if not discovered:
            messagebox.showinfo(
                "Nothing to import",
                "No DesktopN subfolders or shortcuts found under "
                f"{os.path.expanduser('~')}\\Startup.",
                parent=self.dialog,
            )
            return
        existing = len(self._startup_cfg.get("apps", []))
        msg = (
            f"Found {len(discovered)} shortcut(s) under your legacy Startup folder.\n\n"
            "Replace the current list, or append to it?\n\n"
            f"  Yes  = REPLACE (current list of {existing} will be discarded)\n"
            "  No   = APPEND\n"
            "  Cancel = do nothing"
        )
        choice = messagebox.askyesnocancel("Import startup shortcuts", msg, parent=self.dialog)
        if choice is None:
            return
        sam.import_existing_shortcuts(self._startup_cfg, replace=bool(choice))
        self._startup_renumber_orders()
        self._startup_render_list()

    def _startup_refresh_paths(self):
        """Re-resolve every entry's resolved_path. Useful after installing
        an app or moving a custom file."""
        self._sam.refresh_resolved_paths(self._startup_cfg)
        self._startup_render_list()

    # ---------- Scheduled task ----------

    def _startup_update_task_button(self):
        installed = self._sam.is_task_installed()
        # Green = safe/constructive (nothing installed yet); red = this
        # click removes the logon automation — same color language as the
        # per-app ON/OFF toggle.
        if installed:
            text, bg = "Uninstall scheduled task", COLORS["error"]
        else:
            text, bg = "Install scheduled task", COLORS["success"]
        self._startup_task_btn.config(text=text, bg=bg, fg="#ffffff")

    def _startup_toggle_task(self):
        sam = self._sam
        if sam.is_task_installed():
            if not messagebox.askyesno(
                "Uninstall scheduled task?",
                "This removes the FastRak_StartupLauncher entry from Task Scheduler.\n"
                "The launcher will no longer run automatically on logon.\n\n"
                "Continue?",
                parent=self.dialog,
            ):
                return
            ok, detail = sam.uninstall_scheduled_task()
        else:
            # Make sure the launcher is deployed before we register a task
            # that points at it.
            deploy_ok, deploy_detail = sam.deploy_launcher_script()
            if not deploy_ok:
                messagebox.showerror(
                    "Could not deploy launcher",
                    f"Cannot install scheduled task because the launcher "
                    f"could not be copied to your AppData folder:\n\n{deploy_detail}",
                    parent=self.dialog,
                )
                return
            ok, detail = sam.install_scheduled_task()
        if ok:
            messagebox.showinfo("Done", detail, parent=self.dialog)
        else:
            messagebox.showerror("Scheduled task error", detail, parent=self.dialog)
        self._startup_update_task_button()

    def _startup_test_now(self):
        """Save current settings first, then invoke the launcher in a
        visible console so the user can watch."""
        sam = self._sam
        self._startup_cfg["enabled"] = bool(self._startup_enabled_var.get())
        try:
            sam.save_config(self._startup_cfg)
        except OSError as e:
            messagebox.showerror("Save failed", str(e), parent=self.dialog)
            return
        ok, detail = sam.run_launcher_now()
        if not ok:
            messagebox.showerror("Could not run launcher", detail, parent=self.dialog)

    # ============================================================
    # Workstation Apps tab
    # ============================================================
    #
    # Status viewer + thin launcher over modules/workstation_apps.py.
    # Per-app install runs winget in a new console (so the dialog stays
    # responsive); "Install All Missing" hands off to install.py --step
    # apps so the user gets the full picker UI in a console.

    def _build_workstation_apps_tab(self, parent):
        """Build the Workstation Apps tab: status overview + install actions."""
        try:
            import workstation_apps as wa  # noqa: WPS433
        except ImportError as e:
            tk.Label(
                parent,
                text=f"workstation_apps module not available:\n{e}",
                font=font.Font(family="Segoe UI", size=10),
                fg=COLORS["text_secondary"],
                bg=COLORS["bg_primary"],
                justify="left",
            ).pack(padx=20, pady=20, anchor="nw")
            return

        self._wa = wa  # cached for action callbacks

        # Header: summary line + action buttons
        header = tk.Frame(parent, bg=COLORS["bg_primary"])
        header.pack(fill=tk.X, padx=20, pady=(10, 5))

        self._apps_summary_var = tk.StringVar(value="Loading...")
        tk.Label(
            header,
            textvariable=self._apps_summary_var,
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"],
            bg=COLORS["bg_primary"],
        ).pack(side=tk.LEFT)

        actions = tk.Frame(parent, bg=COLORS["bg_primary"])
        actions.pack(fill=tk.X, padx=20, pady=(0, 10))

        tk.Button(
            actions, text="Install Missing...",
            command=self._apps_install_missing_console,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT)

        tk.Button(
            actions, text="Refresh",
            command=self._apps_refresh,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT, cursor="hand2", padx=15, pady=6,
        ).pack(side=tk.LEFT, padx=(10, 0))


        # Scrollable body for the per-category app lists
        body = tk.Frame(parent, bg=COLORS["bg_primary"])
        body.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 10))

        canvas = tk.Canvas(body, bg=COLORS["bg_primary"], highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        self._apps_list_frame = tk.Frame(canvas, bg=COLORS["bg_primary"])

        self._apps_list_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        win_id_apps = canvas.create_window((0, 0), window=self._apps_list_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.bind("<Configure>", lambda e, wid=win_id_apps: canvas.itemconfig(wid, width=e.width))

        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        canvas.bind("<Enter>", lambda e: canvas.bind_all("<MouseWheel>", _on_mousewheel))
        canvas.bind("<Leave>", lambda e: canvas.unbind_all("<MouseWheel>"))

        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._apps_refresh()

    def _apps_refresh(self):
        """Re-read app status and rebuild the per-category rows.

        Invalidates the workstation_apps install-detection cache first
        so apps installed since the dialog opened (in a console window
        or via the per-row Install button) actually show up as
        installed instead of being remembered as missing."""
        if not hasattr(self, "_apps_list_frame"):
            return
        wa = self._wa
        if hasattr(wa, "invalidate_program_cache"):
            wa.invalidate_program_cache()
        for child in self._apps_list_frame.winfo_children():
            child.destroy()

        apps = wa.load_apps()
        if not apps:
            tk.Label(
                self._apps_list_frame,
                text="No workstation_apps configured in setup_config.json.",
                font=font.Font(family="Segoe UI", size=10, slant="italic"),
                fg=COLORS["text_secondary"], bg=COLORS["bg_primary"],
            ).pack(anchor="nw", pady=10)
            self._apps_summary_var.set("No apps configured")
            return

        counts = wa.status_counts(apps, set())
        self._apps_summary_var.set(
            f"{counts.installed}/{counts.total} installed, "
            f"{counts.missing} missing"
        )

        grouped = wa.apps_by_category(apps)
        for cat, cat_apps in grouped.items():
            section = tk.LabelFrame(
                self._apps_list_frame,
                text=f" {cat} ",
                font=font.Font(family="Segoe UI", size=10, weight="bold"),
                fg=COLORS["text_primary"], bg=COLORS["bg_card"],
                padx=10, pady=6,
            )
            section.pack(fill=tk.X, pady=(0, 8), anchor="nw")

            for a in cat_apps:
                self._build_app_row(section, a, wa)

    def _build_app_row(self, parent, app, wa):
        """One row per app: status icon, name, description, download button."""
        row = tk.Frame(parent, bg=COLORS["bg_card"])
        row.pack(fill=tk.X, pady=2)

        if wa.is_installed(app):
            icon, icon_color = "✓", "#22c55e"
            status_text = app.why
        else:
            icon, icon_color = "✗", "#ef4444"
            method = "winget" if app.install_method == "winget" else "manual"
            status_text = f"missing ({method}) — {app.why}"

        # Pack the download button first (side=RIGHT) so it is always
        # visible; the status label fills whatever space remains and wraps.
        if app.url:
            tk.Button(
                row, text="Download",
                command=lambda a=app: self._wa.open_download_page(a),
                bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
                font=font.Font(family="Segoe UI", size=9),
                relief=tk.FLAT, cursor="hand2", padx=8, pady=2,
            ).pack(side=tk.RIGHT, padx=(4, 0))

        tk.Label(
            row, text=icon,
            font=font.Font(family="Segoe UI", size=11, weight="bold"),
            fg=icon_color, bg=COLORS["bg_card"], width=2,
        ).pack(side=tk.LEFT)

        tk.Label(
            row, text=app.name,
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"],
            width=20, anchor="w",
        ).pack(side=tk.LEFT)

        status_label = tk.Label(
            row, text=status_text,
            font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"],
            anchor="w", justify="left",
        )
        status_label.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # Keep wraplength in sync with the label's actual width so text
        # wraps instead of being clipped when the dialog is narrow.
        def _update_wrap(e, lbl=status_label):
            w = e.width - 4
            if w > 10:
                lbl.config(wraplength=w)

        status_label.bind("<Configure>", _update_wrap)

    def _apps_install_one(self, app):
        """Install one app. winget = new console; manual = open URL."""
        wa = self._wa
        if app.install_method == "winget" and app.winget_id:
            if sys.platform != "win32" or not wa.winget_available():
                messagebox.showwarning(
                    "winget unavailable",
                    f"winget is not available on this machine.\n\n"
                    f"Install {app.name} manually from:\n{app.url}",
                    parent=self.dialog,
                )
                return
            try:
                # Open a new console so winget can show progress and the
                # user can confirm UAC prompts. cmd /k keeps the window
                # up after winget finishes so success/failure stays
                # visible.
                subprocess.Popen(
                    ["cmd", "/k", "winget", "install", "--id", app.winget_id,
                     "--accept-source-agreements",
                     "--accept-package-agreements"],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
                logger.info("Launched winget install for %s", app.name)
                messagebox.showinfo(
                    "Install Started",
                    f"winget is installing {app.name} in a new console.\n\n"
                    "Click Refresh after it finishes to update the status.",
                    parent=self.dialog,
                )
            except Exception as e:
                logger.exception("winget install failed to launch")
                messagebox.showerror(
                    "Launch Failed",
                    f"Could not start winget:\n{e}",
                    parent=self.dialog,
                )
            return

        # Manual install: open the vendor download page
        if not app.url:
            messagebox.showinfo(
                app.name,
                "No download URL configured for this app.",
                parent=self.dialog,
            )
            return
        if not wa.open_download_page(app):
            messagebox.showwarning(
                "Could not open browser",
                f"Open this URL manually:\n{app.url}",
                parent=self.dialog,
            )

    def _apps_install_missing_console(self):
        """Hand off to install.py --step apps for the full picker UI. Needs a
        real, visible, interactive terminal (the picker reads input()) - a
        plain background Popen on macOS/Linux would inherit no usable stdin
        and just hang, so each platform opens its own kind of console."""
        project_root = self._project_root()
        script_path = os.path.join(project_root, "install.py")
        if not os.path.isfile(script_path):
            messagebox.showerror(
                "install.py Not Found",
                f"Could not locate:\n{script_path}",
                parent=self.dialog,
            )
            return
        exe = self._console_python()
        try:
            if sys.platform == "win32":
                subprocess.Popen(
                    ["cmd", "/k", exe, script_path, "--step", "apps"],
                    cwd=project_root,
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                )
            elif sys.platform == "darwin":
                import shlex
                import tempfile
                script_body = (
                    "#!/bin/bash\n"
                    f"cd {shlex.quote(project_root)}\n"
                    f"{shlex.quote(exe)} {shlex.quote(script_path)} --step apps\n"
                    'echo\necho "Press Enter to close..."\nread\n'
                )
                fd, tmp_path = tempfile.mkstemp(suffix=".command")
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(script_body)
                os.chmod(tmp_path, 0o755)
                subprocess.Popen(["open", tmp_path])
            else:
                messagebox.showwarning(
                    "Not Supported",
                    "No interactive-console launcher for this platform yet.\n"
                    f"Run manually: {exe} {script_path} --step apps",
                    parent=self.dialog,
                )
                return
            logger.info("Launched install.py --step apps in new console")
        except Exception as e:
            logger.exception("Failed to launch install.py")
            messagebox.showerror(
                "Launch Failed",
                f"Could not start install.py:\n{e}",
                parent=self.dialog,
            )

    def _build_button_row(self):
        """Build the button row at the bottom of the dialog."""
        button_frame = tk.Frame(self.dialog, bg=COLORS["bg_primary"])
        button_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=20, pady=15)

        # Reset button (left side)
        reset_btn = tk.Button(
            button_frame,
            text="Reset Defaults",
            command=self._reset_defaults,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=8
        )
        reset_btn.pack(side=tk.LEFT)

        # Cancel button (right side)
        cancel_btn = tk.Button(
            button_frame,
            text="Cancel",
            command=self._cancel,
            bg=COLORS["bg_secondary"],
            fg=COLORS["text_primary"],
            font=font.Font(family="Segoe UI", size=10),
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=8
        )
        cancel_btn.pack(side=tk.RIGHT, padx=(10, 0))

        # Save button (right side)
        save_btn = self._save_btn = tk.Button(
            button_frame,
            text="Save",
            command=self._save,
            bg=COLORS["accent_dark"],
            fg="#ffffff",
            font=font.Font(family="Segoe UI", size=10, weight="bold"),
            relief=tk.FLAT,
            cursor="hand2",
            padx=20,
            pady=8
        )
        save_btn.pack(side=tk.RIGHT)

    def _validate_paths(self):
        """Validate current path entries and update status labels."""
        # Validate work drive
        work_drive = self.work_drive_var.get()
        work_valid, work_msg = self.settings.validate_drive(work_drive)

        if work_valid:
            self.work_status_label.config(text=f"OK {work_msg}", fg=COLORS["success"])
        else:
            self.work_status_label.config(text=f"! {work_msg}", fg=COLORS["warning"])

        # Validate active base
        active_base = self.active_base_var.get()
        active_valid, active_msg = self.settings.validate_drive(active_base)

        if active_valid:
            self.active_status_label.config(text=f"OK {active_msg}", fg=COLORS["success"])
        else:
            self.active_status_label.config(text=f"! {active_msg}", fg=COLORS["warning"])

        # Validate archive base
        archive_base = self.archive_base_var.get()
        archive_valid, archive_msg = self.settings.validate_drive(archive_base)

        if archive_valid:
            self.archive_status_label.config(text=f"OK {archive_msg}", fg=COLORS["success"])
        else:
            self.archive_status_label.config(text=f"! {archive_msg}", fg=COLORS["warning"])

        # Validate mapped software path
        mapped_sw = self.mapped_sw_var.get()
        sw_valid, sw_msg = self.settings.validate_drive(mapped_sw)

        if sw_valid:
            self.mapped_sw_status_label.config(text=f"OK {sw_msg}", fg=COLORS["success"])
        else:
            self.mapped_sw_status_label.config(text=f"! {sw_msg}", fg=COLORS["warning"])

        # Validate launchers path
        launchers = self.launchers_var.get()
        launch_valid, launch_msg = self.settings.validate_drive(launchers)

        if launch_valid:
            self.launchers_status_label.config(text=f"OK {launch_msg}", fg=COLORS["success"])
        else:
            self.launchers_status_label.config(text=f"! {launch_msg}", fg=COLORS["warning"])

        # Validate boekhouding base (blank = derived default, always OK)
        boek_explicit = self.boekhouding_base_var.get().strip()
        if not boek_explicit:
            self.boekhouding_status_label.config(
                text=f"(default: {self.settings.get_boekhouding_base()})",
                fg=COLORS["text_secondary"])
        else:
            boek_valid, boek_msg = self.settings.validate_drive(boek_explicit)
            if boek_valid:
                self.boekhouding_status_label.config(text=f"OK {boek_msg}", fg=COLORS["success"])
            else:
                self.boekhouding_status_label.config(text=f"! {boek_msg}", fg=COLORS["warning"])

        # Update category path labels
        self._update_category_paths()

    def _update_category_paths(self):
        """Update category path labels based on current drive settings."""
        work_drive = self.work_drive_var.get()
        archive_base = self.archive_base_var.get()

        for category, labels in self.category_labels.items():
            cat_config = self.settings.get_category_config(category)
            work_subpath = cat_config.get("work_subpath", category)
            archive_subpath = cat_config.get("archive_subpath", category)

            work_path = f"{work_drive}\\{work_subpath}".replace('\\', '/')
            archive_path = f"{archive_base}\\{archive_subpath}".replace('\\', '/')

            labels["work"].config(text=work_path)
            labels["archive"].config(text=archive_path)

    def _browse_archive(self):
        """Open folder browser for archive base."""
        current = self.archive_base_var.get()
        initial_dir = current if os.path.isdir(current) else None

        folder = filedialog.askdirectory(
            parent=self.dialog,
            title="Select Archive Base Directory",
            initialdir=initial_dir
        )

        if folder:
            # Normalize to Windows path format
            folder = folder.replace('/', '\\')
            self.archive_base_var.set(folder)
            self._validate_paths()

    def _browse_active(self):
        """Open folder browser for active base."""
        current = self.active_base_var.get()
        initial_dir = current if os.path.isdir(current) else None

        folder = filedialog.askdirectory(
            parent=self.dialog,
            title="Select Active Base Directory",
            initialdir=initial_dir
        )

        if folder:
            folder = folder.replace('/', '\\')
            self.active_base_var.set(folder)
            self._validate_paths()

    def _clear_boekhouding_base(self):
        """Clear the Boekhouding override so it falls back to the derived default."""
        self.boekhouding_base_var.set("")
        self._validate_paths()

    def _browse_to_var(self, var, title):
        """Open folder browser and set the result to a StringVar."""
        current = var.get()
        initial_dir = current if os.path.isdir(current) else None
        folder = filedialog.askdirectory(
            parent=self.dialog,
            title=title,
            initialdir=initial_dir
        )
        if folder:
            var.set(folder.replace('/', '\\'))

    def _reset_defaults(self):
        """Reset to default values."""
        if messagebox.askyesno(
            "Reset Defaults",
            "Reset all settings to default values?\n\n"
            "This will reset:\n"
            "- Active Drive: I:\n"
            "- Active Base: D:\\_work\\Active\n"
            "- Archive Base: D:\\_work\\Archive\n"
            "- All software version defaults",
            parent=self.dialog
        ):
            # Reset paths from DEFAULT_CONFIG
            defaults = self.settings.DEFAULT_CONFIG
            self.work_drive_var.set(defaults["drives"]["work"])
            self.active_base_var.set(defaults["drives"]["active_base"])
            self.archive_base_var.set(defaults["drives"]["archive_base"])
            self.mapped_sw_var.set(defaults["software_sync"]["mapped_software_path"])
            self.launchers_var.set(defaults["software_sync"]["launchers_base_path"])
            self.boekhouding_base_var.set(defaults.get("business", {}).get("boekhouding_base", ""))
            self._validate_paths()

            # Reset software defaults in UI
            if hasattr(self, 'software_entries'):
                defaults = self.settings.DEFAULT_CONFIG.get("software_defaults", {})
                for software, var in self.software_entries.items():
                    var.set(defaults.get(software, ""))

    def _save(self):
        """Save settings and close dialog."""
        work_drive = self.work_drive_var.get()
        active_base = self.active_base_var.get()
        archive_base = self.archive_base_var.get()

        # Validate before saving
        work_valid, _ = self.settings.validate_drive(work_drive)
        active_valid, _ = self.settings.validate_drive(active_base)
        archive_valid, _ = self.settings.validate_drive(archive_base)

        if not work_valid or not active_valid or not archive_valid:
            if not messagebox.askyesno(
                "Invalid Paths",
                "Some paths could not be validated.\n\n"
                "Save anyway? (You can fix this later)",
                parent=self.dialog
            ):
                return

        # Save path config
        self.settings.set_work_drive(work_drive)
        self.settings.set_active_base(active_base)
        self.settings.set_archive_base(archive_base)
        self.settings.set_mapped_software_path(self.mapped_sw_var.get())
        self.settings.set_launchers_base_path(self.launchers_var.get())
        self.settings.set_boekhouding_base(self.boekhouding_base_var.get().strip())

        # Save software defaults
        if hasattr(self, 'software_entries'):
            versions = {software: var.get() for software, var in self.software_entries.items()}
            self.settings.set_software_defaults(**versions)

        # Save general settings
        self.settings.set_start_fullscreen(self.start_fullscreen_var.get())
        self.settings.set_always_on_bottom(self.always_on_bottom_var.get())

        # Sync drive_mappings + wait_for_drives into the startup-apps
        # config from the Drives tab's current mappings. The launcher
        # uses drive_mappings to run `subst` itself at logon — HKCU\Run
        # subst entries on cold boot regularly take 30+ seconds (or
        # never fire within the wait window). wait_for_drives stays as
        # a belt-and-suspenders verification. The Drives tab is the
        # single knob; no separate UI for either.
        if hasattr(self, "_drives_cfg") and hasattr(self, "_startup_cfg"):
            letters = []
            seen = set()
            drive_mappings = []
            for m in self._drives_cfg.get("drive_mappings", []) or []:
                raw = (m.get("drive_letter") or "").strip().upper()
                raw = raw.rstrip("\\").rstrip(":")
                if raw and raw[0].isalpha():
                    norm = f"{raw[0]}:"
                    if norm not in seen:
                        seen.add(norm)
                        letters.append(norm)
                    target = (m.get("target_path") or "").strip().replace("/", "\\")
                    if target:
                        drive_mappings.append({"letter": norm, "target": target})
            self._startup_cfg["wait_for_drives"] = letters
            self._startup_cfg["drive_mappings"] = drive_mappings

        # Save startup apps sidecar (if the tab was built — guard so the
        # save doesn't fail when the module wasn't importable).
        if hasattr(self, "_sam") and hasattr(self, "_startup_cfg"):
            try:
                self._startup_cfg["enabled"] = bool(self._startup_enabled_var.get())
                self._sam.save_config(self._startup_cfg)
            except OSError as e:
                messagebox.showerror(
                    "Startup apps save failed",
                    f"Could not write startup_apps.json:\n{e}",
                    parent=self.dialog,
                )
                return

        # Save drive mappings to setup_config.json (only if the tab was
        # built — same guard pattern as startup apps above).
        if hasattr(self, "_drives_cfg") and hasattr(self, "_drives_config_path"):
            try:
                self._drives_save_config()
            except OSError as e:
                messagebox.showerror(
                    "Drive mappings save failed",
                    f"Could not write setup_config.json:\n{e}",
                    parent=self.dialog,
                )
                return

        self._persist_geometry()
        self.result = True
        self._flash_saved()
        logger.info("Settings saved")

    def _flash_saved(self):
        """Save no longer closes the dialog (it stays open until the
        caller's Settings toggle or Cancel/X closes it), so relabel the
        button briefly as the only feedback that the click landed."""
        btn = self._save_btn
        btn.config(text="Saved", bg=COLORS["success"])
        btn.after(900, lambda: btn.winfo_exists() and btn.config(text="Save", bg=COLORS["accent_dark"]))

    def _cancel(self):
        """Close the dialog without saving pending edits. Does not clear
        self.result — an earlier Save this session should still be
        reported to the caller even if this later close is a Cancel/X."""
        self._persist_geometry()
        self.dialog.destroy()

    def _persist_geometry(self):
        """Save the dialog's current WxH+X+Y so the next open restores
        it. Swallows errors so a missing geometry never blocks close."""
        try:
            geo = self.dialog.geometry()  # "WxH+X+Y"
            if geo:
                self.settings.set_settings_dialog_geometry(geo)
        except (tk.TclError, OSError) as e:
            logger.debug("Could not persist settings dialog geometry: %s", e)

    def show(self) -> bool:
        """
        Show the dialog and wait for it to close.

        Returns:
            True if settings were saved, False if cancelled
        """
        self.dialog.wait_window()
        return self.result
