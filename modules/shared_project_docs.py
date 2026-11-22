"""Where a project's own documents live.

A project's documents (Project_Details.txt, Order_Details_<n>.txt, invoice
shortcuts) go in its ``_LIBRARY`` folder when the project has one, and in the
project root otherwise. ``_LIBRARY`` is optional in some templates (Physical)
and absent in others (Godot, TouchDesigner, Live Video, Audio Production), so
nothing here ever creates it just to hold a document.
"""

from pathlib import Path
from typing import Optional, Union

LIBRARY_DIRNAME = "_LIBRARY"
PROJECT_DETAILS_FILENAME = "Project_Details.txt"

PathLike = Union[str, Path]


def project_docs_dir(project_dir: PathLike) -> Path:
    """The project's ``_LIBRARY`` folder if it exists, else the project root."""
    root = Path(project_dir)
    library = root / LIBRARY_DIRNAME
    return library if library.is_dir() else root


def find_project_details(project_dir: PathLike) -> Optional[Path]:
    """The existing Project_Details.txt (``_LIBRARY`` first, then root), or None."""
    root = Path(project_dir)
    for candidate in (root / LIBRARY_DIRNAME / PROJECT_DETAILS_FILENAME,
                      root / PROJECT_DETAILS_FILENAME):
        if candidate.is_file():
            return candidate
    return None


def ensure_library_dir(project_dir: PathLike) -> Path:
    """Create the project's ``_LIBRARY`` (WooCommerce orders always have one)
    and move a root Project_Details.txt into it, so all documents sit together.
    Returns the ``_LIBRARY`` path."""
    root = Path(project_dir)
    library = root / LIBRARY_DIRNAME
    library.mkdir(parents=True, exist_ok=True)
    root_details = root / PROJECT_DETAILS_FILENAME
    library_details = library / PROJECT_DETAILS_FILENAME
    if root_details.is_file() and not library_details.exists():
        root_details.rename(library_details)
    return library


def project_details_target(project_dir: PathLike) -> Path:
    """Where Project_Details.txt is, or should be written if it doesn't exist yet."""
    return find_project_details(project_dir) or project_docs_dir(project_dir) / PROJECT_DETAILS_FILENAME
