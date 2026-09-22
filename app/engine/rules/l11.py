"""L-11 Provisional billing rate drift (M10). Domain: DCAA cost accounting.

Per indirect pool (fringe, overhead, G&A), the actual rate for the period is
    actual = round_half_up(pool / base, 4)
with the pool and base taken from the period's general-ledger lines (definitions in
engine.metrics.rate_inputs; the base named by the rate agreement's `base_definition`).
The provisional billing rate is the agreement for that pool whose effective dates contain the
period's last day.

    M10 = round_half_up((actual - provisional) / provisional, 4)

Watch when |M10| > rate_drift_watch_pct, Exception when |M10| > rate_drift_exception_pct. One
finding per pool that is an Exception, or a Watch whose exposure reaches materiality (a smaller
Watch is logged with lineage and raises nothing). Exposure is the true-up at provisional rates:
money(|actual - provisional| x base). It is a plain amount (no `exposure_entry_ids`).

Systemic: `periods_beyond_watch` counts the periods, ending at the current one and going back
through CALENDAR-ADJACENT prior months, in which |M10| exceeded the Watch threshold, using the
same provisional rate for the prior months (prior pool/base come from `view.baselines`,
keys `M10.pool.<pool>` / `M10.base.<pool>`). A missing month, or a month with no base, ends the run.
An Exception is High when that count reaches `systemic_periods` (or its exposure reaches
materiality, per FR 6); otherwise Medium.

A pool with no agreement covering the period end, with a zero base, or with no GL lines at all is skipped and named in
`skipped_pools`; if every pool is skipped the rule is Not evaluated, never a pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from engine.canonical import GLLine, RateAgreement, WorkingView
from engine.metrics import (
    BASELINE_WINDOW,
    HUNDRED,
    fmt_pct,
    has_baseline,
    is_rising,
    period_bounds,
    previous_period,
    rate_inputs,
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

RULE_ID = "L-11"
VERSION = "1.1.0"
REQUIRED = ("gl", "rate_data")
AUTHORITIES = ("FAR 52.216-7", "FAR 42.704", "CAS 418 (to verify)")
BASIS = "audit_practice"

POOL_LABELS = {"fringe": "Fringe", "overhead": "Overhead", "ga": "G&A"}

EXPLANATION_TEMPLATE = (
    "For {period_label}, the {pool_label} pool is {pool_amount} against a base of {base_amount}, an actual rate of "
    "{actual_rate_pct} compared with a provisional billing rate of {provisional_rate_pct}. The actual rate is "
    "{abs_m10_pct} {above_below} the provisional rate, so the provisional rate no longer tracks the actual rate. "
    "{streak_sentence}"
    "\n\n"
    "Provisional billing rates are expected to stay close to actual indirect rates under the cited requirements "
    "({authorities}). A gap this large is inconsistent with that expectation: billing at the provisional rate "
    "{direction_text}. A rate revision may be worth raising with the contracting officer."
    "\n\n"
    "{exposure} true-up at provisional rates ({rate_gap_text} x {base_amount}) · {direction_label} · "
    "{pool_label} pool · {streak_short}"
)
RECOMMENDED_ACTION = (
    "Review the pool and base amounts for the period and the entries that drive the change. If the drift is "
    "expected to continue, consider asking the contracting officer to revise the provisional billing rate, and "
    "record the reason for the drift and the decision."
)


def _agreement(view: WorkingView, pool: str, on: date) -> RateAgreement | None:
    """The provisional agreement for `pool` whose [effective_from, effective_to] contains `on`. If several
    do, the one with the latest effective_from wins (ties broken by effective_to, then document name)."""
    cands = [a for a in view.rate_agreements if a.pool == pool and a.effective_from <= on <= a.effective_to]
    if not cands:
        return None
    return max(cands, key=lambda a: (a.effective_from, a.effective_to, a.source_document))


def _m10(actual: Decimal, provisional: Decimal) -> Decimal:
    return ratio(actual - provisional, provisional)


def _beyond(m10: Decimal, watch_pct: Decimal) -> bool:
    return abs(m10) * HUNDRED > watch_pct


def _history(view: WorkingView, pool: str, provisional: Decimal) -> list[dict[str, Any]]:
    """Prior periods (oldest first, at most BASELINE_WINDOW) with their rate and M10 against the SAME
    provisional rate. A retained period with no base cannot yield a rate and is left out."""
    pk, bk = f"M10.pool.{pool}", f"M10.base.{pool}"
    periods = sorted(p for p, m in view.baselines.items() if p < view.period and pk in m and bk in m)
    out: list[dict[str, Any]] = []
    for per in periods[-BASELINE_WINDOW:]:
        base = Decimal(view.baselines[per][bk])
        if base <= 0:
            continue
        rate = ratio(Decimal(view.baselines[per][pk]), base)
        out.append(
            {
                "period": per,
                "rate": rate,
                "m10": _m10(rate, provisional),
                "pool_amount": Decimal(view.baselines[per][pk]),
                "base_amount": base,
            }
        )
    return out


def _streak(period: str, current_m10: Decimal, history: list[dict[str, Any]], watch_pct: Decimal) -> int:
    """Consecutive periods ending at `period` whose |M10| exceeds the Watch threshold. Prior periods must be
    calendar-adjacent months: a gap in the retained history ends the run."""
    if not _beyond(current_m10, watch_pct):
        return 0
    n = 1
    expected = previous_period(period)
    for h in reversed(history):
        if h["period"] != expected or not _beyond(h["m10"], watch_pct):
            break
        n += 1
        expected = previous_period(expected)
    return n


def _change_pct(now: Decimal, before: Decimal | None) -> Decimal | None:
    """Percent change against the prior month, 2 places (context only)."""
    if before is None or before == 0:
        return None
    return (ratio(now - before, before) * HUNDRED).quantize(Decimal("0.01"))


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    if "rate_data" not in view.sources_present:
        return not_evaluated(RULE_ID, "Provisional billing rate data was not uploaded")
    if "gl" not in view.sources_present:
        return not_evaluated(RULE_ID, "Required source not present: gl")

    watch_pct = p["rate_drift_watch_pct"]
    exc_pct = p["rate_drift_exception_pct"]
    systemic_periods = int(p["systemic_periods"])
    materiality = p["materiality_usd"]

    gl = [g for g in view.gl_lines if g.period == view.period]
    if not gl:
        return not_evaluated(RULE_ID, "No general-ledger lines were found for this period")

    _, period_end = period_bounds(view.period)
    inputs = rate_inputs(view, view.period)
    pools = sorted({a.pool for a in view.rate_agreements} | set(inputs.pools))

    skipped: list[dict[str, str]] = []
    scored: list[dict[str, Any]] = []
    for pool in pools:
        agr = _agreement(view, pool, period_end)
        if agr is None:
            skipped.append(
                {"pool": pool, "reason": f"no provisional billing rate agreement covers {period_end.isoformat()}"}
            )
            continue
        base = inputs.base(agr.base_definition)
        if base is None:
            skipped.append({"pool": pool, "reason": f"base definition '{agr.base_definition}' is not recognised"})
            continue
        if base <= 0 or agr.provisional_rate <= 0:
            skipped.append({"pool": pool, "reason": "the rate base is zero for this period"})
            continue
        if pool not in inputs.pools:
            # An agreement exists but the GL carries no indirect lines for this pool: the export is missing the
            # pool's accounts. Scoring that as a 0.0000 actual rate would report a -100% drift and a large
            # "over-billed" exposure out of missing data, the reverse of "missing data is never a result".
            skipped.append({"pool": pool, "reason": "no indirect general-ledger lines were found for this pool"})
            continue
        pool_amount = inputs.pool(pool)
        actual = ratio(pool_amount, base)
        prov = agr.provisional_rate
        m10 = _m10(actual, prov)
        history = _history(view, pool, prov)
        prior = next((h for h in history if h["period"] == previous_period(view.period)), None)
        result = Result.CONSISTENT
        if abs(m10) * HUNDRED > exc_pct:
            result = Result.EXCEPTION
        elif abs(m10) * HUNDRED > watch_pct:
            result = Result.WATCH
        scored.append(
            {
                "pool": pool,
                "agreement": agr,
                "base": base,
                "pool_amount": pool_amount,
                "actual": actual,
                "prov": prov,
                "m10": m10,
                "result": result,
                "history": history,
                "streak": _streak(view.period, m10, history, watch_pct),
                "exposure": money(abs(actual - prov) * base),
                "pool_change_pct": _change_pct(pool_amount, prior["pool_amount"] if prior else None),
                "base_change_pct": _change_pct(base, prior["base_amount"] if prior else None),
            }
        )

    if not scored:
        reasons = "; ".join(f"{s['pool']}: {s['reason']}" for s in skipped)
        return not_evaluated(
            RULE_ID,
            "No pool could be compared with a provisional billing rate"
            + (f" ({reasons})" if reasons else " (no rate agreements were found)"),
        )

    findings: list[Finding] = []
    logged: list[dict] = []
    metrics: list[MetricResult] = []
    raised: list[Result] = []
    for s in scored:
        pool, agr, m10, result = s["pool"], s["agreement"], s["m10"], s["result"]
        label = POOL_LABELS.get(pool, pool)
        under = s["actual"] > s["prov"]
        note = ""
        below_mat = result == Result.WATCH and s["exposure"] < materiality
        if below_mat:
            note = "Watch below materiality: logged with lineage, no finding raised"
            logged.append(
                {
                    "rule_id": RULE_ID,
                    "pool": pool,
                    "period": view.period,
                    "actual_rate": str(s["actual"]),
                    "provisional_rate": str(s["prov"]),
                    "m10": str(m10),
                    "exposure_usd": str(s["exposure"]),
                    "reason": "Watch-level rate drift with exposure below materiality",
                    "source_file": agr.lineage.source_file,
                    "row": agr.lineage.row,
                }
            )
        metrics.append(
            MetricResult(
                metric_id="M10",
                label=f"{label} pool rate drift from the provisional billing rate",
                value=m10,
                numerator=s["actual"] - s["prov"],
                denominator=s["prov"],
                result=result,
                watch_threshold=f"|drift| > {watch_pct}%",
                exception_threshold=f"|drift| > {exc_pct}%",
                note=note,
            )
        )
        if result == Result.CONSISTENT or below_mat:
            continue
        raised.append(result)

        history = s["history"]
        abs_hist = [abs(h["m10"]) for h in history]
        sev, reason = classify_with_reason(
            result,
            exposure=s["exposure"],
            materiality=materiality,
            employees=0,
            periods=s["streak"],
            systemic_periods=systemic_periods,
            trend_rising=has_baseline(abs_hist) and is_rising(abs(m10), abs_hist),
            no_trend_history=not has_baseline(abs_hist),
        )
        pool_lines: list[GLLine] = sorted(
            (g for g in gl if g.cost_type == "indirect" and g.pool == pool),
            key=lambda g: (g.lineage.row, g.account, g.project),
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
                    "role": "pool",
                },
            )
            for g in pool_lines
        ]
        evidence.append(
            EvidenceRef(
                kind="rate_agreement",
                ref_id=f"{pool}/{agr.effective_from.isoformat()}",
                source_file=agr.lineage.source_file,
                sha256=agr.lineage.sha256,
                row=agr.lineage.row,
                detail={
                    "pool": pool,
                    "base_definition": agr.base_definition,
                    "provisional_rate": str(agr.provisional_rate),
                    "effective_from": agr.effective_from.isoformat(),
                    "effective_to": agr.effective_to.isoformat(),
                    "source_document": agr.source_document,
                },
            )
        )
        direction = "under_billed" if under else "over_billed"
        headline = (
            f"{label} rate is {fmt_pct(abs(m10), 1)} {'above' if under else 'below'} its provisional billing rate "
            f"({fmt_pct(s['actual'], 2)} actual vs {fmt_pct(s['prov'], 2)})"
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
                metric_id="M10",
                metric_value=m10,
                metric_numerator=s["actual"] - s["prov"],
                metric_denominator=s["prov"],
                threshold_tripped=(
                    "rate_drift_exception_pct" if result == Result.EXCEPTION else "rate_drift_watch_pct"
                ),
                computed={
                    "kind": "primary",
                    "pool": pool,
                    "pool_label": label,
                    "base_definition": agr.base_definition,
                    "pool_amount": money(s["pool_amount"]),
                    "base_amount": money(s["base"]),
                    "actual_rate": s["actual"],
                    "provisional_rate": s["prov"],
                    "m10": m10,
                    "direction": direction,
                    "periods_beyond_watch": s["streak"],
                    "systemic": s["streak"] >= systemic_periods,
                    "systemic_periods": systemic_periods,
                    "history": [
                        {"period": h["period"], "rate": h["rate"], "m10": h["m10"]} for h in history
                    ],
                    "pool_change_pct": s["pool_change_pct"],
                    "base_change_pct": s["base_change_pct"],
                    "baseline_confidence": "ok" if has_baseline(abs_hist) else "low",
                    "skipped_pools": [dict(x) for x in skipped],
                    "exposure_usd": s["exposure"],
                    "exposure_basis": "rate_true_up",
                },
                exposure_usd=s["exposure"],
                exposure_entry_ids=(),
                employees=(),
                contracts=(),
                evidence=tuple(evidence),
                fingerprint=f"{RULE_ID}|{pool}|{view.period}",
            )
        )

    for x in skipped:
        metrics.append(
            MetricResult(
                metric_id="M10",
                label=f"{POOL_LABELS.get(x['pool'], x['pool'])} pool rate drift from the provisional billing rate",
                value=None,
                numerator=None,
                denominator=None,
                result=Result.NOT_EVALUATED,
                note=f"Skipped: {x['reason']}",
            )
        )

    if Result.EXCEPTION in raised:
        rule_result = Result.EXCEPTION
    elif raised:
        rule_result = Result.WATCH
    else:
        rule_result = Result.CONSISTENT
    return RuleResult(
        rule_id=RULE_ID,
        result=rule_result,
        metrics=tuple(metrics),
        findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Provisional billing rate drift",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "rate_drift_watch_pct": ParamSpec(
                "rate_drift_watch_pct", Decimal("2.0"), Direction.LOWER_IS_STRICTER,
                Decimal("0.5"), Decimal("4.0"), "%",
                "Absolute drift of an actual pool rate from its provisional billing rate above which M10 is a Watch"),
            "rate_drift_exception_pct": ParamSpec(
                "rate_drift_exception_pct", Decimal("5.0"), Direction.LOWER_IS_STRICTER,
                Decimal("2.0"), Decimal("10.0"), "%",
                "Absolute drift above which M10 is an Exception"),
            "systemic_periods": ParamSpec(
                "systemic_periods", Decimal("3"), Direction.LOWER_IS_STRICTER,
                Decimal("2"), Decimal("6"), "periods",
                "Consecutive periods beyond the Watch threshold, ending at the current one, at which an Exception is High"),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        domain=Domain.DCAA_COST_ACCOUNTING.value,
        review_status="unreviewed",
    )
)
