"""
Photo extension for GenericFolderStructureCreator.

Photo projects are named by date/location/activity rather than client/project,
so this extension replaces the primary input row entirely and provides a
custom folder-name builder. There are no software chips and no specs file.
"""

import os
import re
import shutil
import tkinter as tk
from tkinter import messagebox, ttk

from shared_form_keyboard import (
    create_styled_checkbox, create_styled_entry, create_styled_label,
)
from . import FolderStructureExtension

DEFAULT_SD_PATH = r"G:\DCIM"


def _unique_path(dest_dir, name):
    """Return a path in dest_dir for name, suffixing _N if it already exists."""
    stem, ext = os.path.splitext(name)
    path = os.path.join(dest_dir, name)
    n = 1
    while os.path.exists(path):
        path = os.path.join(dest_dir, f"{stem}_{n}{ext}")
        n += 1
    return path


def _copy_with_progress(parent, source, dest_dir):
    """Copy every file under source (flattened) into dest_dir, showing progress."""
    files = [
        os.path.join(root, f)
        for root, _, names in os.walk(source)
        for f in names
    ]
    if not files:
        messagebox.showinfo("Import", f"No files found in {source}")
        return

    win = tk.Toplevel(parent)
    win.title("Importing from card")
    win.transient(parent)
    label = tk.Label(win, text="", width=50, anchor="w")
    label.pack(padx=15, pady=(15, 5))
    bar = ttk.Progressbar(win, maximum=len(files), length=380)
    bar.pack(padx=15, pady=(0, 15))
    win.grab_set()

    failed = []
    for i, src in enumerate(files, 1):
        name = os.path.basename(src)
        label.config(text=f"{i}/{len(files)}  {name}")
        bar["value"] = i
        win.update()
        try:
            shutil.copy2(src, _unique_path(dest_dir, name))
        except OSError as e:
            failed.append(f"{name}: {e}")
    win.destroy()

    if failed:
        messagebox.showwarning(
            "Import finished with errors",
            f"Copied {len(files) - len(failed)}/{len(files)} files.\n\n" + "\n".join(failed[:10]),
        )


def _sanitize(name):
    """Strip path-illegal characters and collapse whitespace."""
    cleaned = re.sub(r'[<>:"/\\|?*]', '', name)
    cleaned = re.sub(r'[^\w\s\-_.,&()]+', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    return cleaned.strip()


class PhotoExtension(FolderStructureExtension):
    def __init__(self, creator):
        super().__init__(creator)
        self.location_var = None
        self.activity_var = None

    def build_main_inputs(self, parent_frame):
        parent_frame.columnconfigure(1, weight=1)
        parent_frame.columnconfigure(3, weight=1)
        parent_frame.columnconfigure(5, weight=1)

        create_styled_label(parent_frame, "Date:").grid(row=0, column=0, sticky="e", padx=(0, 5))
        date_entry = create_styled_entry(parent_frame, textvariable=self.creator.date_var, width=12)
        date_entry.grid(row=0, column=1, sticky="w", padx=(0, 20))

        create_styled_label(parent_frame, "Location:").grid(row=0, column=2, sticky="e", padx=(0, 5))
        self.location_var = tk.StringVar()
        location_entry = create_styled_entry(parent_frame, textvariable=self.location_var, width=20)
        location_entry.grid(row=0, column=3, sticky="ew", padx=(0, 20))

        create_styled_label(parent_frame, "Activity/People:").grid(row=0, column=4, sticky="e", padx=(0, 5))
        self.activity_var = tk.StringVar()
        activity_entry = create_styled_entry(parent_frame, textvariable=self.activity_var, width=20)
        activity_entry.grid(row=0, column=5, sticky="ew")

        for var in (self.location_var, self.activity_var):
            var.trace_add("write", lambda *a: self.creator.update_preview())

        # Photo doesn't have a project_name in the standard sense; use activity
        # so DB writes still get a useful project name. We synthesize via project
        # data overrides rather than sharing a StringVar.
        return [date_entry, location_entry, activity_entry]

    def build_extra_widgets(self, parent_frame):
        self.import_var = tk.BooleanVar(value=os.path.isdir(DEFAULT_SD_PATH))
        self.import_check = create_styled_checkbox(
            parent_frame, text="Import from card:", variable=self.import_var,
        )
        self.import_check.pack(side=tk.LEFT, padx=(0, 5))
        self.import_check.bind(
            "<Return>", lambda e: self.import_var.set(not self.import_var.get())
        )

        self.import_path_var = tk.StringVar(value=DEFAULT_SD_PATH)
        import_entry = create_styled_entry(parent_frame, textvariable=self.import_path_var, width=14)
        import_entry.pack(side=tk.LEFT)
        return [self.import_check, import_entry]

    def after_structure_created(self, project_dir):
        if not self.import_var.get():
            return
        source = self.import_path_var.get().strip()
        if not os.path.isdir(source):
            messagebox.showwarning("Import skipped", f"Source folder not found:\n{source}")
            return
        _copy_with_progress(self.creator.root, source, project_dir)

    def build_folder_name(self):
        date = _sanitize(self.creator.date_var.get())
        location = _sanitize(self.location_var.get() if self.location_var else "")
        activity = _sanitize(self.activity_var.get() if self.activity_var else "")
        return f"{date}_{location}_{activity}"

    def validate_inputs(self):
        if not self.creator.date_var.get().strip():
            return "Please enter a date."
        if not self.location_var or not self.location_var.get().strip():
            return "Please enter a location."
        if not self.activity_var or not self.activity_var.get().strip():
            return "Please enter activity & people information."
        return None

    def build_metadata(self):
        return {
            "location": self.location_var.get().strip() if self.location_var else "",
            "activity": self.activity_var.get().strip() if self.activity_var else "",
        }

    def build_project_data_overrides(self):
        is_personal = self.creator.personal_var is not None and self.creator.personal_var.get()
        return {
            "client_name": "Personal" if is_personal else "Photo",
            "project_name": self.activity_var.get().strip() if self.activity_var else "",
        }
