"""Physical projects carry a kind (3DPrint, CNC, Technical, Arch) from the tag in the folder name."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "modules"))

import fastrack_project_explorer as fpe  # noqa: E402
from pipeline_categories import kind_style  # noqa: E402

parse = fpe.ProjectImporter._parse_folder_name


def test_cnc_project_with_client():
    r = parse("2018-02-15_CNC_Saam_Uiltje", "Physical_Project")
    assert (r["client"], r["project"], r["physical_kind"], r["type"]) == ("Saam", "Uiltje", "CNC", "Physical")


def test_technical_personal_project_keeps_whole_name():
    r = parse("2020-11-28_Technical_RookTon", "Physical_Personal")
    assert (r["client"], r["project"], r["physical_kind"], r["is_personal"]) == ("Personal", "RookTon", "Technical", True)


def test_personal_3dprint_keeps_the_whole_name():
    r = parse("2024-10-17_3DPrint_LeatherStamp_Bert&Sarah", "Physical_Personal")
    assert r["project"] == "LeatherStamp_Bert&Sarah" and r["client"] == "Personal"


def test_product_gets_the_implicit_client():
    r = parse("2025-04-19_3DPrint_Sleutelhanger", "Physical_Product")
    assert (r["client"], r["physical_kind"], r["physical_subtype"]) == ("alles3d", "3DPrint", "Product")


def test_stray_space_after_the_date_is_tolerated():
    r = parse("2026-05-13_ 3DPrint_JohanVanDeMoortel_BubbelbadDop", "Physical_Project")
    assert (r["client"], r["project"]) == ("JohanVanDeMoortel", "BubbelbadDop")


def test_kind_falls_back_to_the_folder_tag_then_to_3dprint():
    assert fpe.physical_kind({"path": r"D:\w\Physical\Project\2018-02-15_CNC_Saam_Uiltje"}) == "CNC"
    assert fpe.physical_kind({"path": "D:/w/Physical/Order/2025-06-17_DirkScheers_Dodecahedron_1574"}) == "3DPrint"
    assert fpe.physical_kind({"path": "x", "metadata": {"physical_kind": "Arch"}}) == "Arch"


def test_every_kind_has_a_distinct_label_and_colour():
    styles = [kind_style("Physical", k) for k in ("3DPrint", "CNC", "Technical", "Arch")]
    assert len({s["badge"] for s in styles}) == 4 and len({s["color"] for s in styles}) == 4
    assert kind_style("Physical", "Nope") is None


def test_card_shows_the_kind_tag():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        app = fpe.ProjectTrackerApp.__new__(fpe.ProjectTrackerApp)
        app.grid_inner = tk.Frame(root)
        app.grid_cards = []
        project = {"project_type": "Physical", "project_name": "Uiltje", "client_name": "Saam",
                   "date_created": "2018-02-15", "status": "archived", "metadata": {},
                   "path": r"D:\Archive\Physical\Project\2018-02-15_CNC_Saam_Uiltje"}
        card = app._create_project_card(project, 0, 0)
        texts = [w.cget("text") for w in card.winfo_children() if isinstance(w, tk.Label)]
        assert "CNC" in texts
    finally:
        root.destroy()
