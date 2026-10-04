"""Sibling-file versioning: parsing, grouping and version-up."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "modules"))

from sandbox_versioning import (  # noqa: E402
    collapse_to_latest, list_versions, split_version, version_up,
)


def _touch(path: Path, text: str = "x") -> str:
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_split_version():
    assert split_version("report.docx") == ("report", 1, ".docx")
    assert split_version("report_v003.docx") == ("report", 3, ".docx")
    assert split_version("my_v2_draft_v010.txt") == ("my_v2_draft", 10, ".txt")
    assert split_version("noext_v002") == ("noext", 2, "")


def test_list_versions_newest_first_and_implicit_v1(tmp_path):
    base = _touch(tmp_path / "report.docx")
    _touch(tmp_path / "report_v002.docx")
    _touch(tmp_path / "report_v003.docx")
    _touch(tmp_path / "other.docx")
    _touch(tmp_path / "report_v002.txt")  # different extension = different doc

    versions = list_versions(base)
    assert [v.number for v in versions] == [3, 2, 1]
    assert versions[0].is_latest and not versions[1].is_latest


def test_collapse_to_latest(tmp_path):
    files = [
        _touch(tmp_path / "a.txt"), _touch(tmp_path / "a_v002.txt"),
        _touch(tmp_path / "b.txt"),
    ]
    result = collapse_to_latest(files)
    assert result == [(str(tmp_path / "a_v002.txt"), 2), (str(tmp_path / "b.txt"), 1)]


def test_version_up_copies_latest_not_selected(tmp_path):
    base = _touch(tmp_path / "report.docx", "one")
    _touch(tmp_path / "report_v002.docx", "two")

    new_path = version_up(base)  # selected old version, latest is v002

    assert Path(new_path).name == "report_v003.docx"
    assert Path(new_path).read_text(encoding="utf-8") == "two"
    assert Path(base).read_text(encoding="utf-8") == "one"  # untouched


def test_version_up_from_unversioned_file(tmp_path):
    base = _touch(tmp_path / "notes.md")
    assert Path(version_up(base)).name == "notes_v002.md"


def test_tags_apply_to_all_versions_and_count_once(tmp_path):
    from sandbox_tag_store import SandboxTagStore

    store = SandboxTagStore(str(tmp_path / "tags.json"))
    paths = [
        _touch(tmp_path / "report.docx"),
        _touch(tmp_path / "report_v002.docx"),
    ]
    other = _touch(tmp_path / "other.docx")

    store.set_tags_bulk(paths, ["client", " draft "])
    store.set_tags(other, ["client"])

    assert store.get_tags(paths[0]) == store.get_tags(paths[1]) == ["client", "draft"]
    assert store.all_tags() == {"client": 3, "draft": 2}
    assert store.all_tags(by_document=True) == {"client": 2, "draft": 1}

    store.set_tags_bulk(paths, [])
    assert store.get_tags(paths[1]) == []
