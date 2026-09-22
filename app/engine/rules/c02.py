"""C-02 Incurred cost submission deadline (M16). Domain: DCAA cost accounting.

A contractor with cost-type contracts submits a final indirect cost rate proposal within six months after the end
of each fiscal year (FAR 52.216-7(d)(2)). `view.ics_submissions` lists each fiscal year's end and, if it has been
made, its submission date. As of the LAST DAY of the run period (the engine never reads a clock):

    due = fiscal year end + `ics_due_months` (same day of month, clamped to the month's last day)
    not submitted and the due date is BEFORE the as-of date -> Exception, days_overdue = as_of - due (>= 1)
    submitted after the due date                        -> history only: logged, no finding
    not yet due, or submitted on time                   -> nothing

`ics_due_months` has a regulatory floor of 6: a customer may set an EARLIER internal deadline, never a later one.
No schedule, or no rate data, is Not evaluated. No dollars are assigned (exposure 0.00); a missed regulatory
deadline is an integrity failure (High).
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal
from typing import Any

from engine.canonical import IcsRecord, WorkingView
from engine.metrics import period_bounds
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
    register,
)
from engine.severity import classify_with_reason

RULE_ID = "C-02"
VERSION = "1.0.0"
REQUIRED = ("rate_data",)
AUTHORITIES = ("FAR 52.216-7(d)(2)",)
BASIS = "regulatory"

EXPLANATION_TEMPLATE = (
    "The incurred cost submission for {fiscal_year} (fiscal year ended {fiscal_year_end}) was due {due_date}, "
    "{deadline_months} months after year end. No submission is recorded as of {as_of}, {days_overdue} days after "
    "the due date."
    "\n\n"
    "A final indirect cost rate proposal is expected within six months after the end of each fiscal year under the "
    "cited requirement ({authorities}). A missing submission past that date is inconsistent with the requirement, "
    "and the year's indirect rates cannot be settled without it."
    "\n\n"
    "{days_overdue} days overdue · {fiscal_year} · no dollar exposure is assigned"
)
RECOMMENDED_ACTION = (
    "Confirm with whoever prepares the submission whether it was made. If it was not, prepare and submit it and "
    "record the date. If it was, add the submission date to the schedule."
)


def add_months(d: date, n: int) -> date:
    """`n` months after `d`, keeping the day of month and clamping to the last day of the target month."""
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _evidence(r: IcsRecord, due: date) -> EvidenceRef:
    return EvidenceRef(
        kind="ics_record",
        ref_id=r.fiscal_year,
        source_file=r.lineage.source_file,
        sha256=r.lineage.sha256,
        row=r.lineage.row,
        detail={
            "fiscal_year": r.fiscal_year,
            "fiscal_year_end": r.fiscal_year_end.isoformat(),
            "due_date": due.isoformat(),
            "submitted_on": r.submitted_on.isoformat() if r.submitted_on else "not recorded",
            "source_document": r.source_document,
        },
    )


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    if "rate_data" not in view.sources_present:
        return not_evaluated(
            RULE_ID, "Indirect rate data, which carries the incurred cost submission schedule, was not uploaded"
        )
    if not view.ics_submissions:
        return not_evaluated(RULE_ID, "No incurred cost submission schedule was provided")
    months = int(p["ics_due_months"])
    materiality = p["materiality_usd"]
    _, as_of = period_bounds(view.period)

    findings: list[Finding] = []
    logged: list[dict[str, Any]] = []
    due_count = 0
    worst = 0
    for r in sorted(view.ics_submissions, key=lambda x: x.fiscal_year_end):
        due = add_months(r.fiscal_year_end, months)
        if due >= as_of:
            continue  # not yet late: a submission due on the as-of date itself can still be made that day
        due_count += 1
        if r.submitted_on is None:
            days = (as_of - due).days
            worst = max(worst, days)
            sev, reason = classify_with_reason(
                Result.EXCEPTION, exposure=money(Decimal(0)), materiality=materiality, employees=0,
                integrity_failure=True, integrity_detail=f"ics_overdue_{days}_days",
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
                    headline=(f"The {r.fiscal_year} incurred cost submission was due {due.isoformat()} and is not "
                              f"recorded as submitted ({days} days overdue)"),
                    metric_id="M16",
                    metric_value=Decimal(days),
                    metric_numerator=Decimal(days),
                    metric_denominator=None,
                    threshold_tripped="submission_past_due_date",
                    computed={
                        "kind": "primary",
                        "fiscal_year": r.fiscal_year,
                        "fiscal_year_end": r.fiscal_year_end.isoformat(),
                        "due_date": due.isoformat(),
                        "deadline_months": Decimal(months),
                        "as_of": as_of.isoformat(),
                        "days_overdue": Decimal(days),
                        "exposure_usd": money(Decimal(0)),
                        "exposure_basis": "none",
                    },
                    exposure_usd=money(Decimal(0)),
                    exposure_entry_ids=(),
                    employees=(),
                    contracts=(),
                    evidence=(_evidence(r, due),),
                    fingerprint=f"{RULE_ID}|{r.fiscal_year}|{view.period}",
                )
            )
        elif r.submitted_on > due:
            logged.append({"rule_id": RULE_ID, "fiscal_year": r.fiscal_year, "due_date": due.isoformat(),
                           "submitted_on": r.submitted_on.isoformat(), "days_late": (r.submitted_on - due).days,
                           "reason": "submitted_late_history_only"})

    result = Result.EXCEPTION if findings else Result.CONSISTENT
    metric = MetricResult(
        metric_id="M16",
        label="Days past the incurred cost submission due date",
        value=Decimal(worst),
        numerator=Decimal(worst),
        denominator=None,
        result=result,
        exception_threshold=f"any submission not made by {months} months after fiscal year end",
        note=f"{due_count} fiscal year(s) were due by {as_of.isoformat()}",
    )
    return RuleResult(
        rule_id=RULE_ID, result=result, metrics=(metric,), findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Incurred cost submission deadline",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "ics_due_months": ParamSpec(
                "ics_due_months", Decimal("6"), Direction.LOWER_IS_STRICTER, Decimal("1"), Decimal("6"), "months",
                "Months after fiscal year end by which the incurred cost submission is due. A customer may set an "
                "earlier deadline than the regulatory six months, never a later one."),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        domain=Domain.DCAA_COST_ACCOUNTING.value,
        review_status="unreviewed",
    )
)
