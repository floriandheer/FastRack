#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Publish Photos to Webshop

Mirrors the photos in a 3D-print product's
`02_Production/Documentation/Product_Photo/Publish` folder into the alles3d
webshop project as Windows `.lnk` shortcuts at
`Web/_Personal/alles3d/products/<product>/photos`.

Sync semantics: any image present in Publish gets a matching `<name>.lnk` in
the destination; any existing `.lnk` whose source no longer exists is removed.
Files in the destination that don't end in `.lnk` are left alone.

Invoked from the Project Tracker's Actions panel for Physical/Product
projects; argv[1] is the active project folder.
"""

import os
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import List, Tuple

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from shared_logging import get_logger, setup_logging
from rak_settings import get_rak_settings
from shared_open_path import open_path

MODULE_NAME = "publish_to_webshop"
logger = get_logger(MODULE_NAME)

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".heic", ".bmp"}
FOLDER_NAME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_3DPrint_(.+)$")


def _platform_path(windows_path: str) -> Path:
    """Same drive-letter→/mnt conversion the project explorer uses."""
    if sys.platform == "win32":
        return Path(windows_path)
    s = str(windows_path).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        drive = s[0].lower()
        rest = s[2:].lstrip("/")
        return Path(f"/mnt/{drive}/{rest}")
    return Path(windows_path)


def _product_name_from_folder(project_folder: Path) -> str:
    m = FOLDER_NAME_RE.match(project_folder.name)
    if not m:
        raise ValueError(
            f"Folder name does not match 'YYYY-MM-DD_3DPrint_<name>': {project_folder.name}"
        )
    return m.group(1)


def _resolve_destination(product_name: str) -> Path:
    web_root = _platform_path(get_rak_settings().get_work_path("Web"))
    return web_root / "_Personal" / "alles3d" / "products" / product_name / "photos"


def _create_shortcut(shortcut_path: Path, target_path: Path) -> bool:
    try:
        import win32com.client
    except ImportError:
        logger.error("pywin32 not available — cannot create .lnk shortcuts")
        return False
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortCut(str(shortcut_path))
        sc.TargetPath = str(target_path)
        sc.WorkingDirectory = str(target_path.parent)
        sc.save()
        return True
    except Exception as e:
        logger.error(f"Failed to create shortcut {shortcut_path}: {e}")
        return False


def sync_publish_to_webshop(project_folder: Path) -> Tuple[List[str], List[str], List[str]]:
    """Sync Publish/ images → destination /photos as .lnk shortcuts.

    Returns (added, removed, skipped) — lists of filenames.
    """
    product_name = _product_name_from_folder(project_folder)
    source_dir = project_folder / "02_Production" / "Documentation" / "Product_Photo" / "Publish"
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Publish folder not found: {source_dir}")

    dest_dir = _resolve_destination(product_name)
    dest_dir.mkdir(parents=True, exist_ok=True)

    source_files = {
        f.name: f for f in source_dir.iterdir()
        if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS
    }
    existing_links = {
        f.name[:-len(".lnk")]: f for f in dest_dir.iterdir()
        if f.is_file() and f.suffix.lower() == ".lnk"
    }

    added, removed, skipped = [], [], []

    for fname, src in source_files.items():
        if fname in existing_links:
            skipped.append(fname)
            continue
        lnk = dest_dir / f"{fname}.lnk"
        if _create_shortcut(lnk, src):
            added.append(fname)
        else:
            skipped.append(fname)

    for fname, lnk in existing_links.items():
        if fname not in source_files:
            try:
                lnk.unlink()
                removed.append(fname)
            except Exception as e:
                logger.error(f"Failed to remove stale shortcut {lnk}: {e}")

    return added, removed, skipped


def _show_summary(project_folder: Path, dest_dir: Path,
                  added: List[str], removed: List[str], skipped: List[str]):
    root = tk.Tk()
    root.title("Publish Photos to Webshop")
    root.geometry("640x420")
    root.configure(bg="#0d1117")

    try:
        from shared_window_icon import apply_category_icon
        apply_category_icon(root, "Physical")
    except Exception:
        pass

    tk.Label(
        root, text="🔗 Sync complete",
        bg="#0d1117", fg="white", font=("Arial", 14, "bold"),
    ).pack(anchor="w", padx=16, pady=(14, 4))

    summary = (
        f"Added: {len(added)}     "
        f"Removed (stale): {len(removed)}     "
        f"Already linked: {len(skipped)}"
    )
    tk.Label(root, text=summary, bg="#0d1117", fg="#8b949e",
             font=("Arial", 10)).pack(anchor="w", padx=16, pady=(0, 8))
    tk.Label(root, text=f"Destination: {dest_dir}", bg="#0d1117", fg="#58a6ff",
             font=("Arial", 9), wraplength=600, justify="left").pack(anchor="w", padx=16, pady=(0, 10))

    body = tk.Text(root, bg="#161b22", fg="white", font=("Consolas", 9),
                   relief=tk.FLAT, wrap="none")
    body.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 10))

    def write(section, items, color):
        if not items:
            return
        body.insert(tk.END, f"{section} ({len(items)})\n", section)
        body.tag_config(section, foreground=color, font=("Consolas", 9, "bold"))
        for n in items:
            body.insert(tk.END, f"  {n}\n")
        body.insert(tk.END, "\n")

    write("Added", added, "#3fb950")
    write("Removed", removed, "#f85149")
    write("Already linked", skipped, "#8b949e")
    body.config(state=tk.DISABLED)

    btns = tk.Frame(root, bg="#0d1117")
    btns.pack(fill=tk.X, padx=16, pady=(0, 14))
    tk.Button(btns, text="Open destination",
              command=lambda: open_path(str(dest_dir)),
              bg="#238636", fg="white", relief=tk.FLAT, padx=12, pady=6,
              cursor="hand2").pack(side=tk.LEFT)
    tk.Button(btns, text="Close", command=root.destroy,
              bg="#1c2128", fg="white", relief=tk.FLAT, padx=12, pady=6,
              cursor="hand2").pack(side=tk.RIGHT)

    root.mainloop()


def main():
    setup_logging(MODULE_NAME)

    if len(sys.argv) < 2:
        print("Usage: PipelineScript_Physical_PublishToWebshop.py <project_folder>", file=sys.stderr)
        sys.exit(2)

    project_folder = Path(sys.argv[1])
    if not project_folder.is_dir():
        messagebox.showerror("Publish Photos", f"Project folder not found:\n{project_folder}")
        sys.exit(1)

    try:
        product_name = _product_name_from_folder(project_folder)
        dest_dir = _resolve_destination(product_name)
        added, removed, skipped = sync_publish_to_webshop(project_folder)
    except FileNotFoundError as e:
        messagebox.showwarning("Publish Photos", str(e))
        sys.exit(0)
    except ValueError as e:
        messagebox.showerror("Publish Photos", str(e))
        sys.exit(1)
    except Exception as e:
        logger.exception("publish_to_webshop failed")
        messagebox.showerror("Publish Photos", f"Sync failed:\n{e}")
        sys.exit(1)

    logger.info(
        f"Synced {project_folder.name} → {dest_dir} "
        f"(added={len(added)}, removed={len(removed)}, skipped={len(skipped)})"
    )
    _show_summary(project_folder, dest_dir, added, removed, skipped)


if __name__ == "__main__":
    main()
