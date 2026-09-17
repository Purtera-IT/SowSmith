"""A synthetic ``orbitbrief.input.v2`` envelope in the shape parser-os emits today.

Nothing here comes from a real deal. The shape is copied from parser-os
origin/main b25c027:

* per-atom projection  -- ``app/core/orbitbrief_envelope.py::_compact_atom``
  (``text`` not ``raw_text``; ``structured`` not ``value``; ``verified``,
  ``review_status``, ``review_flags``, ``calibrated_confidence``,
  ``confidence_raw``, optional ``decision_provenance``)
* enums            -- ``app/core/schemas.py`` AtomType / AuthorityClass / ReviewStatus
* cockpit surfaces -- ``app/core/orbitbrief_core.py`` build_pm_dashboard,
  build_scope_truth, build_sow_readiness_scorecard, build_srl_missing_checklist,
  build_change_order_timeline, build_site_readiness, build_stakeholder_load,
  build_project_vitals. The surfaces below are derived from the atoms by the
  same rules those builders use, including their current quirks, so that a
  defect the diff tool reports is one the real pipeline would produce.
* work-order task atoms -- ``app/core/work_order.py::mint_work_line_atoms``
  (``machine_extractor`` + ``needs_review``, ``structured.backfill_reason``).

Deliberately planted, each mirroring a behaviour of the current pipeline:

1. a ``rejected`` service_line atom (renderer does not filter review_status)
2. a minted work-order task atom (renderer cites it as sourced scope)
3. a generated-gap open question with ``atom_id: None`` (pm_dashboard emits these)
4. the same $12,500 restated in two documents (money_summary.total sums both)
5. a contested quantity whose ``governing_authority`` is ``max()`` over class
   *names* of the governing claims (build_scope_truth), so when a signed SOW
   and a quote agree on 48 the SOW names ``vendor_quote`` as governing
6. ``exclusion`` / ``scope_item`` / ``risk`` atoms of types the renderer reads
   only via surfaces or not at all
7. surfaces the renderer ignores: deal_header, deal_financials,
   bill_of_materials, deal_timeline, run_scope, truth_gate, coverage
"""
from __future__ import annotations

import copy
from typing import Any

PROJECT_ID = "synthetic_deal_0001"
COMPILE_ID = "cmp_synthetic_0001"

# app/core/authority.py rank table
AUTHORITY_RANK = {
    "contractual_scope": 100, "pm_confirmed": 95, "customer_current_authored": 90,
    "approved_site_roster": 80, "vendor_quote": 65, "meeting_note": 55,
    "machine_extractor": 40, "quoted_old_email": 10, "deleted_text": 0,
}


def _atom(
    aid: str,
    atom_type: str,
    text: str,
    *,
    artifact: str = "art_sow_pdf",
    authority: str = "contractual_scope",
    review: str = "auto_accepted",
    entity_keys: list[str] | None = None,
    structured: dict[str, Any] | None = None,
    section_path: list[str] | None = None,
    flags: list[str] | None = None,
    verified: str = "verified",
) -> dict[str, Any]:
    return {
        "id": aid,
        "artifact_id": artifact,
        "atom_type": atom_type,
        "authority_class": authority,
        "confidence": 0.91,
        "text": text,
        "section_path": section_path or ["Scope"],
        "locator": {"page": 1, "line": 1},
        "verified": verified,
        "entity_keys": entity_keys or [],
        "structured": structured or {},
        "calibrated_confidence": 0.88,
        "review_status": review,
        "review_flags": flags or (["accepted_verified_receipt"] if review == "auto_accepted" else []),
        "confidence_raw": 0.93,
    }


def _atoms() -> list[dict[str, Any]]:
    a = [
        _atom("atm_svc_01", "service_line", "Install and terminate structured cabling for new IDF closets."),
        _atom("atm_svc_rej", "service_line", "Relocate the customer data center to a new building.",
              review="rejected", flags=["contradicted_by_newer_document"]),
        _atom("atm_task_01", "task", "Mount and label 24-port patch panels in each IDF."),
        _atom("atm_deliv_01", "deliverable", "As-built drawings and test results delivered within 10 business days."),
        _atom("atm_scope_01", "scope_item", "Provide lift equipment for ceiling work above 12 feet."),
        _atom("atm_qty_sow", "quantity", "Install 48 wireless access points at Site A.",
              entity_keys=["device:access_point", "site:site_a"], structured={"quantity": 48}),
        _atom("atm_qty_quote", "vendor_line_item", "Access point, qty 42",
              artifact="art_quote_xlsx", authority="vendor_quote",
              entity_keys=["device:access_point", "site:site_a"], structured={"quantity": 42}),
        _atom("atm_qty_quote_r2", "vendor_line_item", "Access point (rev 2), qty 48",
              artifact="art_quote_xlsx", authority="vendor_quote",
              entity_keys=["device:access_point", "site:site_a"], structured={"quantity": 48}),
        _atom("atm_excl_01", "exclusion", "Electrical work and conduit are excluded.",
              section_path=["Exclusions"]),
        _atom("atm_money_sow", "vendor_line_item", "Fixed fee for installation: $12,500.00",
              entity_keys=["money:12500.0"], structured={"amount": 12500.0}),
        _atom("atm_money_email", "customer_instruction", "Confirming the $12,500.00 install fee from the SOW.",
              artifact="art_email_eml", authority="quoted_old_email",
              entity_keys=["money:12500.0"], review="needs_review", flags=["quoted_history"]),
        _atom("atm_access_01", "site_access_window", "Work permitted 7am-3pm weekdays; badge escort required."),
        _atom("atm_constraint_01", "constraint", "Response to P1 incidents within 4 hours.",
              structured={"sla": {"response_hours": 4}}),
        _atom("atm_accept_01", "acceptance_criterion", "Each cable run passes Cat6A certification testing."),
        _atom("atm_accept_qa", "acceptance_criterion", "Pending rows imported | PASS | 428",
              section_path=["QA_Checks"]),
        _atom("atm_risk_01", "risk", "Lift availability may delay ceiling work.",
              structured={"risk_id": "R1", "severity": "high", "owner": "", "mitigation": "Reserve lift early"}),
        _atom("atm_ms_01", "milestone_phase", "Phase 1 kickoff on 2026-10-05.",
              entity_keys=["milestone:2026-10-05"]),
        _atom("atm_co_rule", "change_order_rule",
              "Reschedule within 1 business day incurs 100% of planned cost."),
        _atom("atm_contact_01", "stakeholder", "Jordan Example, Facilities Manager",
              entity_keys=["stakeholder:jordan_example"]),
    ]
    # Work-order task atom, minted the way mint_work_line_atoms does it: a deep
    # copy of the supporting atom with a synthesized label and demoted trust.
    support = next(x for x in a if x["id"] == "atm_svc_01")
    task = copy.deepcopy(support)
    task.update({
        "id": "atm_wo_task_01",
        "atom_type": "task",
        "text": "Cable 6 IDF closets",
        "authority_class": "machine_extractor",
        "review_status": "needs_review",
        "review_flags": ["work_order", "task_tier_parent"],
    })
    task["structured"] = {
        "kind": "task", "text": "Cable 6 IDF closets", "task_tier": "parent", "is_quote_line": True,
        "backfilled_from_atom_id": "atm_svc_01", "backfill_reason": "work_order",
        "count": 6, "unit": "closet", "object": "IDF closet", "site_count": 1,
        "after_hours": False, "no_onsite_hands": False, "customer_supplies_equipment": False,
    }
    a.append(task)
    return a


def _scope_truth(atoms: list[dict[str, Any]]) -> dict[str, Any]:
    claims = []
    for x in atoms:
        if x["atom_type"] not in ("quantity", "scope_item", "vendor_line_item", "constraint", "site_roster", "bom_line"):
            continue
        q = x["structured"].get("quantity")
        if q is None:
            continue
        claims.append({
            "atom_id": x["id"], "artifact_id": x["artifact_id"], "quantity": q,
            "authority_class": x["authority_class"],
            "authority_rank": AUTHORITY_RANK[x["authority_class"]], "text": x["text"][:160],
        })
    by_value: dict[int, list[dict[str, Any]]] = {}
    for c in claims:
        by_value.setdefault(c["quantity"], []).append(c)
    ranked = sorted(by_value.items(), key=lambda kv: (-max(c["authority_rank"] for c in kv[1]), -len(kv[1]), kv[0]))
    gov_value, gov_claims = ranked[0]
    entry = {
        "device": "device:access_point", "site": "site:site_a",
        "canonical_quantity": gov_value, "governing_claims": gov_claims,
        # build_scope_truth: max() over the class NAME of the governing claims,
        # as shipped -- "vendor_quote" > "contractual_scope" alphabetically.
        "governing_authority": max(c["authority_class"] for c in gov_claims),
        "all_reported_values": sorted(by_value), "is_contested": len(by_value) > 1,
    }
    contested = [{
        "device": entry["device"], "site": entry["site"], "canonical_quantity": gov_value,
        "competing_values": [v for v in sorted(by_value) if v != gov_value],
        "audit": [{"quantity": q, "claims": cl} for q, cl in sorted(by_value.items())],
    }]
    return {"devices": [entry], "contested": contested, "device_count": 1, "site_count": 1, "contested_count": 1}


def _pm_dashboard(atoms: list[dict[str, Any]]) -> dict[str, Any]:
    money = [{"atom_id": x["id"], "amount": float(k.split(":", 1)[1]), "text": x["text"][:160]}
             for x in atoms for k in x["entity_keys"][:1] if k.startswith("money:")]
    return {
        "blockers": [{"kind": "generated_gap", "atom_id": None, "summary": "Who provides the lift?",
                      "field_id": "site.lift_equipment"}],
        "cross_doc_contradictions": [], "intra_doc_contradictions": [], "change_orders": [],
        "stakeholders": [{"slug": "jordan_example", "name": "Jordan Example", "role": "Facilities Manager",
                          "email": None, "phone": None}],
        "risks_by_owner": {},
        "risks_unowned": [{"atom_id": "atm_risk_01", "artifact_id": "art_sow_pdf", "risk_id": "R1",
                           "summary": "Lift availability may delay ceiling work.", "severity": "high",
                           "mitigation": "Reserve lift early"}],
        "action_items_by_owner": {}, "action_items_unowned": [],
        "open_questions": [{"atom_id": None, "artifact_id": None, "text": "Who provides the lift?",
                            "review_status": "needs_review", "kind": "generated_gap",
                            "field_id": "site.lift_equipment"}],
        "head_start": [],
        "milestones_timeline": [{"atom_id": "atm_ms_01", "iso": "2026-10-05", "text": "Phase 1 kickoff on 2026-10-05."}],
        "sla_summary": [{"atom_id": "atm_constraint_01", "sla": {"response_hours": 4},
                         "text": "Response to P1 incidents within 4 hours."}],
        "exclusions": [{"atom_id": "atm_excl_01", "artifact_id": "art_sow_pdf",
                        "text": "Electrical work and conduit are excluded."}],
        "money_summary": {"total": sum(m["amount"] for m in money), "atoms": money},
        "schematic_review": {},
    }


def build_synthetic_envelope(*, with_devices: bool = True, exclusions_surface: bool = True) -> dict[str, Any]:
    """Build the envelope.

    ``with_devices=False`` drops the quantity claims, which is the branch in
    which the renderer prints service/task/deliverable atoms as scope.
    ``exclusions_surface=False`` empties pm_dashboard.exclusions while the
    exclusion atom stays, as happens when the surface builder is skipped.
    """
    atoms = _atoms()
    if not with_devices:
        atoms = [x for x in atoms if x["id"] not in {"atm_qty_sow", "atm_qty_quote", "atm_qty_quote_r2"}]
    by_type: dict[str, list[str]] = {}
    for x in atoms:
        by_type.setdefault(x["atom_type"], []).append(x["id"])
    env: dict[str, Any] = {
        "schema_version": "orbitbrief.input.v2",
        "project_id": PROJECT_ID,
        "compile_id": COMPILE_ID,
        "generated_at": "2026-09-17T12:00:00Z",
        "summary": {"artifact_count": 3, "atom_count": len(atoms), "page_count": 9},
        "documents": [
            {"artifact_id": "art_sow_pdf", "filename": "synthetic_sow.pdf", "artifact_type": "pdf",
             "parser_version": "synthetic", "structured": {}, "atom_ids": [], "parse_outcome": "parsed"},
            {"artifact_id": "art_quote_xlsx", "filename": "synthetic_quote.xlsx", "artifact_type": "xlsx",
             "parser_version": "synthetic", "structured": {}, "atom_ids": [], "parse_outcome": "parsed"},
            {"artifact_id": "art_email_eml", "filename": "synthetic_thread.eml", "artifact_type": "email",
             "parser_version": "synthetic", "structured": {}, "atom_ids": [], "parse_outcome": "parsed"},
        ],
        "atoms": atoms,
        "packets": [], "entities": [], "edges": [],
        "indexes": {"atoms_by_atom_type": by_type},
        "coverage": {"unrecovered_regions": []},
        "deal_timeline": {"known": False, "quote_asof": None, "events": []},
        "pm_dashboard": _pm_dashboard(atoms),
        "sow_readiness_scorecard": {
            "readiness_score": 0.62, "grade": "C", "blocker_count": 1, "blocked": True,
            "grade_capped_by_blockers": True,
            "dimensions": {"scope": {"score": 0.8, "signals": {}}, "commercial": {"score": 0.5, "signals": {}}},
            "description_by_dimension": {},
        },
        "srl_missing_checklist": {
            "checklist_version": "srl_v2_expanded", "field_count": 4, "present_count": 3, "missing_count": 1,
            "coverage": 0.75, "by_category": {"site": {"present": 1, "total": 2, "coverage": 0.5}},
            "present": [], "missing": [{"field_id": "site.lift_equipment", "label": "Lift equipment owner",
                                        "category": "site"}],
        },
        "reconciliation": {},
        "scope_truth": _scope_truth(atoms) if with_devices else {
            "devices": [], "contested": [], "device_count": 0, "site_count": 0, "contested_count": 0},
        "change_order_timeline": {"entries": [], "entry_count": 0, "with_structured_delta": 0,
                                  "with_approval_signal": 0},
        "site_readiness": {
            "sites": [{"site": "site:site_a", "readiness": 0.7, "maturity": "anchored", "band": "yellow",
                       "anchored": True, "signal_count": 5, "device_keys": ["device:access_point"],
                       "device_count": 1, "stakeholder_keys": [], "stakeholder_count": 0,
                       "constraint_count": 1, "scope_atom_count": 3, "money_present": True,
                       "milestone_present": True, "contradiction_count": 1, "aliases": []}],
            "site_count": 1, "anchored_count": 1, "maturity_breakdown": {"anchored": 1},
            "avg_readiness": 0.7, "least_ready_sites": [],
        },
        "stakeholder_load": {"stakeholders": [], "stakeholder_count": 0, "bottlenecks": [], "max_severity_load": 0},
        "truth_gate": {},
        "project_vitals": {
            "score_100": 69.0, "band": "orange", "capped_by_blockers": True,
            "top_drivers": ["scope_clarity"], "top_detractors": ["risk_ownership"],
            "components": [{"name": "scope_clarity", "weight": 0.3, "raw_score": 0.9, "contribution": 0.27,
                            "signal": {}},
                           {"name": "risk_ownership", "weight": 0.1, "raw_score": 0.0, "contribution": 0.0,
                            "signal": {}}],
        },
        "run_scope": {"as_of": None, "applied": False, "documents_in_scope": 3},
        "deal_header": {"customer": "Synthetic Customer", "quote_type": "fixed_fee"},
        "deal_financials": {"headline": {"total": 12500.0}},
        "bill_of_materials": {"lines": []},
    }
    if not exclusions_surface:
        env["pm_dashboard"]["exclusions"] = []
    return env
