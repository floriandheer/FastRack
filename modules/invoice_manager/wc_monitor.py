"""WooCommerce monitor backend — Config, OrderMonitor, WooCommerceClient,
DocumentManager, InvoiceFiler, ProcessedOrdersTracker.

Imported by ``invoice_manager/state.py`` to power the Orders section.
The legacy Tk GUI (OrderMonitorGUI, SettingsDialog, the ``main()``
launcher in PipelineScript_Physical_WooCommerceOrderMonitor.py) is
superseded by Invoices → Orders + Invoices → Settings → WooCommerce.

User data (config.json, processed_orders.json) lives under the
per-user AppData folder, matching how ``invoice_manager/core`` already
handles ``global_invoice/config.json``.
"""

import os
import re
import sys
import shutil
import requests
from requests.auth import HTTPBasicAuth
import json
import time
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import base64

from shared_logging import get_logger
from rack_settings import get_rack_settings
from shared_project_db import ProjectDatabase
from shared_appdata import get_appdata_path

logger = get_logger("invoice_manager.wc_monitor")


def _get_user_data_dir() -> Path:
    """Per-user AppData folder for the WC monitor.

    Matches the pattern used by ``invoice_manager.core.config._get_user_data_dir``
    so all InvoiceManager user data lives side-by-side.
    """
    return get_appdata_path() / "wc_monitor"


DATA_DIR = _get_user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
PROCESSED_ORDERS_PATH = DATA_DIR / "processed_orders.json"

# Legacy in-repo data dir — files here are moved to DATA_DIR on first run.
_LEGACY_DATA_DIR = Path(__file__).resolve().parent.parent / "woocommerce_monitor_data"
_MIGRATABLE_FILENAMES = ("config.json", "processed_orders.json")


def _migrate_legacy_data() -> None:
    """One-shot migration: move in-repo data files into the user data dir.

    Runs on every Config() construction but is a no-op once the legacy
    files are gone. Never overwrites a file that already exists at the
    new location.
    """
    if not _LEGACY_DATA_DIR.exists():
        return
    moved: List[str] = []
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for filename in _MIGRATABLE_FILENAMES:
        src = _LEGACY_DATA_DIR / filename
        dst = DATA_DIR / filename
        if not src.exists():
            continue
        if dst.exists():
            logger.warning(
                f"Legacy file {src} exists but {dst} already does too — "
                f"leaving legacy in place; resolve manually."
            )
            continue
        try:
            shutil.move(str(src), str(dst))
            moved.append(filename)
        except Exception as e:
            logger.error(f"Failed to migrate {src} -> {dst}: {e}")
    if moved:
        logger.info(
            f"Migrated WC monitor data files to {DATA_DIR}: {moved}. "
            f"Legacy folder {_LEGACY_DATA_DIR} can be removed."
        )

# ====================================
# CONFIGURATION
# ====================================

class Config:
    """Configuration manager for WooCommerce order monitoring"""

    def __init__(self):
        # Per-user AppData under PipelineManager/wc_monitor/.
        # Legacy in-repo data (modules/woocommerce_monitor_data/) is
        # migrated automatically on first construction.
        _migrate_legacy_data()
        self.data_dir = DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = CONFIG_PATH
        self.config = self.load_config()

    def load_config(self) -> Dict:
        """Load configuration from file"""
        default_config = {
            "woocommerce": {
                "url": "https://yourdomain.com",
                "consumer_key": "",
                "consumer_secret": "",
                "api_version": "wc/v3",
                "monitor_secret_key": ""
            },
            "monitoring": {
                "poll_interval": 300,
                "check_orders_since_hours": 48,
                "base_directory": get_rack_settings().get_work_path("Physical").replace('\\', '/') + "/Order",
                "processed_orders_file": str(self.data_dir / "processed_orders.json"),
            },
            "folder_structure": {
                "naming_format": "{customer_name}_{order_number}",  # Placeholders: order_number, order_id, customer_name
                "include_date": True,  # Prepends YYYY-MM-DD_ (uses order's date_created, falls back to today)
            },
            "documents": {
                "invoice_filename": "Invoice_{order_number}.pdf",
                "label_filename": "Shipping_Label_{order_number}.pdf",
                "order_details_filename": "Order_Details_{order_number}.txt"
            },
            "filters": {
                "order_statuses": ["processing", "completed"],  # Which order statuses to monitor
                "shipping_methods": [],  # Empty = all methods. Or: ["bpost", "flat_rate"]
                "payment_methods": []  # Empty = all methods
            },
            "logging": {
                "enabled": True,
                "log_file": str(get_appdata_path() / "logs" / "woocommerce_monitor.log"),
                "log_level": "INFO"
            }
        }

        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded_config = json.load(f)
                    self._merge_config(default_config, loaded_config)
                    # Reset stale / empty path overrides to defaults so old
                    # in-repo paths from before the AppData migration don't
                    # keep getting used by Tracker / log handlers.
                    mon = default_config.get("monitoring", {})
                    if not mon.get("base_directory"):
                        mon["base_directory"] = get_rack_settings().get_work_path("Physical").replace('\\', '/') + "/Order"
                    stored_tracker = mon.get("processed_orders_file") or ""
                    if (not stored_tracker
                            or "woocommerce_monitor_data" in stored_tracker.replace("/", "\\")):
                        mon["processed_orders_file"] = str(self.data_dir / "processed_orders.json")
                    log = default_config.get("logging", {})
                    if not log.get("log_file"):
                        log["log_file"] = str(get_appdata_path() / "logs" / "woocommerce_monitor.log")
                    # Persist the fix so we don't redo the rewrite each launch
                    if loaded_config != default_config:
                        self.config = default_config
                        self.save_config()
                    return default_config
            except Exception as e:
                print(f"Error loading config: {e}")
                return default_config
        else:
            self.save_config(default_config)
            return default_config

    def _merge_config(self, default: Dict, loaded: Dict):
        """Recursively merge loaded config into default"""
        for key, value in loaded.items():
            if key in default:
                if isinstance(value, dict) and isinstance(default[key], dict):
                    self._merge_config(default[key], value)
                else:
                    default[key] = value

    def save_config(self, config: Optional[Dict] = None):
        """Save configuration to file"""
        if config:
            self.config = config

        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.config, f, indent=4)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False


# ====================================
# PROCESSED ORDERS TRACKER
# ====================================

class ProcessedOrdersTracker:
    """Track which orders have already been processed"""

    def __init__(self, config: Config):
        self.config = config
        self.tracker_file = Path(config.config['monitoring']['processed_orders_file'])
        self.processed_orders = self.load_tracker()

    def load_tracker(self) -> Dict:
        """Load processed orders from file"""
        if self.tracker_file.exists():
            try:
                with open(self.tracker_file, 'r') as f:
                    return json.load(f)
            except:
                return {}
        return {}

    def save_tracker(self):
        """Save processed orders to file"""
        try:
            with open(self.tracker_file, 'w') as f:
                json.dump(self.processed_orders, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save tracker: {e}")

    def is_processed(self, order_id: str) -> bool:
        """Check if order has been processed"""
        return str(order_id) in self.processed_orders

    def mark_processed(self, order_id: str, folder_path: str, documents: Dict):
        """Mark order as processed"""
        self.processed_orders[str(order_id)] = {
            "processed_at": datetime.now().isoformat(),
            "folder_path": folder_path,
            "documents": documents
        }
        self.save_tracker()


# ====================================
# WOOCOMMERCE API CLIENT
# ====================================

class WooCommerceClient:
    """WooCommerce REST API client"""

    def __init__(self, config: Config):
        self.config = config
        wc_config = config.config['woocommerce']

        self.base_url = wc_config['url'].rstrip('/')
        self.api_url = f"{self.base_url}/wp-json/{wc_config['api_version']}"
        self.auth = HTTPBasicAuth(
            wc_config['consumer_key'],
            wc_config['consumer_secret']
        )
        self.session = requests.Session()
        self.session.auth = self.auth

    def test_connection(self) -> tuple[bool, str]:
        """Test WooCommerce API connection"""
        try:
            response = self.session.get(f"{self.api_url}/orders", params={'per_page': 1})
            if response.status_code == 200:
                return True, "Connection successful"
            else:
                return False, f"API returned status {response.status_code}"
        except Exception as e:
            return False, str(e)

    def get_recent_orders(self, hours: int = 48) -> List[Dict]:
        """Get recent orders from WooCommerce"""
        try:
            from datetime import datetime, timedelta
            after_date = (datetime.now() - timedelta(hours=hours)).isoformat()

            # Get filter settings
            filters = self.config.config['filters']

            params = {
                'after': after_date,
                'per_page': 100,
                'orderby': 'date',
                'order': 'desc'
            }

            # Add status filter if specified
            if filters.get('order_statuses'):
                params['status'] = ','.join(filters['order_statuses'])

            response = self.session.get(f"{self.api_url}/orders", params=params)
            response.raise_for_status()

            return response.json()
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return []

    def get_order_details(self, order_id: int) -> Optional[Dict]:
        """Get detailed order information"""
        try:
            response = self.session.get(f"{self.api_url}/orders/{order_id}")
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Failed to get order {order_id}: {e}")
            return None

    def update_order_status(self, order_id: int, status: str) -> Optional[Dict]:
        """Update order status (e.g., 'processing', 'completed', 'on-hold')."""
        try:
            response = self.session.put(
                f"{self.api_url}/orders/{order_id}",
                json={"status": status}
            )
            response.raise_for_status()
            logger.info(f"Order {order_id} status updated to '{status}'")
            return response.json()
        except Exception as e:
            logger.error(f"Failed to update order {order_id} status: {e}")
            return None

    def get_orders_for_period(self, after: str, before: str) -> List[Dict]:
        """Get all orders in a date range. Dates as ISO strings (YYYY-MM-DD)."""
        all_orders = []
        page = 1
        try:
            while True:
                params = {
                    'after': f"{after}T00:00:00",
                    'before': f"{before}T23:59:59",
                    'per_page': 100,
                    'page': page,
                    'orderby': 'date',
                    'order': 'asc'
                }
                response = self.session.get(f"{self.api_url}/orders", params=params)
                response.raise_for_status()
                orders = response.json()
                if not orders:
                    break
                all_orders.extend(orders)
                page += 1
            return all_orders
        except Exception as e:
            logger.error(f"Failed to get orders for period {after} to {before}: {e}")
            return all_orders

    def download_invoice_pdf(self, order_id: int, save_path: Path) -> bool:
        """Download invoice PDF via custom WordPress AJAX endpoint (pipeline_get_invoice)."""
        try:
            wc_config = self.config.config['woocommerce']
            secret_key = wc_config.get('monitor_secret_key', '')
            if not secret_key:
                logger.warning("monitor_secret_key not configured — cannot download invoice PDF")
                return False

            endpoint = f"{self.base_url}/wp-admin/admin-ajax.php"
            params = {
                'action': 'pipeline_get_invoice',
                'order_id': order_id,
                'secret': secret_key
            }
            logger.debug(f"Requesting invoice from {endpoint} for order {order_id}")
            response = requests.get(endpoint, params=params, timeout=30, allow_redirects=False)
            logger.debug(f"Invoice response: HTTP {response.status_code}, content-type: {response.headers.get('content-type', 'unknown')}, size: {len(response.content)} bytes")

            if response.status_code in (301, 302, 303, 307, 308):
                redirect_url = response.headers.get('Location', 'unknown')
                logger.error(f"Invoice endpoint redirected to {redirect_url} — check server config")
                return False

            if response.status_code != 200:
                logger.error(f"Invoice endpoint returned HTTP {response.status_code} for order {order_id}")
                return False

            content_type = response.headers.get('content-type', '')
            if 'pdf' not in content_type:
                try:
                    data = response.json()
                    error_msg = data.get('data', data.get('message', 'Unknown error'))
                    logger.error(f"Invoice endpoint error for order {order_id}: {error_msg}")
                except ValueError:
                    logger.error(f"Invoice endpoint returned unexpected content-type '{content_type}' for order {order_id}")
                return False

            save_path.parent.mkdir(parents=True, exist_ok=True)
            with open(save_path, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded invoice PDF for order {order_id}: {save_path}")
            return True
        except requests.exceptions.ConnectionError:
            logger.error(f"Cannot reach invoice endpoint — check your store URL")
            return False
        except requests.exceptions.Timeout:
            logger.error(f"Invoice download timed out for order {order_id}")
            return False
        except Exception as e:
            logger.error(f"Failed to download invoice for order {order_id}: {e}")
            return False

    def create_manual_order(self, customer: Dict, line_items: List[Dict],
                            status: str = "completed") -> Optional[Dict]:
        """
        Create a manual WooCommerce order for project invoicing.

        Args:
            customer: Dict with billing info (first_name, last_name, email, address_1, etc.)
            line_items: List of dicts with name, quantity, total (price as string)
            status: Order status (default 'completed' to trigger invoice generation)
        """
        try:
            order_data = {
                "status": status,
                "billing": customer,
                "line_items": line_items,
                "set_paid": True
            }
            response = self.session.post(f"{self.api_url}/orders", json=order_data)
            response.raise_for_status()
            order = response.json()
            logger.info(f"Created manual order #{order.get('number', order['id'])}")
            return order
        except Exception as e:
            logger.error(f"Failed to create manual order: {e}")
            return None

    def get_all_orders(self) -> List[Dict]:
        """Get all orders matching configured status filters (paginated)."""
        all_orders = []
        page = 1
        try:
            filters = self.config.config['filters']
            while True:
                params = {
                    'per_page': 100,
                    'page': page,
                    'orderby': 'date',
                    'order': 'desc'
                }
                if filters.get('order_statuses'):
                    params['status'] = ','.join(filters['order_statuses'])

                response = self.session.get(f"{self.api_url}/orders", params=params)
                response.raise_for_status()
                orders = response.json()
                if not orders:
                    break
                all_orders.extend(orders)
                page += 1
            return all_orders
        except Exception as e:
            logger.error(f"Failed to get orders: {e}")
            return all_orders

    def matches_filters(self, order: Dict) -> bool:
        """Check if order matches configured filters"""
        filters = self.config.config['filters']

        # Check shipping method filter
        if filters.get('shipping_methods'):
            shipping_method = order.get('shipping_lines', [])
            if shipping_method:
                method_id = shipping_method[0].get('method_id', '').lower()
                if not any(method.lower() in method_id for method in filters['shipping_methods']):
                    return False

        # Check payment method filter
        if filters.get('payment_methods'):
            payment_method = order.get('payment_method', '').lower()
            if not any(method.lower() in payment_method for method in filters['payment_methods']):
                return False

        return True

    def update_invoice_number(self, order_id: int, new_number: str) -> Optional[Dict]:
        """Update the invoice number on a WooCommerce order (WooCommerce PDF Invoices plugin).

        Updates both _wcpdf_invoice_number (formatted string) and
        _wcpdf_invoice_number_data (structured data the plugin reads when
        generating the PDF).
        """
        try:
            # Build the number_data structure that WCPDF expects
            number_data = {
                "number": int(new_number) if new_number.isdigit() else new_number,
                "formatted_number": str(new_number),
                "prefix": "",
                "suffix": "",
            }

            response = self.session.put(
                f"{self.api_url}/orders/{order_id}",
                json={
                    "meta_data": [
                        {"key": "_wcpdf_invoice_number", "value": str(new_number)},
                        {"key": "_wcpdf_invoice_number_data", "value": number_data},
                    ]
                }
            )
            response.raise_for_status()
            logger.info(f"Updated invoice number for order {order_id} to '{new_number}'")
            return response.json()
        except Exception as e:
            logger.error(f"Failed to update invoice number for order {order_id}: {e}")
            return None

    def has_bpost_shipping(self, order: Dict) -> bool:
        """Check if order uses bpost shipping"""
        for shipping_line in order.get('shipping_lines', []):
            method_id = shipping_line.get('method_id', '').lower()
            method_title = shipping_line.get('method_title', '').lower()

            if 'bpost' in method_id or 'bpost' in method_title:
                return True

        for meta in order.get('meta_data', []):
            key = meta.get('key', '').lower()
            if 'bpost' in key:
                return True

        return False

    def get_bpost_label_url(self, order: Dict) -> Optional[str]:
        """Extract bpost label URL from order metadata"""
        label_meta_keys = [
            '_bpost_label_url',
            '_bpost_shipping_label',
            'bpost_label',
            '_shipping_label_url',
            '_bpost_label_pdf',
            'Bpost_trackingurl'
        ]

        for meta in order.get('meta_data', []):
            key = meta.get('key', '')
            if key in label_meta_keys:
                return meta.get('value')

        return None

    def get_bpost_label_from_db(self, order_id: int) -> Optional[str]:
        """Query custom wp_Bpost table for label URL via WordPress AJAX endpoint"""
        try:
            wc_config = self.config.config['woocommerce']
            base_url = wc_config['url'].rstrip('/')
            secret_key = wc_config.get('monitor_secret_key', '')

            if not secret_key:
                logger.debug("No monitor_secret_key configured, skipping database query")
                return None

            endpoint = f"{base_url}/wp-admin/admin-ajax.php"

            params = {
                'action': 'bpost_monitor_get_label',
                'order_id': order_id,
                'secret': secret_key
            }

            response = requests.get(endpoint, params=params, timeout=10)

            if response.status_code == 200:
                try:
                    data = response.json()
                    if data.get('success') and data.get('data'):
                        label_url = data['data'].get('labelurl')
                        if label_url:
                            logger.info(f"Found label URL in wp_Bpost table for order {order_id}")
                            return label_url
                except Exception as e:
                    logger.debug(f"Error parsing response: {e}")

        except Exception as e:
            logger.debug(f"Could not query wp_Bpost table: {e}")

        return None


# ====================================
# DOCUMENT GENERATOR & DOWNLOADER
# ====================================

def sanitize_filename(name: str) -> str:
    """Remove characters invalid in Windows filenames."""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, '_')
    return name


class DocumentManager:
    """Handle document generation and downloading"""

    def __init__(self, config: Config, wc_client: WooCommerceClient):
        self.config = config
        self.wc_client = wc_client
        self.base_dir = Path(config.config['monitoring']['base_directory'])
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def create_order_folder(self, order: Dict) -> Path:
        """Ensure an order folder exists, named from server-side order data.

        The WooCommerce order is the source of truth for both the customer
        name and the date prefix. If a folder for this order already exists
        with a stale name (e.g. date drifted on the server), rename it to
        match instead of spawning a duplicate.
        """
        order_id = order['id']
        order_number = order.get('number', order_id)

        # Build customer name in PascalCase (e.g. "Dirk Scheers" -> "DirkScheers")
        billing = order.get('billing', {})
        customer_name = self._camelcase_name(
            billing.get('first_name', ''),
            billing.get('last_name', ''),
        )

        # Canonical folder name derived from server data
        naming_format = self.config.config['folder_structure']['naming_format']
        folder_name = naming_format.format(
            order_number=order_number,
            order_id=order_id,
            customer_name=customer_name
        )
        if self.config.config['folder_structure'].get('include_date'):
            folder_name = f"{self._order_date(order)}_{folder_name}"

        target = self.base_dir / folder_name

        # If the canonical folder isn't there yet, check for a stale-named
        # folder for this same order and rename it.
        if not target.exists():
            stale = self._find_by_order_number(order_number)
            if stale:
                try:
                    stale.rename(target)
                    logger.info(
                        f"Renamed order folder {stale.name} -> {target.name} "
                        "to match server date"
                    )
                except OSError as e:
                    logger.warning(
                        f"Could not rename {stale.name} -> {target.name}: {e}; "
                        "using existing folder"
                    )
                    target = stale

        # Create canonical folder + standard subdirectories
        target.mkdir(parents=True, exist_ok=True)
        for sub in ("01_Incoming", "02_Production", "03_Outgoing", "_LIBRARY"):
            (target / sub).mkdir(exist_ok=True)

        # Flag any leftover duplicates (e.g. both stale and canonical exist)
        for dup in self._find_all_by_order_number(order_number):
            if dup != target:
                logger.warning(
                    f"Duplicate folder for order #{order_number}: {dup.name} "
                    f"(canonical is {target.name}) — please merge/remove manually"
                )

        return target

    def _find_by_order_number(self, order_number) -> Optional[Path]:
        """Return the first folder ending in `_<order_number>`, or None.

        Using an `_<num>` suffix (not a substring) avoids false positives
        like order #66 matching any folder containing '66'.
        """
        for path in self._find_all_by_order_number(order_number):
            return path
        return None

    def _find_all_by_order_number(self, order_number) -> List[Path]:
        """Return every folder ending in `_<order_number>`."""
        if not self.base_dir.exists():
            return []
        suffix = f"_{order_number}"
        matches: List[Path] = []
        try:
            for item in self.base_dir.iterdir():
                if item.is_dir() and item.name.endswith(suffix):
                    matches.append(item)
        except Exception:
            pass
        return matches

    @staticmethod
    def _camelcase_name(first: str, last: str) -> str:
        """Combine first + last name into PascalCase, stripping non-alphanumerics.

        Splits on whitespace, hyphens, and underscores so multi-part names
        ("Anna-Marie", "Van Driessche") capitalize each segment.

        Static so callers outside DocumentManager (e.g. order_project_linker)
        can reuse the exact same name-derivation logic without instantiating
        a full DocumentManager.
        """
        parts = re.split(r'[\s\-_]+', f"{first} {last}".strip())
        cleaned = []
        for p in parts:
            p = re.sub(r'[^A-Za-z0-9]', '', p)
            if p:
                cleaned.append(p[0].upper() + p[1:])
        return "".join(cleaned) or "Guest"

    @staticmethod
    def _order_date(order: Dict) -> str:
        """Return YYYY-MM-DD for the order's date_created, falling back to today.

        Static for the same reason as _camelcase_name — reused by
        order_project_linker for date-based matching.
        """
        raw = order.get('date_created') or order.get('date_created_gmt') or ''
        if raw:
            # WooCommerce returns ISO 8601 like "2025-10-15T14:30:00"
            try:
                return datetime.fromisoformat(raw.replace('Z', '+00:00')).strftime('%Y-%m-%d')
            except (ValueError, TypeError):
                pass
        return datetime.now().strftime('%Y-%m-%d')

    def _sanitize_filename(self, name: str) -> str:
        """Remove invalid filesystem characters"""
        return sanitize_filename(name)

    def download_shipping_label(self, order: Dict, order_folder: Path) -> Optional[str]:
        """Download shipping label if available"""
        try:
            # Try to get label URL from metadata first
            label_url = self.wc_client.get_bpost_label_url(order)

            # If not in metadata, try database query
            if not label_url and self.wc_client.has_bpost_shipping(order):
                label_url = self.wc_client.get_bpost_label_from_db(order['id'])

            if not label_url:
                return None

            # Download label
            doc_config = self.config.config['documents']
            filename = doc_config['label_filename'].format(
                order_number=order.get('number', order['id']),
                order_id=order['id']
            )

            outgoing_dir = order_folder / "03_Outgoing"
            outgoing_dir.mkdir(exist_ok=True)
            label_path = outgoing_dir / filename

            response = requests.get(label_url, timeout=30)
            response.raise_for_status()

            with open(label_path, 'wb') as f:
                f.write(response.content)

            logger.info(f"Downloaded shipping label: {label_path}")
            return str(label_path)

        except Exception as e:
            logger.error(f"Failed to download label for order {order['id']}: {e}")
            return None

    def create_order_details_file(self, order: Dict, order_folder: Path) -> Optional[str]:
        """Create a text file with order details"""
        try:
            doc_config = self.config.config['documents']
            filename = doc_config['order_details_filename'].format(
                order_number=order.get('number', order['id']),
                order_id=order['id']
            )

            library_dir = order_folder / "_LIBRARY"
            library_dir.mkdir(exist_ok=True)
            details_path = library_dir / filename

            with open(details_path, 'w', encoding='utf-8') as f:
                f.write("=" * 60 + "\n")
                f.write(f"ORDER DETAILS - #{order.get('number', order['id'])}\n")
                f.write("=" * 60 + "\n\n")

                f.write(f"Order ID: {order['id']}\n")
                f.write(f"Order Number: {order.get('number', order['id'])}\n")
                f.write(f"Date: {order['date_created']}\n")
                f.write(f"Status: {order['status'].upper()}\n")
                f.write(f"Currency: {order['currency']}\n")
                f.write(f"Total: {order['currency_symbol']}{order['total']}\n\n")

                # Customer Info
                billing = order.get('billing', {})
                f.write("-" * 60 + "\n")
                f.write("CUSTOMER INFORMATION\n")
                f.write("-" * 60 + "\n")
                f.write(f"Name: {billing.get('first_name', '')} {billing.get('last_name', '')}\n")
                if billing.get('company'):
                    f.write(f"Company: {billing['company']}\n")
                f.write(f"Email: {billing.get('email', '')}\n")
                f.write(f"Phone: {billing.get('phone', '')}\n\n")

                # Billing Address
                f.write("Billing Address:\n")
                f.write(f"  {billing.get('address_1', '')}\n")
                if billing.get('address_2'):
                    f.write(f"  {billing['address_2']}\n")
                f.write(f"  {billing.get('postcode', '')} {billing.get('city', '')}\n")
                f.write(f"  {billing.get('state', '')} {billing.get('country', '')}\n\n")

                # Shipping Address
                shipping = order.get('shipping', {})
                if shipping:
                    f.write("Shipping Address:\n")
                    f.write(f"  {shipping.get('first_name', '')} {shipping.get('last_name', '')}\n")
                    f.write(f"  {shipping.get('address_1', '')}\n")
                    if shipping.get('address_2'):
                        f.write(f"  {shipping['address_2']}\n")
                    f.write(f"  {shipping.get('postcode', '')} {shipping.get('city', '')}\n")
                    f.write(f"  {shipping.get('state', '')} {shipping.get('country', '')}\n\n")

                # Line Items
                f.write("-" * 60 + "\n")
                f.write("ORDER ITEMS\n")
                f.write("-" * 60 + "\n")
                for item in order.get('line_items', []):
                    f.write(f"\n{item.get('name', '')}\n")
                    f.write(f"  SKU: {item.get('sku', 'N/A')}\n")
                    f.write(f"  Quantity: {item.get('quantity', 1)}\n")
                    f.write(f"  Price: {order['currency_symbol']}{item.get('price', 0)}\n")
                    f.write(f"  Total: {order['currency_symbol']}{item.get('total', 0)}\n")

                # Shipping Method
                f.write("\n" + "-" * 60 + "\n")
                f.write("SHIPPING\n")
                f.write("-" * 60 + "\n")
                for ship_line in order.get('shipping_lines', []):
                    f.write(f"Method: {ship_line.get('method_title', 'N/A')}\n")
                    f.write(f"Cost: {order['currency_symbol']}{ship_line.get('total', 0)}\n")

                # Payment
                f.write("\n" + "-" * 60 + "\n")
                f.write("PAYMENT\n")
                f.write("-" * 60 + "\n")
                f.write(f"Method: {order.get('payment_method_title', 'N/A')}\n")
                f.write(f"Transaction ID: {order.get('transaction_id', 'N/A')}\n")

                # Customer Note
                if order.get('customer_note'):
                    f.write("\n" + "-" * 60 + "\n")
                    f.write("CUSTOMER NOTE\n")
                    f.write("-" * 60 + "\n")
                    f.write(order['customer_note'] + "\n")

                f.write("\n" + "=" * 60 + "\n")
                f.write("End of Order Details\n")
                f.write("=" * 60 + "\n")

            logger.info(f"Created order details file: {details_path}")
            return str(details_path)

        except Exception as e:
            logger.error(f"Failed to create order details for order {order['id']}: {e}")
            return None


# ====================================
# INVOICE FILER
# ====================================

class InvoiceFiler:
    """
    Handles filing invoices to the bookkeeping folder and creating
    .lnk shortcuts in order/project folders.

    Bookkeeping structure: {boekhouding_base}/{year}/Q{quarter}/Uitgaand/
    Invoice naming: 3D_YYMMDD_Factuur{number}_{ClientName}.pdf
    """

    def __init__(self, config: Config, wc_client: WooCommerceClient):
        self.config = config
        self.wc_client = wc_client
        settings = get_rack_settings()
        self.library_base = Path(settings.get_boekhouding_base())

    def _get_quarter_dir(self, date_str: str) -> Path:
        """Get the Uitgaand folder for the quarter containing the given date."""
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
        quarter = (dt.month - 1) // 3 + 1
        return self.library_base / str(dt.year) / f"Q{quarter}" / "Uitgaand"

    def _build_invoice_filename(self, invoice_number: str, invoice_date: str,
                                client_name: str) -> str:
        """Build filename: 3D_YYMMDD_Factuur{number}_{ClientName}.pdf"""
        dt = datetime.strptime(invoice_date[:10], "%Y-%m-%d")
        date_part = dt.strftime("%y%m%d")
        # Clean client name for filesystem
        clean_name = client_name.replace(" ", "").replace("_", "")
        for ch in '<>:"/\\|?*':
            clean_name = clean_name.replace(ch, "")
        return f"3D_{date_part}_Factuur{int(invoice_number):03d}_{clean_name}.pdf"

    def _find_outgoing_folder(self, project_folder: Path) -> Optional[Path]:
        """Find the outgoing folder (e.g. 03_Outgoing) in a project folder."""
        try:
            for item in project_folder.iterdir():
                if item.is_dir() and "outgoing" in item.name.lower():
                    return item
        except Exception:
            pass
        return None

    def _create_shortcut(self, shortcut_path: Path, target_path: Path):
        """Create a Windows .lnk shortcut."""
        try:
            import win32com.client
            shell = win32com.client.Dispatch("WScript.Shell")
            shortcut = shell.CreateShortCut(str(shortcut_path))
            shortcut.TargetPath = str(target_path)
            shortcut.WorkingDirectory = str(target_path.parent)
            shortcut.save()
            logger.info(f"Created shortcut: {shortcut_path}")
        except Exception as e:
            logger.error(f"Failed to create shortcut: {e}")

    def _get_invoice_info_from_meta(self, order: Dict) -> Optional[Dict]:
        """Extract invoice number and date from order metadata (WooCommerce PDF Invoices plugin)."""
        invoice_number = None
        invoice_date = None
        invoice_date_formatted = None
        for meta in order.get('meta_data', []):
            key = meta.get('key', '')
            if key == '_wcpdf_invoice_number':
                invoice_number = meta.get('value')
            elif key == '_wcpdf_invoice_date':
                invoice_date = meta.get('value')
            elif key == '_wcpdf_invoice_date_formatted':
                invoice_date_formatted = meta.get('value')
        if invoice_number:
            # Prefer formatted date if available
            if invoice_date_formatted:
                date_str = invoice_date_formatted
            elif invoice_date:
                # Plugin stores a Unix timestamp — convert to YYYY-MM-DD
                try:
                    dt = datetime.fromtimestamp(int(invoice_date))
                    date_str = dt.strftime('%Y-%m-%d')
                except (ValueError, TypeError):
                    # Already a date string
                    date_str = str(invoice_date)[:10]
            else:
                date_str = order.get('date_created', '')[:10]
            return {'invoice_number': str(invoice_number), 'invoice_date': date_str}
        return None

    def file_invoice(self, order: Dict, project_folder: Path) -> Optional[Path]:
        """
        Download invoice, copy to Boekhouding, create .lnk in project folder.

        Args:
            order: WooCommerce order dict
            project_folder: Path to the order/project folder

        Returns:
            Path to the filed invoice in Boekhouding, or None on failure
        """
        order_id = order['id']
        order_number = order.get('number', order_id)

        try:
            # Get invoice info from order metadata (WooCommerce PDF Invoices plugin)
            invoice_info = self._get_invoice_info_from_meta(order)
            if not invoice_info:
                logger.warning(f"No invoice metadata found for order #{order_number} — invoice may not be generated yet")
                return None

            invoice_number = invoice_info['invoice_number']
            invoice_date = invoice_info['invoice_date']
            logger.info(f"Order #{order_number}: invoice #{invoice_number} dated {invoice_date}")

            # Get client name from billing
            billing = order.get('billing', {})
            client_name = f"{billing.get('last_name', '')}".strip()
            if not client_name:
                client_name = f"{billing.get('first_name', '')}".strip()
            if not client_name:
                client_name = "Unknown"

            # Build paths
            quarter_dir = self._get_quarter_dir(invoice_date)
            quarter_dir.mkdir(parents=True, exist_ok=True)

            filename = self._build_invoice_filename(invoice_number, invoice_date, client_name)
            invoice_path = quarter_dir / filename

            # Download invoice PDF directly to Boekhouding
            if not self.wc_client.download_invoice_pdf(order_id, invoice_path):
                return None

            logger.info(f"Filed invoice: {invoice_path}")

            # Create .lnk shortcut in the project's _LIBRARY folder, next to
            # its other documentation (Project/Order Details, invoice cache).
            library_dir = project_folder / "_LIBRARY"
            library_dir.mkdir(parents=True, exist_ok=True)
            shortcut_name = filename.replace(".pdf", ".lnk")
            self._create_shortcut(library_dir / shortcut_name, invoice_path)

            return invoice_path

        except Exception as e:
            logger.error(f"Failed to file invoice for order #{order_number}: {e}")
            return None


# ====================================
# ORDER MONITOR
# ====================================

class OrderMonitor:
    """Main order monitoring logic"""

    def __init__(self, config: Config):
        self.config = config
        self.wc_client = WooCommerceClient(config)
        self.doc_manager = DocumentManager(config, self.wc_client)
        self.invoice_filer = InvoiceFiler(config, self.wc_client)
        self.tracker = ProcessedOrdersTracker(config)
        # The project tracker DB is canonical for projects. Order folders are
        # registered the moment they're created so the tracker doesn't have
        # to re-scan the Order directory afterwards.
        try:
            self.db = ProjectDatabase()
        except Exception as e:
            logger.warning(f"Project DB unavailable; orders will not be registered: {e}")
            self.db = None
        # Core invoice registry — soft optional. If the invoice_manager
        # core isn't configured we fall back to the WooCommerce PDF
        # plugin's own numbering. Once configured, the registry is
        # authoritative.
        self.global_registry = None
        try:
            from invoice_manager.core.config import load_config as _load_gi_config
            from invoice_manager.core.registry import InvoiceRegistry
            gi_config = _load_gi_config()
            self.global_registry = InvoiceRegistry(gi_config.resolve_db_path())
            self._gi_config = gi_config
            logger.info("Global invoice registry attached to WC monitor")
        except Exception as e:
            logger.info(
                f"Global invoice registry not active (will use WC plugin numbering): {e}"
            )
            self._gi_config = None
        self.running = False
        self.callback = None
        self.order_list_callback = None

    def set_callback(self, callback):
        """Set callback function for status updates"""
        self.callback = callback

    def set_order_list_callback(self, callback):
        """Set callback to push full order list to GUI"""
        self.order_list_callback = callback

    def log_status(self, message: str, level: str = "info"):
        """Log status message"""
        if self.callback:
            self.callback(message, level)

        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _register_order_in_db(self, order: Dict, order_folder: Path) -> None:
        """Insert the order's folder into the project DB as a Physical/Order
        project. register_project is idempotent on the path, so re-runs (or
        the legacy folder-scan importer) won't create duplicates."""
        if not self.db:
            return
        try:
            order_id = order.get("id")
            order_number = order.get("number", order_id)

            billing = order.get("billing", {}) or {}
            customer_name = (
                f"{billing.get('first_name', '')} {billing.get('last_name', '')}"
            ).strip()
            if not customer_name:
                customer_name = "Guest"

            # Best-effort date — fall back to today if WooCommerce gave us
            # something unparseable.
            date_str = ""
            for key in ("date_paid", "date_completed", "date_created"):
                raw = order.get(key)
                if raw:
                    try:
                        date_str = str(raw)[:10]
                        if len(date_str) == 10:
                            break
                    except Exception:
                        continue
            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")

            shipping = order.get("shipping", {}) or {}
            location_parts = [
                shipping.get("city", ""),
                shipping.get("country", ""),
            ]
            location = ", ".join(p for p in location_parts if p)

            metadata = {
                "is_personal": False,
                "is_sandbox": False,
                "physical_subtype": "Order",
                "location": location,
                "woo_order_id": order_id,
                "woo_order_number": order_number,
                "woo_status": order.get("status", ""),
                "woo_total": order.get("total", ""),
                "woo_currency": order.get("currency", ""),
            }

            self.db.register_project({
                "client_name": customer_name,
                "project_name": f"Order_{order_number}",
                "project_type": "Physical",
                "date_created": date_str,
                "path": str(order_folder),
                "base_directory": str(self.doc_manager.base_dir),
                "status": "active",
                "notes": "",
                "metadata": metadata,
            })
            logger.info(
                f"Registered Physical/Order project for #{order_number} "
                f"({customer_name}) in DB"
            )

            self._auto_link_to_project(order, order_folder)
        except Exception as e:
            # Don't let DB issues break order processing — folder + invoices
            # still work, and the legacy folder-scan importer can pick the
            # row up later.
            logger.error(f"Failed to register order in project DB: {e}")

    def _auto_link_to_project(self, order: Dict, order_folder: Path) -> None:
        """Conservatively cross-link this order to an existing, unlinked
        Physical/Project row if — and only if — exactly one such row is an
        "exact" date+customer match. Ambiguous (0 or 2+) matches are left
        alone for the manual "Link WooCommerce Orders" tool to resolve —
        never guess unattended in a background polling thread.

        Purely additive metadata (mirrors order_project_linker.apply_link):
        never renames, moves, creates, or deletes a folder or file, and
        never touches a project row that already has a link.
        """
        try:
            from invoice_manager.order_project_linker import score, apply_link

            unlinked_projects = [
                p for p in self.db.get_all_projects(status="all")
                if p.get("project_type") == "Physical"
                and p.get("metadata", {}).get("physical_subtype") == "Project"
                and not p.get("metadata", {}).get("woo_order_number")
            ]
            exact_hits = []
            for p in unlinked_projects:
                candidate = score(p, order)
                if candidate and candidate.confidence == "exact":
                    exact_hits.append(candidate)

            if len(exact_hits) != 1:
                return

            order_row = self.db.get_project_by_path(str(order_folder))
            apply_link(self.db, exact_hits[0].project_row, order, order_folder_row=order_row)
            self.db.save()
            self.log_status(
                f"Auto-linked order #{order.get('number', order.get('id'))} to project "
                f"'{exact_hits[0].project_row.get('project_name', '?')}'",
                "success",
            )
        except Exception as e:
            logger.warning(f"Auto-link check failed (non-fatal): {e}")

    def _assign_global_invoice_number(self, order: Dict) -> Optional[int]:
        """Reserve a number from the global registry and push it to WC.

        Precedence (avoids overwriting numbers WC already issued):
          1. Registry already has a row for this order → reuse + re-push.
          2. WC order already carries a WCPDF invoice number → import it
             into the registry at its original (year, sequence). Don't
             touch WC.
          3. No number anywhere → reserve a fresh one and push to WC.

        Returns the assigned sequence, or None if the registry is inactive.
        """
        if not self.global_registry:
            return None
        try:
            from invoice_manager.core.wc_bridge import (
                build_draft_from_wc_order, extract_wc_invoice_info,
            )
            from invoice_manager.core.registry import RegistryConflictError
        except Exception as e:
            logger.warning(f"invoice_manager.core.wc_bridge unavailable: {e}")
            return None
        try:
            order_id = str(order.get("id"))
            existing = self.global_registry.get_by_source_ref("woocommerce", order_id)
            pushed_already = False

            if existing:
                sequence = existing["sequence"]
                logger.info(
                    f"WC order {order_id} already has invoice "
                    f"#{sequence:03d} ({existing['year']}); re-pushing to WC"
                )
            else:
                wc_info = extract_wc_invoice_info(order)
                draft = build_draft_from_wc_order(order, company_key="3D")

                if wc_info:
                    draft["invoice_date"] = wc_info["invoice_date"]
                    try:
                        row = self.global_registry.import_existing_invoice(
                            year=wc_info["year"], sequence=wc_info["sequence"],
                            draft=draft,
                        )
                        sequence = row["sequence"]
                        self.log_status(
                            f"Imported existing WC invoice "
                            f"#{sequence:03d} for order #{order.get('number', order_id)}",
                            "success",
                        )
                        pushed_already = True
                    except RegistryConflictError as e:
                        logger.error(
                            f"WC order {order_id}: cannot adopt existing "
                            f"WC number #{wc_info['sequence']:03d} "
                            f"({wc_info['year']}) — {e}. Reserving a fresh "
                            f"number instead."
                        )
                        wc_info = None

                if not wc_info:
                    date_str = (order.get("date_created") or "")[:10]
                    try:
                        year = int(date_str[:4])
                    except (ValueError, TypeError):
                        year = datetime.now().year
                    if not draft.get("invoice_date"):
                        draft["invoice_date"] = datetime.now().strftime("%Y-%m-%d")
                    row = self.global_registry.reserve_and_return_row(year, draft)
                    sequence = row["sequence"]
                    self.log_status(
                        f"Reserved global invoice #{sequence:03d} for order #{order.get('number', order_id)}",
                        "success",
                    )

            if pushed_already:
                return sequence

            ok = self.wc_client.update_invoice_number(int(order_id), str(sequence)) is not None
            if not ok:
                if existing:
                    inv_id = existing["id"]
                else:
                    row2 = self.global_registry.get_by_source_ref("woocommerce", order_id)
                    inv_id = row2["id"] if row2 else None
                if inv_id is not None:
                    self.global_registry.enqueue_wc_push(
                        inv_id, order_id, "update_invoice_number returned failure"
                    )
            else:
                if existing:
                    self.global_registry.clear_wc_push(existing["id"])
            return sequence
        except Exception as e:
            logger.error(f"Failed to assign global invoice number: {e}")
            return None

    def process_order(self, order: Dict) -> bool:
        """Process a single order"""
        order_id = str(order['id'])
        order_number = order.get('number', order_id)

        # Check if already processed
        if self.tracker.is_processed(order_id):
            return False

        # Check if matches filters
        if not self.wc_client.matches_filters(order):
            self.log_status(f"Order #{order_number} doesn't match filters", "info")
            return False

        self.log_status(f"Processing order #{order_number}...", "info")

        try:
            # Create order folder
            order_folder = self.doc_manager.create_order_folder(order)
            self.log_status(f"Created folder: {order_folder.name}", "success")

            # Register in the project DB right away so the tracker sees it
            # without needing a folder rescan.
            self._register_order_in_db(order, order_folder)

            # Assign a global invoice number and push it back to WC so the
            # WooCommerce PDF-Invoices plugin generates the PDF with our
            # number. No-op if the global registry isn't configured.
            self._assign_global_invoice_number(order)

            documents = {}

            # Invoices and shipping labels are intentionally NOT downloaded
            # here — those happen via the "View Invoice" / "View Label"
            # buttons (per-order) or "File Quarter Invoices" (bulk Boekhouding
            # sweep). Only order details are generated automatically.

            # Order details
            details_path = self.doc_manager.create_order_details_file(order, order_folder)
            if details_path:
                documents['details'] = details_path
                self.log_status(f"✓ Order details saved", "success")

            # Mark as processed
            self.tracker.mark_processed(order_id, str(order_folder), documents)

            self.log_status(f"✓ Successfully processed order #{order_number}", "success")
            return True

        except Exception as e:
            self.log_status(f"✗ Error processing order #{order_number}: {e}", "error")
            return False

    def check_orders(self):
        """Check for new orders by status filter"""
        try:
            self.log_status("Checking for orders...", "info")

            orders = self.wc_client.get_all_orders()

            # Push full order list to GUI
            if self.order_list_callback:
                self.order_list_callback(orders)

            if not orders:
                self.log_status("No orders found", "info")
                return

            self.log_status(f"Found {len(orders)} order(s)", "info")

            processed_count = 0
            for order in orders:
                if self.process_order(order):
                    processed_count += 1

            if processed_count > 0:
                self.log_status(f"Processed {processed_count} new order(s)", "success")
            else:
                self.log_status("No new orders to process", "info")

        except Exception as e:
            self.log_status(f"Error checking orders: {e}", "error")

    def file_quarter_invoices(self, year: int, quarter: int):
        """
        Fetch all orders for a quarter and file their invoices to Boekhouding.
        Useful for catching up on invoices that weren't filed during monitoring.
        """
        # Calculate quarter date range
        start_month = (quarter - 1) * 3 + 1
        after = f"{year}-{start_month:02d}-01"
        if quarter == 4:
            before = f"{year}-12-31"
        else:
            end_month = quarter * 3
            # Last day of end_month
            import calendar
            last_day = calendar.monthrange(year, end_month)[1]
            before = f"{year}-{end_month:02d}-{last_day:02d}"

        self.log_status(f"Fetching orders for Q{quarter} {year} ({after} to {before})...", "info")

        orders = self.wc_client.get_orders_for_period(after, before)
        if not orders:
            self.log_status(f"No orders found for Q{quarter} {year}", "info")
            return

        self.log_status(f"Found {len(orders)} orders for Q{quarter} {year}", "info")

        filed_count = 0
        skipped_count = 0
        for order in orders:
            order_number = order.get('number', order['id'])

            # Find existing order folder if it exists
            order_folder = self._find_order_folder(order)

            if not order_folder:
                # Create folder if it doesn't exist
                order_folder = self.doc_manager.create_order_folder(order)
                self.log_status(f"Created folder for order #{order_number}", "info")
                self._register_order_in_db(order, order_folder)

            # Check if invoice already filed (look for .lnk in _LIBRARY)
            library_dir = order_folder / "_LIBRARY"
            if library_dir.exists() and any(f.suffix == '.lnk' for f in library_dir.iterdir() if f.is_file()):
                skipped_count += 1
                continue

            filed_path = self.invoice_filer.file_invoice(order, order_folder)
            if filed_path:
                filed_count += 1
                self.log_status(f"✓ Filed invoice for order #{order_number}", "success")
            else:
                self.log_status(f"⚠ Could not file invoice for order #{order_number}", "warning")

        self.log_status(
            f"Quarter done: {filed_count} filed, {skipped_count} already existed", "success"
        )

    def _find_order_folder(self, order: Dict) -> Optional[Path]:
        """Try to find an existing folder for an order in the Order directory."""
        base_dir = Path(self.config.config['monitoring']['base_directory'])
        if not base_dir.exists():
            return None

        order_number = str(order.get('number', order['id']))
        billing = order.get('billing', {})
        customer_name = f"{billing.get('first_name', '')}_{billing.get('last_name', '')}".strip('_')

        try:
            for item in base_dir.iterdir():
                if not item.is_dir():
                    continue
                name = item.name
                # Match by order number in folder name
                if order_number in name:
                    return item
                # Match by customer name
                if customer_name and customer_name in name:
                    return item
        except Exception:
            pass
        return None

    def create_project_invoice(self, customer: Dict, line_items: List[Dict],
                               project_folder: Path) -> Optional[Path]:
        """
        Create a WooCommerce order for a project and file the invoice.

        Args:
            customer: Billing info dict (first_name, last_name, email, address_1, etc.)
            line_items: List of dicts with name, quantity, total
            project_folder: Path to the project folder

        Returns:
            Path to the filed invoice, or None on failure
        """
        self.log_status("Creating WooCommerce order for project invoice...", "info")

        order = self.wc_client.create_manual_order(customer, line_items)
        if not order:
            self.log_status("Failed to create WooCommerce order", "error")
            return None

        order_number = order.get('number', order['id'])
        self.log_status(f"Created order #{order_number}", "success")

        # File the invoice
        filed_path = self.invoice_filer.file_invoice(order, project_folder)
        if filed_path:
            self.log_status(f"✓ Invoice filed: {filed_path.name}", "success")
        else:
            self.log_status(f"⚠ Order created but invoice filing failed", "warning")

        return filed_path

    def start_monitoring(self):
        """Start continuous monitoring"""
        self.running = True
        self.log_status("🚀 Monitoring started", "success")

        while self.running:
            self.check_orders()

            if self.running:
                interval = self.config.config['monitoring']['poll_interval']
                self.log_status(f"Next check in {interval} seconds...", "info")

                for _ in range(interval):
                    if not self.running:
                        break
                    time.sleep(1)

    def stop_monitoring(self):
        """Stop monitoring"""
        self.running = False
        self.log_status("⏹ Monitoring stopped", "warning")

