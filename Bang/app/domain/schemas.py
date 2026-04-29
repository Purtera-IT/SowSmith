from __future__ import annotations

from pydantic import BaseModel, Field


class DomainEntityType(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class DomainPack(BaseModel):
    pack_id: str
    name: str
    version: str
    service_lines: list[str] = Field(default_factory=list)
    entity_types: list[DomainEntityType] = Field(default_factory=list)
    device_aliases: dict[str, list[str]] = Field(default_factory=dict)
    site_alias_patterns: list[str] = Field(default_factory=list)
    action_aliases: dict[str, list[str]] = Field(default_factory=dict)
    constraint_patterns: dict[str, list[str]] = Field(default_factory=dict)
    exclusion_patterns: list[str] = Field(default_factory=list)
    customer_instruction_patterns: list[str] = Field(default_factory=list)
    quantity_units: dict[str, list[str]] = Field(default_factory=dict)
    artifact_role_patterns: dict[str, list[str]] = Field(default_factory=dict)
    risk_defaults: dict[str, float] = Field(default_factory=dict)
    packet_family_hints: dict[str, list[str]] = Field(default_factory=dict)
