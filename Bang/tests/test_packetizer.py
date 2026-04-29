from __future__ import annotations

from app.core.packetizer import build_packets
from app.core.schemas import (
    ArtifactType,
    AtomType,
    AuthorityClass,
    EdgeType,
    PacketFamily,
    PacketStatus,
    ReviewStatus,
    EvidenceAtom,
    EvidenceEdge,
    SourceRef,
)


def _atom(
    atom_id: str,
    *,
    atom_type: AtomType,
    authority: AuthorityClass,
    entity_keys: list[str],
    text: str,
    confidence: float = 0.9,
    quantity: float | None = None,
) -> EvidenceAtom:
    value = {"text": text}
    if quantity is not None:
        value["quantity"] = quantity
    locator = {"quoted": authority == AuthorityClass.quoted_old_email}
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
                id=f"src_{atom_id}",
                artifact_id="art_1",
                artifact_type=ArtifactType.txt,
                filename="fixture.txt",
                locator=locator,
                extraction_method="test",
                parser_version="test",
            )
        ],
        authority_class=authority,
        confidence=confidence,
        review_status=ReviewStatus.auto_accepted,
        review_flags=[],
        parser_version="test",
    )


def _edge(edge_id: str, edge_type: EdgeType, from_id: str, to_id: str, reason: str) -> EvidenceEdge:
    return EvidenceEdge(
        id=edge_id,
        project_id="proj_1",
        from_atom_id=from_id,
        to_atom_id=to_id,
        edge_type=edge_type,
        reason=reason,
        confidence=0.9,
    )


def test_packetizer_v0_conflicts_and_governing_rules() -> None:
    scope_west = _atom(
        "scope_west",
        atom_type=AtomType.scope_item,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Install cameras at west wing",
    )
    exclusion_customer = _atom(
        "excl_customer",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.customer_current_authored,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Please remove west wing from scope",
    )
    exclusion_quoted = _atom(
        "excl_quoted",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.quoted_old_email,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Include west wing",
    )
    qty_approved = _atom(
        "qty_approved",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["device:ip_camera", "site:main_campus"],
        text="Scoped qty 91",
        quantity=91,
    )
    qty_vendor = _atom(
        "qty_vendor",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.vendor_quote,
        entity_keys=["device:ip_camera", "part:cam_ip_001"],
        text="Vendor qty 72",
        quantity=72,
    )
    access = _atom(
        "access_1",
        atom_type=AtomType.constraint,
        authority=AuthorityClass.customer_current_authored,
        entity_keys=["site:main_campus"],
        text="Escort access required after 5pm",
    )
    deleted = _atom(
        "deleted_1",
        atom_type=AtomType.scope_item,
        authority=AuthorityClass.deleted_text,
        entity_keys=["site:west_wing"],
        text="Install AV displays",
    )

    edges = [
        _edge(
            "e_contra_qty",
            EdgeType.contradicts,
            "qty_approved",
            "qty_vendor",
            "Aggregate scoped quantity 91 does not match vendor quantity 72 for device:ip_camera",
        ),
        _edge("e_excludes", EdgeType.excludes, "excl_customer", "scope_west", "Exclusion applies"),
    ]

    packets = build_packets(
        project_id="proj_1",
        atoms=[scope_west, exclusion_customer, exclusion_quoted, qty_approved, qty_vendor, access, deleted],
        entities=[],
        edges=edges,
    )
    families = {p.family for p in packets}
    assert PacketFamily.quantity_conflict in families
    assert PacketFamily.vendor_mismatch in families
    assert PacketFamily.scope_exclusion in families
    assert PacketFamily.site_access in families

    scope_exclusion_packet = next(p for p in packets if p.family == PacketFamily.scope_exclusion)
    assert scope_exclusion_packet.governing_atom_ids == ["excl_customer"]
    assert "exclusion_present" in scope_exclusion_packet.review_flags
    assert "excl_quoted" in (scope_exclusion_packet.supporting_atom_ids + scope_exclusion_packet.contradicting_atom_ids)

    assert "deleted_1" not in scope_exclusion_packet.governing_atom_ids
    assert all(len(p.supporting_atom_ids) + len(p.contradicting_atom_ids) > 0 for p in packets)
    assert all(
        not (p.status in {PacketStatus.active, PacketStatus.needs_review} and not p.governing_atom_ids)
        for p in packets
    )
    assert all(p.certificate is not None for p in packets)
    assert all(p.risk is not None for p in packets)
    assert all(p.anchor_signature is not None for p in packets)
    qty_packet = next(p for p in packets if p.family == PacketFamily.quantity_conflict)
    assert qty_packet.certificate is not None
    assert "91" in qty_packet.certificate.existence_reason and "72" in qty_packet.certificate.existence_reason
    assert qty_packet.certificate.authority_path
    assert "dimensions" in qty_packet.certificate.authority_path[0]
    assert 0.0 <= qty_packet.risk.risk_score <= 1.0


def test_transcript_packets_and_governance_rules() -> None:
    email_exclusion = _atom(
        "email_excl",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.customer_current_authored,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="Please remove west wing from scope.",
    )
    transcript_exclusion = _atom(
        "tx_excl",
        atom_type=AtomType.exclusion,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:west_wing", "device:ip_camera"],
        text="West wing removed from scope for now.",
    )
    transcript_access = _atom(
        "tx_access",
        atom_type=AtomType.constraint,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus"],
        text="Main Campus requires escort access after 5pm.",
    )
    transcript_open_q = _atom(
        "tx_q",
        atom_type=AtomType.open_question,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus"],
        text="Confirm whether MDF room requires badge access?",
    )
    transcript_action = _atom(
        "tx_action",
        atom_type=AtomType.action_item,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus"],
        text="Customer to provide lift access.",
    )
    transcript_decision = _atom(
        "tx_decision",
        atom_type=AtomType.decision,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:west_wing"],
        text="Decision: West Wing removed from scope.",
    )
    transcript_qty = _atom(
        "tx_qty",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.meeting_note,
        entity_keys=["site:main_campus", "device:ip_camera"],
        text="We may add 5 more IP cameras at Main Campus.",
        quantity=5,
    )
    roster_qty = _atom(
        "roster_qty",
        atom_type=AtomType.quantity,
        authority=AuthorityClass.approved_site_roster,
        entity_keys=["site:main_campus", "device:ip_camera"],
        text="Main Campus quantity 50",
        quantity=50,
    )
    edges = [
        _edge("e_ex", EdgeType.excludes, "tx_excl", "email_excl", "Transcript exclusion supports removal"),
        _edge("e_con", EdgeType.contradicts, "tx_qty", "roster_qty", "Quantity mismatch 5 vs 50"),
    ]

    packets = build_packets(
        project_id="proj_1",
        atoms=[
            email_exclusion,
            transcript_exclusion,
            transcript_access,
            transcript_open_q,
            transcript_action,
            transcript_decision,
            transcript_qty,
            roster_qty,
        ],
        entities=[],
        edges=edges,
    )

    families = {p.family for p in packets}
    assert PacketFamily.scope_exclusion in families
    assert PacketFamily.site_access in families
    assert PacketFamily.missing_info in families
    assert PacketFamily.action_item in families
    assert PacketFamily.meeting_decision in families or PacketFamily.quantity_conflict in families

    scope_packet = next(p for p in packets if p.family == PacketFamily.scope_exclusion)
    assert scope_packet.governing_atom_ids
    assert scope_packet.governing_atom_ids[0] == "email_excl"
    assert "tx_excl" in (scope_packet.supporting_atom_ids + scope_packet.contradicting_atom_ids)

    missing_packet = next(p for p in packets if p.family == PacketFamily.missing_info)
    assert missing_packet.status == PacketStatus.needs_review

    decision_packets = [p for p in packets if p.family == PacketFamily.meeting_decision]
    assert all(p.status == PacketStatus.needs_review for p in decision_packets)

    assert all(
        not (
            p.family in {PacketFamily.scope_exclusion, PacketFamily.scope_inclusion, PacketFamily.meeting_decision}
            and any(aid.startswith("tx_") for aid in p.governing_atom_ids)
            and p.status == PacketStatus.active
        )
        for p in packets
    )
    assert all(p.certificate is not None for p in packets)
    assert all(p.risk is not None for p in packets)
    assert all(p.anchor_signature is not None for p in packets)
    scope_packet_cert = scope_packet.certificate
    assert scope_packet_cert is not None
    assert "customer_current_authored" in scope_packet_cert.governing_rationale
    assert scope_packet_cert.authority_path
    assert "dimensions" in scope_packet_cert.authority_path[0]
