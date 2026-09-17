"""Diff a rendered SOW against the envelope it was rendered from (PUR-61).

SowSmith is deterministic, so every difference between what the envelope says
and what the SOW says is a defect in exactly one place: the envelope (parser-os
/ the cockpit surfaces it precomputes) or the renderer (this repo). This tool
finds those differences and assigns each one a side.

Finding kinds
-------------
``unsupported_assertion``  (renderer, high)
    A line in the SOW that states a fact or number and cites no atom, where
    the number cannot be found anywhere in the envelope either. The failure
    mode the pipeline exists to prevent.
``uncited_surface_value``  (envelope, low)
    A number the SOW prints without an atom citation, which does exist in a
    precomputed envelope surface (pm_dashboard, project_vitals, ...). The
    renderer copied it faithfully; the surface carries no receipt for it.
``static_claim``  (renderer, medium)
    Fixed prose the renderer always prints that asserts something about the
    deal ("the governing claim is the one with the highest authority class")
    whether or not the envelope supports it.
``citation_unknown_atom``  (envelope | renderer, high)
    The SOW cites an atom id that is not in ``envelope.atoms``. Envelope side
    when the id is referenced by an envelope surface (the surface points at an
    atom that was not shipped); renderer side otherwise.
``text_mismatch``  (renderer, medium)
    A cited bullet whose text is not the cited atom's text (after the
    renderer's documented 240-char truncation and whitespace folding).
``amount_mismatch``  (envelope, high)
    A money row whose amount does not occur in the cited atom.
``unreviewed_atom_asserted``  (renderer, medium) / ``rejected_atom_asserted``
    (renderer, high) / ``low_authority_asserted`` (renderer, high)
    The SOW states a cited atom as contract content although the envelope marks
    it needs_review / rejected, or its authority is deleted_text or
    quoted_old_email.
``synthetic_atom_asserted``  (renderer, medium)
    The cited atom was minted by parser-os (work_order task atoms:
    ``structured.backfill_reason == "work_order"``). Its text is a machine
    summary, not a sentence in any source document.
``needs_data_with_evidence``  (renderer, high)
    A section prints ``[NEEDS DATA]`` while the envelope holds atoms of the type
    that section is about.
``broken_cross_reference``  (renderer, medium)
    "see §6 (Scope of Work)" where §6 is not Scope of Work, or no §6 exists.
``governing_authority_mismatch``  (envelope, high)
    A scope row's governing source is not the highest-ranked authority class
    among the governing claims (scope_truth takes ``max()`` over class names).
``aggregate_double_count``  (envelope, high)
    A printed money total that sums the same amount restated by several atoms,
    or sums atoms that are not accepted evidence.
``dropped_atom``  (renderer, low)
    A reviewed/auto-accepted content atom the SOW never cites. ``detail``
    says whether its type is read by no section or was cut by a limit/filter.

Usage::

    python -m sowsmith.tools.sow_diff envelope.json [--sow sow.md] [--json out.json]

With no ``--sow`` the envelope is rendered with the installed renderer.
Exit code is 1 when any finding at or above ``--fail-on`` (default: none) exists.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}

# Atom types each SOW section reads, as of render.py on origin/main (140b2e0).
# Kept here, not imported, so the tool describes the renderer rather than
# trusting it. Update both together.
SECTION_ATOM_TYPES: dict[str, tuple[str, ...]] = {
    "Scope of Work": ("service_line", "work_scope_item", "task", "deliverable"),
    "Change Order History": ("change_order_rule",),
    "Schedule & Milestones": ("milestone_phase", "lead_time_constraint", "blackout_date_range"),
    "SLA & Support Terms": ("contract_term", "sla_term", "warranty_term", "support_term"),
    "Site Access, Safety & Constraints": (
        "constraint", "site_access_restriction", "site_access_window", "compliance_rule",
        "compliance", "compliance_classification", "safety_requirement",
    ),
    "Acceptance Criteria": ("acceptance_criterion",),
}

# Atom types a section is *about* but reads only through a precomputed surface.
# If the section says NEEDS DATA while these atoms exist, the section lied.
SECTION_SUBJECT_TYPES: dict[str, tuple[str, ...]] = {
    **SECTION_ATOM_TYPES,
    "Out of Scope": ("exclusion",),
    "Risk Register": ("risk",),
    "Open Questions Before Kickoff": ("open_question",),
    "Stakeholders": ("stakeholder", "contact"),
    "Commercial Terms": ("money", "pricing_term", "vendor_line_item"),
}

# Types that carry no contract content on their own; not reported as dropped.
NON_CONTENT_TYPES = {"entity", "form_option_state", "quantity"}

STATIC_CLAIM_PATTERNS = (
    re.compile(r"highest authority class", re.I),
    re.compile(r"authoritative scope", re.I),
    re.compile(r"every claim .* traces", re.I),
    re.compile(r"uncontested across the source artifacts", re.I),
)

_ATOM_CITE = re.compile(r"atom `([^`]+)`")
_TABLE_ATOM = re.compile(r"`((?:atm|atom)_[A-Za-z0-9_\-]+)`")
_NUMBER = re.compile(r"(?<![\w.])\$?(\d[\d,]*(?:\.\d+)?)")
_HEADING = re.compile(r"^#{1,6}\s+(?:\d+\.\s+)?(.*)$")


@dataclass
class Finding:
    kind: str
    side: str  # "renderer" | "envelope"
    severity: str  # "low" | "medium" | "high"
    section: str
    detail: str
    atom_id: str | None = None
    line: int | None = None


@dataclass
class DiffReport:
    findings: list[Finding] = field(default_factory=list)
    cited_atom_ids: list[str] = field(default_factory=list)
    envelope_atom_count: int = 0

    def counts(self) -> dict[str, dict[str, int]]:
        by_side: Counter[str] = Counter(f.side for f in self.findings)
        by_kind: Counter[str] = Counter(f.kind for f in self.findings)
        by_sev: Counter[str] = Counter(f.severity for f in self.findings)
        return {"side": dict(by_side), "kind": dict(by_kind), "severity": dict(by_sev)}

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "envelope_atom_count": self.envelope_atom_count,
            "cited_atom_count": len(set(self.cited_atom_ids)),
            "findings": [asdict(f) for f in self.findings],
        }


# ───────────────────────── helpers ─────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\\|", "|")).strip().lower()


def _atom_text(atom: dict[str, Any]) -> str:
    for key in ("text", "raw_text", "normalized_text"):
        val = atom.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _structured(atom: dict[str, Any]) -> dict[str, Any]:
    val = atom.get("structured")
    if not isinstance(val, dict):
        val = atom.get("value")
    return val if isinstance(val, dict) else {}


def _walk_scalars(obj: Any, *, skip_keys: frozenset[str] = frozenset()) -> Iterable[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in skip_keys:
                continue
            yield from _walk_scalars(v, skip_keys=skip_keys)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_scalars(v, skip_keys=skip_keys)
    else:
        yield obj


def _number_forms(value: Any) -> set[str]:
    """Every textual form a numeric envelope value can take in the SOW."""
    out: set[str] = set()
    if isinstance(value, bool) or value is None:
        return out
    if isinstance(value, (int, float)):
        f = float(value)
        out |= {str(value), f"{f:,.2f}", f"{f:.2f}", f"{f:.3f}", f"{f:,.0f}", f"{f * 100:.0f}"}
        if f.is_integer():
            out.add(str(int(f)))
    elif isinstance(value, str):
        for m in _NUMBER.finditer(value):
            out.add(m.group(1))
    return out


def _parse_number(tok: str) -> str:
    return tok.lstrip("$")


def _is_placeholder(line: str) -> bool:
    return "[NEEDS DATA" in line or line.startswith("_No ") or line.startswith("_(+")


# ───────────────────────── core ─────────────────────────


def diff_sow(envelope: dict[str, Any], sow_markdown: str) -> DiffReport:
    atoms = [a for a in (envelope.get("atoms") or []) if isinstance(a, dict)]
    by_id = {str(a.get("id")): a for a in atoms if a.get("id")}
    report = DiffReport(envelope_atom_count=len(atoms))

    surfaces = {k: v for k, v in envelope.items() if k not in {"atoms", "edges", "packets", "entities"}}
    surface_numbers: set[str] = set()
    for scalar in _walk_scalars(surfaces):
        surface_numbers |= _number_forms(scalar)
    surface_blob = json.dumps(surfaces, default=str)
    all_numbers = set(surface_numbers)
    for scalar in _walk_scalars(envelope.get("atoms") or []):
        all_numbers |= _number_forms(scalar)

    section = "Header"
    section_lines: dict[str, list[str]] = {}
    lines = sow_markdown.splitlines()
    i = 0
    while i < len(lines):
        raw = lines[i]
        lineno = i + 1
        line = raw.strip()
        h = _HEADING.match(line)
        if h:
            section = h.group(1).split(" — ")[0].strip() if line.startswith("## ") else section
            if line.startswith("# "):
                section = "Header"
            i += 1
            continue
        if line == "---":
            section = "Footer"
            i += 1
            continue
        section_lines.setdefault(section, []).append(line)

        # A bullet's citation sits on the following line.
        cite_line = line
        if line.startswith("- ") and i + 1 < len(lines) and _ATOM_CITE.search(lines[i + 1]):
            cite_line = line + " " + lines[i + 1].strip()
            section_lines[section].append(lines[i + 1].strip())
            i += 1
        cited = _ATOM_CITE.findall(cite_line) + _TABLE_ATOM.findall(cite_line)
        cited = [c for c in dict.fromkeys(cited) if c not in {"—", "-"}]

        if cited:
            for aid in cited:
                report.cited_atom_ids.append(aid)
                _check_citation(report, envelope, by_id, surface_blob, section, lineno, line, aid)
        elif line and not _is_placeholder(line) and not set(line) <= set("|-: "):
            _check_uncited(report, section, lineno, line, surface_numbers, all_numbers)
        i += 1

    _check_cross_references(report, lines)
    _check_governing_authority(report, envelope, lines)
    _check_money_total(report, envelope, sow_markdown)
    _check_needs_data(report, envelope, section_lines)
    _check_dropped(report, atoms, set(report.cited_atom_ids), surface_blob)
    report.findings.sort(key=lambda f: (-SEVERITY_ORDER[f.severity], f.side, f.kind, f.line or 0))
    return report


def _check_citation(report, envelope, by_id, surface_blob, section, lineno, line, aid) -> None:
    atom = by_id.get(aid)
    if atom is None:
        in_surface = f'"{aid}"' in surface_blob
        report.findings.append(Finding(
            kind="citation_unknown_atom",
            side="envelope" if in_surface else "renderer",
            severity="high", section=section, atom_id=aid, line=lineno,
            detail=("an envelope surface references an atom that is not in envelope.atoms"
                    if in_surface else "the SOW cites an atom id that appears nowhere in the envelope"),
        ))
        return

    status = str(atom.get("review_status") or "")
    authority = str(atom.get("authority_class") or "")
    if status == "rejected":
        report.findings.append(Finding("rejected_atom_asserted", "renderer", "high", section,
                                       "cited atom has review_status=rejected", aid, lineno))
    elif status == "needs_review":
        report.findings.append(Finding("unreviewed_atom_asserted", "renderer", "medium", section,
                                       "cited atom has review_status=needs_review", aid, lineno))
    if authority in {"deleted_text", "quoted_old_email"}:
        report.findings.append(Finding("low_authority_asserted", "renderer", "high", section,
                                       f"cited atom has authority_class={authority}", aid, lineno))
    st = _structured(atom)
    if st.get("backfill_reason") == "work_order":
        report.findings.append(Finding(
            "synthetic_atom_asserted", "renderer", "medium", section,
            "cited atom was minted by the work_order stage; supporting atom "
            f"{st.get('backfilled_from_atom_id')!s}", aid, lineno))

    # Bullet text vs atom text.
    if line.startswith("- "):
        shown = _norm(line[2:])
        truth = _norm(_atom_text(atom))
        if shown and not truth.startswith(shown[:230]):
            report.findings.append(Finding("text_mismatch", "renderer", "medium", section,
                                           f"SOW text {shown[:80]!r} is not the atom's text", aid, lineno))

    # Money rows: first cell is an amount.
    m = re.match(r"^\|\s*\$([\d,]+(?:\.\d+)?)\s*\|", line)
    if m and section.startswith("Commercial"):
        amount = float(m.group(1).replace(",", ""))
        forms: set[str] = set()
        for s in _walk_scalars(atom):
            forms |= _number_forms(s)
        if not ({f.replace(",", "") for f in _number_forms(amount)} & {f.replace(",", "") for f in forms}):
            report.findings.append(Finding(
                "amount_mismatch", "envelope", "high", section,
                f"money row ${m.group(1)} does not occur in the cited atom", aid, lineno))


def _check_uncited(report, section, lineno, line, surface_numbers, all_numbers) -> None:
    if section == "Header" and line.startswith(">"):
        # The header disclaimer is covered by STATIC_CLAIM_PATTERNS below.
        pass
    for pat in STATIC_CLAIM_PATTERNS:
        if pat.search(line):
            report.findings.append(Finding("static_claim", "renderer", "medium", section,
                                           f"fixed prose asserts: {line[:120]!r}", None, lineno))
            return
    if line.startswith("_Generated by") or line == "---":
        return
    if section == "Header" and re.match(r"^\*\*(Project ID|Compile ID|Generated):", line):
        return
    line = re.sub(r"^\d+\.\s+", "", line)  # ordered-list marker
    nums = [_parse_number(n) for n in _NUMBER.findall(line)]
    # Section/footnote numbers ("§6", "1.") are layout, not claims.
    nums = [n for n in nums if not re.search(rf"§{re.escape(n)}\b", line)]
    if not nums:
        return
    missing = [n for n in nums if n not in all_numbers and n.replace(",", "") not in {
        x.replace(",", "") for x in all_numbers}]
    if missing:
        report.findings.append(Finding(
            "unsupported_assertion", "renderer", "high", section,
            f"numbers {missing} appear in no atom or surface: {line[:120]!r}", None, lineno))
    else:
        report.findings.append(Finding(
            "uncited_surface_value", "envelope", "low", section,
            f"value(s) {nums} come from a precomputed surface with no atom receipt: {line[:100]!r}",
            None, lineno))


def _check_cross_references(report, lines) -> None:
    titles: dict[str, str] = {}
    for ln in lines:
        m = re.match(r"^##\s+(\d+)\.\s+(.*)$", ln.strip())
        if m:
            titles[m.group(1)] = m.group(2).strip()
    for n, ln in enumerate(lines, start=1):
        for m in re.finditer(r"§(\d+)(?:\s+\(([^)]+)\))?", ln):
            num, want = m.group(1), m.group(2)
            have = titles.get(num)
            if have is None:
                detail = f"§{num} does not exist (sections run to §{max(map(int, titles), default=0)})"
            elif want and not have.lower().startswith(want.lower()):
                detail = f"§{num} is '{have}', not '{want}'"
            else:
                continue
            report.findings.append(Finding("broken_cross_reference", "renderer", "medium",
                                           "—", detail, None, n))


def _check_governing_authority(report, envelope, lines) -> None:
    devices = (envelope.get("scope_truth") or {}).get("devices") or []
    for d in devices:
        gov = d.get("governing_authority")
        claims = [c for c in (d.get("governing_claims") or []) if isinstance(c, dict)]
        if not gov or not claims:
            continue
        top = max(claims, key=lambda c: c.get("authority_rank") or 0)
        classes = {c.get("authority_class") for c in claims}
        if gov == top.get("authority_class"):
            continue
        lineno = next((n for n, ln in enumerate(lines, start=1)
                       if f"`{gov}`" in ln and f"**{d.get('canonical_quantity')}**" in ln), None)
        report.findings.append(Finding(
            "governing_authority_mismatch", "envelope", "high", "Scope of Work",
            f"{d.get('device')} @ {d.get('site')}: governing quantity {d.get('canonical_quantity')} is "
            f"claimed by {sorted(c for c in classes if c)}; highest rank is `{top.get('authority_class')}` "
            f"but the SOW names `{gov}` as governing source",
            top.get("atom_id"), lineno))


def _check_money_total(report, envelope, sow_markdown) -> None:
    money = ((envelope.get("pm_dashboard") or {}).get("money_summary") or {})
    rows = money.get("atoms") or []
    total = money.get("total")
    if not rows or not total or f"{float(total):,.2f}" not in sow_markdown:
        return
    by_id = {str(a.get("id")): a for a in (envelope.get("atoms") or []) if isinstance(a, dict)}
    amounts = Counter(float(r.get("amount") or 0) for r in rows)
    repeated = {a: c for a, c in amounts.items() if c > 1}
    unaccepted = [r.get("atom_id") for r in rows
                  if (by_id.get(str(r.get("atom_id"))) or {}).get("review_status") not in {"auto_accepted", "approved"}]
    if repeated or unaccepted:
        report.findings.append(Finding(
            "aggregate_double_count", "envelope", "high", "Commercial Terms",
            f"printed total ${float(total):,.2f} sums {len(rows)} money atoms"
            + (f"; amounts restated: {repeated}" if repeated else "")
            + (f"; not accepted evidence: {unaccepted}" if unaccepted else "")))


def _check_needs_data(report, envelope, section_lines) -> None:
    atoms = [a for a in (envelope.get("atoms") or []) if isinstance(a, dict)]
    types = Counter(str(a.get("atom_type")) for a in atoms if a.get("review_status") != "rejected")
    for section, body in section_lines.items():
        if not any("[NEEDS DATA" in ln for ln in body):
            continue
        subject = SECTION_SUBJECT_TYPES.get(section, ())
        present = {t: types[t] for t in subject if types.get(t)}
        if present:
            report.findings.append(Finding(
                "needs_data_with_evidence", "renderer", "high", section,
                f"section prints [NEEDS DATA] while the envelope holds {present}"))


def _check_dropped(report, atoms, cited: set[str], surface_blob: str = "") -> None:
    read_types = {t for ts in SECTION_ATOM_TYPES.values() for t in ts}
    for atom in atoms:
        aid = str(atom.get("id"))
        atype = str(atom.get("atom_type"))
        if aid in cited or atype in NON_CONTENT_TYPES:
            continue
        if atom.get("review_status") in {"rejected", "needs_review"}:
            continue
        if f'"{aid}"' in surface_blob:
            why = "reaches the SOW only through a precomputed surface; the rendered row carries no atom id"
        else:
            why = None
        why = why or ("atom type is read by no SOW section" if atype not in read_types
               else "type is read by a section, but the atom was not rendered (limit, dedupe, "
                    "length/QA filter, or a branch that discards it)")
        report.findings.append(Finding("dropped_atom", "renderer", "low", "—",
                                       f"{atype}: {why}", aid))


# ───────────────────────── output ─────────────────────────


def render_report_markdown(report: DiffReport) -> str:
    c = report.counts()
    out = [
        "# SOW vs envelope diff",
        "",
        f"Envelope atoms: {report.envelope_atom_count} · cited by SOW: {len(set(report.cited_atom_ids))}",
        "",
        f"By side: {c['side']}  ",
        f"By severity: {c['severity']}  ",
        f"By kind: {c['kind']}",
        "",
        "| Sev | Side | Kind | Section | Line | Atom | Detail |",
        "|---|---|---|---|---|---|---|",
    ]
    for f in report.findings:
        detail = f.detail.replace("|", "\\|").replace("\n", " ")
        out.append(f"| {f.severity} | {f.side} | {f.kind} | {f.section} | {f.line or ''} "
                   f"| {f.atom_id or ''} | {detail} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sowsmith-diff", description=__doc__.split("\n")[0])
    ap.add_argument("envelope", type=Path)
    ap.add_argument("--sow", type=Path, help="rendered SOW markdown (default: render the envelope now)")
    ap.add_argument("--json", type=Path, help="write the JSON report here")
    ap.add_argument("--out", type=Path, help="write the markdown report here (default stdout)")
    ap.add_argument("--fail-on", choices=["low", "medium", "high"], default=None)
    args = ap.parse_args(argv)

    envelope = json.loads(args.envelope.read_text(encoding="utf-8"))
    if args.sow:
        sow = args.sow.read_text(encoding="utf-8")
    else:
        from sowsmith.render import build_sow_markdown
        sow = build_sow_markdown(envelope)
    report = diff_sow(envelope, sow)
    if args.json:
        args.json.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    md = render_report_markdown(report)
    if args.out:
        args.out.write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    if args.fail_on:
        floor = SEVERITY_ORDER[args.fail_on]
        if any(SEVERITY_ORDER[f.severity] >= floor for f in report.findings):
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
