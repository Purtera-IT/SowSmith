# SowSmith

**Deterministic Statement of Work generator from OrbitBrief envelopes.**

SowSmith consumes the `orbitbrief.input.v2` envelope produced by
[parser-os](https://github.com/Purtera-IT/parser-os) and emits a
contract-grade Statement of Work in markdown. Every claim in the
output traces to a specific atom ID in the source artifacts.
Missing fields surface explicitly as `[NEEDS DATA: field_id]` so a
PM scanning the SOW sees gaps instead of silently shipping
incomplete contracts.

**Zero LLM in the hot path.** The structural backbone + every
receipt comes from this code. An LLM polish layer (in
[Orbitbrief-Core](https://github.com/Purtera-IT/Orbitbrief-Core)
or downstream) can soften phrasing without ever re-deriving the
math.

## Architecture

```
  parser-os                       SowSmith                   downstream
  ─────────                       ────────                   ──────────
  artifacts                                                      LLM
     ↓                                                         polish
   atoms                                                          ↓
     ↓                                                       client SOW
   graph
     ↓
  packets        →  orbitbrief.input.v2  →   sow.md
     ↓               envelope JSON          (this repo)
  envelope
```

## Installation

```bash
pip install -e .
# or, with dev dependencies for pytest:
pip install -e ".[dev]"
```

## CLI usage

```bash
# Render an envelope JSON to a SOW markdown file
sowsmith render path/to/orbitbrief.input.json
# wrote path/to/sow.md  (11,236 chars, 244 lines)

# Custom output path
sowsmith render envelope.json --out my_sow.md

# Print to stdout
sowsmith render envelope.json --out -

# Version
sowsmith --version
# sowsmith 0.1.0 (renderer sowsmith_v1)
```

## Python API

```python
import json
from sowsmith import build_sow_markdown

envelope = json.loads(open("orbitbrief.input.json").read())
sow_markdown = build_sow_markdown(envelope)
open("sow.md", "w", encoding="utf-8").write(sow_markdown)
```

## What's in the SOW

17 sections, every one receipt-grounded:

1. Header — project ID, compile ID, readiness score + band
2. Executive Summary
3. Project Vitals — 0-100 cockpit score with component breakdown
4. Stakeholders — contact table + workload allocation matrix
5. Sites & Locations — per-site readiness scores
6. Scope of Work — authority-weighted canonical quantities
7. Change Order History — chronological audit with from→to deltas
8. Out of Scope — explicit exclusions with source atom IDs
9. Schedule & Milestones — sorted ISO dates
10. Commercial Terms — money atoms with citations
11. SLA & Support Terms — structured SLA targets
12. Site Access, Safety & Constraints
13. Acceptance Criteria
14. Risk Register — sorted by severity, owner + mitigation
15. Open Questions Before Kickoff
16. Readiness Audit — SOW scorecard + SRL coverage by category
17. Evidence Trail — full audit for every contested claim

## Envelope contract

SowSmith reads the following top-level fields from the envelope
(all produced by parser-os):

| Field | Source |
|---|---|
| `project_vitals` | OrbitBrief-Core `build_project_vitals` |
| `pm_dashboard` | OrbitBrief-Core `build_pm_dashboard` |
| `sow_readiness_scorecard` | OrbitBrief-Core `build_sow_readiness_scorecard` |
| `srl_missing_checklist` | OrbitBrief-Core `build_srl_missing_checklist` |
| `scope_truth` | OrbitBrief-Core `build_scope_truth` |
| `change_order_timeline` | OrbitBrief-Core `build_change_order_timeline` |
| `site_readiness` | OrbitBrief-Core `build_site_readiness` |
| `stakeholder_load` | OrbitBrief-Core `build_stakeholder_load` |
| `atoms`, `edges`, `entities`, `packets`, `indexes`, `summary`, `documents` | parser-os envelope core |

Missing surfaces are gracefully skipped — the SOW is best-effort,
not best-guessing.

## Related Purtera repos

- **[parser-os](https://github.com/Purtera-IT/parser-os)** — the
  deterministic evidence compiler that produces the envelope
- **[Orbitbrief-Core](https://github.com/Purtera-IT/Orbitbrief-Core)** —
  LLM-side PM handoff renderer; consumes the same envelope to
  produce a styled HTML handoff
- **purpulse.app** — the canonical policy pack
  (`SOWSmith_policy_pack_v2.yaml`, ~12k lines) and SRL field
  catalog (`SOW_field_catalog_v2.yaml`, ~10.6k lines = 707-field
  Requirements Library). Future SowSmith versions will load
  these directly to expand clause coverage.

## Tests

```bash
pytest
```

8 tests cover: minimal envelope render, all 17 sections present,
`[NEEDS DATA]` placeholders, vitals rendering, scope_truth with
contested audit, change-order delta, stakeholder workload,
severity-sorted risk register, and deterministic output.

## Versioning

| Component | Version |
|---|---|
| Package | `0.1.0` |
| Renderer | `sowsmith_v1` |
| Envelope contract | `orbitbrief.input.v2` |

## License

Proprietary. Purtera-IT.
