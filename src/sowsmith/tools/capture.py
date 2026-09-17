"""Capture the envelope SowSmith was actually fed, and the SOW it rendered (PUR-60).

The real call path (parser-os-worker ``src/parser_os_worker/main.py``, step 4a-5c):

    envelope = build_orbitbrief_envelope(...)
    envelope["compile_id"] = job.compile_id
    upload  deals/<deal>/orbitbrief/latest/envelope.json     <- json.dumps(envelope)
    sow_md = build_sow_markdown(envelope)                      <- the same dict
    upload  deals/<deal>/orbitbrief/latest/SOW_DRAFT.md

So ``envelope.json`` *is* SowSmith's input -- but only if both blobs come from
the same run. They can drift: a SowSmith exception is logged and swallowed, so
the previous ``SOW_DRAFT.md`` stays beside a newer envelope; and a later
recompile can replace the envelope. This tool therefore proves the pairing
instead of assuming it:

* ``sow_compile_id_matches``  -- the SOW footer names the envelope's compile_id
* ``rerender_identical``      -- re-rendering the captured envelope with the
  installed SowSmith reproduces the captured SOW (whitespace-trimmed, because
  the Function App route trims). True proves the envelope was the input *and*
  the installed renderer matches the deployed one.
* ``fresh``                   -- ``generated_at`` is later than the deploy time a
  human supplies with ``--deployed-at`` (stale envelopes have passed a
  freshness check three times before; do not skip it)

It also records, per capture, which top-level fields are present, empty or
missing, atom field coverage, and counts by atom_type / review_status /
authority_class, including work-order task atoms.

Sources (credentials are supplied by the human running it, never stored):

``--source blob``  Azure Blob. ``AZURE_STORAGE_CONNECTION_STRING`` or
                   ``--account-url`` + DefaultAzureCredential. Needs
                   ``pip install azure-storage-blob azure-identity``.
``--source api``   Function App routes ``GET /api/quoting/deal/<id>/orbitbrief/latest/{envelope,sow-draft}``
                   with ``--base-url`` and a bearer token in ``PURPULSE_API_TOKEN``.
``--source dir``   A local directory laid out like the container
                   (``<root>/deals/<id>/orbitbrief/latest/...``). Used by tests.

Captures contain customer data. Write them outside any git checkout; the
default ``--out`` is ``./sow-captures`` and the tool refuses a path inside a
git work tree unless ``--allow-in-repo`` is given.

Example::

    python -m sowsmith.tools.capture --source blob \\
        --deal <id>:single_site --deal <id>:multi_site --deal <id>:time_and_materials \\
        --deployed-at 2026-09-16T18:00:00Z --out ~/sow-captures
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

LATEST = "deals/{deal}/orbitbrief/latest/"
ENVELOPE_BLOB = "envelope.json"
SOW_BLOB = "SOW_DRAFT.md"
OPTIONAL_BLOBS = ("compile-idempotency.json",)

# Top-level keys the current envelope emits (parser-os b25c027) and whether
# render.py reads them. Recorded so a capture shows at a glance what arrived.
EXPECTED_TOP_LEVEL = {
    "schema_version": True, "project_id": True, "compile_id": True, "generated_at": True,
    "summary": True, "documents": False, "atoms": True, "packets": False, "entities": False,
    "edges": False, "indexes": True, "coverage": False, "deal_timeline": False,
    "pm_dashboard": True, "sow_readiness_scorecard": True, "srl_missing_checklist": True,
    "reconciliation": False, "scope_truth": True, "change_order_timeline": True,
    "site_readiness": True, "stakeholder_load": True, "truth_gate": False,
    "project_vitals": True, "run_scope": False, "deal_header": False,
    "deal_financials": False, "bill_of_materials": False, "service_routing": False,
    "facet_sections": False, "site_evidence_conflicts": False,
    "site_count_reconciliation": False, "drawings": False, "crm": False,
    "pm_preferences": False, "warnings": False, "email_threads": False,
    "compile_capabilities": False,
}
ATOM_FIELDS = (
    "id", "artifact_id", "atom_type", "authority_class", "confidence", "text", "section_path",
    "locator", "verified", "entity_keys", "structured", "calibrated_confidence", "review_status",
    "review_flags", "confidence_raw", "decision_provenance",
)


class Source(Protocol):
    def read(self, deal_id: str, name: str) -> tuple[bytes, str | None] | None:
        """Return (bytes, last_modified_iso) or None when absent."""


@dataclass
class DirSource:
    root: Path

    def read(self, deal_id: str, name: str):
        p = self.root / LATEST.format(deal=deal_id) / name
        if not p.is_file():
            return None
        mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        return p.read_bytes(), mtime.strftime("%Y-%m-%dT%H:%M:%SZ")


class BlobSource:  # pragma: no cover - needs Azure; exercised only by a human
    def __init__(self, container: str, account_url: str | None):
        try:
            from azure.storage.blob import BlobServiceClient
        except ImportError as exc:
            raise SystemExit("pip install azure-storage-blob azure-identity") from exc
        conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if conn:
            svc = BlobServiceClient.from_connection_string(conn)
        elif account_url:
            from azure.identity import DefaultAzureCredential
            svc = BlobServiceClient(account_url, credential=DefaultAzureCredential())
        else:
            raise SystemExit("set AZURE_STORAGE_CONNECTION_STRING or pass --account-url")
        self.container = svc.get_container_client(container)

    def read(self, deal_id: str, name: str):
        from azure.core.exceptions import ResourceNotFoundError
        blob = self.container.get_blob_client(LATEST.format(deal=deal_id) + name)
        try:
            props = blob.get_blob_properties()
            data = blob.download_blob().readall()
        except ResourceNotFoundError:
            return None
        return data, props.last_modified.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiSource:  # pragma: no cover - needs a live Function App
    def __init__(self, base_url: str):
        token = os.environ.get("PURPULSE_API_TOKEN")
        if not token:
            raise SystemExit("set PURPULSE_API_TOKEN")
        self.base = base_url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    def read(self, deal_id: str, name: str):
        route = {ENVELOPE_BLOB: "envelope", SOW_BLOB: "sow-draft"}.get(name)
        if route is None:
            return None
        req = urllib.request.Request(
            f"{self.base}/api/quoting/deal/{deal_id}/orbitbrief/latest/{route}", headers=self.headers)
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if route == "envelope":
            return json.dumps(body["data"], indent=2, ensure_ascii=False).encode("utf-8"), None
        return body["markdown"].encode("utf-8"), None


# ───────────────────────── analysis ─────────────────────────


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_empty(v: Any) -> bool:
    return v is None or v == "" or v == [] or v == {}


def field_report(envelope: dict[str, Any]) -> dict[str, Any]:
    present, empty, missing = [], [], []
    for key in EXPECTED_TOP_LEVEL:
        if key not in envelope:
            missing.append(key)
        elif _is_empty(envelope[key]):
            empty.append(key)
        else:
            present.append(key)
    unexpected = sorted(k for k in envelope if k not in EXPECTED_TOP_LEVEL)
    atoms = [a for a in (envelope.get("atoms") or []) if isinstance(a, dict)]

    def tally(key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for a in atoms:
            out[str(a.get(key))] = out.get(str(a.get(key)), 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    atom_field_coverage = {
        f: sum(1 for a in atoms if not _is_empty(a.get(f))) for f in ATOM_FIELDS
    }
    unknown_atom_fields = sorted({k for a in atoms for k in a} - set(ATOM_FIELDS))
    work_order = [a.get("id") for a in atoms
                  if isinstance(a.get("structured"), dict) and a["structured"].get("backfill_reason") == "work_order"]
    open_qs = ((envelope.get("pm_dashboard") or {}).get("open_questions") or [])
    return {
        "top_level": {
            "present": present, "empty": empty, "missing": missing, "unexpected": unexpected,
            "read_by_sowsmith_but_empty_or_missing": [
                k for k in empty + missing if EXPECTED_TOP_LEVEL.get(k)],
        },
        "atom_count": len(atoms),
        "atom_field_coverage": atom_field_coverage,
        "unknown_atom_fields": unknown_atom_fields,
        "atom_type": tally("atom_type"),
        "review_status": tally("review_status"),
        "authority_class": tally("authority_class"),
        "verified": tally("verified"),
        "work_order_task_atoms": len(work_order),
        # Where the envelope already lost what the atoms knew: surface rows
        # with no atom behind them.
        "open_questions_without_atom": sum(1 for q in open_qs if not q.get("atom_id")),
        "summary_atom_count_matches": (envelope.get("summary") or {}).get("atom_count") in (None, len(atoms)),
    }


def pairing_report(envelope: dict[str, Any], sow_md: str, *, deployed_at: str | None,
                   envelope_modified: str | None, sow_modified: str | None) -> dict[str, Any]:
    compile_id = str(envelope.get("compile_id") or "")
    footer = re.search(r"from the OrbitBrief envelope `([^`]*)`", sow_md)
    header = re.search(r"\*\*Compile ID:\*\* `([^`]*)`", sow_md)
    sow_cid = (footer or header).group(1) if (footer or header) else None
    try:
        from sowsmith import build_sow_markdown
        from sowsmith.render import SOW_VERSION
        rerendered = build_sow_markdown(envelope)
        rerender_identical = rerendered.strip() == sow_md.strip()
        renderer = SOW_VERSION
    except Exception as exc:  # the deployed worker swallows this too; record it
        rerender_identical, renderer = False, f"error: {exc!r}"
    generated = _parse_iso(envelope.get("generated_at"))
    deployed = _parse_iso(deployed_at)
    env_m, sow_m = _parse_iso(envelope_modified), _parse_iso(sow_modified)
    return {
        "envelope_compile_id": compile_id,
        "sow_compile_id": sow_cid,
        "sow_compile_id_matches": sow_cid == compile_id and bool(compile_id),
        "rerender_identical": rerender_identical,
        "renderer_used_for_rerender": renderer,
        "generated_at": envelope.get("generated_at"),
        "deployed_at": deployed_at,
        "fresh": (generated > deployed) if (generated and deployed) else None,
        "envelope_last_modified": envelope_modified,
        "sow_last_modified": sow_modified,
        "sow_older_than_envelope": (sow_m < env_m) if (sow_m and env_m) else None,
    }


def capture_deal(source: Source, deal_id: str, shape: str, out_root: Path,
                 *, deployed_at: str | None) -> dict[str, Any]:
    out = out_root / deal_id
    out.mkdir(parents=True, exist_ok=True)
    env_raw = source.read(deal_id, ENVELOPE_BLOB)
    sow_raw = source.read(deal_id, SOW_BLOB)
    record: dict[str, Any] = {
        "deal_id": deal_id, "shape": shape,
        "captured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "errors": [],
    }
    if env_raw is None:
        record["errors"].append("envelope.json not found")
    if sow_raw is None:
        record["errors"].append("SOW_DRAFT.md not found")
    for name in OPTIONAL_BLOBS:
        got = source.read(deal_id, name)
        if got is not None:
            (out / name).write_bytes(got[0])
            try:
                record[name] = json.loads(got[0])
            except ValueError:
                pass
    if env_raw is None or sow_raw is None:
        (out / "capture.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
        return record
    (out / ENVELOPE_BLOB).write_bytes(env_raw[0])
    (out / SOW_BLOB).write_bytes(sow_raw[0])
    record["sha256"] = {
        ENVELOPE_BLOB: hashlib.sha256(env_raw[0]).hexdigest(),
        SOW_BLOB: hashlib.sha256(sow_raw[0]).hexdigest(),
    }
    envelope = json.loads(env_raw[0])
    sow_md = sow_raw[0].decode("utf-8")
    record["pairing"] = pairing_report(envelope, sow_md, deployed_at=deployed_at,
                                       envelope_modified=env_raw[1], sow_modified=sow_raw[1])
    record["fields"] = field_report(envelope)
    (out / "capture.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    return record


def _inside_git_worktree(path: Path) -> bool:
    probe = path if path.exists() else path.parent
    while not probe.exists():
        probe = probe.parent
    try:
        r = subprocess.run(["git", "-C", str(probe), "rev-parse", "--is-inside-work-tree"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and r.stdout.strip() == "true"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="sowsmith-capture", description=__doc__.split("\n")[0])
    ap.add_argument("--source", choices=["blob", "api", "dir"], required=True)
    ap.add_argument("--deal", action="append", required=True, metavar="ID[:SHAPE]",
                    help="deal id, optionally with a shape label (single_site, multi_site, time_and_materials)")
    ap.add_argument("--out", type=Path, default=Path("sow-captures"))
    ap.add_argument("--deployed-at", help="ISO time of the worker deploy the envelopes must postdate")
    ap.add_argument("--container", help="blob container name (required for --source blob)")
    ap.add_argument("--account-url")
    ap.add_argument("--base-url")
    ap.add_argument("--root", type=Path, help="for --source dir")
    ap.add_argument("--allow-in-repo", action="store_true")
    args = ap.parse_args(argv)

    if not args.allow_in_repo and _inside_git_worktree(args.out.resolve()):
        print(f"refusing to write customer data inside a git work tree: {args.out}", file=sys.stderr)
        return 2
    if args.source == "dir":
        if not args.root:
            ap.error("--root is required for --source dir")
        source: Source = DirSource(args.root)
    elif args.source == "blob":  # pragma: no cover
        if not args.container:
            ap.error("--container is required for --source blob")
        source = BlobSource(args.container, args.account_url)
    else:  # pragma: no cover
        if not args.base_url:
            ap.error("--base-url is required for --source api")
        source = ApiSource(args.base_url)
    if not args.deployed_at:
        print("warning: no --deployed-at; freshness is recorded as unknown", file=sys.stderr)

    ok = True
    for spec in args.deal:
        deal_id, _, shape = spec.partition(":")
        rec = capture_deal(source, deal_id, shape or "unspecified", args.out, deployed_at=args.deployed_at)
        p = rec.get("pairing") or {}
        good = not rec["errors"] and p.get("sow_compile_id_matches") and p.get("fresh") is not False
        ok = ok and bool(good)
        print(f"{deal_id} [{rec['shape']}] errors={rec['errors']} compile_match={p.get('sow_compile_id_matches')} "
              f"rerender_identical={p.get('rerender_identical')} fresh={p.get('fresh')}")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
