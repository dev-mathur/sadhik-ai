"""Request bodies (pydantic v2). Response keys follow contracts/sample_payloads/*.json;
responses are plain JSON dicts built by api/service.py, so no float can appear in a figure."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, field_validator


class DispositionBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    disposition: str
    reason_code: str | None = None
    note: str | None = None
    actor: str  # required free text; there is no auth in the MVP

    @field_validator("actor")
    @classmethod
    def _actor_present(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("actor is required")
        return v.strip()


class ConfigBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    yaml: str


class RunBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    period: str
    tier: str
