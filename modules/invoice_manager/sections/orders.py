"""Orders — WooCommerce order browser + per-order actions.

The OrderMonitor backend auto-starts in AppState when the app launches,
so this section is a pure subscriber: it asks AppState for the latest
orders + activity, and exposes per-order actions (open folder, view
invoice, view label, change invoice #, change status, create/repair
folder, file quarter invoices).
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk
from shared_scrollbar_style import CleanScrolledText
from typing import Dict, List, Optional

from shared_logging import get_logger

from invoice_manager.sections.base import Section
from invoice_manager.theme import PALETTE, FONTS
from invoice_manager.widgets.buttons import primary_button, secondary_button
from invoice_manager.widgets.card import Card
from invoice_manager.widgets.chip import ChipGroup
from invoice_manager.widgets.inputs import make_entry, make_spinbox
from invoice_manager.widgets.tree import make_treeview

logger = get_logger("invoice_manager.orders")


class OrdersSection(Section):
    title = "Orders"
    sidebar_key = "orders"
    sidebar_icon = "🛒"

    QUARTERS = ["q1", "q2", "q3", "q4", "year"]

    def __init__(self, parent, state):
        super().__init__(parent, state)
        self._orders_cache: List[Dict] = []
        self._count_var = tk.StringVar(value="")
        self._quarter = state.quarter
        self._search = tk.StringVar()

        # Subscribe to AppState — works even before this section is built.
        state.on_wc_orders(self._on_orders_update)
        state.on_wc_status(self._on_status_update)
        state.on_quarter_change(self._on_state_quarter_change)
        state.on_year_change(
            lambda _y: self._render_orders() if self._built_tree() else None
        )

    # ----- build -------------------------------------------------------

    def build(self, root: tk.Frame) -> None:
        C = PALETTE
        root.configure(bg=C["bg"])

        if not self.state.wc_monitor_available():
            self._build_unavailable(root)
            return

        wrap = tk.Frame(root, bg=C["bg"], padx=20, pady=14)
        wrap.pack(fill="both", expand=True)

        # ----- Row 1: Quarter chips + Search (mirrors Outgoing) -----
        row1 = tk.Frame(wrap, bg=C["bg"])
        row1.pack(fill="x")
        tk.Label(row1, text="Quarter", fg=C["text_dim"], bg=C["bg"],
                 font=FONTS["small"]).pack(side="left", padx=(0, 8))
        self._q_chips = ChipGroup(row1, on_change=self._set_quarter)
        for q, label in [("q1", "Q1"), ("q2", "Q2"),
                         ("q3", "Q3"), ("q4", "Q4"), ("year", "Year")]:
            self._q_chips.add(q, label, selected=(q == self._quarter))
        self._q_chips.pack(side="left")

        tk.Label(row1, text="Search", fg=C["label_fg"], bg=C["bg"],
                 font=FONTS["label"]).pack(side="left", padx=(24, 8))
        make_entry(row1, self._search, width=26).pack(side="left")
        self._search.trace_add("write", lambda *_: self._render_orders())

        # ----- Row 2: monitor status + refresh + count -----
        row2 = tk.Frame(wrap, bg=C["bg"])
        row2.pack(fill="x", pady=(8, 0))

        # "● live" stays ambient — no big Start/Stop buttons. If the
        # monitor isn't actually running (no creds) we show "○ inactive".
        running = self.state.wc_monitor_running()
        self._live_text = tk.StringVar(
            value=("● live · polls every "
                   f"{self._poll_interval_label()} min" if running else
                   "○ monitor inactive — configure WC credentials in Settings")
        )
        live_color = C["dot_filed"] if running else C["text_dim"]
        tk.Label(
            row2, textvariable=self._live_text,
            fg=live_color, bg=C["bg"], font=FONTS["body"],
        ).pack(side="left")

        secondary_button(row2, "↻  Refresh now",
                         self.state.request_wc_refresh, padx=10, pady=2
                         ).pack(side="left", padx=(20, 0))

        tk.Label(row2, textvariable=self._count_var,
                 fg=C["text_dim"], bg=C["bg"], font=FONTS["small"]
                 ).pack(side="right")

        # ----- Orders table -----
        tree_wrap = tk.Frame(wrap, bg=C["bg"])
        tree_wrap.pack(fill="both", expand=True, pady=(10, 0))
        cols = ("order_num", "invoice_num", "date", "customer",
                "city", "status", "total", "processed")
        tree = make_treeview(tree_wrap, cols, height=14, show="headings")
        for c, label, w, anchor in [
            ("order_num",   "Order #",   80,  "center"),
            ("invoice_num", "Invoice #", 80,  "center"),
            ("date",        "Date",      100, "center"),
            ("customer",    "Customer",  220, "w"),
            ("city",        "City",      130, "w"),
            ("status",      "Status",    110, "center"),
            ("total",       "Total",     90,  "e"),
            ("processed",   "Folder",    70,  "center"),
        ]:
            tree.heading(c, text=label)
            tree.column(c, width=w, anchor=anchor)
        for status, color in [
            ("processing", "#3b82f6"), ("completed", "#22c55e"),
            ("on-hold",    "#f59e0b"), ("cancelled", "#ef4444"),
            ("refunded",   "#a855f7"), ("pending",   "#8b949e"),
        ]:
            tree.tag_configure(status, foreground=color)
        tree.bind("<Double-1>", self._on_order_double_click)
        tree.bind("<<TreeviewSelect>>", lambda _e: self._refresh_action_bar())
        self._tree = tree

        # ----- Selection-aware action bar -----
        action_bar = tk.Frame(wrap, bg=C["card_border"], padx=12, pady=8)
        action_bar.pack(fill="x", pady=(10, 0))
        self._sel_label = tk.Label(
            action_bar, text="No selection",
            fg=C["text_dim"], bg=C["card_border"], font=FONTS["small"],
        )
        self._sel_label.pack(side="left")
        self._action_btns = tk.Frame(action_bar, bg=C["card_border"])
        self._action_btns.pack(side="right")
        self._refresh_action_bar()

        # ----- Quarter bulk action -----
        bulk = tk.Frame(wrap, bg=C["bg"])
        bulk.pack(fill="x", pady=(8, 0))
        secondary_button(bulk, "📦  File quarter invoices…",
                         self._file_quarter_dialog).pack(side="left")

        # ----- Activity log -----
        log_card = Card(wrap, title="Activity log")
        log_card.pack(fill="x", pady=(12, 0))
        self._log = CleanScrolledText(
            log_card.body, height=6, wrap=tk.WORD,
            bg=C["bg_input"], fg=C["text"],
            insertbackground=C["text"], relief=tk.FLAT, font=FONTS["mono"],
            highlightthickness=1, highlightbackground=C["input_border"],
        )
        self._log.pack(fill="both", expand=True)
        self._log.tag_config("info", foreground=C["text"])
        self._log.tag_config("success", foreground=C["dot_filed"])
        self._log.tag_config("warning", foreground=C["dot_partial"])
        self._log.tag_config("error", foreground=C["dot_missing"])

    def _build_unavailable(self, root: tk.Frame) -> None:
        C = PALETTE
        wrap = tk.Frame(root, bg=C["bg"], padx=40, pady=40)
        wrap.pack(fill="both", expand=True)
        tk.Label(
            wrap,
            text=("WooCommerce monitor backend not available.\n\n"
                  "The OrderMonitor backend lives in\n"
                  "PipelineScript_Physical_WooCommerceOrderMonitor.py — "
                  "please make sure that file is still present in modules/."),
            fg=C["dot_partial"], bg=C["bg"], font=FONTS["body"],
            justify="left",
        ).pack(anchor="w")

    def _poll_interval_label(self) -> str:
        try:
            m = self.state.wc_monitor()
            secs = m.config.config["monitoring"]["poll_interval"] if m else 300
            return str(secs // 60)
        except Exception:
            return "5"

    # ----- AppState observers -----------------------------------------

    def on_show(self) -> None:
        # Pull whatever AppState already has cached; arriving fresh means
        # the table appears immediately without waiting for the next poll.
        cached = self.state.latest_orders()
        if cached and not self._orders_cache:
            self._on_orders_update(cached)

    def _on_orders_update(self, orders: List[Dict]) -> None:
        self._orders_cache = orders
        if self.frame is not None and self._built_tree():
            self._render_orders()

    def _on_status_update(self, message: str, level: str) -> None:
        if not self._built_tree():
            return
        ts = datetime.now().strftime("%H:%M:%S")
        self._log.insert(tk.END, f"[{ts}] {message}\n", level)
        self._log.see(tk.END)

    def _built_tree(self) -> bool:
        return hasattr(self, "_tree") and self._tree.winfo_exists()

    # ----- order table -------------------------------------------------

    def _render_orders(self) -> None:
        monitor = self.state.wc_monitor()
        sel_ids = set()
        for iid in self._tree.selection():
            vals = self._tree.item(iid, "values")
            if vals:
                sel_ids.add(vals[0])
        self._tree.delete(*self._tree.get_children())

        self._update_chip_counts()
        filtered = self._filter_orders(self._orders_cache)

        for order in filtered:
            order_number = str(order.get("number", order["id"]))
            date_str = order.get("date_created", "")[:10]
            billing = order.get("billing", {})
            customer = f"{billing.get('first_name', '')} {billing.get('last_name', '')}".strip()
            city = billing.get("city", "")
            status = order.get("status", "")
            currency = order.get("currency_symbol", order.get("currency", ""))
            total = f"{currency}{order.get('total', '0.00')}"
            is_processed = "✓" if (monitor and
                                    monitor.tracker.is_processed(str(order["id"]))) else ""

            invoice_num = ""
            for meta in order.get("meta_data", []):
                if meta.get("key") == "_wcpdf_invoice_number":
                    invoice_num = str(meta.get("value", ""))
                    break

            new_id = self._tree.insert(
                "", "end", values=(
                    order_number, invoice_num, date_str, customer,
                    city, status, total, is_processed,
                ), tags=(status,),
            )
            if order_number in sel_ids:
                self._tree.selection_add(new_id)

        self._count_var.set(f"{len(filtered)} of {len(self._orders_cache)} order(s)")
        self._refresh_action_bar()
        self._mark_status_dirty()

    # ----- filter helpers ---------------------------------------------

    def _order_quarter(self, order: Dict) -> Optional[int]:
        """Return 1-4 for the quarter of order.date_created (in state.year),
        or None if the order is from a different year or has no date.
        """
        date_str = order.get("date_created", "")[:10]
        if len(date_str) < 7:
            return None
        try:
            y, m = int(date_str[:4]), int(date_str[5:7])
        except ValueError:
            return None
        if y != self.state.year:
            return None
        return (m - 1) // 3 + 1

    def _matches_search(self, order: Dict, needle: str) -> bool:
        if not needle:
            return True
        billing = order.get("billing", {})
        invoice_num = ""
        for meta in order.get("meta_data", []):
            if meta.get("key") == "_wcpdf_invoice_number":
                invoice_num = str(meta.get("value", ""))
                break
        hay = " ".join(str(x) for x in (
            order.get("number", order.get("id", "")),
            invoice_num,
            f"{billing.get('first_name', '')} {billing.get('last_name', '')}",
            billing.get("city", ""),
        )).lower()
        return needle in hay

    def _filter_orders(self, orders: List[Dict]) -> List[Dict]:
        needle = self._search.get().strip().lower()
        out: List[Dict] = []
        for o in orders:
            q = self._order_quarter(o)
            if q is None:
                continue  # different year — hide
            if self._quarter != "year" and f"q{q}" != self._quarter:
                continue
            if not self._matches_search(o, needle):
                continue
            out.append(o)
        return out

    def _update_chip_counts(self) -> None:
        per_q = {k: 0 for k in self.QUARTERS}
        for o in self._orders_cache:
            q = self._order_quarter(o)
            if q is None:
                continue
            per_q[f"q{q}"] += 1
            per_q["year"] += 1
        self._q_chips.set_counts({k: v if v else None for k, v in per_q.items()})

    def _on_state_quarter_change(self, q: str) -> None:
        self._quarter = q
        if self.frame is not None and self._built_tree():
            self._q_chips.select(q)

    def _set_quarter(self, q: str) -> None:
        self._quarter = q
        self.state.set_quarter(q)
        self._render_orders()

    def _selected_orders(self) -> List[Dict]:
        sel = self._tree.selection()
        if not sel:
            return []
        order_nums = set()
        for iid in sel:
            vals = self._tree.item(iid, "values")
            if vals: order_nums.add(vals[0])
        return [o for o in self._orders_cache
                if str(o.get("number", o["id"])) in order_nums]

    def _refresh_action_bar(self) -> None:
        for child in self._action_btns.winfo_children():
            child.destroy()
        selected = self._selected_orders()
        if not selected:
            self._sel_label.configure(text="Select an order to act on it")
            return
        self._sel_label.configure(text=f"{len(selected)} order(s) selected")

        if len(selected) == 1:
            secondary_button(self._action_btns, "Open folder",
                             self._open_folder).pack(side="left", padx=3)
            secondary_button(self._action_btns, "View invoice",
                             self._view_invoice).pack(side="left", padx=3)
            secondary_button(self._action_btns, "View label",
                             self._view_label).pack(side="left", padx=3)
            secondary_button(self._action_btns, "Change invoice #",
                             self._change_invoice_number).pack(side="left", padx=3)

        secondary_button(self._action_btns, "Create / repair folder",
                         self._create_folders).pack(side="left", padx=3)

        tk.Label(self._action_btns, text="Status →", fg=PALETTE["text_dim"],
                 bg=PALETTE["card_border"], font=FONTS["small"]
                 ).pack(side="left", padx=(10, 4))
        status_var = tk.StringVar(value="completed")
        ttk.Combobox(
            self._action_btns, textvariable=status_var,
            values=["processing", "completed", "on-hold",
                    "cancelled", "refunded", "pending"],
            state="readonly", width=12, style="InvApp.TCombobox",
        ).pack(side="left", padx=(0, 4))
        primary_button(
            self._action_btns, "Apply",
            lambda v=status_var: self._change_status(v.get()),
        ).pack(side="left")

    # ----- per-order actions ------------------------------------------

    def _on_order_double_click(self, _e):
        if self._selected_orders():
            self._open_folder()

    def _open_folder(self):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        order = selected[0]
        tracker_data = monitor.tracker.processed_orders.get(str(order["id"]))
        if tracker_data and tracker_data.get("folder_path"):
            folder = Path(tracker_data["folder_path"])
            if folder.exists():
                self.state.resolve_pdf_open(folder)
                return
        found = monitor._find_order_folder(order)
        if found and found.exists():
            self.state.resolve_pdf_open(found)
            return
        self._on_status_update("No folder found for this order", "warning")

    def _view_invoice(self):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        order = selected[0]
        order_number = order.get("number", order["id"])
        order_folder = monitor._find_order_folder(order)
        if not order_folder:
            try:
                order_folder = monitor.doc_manager.create_order_folder(order)
            except Exception as e:
                self._on_status_update(f"Could not create folder: {e}", "error")
                return
        # Invoices live in _LIBRARY (always present for orders), next to Order Details, under one
        # canonical name — matches resolve_or_fetch_invoice's convention
        # (order_project_linker.py) so there's a single local copy per
        # order rather than a differently-named duplicate each time.
        from shared_project_docs import ensure_library_dir
        library_dir = ensure_library_dir(order_folder)
        invoice_path = library_dir / f"Invoice_{order_number}.pdf"
        if invoice_path.exists():
            self.state.resolve_pdf_open(invoice_path)
            return
        secret = monitor.config.config["woocommerce"].get("monitor_secret_key", "")
        if not secret:
            self._on_status_update("monitor_secret_key not configured", "error")
            return
        self._on_status_update(f"Downloading invoice for #{order_number}…", "info")
        def go():
            if monitor.wc_client.download_invoice_pdf(order["id"], invoice_path):
                self._on_status_update(f"✓ Saved {invoice_path.name}", "success")
                self.state.resolve_pdf_open(invoice_path)
            else:
                self._on_status_update(
                    f"✗ Failed to download invoice for #{order_number}", "error")
        threading.Thread(target=go, daemon=True).start()

    def _view_label(self):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        order = selected[0]
        order_number = order.get("number", order["id"])
        order_folder = monitor.doc_manager.create_order_folder(order)
        outgoing = order_folder / "03_Outgoing"
        outgoing.mkdir(exist_ok=True)
        filename = monitor.config.config["documents"]["label_filename"].format(
            order_number=order_number, order_id=order["id"],
        )
        label_path = outgoing / filename
        if label_path.exists():
            self.state.resolve_pdf_open(label_path)
            return
        self._on_status_update(f"Fetching label for #{order_number}…", "info")
        def go():
            import requests
            wc = monitor.wc_client
            label_url = wc.get_bpost_label_url(order)
            if not label_url and wc.has_bpost_shipping(order):
                label_url = wc.get_bpost_label_from_db(order["id"])
            if not label_url:
                self._on_status_update(f"No label available for #{order_number} yet",
                                       "warning")
                return
            try:
                r = requests.get(label_url, timeout=30)
                r.raise_for_status()
                with open(label_path, "wb") as f:
                    f.write(r.content)
                self._on_status_update(f"✓ Saved {label_path.name}", "success")
                self.state.resolve_pdf_open(label_path)
            except Exception as e:
                self._on_status_update(f"Failed to download label: {e}", "error")
        threading.Thread(target=go, daemon=True).start()

    def _change_invoice_number(self):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        order = selected[0]
        order_number = order.get("number", order["id"])
        current = ""
        for meta in order.get("meta_data", []):
            if meta.get("key") == "_wcpdf_invoice_number":
                current = str(meta.get("value", ""))
                break
        new_number = simpledialog.askstring(
            f"Change invoice # — Order #{order_number}",
            "New invoice number:", initialvalue=current, parent=self.frame,
        )
        if not new_number or new_number == current:
            return
        self._on_status_update(
            f"Updating invoice # for #{order_number} to {new_number}…", "info")
        def go():
            r = monitor.wc_client.update_invoice_number(order["id"], new_number)
            if r:
                for meta in order.get("meta_data", []):
                    if meta.get("key") == "_wcpdf_invoice_number":
                        meta["value"] = new_number
                        break
                else:
                    order.setdefault("meta_data", []).append(
                        {"key": "_wcpdf_invoice_number", "value": new_number})
                self._on_status_update(f"✓ Updated #{order_number} → {new_number}",
                                       "success")
            else:
                self._on_status_update(f"✗ Failed to update #{order_number}", "error")
        threading.Thread(target=go, daemon=True).start()

    def _create_folders(self):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        def go():
            for order in selected:
                order_id = str(order["id"])
                folder = monitor.doc_manager.create_order_folder(order)
                if monitor.tracker.is_processed(order_id):
                    self._on_status_update(f"Ensured directory: {folder.name}",
                                           "success")
                else:
                    monitor.process_order(order)
            if self._orders_cache:
                self.frame.after(0, self._render_orders)
        threading.Thread(target=go, daemon=True).start()

    def _change_status(self, new_status: str):
        selected = self._selected_orders()
        monitor = self.state.wc_monitor()
        if not selected or monitor is None:
            return
        if not messagebox.askyesno(
            "Confirm",
            f"Change status of {len(selected)} order(s) to '{new_status}'?",
        ):
            return
        def go():
            for order in selected:
                r = monitor.wc_client.update_order_status(order["id"], new_status)
                num = order.get("number", order["id"])
                if r:
                    self._on_status_update(f"#{num} → {new_status}", "success")
                else:
                    self._on_status_update(f"Failed to update #{num}", "error")
            monitor.check_orders()
        threading.Thread(target=go, daemon=True).start()

    def _file_quarter_dialog(self):
        monitor = self.state.wc_monitor()
        if monitor is None:
            return
        win = tk.Toplevel(self.frame)
        win.title("File quarter invoices")
        win.configure(bg=PALETTE["bg"])
        win.transient(self.frame)
        win.grab_set()
        f = tk.Frame(win, bg=PALETTE["bg"], padx=18, pady=14)
        f.pack()
        tk.Label(f, text="Select quarter:", fg=PALETTE["text"],
                 bg=PALETTE["bg"], font=FONTS["body"]
                 ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 10))
        now = datetime.now()
        year_var = tk.StringVar(value=str(now.year))
        q_var = tk.StringVar(value=str((now.month - 1) // 3 + 1))
        tk.Label(f, text="Year", fg=PALETTE["label_fg"], bg=PALETTE["bg"],
                 font=FONTS["label"]).grid(row=1, column=0, sticky="w")
        make_spinbox(f, year_var, from_=2020, to=2040, width=6
                     ).grid(row=1, column=1, padx=(8, 20))
        tk.Label(f, text="Quarter", fg=PALETTE["label_fg"], bg=PALETTE["bg"],
                 font=FONTS["label"]).grid(row=1, column=2, sticky="w")
        make_spinbox(f, q_var, from_=1, to=4, width=4
                     ).grid(row=1, column=3, padx=(8, 0))

        def run():
            win.destroy()
            try:
                year = int(year_var.get())
                q = int(q_var.get())
            except ValueError:
                return
            threading.Thread(
                target=monitor.file_quarter_invoices,
                args=(year, q), daemon=True,
            ).start()

        btns = tk.Frame(f, bg=PALETTE["bg"])
        btns.grid(row=2, column=0, columnspan=4, sticky="e", pady=(14, 0))
        secondary_button(btns, "Cancel", win.destroy).pack(side="right", padx=(8, 0))
        primary_button(btns, "Run", run).pack(side="right")

    def reload(self) -> None:
        self.state.request_wc_refresh()

    def summary(self) -> str:
        if not self.state.wc_monitor_running():
            return "Orders · monitor inactive (no WC credentials)"
        n_cached = len(self._orders_cache)
        n_visible = len(self._filter_orders(self._orders_cache)) if n_cached else 0
        processing = sum(1 for o in self._orders_cache
                         if o.get("status") == "processing")
        scope = (f"Q{self._quarter[1]} {self.state.year}"
                 if self._quarter.startswith("q") else f"Year {self.state.year}")
        return (f"{scope} · {n_visible} shown · "
                f"{n_cached} loaded · {processing} need shipping")
