"""DQ-01 Unmatched employee identifiers.

One finding listing every employee ID present in timekeeping (and payroll, when
that source is loaded) but absent from the HRIS roster. Medium severity, exposure
0.00, not counted in coverage. Any HRIS-dependent rule cannot evaluate these
employees, which is why this is a finding and not a silent drop.
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import PayRecord, TimeEntry, WorkingView
from engine.metrics import ZERO, contracts_of_entries, entries_evidence, ratio
from engine.rules.base import (
    EvidenceRef,
    Finding,
    MetricResult,
    ResolvedParams,
    Result,
    RuleResult,
    RuleSpec,
    Severity,
    not_evaluated,
    register,
)

RULE_ID = "DQ-01"
VERSION = "1.0.0"
REQUIRED = ("timekeeping", "hris")
AUTHORITIES = ("audit_practice",)
BASIS = "audit_practice"

EXPLANATION_TEMPLATE = (
    "{unmatched_text} found in timekeeping{payroll_clause} with no match in the HRIS roster: {id_list}."
    "\n\n"
    "Checks that need HRIS attributes, such as the labor category crosswalk, cannot be evaluated for these "
    "employees, so their time sits outside those checks. A common cause is a roster exported before the "
    "employees were onboarded."
    "\n\n"
    "{employees_text} \u00b7 {hours} hours of recorded time outside HRIS-based checks \u00b7 no dollar "
    "exposure is assigned"
)
RECOMMENDED_ACTION = (
    "Confirm whether these employees are missing from the HRIS export or carry a different ID there. "
    "Re-export the roster, or correct the identifiers, and re-run so these employees are covered."
)


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    roster = {e.employee_id for e in view.employees}
    tk_ids = {e.employee_id for e in view.time_entries}
    pay_present = "payroll" in view.sources_present
    pay_ids = {r.employee_id for r in view.pay_records} if pay_present else set()
    all_ids = tk_ids | pay_ids
    unmatched = sorted(all_ids - roster)

    metric_result = Result.EXCEPTION if unmatched else Result.CONSISTENT
    metric = MetricResult(
        metric_id="DQ-01",
        label="Employee IDs not present in the HRIS roster",
        value=ratio(len(unmatched), len(all_ids)),
        numerator=Decimal(len(unmatched)),
        denominator=Decimal(len(all_ids)),
        result=metric_result,
        exception_threshold="any unmatched employee ID",
    )
    if not unmatched:
        return RuleResult(rule_id=RULE_ID, result=Result.CONSISTENT, metrics=(metric,))

    entries: list[TimeEntry] = [e for e in view.time_entries if e.employee_id in set(unmatched)]
    pays: list[PayRecord] = [r for r in view.pay_records if r.employee_id in set(unmatched)] if pay_present else []
    hours = sum((e.hours for e in entries), ZERO)
    evidence: list[EvidenceRef] = list(entries_evidence(entries))
    for r in sorted(pays, key=lambda r: (r.employee_id, r.pay_period)):
        evidence.append(
            EvidenceRef(
                kind="pay_record",
                ref_id=f"{r.employee_id}/{r.pay_period}",
                source_file=r.lineage.source_file,
                sha256=r.lineage.sha256,
                row=r.lineage.row,
                detail={"pay_period": r.pay_period, "total_hours": str(r.total_hours)},
            )
        )

    finding = Finding(
        rule_id=RULE_ID,
        rule_version=VERSION,
        period=view.period,
        authorities=AUTHORITIES,
        basis=BASIS,
        severity=Severity.MEDIUM,
        severity_reason="data_quality:unmatched_employee_ids",
        headline=(
            f"{len(unmatched)} timekeeping employee {'ID is' if len(unmatched) == 1 else 'IDs are'} "
            "not present in the HRIS roster"
        ),
        metric_id="DQ-01",
        metric_value=ratio(len(unmatched), len(all_ids)),
        metric_numerator=Decimal(len(unmatched)),
        metric_denominator=Decimal(len(all_ids)),
        threshold_tripped="any_unmatched_employee_id",
        computed={
            "kind": "primary",
            "unmatched_ids": list(unmatched),
            "unmatched_n": len(unmatched),
            "employee_ids_checked": len(all_ids),
            "in_payroll": bool(pays),
            "hours": hours,
            "exposure_usd": Decimal("0.00"),
            "exposure_basis": "none",
        },
        exposure_usd=Decimal("0.00"),
        exposure_entry_ids=(),
        employees=tuple(unmatched),
        contracts=contracts_of_entries(view, entries),
        evidence=tuple(evidence),
        fingerprint=f"DQ-01|{view.period}",
    )
    return RuleResult(rule_id=RULE_ID, result=Result.EXCEPTION, metrics=(metric,), findings=(finding,))


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Unmatched employee identifiers",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={},
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
        counts_toward_coverage=False,  # a data-quality gate, not a rule evaluated against a requirement
    )
)
