from __future__ import annotations

from openpyxl import Workbook

from app.core.schemas import AtomType
from app.parsers.xlsx_parser import XlsxParser


def test_xlsx_parser_emits_atoms_with_provenance(tmp_path) -> None:
    file_path = tmp_path / "sample.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "site_roster"
    ws.append(["Site", "Floor", "Device", "Quantity", "Access Window", "Scope"])
    ws.append(["Main Campus", "1", "IP Camera", "50", "Weekdays 8am-5pm", "Install"])
    ws.append(["West Wing", "2", "IP Camera", "41", "Escort required", "Install"])
    ws.append(["TOTAL", "", "", "91", "", ""])
    wb.save(file_path)

    atoms = XlsxParser().parse_artifact(
        project_id="proj_1",
        artifact_id="art_1",
        path=file_path,
    )
    assert atoms
    assert all(atom.source_refs for atom in atoms)

    quantity_atoms = [atom for atom in atoms if atom.atom_type == AtomType.quantity]
    quantities = [atom.value.get("quantity") for atom in quantity_atoms]
    assert 50 in quantities
    assert 41 in quantities
    assert 91 not in quantities

    all_keys = {key for atom in atoms for key in atom.entity_keys}
    assert "site:west_wing" in all_keys
    assert "device:ip_camera" in all_keys

    constraint_atoms = [atom for atom in atoms if atom.atom_type == AtomType.constraint]
    assert constraint_atoms
    assert any("escort required" in atom.normalized_text for atom in constraint_atoms)

    first_ref = atoms[0].source_refs[0]
    assert "sheet" in first_ref.locator
    assert "row" in first_ref.locator
    assert first_ref.locator["sheet"] == "site_roster"
