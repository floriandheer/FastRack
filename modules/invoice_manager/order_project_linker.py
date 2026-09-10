"""Matching/linking logic between Physical/Project folders and WooCommerce
orders — pure, UI-free. Reused by the standalone review tool
(PipelineScript_Physical_LinkOrders.py) and by wc_monitor.OrderMonitor's
conservative live auto-link.

Two distinct operations are offered:

  apply_link()             Metadata-only. Writes DB metadata and upserts
                            one text file. Never renames, moves, creates,
                            or deletes a folder or touches an already-
                            linked row. Fully reversible by construction.

  merge_project_with_order()  Does real file operations: physically moves
                            a Physical/Project folder into the Order tree
                            and folds the paired Order folder's content
                            into it. See its docstring for the exact,
                            deliberately conservative sequencing this
                            uses to guarantee no project file is ever
                            lost, even if something goes wrong partway.
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from shared_logging import get_logger
from invoice_manager.wc_monitor import DocumentManager, sanitize_filename
from shared_folder_structure_creator import upsert_order_number_in_specs

logger = get_logger(__name__)


@dataclass
class MatchCandidate:
    project_row: Dict          # Physical/Project DB row
    order: Dict                # raw WooCommerce order dict
    confidence: str             # "exact" | "possible"
    reason: str


def _order_date_str(order: Dict) -> str:
    return DocumentManager._order_date(order)


def _order_customer_camel(order: Dict) -> str:
    billing = order.get("billing", {}) or {}
    return DocumentManager._camelcase_name(
        billing.get("first_name", ""), billing.get("last_name", "")
    )


def score(project_row: Dict, order: Dict) -> Optional[MatchCandidate]:
    """Compare a Project row's date/client against a WooCommerce order's
    date/customer. Returns "exact" (same date, exact client match),
    "possible" (<=3 days apart, exact or substring client match), or None.
    """
    proj_date = project_row.get("date_created", "")
    proj_client = project_row.get("client_name", "")
    order_date = _order_date_str(order)
    order_client = _order_customer_camel(order)
    order_number = order.get("number", order.get("id"))

    if not proj_date or not order_date:
        return None

    try:
        days_apart = abs(
            (datetime.strptime(proj_date, "%Y-%m-%d") - datetime.strptime(order_date, "%Y-%m-%d")).days
        )
    except ValueError:
        return None

    client_exact = proj_client.strip().lower() == order_client.strip().lower()
    client_fuzzy = bool(proj_client) and (
        proj_client.lower() in order_client.lower() or order_client.lower() in proj_client.lower()
    )

    if days_apart == 0 and client_exact:
        return MatchCandidate(
            project_row, order, "exact",
            f"same date ({proj_date}), client '{proj_client}' == '{order_client}'",
        )
    if days_apart <= 3 and (client_exact or client_fuzzy):
        return MatchCandidate(
            project_row, order, "possible",
            f"{days_apart}d apart, client '{proj_client}' ~ '{order_client}' (order #{order_number})",
        )
    return None


def propose_matches(project_rows: List[Dict], orders: List[Dict]) -> List[MatchCandidate]:
    """One candidate per unlinked project row (its best-scoring order).

    Rows already carrying metadata.woo_order_number are skipped entirely —
    an existing link is never reassigned or reconsidered. Rows with no
    candidate are simply absent from the result; callers should treat those
    as "needs manual pairing," never silently skipped or guessed.
    """
    already_matched_orders = set()
    results: List[MatchCandidate] = []
    for row in project_rows:
        if row.get("metadata", {}).get("woo_order_number"):
            continue
        best: Optional[MatchCandidate] = None
        for order in orders:
            order_number = order.get("number", order.get("id"))
            if order_number in already_matched_orders:
                continue
            candidate = score(row, order)
            if candidate and (best is None or candidate.confidence == "exact"):
                best = candidate
                if candidate.confidence == "exact":
                    break
        if best:
            results.append(best)
            already_matched_orders.add(best.order.get("number", best.order.get("id")))
    return results


def apply_link(db, project_row: Dict, order: Dict, order_folder_row: Optional[Dict] = None) -> None:
    """Record the link: DB metadata on both rows (if an Order-type row also
    exists) plus an "Order #:" line in the Project row's specs file.

    Metadata-only — no shutil, no folder creation, no renaming. Caller is
    responsible for calling db.save() once after a batch of these.
    """
    order_number = str(order.get("number", order.get("id")))
    order_id = order.get("id")

    project_updates = {"woo_order_number": order_number, "woo_order_id": order_id}
    if order_folder_row:
        project_updates["linked_order_id"] = order_folder_row["id"]
        db.update_project_metadata(
            order_folder_row["id"], {"linked_project_id": project_row["id"]}, auto_save=False
        )
    db.update_project_metadata(project_row["id"], project_updates, auto_save=False)

    upsert_order_number_in_specs(project_row["path"], order_number=order_number)


# ============================================================
# Physical merge — Project folder + Order folder -> one folder
# ============================================================

class MergeError(Exception):
    """Raised when a Project/Order merge can't proceed safely.

    Nothing destructive has happened when this is raised. Worst case: the
    Project folder was already moved to its new location (itself a
    lossless whole-directory move — nothing inside it was touched file by
    file) plus some already-copied files sitting in the target as harmless
    duplicates. The source Order folder is never modified or moved aside
    until every single file from it has been copied and verified.
    """


@dataclass
class MergeResult:
    target_path: Path
    holding_path: Path
    files_merged: int


# Substrings used to match an Order-folder subfolder (01_Incoming,
# 02_Production, 03_Outgoing, _LIBRARY) to its equivalent in whatever
# subfolder structure the Project folder happens to use (numbering and
# naming has drifted across template versions over time) — mirrors the
# existing substring-matching convention already used by
# InvoiceFiler._find_outgoing_folder in wc_monitor.py.
_SUBFOLDER_KEYWORDS = ("incoming", "production", "outgoing", "library")


def _resolve_folder_path(project_row: Dict) -> Path:
    """Resolve a project row's real on-disk folder, preferring the
    work-drive path for active projects.

    Self-contained copy of the same resolution logic used by
    RenameManager/ArchiveManager in fastrack_project_explorer.py —
    duplicated here (rather than imported) so this module stays UI-free
    and importable without pulling in Tkinter, matching how ArchiveManager
    and RenameManager each already keep their own copy rather than share
    one.
    """
    from rack_settings import get_rack_settings
    stored_path = project_row.get("path", "")
    status = project_row.get("status", "active")
    if status != "active":
        return Path(stored_path)
    try:
        settings = get_rack_settings()
        folder = settings.convert_to_work_drive_path(stored_path)
        if not Path(folder).exists():
            folder = stored_path
        return Path(folder)
    except Exception:
        return Path(stored_path)


def _find_matching_subfolder(parent: Path, keyword: str) -> Optional[Path]:
    """Find an immediate child of parent whose name contains keyword
    (case-insensitive), regardless of numeric prefix."""
    if not parent.exists():
        return None
    keyword = keyword.lower()
    for item in parent.iterdir():
        if item.is_dir() and keyword in item.name.lower():
            return item
    return None


def _safe_destination(dest_dir: Path, filename: str) -> Path:
    """Non-colliding destination path for filename inside dest_dir.

    Never overwrites an existing file — a name clash gets a
    "_fromOrder_N" suffix instead.
    """
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    n = 1
    while True:
        candidate = dest_dir / f"{stem}_fromOrder_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _copy_contents_safe(src_dir: Path, dest_dir: Path, copied: List) -> None:
    """Recursively copy every file under src_dir into dest_dir, preserving
    relative structure. Never overwrites (collisions get a _fromOrder_N
    suffix) and never deletes or modifies anything in src_dir.

    Verifies each copy immediately (destination exists, size matches) and
    raises MergeError the instant one fails — since src_dir is never
    touched, a failure here always leaves the source completely intact
    for inspection or retry.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    for item in src_dir.iterdir():
        if item.is_dir():
            _copy_contents_safe(item, dest_dir / item.name, copied)
        else:
            dest_path = _safe_destination(dest_dir, item.name)
            shutil.copy2(str(item), str(dest_path))
            if not dest_path.exists() or dest_path.stat().st_size != item.stat().st_size:
                raise MergeError(f"Copy verification failed for {item} -> {dest_path}")
            copied.append((item, dest_path))


def merge_project_with_order(db, project_row: Dict, order_row: Dict, order: Dict) -> MergeResult:
    """
    Physically merge a Physical/Project folder with its paired Physical/
    Order folder into one folder living in the Order tree — keeping the
    WooCommerce order's date, the project's own name, and the union of
    both folders' content.

    Sequencing (each step only proceeds once the previous one is verified
    safe — see MergeError for the exact failure-mode guarantee):

      1. Move the whole Project folder in one atomic rename into
         Physical/Order/<order-date>_<client>_<project-name>_<order-num>.
         The Project's own content is never touched file-by-file, so
         nothing in it can be dropped, duplicated, or corrupted by this
         step, regardless of what happens afterward.
      2. Copy (never move) every file from the old Order folder into the
         matching subfolder of the relocated folder, verifying each copy
         immediately. Nothing in the Order folder is deleted or modified
         at this stage.
      3. Only once every file is verified copied: move the now-redundant
         Order folder aside into a _MergedOrderFolders holding directory
         next to the other Order folders (never deleted) so it can be
         double-checked and removed manually later.
      4. Update the database: the Project row becomes the sole surviving
         row (new path, order's date, physical_subtype becomes "Order" to
         match its new location, order-number metadata attached). The old
         Order row is archived to its holding-folder path via
         db.archive_project — reusing the existing, already-tested
         archive/un-archive machinery, so it stays fully inspectable and
         reversible with the Un-Archive button if anything looks wrong.
    """
    project_folder = _resolve_folder_path(project_row)
    order_folder = _resolve_folder_path(order_row)

    if not project_folder.exists():
        raise MergeError(f"Project folder does not exist:\n{project_folder}")
    if not order_folder.exists():
        raise MergeError(f"Order folder does not exist:\n{order_folder}")

    order_number = str(order.get("number", order.get("id")))
    order_date = DocumentManager._order_date(order)
    client = project_row.get("client_name", "")
    safe_name = sanitize_filename(project_row.get("project_name", "")).strip()
    if not safe_name:
        raise MergeError("Project has no name to merge under.")

    target_name = "_".join(p for p in (order_date, client, safe_name, order_number) if p)
    target_path = order_folder.parent / target_name

    if target_path.exists():
        raise MergeError(f"A folder already exists at the target location:\n{target_path}")

    # Step 1 — move the Project folder in one shot.
    logger.info(f"Merge: moving project folder {project_folder} -> {target_path}")
    project_folder.rename(target_path)

    # Step 2 — copy the Order folder's content into the relocated folder.
    copied: List = []
    try:
        for item in order_folder.iterdir():
            if item.is_dir():
                keyword = next((k for k in _SUBFOLDER_KEYWORDS if k in item.name.lower()), None)
                if keyword:
                    dest_subdir = _find_matching_subfolder(target_path, keyword)
                    if dest_subdir is None:
                        # No equivalent exists yet in the target (e.g. no
                        # _LIBRARY) — create one using the order folder's
                        # own already-standard name, at the top level
                        # rather than quarantined, since we recognize
                        # exactly what kind of content this is.
                        dest_subdir = target_path / item.name
                else:
                    # Genuinely unrecognized folder name — quarantine so
                    # nothing is silently misplaced under an assumed
                    # category it may not belong to.
                    dest_subdir = target_path / "_LIBRARY" / "FromOrderFolder" / item.name
                _copy_contents_safe(item, dest_subdir, copied)
            else:
                # Loose top-level file directly in the order folder
                # (unusual, but handle it) — same quarantine treatment.
                dest_dir = target_path / "_LIBRARY" / "FromOrderFolder"
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest_path = _safe_destination(dest_dir, item.name)
                shutil.copy2(str(item), str(dest_path))
                if not dest_path.exists() or dest_path.stat().st_size != item.stat().st_size:
                    raise MergeError(f"Copy verification failed for {item} -> {dest_path}")
                copied.append((item, dest_path))
    except MergeError as e:
        raise MergeError(
            f"{e}\n\n"
            f"Your project folder was safely moved to:\n{target_path}\n"
            f"The order paperwork folder was left untouched at:\n{order_folder}\n\n"
            "Nothing was lost — check the target folder and retry, or copy "
            "the remaining files over manually."
        ) from e

    # Step 3 — only now, move the emptied Order folder aside. Never
    # deleted; _MergedOrderFolders starts with "_" so the project scanner
    # (ProjectImporter.scan_and_import) already skips it, same as
    # _LIBRARY/_Personal/_Sandbox.
    holding_base = order_folder.parent / "_MergedOrderFolders"
    holding_base.mkdir(parents=True, exist_ok=True)
    holding_path = holding_base / order_folder.name
    n = 1
    while holding_path.exists():
        holding_path = holding_base / f"{order_folder.name}_dup_{n}"
        n += 1
    logger.info(f"Merge: moving emptied order folder {order_folder} -> {holding_path}")
    order_folder.rename(holding_path)

    # Step 4 — update the database. Project row becomes the sole survivor;
    # old Order row is archived to its holding-folder path.
    db.rename_project(
        project_row["id"], project_row.get("project_name", safe_name),
        new_path=str(target_path), new_base_directory=str(order_folder.parent),
        auto_save=False,
    )
    db.update_project_metadata(project_row["id"], {
        "physical_subtype": "Order",
        "woo_order_number": order_number,
        "woo_order_id": order.get("id"),
    }, auto_save=False)
    # date_created isn't touched by the calls above — set it directly to
    # the order's date, per the "keep the order's date" requirement.
    fresh_row = db.get_project_by_id(project_row["id"])
    if fresh_row:
        fresh_row["date_created"] = order_date

    db.archive_project(order_row["id"], str(holding_path))  # persists everything
    db.save()

    upsert_order_number_in_specs(str(target_path), order_number=order_number)

    return MergeResult(target_path=target_path, holding_path=holding_path, files_merged=len(copied))


# ============================================================
# Document access — Project Details / Order Details / Invoice
# ============================================================
#
# Used by the Project Tracker's details panel (fastrack_project_explorer.py)
# to open a project's own documentation, for any category. Physical
# projects linked to a WooCommerce order additionally get order details
# and an invoice. All three live side by side in the project's _LIBRARY
# folder.

PROJECT_DETAILS_FILENAME = "Project_Details.txt"


def project_details_path(project_row: Dict) -> Path:
    """Canonical path to a project's own details file, for any category."""
    folder = _resolve_folder_path(project_row)
    return folder / "_LIBRARY" / PROJECT_DETAILS_FILENAME


def _find_legacy_project_details_file(project_row: Dict) -> Optional[Path]:
    """Look for a details file at an older, pre-standardization location
    (project root, or _LIBRARY/Documents — both used before every category
    settled on _LIBRARY/Project_Details.txt), so it can be migrated in
    place on first access instead of silently going undiscovered."""
    folder = _resolve_folder_path(project_row)
    candidates = [
        folder / "project_specifications.txt",
        folder / "_LIBRARY" / "Documents" / "project_specifications.txt",
        folder / "_LIBRARY" / "project_specifications.txt",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def resolve_or_migrate_project_details(project_row: Dict) -> Optional[Path]:
    """Return the canonical _LIBRARY/Project_Details.txt path for this
    project, migrating (move + rename — never a copy that leaves a stale
    duplicate behind) a file found at an older location first. Returns
    None if no details file exists anywhere for this project — nothing is
    fabricated.
    """
    canonical = project_details_path(project_row)
    if canonical.exists():
        return canonical
    legacy = _find_legacy_project_details_file(project_row)
    if legacy is None:
        return None
    canonical.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Migrating project details file: {legacy} -> {canonical}")
    legacy.rename(canonical)
    return canonical


def project_details_exists(project_row: Dict) -> bool:
    """Whether this project has a details file at all — canonical or
    legacy location — without migrating it. Used to decide whether the
    "Project Details" button should be enabled, so it doesn't rename
    files just to answer that question."""
    if project_details_path(project_row).exists():
        return True
    return _find_legacy_project_details_file(project_row) is not None


_WEBSITE_LINE_RE = re.compile(r'^Website:\s*(.*)$')


def get_website_url(project_row: Dict) -> str:
    """Read the live website URL out of the project's own Project_Details.txt
    (a "Website: <url>" line) — the file is the single source of truth for
    this field, so hand-editing it there is the supported way to change it.
    Returns "" if there's no details file, or no such line in it.
    """
    path = project_details_path(project_row)
    if not path.exists():
        path = _find_legacy_project_details_file(project_row)
        if path is None:
            return ""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        match = _WEBSITE_LINE_RE.match(line)
        if match:
            return match.group(1).strip()
    return ""


def set_website_url(project_row: Dict, url: str) -> Path:
    """Set (or, if url is blank, remove) the "Website: <url>" line in the
    project's Project_Details.txt, migrating/creating the file as needed.
    Returns the file path written."""
    path = resolve_or_migrate_project_details(project_row) or project_details_path(project_row)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = ["PROJECT DETAILS", "======================", f"Generated: {timestamp}", ""]

    lines = [line for line in lines if not _WEBSITE_LINE_RE.match(line)]
    url = url.strip()
    if url:
        lines.append(f"Website: {url}")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def order_details_path(project_row: Dict) -> Optional[Path]:
    """Canonical path to a linked project's WooCommerce order-details
    file, or None if the project isn't linked to an order."""
    order_number = project_row.get("metadata", {}).get("woo_order_number")
    if not order_number:
        return None
    folder = _resolve_folder_path(project_row)
    return folder / "_LIBRARY" / f"Order_Details_{order_number}.txt"


def _build_wc_client_if_configured():
    """Build a WooCommerceClient from the saved config, or return None if
    WC credentials haven't been set up yet. Shared by every fetch helper
    below so each one doesn't need to duplicate the credential check."""
    from invoice_manager.wc_monitor import Config as WCConfig, WooCommerceClient
    wc_config = WCConfig()
    if not wc_config.config["woocommerce"].get("consumer_key"):
        return None, wc_config
    return WooCommerceClient(wc_config), wc_config


def linked_physical_projects(db) -> List[Dict]:
    """Every Physical project row (any subtype, any status) that's linked
    to a WooCommerce order — i.e. carries woo_order_number, regardless of
    whether it got there via apply_link, merge_project_with_order, or the
    live auto-link in wc_monitor.

    Filters on woo_order_number only, NOT woo_order_id: the number is
    recoverable from a folder name (ProjectImporter._parse_folder_name),
    but the internal numeric id is not, so a full "Refresh and Import" can
    leave id blank even for a correctly-linked row. See
    _recover_order_id()/backfill_order_ids() for how that gets repaired.
    """
    return [
        p for p in db.get_all_projects(status="all")
        if p.get("project_type") == "Physical"
        and p.get("metadata", {}).get("woo_order_number")
    ]


def backfill_order_ids(db, wc_client) -> int:
    """Bulk repair of every row that has woo_order_number but lost
    woo_order_id (e.g. after a full "Refresh and Import" rebuilt the
    database purely from folder names, which can't recover the numeric
    id). Fetches the live order list once and matches by number.

    Only ever fills in a missing id — never touches a row that already
    has one. Returns the number of rows patched. See _recover_order_id
    for the single-row, self-healing equivalent used by
    resolve_or_fetch_invoice/resolve_or_fetch_order_details so this
    doesn't need to be re-run by hand every time a rebuild happens.
    """
    orders = wc_client.get_all_orders()
    id_by_number = {str(o.get("number", o.get("id"))): o.get("id") for o in orders}

    patched = 0
    for project_row in linked_physical_projects(db):
        metadata = project_row.get("metadata", {})
        if metadata.get("woo_order_id"):
            continue
        order_number = str(metadata.get("woo_order_number"))
        order_id = id_by_number.get(order_number)
        if order_id is None:
            continue
        db.update_project_metadata(project_row["id"], {"woo_order_id": order_id}, auto_save=False)
        metadata["woo_order_id"] = order_id
        patched += 1

    if patched:
        db.save()
    return patched


def _recover_order_id(db, project_row: Dict, wc_client) -> Optional[int]:
    """Single-row version of backfill_order_ids, used inline by
    resolve_or_fetch_invoice/resolve_or_fetch_order_details so a missing
    woo_order_id self-heals the moment it's needed — e.g. right after a
    "Refresh and Import" — rather than requiring a separate manual
    backfill step every time. Persists the recovered id so this only
    costs one lookup per row, ever.

    Returns the id, or None if it can't be found (order genuinely gone,
    no credentials, offline, etc.).
    """
    metadata = project_row.get("metadata", {})
    order_id = metadata.get("woo_order_id")
    if order_id:
        return order_id
    order_number = metadata.get("woo_order_number")
    if not order_number:
        return None
    try:
        orders = wc_client.get_all_orders()
    except Exception as e:
        logger.warning(f"Could not look up order id for #{order_number}: {e}")
        return None
    for o in orders:
        if str(o.get("number", o.get("id"))) == str(order_number):
            order_id = o.get("id")
            db.update_project_metadata(project_row["id"], {"woo_order_id": order_id})
            metadata["woo_order_id"] = order_id
            return order_id
    return None


def resolve_or_fetch_order_details(project_row: Dict, wc_client=None, db=None) -> Optional[Path]:
    """Return the order-details file for a linked project, fetching it
    fresh from WooCommerce and writing it into _LIBRARY if it doesn't
    exist locally yet. Returns None if the project has no linked order, or
    the order can't be reached right now (no credentials, offline, etc.).

    Pass a pre-built wc_client to reuse one across many calls (e.g. a bulk
    backfill) instead of reconstructing it per project. Pass db so a
    missing woo_order_id can be self-healed via _recover_order_id rather
    than just failing.
    """
    metadata = project_row.get("metadata", {})
    canonical = order_details_path(project_row)
    if canonical is None:
        return None
    if canonical.exists():
        return canonical

    wc_config = None
    if wc_client is None:
        wc_client, wc_config = _build_wc_client_if_configured()
        if wc_client is None:
            return None
    if wc_config is None:
        from invoice_manager.wc_monitor import Config as WCConfig
        wc_config = WCConfig()

    order_id = metadata.get("woo_order_id")
    if not order_id and db is not None:
        order_id = _recover_order_id(db, project_row, wc_client)
    if not order_id:
        return None

    order = wc_client.get_order_details(int(order_id))
    if not order:
        return None

    doc_manager = DocumentManager(wc_config, wc_client)
    folder = _resolve_folder_path(project_row)
    written = doc_manager.create_order_details_file(order, folder)
    return Path(written) if written else None


# ============================================================
# Invoices — filed to Boekhouding, linked into _LIBRARY
# ============================================================
#
# An invoice is never stored as a standalone copy inside a project
# folder. The authoritative copy lives in the Boekhouding bookkeeping
# archive (I:\_LIBRARY\Boekhouding\<year>\Q<n>\Uitgaand\), filed there by
# InvoiceFiler.file_invoice using WooCommerce's own invoice number/date/
# client naming. A project's _LIBRARY folder only ever gets a .lnk
# shortcut pointing at that archived file — the same mechanism "File
# Quarter Invoices" already uses, just also reachable per-project from
# here (and from the Project Tracker's Invoice button).

_INVOICE_FILENAME_HINTS = ("invoice", "factuur")
_FACTUUR_NUMBER_RE = re.compile(r"Factuur(\d+)", re.IGNORECASE)


def _find_invoice_by_number_in_dir(dir_path: Path, invoice_number: str) -> Optional[Path]:
    """Fallback for a stale .lnk shortcut whose recorded target no longer
    exists (e.g. the archived Boekhouding file was renamed by hand):
    search the same directory for a PDF whose filename embeds the same
    invoice number, tolerating both padded ("005") and unpadded ("5")
    forms. Invoice numbers are unique per quarter, so this is a safe
    match — and it's read-only (a candidate to link to), so a wrong guess
    would at worst point the shortcut at the wrong PDF, never touch or
    lose anything in the archive itself.
    """
    if not dir_path.exists():
        return None
    try:
        padded = f"{int(invoice_number):03d}"
    except ValueError:
        return None
    unpadded = str(int(invoice_number))
    for item in dir_path.iterdir():
        if not item.is_file() or item.suffix.lower() != ".pdf":
            continue
        m = _FACTUUR_NUMBER_RE.search(item.name)
        if m and m.group(1) in (padded, unpadded, padded.lstrip("0") or "0"):
            return item
    return None


def _reroot_under_current_boekhouding_base(target: Path) -> Optional[Path]:
    """Recover a shortcut target whose whole Boekhouding tree was relocated
    (e.g. the archive nested one level deeper under a new "Facturen"
    subfolder). The target's own trailing <year>/Q<n>/<Uitgaand|
    Binnenkomend>/<file> suffix is usually still valid — just re-rooted
    wherever resolve_boekhouding_base() points today. Read-only: never
    touches the stale target itself. Returns the re-rooted file if it
    actually exists there, else None.
    """
    parts = target.parts
    if len(parts) < 4:
        return None
    tail = Path(*parts[-4:-1])  # <year>/Q<n>/<Uitgaand|Binnenkomend>
    try:
        from invoice_manager.core.config import load_config
        current_base = load_config().resolve_boekhouding_base()
    except Exception:
        return None
    candidate = current_base / tail / target.name
    return candidate if candidate.exists() else None


def _resolve_shortcut_target(lnk_path: Path) -> Optional[Path]:
    """Read a Windows .lnk shortcut's target path, or None if it can't be
    resolved (not on Windows, broken shortcut, missing pywin32, etc.).

    If the recorded target no longer exists, first tries re-rooting it
    under the current boekhouding_base (handles the whole archive tree
    being relocated), then falls back to searching its own recorded
    parent directory for a PDF with the same invoice number embedded in
    its name (handles a single archived invoice renamed in place).
    Either way, a recovered target is written back into the shortcut so
    it opens correctly next time too, and so this lookup only costs
    once, ever — same self-healing approach as _recover_order_id.
    """
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(str(lnk_path))
        target = Path(shortcut.TargetPath) if shortcut.TargetPath else None
        if target and target.exists():
            return target
        if not target:
            return None

        recovered = _reroot_under_current_boekhouding_base(target)
        if recovered is None:
            m = _FACTUUR_NUMBER_RE.search(target.name)
            if m:
                recovered = _find_invoice_by_number_in_dir(target.parent, m.group(1))

        if recovered:
            logger.info(
                f"Shortcut target moved/renamed since filing — repairing "
                f"{lnk_path.name}: {target} -> {recovered}"
            )
            shortcut.TargetPath = str(recovered)
            shortcut.WorkingDirectory = str(recovered.parent)
            shortcut.save()
            return recovered
        return None
    except Exception as e:
        logger.warning(f"Could not resolve shortcut target for {lnk_path}: {e}")
        return None


def _find_library_invoice_link(library_dir: Path) -> Optional[Path]:
    """Return the first invoice-looking .lnk shortcut already sitting in
    _LIBRARY — the "already filed and linked" fast path, local-only, no
    network needed — or None."""
    if not library_dir.exists():
        return None
    for item in library_dir.iterdir():
        if item.is_file() and item.suffix.lower() == ".lnk" and any(
            hint in item.name.lower() for hint in _INVOICE_FILENAME_HINTS
        ):
            return item
    return None


def resolve_or_fetch_invoice(project_row: Dict, wc_client=None, db=None) -> Optional[Path]:
    """Ensure this project's linked WooCommerce order has its invoice
    properly filed to the Boekhouding archive and linked into _LIBRARY as
    a .lnk shortcut — never a separate raw PDF copy; the shortcut *is*
    the local presence, exactly like every other filed invoice.

    Fast path (no network) if a working shortcut already sits in
    _LIBRARY. Otherwise fetches the order, calls the same
    InvoiceFiler.file_invoice() "File Quarter Invoices" already uses
    (download to Boekhouding with its real invoice-number/date/client
    filename, then create the shortcut), and returns the new shortcut.

    Cleans up a superseded raw Invoice_<num>.pdf left behind by an
    earlier version of this function, but only once the proper shortcut
    is confirmed in place — nothing is ever removed on a failed attempt.

    Returns the shortcut path, or None if there's nothing to link yet
    (no linked order, invoice not generated on the WC side, credentials
    not configured, etc.). Pass a pre-built wc_client to reuse one across
    many calls; pass db so a missing woo_order_id self-heals via
    _recover_order_id instead of just failing.
    """
    metadata = project_row.get("metadata", {})
    order_number = metadata.get("woo_order_number")
    if not order_number:
        return None

    folder = _resolve_folder_path(project_row)
    library_dir = folder / "_LIBRARY"

    existing_link = _find_library_invoice_link(library_dir)
    if existing_link and _resolve_shortcut_target(existing_link):
        return existing_link

    if wc_client is None:
        wc_client, _ = _build_wc_client_if_configured()
        if wc_client is None:
            return None

    order_id = metadata.get("woo_order_id")
    if not order_id and db is not None:
        order_id = _recover_order_id(db, project_row, wc_client)
    if not order_id:
        return None

    order = wc_client.get_order_details(int(order_id))
    if not order:
        return None

    from invoice_manager.wc_monitor import InvoiceFiler
    invoice_filer = InvoiceFiler(wc_client.config, wc_client)

    # Before filing — which unconditionally downloads a fresh PDF — check
    # whether this invoice is already archived under some other filename
    # (e.g. renamed by hand after filing). Link to that instead of
    # creating a redundant duplicate re-download in Boekhouding.
    invoice_info = invoice_filer._get_invoice_info_from_meta(order)
    existing_archived = None
    if invoice_info:
        quarter_dir = invoice_filer._get_quarter_dir(invoice_info["invoice_date"])
        existing_archived = _find_invoice_by_number_in_dir(quarter_dir, invoice_info["invoice_number"])

    if existing_archived:
        library_dir.mkdir(parents=True, exist_ok=True)
        shortcut_name = existing_archived.stem + ".lnk"
        invoice_filer._create_shortcut(library_dir / shortcut_name, existing_archived)
    else:
        filed_path = invoice_filer.file_invoice(order, folder)
        if not filed_path:
            return None

    new_link = _find_library_invoice_link(library_dir)

    if new_link:
        stale_raw = library_dir / f"Invoice_{order_number}.pdf"
        if stale_raw.exists() and stale_raw != new_link:
            stale_raw.unlink()
            logger.info(f"Removed superseded raw invoice copy: {stale_raw}")

    return new_link


def ensure_all_invoices_linked(db, wc_client, progress_callback=None) -> Dict[str, object]:
    """Bulk version of resolve_or_fetch_invoice: ensures every
    WooCommerce-linked Physical project has its invoice filed to
    Boekhouding and linked into _LIBRARY. Repairs any missing
    woo_order_id first (backfill_order_ids) so rows broken by a
    "Refresh and Import" are picked up in the same pass.

    progress_callback(index, total, project_name), if given, is called
    before each project is processed.

    Returns {"linked": int, "already_linked": int, "failed": List[str]}
    — failed holds "<project_name> (order #<n>)" for each project that
    couldn't be filed/linked (see the app log for the specific reason).
    """
    backfill_order_ids(db, wc_client)

    rows = linked_physical_projects(db)
    stats = {"linked": 0, "already_linked": 0, "failed": []}

    for i, project_row in enumerate(rows, start=1):
        order_number = project_row.get("metadata", {}).get("woo_order_number")
        if progress_callback:
            progress_callback(i, len(rows), project_row.get("project_name", "?"))

        folder = _resolve_folder_path(project_row)
        already = _find_library_invoice_link(folder / "_LIBRARY")
        was_already_linked = bool(already and _resolve_shortcut_target(already))

        try:
            result = resolve_or_fetch_invoice(project_row, wc_client=wc_client, db=db)
        except Exception as e:
            logger.warning(f"Invoice linking failed for order #{order_number}: {e}")
            result = None

        if result and was_already_linked:
            stats["already_linked"] += 1
        elif result:
            stats["linked"] += 1
        else:
            stats["failed"].append(f"{project_row.get('project_name', '?')} (order #{order_number})")

    return stats
