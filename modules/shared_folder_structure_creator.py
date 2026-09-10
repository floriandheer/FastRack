"""
Generic Folder Structure Creator

Builds a standard keyboard-first form for project folder creation, driven by a
manifest entry from pipeline_categories.subtype_manifest(). Per-subtype divergence
is expressed either through manifest fields (software chips, extra combobox/
entry rows, naming prefix) or through a FolderStructureExtension subclass.

This module replaces ~3000 lines of near-duplicate code across the
PipelineScript_*_FolderStructure*.py creators.
"""

import os
import sys
import importlib
import subprocess
import tkinter as tk
from tkinter import filedialog, messagebox
from datetime import datetime
from pathlib import Path

MODULES_DIR = Path(__file__).parent
sys.path.insert(0, str(MODULES_DIR))

from shared_logging import get_logger
from shared_project_db import ProjectDatabase
from shared_autocomplete_widget import AutocompleteEntry
# Best-effort import: the contacts table only exists if the user has
# the Invoice Manager / Contacts app set up. If anything is missing
# we fall back to a no-op resolver and the typed name passes through
# unchanged.
try:
    from invoice_manager.contacts_resolver import resolve_client_folder_name
except Exception:
    def resolve_client_folder_name(typed_value):  # type: ignore[misc]
        return typed_value
from rack_settings import get_rack_settings
from shared_form_keyboard import (
    FormKeyboardMixin, FORM_COLORS,
    create_styled_entry, create_styled_text, create_styled_button,
    create_styled_label, create_styled_checkbox, create_styled_frame,
    create_styled_labelframe, create_styled_combobox,
    format_button_with_shortcut,
    create_software_chip_row, get_active_software,
    add_name_validation,
)
from shared_folder_tree_parser import parse_tree_file, create_structure as tree_create_structure

logger = get_logger(__name__)

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")


def _load_extension(dotted: str, creator):
    """Resolve a ``module:Class`` extension reference and instantiate it."""
    module_name, class_name = dotted.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, class_name)(creator)


def upsert_order_number_in_specs(project_dir: str, order_number: str) -> None:
    """Add or update an "Order #: <n>" line in a project's
    _LIBRARY/Project_Details.txt, creating the file if it's missing (older
    pre-automation projects may not have one yet).

    Metadata-only: touches this one text file and nothing else — no other
    file or folder in the project is created, moved, or modified.
    """
    docs_dir = Path(project_dir, '_LIBRARY')
    spec_path = docs_dir / 'Project_Details.txt'

    if spec_path.exists():
        lines = spec_path.read_text(encoding='utf-8').splitlines()
    else:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = ["PROJECT DETAILS", "======================", f"Generated: {timestamp}", ""]

    lines = [l for l in lines if not l.startswith("Order #:")]
    # Insert right after the "Project:" line if present, else append.
    insert_at = next((i + 1 for i, l in enumerate(lines) if l.startswith("Project:")), len(lines))
    lines.insert(insert_at, f"Order #: {order_number}")

    docs_dir.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("\n".join(lines) + "\n", encoding='utf-8')
    logger.info(f"Recorded order #{order_number} in {spec_path}")


class GenericFolderStructureCreator(FormKeyboardMixin):
    """Manifest-driven folder structure creator.

    The standard form is: client + project | personal/sandbox + date + software
    chips + manifest-defined extra fields | notes + preview | browse + create.

    Extensions may replace the primary input row, add widgets, override naming,
    add metadata, etc. — see folder_structure_extensions.FolderStructureExtension.
    """

    def __init__(self, root_or_frame, manifest, embedded=False,
                 on_project_created=None, on_cancel=None, project_db=None):
        self.manifest = manifest
        self.embedded = embedded
        self.on_project_created = on_project_created
        self.on_cancel = on_cancel
        self._in_text_field = False

        if embedded:
            self.root = root_or_frame.winfo_toplevel()
            self.parent = root_or_frame
        else:
            self.root = root_or_frame
            self.parent = root_or_frame
            self.root.title(manifest["title"])
            self.root.geometry("900x550")
            self.root.minsize(800, 450)

        self.settings = get_rack_settings()

        if project_db is not None:
            self.project_db = project_db
        else:
            try:
                self.project_db = ProjectDatabase()
            except Exception as e:
                logger.error(f"Failed to initialize database: {e}")
                self.project_db = None

        self.extension = None
        if manifest.get("extension"):
            self.extension = _load_extension(manifest["extension"], self)

        # State containers populated by _build_form
        self.client_name_var = None
        self.project_name_var = None
        self.client_entry = None
        self.project_entry = None
        self.personal_var = None
        self.sandbox_var = None
        self.personal_check = None
        self.sandbox_check = None
        self.date_var = tk.StringVar(value=datetime.now().strftime('%Y-%m-%d'))
        self.date_entry = None
        self.software_chips = {}
        self.software_chip_frame = None
        self.extra_field_vars = {}  # name -> tk.StringVar
        self._extension_focusables = []
        self.notes_text = None
        self.preview_text = None
        self.create_btn = None
        self.browse_btn = None
        self.tree_file = os.path.join(TEMPLATES_DIR, manifest["tree_template"])
        self.base_dir_var = tk.StringVar(
            value=self.settings.get_work_path(manifest["work_path_category"]).replace('\\', '/')
        )
        self.status_var = tk.StringVar(value="Ready")

        self._build_form()
        self._collect_focusable_widgets()
        self._setup_keyboard_navigation()
        if self.extension:
            self.extension.setup_extra_keyboard_shortcuts()

    # ==================== form construction ====================

    def _build_form(self):
        if self.embedded:
            self.parent.configure(bg=FORM_COLORS["bg"])
        else:
            self.root.configure(bg=FORM_COLORS["bg"])
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)

        if self.embedded:
            main_frame = create_styled_frame(self.parent)
            main_frame.pack(fill=tk.BOTH, expand=True, padx=15, pady=15)
        else:
            main_frame = create_styled_frame(self.root)
            main_frame.grid(row=0, column=0, sticky="nsew", padx=15, pady=15)

        main_frame.columnconfigure(0, weight=1)
        main_frame.rowconfigure(2, weight=1)

        self._build_row1(main_frame)
        self._build_row2(main_frame)
        self._build_row3(main_frame)
        self._build_row4(main_frame)

        self.update_preview()
        self._wire_preview_traces()

    def _build_row1(self, main_frame):
        row1 = create_styled_frame(main_frame)
        row1.grid(row=0, column=0, sticky="ew", pady=(0, 10))

        if self.extension:
            replaced = self.extension.build_main_inputs(row1)
            if replaced is not None:
                self._row1_focusables = replaced
                return

        row1.columnconfigure(1, weight=1)
        row1.columnconfigure(3, weight=1)

        create_styled_label(row1, "Client:").grid(row=0, column=0, sticky="e", padx=(0, 5))
        self.client_name_var = tk.StringVar()
        add_name_validation(self.client_name_var)
        autocomplete_cat = self.manifest.get("autocomplete_category")
        if self.project_db and autocomplete_cat:
            self.client_entry = AutocompleteEntry(
                row1, db=self.project_db, category=autocomplete_cat,
                textvariable=self.client_name_var, width=25,
                bg=FORM_COLORS["bg"],
            )
        else:
            self.client_entry = create_styled_entry(row1, textvariable=self.client_name_var, width=25)
        self.client_entry.grid(row=0, column=1, sticky="ew", padx=(0, 20))

        create_styled_label(row1, "Project:").grid(row=0, column=2, sticky="e", padx=(0, 5))
        self.project_name_var = tk.StringVar()
        add_name_validation(self.project_name_var)
        self.project_entry = create_styled_entry(row1, textvariable=self.project_name_var, width=25)
        self.project_entry.grid(row=0, column=3, sticky="ew")

        self._row1_focusables = [self.client_entry, self.project_entry]

    def _build_row2(self, main_frame):
        row2 = create_styled_frame(main_frame)
        row2.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        focusables = []

        if self.manifest.get("supports_personal"):
            self.personal_var = tk.BooleanVar(value=False)
            self.personal_check = create_styled_checkbox(
                row2, text="Personal (P)", variable=self.personal_var,
                command=self._on_personal_toggle,
            )
            self.personal_check.pack(side=tk.LEFT, padx=(0, 20))
            self.personal_check.bind("<Return>", lambda e: self._toggle_var(self.personal_var, self._on_personal_toggle))
            focusables.append(self.personal_check)

        if self.manifest.get("supports_sandbox"):
            self.sandbox_var = tk.BooleanVar(value=False)
            self.sandbox_check = create_styled_checkbox(
                row2, text="Sandbox (S)", variable=self.sandbox_var,
                command=self.update_preview,
            )
            self.sandbox_check.pack(side=tk.LEFT, padx=(0, 20))
            self.sandbox_check.bind("<Return>", lambda e: self._toggle_var(self.sandbox_var))
            focusables.append(self.sandbox_check)

        create_styled_label(row2, "Date:").pack(side=tk.LEFT, padx=(0, 5))
        self.date_entry = create_styled_entry(row2, textvariable=self.date_var, width=12)
        self.date_entry.pack(side=tk.LEFT, padx=(0, 20))
        focusables.append(self.date_entry)

        sw_chip_specs = self.manifest.get("software_chips", [])
        if sw_chip_specs:
            sw_args = self.manifest.get("software_defaults_args") or ()
            sw_defaults = self.settings.get_software_defaults(*sw_args) if sw_args else {}
            chip_names = [name for name, _, _ in sw_chip_specs]
            chip_defaults = {
                name: sw_defaults.get(key, fallback)
                for name, key, fallback in sw_chip_specs
            }
            self.software_chip_frame, self.software_chips = create_software_chip_row(
                row2, chip_names, defaults=chip_defaults,
                on_change=lambda *args: self.update_preview(),
            )
            self.software_chip_frame.pack(side=tk.LEFT, padx=(0, 20))

        for spec in self.manifest.get("extra_fields", []):
            self._build_extra_field(row2, spec)

        if self.extension:
            extra = self.extension.build_extra_widgets(row2)
            if extra:
                self._extension_focusables.extend(extra)

        self._row2_focusables = focusables

    def _build_extra_field(self, parent, spec):
        sw_args = self.manifest.get("software_defaults_args") or ()
        sw_defaults = self.settings.get_software_defaults(*sw_args) if sw_args else {}

        create_styled_label(parent, f"{spec['label']}:").pack(side=tk.LEFT, padx=(0, 5))
        var = tk.StringVar(value=sw_defaults.get(spec["default_key"], spec.get("default_fallback", "")))
        self.extra_field_vars[spec["name"]] = var

        kind = spec.get("kind", "combobox")
        if kind == "combobox":
            widget = create_styled_combobox(
                parent, textvariable=var, width=spec.get("width", 12),
                values=spec.get("choices", []),
            )
        else:
            widget = create_styled_entry(parent, textvariable=var, width=spec.get("width", 15))
        widget.pack(side=tk.LEFT, padx=(0, 20))
        var.trace_add("write", lambda *a: self.update_preview())

    def _build_row3(self, main_frame):
        row3 = create_styled_frame(main_frame)
        row3.grid(row=2, column=0, sticky="nsew", pady=(0, 10))
        row3.columnconfigure(0, weight=1)
        row3.columnconfigure(1, weight=2)
        row3.rowconfigure(0, weight=1)

        notes_frame = create_styled_labelframe(row3, text="Notes (optional)")
        notes_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        notes_frame.columnconfigure(0, weight=1)
        notes_frame.rowconfigure(0, weight=1)

        self.notes_text = create_styled_text(notes_frame, height=6)
        self.notes_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)
        self.notes_text.bind("<FocusIn>", lambda e: setattr(self, '_in_text_field', True))
        self.notes_text.bind("<FocusOut>", lambda e: setattr(self, '_in_text_field', False))

        preview_frame = create_styled_labelframe(row3, text="Preview")
        preview_frame.grid(row=0, column=1, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self.preview_text = tk.Text(
            preview_frame, bg=FORM_COLORS["bg_input"], fg=FORM_COLORS["text_dim"],
            font=("Consolas", 9), wrap=tk.WORD, state=tk.DISABLED,
            highlightthickness=0, relief=tk.FLAT,
        )
        self.preview_text.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

    def _build_row4(self, main_frame):
        row4 = create_styled_frame(main_frame)
        row4.grid(row=3, column=0, sticky="e", pady=(10, 0))

        self.browse_btn = create_styled_button(row4, text="Browse...", command=self.browse_base_dir)
        self.browse_btn.pack(side=tk.LEFT, padx=(0, 10))

        self.create_btn = create_styled_button(
            row4, text=format_button_with_shortcut("Create Project", "create"),
            command=self.create_structure, primary=True,
        )
        self.create_btn.pack(side=tk.LEFT)

    def _wire_preview_traces(self):
        for var in (self.client_name_var, self.project_name_var, self.date_var, self.base_dir_var):
            if var is not None:
                var.trace_add("write", lambda *a: self.update_preview())
        if self.personal_var is not None:
            self.personal_var.trace_add("write", lambda *a: self.update_preview())
        if self.sandbox_var is not None:
            self.sandbox_var.trace_add("write", lambda *a: self.update_preview())

    def _collect_focusable_widgets(self):
        widgets = list(getattr(self, "_row1_focusables", []))
        widgets.extend(getattr(self, "_row2_focusables", []))
        widgets.extend(self._extension_focusables)
        widgets.append(self.notes_text)
        self._focusable_widgets = widgets
        self._create_btn = self.create_btn
        self._browse_btn = self.browse_btn
        self._notes_widget = self.notes_text
        self._personal_checkbox = self.personal_check
        self._personal_var = self.personal_var

    def _setup_keyboard_navigation(self):
        super()._setup_keyboard_navigation()
        if self.manifest.get("supports_sandbox"):
            root = self.parent.winfo_toplevel()
            root.bind("<s>", self._on_s_key)
            root.bind("<S>", self._on_s_key)

    def _on_s_key(self, event):
        if self._in_text_field:
            return
        if self.sandbox_var is not None:
            self.sandbox_var.set(not self.sandbox_var.get())
        return "break"

    def _toggle_var(self, var, after_callback=None):
        var.set(not var.get())
        if after_callback:
            after_callback()
        return "break"

    # ==================== runtime behavior ====================

    def _on_personal_toggle(self):
        """Standard Personal-checkbox handler: auto-fills client name."""
        if self.client_name_var is None:
            self.update_preview()
            return
        if self.personal_var.get():
            self._client_name_backup = self.client_name_var.get()
            self.client_name_var.set("Personal")
        else:
            self.client_name_var.set(getattr(self, "_client_name_backup", ""))
        self.update_preview()

    def browse_base_dir(self):
        directory = filedialog.askdirectory()
        if directory:
            self.base_dir_var.set(directory)
            self.update_preview()

    # ---- naming / routing ----

    def _client_value(self):
        """Raw, user-typed string. Use _resolved_client_value() when the
        result will hit the filesystem or the project DB — that variant
        substitutes the contact's abbreviation when one is registered."""
        return self.client_name_var.get() if self.client_name_var else ""

    def _resolved_client_value(self):
        """``_client_value`` rewritten to the contact's folder-safe
        abbreviation when the typed name matches a known contact (either
        by display_name or by the abbreviation itself, case-insensitive).
        """
        return resolve_client_folder_name(self._client_value())

    def _project_value(self):
        return self.project_name_var.get() if self.project_name_var else ""

    def build_folder_name(self):
        if self.extension:
            override = self.extension.build_folder_name()
            if override is not None:
                return override
        date = self.date_var.get() or datetime.now().strftime('%Y-%m-%d')
        client = self._resolved_client_value()
        project = self._project_value()
        prefix = self.manifest.get("folder_prefix")
        parts = [date]
        if prefix:
            parts.append(prefix)
        if client and client != "Personal":
            parts.extend([client, project])
        else:
            parts.append(project)
        return "_".join(p for p in parts if p)

    def get_target_directory(self):
        base_dir = self.base_dir_var.get()
        if self.extension:
            override = self.extension.get_target_directory(base_dir)
            if override is not None:
                return override
        if self.personal_var is not None and self.personal_var.get():
            return os.path.join(base_dir, "_Personal")
        if self.sandbox_var is not None and self.sandbox_var.get():
            return os.path.join(base_dir, "_Sandbox")
        return base_dir

    def _replacements(self):
        date = self.date_var.get() or datetime.now().strftime('%Y-%m-%d')
        repls = {'YYY-MM-DD': date}
        if self.extension:
            repls.update(self.extension.get_replacements())
        return repls

    def _conditionals(self):
        return self.extension.get_conditionals() if self.extension else {}

    # ---- preview ----

    def update_preview(self):
        if self.preview_text is None:
            return
        self.preview_text.configure(state=tk.NORMAL)
        self.preview_text.delete(1.0, tk.END)

        try:
            folder_name = self.build_folder_name() or "[folder]"
        except Exception:
            folder_name = "[folder]"
        target_dir = self.get_target_directory()
        full_path = os.path.join(target_dir, folder_name).replace('\\', '/')

        self.preview_text.insert(tk.END, f"Path: {full_path}\n\n")

        if self.software_chips:
            active = get_active_software(self.software_chips)
            if active:
                line = "  |  ".join(f"{n}: {v}" for n, v in active.items())
                self.preview_text.insert(tk.END, f"{line}\n\n")

        if self.extra_field_vars:
            extras = "  |  ".join(f"{name}: {var.get()}" for name, var in self.extra_field_vars.items() if var.get())
            if extras:
                self.preview_text.insert(tk.END, f"{extras}\n\n")

        if os.path.isfile(self.tree_file):
            tree_entries = parse_tree_file(self.tree_file)
            conditionals = self._conditionals()
            replacements = self._replacements()
            self.preview_text.insert(tk.END, "Structure:\n")
            skipped = []
            for path, cond in tree_entries:
                if any(path.startswith(p + '/') for p in skipped):
                    continue
                if cond and not conditionals.get(cond, True):
                    skipped.append(path)
                    continue
                rendered = path
                for k, v in replacements.items():
                    rendered = rendered.replace(k, v)
                depth = rendered.count('/')
                name = rendered.split('/')[-1]
                self.preview_text.insert(tk.END, f"{'  ' * depth}{name}/\n")
        else:
            self.preview_text.insert(tk.END, "Tree structure file not found.\n")

        self.preview_text.configure(state=tk.DISABLED)

    # ---- create ----

    def _validate(self):
        if self.extension:
            err = self.extension.validate_inputs()
            if err:
                messagebox.showerror("Error", err)
                return False
            # When extension replaces main inputs, defer entirely to it.
            if self.client_name_var is None or self.project_name_var is None:
                return True

        base_dir = self.base_dir_var.get()
        if not base_dir or not os.path.isdir(base_dir):
            try:
                os.makedirs(base_dir, exist_ok=True)
            except Exception:
                messagebox.showerror("Error", "Please select a valid base directory.")
                return False

        if not self._project_value():
            messagebox.showerror("Error", "Please enter a project name.")
            return False

        if not self._client_value():
            if self.personal_var is not None and self.personal_var.get():
                self.client_name_var.set("Personal")
            else:
                messagebox.showerror("Error", "Please enter a client name.")
                return False

        if not self.date_var.get():
            self.date_var.set(datetime.now().strftime('%Y-%m-%d'))

        if not os.path.isfile(self.tree_file):
            messagebox.showerror("Error", "Tree structure file not found.")
            return False
        return True

    def create_structure(self):
        if not self._validate():
            return

        target_dir = self.get_target_directory()
        try:
            os.makedirs(target_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create base directory: {e}")
            return

        folder_name = self.build_folder_name()
        project_dir = os.path.join(target_dir, folder_name)

        try:
            os.makedirs(project_dir, exist_ok=True)
            tree = parse_tree_file(self.tree_file)
            tree_create_structure(project_dir, tree, self._replacements(), self._conditionals())

            software_specs = get_active_software(self.software_chips) if self.software_chips else {}

            # Use the same abbreviation-resolved value that ends up in
            # the folder name so the project DB and the on-disk folder
            # stay in sync (and so future projects for the same contact
            # group together correctly under one key).
            client_name = self._resolved_client_value() or "Personal"
            project_name = self._project_value()

            metadata = {}
            if software_specs:
                metadata["software_specs"] = software_specs
            if self.personal_var is not None:
                metadata["is_personal"] = self.personal_var.get()
            if self.sandbox_var is not None:
                metadata["is_sandbox"] = self.sandbox_var.get()
            for name, var in self.extra_field_vars.items():
                # Use the manifest-declared metadata_key if present, else the field name
                meta_key = self._extra_field_metadata_key(name) or name
                metadata[meta_key] = var.get()
            if self.extension:
                metadata.update(self.extension.build_metadata())

            project_data = {
                'client_name': client_name,
                'project_name': project_name,
                'project_type': self.manifest["project_type"],
                'date_created': self.date_var.get(),
                'path': project_dir,
                'base_directory': target_dir,
                'status': 'active',
                'notes': self.notes_text.get(1.0, tk.END).strip(),
                'metadata': metadata,
            }
            if self.extension:
                project_data.update(self.extension.build_project_data_overrides())

            if self.manifest.get("creates_specs_file"):
                self._write_specs_file(project_dir, project_data, software_specs)

            self.status_var.set(f"Created project structure for {project_name}")

            if self.embedded and self.on_project_created:
                self.on_project_created(project_data)
            else:
                if messagebox.askyesno("Success", f"Project created at:\n\n{project_dir}\n\nOpen folder?"):
                    self.open_folder(project_dir)
        except Exception as e:
            logger.exception("create_structure failed")
            messagebox.showerror("Error", f"Failed to create structure: {e}")
            self.status_var.set("Error creating project structure")

    def _extra_field_metadata_key(self, name):
        for spec in self.manifest.get("extra_fields", []):
            if spec["name"] == name:
                return spec.get("metadata_key")
        return None

    def _write_specs_file(self, project_dir, project_data, software_specs):
        try:
            docs_dir = os.path.join(project_dir, '_LIBRARY')
            os.makedirs(docs_dir, exist_ok=True)
            spec_path = os.path.join(docs_dir, 'Project_Details.txt')

            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            notes = project_data.get('notes') or "No notes provided."

            parts = [
                "PROJECT DETAILS",
                "======================",
                f"Generated: {timestamp}",
                "",
                f"Project: {project_data['project_name']}",
                f"Client: {project_data['client_name']}",
                f"Date: {project_data['date_created']}",
                "",
            ]
            if software_specs:
                parts.extend(["SOFTWARE VERSIONS", "======================"])
                parts.extend(f"{n}: {v}" for n, v in software_specs.items())
                parts.append("")
            for name, var in self.extra_field_vars.items():
                value = var.get()
                if value:
                    parts.append(f"{name.title()}: {value}")
            if self.extra_field_vars:
                parts.append("")
            if self.extension:
                self.extension.write_specs_extras(parts)
            parts.extend(["NOTES", "======================", notes, ""])

            with open(spec_path, 'w', encoding='utf-8') as f:
                f.write("\n".join(parts))
        except Exception as e:
            messagebox.showwarning("Warning", f"Failed to create project details file: {e}")

    def _handle_cancel(self):
        if self.on_cancel:
            self.on_cancel()

    def open_folder(self, path):
        if not os.path.exists(path):
            return
        try:
            if os.name == 'nt':
                os.startfile(path)
            elif os.name == 'posix':
                if os.uname().sysname == 'Darwin':
                    subprocess.call(['open', path])
                else:
                    subprocess.call(['xdg-open', path])
        except Exception as e:
            logger.warning(f"Could not open folder: {e}")
