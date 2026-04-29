from __future__ import annotations

from pathlib import Path

import yaml

from app.domain.schemas import DomainPack

DOMAIN_DIR = Path(__file__).resolve().parent
DEFAULT_PACK_ID = "default_pack"


def _candidate_pack_path(pack_id_or_path: str | Path) -> Path:
    candidate = Path(pack_id_or_path)
    if candidate.exists():
        return candidate
    pack_id = str(pack_id_or_path).strip()
    if not pack_id:
        return DOMAIN_DIR / "default_pack.yaml"
    return DOMAIN_DIR / f"{pack_id}.yaml"


def load_domain_pack(pack_id_or_path: str | Path | None = None) -> DomainPack:
    if pack_id_or_path is None:
        target = DOMAIN_DIR / "default_pack.yaml"
    else:
        target = _candidate_pack_path(pack_id_or_path)
        if not target.exists():
            target = DOMAIN_DIR / "default_pack.yaml"
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid domain pack YAML at '{target}': {exc}") from None
    except FileNotFoundError:
        raise ValueError(f"Domain pack file not found: {target}") from None
    if not isinstance(payload, dict):
        raise ValueError(f"Domain pack must be a mapping object: {target}")
    try:
        return DomainPack.model_validate(payload)
    except Exception as exc:
        raise ValueError(f"Invalid domain pack schema in '{target}': {exc}") from None
