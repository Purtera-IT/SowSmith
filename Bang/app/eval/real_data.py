from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from app.core.compiler import compile_project
from app.core.validators import validate_compile_result

REDACTION_STATUS = Literal["synthetic", "redacted", "production_unredacted_do_not_share"]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def case_dir(root_dir: Path, case_id: str) -> Path:
    return root_dir / case_id


def init_case(
    root_dir: Path,
    case_id: str,
    *,
    notes: str = "",
    expected_risks: list[str] | None = None,
    redaction_status: REDACTION_STATUS = "redacted",
    allowed_for_tests: bool = False,
) -> Path:
    cdir = case_dir(root_dir, case_id)
    artifacts = cdir / "artifacts"
    labels = cdir / "labels"
    outputs = cdir / "outputs"
    artifacts.mkdir(parents=True, exist_ok=True)
    labels.mkdir(parents=True, exist_ok=True)
    outputs.mkdir(parents=True, exist_ok=True)

    manifest = {
        "case_id": case_id,
        "created_at": _now_iso(),
        "artifact_count": len([p for p in artifacts.rglob("*") if p.is_file()]),
        "notes": notes,
        "expected_risks": expected_risks or [],
        "redaction_status": redaction_status,
        "allowed_for_tests": allowed_for_tests,
    }
    (cdir / "case_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return cdir


def compile_case(root_dir: Path, case_id: str) -> dict[str, Any]:
    cdir = case_dir(root_dir, case_id)
    artifacts = cdir / "artifacts"
    outputs = cdir / "outputs"
    outputs.mkdir(parents=True, exist_ok=True)
    result = compile_project(
        project_dir=artifacts,
        project_id=case_id,
        allow_errors=True,
        allow_unverified_receipts=True,
    )

    compile_result_path = outputs / "compile_result.json"
    compile_result_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

    packet_families = sorted({packet.family.value for packet in result.packets})
    severity_distribution = dict(
        sorted(Counter(packet.risk.severity for packet in result.packets if packet.risk is not None).items())
    )
    hard_errors = sorted([warning for warning in result.warnings if str(warning).startswith("ERROR:")])
    warnings = sorted([warning for warning in result.warnings if str(warning).startswith("WARNING:")])
    receipt_counts = Counter(
        receipt.replay_status for atom in result.atoms for receipt in atom.receipts
    )
    validation = validate_compile_result(result, source_files_available=False)
    invalid_governance_count = len(
        [
            message
            for message in validation
            if message.startswith("ERROR:")
            and ("govern" in message.lower() or "governing" in message.lower())
        ]
    )
    compile_duration_ms = result.trace.total_duration_ms if result.trace is not None else 0.0
    summary = {
        "atom_count": len(result.atoms),
        "packet_count": len(result.packets),
        "packet_families": packet_families,
        "severity_distribution": severity_distribution,
        "hard_errors": hard_errors,
        "warnings": warnings,
        "receipt_verification_counts": dict(sorted(receipt_counts.items())),
        "invalid_governance_count": invalid_governance_count,
        "compile_duration_ms": round(float(compile_duration_ms), 3),
    }
    (outputs / "benchmark_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary


def write_packet_label_skeleton(root_dir: Path, case_id: str) -> Path:
    cdir = case_dir(root_dir, case_id)
    compile_result_path = cdir / "outputs" / "compile_result.json"
    if not compile_result_path.exists():
        raise FileNotFoundError(f"Missing compile_result.json for case '{case_id}'")
    payload = json.loads(compile_result_path.read_text(encoding="utf-8"))
    packets = payload.get("packets", [])
    skeleton = {
        "case_id": case_id,
        "created_at": _now_iso(),
        "packet_labels": [
            {
                "packet_id": packet.get("id"),
                "family": packet.get("family"),
                "anchor_key": packet.get("anchor_key"),
                "predicted_status": packet.get("status"),
                "human_label": {
                    "correct_packet": None,
                    "correct_governing_atom": None,
                    "severity_correct": None,
                    "notes": "",
                },
            }
            for packet in packets
        ],
    }
    labels_dir = cdir / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    target = labels_dir / "packet_labels.json"
    target.write_text(json.dumps(skeleton, indent=2), encoding="utf-8")
    return target


def summarize_cases(root_dir: Path) -> dict[str, Any]:
    case_dirs = [p for p in sorted(root_dir.iterdir(), key=lambda path: path.name) if p.is_dir()]
    correct_packet_yes = 0
    correct_packet_total = 0
    governing_yes = 0
    governing_total = 0
    severity_yes = 0
    severity_total = 0
    failure_modes: Counter[str] = Counter()
    labeled_cases = 0

    for cdir in case_dirs:
        labels_path = cdir / "labels" / "packet_labels.json"
        if not labels_path.exists():
            continue
        labeled_cases += 1
        data = json.loads(labels_path.read_text(encoding="utf-8"))
        for row in data.get("packet_labels", []):
            label = row.get("human_label", {})
            cp = label.get("correct_packet")
            cg = label.get("correct_governing_atom")
            sc = label.get("severity_correct")
            note = str(label.get("notes", "")).strip()

            if cp is not None:
                correct_packet_total += 1
                if cp is True:
                    correct_packet_yes += 1
                else:
                    failure_modes["incorrect_packet"] += 1
            if cg is not None:
                governing_total += 1
                if cg is True:
                    governing_yes += 1
                else:
                    failure_modes["incorrect_governing_atom"] += 1
            if sc is not None:
                severity_total += 1
                if sc is True:
                    severity_yes += 1
                else:
                    failure_modes["incorrect_severity"] += 1
            if note:
                failure_modes[f"note:{note}"] += 1

    def _ratio(num: int, den: int) -> float | None:
        if den == 0:
            return None
        return round(num / den, 4)

    return {
        "case_count": len(case_dirs),
        "labeled_case_count": labeled_cases,
        "packet_precision_estimate": _ratio(correct_packet_yes, correct_packet_total),
        "governing_accuracy_estimate": _ratio(governing_yes, governing_total),
        "severity_accuracy_estimate": _ratio(severity_yes, severity_total),
        "common_failure_modes": [
            {"mode": mode, "count": count}
            for mode, count in failure_modes.most_common(10)
        ],
    }
