from __future__ import annotations

from pathlib import Path

from app.core.schemas import AtomType, AuthorityClass
from app.parsers.quote_parser import QuoteParser
from scripts.make_demo_fixtures import create_demo_project


def test_quote_parser_vendor_quote_atoms(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    project_dir = create_demo_project(root)
    quote_path = project_dir / "vendor_quote.xlsx"

    atoms = QuoteParser().parse_artifact(
        project_id="proj_1",
        artifact_id="art_quote_1",
        path=quote_path,
    )

    assert atoms
    assert all(atom.source_refs for atom in atoms)
    assert all(atom.authority_class == AuthorityClass.vendor_quote for atom in atoms)

    line_items = [a for a in atoms if a.atom_type == AtomType.vendor_line_item]
    assert line_items

    quantity_atoms = [a for a in atoms if a.atom_type == AtomType.quantity]
    assert any(a.value.get("quantity") == 72 for a in quantity_atoms)

    all_keys = {key for atom in atoms for key in atom.entity_keys}
    assert "device:ip_camera" in all_keys

    constraints = [a for a in atoms if a.atom_type == AtomType.constraint]
    assert any(a.value.get("lead_time") == "2 weeks" for a in constraints)

    locator = atoms[0].source_refs[0].locator
    assert "row" in locator
    assert "columns" in locator
