"""Run-level result types and canonical serialization.

Shared by the runner (engine core), rollup/explain (rules & reconciliation) and
the API. Frozen so no agent invents a different shape.

Determinism: `canonical_json` is the ONLY serializer used to compare runs. It
sorts keys and renders Decimal as a string, so two runs are "identical" iff their
canonical JSON is byte-identical. No clock, no randomness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from engine.rules.base import Finding, Result, RuleResult, Tier


def jsonable(obj: Any) -> Any:
    """Recursively convert engine objects to JSON-safe values. Decimal -> str
    (never float), dates -> ISO, enums -> value, dataclasses -> dict, tuples and
    sets -> lists (sets sorted)."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (set, frozenset)):
        return sorted(jsonable(v) for v in obj)
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    return obj


def canonical_json(obj: Any) -> str:
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass(frozen=True)
class Explanation:
    """Template-only prose (the documented no-model fallback, FR §7.4).
    Every figure is taken from the Finding record."""

    what_happened: str
    why_it_matters: str
    impact: str
    recommended_action: str


@dataclass(frozen=True)
class StatusRollup:
    """Four-layer worst-case rollup (design §6.1)."""

    label: str  # "8 open findings" | "No open findings" | "Incomplete data"
    label_kind: str  # "open_findings" | "no_open_findings" | "incomplete_data"
    open_findings: int
    high: int
    medium: int
    low: int
    total_exposure_usd: Decimal  # M13, de-duplicated
    coverage_evaluated: int
    coverage_applicable: int
    coverage_ratio: Decimal
    coverage_floor: Decimal
    rule_results: dict[str, Result]  # rule_id -> Result (layer 2)
    requirement_results: dict[str, Result]  # authority -> worst mapped rule result (layer 3)
    not_evaluated: dict[str, str]  # rule_id -> plain-language reason
    domain: str = ""  # "" for the overall rollup; otherwise a Domain value


@dataclass
class RunOutput:
    run_id: str
    period: str
    tier: Tier
    manifest: dict[str, Any]
    rule_results: list[RuleResult]
    findings: list[Finding]  # deterministic order: severity, rule order, exposure desc, fingerprint
    total_exposure_usd: Decimal
    rollup: StatusRollup
    logged_below_materiality: list[dict[str, Any]] = field(default_factory=list)
    # Per enabled domain, plus `rollup` above as the overall across enabled domains. Disabled domains are
    # absent: their rules are not run and are not "Not evaluated".
    domain_rollups: dict[str, StatusRollup] = field(default_factory=dict)

    def canonical(self) -> str:
        """What replay compares. Excludes run_id and any executed_at so the same
        inputs+rules+config always yield the same string."""
        return canonical_json(
            {
                "period": self.period,
                "tier": self.tier,
                "findings": self.findings,
                "rule_results": [
                    {"rule_id": r.rule_id, "result": r.result, "metrics": r.metrics}
                    for r in self.rule_results
                ],
                "total_exposure_usd": self.total_exposure_usd,
                "rollup": self.rollup,
                "domain_rollups": self.domain_rollups,
            }
        )


class RunBlocked(Exception):
    """A hard validation failure that blocks the run (FR §2.4): unmapped charge
    code, rejected config, missing required file, hash mismatch on replay."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
