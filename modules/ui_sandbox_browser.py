"""
Sandbox Browser Panel
Author: Florian Dheer
Description: Embedded, lazy-loaded file/folder tree for the Sandbox
category's loose files, with simple free-form tagging. Fills the same
right-hand panel slot fastrack_hub.py otherwise gives to ProjectTrackerApp
(creative categories) or InvoiceManager (Business).
"""

import os
import tkinter as tk
from tkinter import ttk, font
from typing import Optional

from ui_theme import COLORS
from sandbox_tag_store import SandboxTagStore
from shared_open_path import open_path

# Sentinel text for the lazy-load placeholder child inserted under every
# unexpanded folder node, so the expand arrow shows without scanning the
# whole subtree up front.
_LOADING_PLACEHOLDER = "…loading…"


class SandboxBrowserPanel(tk.Frame):
    def __init__(self, parent, root_path: str, status_callback=None):
        super().__init__(parent, bg=COLORS["bg_primary"])
        self.root_path = root_path
        self.status_callback = status_callback
        self.tag_store = SandboxTagStore()

        # iid -> absolute path, for both the normal tree and filtered results
        self._iid_paths = {}
        self._filter_after_id = None

        self._build_ui()
        self.refresh()

    def _notify(self, message: str, level: str = "info"):
        """Fire the host status callback, but never let it crash us.

        This panel can be constructed very early (e.g. Sandbox was the
        last-selected category, so it's rebuilt while the Hub restores
        session state during its own __init__ — before the Hub's own
        status bar widget exists yet). A status notification is a nice-to-
        have, never worth taking down tree population over.
        """
        if not self.status_callback:
            return
        try:
            self.status_callback(message, level)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # --- Header ---
        header = tk.Frame(self, bg=COLORS["bg_primary"])
        header.grid(row=0, column=0, sticky="ew", padx=15, pady=(15, 10))
        header.columnconfigure(1, weight=1)

        tk.Label(
            header, text="Sandbox", font=font.Font(family="Segoe UI", size=14, weight="bold"),
            fg=COLORS["text_primary"], bg=COLORS["bg_primary"]
        ).grid(row=0, column=0, sticky="w")

        tk.Label(
            header, text=self.root_path, font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_secondary"], bg=COLORS["bg_primary"]
        ).grid(row=1, column=0, sticky="w", columnspan=2)

        # --- Top bar: filter / refresh / open in explorer ---
        top_bar = tk.Frame(self, bg=COLORS["bg_primary"])
        top_bar.grid(row=1, column=0, sticky="ew", padx=15, pady=(0, 10))
        top_bar.columnconfigure(0, weight=1)

        self.filter_var = tk.StringVar()
        self.filter_var.trace_add("write", lambda *a: self._schedule_filter())
        filter_entry = tk.Entry(
            top_bar, textvariable=self.filter_var,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"], relief=tk.FLAT
        )
        filter_entry.grid(row=0, column=0, sticky="ew", ipady=4)
        self._set_placeholder(filter_entry, "Filter by name or tag...")

        refresh_btn = tk.Button(
            top_bar, text="Refresh", command=self.refresh,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2"
        )
        refresh_btn.grid(row=0, column=1, padx=(8, 0))

        explorer_btn = tk.Button(
            top_bar, text="Open in Explorer", command=self._open_in_explorer,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2"
        )
        explorer_btn.grid(row=0, column=2, padx=(8, 0))

        # --- Tree + tag editor (side by side) ---
        body = tk.Frame(self, bg=COLORS["bg_primary"])
        body.grid(row=2, column=0, sticky="nsew", padx=15, pady=(0, 15))
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        tree_frame = tk.Frame(body, bg=COLORS["bg_primary"])
        tree_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(tree_frame, columns=("tags",), show="tree headings")
        self.tree.heading("#0", text="Name")
        self.tree.heading("tags", text="Tags")
        self.tree.column("#0", width=260)
        self.tree.column("tags", width=140)
        self.tree.grid(row=0, column=0, sticky="nsew")

        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", self._on_double_click)

        self._build_tag_editor(body)

    def _build_tag_editor(self, parent):
        editor = tk.Frame(parent, bg=COLORS["bg_card"])
        editor.grid(row=0, column=1, sticky="nsew")
        editor.columnconfigure(0, weight=1)

        tk.Label(
            editor, text="Selected item", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(12, 4))

        self.selected_path_var = tk.StringVar(value="Nothing selected")
        tk.Label(
            editor, textvariable=self.selected_path_var, font=font.Font(family="Segoe UI", size=9),
            fg=COLORS["text_primary"], bg=COLORS["bg_card"], wraplength=260, justify="left", anchor="w"
        ).grid(row=1, column=0, sticky="ew", padx=12)

        tk.Label(
            editor, text="Tags (comma-separated)", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=2, column=0, sticky="w", padx=12, pady=(16, 4))

        self.tags_var = tk.StringVar()
        self.tags_entry = tk.Entry(
            editor, textvariable=self.tags_var,
            bg=COLORS["bg_secondary"], fg=COLORS["text_primary"],
            insertbackground=COLORS["text_primary"], relief=tk.FLAT, state=tk.DISABLED
        )
        self.tags_entry.grid(row=3, column=0, sticky="ew", padx=12, ipady=4)
        self.tags_entry.bind("<Return>", lambda e: self._save_tags())

        self.save_tags_btn = tk.Button(
            editor, text="Save Tags", command=self._save_tags,
            bg=COLORS["accent"], fg="#ffffff",
            activebackground=COLORS["accent_hover"], relief=tk.FLAT, cursor="hand2",
            state=tk.DISABLED
        )
        self.save_tags_btn.grid(row=4, column=0, sticky="w", padx=12, pady=(8, 16))

        tk.Label(
            editor, text="Existing tags", font=font.Font(family="Segoe UI", size=9, weight="bold"),
            fg=COLORS["text_secondary"], bg=COLORS["bg_card"]
        ).grid(row=5, column=0, sticky="w", padx=12, pady=(4, 4))

        self.existing_tags_frame = tk.Frame(editor, bg=COLORS["bg_card"])
        self.existing_tags_frame.grid(row=6, column=0, sticky="ew", padx=12, pady=(0, 12))

    def _set_placeholder(self, entry: tk.Entry, text: str):
        entry.insert(0, text)
        entry.config(fg=COLORS["text_secondary"])

        def on_focus_in(_e):
            if entry.get() == text:
                entry.delete(0, tk.END)
                entry.config(fg=COLORS["text_primary"])

        def on_focus_out(_e):
            if not entry.get():
                entry.insert(0, text)
                entry.config(fg=COLORS["text_secondary"])

        entry.bind("<FocusIn>", on_focus_in)
        entry.bind("<FocusOut>", on_focus_out)
        self._filter_placeholder = text

    # ------------------------------------------------------------------
    # Tree population
    # ------------------------------------------------------------------

    def refresh(self):
        """Rebuild the tree from disk, respecting any active filter."""
        self.tree.delete(*self.tree.get_children(""))
        self._iid_paths.clear()

        filter_text = self.filter_var.get().strip()
        if filter_text and filter_text != getattr(self, "_filter_placeholder", None):
            self._render_filtered(filter_text)
        else:
            self._render_lazy_root()

        self._notify("Sandbox refreshed", "info")

    def _render_lazy_root(self):
        if not os.path.isdir(self.root_path):
            self.tree.insert("", "end", text=f"(missing: {self.root_path})")
            return
        self._populate_children("", self.root_path)

    def _populate_children(self, parent_iid: str, dir_path: str):
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())
            )
        except OSError:
            return
        for entry in entries:
            self._insert_node(parent_iid, entry.path, entry.is_dir(follow_symlinks=False))

    def _insert_node(self, parent_iid: str, path: str, is_dir: bool) -> str:
        name = os.path.basename(path) or path
        tags = ", ".join(self.tag_store.get_tags(path))
        prefix = "\U0001F4C1 " if is_dir else "\U0001F4C4 "
        iid = self.tree.insert(parent_iid, "end", text=prefix + name, values=(tags,))
        self._iid_paths[iid] = path
        if is_dir:
            self.tree.insert(iid, "end", text=_LOADING_PLACEHOLDER)
        return iid

    def _on_tree_open(self, _event):
        iid = self.tree.focus()
        path = self._iid_paths.get(iid)
        if not path:
            return
        children = self.tree.get_children(iid)
        if len(children) == 1 and self.tree.item(children[0], "text") == _LOADING_PLACEHOLDER:
            self.tree.delete(children[0])
            self._populate_children(iid, path)

    # ------------------------------------------------------------------
    # Filtering
    # ------------------------------------------------------------------

    def _schedule_filter(self, delay=400):
        if self._filter_after_id is not None:
            try:
                self.after_cancel(self._filter_after_id)
            except Exception:
                pass
        self._filter_after_id = self.after(delay, self.refresh)

    def _render_filtered(self, filter_text: str):
        needle = filter_text.lower()
        matches = []
        for dirpath, _dirs, filenames in os.walk(self.root_path):
            for name in filenames:
                full = os.path.join(dirpath, name)
                tags = self.tag_store.get_tags(full)
                if needle in name.lower() or any(needle in t.lower() for t in tags):
                    matches.append(full)

        if not matches:
            self.tree.insert("", "end", text="(no matches)")
            return

        # Build the minimal set of ancestor folders needed to show each match
        # in context, then insert everything fully expanded. Stops at
        # root_path itself (returns "" = the tree's own root) so the top
        # level here matches the flat top level of the normal lazy view.
        node_iids = {}  # normcased dir path -> iid
        root_norm = os.path.normcase(self.root_path)

        def ensure_dir_node(dir_path: str) -> str:
            norm = os.path.normcase(dir_path)
            if norm == root_norm:
                return ""
            if norm in node_iids:
                return node_iids[norm]
            parent_iid = ensure_dir_node(os.path.dirname(dir_path))
            iid = self.tree.insert(parent_iid, "end", text="\U0001F4C1 " + os.path.basename(dir_path), open=True)
            self._iid_paths[iid] = dir_path
            node_iids[norm] = iid
            return iid

        for match in sorted(matches):
            parent_iid = ensure_dir_node(os.path.dirname(match))
            tags = ", ".join(self.tag_store.get_tags(match))
            iid = self.tree.insert(parent_iid, "end", text="\U0001F4C4 " + os.path.basename(match), values=(tags,))
            self._iid_paths[iid] = match

    # ------------------------------------------------------------------
    # Selection / open / tagging
    # ------------------------------------------------------------------

    def _selected_path(self) -> Optional[str]:
        sel = self.tree.selection()
        if not sel:
            return None
        return self._iid_paths.get(sel[0])

    def _on_select(self, _event):
        path = self._selected_path()
        if not path:
            return
        self.selected_path_var.set(path)
        self.tags_var.set(", ".join(self.tag_store.get_tags(path)))
        self.tags_entry.config(state=tk.NORMAL)
        self.save_tags_btn.config(state=tk.NORMAL)
        self._refresh_existing_tags()

    def _on_double_click(self, _event):
        iid = self.tree.focus()
        path = self._iid_paths.get(iid)
        if not path:
            return
        if os.path.isdir(path):
            is_open = self.tree.item(iid, "open")
            if not is_open:
                children = self.tree.get_children(iid)
                if len(children) == 1 and self.tree.item(children[0], "text") == _LOADING_PLACEHOLDER:
                    self.tree.delete(children[0])
                    self._populate_children(iid, path)
                self.tree.item(iid, open=True)
            else:
                self.tree.item(iid, open=False)
        else:
            if open_path(path):
                self._notify(f"Opened: {os.path.basename(path)}", "info")
            else:
                self._notify(f"Failed to open {path}", "error")

    def _save_tags(self):
        path = self._selected_path()
        if not path:
            return
        tags = [t.strip() for t in self.tags_var.get().split(",")]
        self.tag_store.set_tags(path, tags)

        iid = self.tree.selection()[0]
        self.tree.set(iid, "tags", ", ".join(self.tag_store.get_tags(path)))
        self._refresh_existing_tags()
        self._notify(f"Tags saved for {os.path.basename(path)}", "info")

    def _refresh_existing_tags(self):
        for widget in self.existing_tags_frame.winfo_children():
            widget.destroy()

        counts = self.tag_store.all_tags()
        for tag in sorted(counts, key=lambda t: (-counts[t], t.lower()))[:20]:
            btn = tk.Button(
                self.existing_tags_frame, text=f"{tag} ({counts[tag]})",
                command=lambda t=tag: self._append_tag(t),
                bg=COLORS["bg_secondary"], fg=COLORS["text_secondary"],
                activebackground=COLORS["bg_hover"], relief=tk.FLAT, cursor="hand2",
                font=font.Font(family="Segoe UI", size=8)
            )
            btn.pack(side=tk.LEFT, padx=(0, 4), pady=2)

    def _append_tag(self, tag: str):
        current = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        if tag not in current:
            current.append(tag)
        self.tags_var.set(", ".join(current))

    def _open_in_explorer(self):
        if not open_path(self.root_path):
            self._notify("Failed to open file browser", "error")
