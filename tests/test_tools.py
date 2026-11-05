"""Tests for the PUR-60 capture tool and the PUR-61 diff tool, on synthetic envelopes only."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from sowsmith import build_sow_markdown
from sowsmith.tools import capture, sow_diff
from tests.fixtures.synthetic_envelope import COMPILE_ID, build_synthetic_envelope


def _kinds(report, side=None):
    return {f.kind for f in report.findings if side is None or f.side == side}


# ───────────────────────── diff ─────────────────────────


def test_diff_with_devices_assigns_envelope_defects_to_envelope():
    env = build_synthetic_envelope()
    report = sow_diff.diff_sow(env, build_sow_markdown(env))
    envelope_side = _kinds(report, "envelope")
    assert "governing_authority_mismatch" in envelope_side
    assert "aggregate_double_count" in envelope_side


def test_diff_flags_unsupported_citation_and_trust_violations():
    env = build_synthetic_envelope()
    report = sow_diff.diff_sow(env, build_sow_markdown(env))
    unknown = [f for f in report.findings if f.kind == "citation_unknown_atom"]
    assert unknown and unknown[0].atom_id == "None" and unknown[0].side == "renderer"
    assert "low_authority_asserted" in _kinds(report, "renderer")
    assert "broken_cross_reference" in _kinds(report, "renderer")
    assert "static_claim" in _kinds(report, "renderer")


def test_diff_without_devices_catches_rejected_and_work_order_atoms():
    env = build_synthetic_envelope(with_devices=False, exclusions_surface=False)
    report = sow_diff.diff_sow(env, build_sow_markdown(env))
    by_kind = {(f.kind, f.atom_id) for f in report.findings}
    assert ("rejected_atom_asserted", "atm_svc_rej") in by_kind
    assert ("synthetic_atom_asserted", "atm_wo_task_01") in by_kind
    assert ("unreviewed_atom_asserted", "atm_wo_task_01") in by_kind
    assert "needs_data_with_evidence" in _kinds(report)


def test_diff_reports_services_dropped_when_devices_present():
    env = build_synthetic_envelope()
    report = sow_diff.diff_sow(env, build_sow_markdown(env))
    dropped = {f.atom_id for f in report.findings if f.kind == "dropped_atom"}
    assert {"atm_svc_01", "atm_task_01", "atm_deliv_01", "atm_scope_01"} <= dropped
    # rejected / unreviewed atoms are not "dropped": leaving them out is correct
    assert "atm_svc_rej" not in dropped and "atm_wo_task_01" not in dropped


def test_diff_detects_text_and_amount_tampering():
    env = build_synthetic_envelope()
    md = build_sow_markdown(env)
    md = md.replace("- Each cable run passes Cat6A certification testing.",
                    "- Each cable run passes Cat8 certification testing.")
    md = md.replace("| $12,500.00 | Fixed fee", "| $99,000.00 | Fixed fee")
    report = sow_diff.diff_sow(env, md)
    assert ("text_mismatch", "atm_accept_01") in {(f.kind, f.atom_id) for f in report.findings}
    assert ("amount_mismatch", "atm_money_sow") in {(f.kind, f.atom_id) for f in report.findings}


def test_diff_flags_invented_numbers_as_unsupported():
    env = build_synthetic_envelope()
    md = build_sow_markdown(env) + "\nThe engagement requires 777 technician hours.\n"
    report = sow_diff.diff_sow(env, md)
    hits = [f for f in report.findings if f.kind == "unsupported_assertion"]
    assert hits and "777" in hits[0].detail and hits[0].side == "renderer"


def test_clean_sow_has_no_high_renderer_findings_from_citations():
    env = build_synthetic_envelope()
    env["atoms"] = [a for a in env["atoms"] if a["review_status"] == "auto_accepted"]
    env["pm_dashboard"]["open_questions"] = []
    env["pm_dashboard"]["money_summary"] = {"total": 0, "atoms": []}
    report = sow_diff.diff_sow(env, build_sow_markdown(env))
    assert not {"citation_unknown_atom", "rejected_atom_asserted", "unreviewed_atom_asserted",
                "synthetic_atom_asserted", "text_mismatch"} & _kinds(report)


def test_diff_cli_writes_json_and_fails_on_high(tmp_path: Path, capsys):
    env_path = tmp_path / "envelope.json"
    env_path.write_text(json.dumps(build_synthetic_envelope()), encoding="utf-8")
    out_json = tmp_path / "diff.json"
    rc = sow_diff.main([str(env_path), "--json", str(out_json), "--fail-on", "high"])
    assert rc == 1
    data = json.loads(out_json.read_text())
    assert data["counts"]["side"]["envelope"] > 0
    assert "# SOW vs envelope diff" in capsys.readouterr().out


# ───────────────────────── capture ─────────────────────────


def _write_container(root: Path, deal: str, env: dict, sow: str) -> Path:
    latest = root / "deals" / deal / "orbitbrief" / "latest"
    latest.mkdir(parents=True)
    (latest / "envelope.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    (latest / "SOW_DRAFT.md").write_text(sow, encoding="utf-8")
    (latest / "compile-idempotency.json").write_text(json.dumps({"compile_id": env["compile_id"]}))
    return latest


def test_capture_pairs_envelope_and_sow_from_the_same_run(tmp_path: Path):
    env = build_synthetic_envelope()
    _write_container(tmp_path / "container", "deal_a", env, build_sow_markdown(env).strip())
    rec = capture.capture_deal(capture.DirSource(tmp_path / "container"), "deal_a", "single_site",
                               tmp_path / "out", deployed_at="2026-09-16T00:00:00Z")
    assert rec["errors"] == []
    p = rec["pairing"]
    assert p["sow_compile_id_matches"] and p["rerender_identical"] and p["fresh"] is True
    assert (tmp_path / "out" / "deal_a" / "envelope.json").is_file()
    assert (tmp_path / "out" / "deal_a" / "capture.json").is_file()
    f = rec["fields"]
    assert f["work_order_task_atoms"] == 1
    assert f["open_questions_without_atom"] == 1
    assert "deal_financials" in f["top_level"]["present"]
    assert "packets" in f["top_level"]["empty"]
    assert f["atom_field_coverage"]["review_status"] == f["atom_count"]


def test_capture_detects_stale_sow_and_stale_envelope(tmp_path: Path):
    old = build_synthetic_envelope()
    new = build_synthetic_envelope()
    new["compile_id"] = "cmp_newer"
    latest = _write_container(tmp_path / "c", "deal_b", new, build_sow_markdown(old))
    os.utime(latest / "SOW_DRAFT.md", (1_000_000_000, 1_000_000_000))
    rec = capture.capture_deal(capture.DirSource(tmp_path / "c"), "deal_b", "multi_site",
                               tmp_path / "out", deployed_at="2026-09-18T00:00:00Z")
    p = rec["pairing"]
    assert p["sow_compile_id"] == COMPILE_ID and not p["sow_compile_id_matches"]
    assert p["rerender_identical"] is False
    assert p["fresh"] is False
    assert p["sow_older_than_envelope"] is True


def test_capture_records_missing_blobs(tmp_path: Path):
    (tmp_path / "c").mkdir()
    rec = capture.capture_deal(capture.DirSource(tmp_path / "c"), "nope", "t_and_m", tmp_path / "out",
                               deployed_at=None)
    assert "envelope.json not found" in rec["errors"]


def test_capture_cli_refuses_to_write_inside_git_repo(tmp_path: Path, capsys):
    repo_dir = Path(__file__).resolve().parent
    if not capture._inside_git_worktree(repo_dir):
        pytest.skip("tests not running from a git checkout")
    rc = capture.main(["--source", "dir", "--root", str(tmp_path), "--deal", "x", "--out", str(repo_dir / "caps")])
    assert rc == 2
    assert not (repo_dir / "caps").exists()


def test_capture_cli_end_to_end(tmp_path: Path):
    env = build_synthetic_envelope()
    _write_container(tmp_path / "c", "deal_c", env, build_sow_markdown(env))
    rc = capture.main(["--source", "dir", "--root", str(tmp_path / "c"), "--deal", "deal_c:single_site",
                       "--out", str(tmp_path / "out"), "--deployed-at", "2026-09-01T00:00:00Z"])
    assert rc == 0
