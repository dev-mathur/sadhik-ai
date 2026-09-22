"""L-05 Late or reconstructed time entries, and entry-time clustering.

M4 late-entry rate: an entry is late when it was created more than
`late_threshold_hours` after the end of its work date. Watch when the rate exceeds
`late_rate_watch_pct` (no Exception threshold is declared, so none is applied).

M5 cluster index, per DIRECT contract group (entries whose charge code maps to a
contract; indirect codes are not grouped):
    group window share = entries created Friday at/after `cluster_window_start_hour`
                         / entries in the group
    index = group window share / baseline
    baseline = mean of the last 6 `M5.company_window_share` values in view.baselines
               (periods before the current one). With fewer than 3 history periods the
               baseline falls back to this run's own company-wide window share and the
               finding is marked baseline_confidence = "low".
Groups smaller than `cluster_min_group_entries` are not scored.
The group's team is every employee with at least one entry in the group. The
flagged entries are the group's entries inside the window; exposure is
sum(hours x loaded rate) over them (entry-based, so M13 de-duplicates them).
"""

from __future__ import annotations

from decimal import Decimal

from engine.canonical import TimeEntry, WorkingView
from engine.metrics import (
    HUNDRED,
    ZERO,
    baseline_history,
    baseline_mean,
    entries_evidence,
    entry_costs,
    fmt_x,
    has_baseline,
    is_rising,
    lag_hours,
    plural,
    sorted_unique,
    contracts_of_entries,
)
from engine.rules.base import (
    RESULT_RANK,
    Direction,
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

RULE_ID = "L-05"
VERSION = "1.0.0"
REQUIRED = ("timekeeping",)
AUTHORITIES = (
    "DCAA Information for Contractors: timely recording",
    "SF 1408 timekeeping criterion",
)
BASIS = "audit_practice"
M5_KEY = "M5.company_window_share"
M4_KEY = "M4.late_rate"

EXPLANATION_TEMPLATE = (
    "On contract {contract_id}, {window_entries} of {group_entries} time entries ({window_share_pct}) were "
    "created on Fridays from {window_start_hour}:00 onward, against a company baseline of {baseline_share_pct}. "
    "That is {cluster_index_txt}x the baseline across {employees_text}. {baseline_note}"
    "\n\n"
    "Entries created in bulk at the end of the week are more likely to have been reconstructed than recorded "
    "as the work was done. Time is expected to be recorded timely under the cited requirements ({authorities}). This pattern is an "
    "analytic signal that the timekeeping is inconsistent with that expectation; it does not by itself show "
    "that any recorded hours are wrong."
    "\n\n"
    "{window_entries} entries · {employees_text} · {contracts_text} · {exposure} "
    "(hours x loaded rate over the flagged entries)"
)
RECOMMENDED_ACTION = (
    "Ask the project manager why these entries were created together on Fridays. Compare the entries with "
    "supporting records such as calendars, deliverable dates and badge or system logs, and remind the team "
    "of the customer's timekeeping policy. Correct any entry that was reconstructed and document the reason."
)


def _in_window(e: TimeEntry, start_hour: Decimal) -> bool:
    minutes = e.entered_at.hour * 60 + e.entered_at.minute
    return e.entered_at.weekday() == 4 and Decimal(minutes) >= start_hour * 60


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")

    late_hours = p["late_threshold_hours"]
    late_watch_pct = p["late_rate_watch_pct"]
    start_hour = p["cluster_window_start_hour"]
    idx_watch = p["cluster_index_watch"]
    idx_exc = p["cluster_index_exception"]
    min_group = p["cluster_min_group_entries"]
    materiality = p["materiality_usd"]

    entries = sorted(view.time_entries, key=lambda e: e.entry_id)
    total = len(entries)
    if total == 0:
        return not_evaluated(RULE_ID, "No time entries were found for this period")

    findings: list[Finding] = []
    metrics: list[MetricResult] = []
    scored_results: list[Result] = []

    # ---- M4: late-entry rate ------------------------------------------------ #
    late = [e for e in entries if lag_hours(e) > late_hours]
    m4_value = ratio(len(late), total)
    m4_result = Result.WATCH if Decimal(len(late)) * HUNDRED > late_watch_pct * total else Result.CONSISTENT
    metrics.append(
        MetricResult(
            metric_id="M4",
            label="Late-entry rate",
            value=m4_value,
            numerator=Decimal(len(late)),
            denominator=Decimal(total),
            result=m4_result,
            watch_threshold=f"> {late_watch_pct}% of entries created > {late_hours} h after the work date",
        )
    )
    scored_results.append(m4_result)
    if m4_result == Result.WATCH:
        hist = baseline_history(view, M4_KEY)
        rising = has_baseline(hist) and is_rising(m4_value, hist)
        exposure = money(sum((e.hours * view.loaded_rate(e.employee_id) for e in late), ZERO))
        emps = sorted_unique(e.employee_id for e in late)
        sev, reason = classify_with_reason(
            Result.WATCH,
            exposure=exposure,
            materiality=materiality,
            employees=len(emps),
            trend_rising=rising,
            no_trend_history=not has_baseline(hist),
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
                    f"{len(late)} of {total} time entries were created more than "
                    f"{late_hours} hours after the work date"
                ),
                metric_id="M4",
                metric_value=m4_value,
                metric_numerator=Decimal(len(late)),
                metric_denominator=Decimal(total),
                threshold_tripped="late_rate_watch_pct",
                computed={
                    "kind": "late_entries",
                    "late_entries": len(late),
                    "total_entries": total,
                    "late_rate": m4_value,
                    "late_threshold_hours": late_hours,
                    "employees_n": len(emps),
                    "exposure_usd": exposure,
                    "exposure_basis": "loaded_cost",
                    "entry_costs": entry_costs(view, late),
                    "baseline_confidence": "ok" if has_baseline(hist) else "low",
                },
                exposure_usd=exposure,
                exposure_entry_ids=tuple(sorted(e.entry_id for e in late)),
                employees=emps,
                contracts=contracts_of_entries(view, late),
                evidence=entries_evidence(late),
                fingerprint=f"L-05|late_entries|{view.period}",
            )
        )

    # ---- M5: clustering per direct contract group --------------------------- #
    window_all = [e for e in entries if _in_window(e, start_hour)]
    company_share = ratio(len(window_all), total, "0.000001")
    metrics.append(
        MetricResult(
            metric_id=M5_KEY,
            label="Company-wide share of entries created in the Friday window",
            value=ratio(len(window_all), total),
            numerator=Decimal(len(window_all)),
            denominator=Decimal(total),
            result=Result.NOT_APPLICABLE,
            note="Context value retained as baseline history; not thresholded.",
        )
    )

    history = baseline_history(view, M5_KEY)
    if has_baseline(history):
        baseline = baseline_mean(history)
        baseline_confidence = "ok"
        baseline_source = f"mean of the last {len(history)} periods"
    else:
        baseline = company_share
        baseline_confidence = "low"
        baseline_source = "this run's company-wide window share (fewer than 3 prior periods)"

    groups: dict[str, list[TimeEntry]] = {}
    for e in entries:
        m = view.charge_codes.get(e.charge_code)
        if m is not None and m.contract_id:
            groups.setdefault(m.contract_id, []).append(e)

    for contract_id in sorted(groups):
        g = groups[contract_id]
        if Decimal(len(g)) < min_group:
            metrics.append(
                MetricResult(
                    metric_id=f"M5.{contract_id}",
                    label=f"Cluster index, {contract_id}",
                    value=None,
                    numerator=None,
                    denominator=Decimal(len(g)),
                    result=Result.NOT_EVALUATED,
                    note=f"Group has {plural(len(g), 'entry', 'entries')}, fewer than cluster_min_group_entries ({min_group}); not scored.",
                )
            )
            continue
        if baseline is None or baseline == 0:
            metrics.append(
                MetricResult(
                    metric_id=f"M5.{contract_id}",
                    label=f"Cluster index, {contract_id}",
                    value=None,
                    numerator=None,
                    denominator=Decimal(len(g)),
                    result=Result.NOT_EVALUATED,
                    note="Baseline window share is zero, so the index is undefined; not scored.",
                )
            )
            continue

        flagged = [e for e in g if _in_window(e, start_hour)]
        share = Decimal(len(flagged)) / Decimal(len(g))
        index = ratio(share, baseline, "0.0001")
        if index > idx_exc:
            result = Result.EXCEPTION
        elif index > idx_watch:
            result = Result.WATCH
        else:
            result = Result.CONSISTENT
        scored_results.append(result)
        metrics.append(
            MetricResult(
                metric_id=f"M5.{contract_id}",
                label=f"Cluster index, {contract_id}",
                value=index,
                numerator=Decimal(len(flagged)),
                denominator=Decimal(len(g)),
                result=result,
                watch_threshold=f"> {idx_watch}x baseline",
                exception_threshold=f"> {idx_exc}x baseline",
                note=f"Baseline {baseline} ({baseline_source}).",
            )
        )
        if result == Result.CONSISTENT:
            continue

        team = sorted_unique(e.employee_id for e in g)
        exposure = money(sum((e.hours * view.loaded_rate(e.employee_id) for e in flagged), ZERO))
        sev, reason = classify_with_reason(
            result,
            exposure=exposure,
            materiality=materiality,
            employees=len(team),
            no_trend_history=True,
        )
        if baseline_confidence == "low":
            headline = (
                f"Entry-time clustering on the {contract_id} team at {fmt_x(index)}x the "
                f"current company baseline (low baseline confidence)"
            )
        else:
            headline = (
                f"Entry-time clustering on the {contract_id} team at {fmt_x(index)}x the "
                f"{len(history)}-period baseline"
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
                metric_id="M5",
                metric_value=index,
                metric_numerator=Decimal(len(flagged)),
                metric_denominator=Decimal(len(g)),
                threshold_tripped=(
                    "cluster_index_exception" if result == Result.EXCEPTION else "cluster_index_watch"
                ),
                computed={
                    "kind": "cluster",
                    "contract_id": contract_id,
                    "group_entries": len(g),
                    "window_entries": len(flagged),
                    "window_share": ratio(len(flagged), len(g)),
                    "baseline_share": baseline,
                    "baseline_periods": len(history),
                    "baseline_source": baseline_source,
                    "baseline_confidence": baseline_confidence,
                    "cluster_index": index,
                    "window_start_hour": start_hour,
                    "employees_n": len(team),
                    "exposure_usd": exposure,
                    "exposure_basis": "loaded_cost",
                    "entry_costs": entry_costs(view, flagged),
                },
                exposure_usd=exposure,
                exposure_entry_ids=tuple(sorted(e.entry_id for e in flagged)),
                employees=team,
                contracts=(contract_id,),
                evidence=entries_evidence(flagged),
                fingerprint=f"L-05|{contract_id}",
            )
        )

    overall = max(scored_results, key=lambda r: RESULT_RANK[r])
    return RuleResult(rule_id=RULE_ID, result=overall, metrics=tuple(metrics), findings=tuple(findings))


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Late or reconstructed time entries; entry-time clustering",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "late_threshold_hours": ParamSpec(
                "late_threshold_hours", Decimal("72"), Direction.LOWER_IS_STRICTER,
                Decimal("24"), Decimal("168"), "hours", "Hours after the end of the work date beyond which an entry is late"),
            "late_rate_watch_pct": ParamSpec(
                "late_rate_watch_pct", Decimal("5.0"), Direction.LOWER_IS_STRICTER,
                Decimal("1.0"), Decimal("15.0"), "%", "Late-entry rate above which M4 is a Watch"),
            "cluster_window_start_hour": ParamSpec(
                "cluster_window_start_hour", Decimal("12"), Direction.HIGHER_IS_STRICTER,
                Decimal("8"), Decimal("20"), "hour of day", "Friday entry-time window start; an earlier start widens the window"),
            "cluster_index_watch": ParamSpec(
                "cluster_index_watch", Decimal("1.5"), Direction.LOWER_IS_STRICTER,
                Decimal("1.1"), Decimal("3.0"), "x baseline", "Cluster index above which M5 is a Watch"),
            "cluster_index_exception": ParamSpec(
                "cluster_index_exception", Decimal("2.5"), Direction.LOWER_IS_STRICTER,
                Decimal("1.5"), Decimal("5.0"), "x baseline", "Cluster index above which M5 is an Exception"),
            "cluster_min_group_entries": ParamSpec(
                "cluster_min_group_entries", Decimal("30"), Direction.LOWER_IS_STRICTER,
                Decimal("10"), Decimal("200"), "entries", "Groups with fewer entries than this are not scored"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
