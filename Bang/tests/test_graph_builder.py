from __future__ import annotations

from app.core.graph_builder import build_edges
from app.core.ids import stable_id
from app.core.schemas import (
    ArtifactType,
    AtomType,
    AuthorityClass,
    EdgeType,
    EvidenceAtom,
    ReviewStatus,
    SourceRef,
)


def _atom(
    atom_id: str,
    *,
    atom_type: AtomType,
    authority: AuthorityClass,
    entity_keys: list[str],
    quantity: float | None = None,
    text: str = "text",
) -> EvidenceAtom:
    value = {"text": text}
    if quantity is not None:
        value["quantity"] = quantity
    locator = {}
    if authority == AuthorityClass.quoted_old_email:
        locator["quoted"] = True
    return EvidenceAtom(
        id=atom_id,
        project_id="proj_1",
        artifact_id="art_1",
        atom_type=atom_type,
        raw_text=text,
        normalized_text=text.lower(),
        value=value,
        entity_keys=entity_keys,
        source_refs=[
            SourceRef(
                id=stable_id("src", atom_id),
                artifact_id="art_1",
                artifact_type=ArtifactType.txt,
                filename="fixture.txt",
                locator=locator,
                extraction_method="test",
                parser_version="test",
            )
        ],
        authority_class=authority,
        confidence=0.9,
        review_status=ReviewStatus.auto_accepted,
        review_flags=[],
        parser_version="test",
    )


def test_site_specific_quantities_do_not_contradict() -> None:
    a1 = _atom(
        "q1",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:main_campus", "device:ip_camera"],
        quantity=50,
        text="Main Campus IP Camera quantity 50",
    )
    a2 = _atom(
        "q2",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        quantity=41,
        text="West Wing IP Camera quantity 41",
    )
    edges = build_edges("proj_1", [a1, a2], [])
    direct = [
        e
        for e in edges
        if e.edge_type == EdgeType.contradicts and {e.from_atom_id, e.to_atom_id} == {"q1", "q2"}
    ]
    assert not direct


def test_aggregate_scoped_quantity_contradicts_vendor_quantity() -> None:
    s1 = _atom(
        "s1",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:main_campus", "device:ip_camera"],
        quantity=50,
        text="Main Campus quantity 50",
    )
    s2 = _atom(
        "s2",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        quantity=41,
        text="West Wing quantity 41",
    )
    v1 = _atom(
        "v1",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.vendor_quote,
        entity_keys=["device:ip_camera", "part:cam_ip_001"],
        quantity=72,
        text="Vendor quantity 72",
    )
    edges = build_edges("proj_1", [s1, s2, v1], [])
    contradictions = [e for e in edges if e.edge_type == EdgeType.contradicts]
    assert any(
        e.reason == "Aggregate scoped quantity 91 does not match vendor quantity 72 for device:ip_camera"
        for e in contradictions
    )


def test_exclusion_creates_excludes_edge_and_constraint_requires() -> None:
    exclusion = _atom(
        "ex1",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.customer_current_authored,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Exclude west wing cameras",
    )
    scope = _atom(
        "sc1",
        atom_type=AtomType.scope_item,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Install west wing cameras",
    )
    constraint = _atom(
        "ct1",
        atom_type=AtomType.constraint,
        authority=AuthorityClass.customer_current_authored,
        entity_keys=["site:west_wing"],
        text="Escort required at west wing",
    )
    qty = _atom(
        "q1",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        quantity=41,
        text="West wing quantity 41",
    )

    edges = build_edges("proj_1", [exclusion, scope, constraint, qty], [])
    assert any(e.edge_type == EdgeType.excludes and e.from_atom_id == "ex1" and e.to_atom_id == "sc1" for e in edges)
    assert any(e.edge_type == EdgeType.requires and e.from_atom_id == "ct1" for e in edges)
    assert all(e.reason for e in edges)
    assert all(e.confidence >= 0.0 for e in edges)


def test_transcript_exclusion_creates_excludes_edge_against_scope() -> None:
    transcript_exclusion = _atom(
        "tx_ex1",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="West Wing removed from scope.",
    )
    roster_scope = _atom(
        "rs_scope",
        atom_type=AtomType.scope_item,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Install west wing cameras",
    )
    edges = build_edges("proj_1", [transcript_exclusion, roster_scope], [])
    assert any(e.edge_type == EdgeType.excludes and e.from_atom_id == "tx_ex1" for e in edges)


def test_transcript_constraint_creates_requires_edge() -> None:
    transcript_constraint = _atom(
        "tx_c1",
        atom_type=AtomType.constraint,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus"],
        text="Main Campus requires escort access after 5pm.",
    )
    scope_item = _atom(
        "sc_main",
        atom_type=AtomType.scope_item,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:main_campus", "device:ip_camera"],
        text="Install cameras main campus",
    )
    edges = build_edges("proj_1", [transcript_constraint, scope_item], [])
    assert any(e.edge_type == EdgeType.requires and e.from_atom_id == "tx_c1" for e in edges)


def test_transcript_quantity_can_conflict_with_existing_quantity() -> None:
    transcript_qty = _atom(
        "tx_q",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus", "device:ip_camera"],
        quantity=5,
        text="Add 5 more IP cameras at Main Campus",
    )
    roster_qty = _atom(
        "rs_q",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:main_campus", "device:ip_camera"],
        quantity=50,
        text="Main campus quantity 50",
    )
    edges = build_edges("proj_1", [transcript_qty, roster_qty], [])
    assert any(e.edge_type == EdgeType.contradicts for e in edges)


def test_transcript_open_question_does_not_create_false_scope_inclusion_edge() -> None:
    open_q = _atom(
        "tx_oq",
        atom_type=AtomType.open_question,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus"],
        text="Confirm whether MDF room requires badge access?",
    )
    edges = build_edges("proj_1", [open_q], [])
    assert not any(e.edge_type in {EdgeType.excludes, EdgeType.requires, EdgeType.supports} for e in edges)


def test_semantic_edges_include_metadata_and_no_contradictions() -> None:
    e1 = _atom(
        "e1",
        atom_type=AtomType.entity,
        authority=AuthorityClass.machine_extractor,
        entity_keys=["device:ip_camera"],
        text="IP Camera",
    )
    e2 = _atom(
        "e2",
        atom_type=AtomType.entity,
        authority=AuthorityClass.machine_extractor,
        entity_keys=["device:ip_camera"],
        text="security camera",
    )
    edges = build_edges("proj_1", [e1, e2], [])
    semantic_edges = [edge for edge in edges if "semantic_candidate_linker" in edge.reason.lower()]
    assert semantic_edges
    assert all("method=" in edge.reason.lower() for edge in semantic_edges)
    assert all(edge.edge_type != EdgeType.contradicts for edge in semantic_edges)
