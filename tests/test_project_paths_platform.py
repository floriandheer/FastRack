"""The Project Finder on macOS: project folders must be found and stored as native paths.

Its scan folders were built as '<active base>\\Visual' (a literal backslash) and the project database turned
every '/' of a stored path into '\\', so on a Mac nothing was ever found or kept. Windows and WSL behaviour is
checked to be unchanged. Everything lives in tmp_path; nothing touches the real config or database.
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

import fastrack_project_explorer as fpe  # noqa: E402
import shared_project_db  # noqa: E402
from shared_project_db import ProjectDatabase  # noqa: E402

posix_only = pytest.mark.skipif(os.name == "nt", reason="simulates macOS/Linux with POSIX paths")


@pytest.fixture
def mac(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def work(tmp_path, monkeypatch):
    """A Mac-style tree under tmp_path, and the settings the app would load for it."""
    active, archive = tmp_path / "_work" / "Active", tmp_path / "_work" / "Archive"
    for folder in ("Photo/_Personal/2025-10-19_Zele_DagVanDeTrageWegen",
                   "Photo/_Personal/2024-01-03_Hofen_MeTime",
                   "Photo/_Sandbox/2019-09-30_RAWExperimenten",
                   "Visual/2024-05-02_Acme_Poster",
                   "Visual/_LIBRARY",
                   "Audio/2025-03-01_PR_Artist_Track"):
        (active / folder).mkdir(parents=True)
    (archive / "Visual" / "2023-01-10_OldClient_Logo").mkdir(parents=True)

    settings = SimpleNamespace(
        get_active_base=lambda: str(active), get_archive_base=lambda: str(archive),
        get_work_drive=lambda: str(active),
        convert_to_work_drive_path=lambda path: path,       # work root == active base off Windows
    )
    monkeypatch.setattr(fpe, "get_rack_settings", lambda: settings)
    monkeypatch.setattr(shared_project_db, "get_rack_settings", lambda: settings)
    return SimpleNamespace(active=active, archive=archive, settings=settings)


# ---- _get_platform_path ------------------------------------------------------------------------------------------


@posix_only
def test_a_posix_path_with_windows_separators_gets_slashes_off_windows(mac):
    assert fpe._get_platform_path("/Users/me/_work/Active\\Visual\\_Personal") == Path(
        "/Users/me/_work/Active/Visual/_Personal")
    assert fpe._get_platform_path("/Users/me/_work/Active/Visual") == Path("/Users/me/_work/Active/Visual")


def test_drive_letter_paths_still_map_to_mnt_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert fpe._get_platform_path("D:\\_work\\Active\\Visual") == Path("/mnt/d/_work/Active/Visual")
    assert fpe._get_platform_path("I:/Visual") == Path("/mnt/i/Visual")


def test_get_platform_path_on_windows_is_untouched(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    assert fpe._get_platform_path("D:\\_work\\Active") == Path("D:\\_work\\Active")


# ---- the scan folders and the import -------------------------------------------------------------------------------


@posix_only
def test_every_scan_folder_that_exists_on_disk_is_found(mac, work):
    active, archive = fpe.ProjectImporter._build_scan_directories()

    assert active["Visual"] == work.active / "Visual"
    assert active["Photo_Personal"] == work.active / "Photo" / "_Personal"
    assert active["Audio"] == work.active / "Audio"
    assert active["Audio_Sandbox"] == work.active / "Audio" / "_Sandbox"
    assert archive["Audio"] == work.archive / "Audio"
    assert archive["Visual"] == work.archive / "Visual"
    assert all("\\" not in str(p) for p in list(active.values()) + list(archive.values()))
    assert active["Visual"].exists() and active["Photo_Personal"].exists() and archive["Visual"].exists()


@posix_only
def test_importing_finds_the_projects_and_stores_native_paths(mac, work, tmp_path):
    db = ProjectDatabase(db_path=str(tmp_path / "project_database.json"))

    stats = fpe.ProjectImporter.scan_and_import(db)

    assert stats["errors"] == 0 and stats["imported"] >= 5
    paths = {p["path"] for p in db.get_all_projects(status="all")}
    assert str(work.active / "Photo" / "_Personal" / "2025-10-19_Zele_DagVanDeTrageWegen") in paths
    assert str(work.active / "Visual" / "2024-05-02_Acme_Poster") in paths
    assert str(work.archive / "Visual" / "2023-01-10_OldClient_Logo") in paths
    assert not any("\\" in p for p in paths)
    assert [p["status"] for p in db.get_all_projects(status="archived")] == ["archived"]
    assert (work.active / "Visual" / "_LIBRARY").is_dir()           # '_' folders are no projects, and untouched


@posix_only
def test_importing_twice_does_not_duplicate_and_survives_a_restart(mac, work, tmp_path):
    db_file = tmp_path / "project_database.json"
    first = fpe.ProjectImporter.scan_and_import(ProjectDatabase(db_path=str(db_file)))
    again = fpe.ProjectImporter.scan_and_import(ProjectDatabase(db_path=str(db_file)))   # fresh load from disk

    assert first["imported"] > 0
    assert again["imported"] == 0 and again["skipped"] == first["imported"]


# ---- the database's path normalisation ---------------------------------------------------------------------------


@posix_only
def test_native_posix_paths_are_stored_as_they_are_off_windows(mac, work, tmp_path):
    db = ProjectDatabase(db_path=str(tmp_path / "db.json"))
    assert db.normalize_path("/Users/me/_work/Active/Visual/2024-05-02_Acme_Poster") == \
        "/Users/me/_work/Active/Visual/2024-05-02_Acme_Poster"
    assert db.normalize_path("") == ""


def test_wsl_and_windows_normalisation_is_unchanged(tmp_path, monkeypatch):
    settings = SimpleNamespace(get_work_drive=lambda: "I:", get_active_base=lambda: "D:\\_work\\Active")
    monkeypatch.setattr(shared_project_db, "get_rack_settings", lambda: settings)
    db = ProjectDatabase(db_path=str(tmp_path / "db.json"))

    monkeypatch.setattr(sys, "platform", "linux")           # WSL
    assert db.normalize_path("/mnt/d/_work/Active/Visual") == "D:\\_work\\Active\\Visual"

    monkeypatch.setattr(sys, "platform", "win32")
    assert db.normalize_path("I:/Visual/Proj") == "D:\\_work\\Active\\Visual\\Proj"
    assert db.normalize_path("D:/_work/Archive/Visual") == "D:\\_work\\Archive\\Visual"
