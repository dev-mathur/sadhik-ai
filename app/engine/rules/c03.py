"""C-03 Billing rate above a contractual ceiling (M17). Domain: DCAA cost accounting.

A contract can cap an indirect rate. The rate file's optional `Ceiling Rate` records that cap per pool. For each pool
whose provisional agreement covering the last day of the period carries a ceiling:

    excess = provisional_rate - ceiling_rate
    excess > `ceiling_excess_tolerance` (floor 0)  -> Exception
    exposure = money(excess x base), the amount billed above the cap on the pool's rate base

The ACTUAL rate is context only: an actual rate above the ceiling is also shown, but the exposure is what was
BILLED above the cap, at the provisional rate. Billing above a contractual cap is an integrity failure (High).
No rate data, no GL, or no ceiling on any pool is Not evaluated: with nothing to compare against, never a pass.
A pool with no covering agreement, no ceiling, or a zero base is skipped and named in `skipped_pools`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from engine.canonical import RateAgreement, WorkingView
from engine.metrics import fmt_pct, period_bounds, rate_inputs
from engine.rules.base import (
    Direction,
    Domain,
    EvidenceRef,
    Finding,
    MetricResult,
    ParamSpec,
    ResolvedParams,
    Result,
    RuleResult,
    RuleSpec,
    money,
    not_evaluated,
    ratio,
    register,
)
from engine.severity import classify_with_reason

RULE_ID = "C-03"
VERSION = "1.0.0"
REQUIRED = ("gl", "rate_data")
AUTHORITIES = ("Contract rate ceiling clause (to verify)", "FAR 42.707 (to verify)")
BASIS = "contract"
POOL_LABELS = {"fringe": "fringe", "overhead": "overhead", "ga": "G&A"}

EXPLANATION_TEMPLATE = (
    "For {period_label}, the {pool_label} pool is billed at a provisional rate of {provisional_rate_pct}, which is "
    "above the contractual ceiling rate of {ceiling_rate_pct} recorded in the rate data. {actual_sentence}"
    "\n\n"
    "A contract that caps an indirect rate expects billings at or below the cap under the cited terms ({authorities}). "
    "Billing at a rate above it is inconsistent with that term, and the difference is exposed to repayment."
    "\n\n"
    "{excess_pct} above the ceiling on a base of {base_amount} · {exposure} billed above the cap · "
    "{pool_label} pool"
)
RECOMMENDED_ACTION = (
    "Confirm the ceiling rate in the contract, and bring the provisional billing rate down to at or below it. "
    "Review invoices already billed at the higher rate and adjust them."
)


def _agreement(view: WorkingView, pool: str, on: date) -> RateAgreement | None:
    cands = [a for a in view.rate_agreements if a.pool == pool and a.effective_from <= on <= a.effective_to]
    return max(cands, key=lambda a: (a.effective_from, a.effective_to, a.source_document)) if cands else None


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    if "rate_data" not in view.sources_present:
        return not_evaluated(RULE_ID, "Provisional billing rate data was not uploaded")
    if "gl" not in view.sources_present:
        return not_evaluated(RULE_ID, "Required source not present: gl")
    if not any(a.ceiling_rate is not None for a in view.rate_agreements):
        return not_evaluated(RULE_ID, "No contractual ceiling rate is recorded in the rate data")
    tolerance = p["ceiling_excess_tolerance"]
    materiality = p["materiality_usd"]
    _, period_end = period_bounds(view.period)
    inputs = rate_inputs(view, view.period)

    findings: list[Finding] = []
    metrics: list[MetricResult] = []
    skipped: list[dict[str, str]] = []
    usable: list[tuple[str, RateAgreement, Decimal]] = []
    # Decide every skip BEFORE building any finding, so each finding's record lists all of them and not
    # only the pools that happen to sort ahead of it.
    for pool in sorted({a.pool for a in view.rate_agreements}):
        agr = _agreement(view, pool, period_end)
        if agr is None or agr.ceiling_rate is None:
            skipped.append({"pool": pool, "reason": "no ceiling rate applies for this period"})
            continue
        base = inputs.base(agr.base_definition)
        if base is None or base <= 0:
            skipped.append({"pool": pool, "reason": "the rate base is zero or unknown for this period"})
            continue
        usable.append((pool, agr, base))
    checked = len(usable)
    for pool, agr, base in usable:
        label = POOL_LABELS.get(pool, pool)
        prov, ceiling = agr.provisional_rate, agr.ceiling_rate
        excess = prov - ceiling
        actual = ratio(inputs.pool(pool), base)
        breach = excess > tolerance
        metrics.append(
            MetricResult(
                metric_id="M17",
                label=f"{label} provisional rate above its ceiling",
                value=ratio(max(excess, Decimal(0)), 1),
                numerator=prov,
                denominator=ceiling,
                result=Result.EXCEPTION if breach else Result.CONSISTENT,
                exception_threshold=f"provisional rate above the ceiling by more than {fmt_pct(tolerance, 2)}",
            )
        )
        if not breach:
            continue
        exposure = money(excess * base)
        sev, reason = classify_with_reason(
            Result.EXCEPTION, exposure=exposure, materiality=materiality, employees=0,
            integrity_failure=True, integrity_detail="provisional_rate_above_contract_ceiling",
        )
        actual_above = actual > ceiling
        findings.append(
            Finding(
                rule_id=RULE_ID,
                rule_version=VERSION,
                period=view.period,
                authorities=AUTHORITIES,
                basis=BASIS,
                severity=sev,
                severity_reason=reason,
                headline=(f"The {label} provisional billing rate ({fmt_pct(prov, 2)}) is above its contractual "
                          f"ceiling ({fmt_pct(ceiling, 2)})"),
                metric_id="M17",
                metric_value=ratio(excess, 1),
                metric_numerator=prov,
                metric_denominator=ceiling,
                threshold_tripped="provisional_rate_above_ceiling",
                computed={
                    "kind": "primary",
                    "pool": pool,
                    "pool_label": label,
                    "base_definition": agr.base_definition,
                    "base_amount": money(base),
                    "provisional_rate": prov,
                    "ceiling_rate": ceiling,
                    "excess": excess,
                    "actual_rate": actual,
                    "actual_above_ceiling": actual_above,
                    "actual_sentence": (
                        f"The actual rate for the period is {fmt_pct(actual, 2)}, which is also above the ceiling."
                        if actual_above
                        else f"The actual rate for the period is {fmt_pct(actual, 2)}, at or below the ceiling."
                    ),
                    "exposure_usd": exposure,
                    "exposure_basis": "rate_above_ceiling",
                    "skipped_pools": [dict(x) for x in skipped],
                },
                exposure_usd=exposure,
                exposure_entry_ids=(),
                employees=(),
                contracts=(),
                evidence=(
                    EvidenceRef(
                        kind="rate_agreement",
                        ref_id=f"{pool}/{agr.effective_from.isoformat()}",
                        source_file=agr.lineage.source_file,
                        sha256=agr.lineage.sha256,
                        row=agr.lineage.row,
                        detail={
                            "pool": pool,
                            "base_definition": agr.base_definition,
                            "provisional_rate": str(prov),
                            "ceiling_rate": str(ceiling),
                            "effective_from": agr.effective_from.isoformat(),
                            "effective_to": agr.effective_to.isoformat(),
                            "source_document": agr.source_document,
                        },
                    ),
                ),
                fingerprint=f"{RULE_ID}|{pool}|{view.period}",
            )
        )

    if checked == 0:
        return not_evaluated(RULE_ID, "No pool had a ceiling rate and a usable base for this period")
    result = Result.EXCEPTION if findings else Result.CONSISTENT
    return RuleResult(rule_id=RULE_ID, result=result, metrics=tuple(metrics), findings=tuple(findings))


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Billing rate above a contractual ceiling",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "ceiling_excess_tolerance": ParamSpec(
                "ceiling_excess_tolerance", Decimal("0"), Direction.LOWER_IS_STRICTER, None, None, "rate fraction",
                "How far the provisional billing rate may sit above a contractual ceiling before the check trips. "
                "Zero tolerance."),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        domain=Domain.DCAA_COST_ACCOUNTING.value,
        review_status="unreviewed",
    )
)
