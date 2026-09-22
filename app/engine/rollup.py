"""Four-layer worst-case status rollup (design 6.1).

1. metric result  (computed inside each rule)
2. rule result    (RuleResult.result)
3. requirement    each authority takes the worst result among its mapped rules
4. domain/overall the label: "Incomplete data" | "N open findings" | "No open findings"

"Incomplete data" takes precedence over an otherwise clean result whenever coverage
is below the floor. Coverage counts only rules with `counts_toward_coverage` that are
not Not applicable (data-quality gates such as DQ-01 are excluded). A rule that was Not evaluated never counts as evaluated,
and is never reported as Consistent at any layer.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Callable, Sequence

from engine.metrics import total_exposure
from engine.results import StatusRollup
from engine.rules.base import RESULT_RANK, Finding, Result, RuleResult, RuleSpec, Severity, get_rule, ratio


def _worst(results: list[Result]) -> Result:
    """Worst-case, never letting a Not evaluated rule read as Consistent."""
    active = [r for r in results if r != Result.NOT_APPLICABLE]
    if not active:
        return Result.NOT_APPLICABLE
    top = max(active, key=lambda r: RESULT_RANK[r])
    if top in (Result.EXCEPTION, Result.WATCH):
        return top
    if Result.NOT_EVALUATED in active:
        return Result.NOT_EVALUATED
    return Result.CONSISTENT


def rollup(
    rule_results: list[RuleResult],
    applicable: list[RuleSpec],
    *,
    coverage_floor: Decimal,
    is_open: Callable[[Finding], bool] = lambda f: True,
) -> StatusRollup:
    by_id: dict[str, RuleResult] = {r.rule_id: r for r in rule_results}

    # Coverage: rules with counts_toward_coverage only, minus Not applicable; Not evaluated never counts.
    cov_ids = sorted(
        {
            s.id
            for s in applicable
            if s.counts_toward_coverage
            and not (s.id in by_id and by_id[s.id].result == Result.NOT_APPLICABLE)
        }
    )
    evaluated = [
        i for i in cov_ids if i in by_id and by_id[i].result not in (Result.NOT_EVALUATED, Result.NOT_APPLICABLE)
    ]
    n_app, n_eval = len(cov_ids), len(evaluated)
    cov_ratio = ratio(n_eval, n_app)

    # Layer 3: requirement = authority -> worst of its mapped rules' results.
    by_authority: dict[str, list[Result]] = {}
    for s in applicable:
        res = by_id[s.id].result if s.id in by_id else Result.NOT_EVALUATED
        for a in s.authorities:
            by_authority.setdefault(a, []).append(res)
    requirement_results = {a: _worst(rs) for a, rs in sorted(by_authority.items())}

    open_findings = [f for r in rule_results for f in r.findings if is_open(f)]
    high = sum(1 for f in open_findings if f.severity == Severity.HIGH)
    medium = sum(1 for f in open_findings if f.severity == Severity.MEDIUM)
    low = sum(1 for f in open_findings if f.severity == Severity.LOW)

    if cov_ratio < coverage_floor:
        label, kind = "Incomplete data", "incomplete_data"
    elif open_findings:
        n = len(open_findings)
        label, kind = f"{n} open finding{'s' if n != 1 else ''}", "open_findings"
    else:
        label, kind = "No open findings", "no_open_findings"

    return StatusRollup(
        label=label,
        label_kind=kind,
        open_findings=len(open_findings),
        high=high,
        medium=medium,
        low=low,
        total_exposure_usd=total_exposure(open_findings),
        coverage_evaluated=n_eval,
        coverage_applicable=n_app,
        coverage_ratio=cov_ratio,
        coverage_floor=coverage_floor,
        rule_results={r.rule_id: r.result for r in sorted(rule_results, key=lambda r: r.rule_id)},
        requirement_results=requirement_results,
        not_evaluated={
            r.rule_id: r.not_evaluated_reason
            for r in sorted(rule_results, key=lambda r: r.rule_id)
            if r.result == Result.NOT_EVALUATED
        },
    )


def _domain_of(rule_id: str, spec_domain: dict[str, str]) -> str:
    """Domain of a rule result. `specs` normally names it; a result whose spec the caller left out of the
    applicable list (a rule above the run tier) is looked up in the registry, defaulting to labor."""
    if rule_id in spec_domain:
        return spec_domain[rule_id]
    try:
        return get_rule(rule_id).domain
    except KeyError:
        return "labor"


def rollup_domains(
    rule_results: list[RuleResult],
    specs: list[RuleSpec],
    enabled_domains: Sequence[str],
    *,
    coverage_floor: Decimal,
    is_open: Callable[[Finding], bool] = lambda f: True,
) -> tuple[StatusRollup, dict[str, StatusRollup]]:
    """Per-domain rollups plus the overall rollup across the ENABLED domains.

    Each enabled domain is `rollup()` over that domain's specs and results, with `StatusRollup.domain`
    set. Results and specs of a domain that is not enabled are ignored entirely.

    Overall (`domain=""`): finding counts are summed; total exposure is M13 over ALL open findings of ALL
    enabled domains (so an entry counted by findings in two domains counts once); coverage is summed and its
    ratio recomputed from the sums; rule, requirement and not-evaluated maps are merged (a requirement cited
    in two domains takes the worse result). The label is "Incomplete data" if ANY enabled domain is below
    the coverage floor (it takes precedence), otherwise "N open findings" or "No open findings".
    """
    enabled = list(dict.fromkeys(enabled_domains))
    spec_domain = {s.id: s.domain for s in specs}

    domain_rollups: dict[str, StatusRollup] = {}
    open_all: list[Finding] = []
    for d in enabled:
        d_specs = [s for s in specs if s.domain == d]
        d_results = [r for r in rule_results if _domain_of(r.rule_id, spec_domain) == d]
        domain_rollups[d] = replace(
            rollup(d_results, d_specs, coverage_floor=coverage_floor, is_open=is_open), domain=d
        )
        open_all.extend(f for r in d_results for f in r.findings if is_open(f))

    n_eval = sum(r.coverage_evaluated for r in domain_rollups.values())
    n_app = sum(r.coverage_applicable for r in domain_rollups.values())
    cov_ratio = ratio(n_eval, n_app)
    thin = any(r.coverage_ratio < coverage_floor for r in domain_rollups.values()) or not domain_rollups

    if thin:
        label, kind = "Incomplete data", "incomplete_data"
    elif open_all:
        n = len(open_all)
        label, kind = f"{n} open finding{'s' if n != 1 else ''}", "open_findings"
    else:
        label, kind = "No open findings", "no_open_findings"

    rule_map: dict[str, Result] = {}
    requirements: dict[str, list[Result]] = {}
    not_eval: dict[str, str] = {}
    for r in domain_rollups.values():
        rule_map.update(r.rule_results)
        not_eval.update(r.not_evaluated)
        for a, res in r.requirement_results.items():
            requirements.setdefault(a, []).append(res)

    overall = StatusRollup(
        label=label,
        label_kind=kind,
        open_findings=sum(r.open_findings for r in domain_rollups.values()),
        high=sum(r.high for r in domain_rollups.values()),
        medium=sum(r.medium for r in domain_rollups.values()),
        low=sum(r.low for r in domain_rollups.values()),
        total_exposure_usd=total_exposure(open_all),
        coverage_evaluated=n_eval,
        coverage_applicable=n_app,
        coverage_ratio=cov_ratio,
        coverage_floor=coverage_floor,
        rule_results=dict(sorted(rule_map.items())),
        requirement_results={a: _worst(rs) for a, rs in sorted(requirements.items())},
        not_evaluated=dict(sorted(not_eval.items())),
        domain="",
    )
    return overall, domain_rollups
