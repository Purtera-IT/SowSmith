from __future__ import annotations

import re

from app.domain import get_active_domain_pack
from app.core.normalizers import normalize_text
from app.core.schemas import AtomType, EvidenceAtom, EvidenceEdge, EvidencePacket, PacketFamily, PacketRisk, PacketStatus

_BASE_RISK: dict[PacketFamily, float] = {
    PacketFamily.quantity_conflict: 0.75,
    PacketFamily.vendor_mismatch: 0.80,
    PacketFamily.scope_exclusion: 0.85,
    PacketFamily.site_access: 0.65,
    PacketFamily.missing_info: 0.60,
    PacketFamily.meeting_decision: 0.55,
    PacketFamily.action_item: 0.40,
    PacketFamily.scope_inclusion: 0.20,
    PacketFamily.customer_override: 0.70,
    PacketFamily.quantity_claim: 0.35,
}

_OPS_IMPACT: dict[PacketFamily, list[str]] = {
    PacketFamily.quantity_conflict: ["commercial_quote", "procurement_alignment"],
    PacketFamily.vendor_mismatch: ["commercial_quote", "procurement_alignment", "schedule_risk"],
    PacketFamily.scope_exclusion: ["scope_baseline", "change_order_risk"],
    PacketFamily.site_access: ["dispatch_readiness", "onsite_execution"],
    PacketFamily.missing_info: ["decision_latency", "schedule_risk"],
    PacketFamily.meeting_decision: ["scope_alignment"],
    PacketFamily.action_item: ["owner_followup"],
    PacketFamily.scope_inclusion: ["baseline_tracking"],
    PacketFamily.customer_override: ["scope_baseline", "commercial_alignment"],
    PacketFamily.quantity_claim: ["baseline_tracking"],
}


def _unit_exposure_from_atoms(atoms: list[EvidenceAtom]) -> float:
    parts: list[str] = []
    for atom in atoms:
        parts.extend(
            [
                atom.raw_text,
                str(atom.value.get("item", "")),
                str(atom.value.get("description", "")),
                " ".join(atom.entity_keys),
            ]
        )
    text_blob = normalize_text(" ".join(parts))
    pack = get_active_domain_pack()
    pack_defaults = pack.risk_defaults
    if "ip camera" in text_blob or "camera" in text_blob:
        return float(pack_defaults.get("ip_camera_unit_exposure", 300.0))
    if "access point" in text_blob or " ap" in text_blob or "ap:" in text_blob:
        return float(pack_defaults.get("access_point_unit_exposure", 250.0))
    if "switch" in text_blob:
        return float(pack_defaults.get("switch_unit_exposure", 500.0))
    if "ip camera" in text_blob or "camera" in text_blob:
        return 300.0
    if "access point" in text_blob or " ap" in text_blob or "ap:" in text_blob:
        return 250.0
    if "switch" in text_blob:
        return 500.0
    return 200.0


def _estimate_cost(packet: EvidencePacket, atoms: list[EvidenceAtom]) -> float | None:
    pack_defaults = get_active_domain_pack().risk_defaults
    if packet.family in {PacketFamily.quantity_conflict, PacketFamily.vendor_mismatch}:
        reason_numbers = [float(token) for token in re.findall(r"\d+(?:\.\d+)?", packet.reason)]
        if len(reason_numbers) >= 2:
            diff = abs(reason_numbers[0] - reason_numbers[1])
            return round(diff * _unit_exposure_from_atoms(atoms), 2)
        quantities = [
            float(atom.value.get("quantity"))
            for atom in atoms
            if atom.atom_type == AtomType.quantity and isinstance(atom.value.get("quantity"), (int, float))
        ]
        if len(quantities) >= 2:
            diff = abs(quantities[0] - quantities[1])
            return round(diff * _unit_exposure_from_atoms(atoms), 2)
        return None
    if packet.family == PacketFamily.site_access:
        return float(pack_defaults.get("failed_dispatch_exposure", 400.0))
    if packet.family == PacketFamily.scope_exclusion:
        quantities = [
            float(atom.value.get("quantity"))
            for atom in atoms
            if atom.atom_type == AtomType.quantity and isinstance(atom.value.get("quantity"), (int, float))
        ]
        if quantities:
            return round(quantities[0] * _unit_exposure_from_atoms(atoms), 2)
        return float(pack_defaults.get("unpriced_scope_default", 5000.0))
    return None


def _severity(score: float) -> str:
    if score >= 0.90:
        return "critical"
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def _priority(severity: str, packet: EvidencePacket) -> int:
    if packet.review_flags and "roster_vendor_aggregate_mismatch" in packet.review_flags:
        if severity == "low":
            return 3
        if severity == "medium":
            return 2
    if packet.family == PacketFamily.scope_inclusion and packet.status == PacketStatus.active:
        return 5
    if severity == "critical":
        return 1
    if severity == "high":
        return 2
    if severity == "medium":
        return 3
    return 4


def score_packet_risk(packet: EvidencePacket, atoms: list[EvidenceAtom], edges: list[EvidenceEdge]) -> PacketRisk:
    del edges
    score = _BASE_RISK.get(packet.family, 0.50)
    reasons: list[str] = [f"base:{packet.family.value}={score:.2f}"]

    if packet.status == PacketStatus.needs_review:
        score += 0.10
        reasons.append("status:needs_review")
    if "contradiction_present" in packet.review_flags:
        score += 0.10
        reasons.append("flag:contradiction_present")
    if "customer_current_override" in packet.review_flags:
        score += 0.10
        reasons.append("flag:customer_current_override")
    if "exclusion_present" in packet.review_flags:
        score += 0.10
        reasons.append("flag:exclusion_present")
    if "vendor_scope_quantity_mismatch" in packet.review_flags:
        score += 0.15
        reasons.append("flag:vendor_scope_quantity_mismatch")
    if "roster_vendor_aggregate_mismatch" in packet.review_flags:
        score += 0.08
        reasons.append("flag:roster_vendor_aggregate_mismatch")
    if "low_confidence_atom" in packet.review_flags:
        score += 0.05
        reasons.append("flag:low_confidence_atom")

    if packet.certificate is not None:
        if packet.certificate.ambiguity_score > 0.5:
            score += 0.10
            reasons.append("certificate:ambiguity_gt_0.5")
        no_contradiction = len(packet.contradicting_atom_ids) == 0
        if packet.certificate.evidence_completeness_score > 0.9 and no_contradiction:
            score -= 0.10
            reasons.append("certificate:high_completeness_discount")

    score = max(0.0, min(1.0, round(score, 4)))
    severity = _severity(score)
    return PacketRisk(
        risk_score=score,
        severity=severity,  # type: ignore[arg-type]
        risk_reasons=sorted(reasons),
        estimated_cost_exposure=_estimate_cost(packet, atoms),
        operational_impact=_OPS_IMPACT.get(packet.family, ["general_review"]),
        review_priority=_priority(severity, packet),
    )
