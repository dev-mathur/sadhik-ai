"""L-08 Direct/indirect classification consistency (M9). Domain: DCAA cost accounting.

Each employee's current-period hours are split by the confirmed charge-code map:
  direct    = hours on a code with cost_type "direct" (a contract charge code)
  indirect  = hours on a code with cost_type "indirect" in any pool EXCEPT fringe (overhead, G&A, ...)
Fringe-pool codes (fringe administration, leave) are excluded from both sides, and so are unmapped
codes. share = indirect / (direct + indirect).

The baseline is the employee's own history, `view.classification_history[employee][period] =
(direct_hours, indirect_hours)`: the latest BASELINE_WINDOW periods before the current one that have
any hours. An employee with fewer than MIN_HISTORY_PERIODS such periods is not scored; they are listed
in `logged_below_materiality` with reason `insufficient_history`.

Flagged when BOTH
    share_now - mean(share_hist)      >= indirect_share_shift_pp  (percentage points)
    indirect_now - mean(indirect_hist) >= min_excess_hours
excess = indirect_now - mean(indirect_hist); exposure = money(excess x loaded rate). The excess is not
tied to specific entries, so `exposure_entry_ids` is empty (M13 counts it as a plain amount). A flagged
employee is an Exception and an integrity failure (High). Comparisons are exact Decimal comparisons.

No history at all (`classification_history` empty), or nobody scoreable, is Not evaluated: without a
baseline there is nothing to compare against, and that is never a pass.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine.canonical import TimeEntry, WorkingView
from engine.metrics import (
    BASELINE_WINDOW,
    HUNDRED,
    ZERO,
    entries_evidence,
    fmt_hours,
    in_period,
)
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

RULE_ID = "L-08"
VERSION = "1.0.0"
REQUIRED = ("timekeeping",)
AUTHORITIES = ("DFARS 252.242-7006(c)(2)", "CAS 402 (to verify)")
BASIS = "audit_practice"
# The history minimum is a constant of the method (design FR 6.2), not a customer parameter.
MIN_HISTORY_PERIODS = 3

EXPLANATION_TEMPLATE = (
    "In {period_label}, {employee_id} recorded {indirect_hours} hours on indirect charge codes and "
    "{direct_hours} hours on direct charge codes, an indirect share of {share_now_pct}. Over the prior "
    "{history_periods} periods the same employee's indirect share averaged {baseline_share_pct} "
    "({baseline_indirect_hours} indirect hours a period), a rise of {share_shift_pp} percentage points and "
    "{excess_hours} hours."
    "\n\n"
    "Work is expected to be classified between direct and indirect consistently from period to period under the "
    "cited requirements ({authorities}). A sudden rise in indirect hours against the employee's own history is "
    "inconsistent with that expectation. It may mean hours were reclassified from contract work to an indirect "
    "account, or that the employee's role changed and the change is not yet documented."
    "\n\n"
    "{excess_hours} hours above the employee's baseline · {employees_text} · {exposure} (excess hours x loaded rate)"
)
RECOMMENDED_ACTION = (
    "Review the employee's indirect entries for the period with their supervisor and confirm each against the "
    "work performed. If the role changed, document the change. If hours belong on a contract, correct the charge "
    "codes and the affected costs."
)


def _split(view: WorkingView, entries: list[TimeEntry]) -> tuple[Decimal, Decimal, list[TimeEntry]]:
    """(direct hours, indirect hours, indirect-coded entries) by the confirmed charge-code map."""
    direct = indirect = ZERO
    ind_entries: list[TimeEntry] = []
    for e in entries:
        m = view.charge_codes.get(e.charge_code)
        if m is None:
            continue  # unmapped: no classification to compare
        if m.cost_type == "direct":
            direct += e.hours
        elif m.cost_type == "indirect" and m.pool != "fringe":
            indirect += e.hours
            ind_entries.append(e)
    return direct, indirect, ind_entries


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, ZERO) / Decimal(len(values))


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    if "timekeeping" not in view.sources_present:
        return not_evaluated(RULE_ID, "Required source not present: timekeeping")
    if not view.classification_history:
        return not_evaluated(RULE_ID, "Classification history from prior periods was not available")

    shift_pp = p["indirect_share_shift_pp"]
    min_excess = p["min_excess_hours"]

    by_emp: dict[str, list[TimeEntry]] = {}
    for e in view.time_entries:
        if in_period(e.work_date, view.period):
            by_emp.setdefault(e.employee_id, []).append(e)

    findings: list[Finding] = []
    logged: list[dict] = []
    scored = 0
    for emp_id in sorted(by_emp):
        entries = by_emp[emp_id]
        direct, indirect, ind_entries = _split(view, entries)
        if direct + indirect == 0:
            continue  # only fringe/unmapped hours this period: nothing to classify

        hist_by_period = view.classification_history.get(emp_id, {})
        usable = sorted(
            per for per, (d, i) in hist_by_period.items() if per < view.period and d + i > 0
        )[-BASELINE_WINDOW:]
        if len(usable) < MIN_HISTORY_PERIODS:
            logged.append(
                {
                    "rule_id": RULE_ID,
                    "employee_id": emp_id,
                    "period": view.period,
                    "history_periods": len(usable),
                    "min_history_periods": MIN_HISTORY_PERIODS,
                    "reason": "insufficient_history",
                    "source_file": entries[0].lineage.source_file,
                    "row": entries[0].lineage.row,
                }
            )
            continue

        scored += 1
        hist = [(per, *hist_by_period[per]) for per in usable]
        base_share = _mean([i / (d + i) for _, d, i in hist])
        base_indirect = _mean([i for _, _, i in hist])
        share_now = indirect / (direct + indirect)
        shift = share_now - base_share  # fraction
        excess = indirect - base_indirect
        if shift * HUNDRED < shift_pp or excess < min_excess:
            continue

        shift_txt = ratio(shift * HUNDRED, 1, "0.1")
        exposure = money(excess * view.loaded_rate(emp_id))
        sev, reason = classify_with_reason(
            Result.EXCEPTION,
            exposure=exposure,
            materiality=p["materiality_usd"],
            employees=1,
            integrity_failure=True,
            integrity_detail=f"indirect_share_up_{shift_txt}_pp",
        )
        evidence: list[EvidenceRef] = list(entries_evidence(ind_entries))
        evidence.append(
            EvidenceRef(
                kind="history",
                ref_id=emp_id,
                source_file="classification_history.json",
                sha256="",
                row=None,
                detail={
                    "periods": usable,
                    "direct_hours": {per: str(d) for per, d, _ in hist},
                    "indirect_hours": {per: str(i) for per, _, i in hist},
                    "baseline_share": str(ratio(base_share, 1)),
                    "baseline_indirect_hours": str(ratio(base_indirect, 1)),
                },
            )
        )
        findings.append(
            Finding(
                rule_id=RULE_ID,
                rule_version=VERSION,
                period=view.period,
                authorities=AUTHORITIES,
                basis=BASIS,
                severity=sev,
                severity_reason=reason,
                headline=(
                    f"{emp_id} recorded {fmt_hours(indirect)} indirect hours, {fmt_hours(ratio(excess, 1, '0.1'))} above "
                    f"their own baseline; indirect share up {shift_txt} percentage points"
                ),
                metric_id="M9",
                metric_value=ratio(shift, 1),
                metric_numerator=indirect,
                metric_denominator=direct + indirect,
                threshold_tripped="indirect_share_shift_pp",
                computed={
                    "kind": "primary",
                    "employee_id": emp_id,
                    "direct_hours": direct,
                    "indirect_hours": indirect,
                    "share_now": ratio(share_now, 1),
                    "baseline_share": ratio(base_share, 1),
                    "share_shift": ratio(shift, 1),
                    "share_shift_pp": ratio(shift * HUNDRED, 1, "0.1"),
                    "baseline_indirect_hours": ratio(base_indirect, 1, "0.1"),
                    "excess_hours": ratio(excess, 1, "0.1"),
                    "history_periods": len(usable),
                    "indirect_entry_count": len(ind_entries),
                    "loaded_rate": view.loaded_rate(emp_id),
                    "exposure_usd": exposure,
                    "exposure_basis": "loaded_cost",
                },
                exposure_usd=exposure,
                exposure_entry_ids=(),
                employees=(emp_id,),
                contracts=(),
                evidence=tuple(evidence),
                fingerprint=f"{RULE_ID}|{emp_id}|{view.period}",
            )
        )

    if scored == 0:
        return not_evaluated(
            RULE_ID,
            f"No employee has at least {MIN_HISTORY_PERIODS} prior periods of classification history",
        )

    result = Result.EXCEPTION if findings else Result.CONSISTENT
    skipped = len(logged)
    metric = MetricResult(
        metric_id="M9",
        label="Employees whose indirect share of hours rose above their own baseline",
        value=ratio(len(findings), scored),
        numerator=Decimal(len(findings)),
        denominator=Decimal(scored),
        result=result,
        exception_threshold=f"share up >= {shift_pp} points and >= {min_excess} hours over baseline",
        note=(
            f"{skipped} employee{'s' if skipped != 1 else ''} not scored: fewer than {MIN_HISTORY_PERIODS} "
            "periods of history"
            if skipped
            else ""
        ),
    )
    return RuleResult(
        rule_id=RULE_ID,
        result=result,
        metrics=(metric,),
        findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Direct and indirect classification consistency",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "indirect_share_shift_pp": ParamSpec(
                "indirect_share_shift_pp", Decimal("15"), Direction.LOWER_IS_STRICTER,
                Decimal("5"), Decimal("40"), "percentage points",
                "Rise in an employee's indirect share of hours over their own baseline at or above which the check trips"),
            "min_excess_hours": ParamSpec(
                "min_excess_hours", Decimal("16"), Direction.LOWER_IS_STRICTER,
                Decimal("4"), Decimal("80"), "hours",
                "Indirect hours above the employee's own baseline that must also be reached before a finding is raised"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        domain=Domain.DCAA_COST_ACCOUNTING.value,
        review_status="unreviewed",
    )
)
