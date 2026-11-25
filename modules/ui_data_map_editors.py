"""
Data Map editors - the dark modal windows the Data Manager uses to edit its
map (shared_data_map): one location/transport list manager, a dataset
editor, and the small "this dataset at this location" place editor that
opens when a matrix cell is clicked.

Dropdowns are plain tk Menus (see Dropdown) rather than ttk.Combobox, so
their popups are dark without changing app-wide ttk/option-database styles.
"""

import os
import tkinter as tk
from tkinter import filedialog
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from shared_colors import (
    BG_DARKEST, BG_RAISED_1, BG_RAISED_2, BG_HOVER, BORDER,
    TEXT_PRIMARY, TEXT_SECONDARY, ACCENT, ACCENT_DARK, ERROR,
)
from shared_data_map import ROLES, slugify

FONT = ("Segoe UI", 10)
SMALL = ("Segoe UI", 9)
BOLD = ("Segoe UI", 10, "bold")
HEAD = ("Segoe UI", 13, "bold")


# ------------------------------------------------------------- widgets

def _entry(parent, var, width=30):
    e = tk.Entry(parent, textvariable=var, width=width, bg=BG_RAISED_1, fg=TEXT_PRIMARY,
                 insertbackground=TEXT_PRIMARY, relief=tk.FLAT, font=FONT,
                 highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT)
    return e


def _text(parent, height=4):
    return tk.Text(parent, height=height, bg=BG_RAISED_1, fg=TEXT_PRIMARY, wrap=tk.WORD,
                   insertbackground=TEXT_PRIMARY, relief=tk.FLAT, font=FONT, undo=True,
                   highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
                   padx=6, pady=4)


def _button(parent, text, command, primary=False, danger=False):
    bg = ACCENT_DARK if primary else BG_RAISED_2
    fg = "#ffffff" if primary else (ERROR if danger else TEXT_PRIMARY)
    hover = ACCENT if primary else BG_HOVER
    b = tk.Label(parent, text=text, bg=bg, fg=fg, font=BOLD if primary else FONT,
                 padx=14, pady=5, cursor="hand2")
    b.bind("<Button-1>", lambda _e: command())
    b.bind("<Enter>", lambda _e: b.configure(bg=hover))
    b.bind("<Leave>", lambda _e: b.configure(bg=bg))
    return b


def _label(parent, text, dim=False, font=FONT, **kw):
    return tk.Label(parent, text=text, bg=kw.pop("bg", BG_DARKEST),
                    fg=TEXT_SECONDARY if dim else TEXT_PRIMARY, font=font, anchor="w",
                    justify="left", **kw)


class Dropdown(tk.Label):
    """A dark select box. ``choices`` is ``[(value, label)]``; a label of
    the form "Group › Item" goes into a "Group" submenu when ``grouped``.
    ``.get()`` / ``.set(value)``; ``on_change(value)`` fires on pick."""

    def __init__(self, parent, choices: Sequence[Tuple[Any, str]], value=None,
                 grouped=False, placeholder="— none —", on_change: Callable = None, width=28):
        super().__init__(parent, bg=BG_RAISED_1, fg=TEXT_PRIMARY, font=FONT, anchor="w",
                         padx=6, pady=3, cursor="hand2", width=width,
                         highlightthickness=1, highlightbackground=BORDER)
        self._choices = list(choices)
        self._grouped = grouped
        self._placeholder = placeholder
        self._on_change = on_change
        self._value = None
        self.set(value)
        self.bind("<Button-1>", self._post)

    def get(self):
        return self._value

    def set(self, value):
        self._value = value
        label = next((l for v, l in self._choices if v == value), None)
        self.configure(text=(label or self._placeholder) + "  ▾",
                       fg=TEXT_PRIMARY if label else TEXT_SECONDARY)

    def _menu(self, parent=None):
        return tk.Menu(parent or self, tearoff=False, bg=BG_RAISED_2, fg=TEXT_PRIMARY,
                       activebackground=ACCENT_DARK, activeforeground="#ffffff",
                       font=FONT, bd=0, relief=tk.FLAT)

    def _pick(self, value):
        self.set(value)
        if self._on_change:
            self._on_change(value)

    def _post(self, _event=None):
        menu = self._menu()
        groups: Dict[str, tk.Menu] = {}
        for value, label in self._choices:
            if self._grouped and " › " in label:
                group, rest = label.split(" › ", 1)
                if group not in groups:
                    groups[group] = self._menu(menu)
                    menu.add_cascade(label=group, menu=groups[group])
                groups[group].add_command(label=rest, command=lambda v=value: self._pick(v))
            else:
                menu.add_command(label=label, command=lambda v=value: self._pick(v))
        menu.tk_popup(self.winfo_rootx(), self.winfo_rooty() + self.winfo_height())


# ------------------------------------------------------------ base window

class _Modal(tk.Toplevel):
    """Dark modal window: build into ``self.body``, buttons into
    ``self.bar``; ``self.result`` is what the caller gets back."""

    def __init__(self, parent, title: str, heading: str, sub: str = "", width=520):
        super().__init__(parent)
        self.result = None
        self.title(title)
        self.configure(bg=BG_DARKEST)
        self.minsize(width, 0)
        self.resizable(True, True)
        outer = tk.Frame(self, bg=BG_DARKEST)
        outer.pack(fill=tk.BOTH, expand=True, padx=18, pady=14)
        _label(outer, heading, font=HEAD).pack(fill=tk.X)
        if sub:
            _label(outer, sub, dim=True, font=SMALL, wraplength=width - 40).pack(fill=tk.X, pady=(2, 0))
        self.body = tk.Frame(outer, bg=BG_DARKEST)
        self.body.pack(fill=tk.BOTH, expand=True, pady=(12, 0))
        self.bar = tk.Frame(outer, bg=BG_DARKEST)
        self.bar.pack(fill=tk.X, pady=(14, 0))
        self.bind("<Escape>", lambda _e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    def show(self):
        self.update_idletasks()
        parent = self.master
        try:
            x = parent.winfo_rootx() + max((parent.winfo_width() - self.winfo_reqwidth()) // 2, 0)
            y = parent.winfo_rooty() + max((parent.winfo_height() - self.winfo_reqheight()) // 3, 0)
            self.geometry(f"+{x}+{y}")
            self.transient(parent.winfo_toplevel())
        except tk.TclError:
            pass
        self.focus_set()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        self.wait_window(self)
        return self.result

    def finish(self, result):
        self.result = result
        self.destroy()

    def cancel(self):
        self.destroy()

    def field(self, row, label, widget, hint=""):
        _label(self.body, label, dim=True, font=SMALL).grid(row=row, column=0, sticky="nw",
                                                              padx=(0, 12), pady=4)
        widget.grid(row=row, column=1, sticky="ew", pady=4)
        if hint:
            _label(self.body, hint, dim=True, font=SMALL).grid(row=row, column=2, sticky="w", padx=(8, 0))
        self.body.columnconfigure(1, weight=1)


# ---------------------------------------------------------- place editor

class PlaceDialog(_Modal):
    """Edit one dataset's copy at one location. Result: the place dict,
    the string "remove", or None (cancelled)."""

    def __init__(self, parent, dataset_name: str, location_name: str,
                 place: Optional[Dict[str, Any]], transports: List[Dict[str, Any]]):
        super().__init__(parent, "Data location", f"{dataset_name} on {location_name}",
                         "What this copy is, how it gets here, and anything worth remembering.")
        place = place or {}
        role_choices = [(k, f"{g}  {label} — {hint}") for k, (g, label, hint) in ROLES.items()]
        self.role = Dropdown(self.body, role_choices, place.get("role") or "live", width=44)
        self.field(0, "Role", self.role)
        t_choices = [("", "— none / not needed —")] + [(t["key"], f"{t['icon']}  {t['name']}".strip())
                                                       for t in transports]
        self.transport = Dropdown(self.body, t_choices, place.get("transport", ""), width=44)
        self.field(1, "Moved by", self.transport)

        path_row = tk.Frame(self.body, bg=BG_DARKEST)
        self.path = tk.StringVar(value=place.get("path", ""))
        _entry(path_row, self.path).pack(side=tk.LEFT, fill=tk.X, expand=True)
        _button(path_row, "…", self._browse).pack(side=tk.LEFT, padx=(6, 0))
        self.field(2, "Path / address", path_row)
        self.notes = _text(self.body, height=5)
        self.notes.insert("1.0", place.get("notes", ""))
        self.field(3, "Notes", self.notes)

        if place:
            _button(self.bar, "Remove from here", lambda: self.finish("remove"), danger=True).pack(side=tk.LEFT)
        _button(self.bar, "Save", self._save, primary=True).pack(side=tk.RIGHT)
        _button(self.bar, "Cancel", self.cancel).pack(side=tk.RIGHT, padx=(0, 8))
        self.bind("<Control-Return>", lambda _e: self._save())

    def _browse(self):
        start = os.path.expandvars(self.path.get()) if self.path.get() else ""
        chosen = filedialog.askdirectory(parent=self, initialdir=start if os.path.isdir(start) else None)
        if chosen:
            self.path.set(chosen.replace("/", "\\") if os.name == "nt" else chosen)

    def _save(self):
        self.finish({
            "role": self.role.get(),
            "transport": self.transport.get() or "",
            "path": self.path.get().strip(),
            "notes": self.notes.get("1.0", "end-1c").strip(),
        })


# -------------------------------------------------------- dataset editor

class DatasetDialog(_Modal):
    """Create/edit a dataset's identity, notes and tools (where it lives is
    edited per cell). Result: the updated dataset dict, "delete", or None."""

    def __init__(self, parent, dataset: Optional[Dict[str, Any]], taken_keys,
                 app_names: Sequence[str], tool_refs: Sequence[Tuple[list, str]]):
        is_new = dataset is None
        super().__init__(parent, "Dataset", "New dataset" if is_new else f"Edit {dataset['name']}",
                         "A piece of data an app owns: a library, vault, database or config folder.",
                         width=600)
        self._dataset = dict(dataset or {"key": "", "places": [], "tools": []})
        self._taken = set(taken_keys) - {self._dataset.get("key")}
        self._tool_labels = {tuple(r): l for r, l in tool_refs}

        self.name = tk.StringVar(value=self._dataset.get("name", ""))
        self.icon = tk.StringVar(value=self._dataset.get("icon", ""))
        self.app = tk.StringVar(value=self._dataset.get("app", ""))
        self.desc = tk.StringVar(value=self._dataset.get("description", ""))
        name_entry = _entry(self.body, self.name)
        self.field(0, "Name", name_entry)
        self.field(1, "Icon", _entry(self.body, self.icon, width=6), "an emoji")

        app_row = tk.Frame(self.body, bg=BG_DARKEST)
        _entry(app_row, self.app).pack(side=tk.LEFT, fill=tk.X, expand=True)
        Dropdown(app_row, [(n, n) for n in app_names], None, placeholder="catalog",
                 on_change=self.app.set, width=10).pack(side=tk.LEFT, padx=(6, 0))
        self.field(2, "App", app_row, "launched from the row")
        self.field(3, "Description", _entry(self.body, self.desc))
        self.notes = _text(self.body, height=6)
        self.notes.insert("1.0", self._dataset.get("notes", ""))
        self.field(4, "Notes", self.notes)

        tools_box = tk.Frame(self.body, bg=BG_DARKEST)
        self.tools = [list(r) for r in self._dataset.get("tools", [])]
        self.tool_list = tk.Listbox(tools_box, height=5, bg=BG_RAISED_1, fg=TEXT_PRIMARY, font=FONT,
                                    selectbackground=ACCENT_DARK, relief=tk.FLAT, highlightthickness=1,
                                    highlightbackground=BORDER, activestyle="none")
        self.tool_list.pack(fill=tk.BOTH, expand=True)
        row = tk.Frame(tools_box, bg=BG_DARKEST)
        row.pack(fill=tk.X, pady=(6, 0))
        Dropdown(row, [(tuple(r), l) for r, l in tool_refs], None, grouped=True,
                 placeholder="＋ Add a tool", on_change=self._add_tool, width=24).pack(side=tk.LEFT)
        _button(row, "Remove selected", self._remove_tool).pack(side=tk.LEFT, padx=(6, 0))
        self.field(5, "Tools", tools_box)
        self._refresh_tools()

        if not is_new:
            _button(self.bar, "Delete dataset", lambda: self.finish("delete"), danger=True).pack(side=tk.LEFT)
        _button(self.bar, "Save", self._save, primary=True).pack(side=tk.RIGHT)
        _button(self.bar, "Cancel", self.cancel).pack(side=tk.RIGHT, padx=(0, 8))
        self.bind("<Control-Return>", lambda _e: self._save())
        name_entry.focus_set()

    def _refresh_tools(self):
        self.tool_list.delete(0, tk.END)
        for ref in self.tools:
            self.tool_list.insert(tk.END, self._tool_labels.get(tuple(ref), f"(missing) {ref[2]}"))

    def _add_tool(self, ref):
        if ref and list(ref) not in self.tools:
            self.tools.append(list(ref))
            self._refresh_tools()

    def _remove_tool(self):
        for index in reversed(self.tool_list.curselection()):
            del self.tools[index]
        self._refresh_tools()

    def _save(self):
        name = self.name.get().strip()
        if not name:
            return
        ds = self._dataset
        ds.update({
            "name": name,
            "icon": self.icon.get().strip(),
            "app": self.app.get().strip(),
            "description": self.desc.get().strip(),
            "notes": self.notes.get("1.0", "end-1c").strip(),
            "tools": self.tools,
        })
        if not ds.get("key"):
            ds["key"] = slugify(name, self._taken)
        self.finish(ds)


# ------------------------------------------- locations / transports lists

class ListManagerDialog(_Modal):
    """Edit an ordered list of simple items (locations or transports).

    ``fields`` is ``[(field, label, kind, extra)]`` with kind "entry",
    "short", "text" or "dropdown" (extra = choices, plus grouped flag).
    Result: the new list (order = column order), or None when cancelled.
    """

    def __init__(self, parent, title, heading, sub, items, fields, new_label):
        super().__init__(parent, title, heading, sub, width=720)
        self.items = [dict(i) for i in items]
        self.fields = fields
        self.new_label = new_label
        self.current = None
        self.vars: Dict[str, Any] = {}

        left = tk.Frame(self.body, bg=BG_DARKEST)
        left.pack(side=tk.LEFT, fill=tk.Y)
        self.listbox = tk.Listbox(left, width=24, height=14, bg=BG_RAISED_1, fg=TEXT_PRIMARY, font=FONT,
                                  selectbackground=ACCENT_DARK, relief=tk.FLAT, highlightthickness=1,
                                  highlightbackground=BORDER, activestyle="none", exportselection=False)
        self.listbox.pack(fill=tk.Y, expand=True)
        self.listbox.bind("<<ListboxSelect>>", lambda _e: self._select_from_list())
        btns = tk.Frame(left, bg=BG_DARKEST)
        btns.pack(fill=tk.X, pady=(6, 0))
        _button(btns, "＋ New", self._new).pack(side=tk.LEFT)
        _button(btns, "▲", lambda: self._move(-1)).pack(side=tk.LEFT, padx=(4, 0))
        _button(btns, "▼", lambda: self._move(1)).pack(side=tk.LEFT, padx=(4, 0))
        _button(btns, "Delete", self._delete, danger=True).pack(side=tk.RIGHT)

        self.form = tk.Frame(self.body, bg=BG_DARKEST)
        self.form.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))
        self.form.columnconfigure(1, weight=1)
        for row, (field, label, kind, extra) in enumerate(fields):
            _label(self.form, label, dim=True, font=SMALL).grid(row=row, column=0, sticky="nw",
                                                                  padx=(0, 12), pady=4)
            if kind == "text":
                widget = _text(self.form, height=7)
            elif kind == "dropdown":
                choices, grouped = extra
                widget = Dropdown(self.form, choices, None, grouped=grouped)
            else:
                var = tk.StringVar()
                widget = _entry(self.form, var, width=6 if kind == "short" else 30)
                widget.var = var
            widget.grid(row=row, column=1, sticky="w" if kind == "short" else "ew", pady=4)
            self.vars[field] = (kind, widget)

        _button(self.bar, "Save", self._save, primary=True).pack(side=tk.RIGHT)
        _button(self.bar, "Cancel", self.cancel).pack(side=tk.RIGHT, padx=(0, 8))
        self._refresh_list()
        self._select(0 if self.items else None)

    def _store_form(self):
        if self.current is None or self.current >= len(self.items):
            return
        item = self.items[self.current]
        for field, (kind, widget) in self.vars.items():
            if kind == "text":
                item[field] = widget.get("1.0", "end-1c").strip()
            elif kind == "dropdown":
                item[field] = widget.get()
            else:
                item[field] = widget.var.get().strip()
        item["name"] = item.get("name") or "Unnamed"

    def _load_form(self):
        item = self.items[self.current] if self.current is not None else {}
        state = tk.NORMAL if self.current is not None else tk.DISABLED
        for field, (kind, widget) in self.vars.items():
            value = item.get(field)
            if kind == "text":
                widget.configure(state=tk.NORMAL)
                widget.delete("1.0", tk.END)
                widget.insert("1.0", value or "")
                widget.configure(state=state)
            elif kind == "dropdown":
                widget.set(tuple(value) if isinstance(value, list) else value)
            else:
                widget.var.set(value or "")
                widget.configure(state=state)

    def _refresh_list(self):
        self.listbox.delete(0, tk.END)
        for item in self.items:
            self.listbox.insert(tk.END, f"{item.get('icon', '')}  {item.get('name', '')}".strip())

    def _select(self, index):
        self.current = index
        self.listbox.selection_clear(0, tk.END)
        if index is not None:
            self.listbox.selection_set(index)
            self.listbox.see(index)
        self._load_form()

    def _select_from_list(self):
        sel = self.listbox.curselection()
        if not sel or sel[0] == self.current:
            return
        self._store_form()
        self._refresh_list()
        self._select(sel[0])

    def _new(self):
        self._store_form()
        taken = {i.get("key") for i in self.items}
        self.items.append({"key": slugify(self.new_label, taken), "name": self.new_label})
        self._refresh_list()
        self._select(len(self.items) - 1)

    def _delete(self):
        if self.current is None:
            return
        del self.items[self.current]
        self._refresh_list()
        self._select(min(self.current, len(self.items) - 1) if self.items else None)

    def _move(self, step):
        if self.current is None:
            return
        self._store_form()
        target = self.current + step
        if 0 <= target < len(self.items):
            self.items[self.current], self.items[target] = self.items[target], self.items[self.current]
            self._refresh_list()
            self._select(target)

    def _save(self):
        self._store_form()
        for item in self.items:
            tool = item.get("tool")
            if isinstance(tool, tuple):
                item["tool"] = list(tool)
        self.finish(self.items)
