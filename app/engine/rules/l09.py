"""L-09 Charges outside assignment or period of performance (M8).

An entry is a violation when its charge code maps to a contract and
  - work_date is after the contract's PoP end, or before its PoP start
    (both PoP dates are inclusive: a charge ON the end date is inside the PoP), or
  - the mapped contract is unknown to the working view, or the contract does not
    list the charge code among its own charge codes, or
  - the charge code has no mapping at all.
Indirect codes (no contract) are out of scope for this rule.

One finding per contract, fingerprint L-09|<contract_id>. Exposure =
sum(hours x loaded rate) over the violating entries (entry-based). Exception when
the violating hours exceed `out_of_pop_hours_tolerance`. Integrity failure => High.
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import TimeEntry, WorkingView
from engine.metrics import (
    ZERO,
    contracts_of_entries,
    entries_evidence,
    entry_costs,
    fmt_hours,
    sorted_unique,
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
    register,
)
from engine.severity import classify_with_reason

RULE_ID = "L-09"
VERSION = "1.0.0"
REQUIRED = ("timekeeping", "contracts")
AUTHORITIES = ("FAR 31.201-4", "FAR 52.216-7")
BASIS = "contract"

EXPLANATION_TEMPLATE = (
    "{hours} hours were charged to contract {contract_id} outside its period of performance "
    "({pop_start} to {pop_end}) by {employees_text}. {breakdown}"
    "\n\n"
    "Costs are chargeable to a contract only for work inside its period of performance. Charges outside "
    "those dates are inconsistent with the cited requirements ({authorities}), unless a modification extending the dates has not "
    "yet been recorded."
    "\n\n"
    "{hours} hours · {employees_text} · {contracts_text} · {exposure} (hours x loaded rate)"
)
RECOMMENDED_ACTION = (
    "Confirm the contract's current period of performance. If a modification extended it, upload the "
    "modification and confirm the new dates. If not, move the hours to an open charge code or an indirect "
    "account and correct the affected costs."
)

_UNMAPPED = "unmapped"


def _violation(view: WorkingView, e: TimeEntry) -> tuple[str, str] | None:
    """(contract key, reason) if the entry violates, else None."""
    m = view.charge_codes.get(e.charge_code)
    if m is None:
        return _UNMAPPED, "charge_code_not_mapped"
    if m.contract_id is None:
        return None
    c = view.contract(m.contract_id)
    if c is None:
        return m.contract_id, "contract_not_found"
    if e.charge_code not in c.charge_codes:
        return m.contract_id, "charge_code_not_on_contract"
    if e.work_date > c.pop_end:
        return m.contract_id, "after_pop_end"
    if e.work_date < c.pop_start:
        return m.contract_id, "before_pop_start"
    return None


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    tolerance = p["out_of_pop_hours_tolerance"]
    materiality = p["materiality_usd"]

    groups: dict[str, list[tuple[TimeEntry, str]]] = {}
    for e in sorted(view.time_entries, key=lambda x: x.entry_id):
        v = _violation(view, e)
        if v is not None:
            groups.setdefault(v[0], []).append((e, v[1]))

    total_hours = sum((e.hours for e in view.time_entries), ZERO)
    findings: list[Finding] = []
    logged: list[dict] = []
    all_hours = ZERO

    for key in sorted(groups):
        pairs = groups[key]
        entries = [e for e, _ in pairs]
        hours = sum((e.hours for e in entries), ZERO)
        all_hours += hours
        if hours <= tolerance:
            for e, why in pairs:
                logged.append(
                    {
                        "rule_id": RULE_ID,
                        "employee_id": e.employee_id,
                        "entry_id": e.entry_id,
                        "hours": str(e.hours),
                        "reason": f"{why}; within out_of_pop_hours_tolerance",
                        "source_file": e.lineage.source_file,
                        "row": e.lineage.row,
                    }
                )
            continue

        contract = view.contract(key)
        reasons = sorted({why for _, why in pairs})
        after = sum((e.hours for e, w in pairs if w == "after_pop_end"), ZERO)
        before = sum((e.hours for e, w in pairs if w == "before_pop_start"), ZERO)
        other = hours - after - before
        parts = []
        if after:
            parts.append(f"{fmt_hours(after)} hours are dated after the period of performance ended")
        if before:
            parts.append(f"{fmt_hours(before)} hours are dated before it began")
        if other:
            parts.append(f"{fmt_hours(other)} hours are charged to a code the contract does not list")
        breakdown = ("Of these, " + "; ".join(parts) + ".") if parts else ""
        exposure = money(sum((e.hours * view.loaded_rate(e.employee_id) for e in entries), ZERO))
        emps = sorted_unique(e.employee_id for e in entries)
        sev, reason = classify_with_reason(
            Result.EXCEPTION,
            exposure=exposure,
            materiality=materiality,
            employees=len(emps),
            integrity_failure=True,
            integrity_detail="charge_outside_period_of_performance_or_assignment",
        )
        if contract is not None and reasons == ["after_pop_end"]:
            headline = (
                f"{fmt_hours(hours)} hours charged to {key} after its period of performance "
                f"ended {contract.pop_end.isoformat()}"
            )
        elif contract is not None and reasons == ["before_pop_start"]:
            headline = (
                f"{fmt_hours(hours)} hours charged to {key} before its period of performance "
                f"began {contract.pop_start.isoformat()}"
            )
        elif contract is not None and set(reasons) <= {"after_pop_end", "before_pop_start"}:
            headline = f"{fmt_hours(hours)} hours charged to {key} outside its period of performance"
        elif key == _UNMAPPED:
            headline = f"{fmt_hours(hours)} hours charged to codes that map to no contract"
        else:
            headline = f"{fmt_hours(hours)} hours charged to {key} outside its assignment or period of performance"

        evidence: list[EvidenceRef] = list(entries_evidence(entries))
        if contract is not None:
            evidence.append(
                EvidenceRef(
                    kind="contract_term",
                    ref_id=f"{key}/period_of_performance",
                    source_file=contract.lineage.source_file,
                    sha256=contract.lineage.sha256,
                    row=contract.lineage.row,
                    detail={
                        "pop_start": contract.pop_start.isoformat(),
                        "pop_end": contract.pop_end.isoformat(),
                        "contract_type": contract.type,
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
                headline=headline,
                metric_id="M8",
                metric_value=hours,
                metric_numerator=hours,
                metric_denominator=total_hours,
                threshold_tripped="out_of_pop_hours_tolerance",
                computed={
                    "kind": "primary",
                    "contract_id": key,
                    "hours": hours,
                    "hours_after_pop_end": after,
                    "hours_before_pop_start": before,
                    "reasons": reasons,
                    "pop_start": contract.pop_start.isoformat() if contract else None,
                    "pop_end": contract.pop_end.isoformat() if contract else None,
                    "breakdown": breakdown,
                    "employees_n": len(emps),
                    "entry_count": len(entries),
                    "exposure_usd": exposure,
                    "exposure_basis": "loaded_cost",
                    "entry_costs": entry_costs(view, entries),
                },
                exposure_usd=exposure,
                exposure_entry_ids=tuple(sorted(e.entry_id for e in entries)),
                employees=emps,
                contracts=(key,) if key != _UNMAPPED else contracts_of_entries(view, entries),
                evidence=tuple(evidence),
                fingerprint=f"L-09|{key}",
            )
        )

    result = Result.EXCEPTION if findings else Result.CONSISTENT
    metric = MetricResult(
        metric_id="M8",
        label="Hours charged outside period of performance or assignment",
        value=all_hours,
        numerator=all_hours,
        denominator=total_hours,
        result=result,
        exception_threshold=f"> {tolerance} hours",
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
        title="Charges outside assignment or period of performance",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "out_of_pop_hours_tolerance": ParamSpec(
                "out_of_pop_hours_tolerance", Decimal("0"), Direction.LOWER_IS_STRICTER,
                None, None, "hours", "Zero tolerance: hours outside the period of performance above this are an Exception"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
