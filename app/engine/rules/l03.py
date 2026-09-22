"""L-03 Payroll gross pay vs. general-ledger labor (M2).

M2 = |sum(GL labor for the period) - sum(payroll gross pay for the period)| / payroll gross.
GL labor is every GL line when `view.account_categories` is empty (table not provided), and only lines in
`labor` accounts when it is provided (an account missing from a non-empty table counts as labor).
Consistent below the Watch threshold. A Watch or Exception raises one finding for
the period, priced at the dollar difference (not entry-based).
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import WorkingView
from engine.metrics import (
    HUNDRED,
    ZERO,
    baseline_history,
    fmt_money,
    fmt_pct,
    has_baseline,
    is_labor_line,
    is_rising,
)
from engine.rules.base import (
    Direction,
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

RULE_ID = "L-03"
VERSION = "1.0.0"
REQUIRED = ("payroll", "gl")
AUTHORITIES = ("FAR 31.201-2", "SF 1408 labor distribution")
BASIS = "audit_practice"
HISTORY_KEY = "M2.dollar_variance"

EXPLANATION_TEMPLATE = (
    "For {period_label}, payroll gross pay is {payroll_total} and general-ledger labor is {gl_total}, "
    "a difference of {variance} ({variance_pct} of payroll)."
    "\n\n"
    "Labor dollars in the general ledger are expected to reconcile to payroll. A difference above the "
    "configured threshold is inconsistent with the cost distribution expected under the cited requirements ({authorities}) and "
    "may mean a posting, accrual or timing error."
    "\n\n"
    "{variance} difference · {variance_pct} of payroll gross pay"
)
RECOMMENDED_ACTION = (
    "Identify the general-ledger lines or payroll records that account for the difference. Post the "
    "correcting entry, or document the timing item that explains it."
)


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    watch_pct = p["dollar_variance_watch_pct"]
    exc_pct = p["dollar_variance_exception_pct"]
    materiality = p["materiality_usd"]

    pay = [r for r in view.pay_records if r.pay_period.startswith(view.period)]
    # GL LABOR only. With no account-category table every line counts (the pre-DCAA tie-out, unchanged);
    # once the table exists, direct non-labor and non-labor pool lines are excluded from the tie-out.
    gl = [g for g in view.gl_lines if g.period == view.period and is_labor_line(view, g.account)]
    payroll_total = sum((r.gross_pay for r in pay), ZERO)
    gl_total = sum((g.amount for g in gl), ZERO)
    if payroll_total == 0 and gl_total == 0:
        return not_evaluated(RULE_ID, "No payroll or general-ledger labor amounts were found for this period")

    diff = abs(gl_total - payroll_total)
    result = Result.CONSISTENT
    if diff * HUNDRED > exc_pct * payroll_total:
        result = Result.EXCEPTION
    elif diff * HUNDRED > watch_pct * payroll_total:
        result = Result.WATCH
    value = ratio(diff, payroll_total) if payroll_total else Decimal("1.0000")

    metric = MetricResult(
        metric_id="M2",
        label="Payroll vs GL dollar variance",
        value=value,
        numerator=money(diff),
        denominator=money(payroll_total),
        result=result,
        watch_threshold=f"> {watch_pct}%",
        exception_threshold=f"> {exc_pct}%",
    )
    if result == Result.CONSISTENT:
        return RuleResult(rule_id=RULE_ID, result=result, metrics=(metric,))

    history = baseline_history(view, HISTORY_KEY)
    rising = has_baseline(history) and is_rising(value, history)
    exposure = money(diff)
    sev, reason = classify_with_reason(
        result,
        exposure=exposure,
        materiality=materiality,
        employees=len({r.employee_id for r in pay}),
        trend_rising=rising,
        no_trend_history=not has_baseline(history),
    )
    evidence = [
        EvidenceRef(
            kind="gl_line",
            ref_id=f"{g.account}/{g.project}",
            source_file=g.lineage.source_file,
            sha256=g.lineage.sha256,
            row=g.lineage.row,
            detail={
                "account": g.account,
                "project": g.project,
                "amount": str(g.amount),
                "cost_type": g.cost_type,
                "pool": g.pool,
            },
        )
        for g in gl
    ] + [
        EvidenceRef(
            kind="pay_record",
            ref_id=f"{r.employee_id}/{r.pay_period}",
            source_file=r.lineage.source_file,
            sha256=r.lineage.sha256,
            row=r.lineage.row,
            detail={"pay_period": r.pay_period, "gross_pay": str(r.gross_pay)},
        )
        for r in sorted(pay, key=lambda r: (r.employee_id, r.pay_period))
    ]
    finding = Finding(
        rule_id=RULE_ID,
        rule_version=VERSION,
        period=view.period,
        authorities=AUTHORITIES,
        basis=BASIS,
        severity=sev,
        severity_reason=reason,
        headline=(
            f"General-ledger labor differs from payroll gross pay by {fmt_money(diff)} "
            f"({fmt_pct(value, 2)})"
        ),
        metric_id="M2",
        metric_value=value,
        metric_numerator=money(diff),
        metric_denominator=money(payroll_total),
        threshold_tripped=(
            "dollar_variance_exception_pct" if result == Result.EXCEPTION else "dollar_variance_watch_pct"
        ),
        computed={
            "kind": "primary",
            "payroll_total": money(payroll_total),
            "gl_total": money(gl_total),
            "variance": exposure,
            "variance_ratio": value,
            "exposure_usd": exposure,
            "exposure_basis": "dollar_difference",
            "baseline_confidence": "ok" if has_baseline(history) else "low",
        },
        exposure_usd=exposure,
        exposure_entry_ids=(),
        employees=(),
        contracts=(),
        evidence=tuple(evidence),
        fingerprint=f"L-03|{view.period}",
    )
    return RuleResult(rule_id=RULE_ID, result=result, metrics=(metric,), findings=(finding,))


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Payroll vs. general ledger labor dollars",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "dollar_variance_watch_pct": ParamSpec(
                "dollar_variance_watch_pct", Decimal("0.5"), Direction.LOWER_IS_STRICTER,
                Decimal("0.1"), Decimal("1.0"), "%", "Dollar variance above which M2 is a Watch"),
            "dollar_variance_exception_pct": ParamSpec(
                "dollar_variance_exception_pct", Decimal("2.0"), Direction.LOWER_IS_STRICTER,
                Decimal("0.5"), Decimal("4.0"), "%", "Dollar variance above which M2 is an Exception"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
