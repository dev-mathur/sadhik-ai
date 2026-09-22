"""L-06 Post-submission time edits (M6).

M6 edit rate = edits / time entries. Also computed: the share of edits with no
recorded reason (undocumented) and the share made in the last two calendar days of
the period. Watch when the edit rate exceeds `edit_rate_watch_pct`; Exception when
the undocumented share exceeds `undocumented_edit_exception_pct` or the last-two-days
share exceeds `edits_last_two_days_exception_pct`.

One finding per period. Exposure = sum(hours x loaded rate) over the DISTINCT entries
that carry at least one undocumented edit (entry-based, so M13 de-duplicates).

Trend: the current edit rate is compared with the retained `M6.edit_rate` history
(periods before this one). It is rising when it is a new high over that history
(> max). Fewer than 3 history periods is "no trend history", which severity treats
conservatively (Medium), never as stable.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from engine.canonical import TimeEdit, TimeEntry, WorkingView
from engine.metrics import (
    HUNDRED,
    ZERO,
    baseline_history,
    contracts_of_entries,
    entries_evidence,
    entry_costs,
    fmt_pct,
    has_baseline,
    is_rising,
    period_bounds,
    plural,
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
    ratio,
    register,
)
from engine.severity import classify_with_reason

RULE_ID = "L-06"
VERSION = "1.0.0"
REQUIRED = ("time_edits",)
AUTHORITIES = ("DCAA Information for Contractors", "FAR 52.215-2")
BASIS = "audit_practice"
HISTORY_KEY = "M6.edit_rate"

EXPLANATION_TEMPLATE = (
    "In {period_label}, {edit_count} time entries were edited after submission, {edit_rate_pct} of "
    "{total_entries} entries. Edits with no recorded reason: {undocumented_count} ({undocumented_share_pct} "
    "of edits). Edits made in the last two days of the period: {last_two_days_share_pct} of edits."
    "\n\n"
    "Changes to submitted time are expected to carry a documented reason. Edits without one are inconsistent "
    "with the cited requirements ({authorities}) and make it harder to show that the recorded hours are the "
    "hours worked."
    "\n\n"
    "{undocumented_text} \u00b7 {employees_text} \u00b7 {undocumented_hours} hours \u00b7 "
    "{exposure} (hours x loaded rate on the edited entries)"
)
RECOMMENDED_ACTION = (
    "Ask the editors for the reason behind each undocumented edit and record it against the edit. If a "
    "documented correction memo covers them, attach it. Edits with no supportable reason should be reversed "
    "and the underlying entries reviewed."
)


def _undocumented(edit: TimeEdit) -> bool:
    return edit.reason is None or not str(edit.reason).strip()


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")
    if "timekeeping" not in view.sources_present or not view.time_entries:
        return not_evaluated(
            RULE_ID, "The edit rate needs the timekeeping entries as its denominator, and none were loaded"
        )

    watch_pct = p["edit_rate_watch_pct"]
    undoc_pct = p["undocumented_edit_exception_pct"]
    last2_pct = p["edits_last_two_days_exception_pct"]
    materiality = p["materiality_usd"]

    total_entries = len(view.time_entries)
    edits = sorted(view.time_edits, key=lambda e: e.edit_id)
    n_edits = len(edits)
    undoc = [e for e in edits if _undocumented(e)]
    _, last_day = period_bounds(view.period)
    cutoff = last_day - timedelta(days=1)
    last2 = [e for e in edits if e.edited_at.date() >= cutoff]

    edit_rate = ratio(n_edits, total_entries)
    undoc_share = ratio(len(undoc), n_edits)
    last2_share = ratio(len(last2), n_edits)

    exceptions: list[str] = []
    if n_edits and Decimal(len(undoc)) * HUNDRED > undoc_pct * n_edits:
        exceptions.append("undocumented_edit_exception_pct")
    if n_edits and Decimal(len(last2)) * HUNDRED > last2_pct * n_edits:
        exceptions.append("edits_last_two_days_exception_pct")
    watch = Decimal(n_edits) * HUNDRED > watch_pct * total_entries

    if exceptions:
        result = Result.EXCEPTION
    elif watch:
        result = Result.WATCH
    else:
        result = Result.CONSISTENT

    metrics = (
        MetricResult(
            metric_id="M6",
            label="Post-submission edit rate",
            value=edit_rate,
            numerator=Decimal(n_edits),
            denominator=Decimal(total_entries),
            result=Result.WATCH if watch else Result.CONSISTENT,
            watch_threshold=f"> {watch_pct}%",
        ),
        MetricResult(
            metric_id="M6.undocumented_share",
            label="Share of edits with no recorded reason",
            value=undoc_share,
            numerator=Decimal(len(undoc)),
            denominator=Decimal(n_edits),
            result=Result.EXCEPTION if "undocumented_edit_exception_pct" in exceptions else Result.CONSISTENT,
            exception_threshold=f"> {undoc_pct}%",
        ),
        MetricResult(
            metric_id="M6.last_two_days_share",
            label="Share of edits made in the last two days of the period",
            value=last2_share,
            numerator=Decimal(len(last2)),
            denominator=Decimal(n_edits),
            result=Result.EXCEPTION if "edits_last_two_days_exception_pct" in exceptions else Result.CONSISTENT,
            exception_threshold=f"> {last2_pct}%",
        ),
    )
    if result == Result.CONSISTENT:
        return RuleResult(rule_id=RULE_ID, result=result, metrics=metrics)

    by_id: dict[str, TimeEntry] = {e.entry_id: e for e in view.time_entries}
    undoc_entries = [by_id[eid] for eid in sorted({e.entry_id for e in undoc}) if eid in by_id]
    exposure = money(sum((e.hours * view.loaded_rate(e.employee_id) for e in undoc_entries), ZERO))
    undoc_hours = sum((e.hours for e in undoc_entries), ZERO)
    if undoc_entries:
        subject_entries = undoc_entries
    else:
        subject_entries = [by_id[eid] for eid in sorted({e.entry_id for e in edits}) if eid in by_id]
    employees = sorted_unique(e.employee_id for e in subject_entries)

    history = baseline_history(view, HISTORY_KEY)
    rising = has_baseline(history) and is_rising(edit_rate, history)
    sev, reason = classify_with_reason(
        result,
        exposure=exposure,
        materiality=materiality,
        employees=len(employees),
        trend_rising=rising,
        no_trend_history=not has_baseline(history),
    )

    headline = f"Post-submission edit rate {fmt_pct(edit_rate)}"
    if undoc:
        headline += f", with {plural(len(undoc), 'edit')} lacking a reason code"
    if "edits_last_two_days_exception_pct" in exceptions:
        headline += f", {fmt_pct(last2_share, 0)} made in the last two days of the period"

    evidence: list[EvidenceRef] = [
        EvidenceRef(
            kind="edit",
            ref_id=e.edit_id,
            source_file=e.lineage.source_file,
            sha256=e.lineage.sha256,
            row=e.lineage.row,
            detail={
                "entry_id": e.entry_id,
                "edited_at": e.edited_at.isoformat(),
                "editor_id": e.editor_id,
                "old_value": e.old_value,
                "new_value": e.new_value,
                "reason": e.reason,
            },
        )
        for e in undoc
    ]
    evidence.extend(entries_evidence(subject_entries))

    finding = Finding(
        rule_id=RULE_ID,
        rule_version=VERSION,
        period=view.period,
        authorities=AUTHORITIES,
        basis=BASIS,
        severity=sev,
        severity_reason=reason,
        headline=headline,
        metric_id="M6",
        metric_value=edit_rate,
        metric_numerator=Decimal(n_edits),
        metric_denominator=Decimal(total_entries),
        threshold_tripped=exceptions[0] if exceptions else "edit_rate_watch_pct",
        computed={
            "kind": "primary",
            "edit_count": n_edits,
            "total_entries": total_entries,
            "edit_rate": edit_rate,
            "undocumented_count": len(undoc),
            "undocumented_share": undoc_share,
            "last_two_days_count": len(last2),
            "last_two_days_share": last2_share,
            "undocumented_hours": undoc_hours,
            "employees_n": len(employees),
            "trend_rising": rising,
            "trend_history_periods": len(history),
            "baseline_confidence": "ok" if has_baseline(history) else "low",
            "exposure_usd": exposure,
            "exposure_basis": "loaded_cost",
            "entry_costs": entry_costs(view, undoc_entries),
        },
        exposure_usd=exposure,
        exposure_entry_ids=tuple(sorted(e.entry_id for e in undoc_entries)),
        employees=employees,
        contracts=contracts_of_entries(view, subject_entries),
        evidence=tuple(evidence),
        fingerprint=f"L-06|{view.period}",
    )
    return RuleResult(rule_id=RULE_ID, result=result, metrics=metrics, findings=(finding,))


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Post-submission time edits",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "edit_rate_watch_pct": ParamSpec(
                "edit_rate_watch_pct", Decimal("3.0"), Direction.LOWER_IS_STRICTER,
                Decimal("0.5"), Decimal("10.0"), "%", "Edit rate above which M6 is a Watch"),
            "undocumented_edit_exception_pct": ParamSpec(
                "undocumented_edit_exception_pct", Decimal("10.0"), Direction.LOWER_IS_STRICTER,
                Decimal("1.0"), Decimal("25.0"), "%", "Share of edits with no reason above which M6 is an Exception"),
            "edits_last_two_days_exception_pct": ParamSpec(
                "edits_last_two_days_exception_pct", Decimal("50.0"), Direction.LOWER_IS_STRICTER,
                Decimal("20.0"), Decimal("90.0"), "%", "Share of edits made in the last two days above which M6 is an Exception"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
