"""Shared application state for InvoiceManager.

One AppState instance is created at startup and passed to every section.
It exposes:
  - the loaded global_invoice config (companies, paths, credentials)
  - the InvoiceRegistry (sqlite-backed authoritative number store)
  - the WooCommerce monitor backend (auto-started on app launch)
  - the DebugSession (snapshot/restore around test invoices)
  - background legacy-scan results (kicked off at startup)
  - reactive year/company filter chosen in the top bar
  - observer hooks so sections can react to filter changes
    and to monitor/legacy updates without polling
"""

from __future__ import annotations

import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from shared_logging import get_logger

logger = get_logger("invoice_manager.state")


class AppState:
    """Lightweight reactive container for cross-section state."""

    def __init__(self):
        # Lazy imports — keep startup fast.
        from invoice_manager.core.config import load_config
        from invoice_manager.core.registry import InvoiceRegistry
        from invoice_manager.core.debug_mode import DebugSession
        from invoice_manager.core.config import DATA_DIR

        self.config = load_config()
        self.registry = InvoiceRegistry(self.config.resolve_db_path())
        self.debug_session = DebugSession(DATA_DIR / "debug_session.json")

        self.year: int = datetime.now().year
        self.company: str = "All"  # "All" or a company.key
        self.quarter: str = self._current_quarter()

        self._year_listeners: List[Callable[[int], None]] = []
        self._company_listeners: List[Callable[[str], None]] = []
        self._quarter_listeners: List[Callable[[str], None]] = []

        # WooCommerce monitor — built at start_wc_monitor() time
        self._wc_monitor: Optional[Any] = None
        self._monitor_thread: Optional[threading.Thread] = None
        self._monitor_running: bool = False
        self._latest_orders: List[Dict] = []
        self._status_listeners: List[Callable[[str, str], None]] = []
        self._orders_listeners: List[Callable[[List[Dict]], None]] = []

        # Legacy invoice scan — started in background at app launch
        self._legacy_results: List[Any] = []
        self._legacy_reg_status: Dict[str, str] = {}
        self._legacy_scan_state: str = "idle"  # idle | scanning | done | failed
        self._legacy_scan_error: Optional[str] = None
        self._legacy_listeners: List[Callable[[], None]] = []

        # Tk root — set by start_wc_monitor / start_legacy_scan so background
        # threads can marshal callbacks onto the main loop.
        self._root: Optional[tk.Misc] = None

    # ===== year =======================================================

    def set_year(self, year: int) -> None:
        if year == self.year:
            return
        self.year = year
        for cb in list(self._year_listeners):
            try:
                cb(year)
            except Exception:
                logger.exception("year listener failed")

    def on_year_change(self, cb: Callable[[int], None]) -> None:
        self._year_listeners.append(cb)

    def available_years(self) -> List[int]:
        """Years the user can pick in the top bar.

        Union of (registry years) ∪ (legacy-scan years, if the background
        scan is done) ∪ (the currently-selected year, always). Sorted
        newest-first.
        """
        years_set = set(self.registry.list_years() or [])
        if self._legacy_scan_state == "done":
            for r in self._legacy_results:
                if getattr(r, "year", None):
                    years_set.add(r.year)
        years_set.add(self.year)
        return sorted(years_set, reverse=True) or [self.year]

    # ===== quarter ====================================================

    @staticmethod
    def _current_quarter() -> str:
        return f"q{(datetime.now().month - 1) // 3 + 1}"

    def set_quarter(self, quarter: str) -> None:
        if quarter == self.quarter:
            return
        self.quarter = quarter
        for cb in list(self._quarter_listeners):
            try:
                cb(quarter)
            except Exception:
                logger.exception("quarter listener failed")

    def on_quarter_change(self, cb: Callable[[str], None]) -> None:
        self._quarter_listeners.append(cb)

    # ===== company ====================================================

    def set_company(self, company_key: str) -> None:
        if company_key == self.company:
            return
        self.company = company_key
        for cb in list(self._company_listeners):
            try:
                cb(company_key)
            except Exception:
                logger.exception("company listener failed")

    def on_company_change(self, cb: Callable[[str], None]) -> None:
        self._company_listeners.append(cb)

    def company_keys(self) -> List[str]:
        return [c.key for c in self.config.companies]

    def company_display(self, key: str) -> str:
        if key == "All":
            return "All"
        try:
            c = self.config.get_company(key)
            return f"{c.key} — {c.display_name}"
        except KeyError:
            return key

    # ===== WooCommerce monitor — auto-started =========================

    def start_wc_monitor(self, root: tk.Misc) -> None:
        """Initialize the OrderMonitor backend and start polling.

        Called once during InvoiceManager construction. Silently no-ops if:
          - the backend file isn't importable
          - the WC consumer_key/secret aren't configured

        Both are valid "the user hasn't set this up yet" states — we
        don't want to spam dialogs at startup.
        """
        if self._monitor_running:
            return
        self._root = root
        try:
            from invoice_manager.wc_monitor import (
                Config as WCConfig, OrderMonitor,
            )
        except Exception as e:
            logger.info(f"WC monitor backend not available: {e}")
            return
        try:
            wc_cfg = WCConfig()
            creds = wc_cfg.config["woocommerce"]
            if not creds.get("consumer_key") or not creds.get("consumer_secret"):
                logger.info("WC creds not configured — monitor will not start")
                return
            monitor = OrderMonitor(wc_cfg)
        except Exception as e:
            logger.warning(f"Could not build WC monitor: {e}")
            return

        def _on_status(msg: str, level: str = "info"):
            if self._root is not None:
                self._root.after(0, self._dispatch_status, msg, level)

        def _on_orders(orders: List[Dict]):
            if self._root is not None:
                self._root.after(0, self._dispatch_orders, orders)

        monitor.set_callback(_on_status)
        monitor.set_order_list_callback(_on_orders)

        # The legacy InvoiceFiler derives library_base from
        # rack_settings.get_active_base(), which can disagree with
        # global_invoice config.paths.boekhouding_base. Pin it to the
        # path the rest of the app reads so "Orders → File quarter
        # invoices…" lands in the same place as everything else.
        try:
            monitor.invoice_filer.library_base = self.config.resolve_boekhouding_base()
        except Exception:
            logger.exception("Could not pin monitor library_base")

        self._wc_monitor = monitor
        self._monitor_running = True
        self._monitor_thread = threading.Thread(
            target=monitor.start_monitoring, daemon=True,
        )
        self._monitor_thread.start()
        logger.info("WC monitor started in background")

    def wc_monitor(self):
        """Return the OrderMonitor instance (or None if not running)."""
        return self._wc_monitor

    def wc_monitor_running(self) -> bool:
        return self._monitor_running

    def latest_orders(self) -> List[Dict]:
        return list(self._latest_orders)

    def on_wc_status(self, cb: Callable[[str, str], None]) -> None:
        self._status_listeners.append(cb)

    def on_wc_orders(self, cb: Callable[[List[Dict]], None]) -> None:
        self._orders_listeners.append(cb)

    def request_wc_refresh(self) -> None:
        """Manually trigger a check_orders() outside the poll interval."""
        if self._wc_monitor is None:
            return
        threading.Thread(target=self._wc_monitor.check_orders,
                         daemon=True).start()

    def _dispatch_status(self, msg: str, level: str) -> None:
        for cb in list(self._status_listeners):
            try:
                cb(msg, level)
            except Exception:
                logger.exception("wc status listener failed")

    def _dispatch_orders(self, orders: List[Dict]) -> None:
        self._latest_orders = orders
        for cb in list(self._orders_listeners):
            try:
                cb(orders)
            except Exception:
                logger.exception("wc orders listener failed")

    # ===== Legacy invoice scan — auto-started =========================

    def start_legacy_scan(self, root: tk.Misc) -> None:
        """Background-scan Boekhouding/ for legacy PDFs.

        Called once at app launch. Results cached in-memory for the
        session; sections subscribe via `on_legacy_scan(cb)` to be
        notified when scan_state transitions to 'done' or 'failed'.
        """
        if self._legacy_scan_state != "idle":
            return
        self._root = root
        self._legacy_scan_state = "scanning"
        threading.Thread(target=self._legacy_worker,
                         daemon=True, name="legacy-scan").start()
        logger.info("Legacy scan started in background")

    def _legacy_worker(self) -> None:
        try:
            from invoice_manager.core.legacy_scanner import scan_boekhouding
            boek = self.config.resolve_boekhouding_base()
            known = {c.output_prefix.upper() for c in self.config.companies}
            results = scan_boekhouding(boek, known)
            all_inv = self.registry.list_invoices(limit=9999)
            known_paths = {inv["pdf_path"] for inv in all_inv if inv.get("pdf_path")}
            known_slots = {(inv["year"], inv["company_key"], inv["sequence"])
                           for inv in all_inv}
            reg_status: Dict[str, str] = {}
            for r in results:
                pk = str(r.path)
                if pk in known_paths or (r.year, r.company_key, r.sequence) in known_slots:
                    reg_status[pk] = "in registry"
                else:
                    reg_status[pk] = "new"
            self._legacy_results = results
            self._legacy_reg_status = reg_status
            self._legacy_scan_state = "done"
            self._legacy_scan_error = None
            logger.info(f"Legacy scan done: {len(results)} PDF(s) found")
        except Exception as e:
            logger.exception("Legacy scan failed")
            self._legacy_scan_state = "failed"
            self._legacy_scan_error = str(e)
        finally:
            if self._root is not None:
                self._root.after(0, self._dispatch_legacy)

    def _dispatch_legacy(self) -> None:
        for cb in list(self._legacy_listeners):
            try:
                cb()
            except Exception:
                logger.exception("legacy listener failed")

    def on_legacy_scan(self, cb: Callable[[], None]) -> None:
        self._legacy_listeners.append(cb)

    def legacy_results(self) -> List[Any]:
        return list(self._legacy_results)

    def legacy_reg_status(self) -> Dict[str, str]:
        return dict(self._legacy_reg_status)

    def legacy_scan_state(self) -> str:
        return self._legacy_scan_state

    def legacy_scan_error(self) -> Optional[str]:
        return self._legacy_scan_error

    def rescan_legacy(self) -> None:
        """Force a fresh legacy scan, invalidating cached results."""
        if self._legacy_scan_state == "scanning":
            return
        self._legacy_scan_state = "idle"
        if self._root is not None:
            self.start_legacy_scan(self._root)

    def mark_legacy_imported(self, path: str) -> None:
        """Update cached scan state after an import succeeds.

        Cheaper than re-scanning the whole tree.
        """
        self._legacy_reg_status[path] = "in registry"

    # ===== helpers ====================================================

    def resolve_pdf_open(self, path: str | Path) -> None:
        """Open a file with the OS default handler."""
        import os, subprocess, sys
        p = str(path)
        try:
            if sys.platform == "win32":
                os.startfile(p)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            else:
                subprocess.Popen(["xdg-open", p])
        except Exception:
            logger.exception(f"Could not open {p}")

    def wc_monitor_available(self) -> bool:
        """True if the backend file is importable. Doesn't say anything
        about whether the monitor is *running* — for that, use
        wc_monitor_running().
        """
        try:
            from invoice_manager.wc_monitor import Config  # noqa: F401
            return True
        except Exception:
            return False
