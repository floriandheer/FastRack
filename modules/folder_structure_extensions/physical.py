"""
Physical (3D printing) extension for GenericFolderStructureCreator.

Replaces the standard Personal/Sandbox checkboxes with a 4-way mutually
exclusive radio (Personal / Order / Product / Project) and adds slicer +
printer combos plus optional preproduction/_LIBRARY folder toggles. Folder
name omits the client when the project is Personal or Product.

Shortcut letters: P=Personal (pipeline-wide), O=Order, D=proDuct, J=Project.
"""

import os
import tkinter as tk

from shared_form_keyboard import (
    create_styled_label, create_styled_checkbox, create_styled_combobox,
    create_styled_frame,
)
from . import FolderStructureExtension


class PhysicalExtension(FolderStructureExtension):
    def __init__(self, creator):
        super().__init__(creator)
        self.personal_var = None
        self.order_var = None
        self.product_var = None
        self.project_var = None
        self.personal_check = None
        self.order_check = None
        self.product_check = None
        self.project_check = None
        self.kind_var = None
        self.slicer_var = None
        self.printer_var = None
        self.preproduction_var = None
        self.library_var = None
        self._client_backup = ""
        self._base_dir_backup = ""

    def build_extra_widgets(self, parent_frame):
        c = self.creator
        sw_args = c.manifest.get("software_defaults_args") or ()
        sw_defaults = c.settings.get_software_defaults(*sw_args) if sw_args else {}

        type_frame = create_styled_frame(parent_frame)
        type_frame.pack(side=tk.LEFT, padx=(0, 20))

        self.personal_var = tk.BooleanVar(value=False)
        self.personal_check = create_styled_checkbox(
            type_frame, text="Personal (P)", variable=self.personal_var,
            command=lambda: self._toggle_type('personal'),
        )
        self.personal_check.pack(side=tk.LEFT, padx=(0, 10))

        self.order_var = tk.BooleanVar(value=False)
        self.order_check = create_styled_checkbox(
            type_frame, text="Order (O)", variable=self.order_var,
            command=lambda: self._toggle_type('order'),
        )
        self.order_check.pack(side=tk.LEFT, padx=(0, 10))

        self.product_var = tk.BooleanVar(value=False)
        self.product_check = create_styled_checkbox(
            type_frame, text="Product (D)", variable=self.product_var,
            command=lambda: self._toggle_type('product'),
        )
        self.product_check.pack(side=tk.LEFT, padx=(0, 10))

        self.project_var = tk.BooleanVar(value=False)
        self.project_check = create_styled_checkbox(
            type_frame, text="Project (J)", variable=self.project_var,
            command=lambda: self._toggle_type('project'),
        )
        self.project_check.pack(side=tk.LEFT)

        # Kind of physical project: the tag in the folder name and on the card
        create_styled_label(parent_frame, "Kind:").pack(side=tk.LEFT, padx=(0, 5))
        self.kind_var = tk.StringVar(value="3DPrint")
        create_styled_combobox(
            parent_frame, textvariable=self.kind_var, width=9,
            values=['3DPrint', 'CNC', 'Technical', 'Arch'],
        ).pack(side=tk.LEFT, padx=(0, 10))

        # Hardware combos
        create_styled_label(parent_frame, "Slicer:").pack(side=tk.LEFT, padx=(0, 5))
        self.slicer_var = tk.StringVar(value=sw_defaults.get("slicer", "Bambu Studio"))
        slicer_combo = create_styled_combobox(
            parent_frame, textvariable=self.slicer_var, width=15,
            values=['Bambu Studio', 'PrusaSlicer', 'Cura', 'Simplify3D', 'Creality Slicer', 'Other'],
        )
        slicer_combo.pack(side=tk.LEFT, padx=(0, 10))

        create_styled_label(parent_frame, "Printer:").pack(side=tk.LEFT, padx=(0, 5))
        self.printer_var = tk.StringVar(value=sw_defaults.get("printer", "Bambu Lab X1 Carbon"))
        printer_combo = create_styled_combobox(
            parent_frame, textvariable=self.printer_var, width=18,
            values=['Bambu Lab X1 Carbon', 'Bambu Lab P1S', 'Bambu Lab X1', 'Prusa MK3S+', 'Creality Ender 3', 'Other'],
        )
        printer_combo.pack(side=tk.LEFT, padx=(0, 10))

        # Conditional folder toggles
        self.preproduction_var = tk.BooleanVar(value=False)
        create_styled_checkbox(
            parent_frame, text="Preproduction", variable=self.preproduction_var,
            command=c.update_preview,
        ).pack(side=tk.LEFT, padx=(0, 10))

        self.library_var = tk.BooleanVar(value=False)
        create_styled_checkbox(
            parent_frame, text="_LIBRARY", variable=self.library_var,
            command=c.update_preview,
        ).pack(side=tk.LEFT)

        # Wire previews
        for var in (self.personal_var, self.order_var, self.product_var, self.project_var,
                    self.kind_var, self.slicer_var, self.printer_var,
                    self.preproduction_var, self.library_var):
            var.trace_add("write", lambda *a: c.update_preview())

        # Make personal_var visible to the FormKeyboardMixin (P shortcut + checkbox handling)
        c.personal_var = self.personal_var
        c.personal_check = self.personal_check
        self.personal_check.bind("<Return>", lambda e: self._enter_toggle('personal'))
        self.order_check.bind("<Return>", lambda e: self._enter_toggle('order'))
        self.product_check.bind("<Return>", lambda e: self._enter_toggle('product'))
        self.project_check.bind("<Return>", lambda e: self._enter_toggle('project'))

        return [self.personal_check, self.order_check, self.product_check, self.project_check]

    def setup_extra_keyboard_shortcuts(self):
        root = self.creator.parent.winfo_toplevel()
        root.bind("<o>", self._on_o)
        root.bind("<O>", self._on_o)
        root.bind("<d>", self._on_d)
        root.bind("<D>", self._on_d)
        root.bind("<j>", self._on_j)
        root.bind("<J>", self._on_j)

    def _on_o(self, event):
        if getattr(self.creator, '_in_text_field', False):
            return
        self._toggle_type('order')
        return "break"

    def _on_d(self, event):
        if getattr(self.creator, '_in_text_field', False):
            return
        self._toggle_type('product')
        return "break"

    def _on_j(self, event):
        if getattr(self.creator, '_in_text_field', False):
            return
        self._toggle_type('project')
        return "break"

    def _enter_toggle(self, kind):
        self._toggle_type(kind)
        return "break"

    def _toggle_type(self, clicked):
        """Activate the clicked radio, clearing the others.

        Matches legacy semantics: clicking an already-active radio does NOT
        deselect it. To switch off, click a different one.
        """
        c = self.creator
        targets = {
            'personal': self.personal_var,
            'order': self.order_var,
            'product': self.product_var,
            'project': self.project_var,
        }
        # When invoked via Checkbutton command, the var is already flipped; if
        # the user clicked an active radio, the flip turned it off — restore it.
        targets[clicked].set(True)
        for k, v in targets.items():
            if k != clicked:
                v.set(False)

        # Snapshot client + base_dir on first switch into a special mode
        any_special = (self.personal_var.get() or self.order_var.get()
                       or self.product_var.get() or self.project_var.get())
        if any_special and not self._client_backup:
            self._client_backup = c.client_name_var.get() if c.client_name_var else ""
            self._base_dir_backup = c.base_dir_var.get()

        physical_base = c.settings.get_work_path("Physical").replace('\\', '/')
        if self.personal_var.get():
            if c.client_name_var: c.client_name_var.set("Personal")
            c.base_dir_var.set(physical_base + "/_personal")
        elif self.order_var.get():
            if c.client_name_var:
                c.client_name_var.set(self._client_backup)
            c.base_dir_var.set(physical_base + "/Order")
        elif self.product_var.get():
            if c.client_name_var: c.client_name_var.set("alles3d")
            c.base_dir_var.set(physical_base + "/Product")
        elif self.project_var.get():
            if c.client_name_var:
                c.client_name_var.set(self._client_backup)
            c.base_dir_var.set(physical_base + "/Project")
        else:
            if c.client_name_var:
                c.client_name_var.set(self._client_backup)
            c.base_dir_var.set(self._base_dir_backup or physical_base)
            self._client_backup = ""
            self._base_dir_backup = ""

        c.update_preview()

    # ---- naming + routing ----

    def build_folder_name(self):
        c = self.creator
        from datetime import datetime
        date = c.date_var.get() or datetime.now().strftime('%Y-%m-%d')
        client = c.client_name_var.get() if c.client_name_var else ""
        project = c.project_name_var.get() if c.project_name_var else ""
        kind = self.kind_var.get() if self.kind_var else "3DPrint"
        if self.personal_var.get() or self.product_var.get():
            return f"{date}_{kind}_{project}"
        return f"{date}_{kind}_{client}_{project}"

    def get_target_directory(self, base_dir):
        # Physical routing is driven by the radio above writing to base_dir_var
        # directly, so the standard base_dir is already correct.
        return base_dir

    # ---- tree conditionals ----

    def get_conditionals(self):
        from shared_form_keyboard import get_active_software
        c = self.creator
        active = get_active_software(c.software_chips) if c.software_chips else {}
        return {
            'preproduction': self.preproduction_var.get() if self.preproduction_var else False,
            'library': self.library_var.get() if self.library_var else False,
            'houdini': 'Houdini' in active,
            'blender': 'Blender' in active,
            'freecad': 'FreeCAD' in active,
            'alibre': 'Alibre' in active,
            'affinity': 'Affinity' in active,
        }

    # ---- metadata + specs ----

    def build_metadata(self):
        subtype = ''
        if self.order_var and self.order_var.get():
            subtype = 'Order'
        elif self.product_var and self.product_var.get():
            subtype = 'Product'
        elif self.project_var and self.project_var.get():
            subtype = 'Project'
        elif self.personal_var and self.personal_var.get():
            subtype = 'Personal'
        return {
            "slicer": self.slicer_var.get() if self.slicer_var else "",
            "printer": self.printer_var.get() if self.printer_var else "",
            "is_personal": self.personal_var.get() if self.personal_var else False,
            "is_product": self.product_var.get() if self.product_var else False,
            "is_order": self.order_var.get() if self.order_var else False,
            "physical_subtype": subtype,
            "physical_kind": self.kind_var.get() if self.kind_var else "3DPrint",
        }

    def write_specs_extras(self, content_parts):
        content_parts.extend([
            "HARDWARE",
            "======================",
            f"Slicer: {self.slicer_var.get() if self.slicer_var else ''}",
            f"3D Printer: {self.printer_var.get() if self.printer_var else ''}",
            "",
            "PROJECT STRUCTURE",
            "======================",
            "Preproduction folder " + ("included" if self.preproduction_var and self.preproduction_var.get() else "not included"),
            "_LIBRARY folder " + ("included" if self.library_var and self.library_var.get() else "not included"),
            "",
        ])

