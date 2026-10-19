"""
UI Project Deck - Small always-on-top popup showing a project's icon,
name, and quick actions (Open Folder + project-context scripts).
"""

import tkinter as tk
from typing import Callable, Dict, Optional

from shared_logging import get_logger
from pipeline_categories import category_color, category_emoji, project_type_info
from shared_color_button import make_button

logger = get_logger("pipeline")

BG = "#0d1117"
PANEL_BG = "#1c2128"
MUTED_FG = "#8b949e"
SEPARATOR = "#30363d"


class ProjectDeckWindow(tk.Toplevel):
    """A small, always-on-top popup with a project's icon, name, Open
    Folder button, and every applicable project action button."""

    def __init__(self, parent, project: Dict, *, tracker_app,
                 offset_index: int = 0, on_close: Optional[Callable] = None):
        super().__init__(parent)
        self._project = project
        self._tracker_app = tracker_app
        self._on_close = on_close

        self.configure(bg=BG)
        self.title(f"{project.get('project_name', 'Project')} — Deck")
        self.resizable(False, False)
        self.attributes("-topmost", True)
        self.protocol("WM_DELETE_WINDOW", self._handle_close)

        self._build_ui()
        self.update_idletasks()
        self._place(parent, offset_index)

    def _build_ui(self):
        project = self._project
        info = project_type_info(project.get("project_type", ""))
        icon = category_emoji(info.get("category")) or info.get("icon", "📁")

        container = tk.Frame(self, bg=BG, padx=16, pady=16)
        container.pack(fill=tk.BOTH, expand=True)
        self._register_draggable(container)

        icon_label = tk.Label(
            container, text=icon, bg=BG, fg="white",
            font=("Segoe UI Emoji", 56),
        )
        icon_label.pack()
        self._register_draggable(icon_label)

        name_label = tk.Label(
            container, text=project.get("project_name", "-"), bg=BG, fg="white",
            font=("Arial", 12, "bold"), wraplength=220, justify="center",
        )
        name_label.pack(pady=(6, 0))
        self._register_draggable(name_label)

        subline = " / ".join(
            part for part in (project.get("client_name"), info.get("name")) if part
        )
        if subline:
            subline_label = tk.Label(
                container, text=subline, bg=BG, fg=MUTED_FG,
                font=("Arial", 8), wraplength=220, justify="center",
            )
            subline_label.pack(pady=(2, 0))
            self._register_draggable(subline_label)

        separator = tk.Frame(container, bg=SEPARATOR, height=1)
        separator.pack(fill=tk.X, pady=10)
        self._register_draggable(separator)

        open_btn = make_button(
            container,
            text="📂 Open Folder",
            command=lambda: self._tracker_app._open_project_folder(self._project),
            bg="#238636",
            fg="white",
            font=("Arial", 9),
            relief=tk.FLAT,
            cursor="hand2",
            padx=15,
            pady=6,
        )
        open_btn.pack(fill=tk.X, pady=(0, 6))

        actions = self._tracker_app._project_actions(project)
        if not actions:
            tk.Label(
                container, text="No actions available", bg=BG, fg=MUTED_FG,
                font=("Arial", 8, "italic"),
            ).pack(pady=(4, 0))
        else:
            for category_key, script_key, subcat_key, script_data in actions:
                color = category_color(category_key.capitalize()) or "#238636"
                label = f"{script_data.get('icon', '')} {script_data.get('name', script_key)}".strip()
                btn = make_button(
                    container,
                    text=label,
                    command=lambda sd=script_data: self._run_action(sd),
                    bg=color,
                    fg="white",
                    font=("Arial", 9),
                    relief=tk.FLAT,
                    cursor="hand2",
                    padx=15,
                    pady=6,
                    anchor="w",
                )
                btn.pack(fill=tk.X, pady=2)

    def _run_action(self, script_data: Dict):
        self._tracker_app._run_project_action_for(self._project, script_data)

    def _register_draggable(self, widget):
        """Let the window be dragged by pressing on this widget (used for
        the background frame and the icon/name labels, never on buttons)."""
        widget.bind("<ButtonPress-1>", self._start_drag)
        widget.bind("<B1-Motion>", self._do_drag)

    def _start_drag(self, event):
        self._drag_start_x = event.x_root
        self._drag_start_y = event.y_root
        self._drag_win_x = self.winfo_x()
        self._drag_win_y = self.winfo_y()

    def _do_drag(self, event):
        dx = event.x_root - self._drag_start_x
        dy = event.y_root - self._drag_start_y
        self.geometry(f"+{self._drag_win_x + dx}+{self._drag_win_y + dy}")

    def _place(self, parent, offset_index: int):
        base_x = parent.winfo_rootx() + 60 + offset_index * 28
        base_y = parent.winfo_rooty() + 60 + offset_index * 28

        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        x = max(0, min(base_x, screen_w - width))
        y = max(0, min(base_y, screen_h - height))

        self.geometry(f"+{x}+{y}")

    def focus_deck(self):
        self.lift()
        self.focus_force()

    def _handle_close(self):
        if self._on_close:
            try:
                self._on_close()
            except Exception:
                logger.exception("Deck on_close callback failed")
        self.destroy()
