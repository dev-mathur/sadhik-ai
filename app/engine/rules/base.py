"""Rule contract: RuleSpec, Finding, MetricResult, registry.

This is the frozen interface between the engine core (config, runner, manifest)
and the rule implementations. See docs/hld/system-design.md §5.1 for the rule
anatomy and §15.9 for the Finding record shape.

Invariants enforced by this module's shape:
  - A rule is a PURE function of (WorkingView, ResolvedParams) -> RuleResult.
    No I/O, no clock, no randomness, no LLM.
  - Rules never see each other's output (no chaining, §5.4).
  - A rule whose required source is absent returns NOT_EVALUATED, never PASS.
  - All money and hours are Decimal. Never float.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, Callable

from engine.canonical import WorkingView

# --------------------------------------------------------------------------- #
# Fixed-point helpers. Documented rounding: half-up, to the cent.
# F-3316 is 102.75 * 9.5 = 976.125 -> 976.13. That is deliberate.
# --------------------------------------------------------------------------- #
CENT = Decimal("0.01")


def money(x: Decimal | int | str) -> Decimal:
    """Round to cents, half-up. The one place money is rounded."""
    return Decimal(x).quantize(CENT, rounding=ROUND_HALF_UP)


def ratio(num: Decimal | int, den: Decimal | int, places: str = "0.0001") -> Decimal:
    """Deterministic ratio, half-up. Returns 0 on a zero denominator."""
    den = Decimal(den)
    if den == 0:
        return Decimal("0").quantize(Decimal(places))
    return (Decimal(num) / den).quantize(Decimal(places), rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------- #
# Results and severity
# --------------------------------------------------------------------------- #
class Result(str, Enum):
    """Metric/rule result. Ordered worst-last by SEVERITY_ORDER below."""

    CONSISTENT = "Consistent"
    WATCH = "Watch"
    EXCEPTION = "Exception"
    NOT_EVALUATED = "Not evaluated"
    NOT_APPLICABLE = "Not applicable"


# Worst-case rollup order (§6.1). NOT_EVALUATED is not "worse" than an
# Exception, but it must never be reported as Consistent; rollup handles it
# separately via coverage. See engine/rollup.py.
RESULT_RANK = {
    Result.NOT_APPLICABLE: -1,
    Result.NOT_EVALUATED: 0,
    Result.CONSISTENT: 1,
    Result.WATCH: 2,
    Result.EXCEPTION: 3,
}


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Status(str, Enum):
    """Finding lifecycle (§8.3)."""

    OPEN = "open"
    IN_REVIEW = "in_review"
    CONFIRMED = "confirmed"
    LEGIT_EXCEPTION = "legit_exception"
    DATA_ERROR = "data_error"
    REMEDIATED = "remediated"
    CLOSED = "closed"


class Domain(str, Enum):
    """Compliance domains (design section 8). A domain is enabled per customer in the config file."""

    LABOR = "labor"
    DCAA_COST_ACCOUNTING = "dcaa_cost_accounting"


DOMAIN_NAMES: dict[str, str] = {
    Domain.LABOR.value: "Labor",
    Domain.DCAA_COST_ACCOUNTING.value: "DCAA cost accounting",
}
DEFAULT_ENABLED_DOMAINS: tuple[str, ...] = (Domain.LABOR.value,)


class Tier(str, Enum):
    """Run cadence tiers (§7.1). A rule's tier is the earliest tier at which all
    of its required sources are available."""

    FAST = "fast"
    PAY_PERIOD = "pay_period"
    CLOSE = "close"


TIER_ORDER = {Tier.FAST: 0, Tier.PAY_PERIOD: 1, Tier.CLOSE: 2}

# Sources each tier makes available (cumulative). Contracts are slow-changing and
# already confirmed, so they are present from the fast tier.
TIER_SOURCES: dict[Tier, frozenset[str]] = {
    Tier.FAST: frozenset({"timekeeping", "time_edits", "contracts"}),
    Tier.PAY_PERIOD: frozenset({"timekeeping", "time_edits", "contracts", "payroll", "hris"}),
    Tier.CLOSE: frozenset(
        {"timekeeping", "time_edits", "contracts", "payroll", "hris", "gl", "rate_data"}
    ),
}


# --------------------------------------------------------------------------- #
# Parameters
# --------------------------------------------------------------------------- #
class Direction(str, Enum):
    """Which way is stricter. Validation compares strictness, not magnitude."""

    LOWER_IS_STRICTER = "lower_is_stricter"
    HIGHER_IS_STRICTER = "higher_is_stricter"


@dataclass(frozen=True)
class ParamSpec:
    """A tunable parameter as declared on a rule (§5.1)."""

    name: str
    default: Decimal
    direction: Direction
    min: Decimal | None = None  # allowed range, customer-settable
    max: Decimal | None = None
    unit: str = ""
    description: str = ""


# ResolvedParams is a flat, immutable mapping of parameter name -> Decimal,
# already run through floor -> default -> customer resolution by the engine core.
# Rules receive ONLY this; they never read the config file or the floor registry.
ResolvedParams = dict[str, Decimal]


# --------------------------------------------------------------------------- #
# Findings (shape mirrors docs/hld/system-design.md §15.9)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EvidenceRef:
    """One source record backing a finding. Every field is drillable."""

    kind: str  # "time_entry" | "pay_record" | "gl_line" | "contract_term" | "crosswalk" | "edit" | "employee"
    ref_id: str  # entry_id, employee_id, etc.
    source_file: str
    sha256: str
    row: int | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    rule_id: str
    rule_version: str
    period: str
    authorities: tuple[str, ...]
    basis: str
    severity: Severity
    severity_reason: str
    headline: str
    metric_id: str
    metric_value: Decimal
    metric_numerator: Decimal | None
    metric_denominator: Decimal | None
    threshold_tripped: str
    # Everything the evaluator computed. Nothing here is written by a model.
    computed: dict[str, Any]
    exposure_usd: Decimal
    # For M13 de-duplication: entry_ids whose dollars this finding counts.
    # Two findings sharing an entry_id count its dollars once in total exposure.
    exposure_entry_ids: tuple[str, ...]
    employees: tuple[str, ...]
    contracts: tuple[str, ...]
    evidence: tuple[EvidenceRef, ...]
    # Stable identity of the underlying condition, independent of run. Used for
    # compliance memory and to reopen a closed finding when a pattern recurs.
    fingerprint: str
    # Materiality: below-floor findings are logged but not raised.
    below_materiality: bool = False


@dataclass(frozen=True)
class MetricResult:
    metric_id: str
    label: str
    value: Decimal | None
    numerator: Decimal | None
    denominator: Decimal | None
    result: Result
    watch_threshold: str = ""
    exception_threshold: str = ""
    note: str = ""


@dataclass(frozen=True)
class RuleResult:
    """What a rule returns. Findings plus the metrics that produced the verdict."""

    rule_id: str
    result: Result
    metrics: tuple[MetricResult, ...] = ()
    findings: tuple[Finding, ...] = ()
    not_evaluated_reason: str = ""
    # Below-materiality deviations: logged with lineage, no finding raised (§6.2).
    logged_below_materiality: tuple[dict[str, Any], ...] = ()


# --------------------------------------------------------------------------- #
# RuleSpec + registry
# --------------------------------------------------------------------------- #
Evaluator = Callable[[WorkingView, ResolvedParams], RuleResult]


@dataclass(frozen=True)
class RuleSpec:
    id: str
    version: str
    title: str
    authorities: tuple[str, ...]
    basis: str  # regulatory | contract | audit_practice | customer_policy
    required_sources: tuple[str, ...]
    parameters: dict[str, ParamSpec]
    evaluate: Evaluator
    domain: str = "labor"
    review_status: str = "unreviewed"  # no CPA has reviewed this citation yet
    explanation_template: str = ""
    recommended_action: str = ""
    # False for data-quality gates (DQ-*): they raise findings but are not "rules evaluated" in coverage.
    # Replaces the `id.startswith("L-")` proxy, which conflated "labor" with "counts toward coverage".
    counts_toward_coverage: bool = True

    @property
    def tier(self) -> Tier:
        """Earliest tier at which every required source is available."""
        for t in (Tier.FAST, Tier.PAY_PERIOD, Tier.CLOSE):
            if set(self.required_sources) <= TIER_SOURCES[t]:
                return t
        return Tier.CLOSE


_REGISTRY: dict[str, RuleSpec] = {}


def register(spec: RuleSpec) -> RuleSpec:
    """Register a rule. Called at import time by each rule module."""
    if spec.id in _REGISTRY:
        raise ValueError(f"duplicate rule id {spec.id}")
    _REGISTRY[spec.id] = spec
    return spec


def all_rules() -> list[RuleSpec]:
    """Every registered rule, in stable ID order. Order must never affect results."""
    _load_rule_modules()
    return sorted(_REGISTRY.values(), key=lambda r: r.id)


def get_rule(rule_id: str) -> RuleSpec:
    _load_rule_modules()
    return _REGISTRY[rule_id]


_LOADED = False


def _load_rule_modules() -> None:
    """Import every rule module so its register() call runs. Modules are named
    l*.py / dq*.py in this package; discovered rather than listed so adding a rule
    is a single new file."""
    global _LOADED
    if _LOADED:
        return
    import importlib
    import pkgutil

    import engine.rules as pkg

    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name == "base":
            continue
        importlib.import_module(f"engine.rules.{m.name}")
    _LOADED = True


def not_evaluated(rule_id: str, reason: str) -> RuleResult:
    """The only correct return for a rule missing a required source. Never PASS."""
    return RuleResult(rule_id=rule_id, result=Result.NOT_EVALUATED, not_evaluated_reason=reason)
