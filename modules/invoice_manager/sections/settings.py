"""Settings — environment status, file/folder shortcuts, debug-mode controls.

Four cards: Environment (paths + creds + health), WooCommerce
(credentials + monitoring, replaces the legacy ``SettingsDialog``),
Bookkeeping folder structure (quarter folder creator, integrated from
the legacy ``PipelineScript_Bookkeeping_FolderStructure.py``) and Debug
mode (snapshot + restore).
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Callable, Dict, List

from invoice_manager.core.config import DATA_DIR
from invoice_manager.core.debug_mode import (
    DEBUG_SUBFOLDER_NAME, perform_rollback,
)
from invoice_manager.dialogs import ask_yes_no, show_error, show_info
from shared_logging import get_logger

from invoice_manager.bookkeeping import (
    QUARTERS, create_quarter_folders, get_current_quarter,
    get_next_quarter, list_quarter_statuses,
)
from invoice_manager.sections.base import Section
from invoice_manager.theme import PALETTE, FONTS
from invoice_manager.widgets.buttons import secondary_button, danger_button, primary_button
from invoice_manager.widgets.card import Card

logger = get_logger("invoice_manager.settings")


class SettingsSection(Section):
    title = "Settings"
    sidebar_key = "settings"
    sidebar_icon = "⚙"

    def __init__(self, parent, state, *, on_debug_toggle: Callable[[], None]):
        super().__init__(parent, state)
        self._on_debug_toggle = on_debug_toggle

    def build(self, root: tk.Frame) -> None:
        C = PALETTE
        root.configure(bg=C["bg"])
        wrap = tk.Frame(root, bg=C["bg"], padx=20, pady=14)
        wrap.pack(fill="both", expand=True)

        # ----- Environment card -----
        env_card = Card(wrap, title="Environment")
        env_card.pack(fill="x")
        self._build_env(env_card.body)

        # ----- WooCommerce card -----
        wc_card = Card(wrap, title="WooCommerce")
        wc_card.pack(fill="x", pady=(12, 0))
        self._build_wc(wc_card.body)

        # ----- Bookkeeping folder structure card -----
        book_card = Card(wrap, title="Bookkeeping folder structure")
        book_card.pack(fill="x", pady=(12, 0))
        self._build_bookkeeping(book_card.body)

        # ----- Debug card -----
        dbg_card = Card(wrap, title="Debug mode")
        dbg_card.pack(fill="x", pady=(12, 0))
        self._build_debug(dbg_card.body)

    def _build_env(self, parent: tk.Frame) -> None:
        C = PALETTE

        tk.Label(
            parent,
            text=("Paths are managed centrally in the global FastRack settings "
                  "(rack_config.json → business). Displayed here read-only."),
            fg=C["text_dim"], bg=C["card_bg"],
            font=FONTS["small"], wraplength=820, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._env_value_labels: Dict[str, tk.Label] = {}
        for label in ("DB path", "Boekhouding base", "soffice binary",
                      "WC credentials", "monitor_secret_key"):
            row = tk.Frame(parent, bg=C["card_bg"])
            row.pack(fill="x", pady=2)
            tk.Label(row, text=label, fg=C["text_dim"], bg=C["card_bg"],
                     font=FONTS["small"], width=22, anchor="w").pack(side="left")
            value_lbl = tk.Label(
                row, text="", fg=C["text_dim"], bg=C["card_bg"],
                font=FONTS["body"], anchor="w",
            )
            value_lbl.pack(side="left")
            self._env_value_labels[label] = value_lbl

        self._refresh_env_values()

        sep = tk.Frame(parent, bg=C["card_border"], height=1)
        sep.pack(fill="x", pady=(12, 10))

        cfg = self.state.config
        btn_row = tk.Frame(parent, bg=C["card_bg"])
        btn_row.pack(fill="x")
        for text, cmd in [
            ("Open config.json",   lambda: self.state.resolve_pdf_open(cfg.source_path)),
            ("Open data folder",   lambda: self.state.resolve_pdf_open(cfg.source_path.parent)),
            ("Open templates",     self._open_templates_folder),
            ("Run health check",   self._health_check),
        ]:
            secondary_button(btn_row, text, cmd).pack(side="left", padx=(0, 6))

    def _refresh_env_values(self) -> None:
        """Recompute the dynamic right-hand values of the Environment card.

        Called on first build and again after the WooCommerce card saves
        new credentials so the cred status reflects reality without an
        app restart.
        """
        C = PALETTE
        cfg = self.state.config
        soffice = cfg.resolve_soffice_path()
        boek = cfg.resolve_boekhouding_base()
        db = cfg.resolve_db_path()
        creds = cfg.get_wc_credentials_for_alles3d() or {}
        has_secret = bool(creds.get("monitor_secret_key"))
        rows = [
            ("DB path",            str(db), db.exists()),
            ("Boekhouding base",   str(boek), boek.exists()),
            ("soffice binary",     str(soffice or "(not configured)"),
             bool(soffice and soffice.exists())),
            ("WC credentials",     "✓ configured" if creds.get("consumer_key") else "(not set)",
             bool(creds.get("consumer_key"))),
            ("monitor_secret_key", "● set — File Quarter WC download available"
             if has_secret else "○ not set — File Quarter WC download disabled",
             has_secret),
        ]
        for label, value, ok in rows:
            lbl = self._env_value_labels.get(label)
            if lbl is not None:
                lbl.configure(text=value,
                              fg=(C["dot_filed"] if ok else C["dot_partial"]))

    def _open_templates_folder(self):
        templates_dir = self.state.config.resolve_templates_dir()
        if not templates_dir.exists():
            messagebox.showinfo("Templates folder",
                                f"Folder does not exist yet:\n{templates_dir}")
            return
        self.state.resolve_pdf_open(templates_dir)

    def _health_check(self):
        warnings = self.state.registry.health_check()
        if not warnings:
            messagebox.showinfo("Health check", "✓  All years have gapless numbering.")
        else:
            messagebox.showwarning("Health check — warnings", "\n\n".join(warnings))

    # ----- WooCommerce credentials + monitoring ----------------------

    def _build_wc(self, parent: tk.Frame) -> None:
        """Inline editor for the WooCommerce monitor config.

        Replaces the legacy SettingsDialog that lived in
        ``PipelineScript_Physical_WooCommerceOrderMonitor.py``. Reads
        and writes the same ``AppData/PipelineManager/wc_monitor/config.json``
        the backend monitor uses, so Orders, ``Sync WC`` in Outgoing,
        and ``File Quarter WC`` all pick up new values without an app
        restart (the running monitor's config dict is patched live
        too — see :meth:`_wc_save`).
        """
        C = PALETTE
        wc, monitoring, filters = self._load_wc_settings()

        intro = tk.Label(
            parent,
            text=("Stored in AppData/PipelineManager/wc_monitor/config.json. "
                  "Powers Orders, Outgoing → Sync WC, and File Quarter WC."),
            fg=C["text_dim"], bg=C["card_bg"],
            font=FONTS["small"], wraplength=820, justify="left",
        )
        intro.pack(anchor="w", pady=(0, 8))

        grid = tk.Frame(parent, bg=C["card_bg"])
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        self._wc_vars: Dict[str, tk.Variable] = {}
        self._wc_show_secret: Dict[str, tk.BooleanVar] = {}

        def field(row: int, label: str, var_name: str, value: str,
                  secret: bool = False, width: int = 60) -> None:
            tk.Label(grid, text=label, fg=C["text_dim"], bg=C["card_bg"],
                     font=FONTS["small"], anchor="w", width=22
                     ).grid(row=row, column=0, sticky="w", pady=3)
            var = tk.StringVar(value=value)
            self._wc_vars[var_name] = var
            entry = ttk.Entry(grid, textvariable=var, width=width,
                              show="*" if secret else "")
            entry.grid(row=row, column=1, sticky="ew", pady=3)
            if secret:
                show_var = tk.BooleanVar(value=False)
                self._wc_show_secret[var_name] = show_var

                def _toggle(e=entry, sv=show_var):
                    e.configure(show="" if sv.get() else "*")

                ttk.Checkbutton(grid, text="show", variable=show_var,
                                command=_toggle).grid(row=row, column=2,
                                                      sticky="w", padx=(6, 0))

        field(0, "WordPress URL:", "url", wc.get("url", ""))
        field(1, "Consumer key:", "consumer_key", wc.get("consumer_key", ""))
        field(2, "Consumer secret:", "consumer_secret",
              wc.get("consumer_secret", ""), secret=True)
        field(3, "Monitor secret key:", "monitor_secret_key",
              wc.get("monitor_secret_key", ""), secret=True)

        # ----- separator -----
        sep = tk.Frame(parent, bg=C["card_border"], height=1)
        sep.pack(fill="x", pady=(10, 8))

        # ----- monitoring -----
        mon_row = tk.Frame(parent, bg=C["card_bg"])
        mon_row.pack(fill="x")
        mon_row.columnconfigure(1, weight=1)

        tk.Label(mon_row, text="Poll interval (s):", fg=C["text_dim"],
                 bg=C["card_bg"], font=FONTS["small"], anchor="w", width=22
                 ).grid(row=0, column=0, sticky="w", pady=3)
        self._wc_vars["poll_interval"] = tk.StringVar(
            value=str(monitoring.get("poll_interval", 300)))
        ttk.Spinbox(
            mon_row, from_=30, to=3600, increment=30, width=10,
            textvariable=self._wc_vars["poll_interval"],
        ).grid(row=0, column=1, sticky="w", pady=3)

        tk.Label(mon_row, text="Order statuses:", fg=C["text_dim"],
                 bg=C["card_bg"], font=FONTS["small"], anchor="w", width=22
                 ).grid(row=1, column=0, sticky="w", pady=3)
        self._wc_vars["order_statuses"] = tk.StringVar(
            value=", ".join(filters.get("order_statuses", []) or []))
        ttk.Entry(mon_row, textvariable=self._wc_vars["order_statuses"],
                  width=40).grid(row=1, column=1, sticky="ew", pady=3)
        tk.Label(mon_row, text="comma-separated · e.g. processing, completed",
                 fg=C["text_dim"], bg=C["card_bg"], font=FONTS["small"]
                 ).grid(row=2, column=1, sticky="w")

        # ----- buttons -----
        btn_row = tk.Frame(parent, bg=C["card_bg"])
        btn_row.pack(fill="x", pady=(10, 0))

        self._wc_status_var = tk.StringVar(value="")
        tk.Label(btn_row, textvariable=self._wc_status_var,
                 fg=C["text_dim"], bg=C["card_bg"], font=FONTS["small"]
                 ).pack(side="left")

        primary_button(btn_row, "Save credentials", self._wc_save
                       ).pack(side="right")
        secondary_button(btn_row, "Reload", self._wc_reload
                         ).pack(side="right", padx=(0, 6))

    def _wc_config_path(self) -> Path:
        from invoice_manager.wc_monitor import CONFIG_PATH
        return CONFIG_PATH

    def _load_wc_settings(self) -> tuple[dict, dict, dict]:
        """Return (woocommerce, monitoring, filters) sub-dicts.

        Missing file is fine — returns empty dicts so the editor shows
        blank fields the user can fill in for the first time.
        """
        import json
        p = self._wc_config_path()
        if not p.exists():
            return {}, {}, {}
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.exception(f"Could not read {p}")
            messagebox.showerror("WooCommerce settings",
                                 f"Could not read config:\n{e}")
            return {}, {}, {}
        return (data.get("woocommerce") or {},
                data.get("monitoring") or {},
                data.get("filters") or {})

    def _wc_reload(self) -> None:
        wc, monitoring, filters = self._load_wc_settings()
        self._wc_vars["url"].set(wc.get("url", ""))
        self._wc_vars["consumer_key"].set(wc.get("consumer_key", ""))
        self._wc_vars["consumer_secret"].set(wc.get("consumer_secret", ""))
        self._wc_vars["monitor_secret_key"].set(wc.get("monitor_secret_key", ""))
        self._wc_vars["poll_interval"].set(str(monitoring.get("poll_interval", 300)))
        self._wc_vars["order_statuses"].set(
            ", ".join(filters.get("order_statuses", []) or []))
        self._wc_status_var.set("Reloaded from disk.")

    def _wc_save(self) -> None:
        import json
        p = self._wc_config_path()
        # Read full file so we don't clobber sections we don't edit
        # (folder_structure, documents, logging, processed_orders_file, …)
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logger.exception(f"Could not read {p}")
                messagebox.showerror("WooCommerce settings",
                                     f"Could not read existing config:\n{e}")
                return
        else:
            data = {}

        wc = data.setdefault("woocommerce", {})
        wc.setdefault("api_version", "wc/v3")
        wc["url"] = self._wc_vars["url"].get().strip()
        wc["consumer_key"] = self._wc_vars["consumer_key"].get().strip()
        wc["consumer_secret"] = self._wc_vars["consumer_secret"].get().strip()
        wc["monitor_secret_key"] = self._wc_vars["monitor_secret_key"].get().strip()

        mon = data.setdefault("monitoring", {})
        try:
            mon["poll_interval"] = int(self._wc_vars["poll_interval"].get())
        except (ValueError, TypeError):
            messagebox.showerror("WooCommerce settings",
                                 "Poll interval must be a whole number of seconds.")
            return

        filt = data.setdefault("filters", {})
        filt["order_statuses"] = [
            s.strip()
            for s in self._wc_vars["order_statuses"].get().split(",")
            if s.strip()
        ]

        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
        except Exception as e:
            logger.exception(f"Failed to save {p}")
            messagebox.showerror("WooCommerce settings",
                                 f"Could not save:\n{e}")
            return

        # Patch the running monitor's in-memory config so the next
        # poll uses the new values without restarting the app. If the
        # monitor isn't running yet (creds were empty at startup),
        # offer to start it now.
        monitor = self.state.wc_monitor()
        if monitor is not None and getattr(monitor, "config", None) is not None:
            try:
                monitor.config.config["woocommerce"].update(wc)
                monitor.config.config["monitoring"]["poll_interval"] = mon["poll_interval"]
                monitor.config.config["filters"]["order_statuses"] = filt["order_statuses"]
                self._wc_status_var.set("Saved · running monitor updated.")
            except Exception:
                logger.exception("Could not patch running monitor config")
                self._wc_status_var.set("Saved · restart the app to apply.")
        elif wc["consumer_key"] and wc["consumer_secret"]:
            if messagebox.askyesno(
                "WooCommerce settings",
                "Credentials saved. Start the WooCommerce monitor now?",
            ):
                try:
                    self.state.start_wc_monitor(self.frame.winfo_toplevel())
                    self._wc_status_var.set("Saved · monitor started.")
                except Exception as e:
                    logger.exception("Failed to start monitor after save")
                    messagebox.showerror("WooCommerce settings",
                                         f"Could not start monitor:\n{e}")
                    self._wc_status_var.set("Saved · monitor did not start.")
            else:
                self._wc_status_var.set("Saved · monitor not started.")
        else:
            self._wc_status_var.set("Saved.")

        # Reflect new cred status in the Environment card above
        self._refresh_env_values()

    # ----- Bookkeeping folder structure ------------------------------

    def _build_bookkeeping(self, parent: tk.Frame) -> None:
        """Quarter folder creator — replaces the standalone
        ``PipelineScript_Bookkeeping_FolderStructure.py`` GUI.

        The base directory is the same one InvoiceManager reads
        everywhere (resolve_boekhouding_base → rack_settings), so newly
        created folders line up with the Outgoing / Incoming views.
        """
        C = PALETTE
        cfg = self.state.config
        boek = cfg.resolve_boekhouding_base()

        cur_q = get_current_quarter()
        next_q = get_next_quarter()

        # ----- info line -----
        info = tk.Label(
            parent,
            text=(f"Creates {{year}}/Q{{n}}/Binnenkomend + Uitgaand under the "
                  f"boekhouding root.  Current quarter: {cur_q} · Next: {next_q}"),
            fg=C["text_dim"], bg=C["card_bg"],
            font=FONTS["small"], wraplength=820, justify="left",
        )
        info.pack(anchor="w", pady=(0, 6))

        # ----- year + quarter pickers -----
        pick_row = tk.Frame(parent, bg=C["card_bg"])
        pick_row.pack(fill="x", pady=(0, 6))

        tk.Label(pick_row, text="Year:", fg=C["text_dim"], bg=C["card_bg"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 4))
        self._book_year_var = tk.StringVar(value=str(self.state.year))
        ttk.Spinbox(
            pick_row, from_=2000, to=2099, width=8,
            textvariable=self._book_year_var, state="readonly",
        ).pack(side="left", padx=(0, 12))

        tk.Label(pick_row, text="Quarter:", fg=C["text_dim"], bg=C["card_bg"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 4))
        self._book_quarter_var = tk.StringVar(value=cur_q)
        ttk.Combobox(
            pick_row, values=list(QUARTERS), width=6,
            textvariable=self._book_quarter_var, state="readonly",
        ).pack(side="left")

        # React to year change so the status grid stays in sync
        self._book_year_var.trace_add("write", lambda *_: self._refresh_book_status())

        # ----- status grid -----
        self._book_status_frame = tk.Frame(parent, bg=C["card_bg"])
        self._book_status_frame.pack(fill="x", pady=(0, 8))
        self._book_status_rows: Dict[str, Dict[str, tk.Widget]] = {}
        for q in QUARTERS:
            row = tk.Frame(self._book_status_frame, bg=C["card_bg"])
            row.pack(fill="x", pady=1)
            tk.Label(row, text=q, fg=C["text"], bg=C["card_bg"],
                     font=FONTS["body_bold"], width=4, anchor="w").pack(side="left")
            status_lbl = tk.Label(row, text="—", bg=C["card_bg"],
                                  font=FONTS["body"], anchor="w", width=14)
            status_lbl.pack(side="left", padx=(0, 8))
            path_lbl = tk.Label(row, text="", fg=C["text_dim"], bg=C["card_bg"],
                                font=FONTS["small"], anchor="w")
            path_lbl.pack(side="left", fill="x", expand=True)
            self._book_status_rows[q] = {"status": status_lbl, "path": path_lbl}

        # ----- action buttons -----
        btn_row = tk.Frame(parent, bg=C["card_bg"])
        btn_row.pack(fill="x")
        secondary_button(btn_row, f"Create current ({cur_q})",
                         lambda: self._create_book_quarter(cur_q)
                         ).pack(side="left", padx=(0, 6))
        secondary_button(btn_row, f"Create next ({next_q})",
                         lambda: self._create_book_quarter(next_q)
                         ).pack(side="left", padx=(0, 6))
        primary_button(btn_row, "Create selected",
                       self._create_book_quarter_selected
                       ).pack(side="left", padx=(0, 6))
        secondary_button(btn_row, "Open boekhouding folder",
                         lambda: self.state.resolve_pdf_open(boek)
                         ).pack(side="left", padx=(0, 6))

        self._refresh_book_status()

    def _refresh_book_status(self) -> None:
        C = PALETTE
        cfg = self.state.config
        try:
            year = int(self._book_year_var.get())
        except (ValueError, TypeError):
            return
        boek = cfg.resolve_boekhouding_base()
        for q, status, path in list_quarter_statuses(boek, year):
            row = self._book_status_rows.get(q)
            if not row:
                continue
            txt, color = {
                "complete":   ("✓ complete",   C["dot_filed"]),
                "incomplete": ("⚠ incomplete", C["dot_partial"]),
                "missing":    ("○ missing",    C["text_dim"]),
            }[status]
            row["status"].configure(text=txt, fg=color)
            row["path"].configure(text=str(path))

    def _create_book_quarter(self, quarter: str) -> None:
        try:
            year = int(self._book_year_var.get())
        except (ValueError, TypeError):
            messagebox.showerror("Bookkeeping", "Pick a valid year first.")
            return
        boek = self.state.config.resolve_boekhouding_base()
        if not boek.exists():
            if not messagebox.askyesno(
                "Bookkeeping",
                f"The boekhouding base does not exist yet:\n{boek}\n\nCreate it?",
            ):
                return
            try:
                boek.mkdir(parents=True, exist_ok=True)
            except Exception as e:
                messagebox.showerror("Bookkeeping",
                                     f"Could not create base folder:\n{e}")
                return
        ok, result = create_quarter_folders(boek, year, quarter)
        if not ok:
            logger.error(f"Bookkeeping quarter create failed: {result}")
            messagebox.showerror("Bookkeeping",
                                 f"Could not create folders:\n{result}")
            return
        self._refresh_book_status()
        if messagebox.askyesno(
            "Bookkeeping",
            f"Created folder structure for {quarter} {year}:\n{result}\n\nOpen it?",
        ):
            self.state.resolve_pdf_open(result)

    def _create_book_quarter_selected(self) -> None:
        q = self._book_quarter_var.get() or get_current_quarter()
        self._create_book_quarter(q)

    def _build_debug(self, parent: tk.Frame) -> None:
        C = PALETTE
        tk.Label(
            parent,
            text=(f"Snapshots the invoice DB and files test invoices into "
                  f"Boekhouding/{DEBUG_SUBFOLDER_NAME}/. Exit restores the DB and "
                  f"deletes the test PDFs."),
            fg=C["text_dim"], bg=C["card_bg"],
            font=FONTS["small"], wraplength=820, justify="left",
        ).pack(anchor="w", pady=(0, 8))

        self._debug_status_var = tk.StringVar(value="")
        tk.Label(parent, textvariable=self._debug_status_var,
                 fg=C["text"], bg=C["card_bg"], font=FONTS["body_bold"]
                 ).pack(anchor="w", pady=(0, 8))

        btns = tk.Frame(parent, bg=C["card_bg"])
        btns.pack(anchor="w")
        self._enter_btn = secondary_button(btns, "🐛  Enter debug mode",
                                           self._enter_debug)
        self._enter_btn.pack(side="left")
        self._exit_btn = danger_button(btns, "Exit debug mode",
                                        self._exit_debug)
        self._exit_btn.pack(side="left", padx=(8, 0))

    def on_show(self) -> None:
        self._refresh_debug_status()
        try:
            self._refresh_book_status()
        except AttributeError:
            # Card hasn't been built yet on first mount — build() runs first
            pass

    def on_year_change(self, year: int) -> None:
        try:
            self._book_year_var.set(str(year))
        except AttributeError:
            pass

    def _refresh_debug_status(self) -> None:
        active = self.state.debug_session.is_active()
        if active:
            self._debug_status_var.set(
                f"Status: ACTIVE since {self.state.debug_session.started_at} · "
                f"{len(self.state.debug_session.created_pdfs)} test PDF(s)"
            )
            self._enter_btn.configure(state="disabled")
            self._exit_btn.configure(state="normal")
        else:
            self._debug_status_var.set("Status: inactive")
            self._enter_btn.configure(state="normal")
            self._exit_btn.configure(state="disabled")

    def _enter_debug(self):
        if self.state.debug_session.is_active():
            return
        if not ask_yes_no(
            self.parent,
            "Enter debug mode",
            f"Snapshot the invoice DB and switch to debug mode?\n\n"
            f"While active:\n"
            f"  • Test invoices file into Boekhouding/{DEBUG_SUBFOLDER_NAME}/\n"
            f"  • DB still records each invoice as if it were real\n"
            f"  • Exiting restores the snapshot and deletes test PDFs\n\nContinue?",
        ):
            return
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = DATA_DIR / f"invoices.sqlite.debug_{ts}.bak"
            self.state.registry.backup_db(backup_path)
            self.state.debug_session.start(backup_path)
        except Exception as e:
            logger.exception("Failed to start debug session")
            show_error(self.parent, "Enter debug mode", f"Could not start: {e}")
            return
        self._refresh_debug_status()
        self._on_debug_toggle()
        show_info(
            self.parent,
            "Debug mode",
            "Debug mode ACTIVE. Test invoices will not affect production numbering.",
        )

    def _exit_debug(self):
        ds = self.state.debug_session
        if not ds.is_active():
            return
        pdf_count = len(ds.created_pdfs)
        if not ask_yes_no(
            self.parent,
            "Exit debug mode",
            f"Roll back debug mode?\n\n"
            f"  • Restore the invoice DB from snapshot\n"
            f"  • Delete {pdf_count} test PDF(s)\n\nContinue?",
        ):
            return
        report = self._rollback()
        self._refresh_debug_status()
        self._on_debug_toggle()
        show_info(self.parent, "Exit debug mode", report)

    def _rollback(self) -> str:
        return perform_rollback(self.state.debug_session, self.state.registry)

    def summary(self) -> str:
        return "Settings"
