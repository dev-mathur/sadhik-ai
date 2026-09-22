"""L-02 Timekeeping hours vs. paid hours (M1).

Per employee-pay-period: gap = timekeeping hours - paid hours (regular + OT + PTO).
Exposure = |gap| x loaded rate (gap hours, so `exposure_entry_ids` is empty).
A per-employee finding is raised when |gap| > employee_gap_materiality_hours;
it is an Exception when |gap| > employee_gap_exception_hours, else a Watch.
Smaller gaps are logged with lineage and raise nothing. The M1 aggregate is
Watch/Exception against the percentage thresholds; an aggregate Exception with
no per-employee finding to carry it raises one aggregate finding priced only on
the gaps not already priced individually (so nothing is counted twice).
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import PayRecord, TimeEntry, WorkingView
from engine.metrics import HUNDRED, ZERO, entries_evidence, fmt_hours, in_period, pay_period_of
from engine.rules.base import (
    Direction,
    EvidenceRef,
    Finding,
    MetricResult,
    ParamSpec,
    RESULT_RANK,
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

RULE_ID = "L-02"
VERSION = "1.0.0"
REQUIRED = ("timekeeping", "payroll")
AUTHORITIES = ("FAR 31.201-2", "SF 1408 labor distribution")
BASIS = "audit_practice"

EXPLANATION_TEMPLATE = (
    "For pay period {pay_period}, timekeeping for employee {employee_id} totals {time_hours} hours and "
    "payroll shows {paid_hours} hours paid, a gap of {abs_gap} hours ({direction_text})."
    "\n\n"
    "Hours recorded on timesheets are expected to reconcile to hours paid. A gap of this size is "
    "inconsistent with the labor distribution expected under the cited requirements ({authorities}) and may mean unpaid time, pay "
    "without supporting time, or a correction that was not carried through both systems."
    "\n\n"
    "{abs_gap} hours · {employees_text} · {exposure} ({abs_gap} hours x {loaded_rate} loaded rate per hour)"
)
RECOMMENDED_ACTION = (
    "Compare the employee's timesheet entries with the payroll record for the pay period. Confirm whether "
    "hours were missed in payroll, paid without time recorded, or corrected in one system only, then "
    "correct the record that is wrong and document the reason."
)


def _pay_period_rows(view: WorkingView):
    time_by: dict[tuple[str, str], list[TimeEntry]] = {}
    for e in view.time_entries:
        if in_period(e.work_date, view.period):
            time_by.setdefault((e.employee_id, pay_period_of(e.work_date)), []).append(e)
    pay_by: dict[tuple[str, str], list[PayRecord]] = {}
    for r in view.pay_records:
        if r.pay_period.startswith(view.period):
            pay_by.setdefault((r.employee_id, r.pay_period), []).append(r)
    return time_by, pay_by


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    watch_pct = p["hours_variance_watch_pct"]
    exc_pct = p["hours_variance_exception_pct"]
    exc_hours = p["employee_gap_exception_hours"]
    mat_hours = p["employee_gap_materiality_hours"]
    materiality = p["materiality_usd"]

    time_by, pay_by = _pay_period_rows(view)
    keys = sorted(set(time_by) | set(pay_by))

    rows = []
    total_abs_gap = ZERO
    total_paid = ZERO
    for key in keys:
        time_entries = time_by.get(key, [])
        pay_records = pay_by.get(key, [])
        time_hours = sum((e.hours for e in time_entries), ZERO)
        paid_hours = sum((r.total_hours for r in pay_records), ZERO)
        gap = time_hours - paid_hours
        total_abs_gap += abs(gap)
        total_paid += paid_hours
        rows.append((key, time_entries, pay_records, time_hours, paid_hours, gap))

    if total_paid == 0:
        return not_evaluated(RULE_ID, "No paid hours were found in the payroll source for this period")

    # M1 aggregate result, compared exactly (no rounded intermediate).
    m1_result = Result.CONSISTENT
    if total_abs_gap * HUNDRED > exc_pct * total_paid:
        m1_result = Result.EXCEPTION
    elif total_abs_gap * HUNDRED > watch_pct * total_paid:
        m1_result = Result.WATCH

    findings: list[Finding] = []
    logged: list[dict] = []
    flagged_keys: set[tuple[str, str]] = set()
    worst_emp = Result.CONSISTENT
    max_gap = ZERO

    for key, time_entries, pay_records, time_hours, paid_hours, gap in rows:
        emp_id, pay_period = key
        abs_gap = abs(gap)
        max_gap = max(max_gap, abs_gap)
        if abs_gap == 0:
            continue
        if abs_gap <= mat_hours:
            src = pay_records[0].lineage if pay_records else (time_entries[0].lineage if time_entries else None)
            logged.append(
                {
                    "rule_id": RULE_ID,
                    "employee_id": emp_id,
                    "pay_period": pay_period,
                    "gap_hours": str(abs_gap),
                    "signed_gap_hours": str(gap),
                    "threshold_hours": str(mat_hours),
                    "reason": "gap at or below employee_gap_materiality_hours",
                    "source_file": src.source_file if src else None,
                    "row": src.row if src else None,
                }
            )
            continue

        flagged_keys.add(key)
        result = Result.EXCEPTION if abs_gap > exc_hours else Result.WATCH
        if RESULT_RANK[result] > RESULT_RANK[worst_emp]:
            worst_emp = result
        rate = view.loaded_rate(emp_id)
        exposure = money(abs_gap * rate)
        sev, reason = classify_with_reason(
            result,
            exposure=exposure,
            materiality=materiality,
            employees=1,
            no_trend_history=True,  # a single employee-period has no trend to show it is stable
        )
        direction = "time_exceeds_pay" if gap > 0 else "pay_exceeds_time"
        headline = (
            f"Timekeeping exceeds paid hours by {fmt_hours(abs_gap)} h for {emp_id}"
            if gap > 0
            else f"Paid hours exceed timekeeping by {fmt_hours(abs_gap)} h for {emp_id}"
        )
        evidence: list[EvidenceRef] = []
        for r in pay_records:
            evidence.append(
                EvidenceRef(
                    kind="pay_record",
                    ref_id=f"{r.employee_id}/{r.pay_period}",
                    source_file=r.lineage.source_file,
                    sha256=r.lineage.sha256,
                    row=r.lineage.row,
                    detail={
                        "pay_period": r.pay_period,
                        "regular_hours": str(r.regular_hours),
                        "overtime_hours": str(r.overtime_hours),
                        "pto_hours": str(r.pto_hours),
                        "total_hours": str(r.total_hours),
                    },
                )
            )
        evidence.extend(entries_evidence(time_entries))
        findings.append(
            Finding(
                rule_id=RULE_ID,
                rule_version=VERSION,
                period=view.period,
                authorities=AUTHORITIES,
                basis=BASIS,
                severity=sev,
                severity_reason=reason,
                headline=headline,
                metric_id="M1",
                metric_value=ratio(abs_gap, paid_hours) if paid_hours else Decimal("1.0000"),
                metric_numerator=abs_gap,
                metric_denominator=paid_hours,
                threshold_tripped=(
                    "employee_gap_exception_hours"
                    if result == Result.EXCEPTION
                    else "employee_gap_materiality_hours"
                ),
                computed={
                    "kind": "employee_gap",
                    "employee_id": emp_id,
                    "pay_period": pay_period,
                    "time_hours": time_hours,
                    "paid_hours": paid_hours,
                    "gap_hours": gap,
                    "abs_gap": abs_gap,
                    "direction": direction,
                    "loaded_rate": rate,
                    "exposure_usd": exposure,
                    "exposure_basis": "gap_hours_x_loaded_rate",
                },
                exposure_usd=exposure,
                exposure_entry_ids=(),
                employees=(emp_id,),
                contracts=(),
                evidence=tuple(evidence),
                fingerprint=f"L-02|{emp_id}|{pay_period}",
            )
        )

    # Aggregate Exception with nothing individually flagged to carry it.
    if m1_result == Result.EXCEPTION and not findings:
        agg_exposure = money(sum((abs(g) * view.loaded_rate(k[0]) for k, _, _, _, _, g in rows), ZERO))
        emps = tuple(sorted({k[0] for k, _, _, _, _, g in rows if g != 0}))
        sev, reason = classify_with_reason(
            Result.EXCEPTION, exposure=agg_exposure, materiality=materiality, employees=len(emps)
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
                    f"Timekeeping and payroll hours differ by {fmt_hours(total_abs_gap)} h across "
                    f"{len(emps)} employees"
                ),
                metric_id="M1",
                metric_value=ratio(total_abs_gap, total_paid),
                metric_numerator=total_abs_gap,
                metric_denominator=total_paid,
                threshold_tripped="hours_variance_exception_pct",
                computed={
                    "kind": "aggregate",
                    "total_abs_gap": total_abs_gap,
                    "total_paid_hours": total_paid,
                    "employees_n": len(emps),
                    "exposure_usd": agg_exposure,
                    "exposure_basis": "gap_hours_x_loaded_rate",
                },
                exposure_usd=agg_exposure,
                exposure_entry_ids=(),
                employees=emps,
                contracts=(),
                evidence=(),
                fingerprint=f"L-02|{view.period}|aggregate",
            )
        )
        worst_emp = Result.EXCEPTION

    m1 = MetricResult(
        metric_id="M1",
        label="Timekeeping vs payroll hours variance",
        value=ratio(total_abs_gap, total_paid),
        numerator=total_abs_gap,
        denominator=total_paid,
        result=m1_result,
        watch_threshold=f"> {watch_pct}%",
        exception_threshold=f"> {exc_pct}%",
    )
    m1_emp = MetricResult(
        metric_id="M1.max_employee_gap",
        label="Largest employee-pay-period gap (hours)",
        value=max_gap,
        numerator=max_gap,
        denominator=None,
        result=worst_emp,
        watch_threshold=f"> {mat_hours} h",
        exception_threshold=f"> {exc_hours} h",
    )
    overall = max((m1_result, worst_emp), key=lambda r: RESULT_RANK[r])
    return RuleResult(
        rule_id=RULE_ID,
        result=overall,
        metrics=(m1, m1_emp),
        findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Timekeeping hours vs. paid hours",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "hours_variance_watch_pct": ParamSpec(
                "hours_variance_watch_pct", Decimal("1.0"), Direction.LOWER_IS_STRICTER,
                Decimal("0.25"), Decimal("2.0"), "%", "Aggregate hours variance above which M1 is a Watch"),
            "hours_variance_exception_pct": ParamSpec(
                "hours_variance_exception_pct", Decimal("3.0"), Direction.LOWER_IS_STRICTER,
                Decimal("1.0"), Decimal("5.0"), "%", "Aggregate hours variance above which M1 is an Exception"),
            "employee_gap_exception_hours": ParamSpec(
                "employee_gap_exception_hours", Decimal("8.0"), Direction.LOWER_IS_STRICTER,
                Decimal("2.0"), Decimal("16.0"), "hours", "Employee-pay-period gap above which the finding is an Exception"),
            "employee_gap_materiality_hours": ParamSpec(
                "employee_gap_materiality_hours", Decimal("4.0"), Direction.LOWER_IS_STRICTER,
                Decimal("1.0"), Decimal("8.0"), "hours", "Gaps at or below this are logged with lineage and raise no finding"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
