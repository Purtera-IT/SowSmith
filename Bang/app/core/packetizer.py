from __future__ import annotations

import re
from collections import defaultdict

from app.core.authority import compare_atoms, choose_governing_atoms, is_scope_impacting_meeting_atom
from app.core.anchors import make_anchor_signature
from app.core.ids import stable_id
from app.core.normalizers import normalize_text
from app.core.packet_certificates import build_packet_certificate
from app.core.risk import score_packet_risk
from app.core.schemas import (
    AtomType,
    AuthorityClass,
    EntityRecord,
    EvidenceAtom,
    EvidenceEdge,
    EvidencePacket,
    PacketFamily,
    PacketStatus,
)

ACCESS_TEXT_RE = re.compile(r"(access|escort|badge|after\s*hours|weekdays|\d{1,2}(?::\d{2})?\s*(am|pm)\s*-\s*\d{1,2}(?::\d{2})?\s*(am|pm))", re.I)


def _anchor_for_atoms(atoms: list[EvidenceAtom]) -> tuple[str, str]:
    all_keys = [key for atom in atoms for key in atom.entity_keys]
    site_keys = sorted(k for k in set(all_keys) if k.startswith("site:"))
    device_keys = sorted(k for k in set(all_keys) if k.startswith("device:"))
    if site_keys:
        return "site", site_keys[0]
    if device_keys:
        return "device", device_keys[0]
    return "entity", "unknown"


def _packet_confidence(governing_atoms: list[EvidenceAtom], has_contradiction: bool) -> float:
    if not governing_atoms:
        return 0.0
    value = max(atom.confidence for atom in governing_atoms)
    if has_contradiction:
        value -= 0.15
    return max(0.0, min(1.0, value))


def _select_governing_atoms(
    atoms: list[EvidenceAtom],
    *,
    family: PacketFamily | None = None,
    prefer_customer_exclusion: bool = False,
) -> list[EvidenceAtom]:
    candidates = [a for a in atoms if a.authority_class != AuthorityClass.deleted_text]
    if not candidates:
        return []
    if prefer_customer_exclusion:
        customer_exclusions = [
            a
            for a in candidates
            if a.atom_type == AtomType.exclusion and a.authority_class == AuthorityClass.customer_current_authored
        ]
        if customer_exclusions:
            return sorted(customer_exclusions, key=lambda a: a.id)[:1]
    if any(a.authority_class == AuthorityClass.customer_current_authored for a in candidates):
        candidates = [
            a
            for a in candidates
            if a.authority_class != AuthorityClass.quoted_old_email
        ]
    context = {"packet_family": family.value} if family is not None else None
    winners = choose_governing_atoms(candidates, context=context)
    if not winners:
        return []
    best = winners[0]
    for atom in winners[1:]:
        decision = compare_atoms(best, atom)
        best = best if decision.governing_atom_id == best.id else atom
    return [best]


def _build_packet(
    project_id: str,
    family: PacketFamily,
    atoms: list[EvidenceAtom],
    related_edges: list[EvidenceEdge],
    status: PacketStatus,
    reason: str,
    contradicting_atom_ids: list[str] | None = None,
    review_flags: list[str] | None = None,
    prefer_customer_exclusion: bool = False,
    owner: str | None = None,
) -> EvidencePacket:
    governing_atoms = _select_governing_atoms(
        atoms,
        family=family,
        prefer_customer_exclusion=prefer_customer_exclusion,
    )
    governing_ids = [a.id for a in governing_atoms]
    support_ids = sorted({a.id for a in atoms if a.id not in set(contradicting_atom_ids or [])})
    contradicting_ids = sorted(set(contradicting_atom_ids or []))
    edge_ids = sorted({e.id for e in related_edges})
    anchor_signature = make_anchor_signature(family, atoms, owner=owner)
    anchor_type = anchor_signature.anchor_type
    anchor_key = anchor_signature.canonical_key

    flags = set(review_flags or [])
    if contradicting_ids:
        flags.add("contradiction_present")
    if any(a.confidence < 0.75 for a in atoms):
        flags.add("low_confidence_atom")
    if any(a.authority_class == AuthorityClass.deleted_text for a in atoms):
        flags.add("deleted_text_present")
    if any("semantic_candidate_linker" in edge.reason.lower() for edge in related_edges):
        flags.add("semantic_candidate_linker")

    effective_status = status
    if not governing_ids and status in {PacketStatus.active, PacketStatus.needs_review}:
        effective_status = PacketStatus.rejected
    elif governing_atoms and any(atom.review_status.value == "needs_review" for atom in governing_atoms):
        if effective_status == PacketStatus.active:
            effective_status = PacketStatus.needs_review

    packet = EvidencePacket(
        id=stable_id("pkt", project_id, family.value, anchor_signature.hash),
        project_id=project_id,
        family=family,
        anchor_type=anchor_type,
        anchor_key=anchor_key,
        anchor_signature=anchor_signature,
        governing_atom_ids=governing_ids,
        supporting_atom_ids=support_ids,
        contradicting_atom_ids=contradicting_ids,
        related_edge_ids=edge_ids,
        confidence=_packet_confidence(governing_atoms, bool(contradicting_ids)),
        status=effective_status,
        reason=reason,
        review_flags=sorted(flags),
    )
    return packet


def _is_risky_action_item(atom: EvidenceAtom) -> bool:
    text = normalize_text(atom.raw_text)
    return any(token in text for token in ("scope", "add", "remove", "price", "cost", "commercial", "change"))


def build_packets(
    project_id: str,
    atoms: list[EvidenceAtom],
    entities: list[EntityRecord],
    edges: list[EvidenceEdge],
    attach_metadata: bool = True,
) -> list[EvidencePacket]:
    del entities  # reserved for future packet anchoring refinements
    atom_by_id = {a.id: a for a in atoms}
    packets: list[EvidencePacket] = []
    consumed_by_conflict_or_exclusion: set[str] = set()

    # 1) quantity_conflict
    for edge in edges:
        if edge.edge_type.value != "contradicts":
            continue
        a = atom_by_id.get(edge.from_atom_id)
        b = atom_by_id.get(edge.to_atom_id)
        if not a or not b or a.atom_type != AtomType.quantity or b.atom_type != AtomType.quantity:
            continue
        qty_a = a.value.get("quantity")
        qty_b = b.value.get("quantity")
        reason = edge.reason if edge.reason else f"Quantity conflict between {qty_a} and {qty_b}."
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.quantity_conflict,
            atoms=[a, b],
            related_edges=[edge],
            status=PacketStatus.needs_review,
            reason=reason,
            contradicting_atom_ids=[a.id, b.id],
        )
        packets.append(packet)
        consumed_by_conflict_or_exclusion.update([a.id, b.id])

    # 2) vendor_mismatch
    for edge in edges:
        if edge.edge_type.value != "contradicts":
            continue
        a = atom_by_id.get(edge.from_atom_id)
        b = atom_by_id.get(edge.to_atom_id)
        if not a or not b:
            continue
        authorities = {a.authority_class, b.authority_class}
        if (
            a.atom_type == AtomType.quantity
            and b.atom_type == AtomType.quantity
            and authorities == {AuthorityClass.approved_site_roster, AuthorityClass.vendor_quote}
        ):
            packet = _build_packet(
                project_id=project_id,
                family=PacketFamily.vendor_mismatch,
                atoms=[a, b],
                related_edges=[edge],
                status=PacketStatus.needs_review,
                reason=edge.reason if edge.reason else "Vendor quote quantity does not match scoped quantity.",
                contradicting_atom_ids=[a.id, b.id],
                review_flags=["vendor_scope_quantity_mismatch"],
            )
            packets.append(packet)
            consumed_by_conflict_or_exclusion.update([a.id, b.id])

    # 3) scope_exclusion
    exclusion_atoms = [a for a in atoms if a.atom_type == AtomType.exclusion]
    excludes_edges = [e for e in edges if e.edge_type.value == "excludes"]
    grouped_exclusions: dict[str, list[EvidenceAtom]] = defaultdict(list)
    for atom in exclusion_atoms:
        site_keys = sorted(k for k in atom.entity_keys if k.startswith("site:"))
        if site_keys:
            for site_key in site_keys:
                grouped_exclusions[site_key].append(atom)
        else:
            _, anchor_key = _anchor_for_atoms([atom])
            grouped_exclusions[anchor_key].append(atom)
    for anchor_key, ex_atoms in grouped_exclusions.items():
        related = [
            e
            for e in excludes_edges
            if any(
                atom_by_id.get(aid) and anchor_key in atom_by_id[aid].entity_keys
                for aid in (e.from_atom_id, e.to_atom_id)
            )
        ]
        conflict_targets = [
            atom_by_id[e.to_atom_id]
            for e in related
            if atom_by_id.get(e.to_atom_id) is not None
            and atom_by_id[e.to_atom_id].atom_type in {AtomType.scope_item, AtomType.quantity}
        ]
        all_atoms = ex_atoms + conflict_targets
        has_transcript_exclusion = any(
            atom.authority_class == AuthorityClass.meeting_note for atom in ex_atoms
        )
        status = (
            PacketStatus.needs_review
            if conflict_targets or has_transcript_exclusion
            else PacketStatus.active
        )
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.scope_exclusion,
            atoms=all_atoms,
            related_edges=related,
            status=status,
            reason="Exclusion directive identified for scoped work.",
            contradicting_atom_ids=[a.id for a in conflict_targets],
            review_flags=["exclusion_present"],
            prefer_customer_exclusion=True,
        )
        packets.append(packet)
        consumed_by_conflict_or_exclusion.update(a.id for a in all_atoms)

    # 4) site_access
    access_constraints = [
        a
        for a in atoms
        if a.atom_type == AtomType.constraint and ACCESS_TEXT_RE.search(a.raw_text)
    ]
    for atom in access_constraints:
        status = PacketStatus.needs_review if atom.confidence < 0.75 else PacketStatus.active
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.site_access,
            atoms=[atom],
            related_edges=[e for e in edges if atom.id in {e.from_atom_id, e.to_atom_id}],
            status=status,
            reason="Site access constraint captured.",
        )
        packets.append(packet)

    # 5) meeting_decision
    meeting_decision_atoms = [a for a in atoms if a.atom_type in {AtomType.decision, AtomType.meeting_commitment}]
    for atom in meeting_decision_atoms:
        same_anchor_atoms = [
            other
            for other in atoms
            if other.id != atom.id
            and set(other.entity_keys).intersection(set(atom.entity_keys))
            and other.atom_type in {AtomType.scope_item, AtomType.exclusion, AtomType.quantity, AtomType.customer_instruction}
        ]
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.meeting_decision,
            atoms=[atom] + same_anchor_atoms,
            related_edges=[e for e in edges if atom.id in {e.from_atom_id, e.to_atom_id}],
            status=PacketStatus.needs_review if is_scope_impacting_meeting_atom(atom) else PacketStatus.active,
            reason="Meeting decision captured from transcript evidence.",
            review_flags=["verbal_commitment_requires_confirmation"],
        )
        packets.append(packet)

    # 6) action_item
    action_items = [a for a in atoms if a.atom_type == AtomType.action_item]
    for atom in action_items:
        owner = str(atom.value.get("owner", "")).strip().lower()
        risky = _is_risky_action_item(atom)
        status = PacketStatus.active
        if not owner or owner == "unknown" or risky:
            status = PacketStatus.needs_review
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.action_item,
            atoms=[atom],
            related_edges=[e for e in edges if atom.id in {e.from_atom_id, e.to_atom_id}],
            status=status,
            reason="Action item extracted from transcript.",
            owner=owner or "unknown",
        )
        packets.append(packet)

    # 7) missing_info
    open_questions = [a for a in atoms if a.atom_type == AtomType.open_question]
    for atom in open_questions:
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.missing_info,
            atoms=[atom],
            related_edges=[e for e in edges if atom.id in {e.from_atom_id, e.to_atom_id}],
            status=PacketStatus.needs_review,
            reason="Open question from transcript requires clarification.",
        )
        packets.append(packet)

    # 8) customer_override
    customer_instructions = [
        a
        for a in atoms
        if a.atom_type == AtomType.customer_instruction and a.authority_class == AuthorityClass.customer_current_authored
    ]
    for atom in customer_instructions:
        conflicts = [
            other
            for other in atoms
            if other.id != atom.id
            and set(other.entity_keys).intersection(set(atom.entity_keys))
            and other.atom_type in {AtomType.scope_item, AtomType.exclusion, AtomType.quantity}
        ]
        status = PacketStatus.needs_review if conflicts else PacketStatus.active
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.customer_override,
            atoms=[atom] + conflicts,
            related_edges=[e for e in edges if atom.id in {e.from_atom_id, e.to_atom_id}],
            status=status,
            reason="Customer current instruction overrides prior context.",
            contradicting_atom_ids=[c.id for c in conflicts],
            review_flags=["customer_current_override"],
        )
        packets.append(packet)

    # 9) scope_inclusion
    inclusion_candidates = [
        a
        for a in atoms
        if a.atom_type in {AtomType.scope_item, AtomType.quantity}
        and a.id not in consumed_by_conflict_or_exclusion
    ]
    grouped_inclusions: dict[str, list[EvidenceAtom]] = defaultdict(list)
    for atom in inclusion_candidates:
        _, anchor_key = _anchor_for_atoms([atom])
        grouped_inclusions[anchor_key].append(atom)
    for anchor_key in sorted(grouped_inclusions):
        group_atoms = grouped_inclusions[anchor_key]
        related_edges = [e for e in edges if any(a.id in {e.from_atom_id, e.to_atom_id} for a in group_atoms)]
        packet = _build_packet(
            project_id=project_id,
            family=PacketFamily.scope_inclusion,
            atoms=group_atoms,
            related_edges=related_edges,
            status=PacketStatus.needs_review
            if any(is_scope_impacting_meeting_atom(atom) for atom in group_atoms)
            else PacketStatus.active,
            reason="Scoped inclusion evidence is consistent.",
        )
        packets.append(packet)

    # Deduplicate packets by family + canonical anchor signature.
    dedup: dict[tuple[str, str], EvidencePacket] = {}
    for packet in packets:
        signature_hash = packet.anchor_signature.hash if packet.anchor_signature is not None else packet.anchor_key
        key = (packet.family.value, signature_hash)
        if key not in dedup:
            dedup[key] = packet
        else:
            existing = dedup[key]
            if (
                packet.family == PacketFamily.quantity_conflict
                and "Aggregate scoped quantity" in packet.reason
                and "Aggregate scoped quantity" not in existing.reason
            ):
                dedup[key] = packet
            if (
                packet.family == PacketFamily.vendor_mismatch
                and "Aggregate scoped quantity" in packet.reason
                and "Aggregate scoped quantity" not in existing.reason
            ):
                dedup[key] = packet

    result = list(dedup.values())
    if attach_metadata:
        atom_by_id = {atom.id: atom for atom in atoms}
        for packet in result:
            packet.certificate = build_packet_certificate(packet, atom_by_id)
            packet_atoms = [
                atom_by_id[atom_id]
                for atom_id in (packet.supporting_atom_ids + packet.contradicting_atom_ids)
                if atom_id in atom_by_id
            ]
            packet.risk = score_packet_risk(packet, packet_atoms, edges)
    result = sorted(
        result,
        key=lambda p: (
            p.risk.review_priority if p.risk is not None else 5,
            -(p.risk.risk_score if p.risk is not None else 0.0),
            p.family.value,
            p.anchor_key,
            p.id,
        ),
    )
    return result
