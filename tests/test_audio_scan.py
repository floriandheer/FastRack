"""Audio projects sit directly under Audio with a PR / DJ prefix, like the other categories."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

import fastrack_project_explorer as fpe  # noqa: E402

parse = fpe.ProjectImporter._parse_folder_name


def test_pr_folder_without_client():
    r = parse("2020-08-11_PR_Arend", "Audio")
    assert (r["date"], r["client"], r["project"], r["type"]) == ("2020-08-11", "Personal", "Arend", "Audio-Production")


def test_pr_folder_with_client():
    r = parse("2024-02-03_PR_Acme_Jingle", "Audio")
    assert (r["client"], r["project"], r["is_personal"]) == ("Acme", "Jingle", False)


def test_dj_folder():
    r = parse("2023-08-04_DJ_Techno01", "Audio")
    assert (r["project"], r["type"], r["date"]) == ("Techno01", "Audio-DJ", "2023-08-04")


def test_dj_project_name_may_contain_underscores():
    assert parse("2023-09-29_DJ_TVerdriet_Klusterrock", "Audio")["project"] == "TVerdriet_Klusterrock"


def test_personal_and_sandbox_folders_have_no_client():
    assert parse("2025-01-01_PR_Acme_Idea", "Audio_Personal")["client"] == "Personal"
    assert parse("2025-01-01_PR_Idea", "Audio_Sandbox")["type"] == "Audio-Production"


def test_untagged_dated_folder_is_still_production():
    r = parse("2021-03-04_Acme_Track", "Audio")
    assert (r["type"], r["client"], r["project"]) == ("Audio-Production", "Acme", "Track")


def test_stage_folders_and_undated_folders_are_not_projects():
    for name in ("01_Prod", "02_DJ", "Samples", "DJSet_Techno01 Project"):
        assert parse(name, "Audio") is None


def test_audio_types_belong_to_the_audio_category():
    app = fpe.ProjectTrackerApp.__new__(fpe.ProjectTrackerApp)
    assert app._get_category_for_type("Audio-DJ") == "Audio"
    assert app._get_category_for_type("Audio-Production") == "Audio"


def test_scan_folders_for_audio():
    active, archive = fpe.ProjectImporter._build_scan_directories()
    assert {"Audio", "Audio_Personal", "Audio_Sandbox"} <= set(active)
    assert {"Audio", "Audio_Personal", "Audio_Sandbox"} <= set(archive)
    assert active["Audio_Sandbox"].name == "_Sandbox" and active["Audio_Sandbox"].parent.name == "Audio"
