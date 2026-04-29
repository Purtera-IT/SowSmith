from __future__ import annotations

from app.domain import get_active_domain_pack
from app.core.ids import stable_id
from app.core.normalizers import normalize_text
from app.core.schemas import AuthorityClass, EdgeType, EntityRecord, EvidenceAtom, EvidenceEdge
from app.semantic.linker import propose_semantic_link_candidates


def _quantity_value(atom: EvidenceAtom) -> float | None:
    value = atom.value.get("quantity") if isinstance(atom.value, dict) else None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _shared_keys(a: EvidenceAtom, b: EvidenceAtom) -> set[str]:
    return set(a.entity_keys).intersection(set(b.entity_keys))


def _site_keys(atom: EvidenceAtom) -> set[str]:
    return {k for k in atom.entity_keys if k.startswith("site:")}


def _device_keys(atom: EvidenceAtom) -> set[str]:
    return {k for k in atom.entity_keys if k.startswith("device:")}


def _floor_room_keys(atom: EvidenceAtom) -> set[str]:
    return {k for k in atom.entity_keys if k.startswith("floor:") or k.startswith("room:") or k.startswith("device:")}


def _edge_id(project_id: str, edge_type: EdgeType, from_id: str, to_id: str, reason: str) -> str:
    return stable_id("edge", project_id, edge_type.value, from_id, to_id, reason)


def _build_edge(
    project_id: str,
    edge_type: EdgeType,
    from_atom: EvidenceAtom,
    to_atom: EvidenceAtom,
    reason: str,
    confidence: float,
) -> EvidenceEdge:
    return EvidenceEdge(
        id=_edge_id(project_id, edge_type, from_atom.id, to_atom.id, reason),
        project_id=project_id,
        from_atom_id=from_atom.id,
        to_atom_id=to_atom.id,
        edge_type=edge_type,
        reason=reason,
        confidence=confidence,
    )


def build_edges(project_id: str, atoms: list[EvidenceAtom], entities: list[EntityRecord]) -> list[EvidenceEdge]:
    pack = get_active_domain_pack()
    exclusion_patterns = [normalize_text(pattern) for pattern in pack.exclusion_patterns]
    constraint_patterns = [
        normalize_text(pattern)
        for patterns in pack.constraint_patterns.values()
        for pattern in patterns
    ]
    edges: list[EvidenceEdge] = []
    seen: set[str] = set()

    def push(edge: EvidenceEdge) -> None:
        if edge.id in seen:
            return
        seen.add(edge.id)
        edges.append(edge)

    ordered = sorted(atoms, key=lambda a: a.id)
    atom_by_id = {atom.id: atom for atom in ordered}

    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            a = ordered[i]
            b = ordered[j]
            shared = _shared_keys(a, b)
            if not shared:
                continue

            # supports: same entity keys + same atom_type + same normalized value/quantity.
            quantity_a = _quantity_value(a)
            quantity_b = _quantity_value(b)
            if a.atom_type == b.atom_type:
                same_value = normalize_text(str(a.value)) == normalize_text(str(b.value))
                same_quantity = quantity_a is not None and quantity_b is not None and quantity_a == quantity_b
                if same_value or same_quantity:
                    push(
                        _build_edge(
                            project_id,
                            EdgeType.supports,
                            a,
                            b,
                            "Atoms support each other with matching entity, type, and value",
                            0.88,
                        )
                    )
            if (
                a.atom_type.value == "constraint"
                and b.atom_type.value == "constraint"
                and _site_keys(a).intersection(_site_keys(b))
            ):
                push(
                    _build_edge(
                        project_id,
                        EdgeType.supports,
                        a,
                        b,
                        "Constraint atoms align on same site context",
                        0.84,
                    )
                )

            # contradicts: same entity key/device + quantity differs (except different site keys).
            if a.atom_type.value == "quantity" and b.atom_type.value == "quantity":
                if quantity_a is not None and quantity_b is not None and quantity_a != quantity_b:
                    sites_a = _site_keys(a)
                    sites_b = _site_keys(b)
                    if sites_a and sites_b and sites_a != sites_b:
                        continue
                    if _device_keys(a).intersection(_device_keys(b)) or shared:
                        push(
                            _build_edge(
                                project_id,
                                EdgeType.contradicts,
                                a,
                                b,
                                f"Quantity mismatch {quantity_a:g} vs {quantity_b:g} for shared entity context",
                                0.9,
                            )
                        )

    # excludes: exclusion atom mentions entity key in another atom.
    exclusions = [a for a in ordered if a.atom_type.value == "exclusion"]
    exclusions.extend(
        [
            atom
            for atom in ordered
            if atom.atom_type.value == "customer_instruction"
            and any(pattern in normalize_text(atom.raw_text) for pattern in exclusion_patterns)
        ]
    )
    exclusions = sorted({atom.id: atom for atom in exclusions}.values(), key=lambda atom: atom.id)
    for ex in exclusions:
        ex_keys = set(ex.entity_keys)
        for target in ordered:
            if target.id == ex.id:
                continue
            if ex_keys.intersection(set(target.entity_keys)):
                push(
                    _build_edge(
                        project_id,
                        EdgeType.excludes,
                        ex,
                        target,
                        "Exclusion atom applies to target entity context",
                        0.9,
                    )
                )

    # requires: constraint shares site with scope/quantity atoms.
    constraints = [a for a in ordered if a.atom_type.value == "constraint"]
    constraints.extend(
        [
            atom
            for atom in ordered
            if atom.atom_type.value == "customer_instruction"
            and any(pattern in normalize_text(atom.raw_text) for pattern in constraint_patterns)
        ]
    )
    constraints = sorted({atom.id: atom for atom in constraints}.values(), key=lambda atom: atom.id)
    for constraint in constraints:
        sites = _site_keys(constraint)
        if not sites:
            continue
        for target in ordered:
            if target.id == constraint.id or target.atom_type.value not in {"scope_item", "quantity"}:
                continue
            if sites.intersection(_site_keys(target)):
                push(
                    _build_edge(
                        project_id,
                        EdgeType.requires,
                        constraint,
                        target,
                        "Constraint requires adherence for same site context",
                        0.86,
                    )
                )

    # Aggregate device quantity contradiction: approved_site_roster vs vendor_quote.
    by_device_and_authority: dict[tuple[str, AuthorityClass], dict[str, object]] = {}
    for atom in ordered:
        if atom.atom_type.value != "quantity":
            continue
        qty = _quantity_value(atom)
        if qty is None:
            continue
        for device_key in _device_keys(atom):
            key = (device_key, atom.authority_class)
            bucket = by_device_and_authority.setdefault(key, {"total": 0.0, "atoms": []})
            bucket["total"] = float(bucket["total"]) + qty
            bucket["atoms"].append(atom)

    device_keys = sorted({k[0] for k in by_device_and_authority})
    for device_key in device_keys:
        approved = by_device_and_authority.get((device_key, AuthorityClass.approved_site_roster))
        vendor = by_device_and_authority.get((device_key, AuthorityClass.vendor_quote))
        if not approved or not vendor:
            continue
        approved_total = float(approved["total"])
        vendor_total = float(vendor["total"])
        if approved_total == vendor_total:
            continue
        from_atom = sorted(approved["atoms"], key=lambda a: a.id)[0]
        to_atom = sorted(vendor["atoms"], key=lambda a: a.id)[0]
        reason = (
            f"Aggregate scoped quantity {int(approved_total) if approved_total.is_integer() else approved_total:g} "
            f"does not match vendor quantity {int(vendor_total) if vendor_total.is_integer() else vendor_total:g} "
            f"for {device_key}"
        )
        push(_build_edge(project_id, EdgeType.contradicts, from_atom, to_atom, reason, 0.95))

    semantic_candidates = propose_semantic_link_candidates(ordered, domain_pack=pack)
    for candidate in semantic_candidates:
        if candidate.status != "accepted":
            continue
        from_atom = atom_by_id.get(candidate.from_atom_id)
        to_atom = atom_by_id.get(candidate.to_atom_id)
        if from_atom is None or to_atom is None:
            continue
        if candidate.proposed_edge_type == EdgeType.contradicts:
            continue
        reason = (
            "semantic_candidate_linker "
            f"method={candidate.method} score={candidate.similarity_score:.3f} "
            f"status={candidate.status}; {candidate.reason}"
        )
        push(
            _build_edge(
                project_id=project_id,
                edge_type=candidate.proposed_edge_type,
                from_atom=from_atom,
                to_atom=to_atom,
                reason=reason,
                confidence=min(0.99, max(0.5, candidate.similarity_score)),
            )
        )

    edges.sort(key=lambda e: e.id)
    return edges


def build_entity_edges(atoms: list[EvidenceAtom]):
    """Compatibility wrapper for older call sites."""
    return build_edges(project_id="unknown_project", atoms=atoms, entities=[])
