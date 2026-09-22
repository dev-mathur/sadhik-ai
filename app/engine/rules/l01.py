"""L-01 Labor category qualification.

Mismatch = the category charged on a timesheet entry is not the contract category
the employee's HRIS title maps to in the customer-confirmed crosswalk.
Exposure = max(0, charged_rate - approved_rate) x hours (a rate difference, so
`exposure_entry_ids` is empty). Any mismatch is an integrity failure => High.
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import TimeEntry, WorkingView
from engine.metrics import ZERO, employee_evidence, entries_evidence, plural
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

RULE_ID = "L-01"
VERSION = "1.0.0"
REQUIRED = ("timekeeping", "hris", "contracts")

SPEC_AUTHORITIES = ("FAR 52.232-7", "FAR 31.201-2")
SPEC_BASIS = "regulatory"

# Three paragraphs: what happened / why it matters / impact. Filled by
# engine.explain from Finding fields only.
EXPLANATION_TEMPLATE = (
    "In {period_label}, employee {employee_id} recorded {hours} hours on contract {contract_id} under the "
    "labor category \"{charged_category}\". The confirmed crosswalk maps this employee's HRIS title, "
    "\"{hris_title}\", to \"{crosswalked_category}\". {rate_sentence}"
    "\n\n"
    "Under the cited requirements ({authorities}), hours are billed at the contract rate for the category the employee qualifies "
    "for. Hours recorded under a different category than the crosswalk supports are inconsistent with "
    "that requirement, and any rate difference is exposed to over-billing."
    "\n\n"
    "{hours} hours \u00b7 {employees_text} \u00b7 {contracts_text} \u00b7 {exposure}{rate_math}"
)
RECOMMENDED_ACTION = (
    "Confirm which category the employee is approved for on the contract. If the employee holds a "
    "qualification the HRIS title does not reflect, update the crosswalk and attach the supporting "
    "documentation. If not, correct the timesheet entries and the affected invoice."
)


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    tolerance = p["unapproved_category_tolerance"]
    materiality = p["materiality_usd"]
    total_entries = len(view.time_entries)

    # (employee_id, contract_id) -> list of (entry, expected_category)
    groups: dict[tuple[str, str], list[tuple[TimeEntry, str]]] = {}
    not_evaluable = 0
    for e in view.time_entries:
        mapping = view.charge_codes.get(e.charge_code)
        if mapping is None or mapping.contract_id is None:
            continue  # indirect or unmapped code: not a contract labor-category question
        emp = view.employee(e.employee_id)
        expected = view.category_crosswalk.get(emp.title) if emp is not None else None
        if expected is None:
            not_evaluable += 1  # no HRIS row or no crosswalk row: cannot establish the approved category
            continue
        if e.labor_category != expected:
            groups.setdefault((e.employee_id, mapping.contract_id), []).append((e, expected))

    findings: list[Finding] = []
    logged: list[dict] = []
    mismatch_entries = 0
    for (emp_id, contract_id) in sorted(groups):
        pairs = sorted(groups[(emp_id, contract_id)], key=lambda x: x[0].entry_id)
        entries = [e for e, _ in pairs]
        if len(entries) <= tolerance:
            for e in entries:
                logged.append(
                    {
                        "rule_id": RULE_ID,
                        "employee_id": emp_id,
                        "entry_id": e.entry_id,
                        "hours": str(e.hours),
                        "reason": "category mismatch within unapproved_category_tolerance",
                        "source_file": e.lineage.source_file,
                        "row": e.lineage.row,
                    }
                )
            continue
        mismatch_entries += len(entries)

        contract = view.contract(contract_id)
        emp = view.employee(emp_id)
        hours = sum((e.hours for e in entries), ZERO)
        exposure_raw = ZERO
        charged_cats: set[str] = set()
        approved_cats: set[str] = set()
        rates_missing = False
        charged_rate = approved_rate = None
        for e, expected in pairs:
            charged_cats.add(e.labor_category)
            approved_cats.add(expected)
            c_cat = contract.category(e.labor_category) if contract else None
            a_cat = contract.category(expected) if contract else None
            if c_cat is None or a_cat is None:
                rates_missing = True
                continue
            charged_rate, approved_rate = c_cat.ceiling_rate, a_cat.ceiling_rate
            exposure_raw += max(ZERO, c_cat.ceiling_rate - a_cat.ceiling_rate) * e.hours
        exposure = money(exposure_raw)
        single = len(charged_cats) == 1 and len(approved_cats) == 1 and not rates_missing
        charged_txt = " / ".join(sorted(charged_cats))
        approved_txt = " / ".join(sorted(approved_cats))

        computed = {
            "kind": "primary",
            "employee_id": emp_id,
            "hris_title": emp.title if emp else "",
            "crosswalked_category": approved_txt,
            "charged_category": charged_txt,
            "contract_id": contract_id,
            "hours": hours,
            "entry_count": len(entries),
            "charged_rate": charged_rate if single else None,
            "approved_rate": approved_rate if single else None,
            "rate_difference": (charged_rate - approved_rate) if single else None,
            "rate_unavailable": rates_missing,
            "exposure_usd": exposure,
            "exposure_basis": "rate_difference",
        }
        sev, reason = classify_with_reason(
            Result.EXCEPTION,
            exposure=exposure,
            materiality=materiality,
            employees=1,
            integrity_failure=True,
            integrity_detail="category_differs_from_crosswalk",
        )
        evidence: list[EvidenceRef] = list(entries_evidence(entries))
        if emp is not None:
            evidence.append(employee_evidence(emp))
            evidence.append(
                EvidenceRef(
                    kind="crosswalk",
                    ref_id=emp.title,
                    source_file="labor_category_crosswalk.csv",
                    sha256="",
                    row=None,
                    detail={"hris_title": emp.title, "contract_category": approved_txt},
                )
            )
        if contract is not None:
            for cat_name in sorted(charged_cats | approved_cats):
                cat = contract.category(cat_name)
                evidence.append(
                    EvidenceRef(
                        kind="contract_term",
                        ref_id=f"{contract_id}/{cat_name}",
                        source_file=contract.lineage.source_file,
                        sha256=contract.lineage.sha256,
                        row=contract.lineage.row,
                        detail={
                            "category": cat_name,
                            "ceiling_rate": str(cat.ceiling_rate) if cat else None,
                            "contract_type": contract.type,
                        },
                    )
                )
        findings.append(
            Finding(
                rule_id=RULE_ID,
                rule_version=VERSION,
                period=view.period,
                authorities=SPEC_AUTHORITIES,
                basis=SPEC_BASIS,
                severity=sev,
                severity_reason=reason,
                headline=(
                    f"{charged_txt} billed on {contract_id} for an employee "
                    f"crosswalked to {approved_txt}"
                ),
                metric_id="M3",
                metric_value=ratio(len(entries), total_entries),
                metric_numerator=Decimal(len(entries)),
                metric_denominator=Decimal(total_entries),
                threshold_tripped="any_unapproved_category",
                computed=computed,
                exposure_usd=exposure,
                exposure_entry_ids=(),
                employees=(emp_id,),
                contracts=(contract_id,),
                evidence=tuple(evidence),
                fingerprint=f"L-01|{emp_id}|{contract_id}",
            )
        )

    m3 = MetricResult(
        metric_id="M3",
        label="Labor category mismatch rate",
        value=ratio(mismatch_entries, total_entries),
        numerator=Decimal(mismatch_entries),
        denominator=Decimal(total_entries),
        result=Result.EXCEPTION if findings else Result.CONSISTENT,
        exception_threshold=f"mismatched entries > {tolerance}",
        note=(
            f"{plural(not_evaluable, 'direct entry', 'direct entries')} could not be checked "
            "(employee or HRIS title not in the crosswalk)"
            if not_evaluable
            else ""
        ),
    )
    return RuleResult(
        rule_id=RULE_ID,
        result=Result.EXCEPTION if findings else Result.CONSISTENT,
        metrics=(m3,),
        findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Labor category qualification",
        authorities=SPEC_AUTHORITIES,
        basis=SPEC_BASIS,
        required_sources=REQUIRED,
        parameters={
            "unapproved_category_tolerance": ParamSpec(
                name="unapproved_category_tolerance",
                default=Decimal("0"),
                direction=Direction.LOWER_IS_STRICTER,
                unit="entries",
                description="Zero tolerance: entries billed under a category other than the crosswalked one.",
            ),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
