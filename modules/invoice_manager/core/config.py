"""Configuration loader for the Global Invoice module.

User-private data (config.json, invoices.sqlite) lives outside the repo
in the per-user PipelineManager AppData folder so it isn't synced to git.
Templates and the example config are bundled inside the package itself.

Falls back to sensible defaults for db_path / boekhouding_base / soffice_path.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from shared_logging import get_logger

from .models import Company

logger = get_logger(__name__)


# Path layout (post-consolidation):
#   <repo>/modules/invoice_manager/core/config.py            ← this file
#   <repo>/modules/invoice_manager/core/config.json.example  ← template (committed)
#   <repo>/modules/invoice_manager/core/README.txt           ← docs (committed)
#   <appdata>/PipelineManager/global_invoice/config.json     ← user data
#   <appdata>/PipelineManager/global_invoice/invoices.sqlite ← user data
_PKG_DIR = Path(__file__).resolve().parent
_invoice_manager_DIR = _PKG_DIR.parent
_MODULES_DIR = _invoice_manager_DIR.parent
REPO_ROOT = _MODULES_DIR.parent
CONFIG_EXAMPLE_PATH = _PKG_DIR / "config.json.example"

# Legacy (pre-consolidation) in-repo data dir — files here get moved to DATA_DIR on first run.
_LEGACY_DATA_DIR = _MODULES_DIR / "global_invoice_data"

_MIGRATABLE_FILENAMES = (
    "config.json",
    "invoices.sqlite",
    "invoices.sqlite-wal",
    "invoices.sqlite-shm",
)


def _get_user_data_dir() -> Path:
    """Per-user PipelineManager AppData dir, matching rak_settings convention."""
    if sys.platform == "win32":
        return Path.home() / "AppData" / "Local" / "PipelineManager" / "global_invoice"
    # WSL: prefer the Windows user's AppData so it stays in sync with native runs
    windows_users = Path("/mnt/c/Users")
    if windows_users.exists():
        username = os.environ.get("USER", "")
        user_path = windows_users / username
        if user_path.exists():
            return user_path / "AppData" / "Local" / "PipelineManager" / "global_invoice"
    return Path.home() / ".local" / "share" / "PipelineManager" / "global_invoice"


DATA_DIR = _get_user_data_dir()
CONFIG_PATH = DATA_DIR / "config.json"
DEFAULT_DB_PATH = DATA_DIR / "invoices.sqlite"


def _migrate_legacy_data() -> None:
    """One-shot migration: move legacy in-repo data files into the user data dir.

    Runs on every load_config() but is a no-op once the legacy files are gone.
    Never overwrites a file that already exists at the new location.
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
            logger.error(f"Failed to migrate {src} → {dst}: {e}")
    if moved:
        logger.info(
            f"Migrated global_invoice data files to {DATA_DIR}: {moved}. "
            f"Legacy folder {_LEGACY_DATA_DIR} can be removed."
        )


class ConfigError(Exception):
    pass


class GlobalInvoiceConfig:
    def __init__(self, raw: dict, source_path: Path):
        self._raw = raw
        self.source_path = source_path
        self.companies: List[Company] = []
        self._companies_by_key: Dict[str, Company] = {}
        self._parse()

    def _parse(self):
        companies = self._raw.get("companies") or []
        if not companies:
            raise ConfigError("config.json has no 'companies' entries")
        for entry in companies:
            try:
                c = Company(
                    key=entry["key"],
                    display_name=entry["display_name"],
                    legal_name=entry.get("legal_name", entry["display_name"]),
                    vat=entry.get("vat", ""),
                    address_lines=list(entry.get("address_lines") or []),
                    output_prefix=entry.get("output_prefix", entry["key"]),
                    default_vat_rate=float(entry.get("default_vat_rate", 21.0)),
                    email=entry.get("email", ""),
                    iban=entry.get("iban", ""),
                    bic=entry.get("bic", ""),
                    template_path=entry.get("template_path"),
                    wc_binding=entry.get("wc_binding"),
                )
            except KeyError as ke:
                raise ConfigError(f"Company entry missing required field: {ke}") from ke
            self.companies.append(c)
            self._companies_by_key[c.key] = c

    # --- accessors ---
    def get_company(self, key: str) -> Company:
        if key not in self._companies_by_key:
            raise KeyError(f"Unknown company key: {key!r}")
        return self._companies_by_key[key]

    def company_keys(self) -> List[str]:
        return [c.key for c in self.companies]

    @property
    def currency(self) -> str:
        return self._raw.get("behavior", {}).get("currency", "EUR")

    @property
    def year_change_confirm_threshold(self) -> int:
        return int(self._raw.get("behavior", {}).get("year_change_confirm_threshold", 1))

    @property
    def auto_open_pdf_after_generate(self) -> bool:
        return bool(self._raw.get("behavior", {}).get("auto_open_pdf_after_generate", True))

    # --- path resolution ---
    def resolve_template_path(self, company: Company) -> Optional[Path]:
        if not company.template_path:
            return None
        p = Path(company.template_path)
        if not p.is_absolute():
            p = REPO_ROOT / p
        return p

    def resolve_templates_dir(self) -> Path:
        """Best-effort path to the folder holding .ott invoice templates.

        Uses the parent of the first configured template; falls back to the
        repo's canonical templates/invoice_templates_ott directory.
        """
        for c in self.companies:
            p = self.resolve_template_path(c)
            if p is not None:
                return p.parent
        return REPO_ROOT / "templates" / "invoice_templates_ott"

    def resolve_db_path(self) -> Path:
        """Path to the invoice registry SQLite DB.

        rak_settings.business.invoice_db_path is primary; empty there
        means "use the AppData default". ``config.json paths.db_path``
        is honoured for backwards compat only.
        """
        from rak_settings import get_rak_settings
        rak_value = get_rak_settings().get_invoice_db_path()
        if rak_value:
            p = Path(rak_value)
            if not p.is_absolute():
                p = REPO_ROOT / p
            return p
        legacy = (self._raw.get("paths") or {}).get("db_path")
        if legacy:
            p = Path(legacy)
            if not p.is_absolute():
                p = REPO_ROOT / p
            return p
        return DEFAULT_DB_PATH

    def resolve_boekhouding_base(self) -> Path:
        """Authoritative bookkeeping root.

        rak_settings.business.boekhouding_base is primary. If empty,
        the legacy ``config.json paths.boekhouding_base`` is honoured
        for backwards compat; otherwise we derive from active_base.
        """
        from rak_settings import get_rak_settings
        rak = get_rak_settings()
        if rak.get_boekhouding_base_explicit():
            return Path(rak.get_boekhouding_base_explicit())
        legacy = (self._raw.get("paths") or {}).get("boekhouding_base")
        if legacy:
            return Path(legacy)
        return Path(rak.get_boekhouding_base())

    def resolve_soffice_path(self) -> Optional[Path]:
        """LibreOffice ``soffice`` binary.

        Sourced from ``rak_settings.business.soffice_path``; empty
        there falls back to PATH lookup and common install locations.
        ``config.json paths.soffice_path`` is honoured as a last-resort
        legacy override.
        """
        from rak_settings import get_rak_settings
        rak_value = get_rak_settings().get_soffice_path()
        legacy = (self._raw.get("paths") or {}).get("soffice_path")
        explicit = rak_value or legacy
        if explicit:
            p = Path(explicit)
            return p if p.exists() else None
        # Search PATH
        found = shutil.which("soffice") or shutil.which("soffice.exe")
        if found:
            return Path(found)
        # Common install locations per platform (Windows, WSL via /mnt/c,
        # macOS .app bundle - not on PATH by default even when installed -
        # and generic Linux).
        candidates = [
            r"C:\Program Files\LibreOffice\program\soffice.exe",
            r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
            "/mnt/c/Program Files/LibreOffice/program/soffice.exe",
            "/mnt/c/Program Files (x86)/LibreOffice/program/soffice.exe",
            "/Applications/LibreOffice.app/Contents/MacOS/soffice",
            "/usr/bin/soffice",
            "/usr/bin/libreoffice",
        ]
        for c in candidates:
            p = Path(c)
            if p.exists():
                return p
        return None

    # --- WC binding helper ---
    def get_wc_credentials_for_alles3d(self) -> Optional[dict]:
        """Return WC credentials dict for company '3D'.

        If wc_binding.use_monitor_config is true, read from the WC
        monitor's AppData config so credentials live in one place.
        """
        try:
            company = self.get_company("3D")
        except KeyError:
            return None
        if not company.wc_binding:
            return None
        if company.wc_binding.get("use_monitor_config"):
            from invoice_manager.wc_monitor import CONFIG_PATH as monitor_cfg
            if monitor_cfg.exists():
                try:
                    with open(monitor_cfg, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    return data.get("woocommerce")
                except Exception as e:
                    logger.warning(f"Failed to read WC monitor config: {e}")
                    return None
            else:
                logger.warning("WC monitor config.json not found; cannot load credentials")
                return None
        # Inline credentials
        binding = dict(company.wc_binding)
        binding.pop("use_monitor_config", None)
        return binding


def load_config(path: Optional[Path] = None) -> GlobalInvoiceConfig:
    """Load and parse config.json. Raises ConfigError if missing or invalid."""
    if path is None:
        _migrate_legacy_data()
    cfg_path = Path(path) if path else CONFIG_PATH
    if not cfg_path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        raise ConfigError(
            f"Config not found: {cfg_path}\n\n"
            f"Copy the template into place:\n"
            f"    {CONFIG_EXAMPLE_PATH}\n"
            f"  → {cfg_path}\n\n"
            f"Then edit the company entries with your real legal name, VAT, "
            f"address, and bank details."
        )
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"Invalid JSON in {cfg_path}: {e}") from e
    return GlobalInvoiceConfig(raw, cfg_path)
