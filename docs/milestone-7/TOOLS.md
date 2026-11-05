# Milestone 7 tools: capture and diff

Two tools for separating "wrong in the envelope" from "wrong in the renderer".
Both live in `src/sowsmith/tools/` and are tested on synthetic envelopes only.

## Capture (PUR-60): `python -m sowsmith.tools.capture`

Saves the envelope SowSmith was actually fed and the SOW it rendered, from the
production call path: parser-os-worker builds the envelope, uploads
`deals/<id>/orbitbrief/latest/envelope.json`, passes **the same dict** to
`build_sow_markdown`, and uploads `SOW_DRAFT.md` beside it.

The two blobs are not guaranteed to come from one run. The worker logs and
swallows a render exception, so an old `SOW_DRAFT.md` can sit beside a newer
envelope. The tool checks the pairing instead of assuming it:

| Check | Meaning |
|---|---|
| `sow_compile_id_matches` | SOW footer names the envelope's `compile_id` |
| `rerender_identical` | installed SowSmith reproduces the captured SOW from the captured envelope |
| `fresh` | `generated_at` is later than `--deployed-at` |
| `sow_older_than_envelope` | blob timestamps (blob source only) |

`capture.json` also lists top-level fields present, empty and missing (and which
of those SowSmith reads), atom field coverage, and counts by type, review
status, authority and verification, including work-order task atoms.

A human runs it with their own credentials. Captures contain customer data:
write them outside any checkout. The tool refuses a git work tree by default,
and `sow-captures/` is gitignored.

```bash
pip install azure-storage-blob azure-identity
export AZURE_STORAGE_CONNECTION_STRING=...        # or --account-url + az login
python -m sowsmith.tools.capture --source blob --container <container> \
  --deal <id>:single_site --deal <id>:multi_site --deal <id>:time_and_materials \
  --deployed-at <worker deploy time, ISO> --out ~/sow-captures
```

`--source api --base-url https://<function app>` with `PURPULSE_API_TOKEN` reads
the same blobs through `GET /api/quoting/deal/<id>/orbitbrief/latest/{envelope,sow-draft}`.
That route trims the markdown and does not return blob timestamps.

## Diff (PUR-61): `python -m sowsmith.tools.sow_diff`

```bash
python -m sowsmith.tools.sow_diff ~/sow-captures/<id>/envelope.json \
  --sow ~/sow-captures/<id>/SOW_DRAFT.md --json diff.json --out diff.md
```

Each finding has a **side** (`envelope` or `renderer`) and a severity. See the
module docstring for the full list of finding kinds. The ones to read first:

* `unsupported_assertion`: a number in the SOW that appears in no atom and no surface
* `citation_unknown_atom`: the SOW cites an atom that is not in the envelope (including `atom None`)
* `rejected_atom_asserted`, `unreviewed_atom_asserted`, `low_authority_asserted`, `synthetic_atom_asserted`
* `governing_authority_mismatch`, `aggregate_double_count`: envelope surfaces that are wrong before SowSmith touches them
* `needs_data_with_evidence`: `[NEEDS DATA]` printed while the atoms exist

`synthetic/` holds the fixture envelopes, the SOWs the current renderer makes
from them, and the resulting diffs. Regenerate them from
`tests/fixtures/synthetic_envelope.py`.
