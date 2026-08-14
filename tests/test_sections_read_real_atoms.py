"""Sections claimed "no data" while the envelope held the atoms.

Audited three deals 2026-08-13: five of sixteen sections were placeholders on
every one. The envelope for 01491cca carried constraint=19, change_order_rule=9,
acceptance_criterion=1.
"""
from __future__ import annotations

from sowsmith.render import build_sow_markdown


def _env(atoms, **extra):
    by_type: dict[str, list[str]] = {}
    for a in atoms:
        by_type.setdefault(a["atom_type"], []).append(a["id"])
    env = {
        "project_id": "P-1", "atoms": atoms,
        "indexes": {"atoms_by_atom_type": by_type},
    }
    env.update(extra)
    return env


CONSTRAINT = {
    "id": "c1", "atom_type": "constraint", "section_path": ["Requirements"],
    "text": "If surface-mounted raceway is installed on wall surfaces, it shall be "
            "secured with fasteners rated for the substrate.",
}
CO_RULE = {
    "id": "co1", "atom_type": "change_order_rule", "section_path": ["Commercial"],
    "text": "Cancellation or reschedule within 1 business day of the scheduled "
            "visit incurs 100% of planned site cost.",
}
ACCEPT = {
    "id": "ac1", "atom_type": "acceptance_criterion", "section_path": ["ACCEPTANCE"],
    "text": "Each site is accepted when all APs report to the controller.",
}
QA_ROW = {
    "id": "qa1", "atom_type": "acceptance_criterion", "section_path": ["QA_Checks"],
    "text": "Pending rows with valid HC imported | PASS | 428 | Expected 428 rows",
}
DECISION = {
    "id": "d1", "atom_type": "decision", "section_path": ["Notes"],
    "text": "Corporate and Technology business units centralized at HQ office in "
            "Maryville, TN, supporting all other business units.",
}


def test_constraints_render_the_atoms():
    """Read raw_text, which does not exist on envelope atoms — 19 became zero."""
    md = build_sow_markdown(_env([CONSTRAINT]))
    assert "surface-mounted raceway" in md
    assert "_No site-access or safety constraints captured._" not in md


def test_change_order_rules_render_when_the_timeline_is_empty():
    md = build_sow_markdown(_env([CO_RULE], change_order_timeline={"entries": []}))
    assert "100% of planned site cost" in md
    assert "change-order rule" in md.lower()


def test_acceptance_reads_acceptance_criterion_atoms():
    md = build_sow_markdown(_env([ACCEPT]))
    assert "report to the controller" in md


def test_internal_qa_never_becomes_contractual_acceptance():
    md = build_sow_markdown(_env([QA_ROW]))
    assert "valid HC imported" not in md
    assert "NEEDS DATA" in md


def test_decisions_are_not_acceptance_criteria():
    """These were the section's original source and are not criteria."""
    md = build_sow_markdown(_env([DECISION]))
    assert "centralized at HQ" not in md.split("## 12.")[-1].split("## 13.")[0]


def test_text_field_and_raw_text_both_work():
    legacy = dict(CONSTRAINT); legacy.pop("text"); legacy["raw_text"] = "Legacy field wins too."
    assert "Legacy field wins too." in build_sow_markdown(_env([legacy]))


def test_missing_index_still_finds_atoms_by_type():
    env = {"project_id": "P-1", "atoms": [ACCEPT], "indexes": {}}
    assert "report to the controller" in build_sow_markdown(env)
