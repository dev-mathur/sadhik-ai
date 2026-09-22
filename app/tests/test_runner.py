"""Runner + manifest + replay, driven by STUB rules over a tiny fixture.

The runner is orchestration only, so these tests use stub evaluators registered by
monkeypatching engine.rules.base._REGISTRY (restored by monkeypatch). Real rule
behaviour is tested by the rule modules' own tests.
"""

from __future__ import annotations

import dataclasses
import json
import random
import shutil
import sys
import types
from decimal import Decimal

import pytest

import engine.rules.base as base
from engine.results import RunBlocked, StatusRollup
from engine.rules.base import (
    EvidenceRef, Finding, MetricResult, Result, RuleResult, RuleSpec, Severity, Tier, all_rules, money,
)
from engine.manifest import replay
from engine.runner import execute_run
from tests.conftest import FIXTURES, SHIPPED_CONFIG, make_dcaa_data, make_stub_spec

MINI = FIXTURES / "mini"
D = Decimal
DCAA = "dcaa_cost_accounting"
CONFIG = SHIPPED_CONFIG.read_text()


def _stub_rollup(rule_results, applicable, *, coverage_floor, is_open=lambda f: True):
    """Stand-in for engine.rollup.rollup when Agent C's module is not present yet."""
    ids = {s.id for s in applicable}
    evaluated = [r for r in rule_results if r.rule_id in ids and r.result != Result.NOT_EVALUATED]
    findings = [f for r in rule_results for f in r.findings]
    ratio = (D(len(evaluated)) / D(len(ids))) if ids else D(0)
    return StatusRollup(
        label=f"{len(findings)} open findings", label_kind="open_findings", open_findings=len(findings),
        high=0, medium=0, low=0, total_exposure_usd=D("0.00"), coverage_evaluated=len(evaluated),
        coverage_applicable=len(ids), coverage_ratio=ratio, coverage_floor=coverage_floor,
        rule_results={r.rule_id: r.result for r in rule_results}, requirement_results={},
        not_evaluated={r.rule_id: r.not_evaluated_reason for r in rule_results if r.result == Result.NOT_EVALUATED},
    )


def _stub_rollup_domains(rule_results, specs, enabled_domains, *, coverage_floor, is_open=lambda f: True):
    """Stand-in for engine.rollup.rollup_domains (Agent C2) until it exists: `_stub_rollup` per enabled domain
    plus an overall that sums them. Like the real one, a result whose spec is not in `specs` (a DQ gate) is
    attributed to its domain through the registry. Only what the runner tests read is modelled."""
    dom_of = {s.id: s.domain for s in specs}
    domain = lambda rid: dom_of.get(rid) or base.get_rule(rid).domain  # noqa: E731
    per = {}
    for d in enabled_domains:
        dom = [s for s in specs if s.domain == d]
        per[d] = dataclasses.replace(
            _stub_rollup([r for r in rule_results if domain(r.rule_id) == d], dom, coverage_floor=coverage_floor), domain=d
        )
    overall = _stub_rollup(
        [r for r in rule_results if domain(r.rule_id) in enabled_domains],
        [s for s in specs if s.domain in enabled_domains],
        coverage_floor=coverage_floor,
    )
    return overall, per


ROLLUP_CALLS: list[dict] = []  # what the runner handed rollup_domains, per run


@pytest.fixture(autouse=True)
def _rollup_available(monkeypatch):
    try:
        import engine.rollup  # noqa: F401
    except ImportError:
        mod = types.ModuleType("engine.rollup")
        mod.rollup = _stub_rollup
        monkeypatch.setitem(sys.modules, "engine.rollup", mod)
    import engine.rollup as er

    real = getattr(er, "rollup_domains", None) or _stub_rollup_domains  # the real one once Agent C2 lands it
    ROLLUP_CALLS.clear()

    def spy(rule_results, specs, enabled_domains, **kw):
        ROLLUP_CALLS.append({"results": [r.rule_id for r in rule_results], "specs": [s.id for s in specs],
                             "enabled": tuple(enabled_domains)})
        return real(rule_results, specs, enabled_domains, **kw)

    monkeypatch.setattr(er, "rollup_domains", spy, raising=False)


def finding(rule_id, sev, exposure, fp, computed=None, employees=()):
    return Finding(
        rule_id=rule_id, rule_version="1.0.0", period="2026-08", authorities=("stub",), basis="audit_practice",
        severity=sev, severity_reason="stub", headline=f"{rule_id} stub finding", metric_id="M0",
        metric_value=D("1"), metric_numerator=None, metric_denominator=None, threshold_tripped="stub",
        computed=computed or {}, exposure_usd=D(exposure), exposure_entry_ids=(), employees=tuple(employees),
        contracts=(), evidence=(EvidenceRef("time_entry", "TE-1", "f.csv", "0" * 64, 2),), fingerprint=fp,
    )


def make_rules(registry) -> list[RuleSpec]:
    """Stub evaluators. Each one echoes what the runner handed it into `computed`."""

    def echo(rule_id, sev, exposure, fp):
        def ev(view, p):
            return RuleResult(
                rule_id=rule_id,
                result=Result.EXCEPTION,
                metrics=(MetricResult("M0", "stub", D(len(view.time_entries)), None, None, Result.EXCEPTION),),
                findings=(finding(rule_id, sev, exposure, fp, {"params": {k: str(v) for k, v in sorted(p.items())},
                                                                "entries": len(view.time_entries)}),),
            )
        return ev

    def clean(rule_id):
        return lambda view, p: RuleResult(rule_id=rule_id, result=Result.CONSISTENT)

    def poisoner(view, p):
        view.time_entries.clear()  # would corrupt any rule sharing this view
        view.baselines.clear()
        return RuleResult(rule_id="L-01", result=Result.CONSISTENT)

    dq = make_stub_spec(registry, "L-05", ("timekeeping", "hris"))  # template for a DQ rule
    dq = RuleSpec(**{**dq.__dict__, "id": "DQ-01", "parameters": {}, "counts_toward_coverage": False,  # like the real DQ-01
                     "evaluate": echo("DQ-01", Severity.MEDIUM, "0.00", "DQ-01|2026-08")})
    def with_eval(spec, ev):
        return RuleSpec(**{**spec.__dict__, "evaluate": ev})

    return [
        with_eval(make_stub_spec(registry, "L-01", ("timekeeping", "hris", "contracts")), poisoner),
        with_eval(make_stub_spec(registry, "L-02", ("timekeeping", "payroll")), echo("L-02", Severity.MEDIUM, "705.60", "L-02|E-1104|2026-08-B")),
        with_eval(make_stub_spec(registry, "L-03", ("payroll", "gl")), clean("L-03")),
        with_eval(make_stub_spec(registry, "L-05", ("timekeeping",)), echo("L-05", Severity.HIGH, "27518.40", "L-05|C-7302")),
        with_eval(make_stub_spec(registry, "L-06", ("time_edits",)), echo("L-06", Severity.LOW, "4199.80", "L-06|2026-08")),
        with_eval(make_stub_spec(registry, "L-09", ("timekeeping", "contracts")), echo("L-09", Severity.HIGH, "5852.40", "L-09|C-7302")),
        with_eval(dataclasses.replace(make_stub_spec(registry, "L-11", ("rate_data",)), domain=DCAA), clean("L-11")),
        dq,
    ]


@pytest.fixture()
def rules(registry, monkeypatch):
    specs = make_rules(registry)
    monkeypatch.setattr(base, "_REGISTRY", {s.id: s for s in specs})
    monkeypatch.setattr(base, "_LOADED", True)  # do not import real rule modules
    return specs


@pytest.fixture()
def data(tmp_path):
    dst = tmp_path / "data"
    shutil.copytree(MINI, dst)
    return dst


def run(data_dir=MINI, config=CONFIG, tier=Tier.CLOSE, **kw):
    return execute_run(data_dir, config, "2026-08", tier, "RUN-T-001", "2026-09-03T14:22:07Z", **kw)


def by_id(out):
    return {r.rule_id: r for r in out.rule_results}


# ---------------------------------------------------------------------------
def test_end_to_end_orchestration(rules):
    assert [r.id for r in all_rules()] == sorted(s.id for s in rules)  # registry monkeypatch is live
    out = run()
    res = by_id(out)
    # rule results are in stable id order regardless of evaluation order
    assert [r.rule_id for r in out.rule_results] == sorted(res)
    # findings: severity high->low, L- before DQ-, exposure desc within a rule, then fingerprint
    assert [f.fingerprint for f in out.findings] == [
        "L-05|C-7302", "L-09|C-7302", "L-02|E-1104|2026-08-B", "DQ-01|2026-08", "L-06|2026-08",
    ]
    assert out.total_exposure_usd == D("38276.20")  # 27518.40+5852.40+705.60+0.00+4199.80: runner used metrics.total_exposure
    # missing source => Not evaluated with a plain reason; never a pass
    assert res["L-11"].result == Result.NOT_EVALUATED
    assert res["L-11"].not_evaluated_reason == "Provisional billing rate data was not uploaded"
    assert res["L-11"].findings == ()
    assert out.rollup.rule_results["L-11"] == Result.NOT_EVALUATED
    # coverage: 7 applicable L- rules at close (DQ- excluded), L-11 not evaluated
    assert (out.rollup.coverage_evaluated, out.rollup.coverage_applicable) == (6, 7)


def test_params_are_resolved_and_materiality_injected(rules):
    out = run()
    p = by_id(out)["L-05"].findings[0].computed["params"]
    assert p["late_threshold_hours"] == "48"  # the shipped config's stricter override
    assert p["cluster_min_group_entries"] == "30"  # registry default
    # basis = sum(hours x loaded rate) = 3487.825 -> 3487.83; x 0.005 = 17.43915 -> 17.44
    assert p["materiality_usd"] == "17.44"
    assert out.manifest["materiality"] == {"basis_usd": "3487.83", "pct": "0.005", "usd": "17.44"}
    assert by_id(out)["L-09"].findings[0].computed["params"]["out_of_pop_hours_tolerance"] == "0"


def test_rules_are_independent_and_order_does_not_matter(rules):
    baseline = run().canonical()
    rng = random.Random(7)
    for _ in range(6):
        shuffled = list(rules)
        rng.shuffle(shuffled)
        assert run(rules=shuffled).canonical() == baseline
    assert run(rules=list(reversed(rules))).canonical() == baseline
    # the L-01 stub wipes the view it is handed; every other rule still saw all 5 entries
    for f in run(rules=list(reversed(rules))).findings:
        assert f.computed["entries"] == 5


def test_runs_are_deterministic_and_executed_at_is_not_part_of_the_result(rules):
    a = execute_run(MINI, CONFIG, "2026-08", Tier.CLOSE, "RUN-A", "2026-09-03T14:22:07Z")
    b = execute_run(MINI, CONFIG, "2026-08", Tier.CLOSE, "RUN-B", "1999-01-01T00:00:00Z")
    c = execute_run(MINI, CONFIG, "2026-08", Tier.CLOSE, "RUN-C")
    assert a.canonical() == b.canonical() == c.canonical()
    assert c.manifest["executed_at"] is None and a.manifest["executed_at"] == "2026-09-03T14:22:07Z"


def test_fast_tier_gates_rules_by_source_and_coverage(rules):
    out = run(tier=Tier.FAST)
    res = by_id(out)
    assert out.tier == Tier.FAST
    for rid in ("L-02", "L-03", "L-11"):
        assert res[rid].result == Result.NOT_EVALUATED and res[rid].findings == ()
    assert "Payroll data is not part of the fast run" in res["L-02"].not_evaluated_reason
    assert res["L-05"].result == Result.EXCEPTION
    assert out.manifest["sources_absent"] == ["gl", "hris", "payroll", "rate_data"]
    # coverage counts only L- rules whose tier is <= the run tier: L-05, L-06, L-09
    assert out.rollup.coverage_applicable == 3 and out.rollup.coverage_evaluated == 3


def test_not_applicable_from_config_is_excluded_from_coverage_with_reason(rules):
    text = CONFIG + '  L-11:\n    status: not_applicable\n    reason: "No provisional billing rate agreement in place"\n'
    out = run(config=text)
    r = by_id(out)["L-11"]
    assert r.result == Result.NOT_APPLICABLE
    assert "No provisional billing rate agreement in place" in r.not_evaluated_reason
    assert out.rollup.coverage_applicable == 6 and out.rollup.coverage_evaluated == 6


def test_disabled_rule_is_not_evaluated_and_stays_in_coverage(rules):
    out = run(config=CONFIG + "  L-06:\n    enabled: false\n")
    r = by_id(out)["L-06"]
    assert r.result == Result.NOT_EVALUATED and "disabled" in r.not_evaluated_reason
    assert (out.rollup.coverage_evaluated, out.rollup.coverage_applicable) == (5, 7)


def test_contract_scope_is_passed_through_as_reserved_keys(rules):
    text = CONFIG.replace(
        "late_threshold_hours: 48     # default 72 -> stricter, accepted\n",
        "late_threshold_hours: 48\n    scope:\n      contract:\n        C-8841: {late_threshold_hours: 36}\n",
    )
    assert text != CONFIG
    p = by_id(run(config=text))["L-05"].findings[0].computed["params"]
    assert p["late_threshold_hours"] == "48" and p["scope:C-8841:late_threshold_hours"] == "36"


def test_rejected_config_blocks_the_run(rules):
    with pytest.raises(RunBlocked) as ei:
        run(config=CONFIG.replace("late_threshold_hours: 48", "late_threshold_hours: 200"))
    assert ei.value.code == "config_rejected"
    assert "line" in ei.value.message and "200" in ei.value.message
    assert [e.code for e in ei.value.errors] == ["exceeds_max"]


def test_unmapped_charge_code_blocks_the_run(rules, data):
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text() + "TE-000006,E-0001,2026-08-06,8.0,ZZ-999,Data Engineer II,2026-08-06T17:00:00,,,\n")
    with pytest.raises(RunBlocked) as ei:
        run(data)
    assert ei.value.code == "unmapped_charge_code"


def test_a_failing_rule_blocks_rather_than_passing(registry, monkeypatch):
    def boom(view, p):
        raise ZeroDivisionError("bad rule")

    spec = make_stub_spec(registry, "L-05", ("timekeeping",), evaluate=boom)
    monkeypatch.setattr(base, "_REGISTRY", {"L-05": spec})
    monkeypatch.setattr(base, "_LOADED", True)
    with pytest.raises(RunBlocked) as ei:
        run()
    assert ei.value.code == "rule_failed" and "L-05" in ei.value.message


# --- manifest ---------------------------------------------------------------
def test_manifest_pins_everything_and_is_json_safe(rules):
    out = run()
    m = out.manifest
    json.dumps(m)  # JSON-safe
    assert (m["run_id"], m["period"], m["tier"], m["executed_at"]) == ("RUN-T-001", "2026-08", "close", "2026-09-03T14:22:07Z")
    assert [i["source"] for i in m["inputs"]] == ["timekeeping", "time_edits", "payroll", "gl", "hris", "contracts"]
    tk = m["inputs"][0]
    assert tk["file"] == "meridian_time_2026-08.csv" and tk["rows"] == 5 and tk["data_as_of"] == "2026-08-31" and len(tk["sha256"]) == 64
    assert m["sources_absent"] == ["rate_data"]
    assert m["mapping_versions"] == {"adp": 2, "bamboo": 1, "quickbooks": 2, "unanet": 3}
    assert m["rule_versions"] == {r.id: "1.0.0" for r in rules}
    import hashlib
    assert m["config_file"] == {"config_version": 7, "sha256": hashlib.sha256(CONFIG.encode()).hexdigest()}
    assert m["floor_registry_version"] == "2026-09-01"
    assert m["declared_schedule"] == {"fast": "weekly", "pay_period": "semi_monthly", "close": "monthly"}
    assert m["canonical_sha256"] == hashlib.sha256(out.canonical().encode()).hexdigest()
    assert {t["source"] for t in m["reference_tables"]} >= {"charge_codes", "loaded_rates", "category_crosswalk"}


# --- replay -----------------------------------------------------------------
def test_replay_is_identical(rules):
    out = run()
    r = replay(out.manifest, MINI, CONFIG, expected_canonical=out.canonical())
    assert r.identical and r.diff == () and r.reason is None and r.findings_compared == 5
    # the manifest alone is enough (it carries the result hash)
    assert replay(out.manifest, MINI, CONFIG).identical


def test_replay_refuses_when_an_input_changed(rules, data):
    out = run(data)
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text().replace("8.0,C8841-DEV", "7.0,C8841-DEV", 1))
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, data, CONFIG, expected_canonical=out.canonical())
    assert ei.value.code == "inputs_changed" and "meridian_time_2026-08.csv" in ei.value.message


def test_replay_refuses_when_a_reference_table_changed(rules, data):
    out = run(data)
    lr = data / "loaded_rates.csv"
    lr.write_text(lr.read_text().replace("96.40", "99.99"))
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, data, CONFIG)
    assert ei.value.code == "inputs_changed" and "loaded_rates.csv" in ei.value.message


def test_replay_refuses_when_an_input_file_is_gone(rules, data):
    out = run(data)
    (data / "adp_payroll_2026-08.csv").unlink()
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, data, CONFIG)
    assert ei.value.code == "inputs_changed"


def test_replay_refuses_when_the_config_changed(rules):
    out = run()
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, MINI, CONFIG.replace("late_threshold_hours: 48", "late_threshold_hours: 60"))
    assert ei.value.code == "inputs_changed" and "config" in ei.value.message


def test_replay_reports_a_diff_when_results_differ(rules, monkeypatch):
    out = run()
    # a rule changes behaviour between the original run and the replay (its version is bumped too)
    bumped = RuleSpec(**{**base._REGISTRY["L-09"].__dict__, "version": "1.1.0",
                         "evaluate": lambda v, p: RuleResult(rule_id="L-09", result=Result.CONSISTENT)})
    monkeypatch.setitem(base._REGISTRY, "L-09", bumped)
    r = replay(out.manifest, MINI, CONFIG, expected_canonical=out.canonical())
    assert not r.identical and r.reason
    assert any("L-09|C-7302 is in the original run but not in the replay" in d for d in r.diff)
    assert any("rule L-09 version 1.0.0 -> 1.1.0" in d for d in r.diff)
    assert any("total_exposure_usd" in d for d in r.diff)
    # same through the manifest hash alone
    assert not replay(out.manifest, MINI, CONFIG).identical


def test_replay_without_any_baseline_does_not_claim_identity(rules):
    out = run()
    m = {k: v for k, v in out.manifest.items() if k != "canonical_sha256"}
    r = replay(m, MINI, CONFIG)
    assert not r.identical and "no baseline" in r.reason


def test_smoke_against_generated_data_if_present():
    from pathlib import Path
    from engine.ingest import load_working_view

    root = Path(__file__).resolve().parents[1] / "data" / "out"
    if not (root / "sources.json").exists():
        pytest.skip("data/out not generated yet")
    r = load_working_view(root, "2026-08", Tier.CLOSE)
    v = r.view
    assert len(v.employees) == 142 and len(v.pay_records) == 284 and len(v.contracts) == 2
    assert 4000 <= len(v.time_entries) <= 4250 and len(v.time_edits) == 148
    assert len(v.baselines) == 6
    if (root / "meridian_rates_2026-08.csv").exists():  # the DCAA-extended dataset
        assert r.sources_absent == frozenset()
        assert [a.pool for a in v.rate_agreements] == ["fringe", "overhead", "ga"]
        assert v.account_categories and set(v.account_categories.values()) == {"labor", "non_labor"}
        assert v.classification_history
    else:
        assert r.sources_absent == frozenset({"rate_data"})


def test_real_rules_run_independently_of_order():
    """Smoke over the real registered rules on the tiny fixture: reasoning about order-independence
    without stubs. (Correctness of each rule is tested elsewhere.)"""
    specs = all_rules()
    if len(specs) < 2:
        pytest.skip("real rules not registered")
    baseline = run(rules=specs)
    assert by_id(baseline)["L-11"].result == Result.NOT_EVALUATED
    rng = random.Random(3)
    for _ in range(4):
        shuffled = list(specs)
        rng.shuffle(shuffled)
        assert run(rules=shuffled).canonical() == baseline.canonical()
    assert replay(baseline.manifest, MINI, CONFIG, expected_canonical=baseline.canonical()).identical


# --- domains ----------------------------------------------------------------
LABOR_ONLY = CONFIG.replace("  - dcaa_cost_accounting\n", "")
NO_DOMAINS_KEY = CONFIG.replace("domains:                         # compliance domains to run; a domain left out is not run at all\n"
                                "  - labor\n  - dcaa_cost_accounting\n", "")


def test_domain_config_variants_are_what_they_claim():
    assert "  - labor\n" in LABOR_ONLY and "dcaa_cost_accounting" not in LABOR_ONLY
    assert "domains:" not in NO_DOMAINS_KEY and "labor" not in NO_DOMAINS_KEY.split("schedule:")[0].split("reason:")[1]


def dcaa_rules(registry, base_specs, calls):
    """The stub set plus L-08, and an L-11 that records that it was executed. Both are dcaa_cost_accounting."""

    def l08(view, p):
        calls.append("L-08")
        return RuleResult(
            rule_id="L-08", result=Result.EXCEPTION,
            findings=(finding("L-08", Severity.HIGH, "3856.00", "L-08|E-0001|2026-08"),),
        )

    def l11(view, p):
        calls.append("L-11")
        return RuleResult(rule_id="L-11", result=Result.CONSISTENT)

    out = []
    for s in base_specs:
        out.append(dataclasses.replace(s, evaluate=l11) if s.id == "L-11" else s)
    spec = dataclasses.replace(make_stub_spec(registry, "L-08", ("timekeeping",)), domain=DCAA, evaluate=l08)
    base._REGISTRY["L-08"] = spec  # the `rules` fixture's per-test registry: rollup attributes a result to its domain through it
    out.append(spec)
    return out


def test_a_disabled_domain_is_not_run_and_is_absent_from_every_result(registry, rules, monkeypatch, dcaa_data):
    calls: list[str] = []
    specs = dcaa_rules(registry, rules, calls)
    out = run(dcaa_data, config=LABOR_ONLY, rules=specs)
    assert calls == []  # neither dcaa rule was executed
    assert sorted(by_id(out)) == ["DQ-01", "L-01", "L-02", "L-03", "L-05", "L-06", "L-09"]
    # absent everywhere: not "Not evaluated", not in the rollup, not in the manifest's rule versions
    assert "L-08" not in out.rollup.rule_results and "L-11" not in out.rollup.rule_results
    assert "L-11" not in out.rollup.not_evaluated
    assert "L-08" not in out.manifest["rule_versions"] and "L-11" not in out.manifest["rule_versions"]
    assert all(f.rule_id not in ("L-08", "L-11") for f in out.findings)
    assert out.manifest["enabled_domains"] == ["labor"]
    assert list(out.domain_rollups) == ["labor"]
    assert out.domain_rollups["labor"].domain == "labor"
    assert ROLLUP_CALLS[-1]["enabled"] == ("labor",)
    assert not any(i in ROLLUP_CALLS[-1]["results"] for i in ("L-08", "L-11"))


def test_the_default_when_domains_is_absent_is_labor_only(registry, rules, dcaa_data):
    calls: list[str] = []
    out = run(dcaa_data, config=NO_DOMAINS_KEY, rules=dcaa_rules(registry, rules, calls))
    assert calls == [] and "L-08" not in by_id(out) and "L-11" not in by_id(out)
    assert out.manifest["enabled_domains"] == ["labor"] and list(out.domain_rollups) == ["labor"]


def test_with_both_domains_enabled_the_dcaa_rules_run_and_appear(registry, rules, dcaa_data):
    calls: list[str] = []
    out = run(dcaa_data, rules=dcaa_rules(registry, rules, calls))
    assert sorted(calls) == ["L-08", "L-11"]
    assert sorted(by_id(out)) == ["DQ-01", "L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11"]
    assert by_id(out)["L-11"].result == Result.CONSISTENT  # rate_data present in this dataset
    assert out.rollup.rule_results["L-08"] == Result.EXCEPTION
    assert out.manifest["enabled_domains"] == ["labor", DCAA]
    assert list(out.domain_rollups) == ["labor", DCAA]
    assert {d: r.domain for d, r in out.domain_rollups.items()} == {"labor": "labor", DCAA: DCAA}
    assert out.rollup.domain == ""  # the overall rollup
    assert ROLLUP_CALLS[-1]["enabled"] == ("labor", DCAA)
    # coverage is per domain: labor 6 rules, dcaa L-08 + L-11
    assert (out.domain_rollups["labor"].coverage_applicable, out.domain_rollups["labor"].coverage_evaluated) == (6, 6)
    assert (out.domain_rollups[DCAA].coverage_applicable, out.domain_rollups[DCAA].coverage_evaluated) == (2, 2)


def test_domains_order_in_config_is_kept_in_the_output(registry, rules, dcaa_data):
    text = CONFIG.replace("  - labor\n  - dcaa_cost_accounting\n", "  - dcaa_cost_accounting\n  - labor\n")
    assert text != CONFIG
    out = run(dcaa_data, config=text, rules=dcaa_rules(registry, rules, []))
    assert out.manifest["enabled_domains"] == [DCAA, "labor"] and list(out.domain_rollups) == [DCAA, "labor"]


def test_coverage_is_per_domain_and_tier_filtered(registry, rules, dcaa_data):
    out = run(dcaa_data, tier=Tier.FAST, rules=dcaa_rules(registry, rules, []))
    # fast tier: labor L-05, L-06, L-09; dcaa L-08 only (L-11 needs the close tier and is not in the denominator)
    lab, dcaa = out.domain_rollups["labor"], out.domain_rollups[DCAA]
    assert (lab.coverage_applicable, lab.coverage_evaluated) == (3, 3)
    assert (dcaa.coverage_applicable, dcaa.coverage_evaluated) == (1, 1)
    assert (out.rollup.coverage_applicable, out.rollup.coverage_evaluated) == (4, 4)
    assert by_id(out)["L-11"].result == Result.NOT_EVALUATED  # still run and reported, just not in coverage yet


def test_not_applicable_is_excluded_from_its_domains_coverage(registry, rules, dcaa_data):
    text = CONFIG + '  L-08:\n    status: not_applicable\n    reason: "No indirect share concern for this customer"\n'
    out = run(dcaa_data, config=text, rules=dcaa_rules(registry, rules, []))
    assert by_id(out)["L-08"].result == Result.NOT_APPLICABLE
    d = out.domain_rollups[DCAA]
    assert (d.coverage_applicable, d.coverage_evaluated) == (1, 1)
    assert d.rule_results["L-08"] == Result.NOT_APPLICABLE and "L-08" not in out.domain_rollups["labor"].rule_results


def test_data_quality_gates_are_not_coverage_and_sort_after_coverage_rules(registry, rules, dcaa_data):
    out = run(dcaa_data, rules=dcaa_rules(registry, rules, []))
    passed = ROLLUP_CALLS[-1]["specs"]
    spec_dq = next(s for s in rules if s.id == "DQ-01")
    assert spec_dq.counts_toward_coverage is False
    # the coverage universe handed to the rollup holds coverage rules only (never the DQ gate)
    assert "DQ-01" not in passed and sorted(passed) == ["L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11"]
    # but the gate's result is still passed, and its finding still belongs to its domain
    assert "DQ-01" in ROLLUP_CALLS[-1]["results"]
    assert out.domain_rollups["labor"].coverage_applicable == 6
    assert out.domain_rollups["labor"].open_findings == 5 and out.domain_rollups[DCAA].open_findings == 1
    assert out.rollup.open_findings == 6 == len(out.findings)
    # sort: severity, then coverage rules before DQ, then id. The original five keep their order; the new
    # high-severity L-08 slots in by id among the highs.
    assert [f.fingerprint for f in out.findings] == [
        "L-05|C-7302", "L-08|E-0001|2026-08", "L-09|C-7302", "L-02|E-1104|2026-08-B", "DQ-01|2026-08", "L-06|2026-08",
    ]


def test_the_original_findings_keep_their_relative_order_when_a_domain_is_added(registry, rules, dcaa_data):
    labor_only = [f.fingerprint for f in run(dcaa_data, config=LABOR_ONLY, rules=dcaa_rules(registry, rules, [])).findings]
    both = [f.fingerprint for f in run(dcaa_data, rules=dcaa_rules(registry, rules, [])).findings]
    assert [f for f in both if not f.startswith("L-08")] == labor_only


def test_a_dq_finding_of_equal_severity_sorts_after_a_dcaa_finding(registry, monkeypatch, dcaa_data):
    """`counts_toward_coverage`, not the id prefix, decides: a dcaa rule (id L-08 here) is a coverage rule."""
    def ev(rid, sev):
        return lambda view, p: RuleResult(rule_id=rid, result=Result.EXCEPTION,
                                          findings=(finding(rid, sev, "10.00", f"{rid}|x"),))
    l08 = dataclasses.replace(make_stub_spec(registry, "L-08", ("timekeeping",)), domain=DCAA, evaluate=ev("L-08", Severity.MEDIUM))
    dq = dataclasses.replace(make_stub_spec(registry, "L-05", ("timekeeping",)), id="DQ-01", parameters={},
                             counts_toward_coverage=False, evaluate=ev("DQ-01", Severity.MEDIUM))
    out = run(dcaa_data, rules=[dq, l08])
    assert [f.rule_id for f in out.findings] == ["L-08", "DQ-01"]


def test_disabled_domain_does_not_block_config_for_its_rules(registry, rules, dcaa_data):
    # parameters for a rule in a disabled domain are still validated (a typo is not hidden) but do not run it
    text = LABOR_ONLY + "  L-11:\n    rate_drift_watch_pct: 1.5\n"
    out = run(dcaa_data, config=text, rules=dcaa_rules(registry, rules, []))
    assert "L-11" not in by_id(out)


# --- manifest and replay with the new tables ----------------------------------
def test_manifest_pins_the_new_tables_the_rate_source_and_the_domains(rules, dcaa_data):
    m = run(dcaa_data).manifest
    json.dumps(m)
    assert [i["source"] for i in m["inputs"]] == ["timekeeping", "time_edits", "payroll", "gl", "hris", "contracts", "rate_data"]
    rate = m["inputs"][-1]
    assert rate["file"] == "meridian_rates_2026-08.csv" and rate["rows"] == 3 and rate["data_as_of"] == "2026-08-31"
    tables = {t["source"]: t for t in m["reference_tables"]}
    assert tables["account_categories"]["file"] == "account_categories.csv" and tables["account_categories"]["rows"] == 3
    assert tables["classification_history"]["file"] == "classification_history.json"
    assert tables["classification_history"]["rows"] == 2 and len(tables["classification_history"]["sha256"]) == 64
    assert m["sources_absent"] == []
    assert m["mapping_versions"]["rate_data"] == 1
    assert m["enabled_domains"] == ["labor", DCAA]


def test_manifest_without_the_optional_tables_lists_neither(rules):
    m = run().manifest  # tests/fixtures/mini has neither
    assert {"account_categories", "classification_history"}.isdisjoint(t["source"] for t in m["reference_tables"])
    assert m["enabled_domains"] == ["labor", DCAA]


def test_replay_reproduces_a_run_that_used_the_new_tables(rules, dcaa_data):
    out = run(dcaa_data)
    r = replay(out.manifest, dcaa_data, CONFIG, expected_canonical=out.canonical())
    assert r.identical and r.diff == () and r.reason is None
    assert replay(out.manifest, dcaa_data, CONFIG).identical
    assert out.canonical() == run(dcaa_data).canonical()


@pytest.mark.parametrize("name", ["account_categories.csv", "classification_history.json", "meridian_rates_2026-08.csv"])
def test_replay_refuses_when_one_of_the_new_files_changed(rules, dcaa_data, name):
    out = run(dcaa_data)
    f = dcaa_data / name
    f.write_text(f.read_text() + ("\n" if name.endswith(".csv") else " "))
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, dcaa_data, CONFIG, expected_canonical=out.canonical())
    assert ei.value.code == "inputs_changed" and name in ei.value.message


def test_replay_refuses_when_a_pinned_new_table_is_gone(rules, dcaa_data):
    out = run(dcaa_data)
    (dcaa_data / "account_categories.csv").unlink()
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, dcaa_data, CONFIG)
    assert ei.value.code == "inputs_changed" and "account_categories.csv is missing" in ei.value.message


def test_replay_refuses_when_an_optional_table_appears_after_the_run(rules, data):
    out = run(data)  # ran without account_categories.csv
    (data / "account_categories.csv").write_text("Account,Category\n6000,labor\n")
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, data, CONFIG)
    assert ei.value.code == "inputs_changed" and "account_categories.csv was not part of the original run" in ei.value.message


def test_replay_refuses_when_the_domains_in_the_config_changed(rules, dcaa_data):
    out = run(dcaa_data)
    with pytest.raises(RunBlocked) as ei:
        replay(out.manifest, dcaa_data, LABOR_ONLY)
    assert ei.value.code == "inputs_changed" and "config" in ei.value.message


def test_replay_of_a_labor_only_run_is_identical(rules, data):
    out = run(data, config=LABOR_ONLY)
    assert out.manifest["enabled_domains"] == ["labor"]
    assert replay(out.manifest, data, LABOR_ONLY, expected_canonical=out.canonical()).identical


def test_rules_see_the_new_view_fields_through_isolated_copies(registry, monkeypatch, dcaa_data):
    """A rule that scribbles on the DCAA additions must not change what another rule sees."""
    seen: dict[str, tuple] = {}

    def vandal(view, p):
        view.rate_agreements.clear()
        view.account_categories.clear()
        view.classification_history["E-0001"].clear()
        view.classification_history.clear()
        return RuleResult(rule_id="L-05", result=Result.CONSISTENT)

    def observer(view, p):
        seen["obs"] = (len(view.rate_agreements), len(view.account_categories), len(view.classification_history["E-0001"]))
        return RuleResult(rule_id="L-09", result=Result.CONSISTENT)

    a = dataclasses.replace(make_stub_spec(registry, "L-05", ("timekeeping",)), evaluate=vandal)
    b = dataclasses.replace(make_stub_spec(registry, "L-09", ("timekeeping",)), evaluate=observer)
    for order in ([a, b], [b, a]):
        run(dcaa_data, rules=order)
        assert seen["obs"] == (3, 3, 2)
