"""Shared test helpers (engine core)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from engine.floors import FloorRegistry, load_floors
from engine.rules.base import ParamSpec, Result, RuleResult, RuleSpec

ROOT = Path(__file__).resolve().parents[1]
FLOORS = ROOT / "config" / "floors.yaml"
SHIPPED_CONFIG = ROOT / "config" / "meridian-rules.yaml"
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.fixture(scope="session")
def registry() -> FloorRegistry:
    return load_floors(FLOORS)


def make_stub_spec(registry: FloorRegistry, rule_id: str, required=("timekeeping",), evaluate=None) -> RuleSpec:
    """A RuleSpec whose parameters mirror floors.yaml, with a do-nothing evaluator."""
    params = {
        name: ParamSpec(name, fp.default, fp.direction, fp.min, fp.max)
        for name, fp in registry.rule_params(rule_id).items()
    }
    basis = next((fp.basis for fp in registry.rule_params(rule_id).values()), "audit_practice")

    def _eval(view, p):
        return RuleResult(rule_id=rule_id, result=Result.CONSISTENT)

    return RuleSpec(
        id=rule_id,
        version="1.0.0",
        title=f"stub {rule_id}",
        authorities=("stub",),
        basis=basis or "audit_practice",
        required_sources=tuple(required),
        parameters=params,
        evaluate=evaluate or _eval,
    )


@pytest.fixture(scope="session")
def stub_rules(registry) -> list[RuleSpec]:
    sources = {
        "L-01": ("timekeeping", "hris", "contracts"),
        "L-02": ("timekeeping", "payroll"),
        "L-03": ("payroll", "gl"),
        "L-05": ("timekeeping",),
        "L-06": ("time_edits",),
        "L-09": ("timekeeping", "contracts"),
        "L-11": ("rate_data",),
    }
    return [make_stub_spec(registry, rid, req) for rid, req in sources.items()]


RATE_DATA_ENTRY = {"file": "meridian_rates_2026-08.csv", "data_as_of": "2026-08-31"}


def make_dcaa_data(dst: Path) -> Path:
    """tests/fixtures/mini plus the DCAA additions: the rate file (declared as `rate_data` in sources.json)
    and the two optional reference tables. `mini` itself has none of them and must keep working."""
    shutil.copytree(FIXTURES / "mini", dst)
    for f in (FIXTURES / "dcaa_overlay").iterdir():
        shutil.copy(f, dst / f.name)
    idx = json.loads((dst / "sources.json").read_text())
    idx["sources"]["rate_data"] = dict(RATE_DATA_ENTRY)
    idx["absent"] = [a for a in idx["absent"] if a != "rate_data"]
    (dst / "sources.json").write_text(json.dumps(idx))
    return dst


@pytest.fixture()
def dcaa_data(tmp_path) -> Path:
    return make_dcaa_data(tmp_path / "dcaa_data")
