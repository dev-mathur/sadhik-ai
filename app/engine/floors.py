"""Floor registry loader (design 5.2, 5.3).

The registry is Sadhik-owned (config/floors.yaml). Customers cannot express
anything in it. Each parameter carries a `direction` so every comparison in the
engine is a STRICTNESS comparison, never a raw-magnitude comparison.

Vocabulary used throughout the engine:
  floor  the loosest value a customer may hold (a value looser than this is refused).
  min/max the range Sadhik allows a customer to tune within.
  A value is "looser than" another when, by `direction`, it tolerates more.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from engine.rules.base import Direction

GLOBAL = "global"
REGULATORY_BASES = frozenset({"regulatory", "contract"})


def to_decimal(value: Any) -> Decimal:
    """Exact Decimal from a YAML/JSON scalar. Floats go through repr, never binary."""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    return Decimal(str(value))


def is_looser(direction: Direction, a: Decimal, b: Decimal) -> bool:
    """True when `a` tolerates strictly more than `b` under `direction`."""
    if direction == Direction.LOWER_IS_STRICTER:
        return a > b
    return a < b


def strictest(direction: Direction, *values: Decimal) -> Decimal:
    """The strictest of `values` under `direction`."""
    best = values[0]
    for v in values[1:]:
        if is_looser(direction, best, v):
            best = v
    return best


@dataclass(frozen=True)
class FloorParam:
    rule_id: str
    name: str
    direction: Direction
    floor: Decimal | None
    default: Decimal
    min: Decimal | None
    max: Decimal | None
    basis: str | None
    citation: str
    note: str = ""

    def looser_than_floor(self, value: Decimal) -> bool:
        return self.floor is not None and is_looser(self.direction, value, self.floor)

    def violation(self, value: Decimal) -> tuple[str, str] | None:
        """(code, message) if `value` is refused for this parameter, else None.
        Never adjusts the value: the caller reports it and moves on."""
        n = self.name
        if self.floor is not None and is_looser(self.direction, value, self.floor):
            return (
                "looser_than_floor",
                f"{n} {fmt(value)} is looser than the floor {fmt(self.floor)}; "
                "floors cannot be loosened",
            )
        if self.max is not None and value > self.max:
            return ("exceeds_max", f"{n} {fmt(value)} exceeds the maximum allowed value {fmt(self.max)}")
        if self.min is not None and value < self.min:
            return ("below_min", f"{n} {fmt(value)} is below the minimum allowed value {fmt(self.min)}")
        return None


def fmt(d: Decimal) -> str:
    """Plain decimal text (no exponent), trailing zeros trimmed."""
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


@dataclass(frozen=True)
class FloorRegistry:
    version: str
    review_status: str
    rules: dict[str, dict[str, FloorParam]] = field(default_factory=dict)
    globals: dict[str, FloorParam] = field(default_factory=dict)

    def param(self, rule_id: str, name: str) -> FloorParam:
        """KeyError if the parameter is not in the registry. `param("global", name)`
        returns a global parameter."""
        if rule_id == GLOBAL:
            return self.globals[name]
        return self.rules[rule_id][name]

    def has_param(self, rule_id: str, name: str) -> bool:
        if rule_id == GLOBAL:
            return name in self.globals
        return name in self.rules.get(rule_id, {})

    def global_param(self, name: str) -> FloorParam:
        return self.globals[name]

    def rule_params(self, rule_id: str) -> dict[str, FloorParam]:
        return dict(self.rules.get(rule_id, {}))

    def is_regulatory(self, rule_id: str) -> bool:
        """True when any of the rule's parameters is regulatory- or contract-based:
        such a rule cannot be switched off (design 5.3)."""
        return any(p.basis in REGULATORY_BASES for p in self.rules.get(rule_id, {}).values())


def _parse_param(rule_id: str, name: str, body: dict[str, Any]) -> FloorParam:
    if not isinstance(body, dict):
        raise ValueError(f"floors: {rule_id}.{name} must be a mapping")
    try:
        direction = Direction(body["direction"])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"floors: {rule_id}.{name} has a missing or invalid direction") from exc
    if "default" not in body:
        raise ValueError(f"floors: {rule_id}.{name} has no default")

    def opt(key: str) -> Decimal | None:
        v = body.get(key)
        return None if v is None else to_decimal(v)

    return FloorParam(
        rule_id=rule_id,
        name=name,
        direction=direction,
        floor=opt("floor"),
        default=to_decimal(body["default"]),
        min=opt("min"),
        max=opt("max"),
        basis=body.get("basis"),
        citation=str(body.get("citation", "")),
        note=str(body.get("note", "")),
    )


def load_floors(path: Path) -> FloorRegistry:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("floors: file must be a mapping")
    rules: dict[str, dict[str, FloorParam]] = {}
    for rule_id, params in (doc.get("rules") or {}).items():
        rules[str(rule_id)] = {
            str(name): _parse_param(str(rule_id), str(name), body) for name, body in (params or {}).items()
        }
    globals_ = {
        str(name): _parse_param(GLOBAL, str(name), body) for name, body in (doc.get("global") or {}).items()
    }
    return FloorRegistry(
        version=str(doc.get("floor_registry_version", "")),
        review_status=str(doc.get("review_status", "unreviewed")),
        rules=rules,
        globals=globals_,
    )
