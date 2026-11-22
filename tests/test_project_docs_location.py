"""Project documents go in _LIBRARY when the project has one, else in the project root — _LIBRARY is never
created just to hold them (Physical without the _LIBRARY toggle, Godot, TouchDesigner, Live Video, Audio Production)."""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

from shared_project_docs import find_project_details, project_details_target, project_docs_dir  # noqa: E402

DETAILS = "Project_Details.txt"


@pytest.fixture
def bare(tmp_path):
    project = tmp_path / "2026-11-19_3DPrint_Client_Thing"
    (project / "02_Production").mkdir(parents=True)
    return project


@pytest.fixture
def with_library(bare):
    (bare / "_LIBRARY").mkdir()
    return bare


def test_docs_dir_follows_the_library_folder(bare):
    assert project_docs_dir(bare) == bare
    (bare / "_LIBRARY").mkdir()
    assert project_docs_dir(bare) == bare / "_LIBRARY"


def test_existing_details_file_is_found_in_either_place(bare):
    assert find_project_details(bare) is None
    (bare / DETAILS).write_text("x")
    assert find_project_details(bare) == bare / DETAILS
    (bare / "_LIBRARY").mkdir()                       # _LIBRARY added later: the root file is still the one
    assert project_details_target(bare) == bare / DETAILS


def _creator():
    from shared_folder_structure_creator import GenericFolderStructureCreator
    creator = GenericFolderStructureCreator.__new__(GenericFolderStructureCreator)
    creator.extra_field_vars = {}
    creator.extension = None
    return creator


def _project_data(path):
    return {"project_name": "Thing", "client_name": "Client", "date_created": "2026-11-19",
            "notes": "", "path": str(path)}


def test_creator_writes_details_to_root_without_library(bare):
    _creator()._write_specs_file(str(bare), _project_data(bare), {})
    assert (bare / DETAILS).is_file()
    assert not (bare / "_LIBRARY").exists()


def test_creator_writes_details_into_library_when_present(with_library):
    _creator()._write_specs_file(str(with_library), _project_data(with_library), {})
    assert (with_library / "_LIBRARY" / DETAILS).is_file()
    assert not (with_library / DETAILS).exists()


def test_linking_an_order_creates_library_and_moves_details_into_it(bare):
    from shared_folder_structure_creator import upsert_order_number_in_specs
    (bare / DETAILS).write_text("PROJECT DETAILS\nProject: Thing\n", encoding="utf-8")
    upsert_order_number_in_specs(str(bare), "1574")
    assert "Order #: 1574" in (bare / "_LIBRARY" / DETAILS).read_text(encoding="utf-8")
    assert not (bare / DETAILS).exists()


def test_order_documents_always_live_in_library(bare, monkeypatch):
    from invoice_manager import order_project_linker as opl
    monkeypatch.setattr(opl, "_resolve_folder_path", lambda row: bare)
    row = {"metadata": {"woo_order_number": "1574"}}
    assert opl.order_details_path(row) == bare / "_LIBRARY" / "Order_Details_1574.txt"


def test_linker_project_details_follow_the_root_rule(bare, monkeypatch):
    from invoice_manager import order_project_linker as opl
    monkeypatch.setattr(opl, "_resolve_folder_path", lambda row: bare)
    row = {"metadata": {}}

    assert opl.project_details_path(row) == bare / DETAILS
    assert not opl.project_details_exists(row)

    opl.set_website_url(row, "https://example.com")
    assert opl.get_website_url(row) == "https://example.com"
    assert opl.project_details_exists(row)
    assert not (bare / "_LIBRARY").exists()


def test_legacy_root_specs_migrate_to_root_without_library(bare, monkeypatch):
    from invoice_manager import order_project_linker as opl
    monkeypatch.setattr(opl, "_resolve_folder_path", lambda row: bare)
    (bare / "project_specifications.txt").write_text("old")
    assert opl.resolve_or_migrate_project_details({}) == bare / DETAILS
    assert not (bare / "_LIBRARY").exists()


def test_physical_order_always_includes_library(tk_window):
    import tkinter as tk
    from folder_structure_extensions import physical

    ext = physical.PhysicalExtension.__new__(physical.PhysicalExtension)
    for name in ("personal_var", "order_var", "product_var", "project_var", "library_var"):
        setattr(ext, name, tk.BooleanVar(master=tk_window, value=False))
    ext.library_check = tk.Checkbutton(tk_window, variable=ext.library_var)
    ext._library_backup = None

    assert not ext._library_included()
    ext.order_var.set(True)
    ext._sync_order_library()
    assert ext._library_included() and ext.library_var.get()
    assert str(ext.library_check.cget("state")) == "disabled"

    ext.order_var.set(False)
    ext.project_var.set(True)
    ext._sync_order_library()
    assert not ext.library_var.get()                    # the user's own choice comes back
    assert str(ext.library_check.cget("state")) == "normal"
