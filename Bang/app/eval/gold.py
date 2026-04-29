from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class GoldExpectedPacket(BaseModel):
    family: str
    anchor_key_contains: str
    must_contain_quantities: list[float] = Field(default_factory=list)
    expected_status: str | None = None
    forbidden_governing_authority: list[str] = Field(default_factory=list)


class GoldExpectedGoverning(BaseModel):
    family: str
    anchor_key_contains: str
    governing_authority: str


class GoldForbiddenCondition(BaseModel):
    condition: str


class GoldScenario(BaseModel):
    scenario_id: str
    project_dir: str | None = None
    expected_packets: list[GoldExpectedPacket] = Field(default_factory=list)
    expected_governing: list[GoldExpectedGoverning] = Field(default_factory=list)
    forbidden: list[GoldForbiddenCondition] = Field(default_factory=list)


def load_gold(path: Path) -> GoldScenario:
    return GoldScenario.model_validate(json.loads(path.read_text(encoding="utf-8")))
