"""Severity classification per FR 6 (design 15.8).

High   = an Exception with exposure >= materiality, OR systemic (>= 3 periods or
         >= 5 employees), OR an integrity failure.
Medium = an Exception below those tests, OR a Watch with a rising trend
         (or a Watch with no trend history at all: absence of evidence that it is
         stable is not treated as stable).
Low    = a stable or improving Watch.

Systemic and exposure tests apply to Exceptions only. A Watch is never High.
"""

from __future__ import annotations

from decimal import Decimal

from engine.rules.base import Result, Severity

SYSTEMIC_EMPLOYEES = 5
SYSTEMIC_PERIODS = 3


def classify_with_reason(
    result: Result,
    *,
    exposure: Decimal,
    materiality: Decimal,
    employees: int,
    periods: int = 1,
    integrity_failure: bool = False,
    trend_rising: bool = False,
    no_trend_history: bool = False,
    integrity_detail: str = "",
    systemic_periods: int = SYSTEMIC_PERIODS,
) -> tuple[Severity, str]:
    """Return (severity, severity_reason). `no_trend_history`, `integrity_detail` and
    `systemic_periods` are additive keyword-only extras to the ENGINE_API signature.
    `systemic_periods` lets a rule with a customer-tunable systemic threshold (L-11) pass it;
    the default is the FR 6 constant, so every existing caller is unchanged."""
    if integrity_failure:
        return Severity.HIGH, "integrity_failure" + (f":{integrity_detail}" if integrity_detail else "")

    if result == Result.EXCEPTION:
        reasons: list[str] = []
        if exposure >= materiality:
            reasons.append("exposure_at_or_above_materiality")
        if employees >= SYSTEMIC_EMPLOYEES:
            reasons.append(f"systemic:{employees}_employees")
        if periods >= systemic_periods:
            reasons.append(f"systemic:{periods}_periods")
        if reasons:
            return Severity.HIGH, ";".join(reasons)
        return Severity.MEDIUM, "exception_below_materiality"

    if result == Result.WATCH:
        if trend_rising:
            return Severity.MEDIUM, "watch_rising_trend"
        if no_trend_history:
            return Severity.MEDIUM, "watch_no_trend_history"
        return Severity.LOW, "watch_stable_or_improving"

    raise ValueError(f"cannot classify severity for result {result!r}: only Watch and Exception raise findings")


def classify(
    result: Result,
    *,
    exposure: Decimal,
    materiality: Decimal,
    employees: int,
    periods: int = 1,
    integrity_failure: bool = False,
    trend_rising: bool = False,
    no_trend_history: bool = False,
) -> Severity:
    return classify_with_reason(
        result,
        exposure=exposure,
        materiality=materiality,
        employees=employees,
        periods=periods,
        integrity_failure=integrity_failure,
        trend_rising=trend_rising,
        no_trend_history=no_trend_history,
    )[0]
