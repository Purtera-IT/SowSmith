"""Integration: material identity roster vs vendor quantity contradict edges on real COPPER pack (optional path)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.compiler import compile_project
from app.core.schemas import AuthorityClass


COPPER_ROOT = Path(
    os.environ.get(
        "COPPER_VALIDATION_ROOT",
        r"c:\Users\lilli\Downloads\purtera_copper_low_voltage_public_validation_packs"
        r"\purtera_copper_low_voltage_validation_packs\real_data_cases",
    )
)
CASE_DIR = COPPER_ROOT / "COPPER_001_SPRING_LAKE_AUDITORIUM" / "artifacts"


@pytest.mark.skipif(not (CASE_DIR / "extracted").is_dir(), reason="COPPER_001 artifacts not present")
def test_copper_drop_schedule_vs_vendor_material_contradiction_edges() -> None:
    project_dir = CASE_DIR
    result = compile_project(
        project_dir=project_dir,
        project_id="COPPER_001_SPRING_LAKE_AUDITORIUM",
        allow_errors=True,
        allow_unverified_receipts=True,
    )
    mat = [
        e
        for e in result.edges
        if e.edge_type.value == "contradicts"
        and (e.metadata or {}).get("comparison_basis") == "aggregate_roster_vs_summed_vendor_quote"
    ]
    assert len(mat) >= 3
    by = {a.id: a for a in result.atoms}
    seen_identities: set[str] = set()
    for e in mat:
        fa, ta = by.get(e.from_atom_id), by.get(e.to_atom_id)
        assert fa and ta
        assert fa.authority_class == AuthorityClass.approved_site_roster
        assert ta.authority_class == AuthorityClass.vendor_quote
        assert fa.atom_type.value == "quantity"
        assert ta.atom_type.value == "quantity"
        ni = (fa.value or {}).get("normalized_item")
        assert isinstance(ni, str)
        seen_identities.add(ni.lower())
    assert {"rj45", "cat6_utp", "cat6_stp"}.issubset(seen_identities)
