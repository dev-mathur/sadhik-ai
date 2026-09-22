"""C-01 Unallowable cost screening (M15). Domain: DCAA cost accounting.

The confirmed account allowability table (`view.account_allowability`, account -> (class, citation)) says which
GL accounts hold costs the FAR 31.205 cost principles treat as unallowable, and which are conditional. For each
indirect pool the rule totals the current-period GL lines in those accounts:

    unallowable  any amount above `unallowable_tolerance_usd` (floor 0) is an Exception. Exposure is that amount:
                 cost claimed in the pool that must be removed before rates are computed.
    conditional  allowability depends on the facts. Raises a Watch finding only when the amount reaches
                 materiality, otherwise it is logged with its lineage and nothing is raised.

An account missing from the table is treated as allowable (ingest warns). No table at all, or no GL, is Not
evaluated: without a confirmed classification there is nothing to screen against, and that is never a pass.
One finding per pool (fingerprint C-01|<pool>|<period>; a conditional finding is C-01|<pool>|conditional|<period>, so
the two can coexist in one pool). Exposure is a plain amount, not tied to entries.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from engine.canonical import GLLine, WorkingView
from engine.metrics import ZERO, fmt_money
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

RULE_ID = "C-01"
VERSION = "1.0.0"
REQUIRED = ("gl",)
AUTHORITIES = ("FAR 31.205 (cost principles)", "FAR 31.201-6 (accounting for unallowable costs)")
BASIS = "regulatory"
POOL_LABELS = {"fringe": "fringe", "overhead": "overhead", "ga": "G&A"}

EXPLANATION_TEMPLATE = (
    "For {period_label}, {unallowable_amount} sits in the {pool_label} pool in {account_count_text} that the cost "
    "principles treat as expressly unallowable: {accounts_text}. That is {unallowable_share_pct} of the pool "
    "({pool_amount})."
    "\n\n"
    "Costs that the cited principles ({authorities}) make expressly unallowable are expected to be excluded from "
    "indirect pools before rates are computed. Their presence in the pool is inconsistent with that expectation, "
    "and it overstates the pool rate by the amount included."
    "\n\n"
    "{exposure} of unallowable cost in the {pool_label} pool · {unallowable_share_pct} of the pool"
)
RECOMMENDED_ACTION = (
    "Confirm each account with whoever prepares the rates. Remove the unallowable amounts from the pool before rates "
    "are computed or billed, and keep them in their own accounts so they can be identified at audit."
)
CONDITIONAL_TEMPLATE = (
    "For {period_label}, {unallowable_amount} sits in the {pool_label} pool in {account_count_text} whose "
    "allowability depends on the facts: {accounts_text}."
    "\n\n"
    "Whether these costs are allowable turns on how they were incurred under the cited principles ({authorities}). "
    "The amount is large enough to be worth confirming."
    "\n\n"
    "{exposure} of conditionally allowable cost in the {pool_label} pool"
)
CONDITIONAL_ACTION = (
    "Review the underlying transactions. Keep the allowable part in the pool and move any part that is not "
    "allowable out of it."
)


def _line_evidence(g: GLLine, cls: str, cite: str) -> EvidenceRef:
    return EvidenceRef(
        kind="gl_line",
        ref_id=f"{g.account}/{g.project}",
        source_file=g.lineage.source_file,
        sha256=g.lineage.sha256,
        row=g.lineage.row,
        detail={
            "account": g.account,
            "amount": str(g.amount),
            "pool": g.pool or "",
            "allowability": cls,
            "citation": cite,
        },
    )


def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    if "gl" not in view.sources_present:
        return not_evaluated(RULE_ID, "Required source not present: gl")
    if not view.account_allowability:
        return not_evaluated(RULE_ID, "No confirmed account allowability table was provided")
    tolerance = p["unallowable_tolerance_usd"]
    materiality = p["materiality_usd"]

    gl = [g for g in view.gl_lines if g.period == view.period]
    if not gl:
        return not_evaluated(RULE_ID, "No general-ledger lines were found for this period")

    pool_total: dict[str, Decimal] = {}
    unallow: dict[str, list[tuple[GLLine, str]]] = {}
    cond: dict[str, list[tuple[GLLine, str]]] = {}
    for g in gl:
        if g.cost_type != "indirect" or not g.pool:
            continue
        pool_total[g.pool] = pool_total.get(g.pool, ZERO) + g.amount
        cls, cite = view.account_allowability.get(g.account, ("allowable", ""))
        if cls == "unallowable":
            unallow.setdefault(g.pool, []).append((g, cite))
        elif cls == "conditional":
            cond.setdefault(g.pool, []).append((g, cite))

    if not pool_total:
        # Direct lines only: no indirect pool was screened, so "no unallowable cost" would be a claim about nothing.
        return not_evaluated(RULE_ID, "No indirect cost lines were found in the general ledger for this period")

    findings: list[Finding] = []
    logged: list[dict[str, Any]] = []
    metrics: list[MetricResult] = []
    any_exception = any_watch = False

    def build(pool: str, lines: list[tuple[GLLine, str]], cls: str, result: Result) -> Finding:
        amount = money(sum((g.amount for g, _ in lines), ZERO))
        total = pool_total[pool]
        share = ratio(amount, total)
        by_account: dict[str, Decimal] = {}
        for g, _ in lines:
            by_account[g.account] = by_account.get(g.account, ZERO) + g.amount
        cites = {g.account: c for g, c in lines}
        accounts = tuple(f"{a}: {fmt_money(v)} ({cites[a]})" if cites[a] else f"{a}: {fmt_money(v)}"
                         for a, v in sorted(by_account.items()))
        sev, reason = classify_with_reason(
            result, exposure=amount, materiality=materiality, employees=0, no_trend_history=True
        )
        label = POOL_LABELS.get(pool, pool)
        if cls == "unallowable":
            headline = (f"{fmt_money(amount)} of expressly unallowable cost sits in the {label} pool "
                        f"({len(by_account)} account{'s' if len(by_account) != 1 else ''})")
        else:
            headline = (f"{fmt_money(amount)} of conditionally allowable cost sits in the {label} pool "
                        f"({len(by_account)} account{'s' if len(by_account) != 1 else ''})")
        return Finding(
            rule_id=RULE_ID,
            rule_version=VERSION,
            period=view.period,
            authorities=AUTHORITIES,
            basis=BASIS,
            severity=sev,
            severity_reason=reason,
            headline=headline,
            metric_id="M15",
            metric_value=share,
            metric_numerator=amount,
            metric_denominator=total,
            threshold_tripped="any_unallowable_amount" if cls == "unallowable" else "conditional_at_or_above_materiality",
            computed={
                "kind": "primary" if cls == "unallowable" else "conditional",
                "pool": pool,
                "pool_label": label,
                "pool_amount": total,
                "unallowable_amount": amount,
                "unallowable_share": share,
                "account_count": len(by_account),
                "account_count_text": f"{len(by_account)} account{'s' if len(by_account) != 1 else ''}",
                "accounts": accounts,
                "accounts_text": "; ".join(accounts),
                "allowability": cls,
                "exposure_usd": amount,
                "exposure_basis": "unallowable_amount" if cls == "unallowable" else "conditional_amount",
            },
            exposure_usd=amount,
            exposure_entry_ids=(),
            employees=(),
            contracts=(),
            evidence=tuple(_line_evidence(g, cls, c) for g, c in sorted(lines, key=lambda x: x[0].lineage.row)),
            fingerprint=(f"{RULE_ID}|{pool}|{view.period}" if cls == "unallowable"
                         else f"{RULE_ID}|{pool}|conditional|{view.period}"),
        )

    for pool in sorted(pool_total):
        lines = unallow.get(pool, [])
        amount = sum((g.amount for g, _ in lines), ZERO)
        if lines and amount > tolerance:
            findings.append(build(pool, lines, "unallowable", Result.EXCEPTION))
            any_exception = True
        # Conditional cost is judged on its own, whether or not the pool also holds unallowable cost.
        clines = cond.get(pool, [])
        camount = sum((g.amount for g, _ in clines), ZERO)
        if clines and camount >= materiality:
            findings.append(build(pool, clines, "conditional", Result.WATCH))
            any_watch = True
        elif clines:
            for g, cite in clines:
                logged.append({"rule_id": RULE_ID, "account": g.account, "pool": pool, "amount": str(g.amount),
                               "allowability": "conditional", "citation": cite,
                               "reason": "below_materiality", "source_file": g.lineage.source_file,
                               "row": g.lineage.row})

    total_unallowable = sum((sum((g.amount for g, _ in v), ZERO) for v in unallow.values()), ZERO)
    total_pools = sum(pool_total.values(), ZERO)
    result = Result.EXCEPTION if any_exception else (Result.WATCH if any_watch else Result.CONSISTENT)
    metrics.append(
        MetricResult(
            metric_id="M15",
            label="Expressly unallowable cost as a share of the indirect pools",
            value=ratio(total_unallowable, total_pools),
            numerator=money(total_unallowable),
            denominator=money(total_pools),
            result=result,
            exception_threshold=f"any amount above {fmt_money(tolerance)} in an unallowable account",
            note=(f"{len(logged)} conditional line(s) below materiality logged, not raised" if logged else ""),
        )
    )
    return RuleResult(
        rule_id=RULE_ID,
        result=result,
        metrics=tuple(metrics),
        findings=tuple(findings),
        logged_below_materiality=tuple(logged),
    )


SPEC = register(
    RuleSpec(
        id=RULE_ID,
        version=VERSION,
        title="Unallowable cost screening",
        authorities=AUTHORITIES,
        basis=BASIS,
        required_sources=REQUIRED,
        parameters={
            "unallowable_tolerance_usd": ParamSpec(
                "unallowable_tolerance_usd", Decimal("0"), Direction.LOWER_IS_STRICTER, None, None, "USD",
                "Amount in an unallowable account, within an indirect pool, above which the check trips. Zero tolerance."),
        },
        evaluate=evaluate,
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        domain=Domain.DCAA_COST_ACCOUNTING.value,
        review_status="unreviewed",
    )
)
