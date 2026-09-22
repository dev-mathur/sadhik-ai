"""Run orchestrator (design 5.4, 5.5, 7).

Orchestration ONLY: this module wires config -> ingest -> rules -> rollup. It never
computes a metric, a threshold or a finding itself. Rules run independently: each gets
its own copy of the view and its own params, sees nothing of the others, and the output
is assembled in a deterministic order, so results cannot depend on evaluation order.

Deterministic: no clock, no randomness, no environment. `executed_at` is caller-supplied.
"""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from pathlib import Path
from typing import Sequence

from engine.canonical import WorkingView
from engine.config import resolve_domains, resolve_global, resolve_params, resolve_scoped, validate_config
from engine.floors import FloorRegistry, load_floors
from engine.ingest import load_working_view
from engine.manifest import build_manifest, canonical_hash
from engine.results import RunBlocked, RunOutput
from engine.rules.base import (
    TIER_ORDER,
    TIER_SOURCES,
    Finding,
    Result,
    RuleResult,
    RuleSpec,
    Severity,
    Tier,
    all_rules,
    money,
    not_evaluated,
)

FLOORS_PATH = Path(__file__).resolve().parents[1] / "config" / "floors.yaml"

_SOURCE_LABELS = {
    "timekeeping": "Timekeeping data",
    "time_edits": "Timekeeping edit history",
    "payroll": "Payroll data",
    "gl": "General ledger data",
    "hris": "HRIS roster data",
    "contracts": "Contract data",
    "rate_data": "Provisional billing rate data",
}
_SEVERITY_RANK = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


def _isolated_view(view: WorkingView) -> WorkingView:
    """A private copy for one rule: fresh containers around the (frozen) records, so a
    misbehaving rule cannot change what another rule sees."""
    return dataclasses.replace(
        view,
        employees=list(view.employees),
        time_entries=list(view.time_entries),
        time_edits=list(view.time_edits),
        pay_records=list(view.pay_records),
        gl_lines=list(view.gl_lines),
        contracts=list(view.contracts),
        charge_codes=dict(view.charge_codes),
        category_crosswalk=dict(view.category_crosswalk),
        loaded_rates=dict(view.loaded_rates),
        baselines={p: dict(m) for p, m in view.baselines.items()},
        rate_agreements=list(view.rate_agreements),
        account_categories=dict(view.account_categories),
        classification_history={e: dict(by_period) for e, by_period in view.classification_history.items()},
        account_allowability=dict(view.account_allowability),
        ics_submissions=list(view.ics_submissions),
    )


def _missing_reason(missing: list[str], tier: Tier) -> str:
    """Plain-language reason a required source is unavailable. Never a pass."""
    parts = []
    for s in missing:
        label = _SOURCE_LABELS.get(s, s)
        if s not in TIER_SOURCES[tier]:
            parts.append(f"{label} is not part of the {tier.value.replace('_', '-')} run")
        else:
            parts.append(f"{label} was not uploaded")
    return " and ".join(parts) if len(parts) < 3 else "; ".join(parts)


def _finding_sort_key(f: Finding, counts_toward_coverage: bool) -> tuple:
    # severity high->low, then rule order (coverage rules before data-quality gates, then id), exposure
    # desc, fingerprint. "Coverage rule" is the spec's flag, no longer a guess from the id prefix.
    return (
        _SEVERITY_RANK[f.severity],
        0 if counts_toward_coverage else 1,
        f.rule_id,
        -f.exposure_usd,
        f.fingerprint,
    )


def execute_run(
    data_dir: Path,
    config_text: str,
    period: str,
    tier: Tier,
    run_id: str,
    executed_at: str | None = None,
    *,
    rules: Sequence[RuleSpec] | None = None,
    floors_path: Path | None = None,
) -> RunOutput:
    """Execute one run. Raises RunBlocked on a hard validation failure (rejected config,
    unmapped charge code, missing input, schema drift). `rules` and `floors_path` exist so
    tests can inject stub rules; production callers omit them."""
    # Imported here so config/ingest/manifest remain usable while the rule-side modules load.
    from engine.metrics import labor_cost_basis, total_exposure
    from engine.rollup import rollup_domains

    registry: FloorRegistry = load_floors(floors_path or FLOORS_PATH)
    specs = list(rules) if rules is not None else all_rules()

    cres = validate_config(config_text, registry, specs)
    if not cres.accepted:
        first = "; ".join(
            f"line {e.line}: {e.message}" if e.line else e.message for e in cres.errors[:5]
        )
        exc = RunBlocked("config_rejected", f"The rules config was rejected ({len(cres.errors)} error(s)): {first}")
        exc.errors = cres.errors  # type: ignore[attr-defined]
        raise exc
    config = cres.config
    assert config is not None

    ing = load_working_view(data_dir, period, tier)
    view = ing.view

    # A domain the customer did not enable is not run: its rules are absent from the results below
    # (they are not "Not evaluated", the customer never asked for them).
    enabled = resolve_domains(config)
    specs = [s for s in specs if s.domain in enabled]

    global_params = resolve_global(config, registry)
    basis = labor_cost_basis(view)  # materiality basis (Agent C's definition; the runner only injects it)
    materiality_usd = money(basis * global_params["materiality_pct"])

    results: list[RuleResult] = []
    for spec in specs:  # evaluation order is irrelevant: results are re-sorted below
        results.append(_run_rule(spec, view, ing.sources_present, config, registry, tier, materiality_usd))

    results.sort(key=lambda r: r.rule_id)
    spec_by_id = {s.id: s for s in specs}
    findings = sorted(
        (f for r in results for f in r.findings),
        key=lambda f: _finding_sort_key(f, spec_by_id[f.rule_id].counts_toward_coverage if f.rule_id in spec_by_id else True),
    )
    total = total_exposure(findings)

    # Coverage universe (per domain, inside rollup_domains): rules that count toward coverage, whose tier is
    # at or below this run's tier, and that are not marked not applicable. A rule in scope whose source is
    # missing stays in the denominator, so it can only lower coverage, never raise it. Data-quality gates
    # (counts_toward_coverage False) are not coverage and are not in this list; rollup_domains still
    # attributes their findings to their own domain.
    result_by_id = {r.rule_id: r for r in results}
    applicable = [
        s
        for s in specs
        if s.counts_toward_coverage
        and result_by_id[s.id].result != Result.NOT_APPLICABLE
        and TIER_ORDER[s.tier] <= TIER_ORDER[tier]
    ]
    status, domain_status = rollup_domains(
        results, applicable, enabled, coverage_floor=global_params["coverage_floor"]
    )

    logged = [dict(item) for r in results for item in r.logged_below_materiality]

    out = RunOutput(
        run_id=run_id,
        period=period,
        tier=tier,
        manifest={},
        rule_results=results,
        findings=findings,
        total_exposure_usd=total,
        rollup=status,
        logged_below_materiality=logged,
        domain_rollups=domain_status,
    )
    out.manifest = build_manifest(
        run_id=run_id,
        period=period,
        tier=tier,
        executed_at=executed_at,
        ingest=ing,
        rules=specs,
        config_result=cres,
        registry=registry,
        materiality_basis_usd=basis,
        materiality_pct=global_params["materiality_pct"],
        materiality_usd=materiality_usd,
        canonical_sha256=canonical_hash(out.canonical()),
    )
    return out


def _run_rule(
    spec: RuleSpec,
    view: WorkingView,
    present: frozenset[str],
    config,
    registry: FloorRegistry,
    tier: Tier,
    materiality_usd: Decimal,
) -> RuleResult:
    body = config.rules.get(spec.id, {})
    if body.get("status") == "not_applicable":
        return RuleResult(
            rule_id=spec.id,
            result=Result.NOT_APPLICABLE,
            not_evaluated_reason=f"Marked not applicable by the customer: {body.get('reason', '')}",
        )
    if body.get("enabled") is False:
        return not_evaluated(spec.id, "This rule is disabled in the customer's rules config")
    missing = [s for s in spec.required_sources if s not in present]
    if missing:
        return not_evaluated(spec.id, _missing_reason(missing, tier))

    params = resolve_params(spec, config, registry)
    params.update(resolve_scoped(spec, config, registry))
    params["materiality_usd"] = materiality_usd  # reserved key, injected by the runner
    try:
        return spec.evaluate(_isolated_view(view), params)
    except RunBlocked:
        raise
    except Exception as exc:  # a broken rule must never degrade into a pass
        raise RunBlocked("rule_failed", f"Rule {spec.id} failed: {type(exc).__name__}: {exc}") from exc
