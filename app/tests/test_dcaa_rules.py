"""DCAA cost-accounting domain: L-11 (indirect rate drift), L-08 (direct/indirect consistency), the L-03
labor-only tie-out, domain-aware rollup and the explanations. Small hand-built WorkingViews, exact arithmetic.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date
from decimal import Decimal

from engine.canonical import ChargeCodeMap, GLLine, RateAgreement, WorkingView
from engine.copy_lint import find_restricted
from engine.explain import explain
from engine.metrics import previous_period, rate_inputs, total_exposure
from engine.rollup import rollup, rollup_domains
from engine.rules.base import (
    Domain,
    Finding,
    Result,
    RuleResult,
    Severity,
    Tier,
    all_rules,
    get_rule,
    money,
)
from engine.severity import classify_with_reason
from tests.test_rules_smoke import ALL_SOURCES, PERIOD, D, entry, lin, pay, run, view, without

DCAA_SOURCES = ALL_SOURCES | {"rate_data"}


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def gl(account: str, amount: str, cost_type: str, pool: str | None = None, project: str = "C-8841", row: int = 2,
       period: str = PERIOD) -> GLLine:
    return GLLine(account, project, period, D(amount), cost_type, pool, lin("qb_gl", row))


def agr(pool: str, base_definition: str, rate: str, frm: str = "2026-01-01", to: str = "2026-12-31",
        row: int = 2) -> RateAgreement:
    return RateAgreement(pool, base_definition, D(rate), date.fromisoformat(frm), date.fromisoformat(to),
                         "FY2026 provisional billing rate schedule", lin("rates", row))


AGREEMENTS = [
    agr("fringe", "direct_labor", "0.2800", row=2),
    agr("overhead", "direct_labor", "0.1800", row=3),
    agr("ga", "total_cost_input", "0.1000", row=4),
]

CATEGORIES = {
    "5010 Direct Labor": "labor",
    "6190 Payroll Accrual Adjustment": "labor",
    "6210 Fringe and Leave Labor": "labor",
    "5410 Subcontractors": "non_labor",
    "6410 Rent and Facilities": "non_labor",
    "6510 Insurance": "non_labor",
}

# G&A history (DCAA_DATA_SPEC 1.4): Feb..Jul rates against a 2.9M base, so M10 = +0.8%, -0.4%, +1.2%, +4.0%, +5.5%, +6.8%
GA_HISTORY = {"2026-02": "0.1008", "2026-03": "0.0996", "2026-04": "0.1012",
              "2026-05": "0.1040", "2026-06": "0.1055", "2026-07": "0.1068"}
FR_HISTORY = {"2026-02": "0.2792", "2026-03": "0.2805", "2026-04": "0.2811",
              "2026-05": "0.2796", "2026-06": "0.2803", "2026-07": "0.2809"}
OH_HISTORY = {"2026-02": "0.1802", "2026-03": "0.1794", "2026-04": "0.1806",
              "2026-05": "0.1797", "2026-06": "0.1791", "2026-07": "0.1788"}


def baselines(ga: dict[str, str] | None = None, skip: tuple[str, ...] = ()) -> dict[str, dict[str, Decimal]]:
    ga = GA_HISTORY if ga is None else ga
    out: dict[str, dict[str, Decimal]] = {}
    for per in sorted(set(ga) | set(FR_HISTORY)):
        if per in skip:
            continue
        m: dict[str, Decimal] = {}
        for pool, series, base in (("ga", ga, D("2900000.00")), ("fringe", FR_HISTORY, D("1700000.00")),
                                   ("overhead", OH_HISTORY, D("1700000.00"))):
            if per in series:
                m[f"M10.base.{pool}"] = base
                m[f"M10.pool.{pool}"] = money(D(series[per]) * base)
        out[per] = m
    return out


def v_rates(ga_rate: str = "0.1080", fringe: str = "281000.00", overhead_nl: str = "176862.00", *,
            history: bool = True, agreements: list[RateAgreement] | None = None, categories: bool = True,
            ga_history: dict[str, str] | None = None, skip: tuple[str, ...] = (), **kw) -> WorkingView:
    """August: DL 1,000,000.00, DNL 500,000.00, fringe pool 281,000.00 (0.2810), overhead pool 179,000.00 (0.1790, of which
    2,138.00 is the labor accrual line and the rest is non-labor), G&A base 1,960,000.00 and G&A pool = ga_rate x base."""
    ga_pool = money(D(ga_rate) * D("1960000.00"))
    lines = [
        gl("5010 Direct Labor", "600000.00", "direct", None, "C-8841", 2),
        gl("5010 Direct Labor", "400000.00", "direct", None, "C-7302", 3),
        gl("5410 Subcontractors", "500000.00", "direct", None, "C-8841", 4),
        gl("6210 Fringe and Leave Labor", fringe, "indirect", "fringe", "INDIRECT", 5),
        gl("6190 Payroll Accrual Adjustment", "2138.00", "indirect", "overhead", "INDIRECT", 6),
        gl("6410 Rent and Facilities", overhead_nl, "indirect", "overhead", "INDIRECT", 7),
        gl("6510 Insurance", str(ga_pool), "indirect", "ga", "INDIRECT", 8),
    ]
    base = dict(
        gl_lines=lines,
        rate_agreements=list(AGREEMENTS) if agreements is None else agreements,
        account_categories=dict(CATEGORIES) if categories else {},
        baselines=baselines(ga_history, skip) if history else {},
        sources_present=DCAA_SOURCES,
    )
    base.update(kw)
    return view(**base)


def ga_finding(res: RuleResult) -> Finding:
    return next(f for f in res.findings if f.fingerprint == "L-11|ga|2026-08")


# --------------------------------------------------------------------------- #
# registry: domains, tiers, coverage flags, citations
# --------------------------------------------------------------------------- #
def test_domains_tiers_and_coverage_flags():
    by = {r.id: r for r in all_rules()}
    assert by["L-08"].domain == by["L-11"].domain == Domain.DCAA_COST_ACCOUNTING.value
    for rid in ("L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01"):
        assert by[rid].domain == Domain.LABOR.value
    assert by["L-08"].tier == Tier.FAST and by["L-08"].required_sources == ("timekeeping",)
    assert by["L-11"].tier == Tier.CLOSE and by["L-11"].required_sources == ("gl", "rate_data")
    assert [r.id for r in all_rules() if not r.counts_toward_coverage] == ["DQ-01"]
    assert by["L-11"].authorities == ("FAR 52.216-7", "FAR 42.704", "CAS 418 (to verify)")
    assert by["L-08"].authorities == ("DFARS 252.242-7006(c)(2)", "CAS 402 (to verify)")
    assert by["L-08"].basis == by["L-11"].basis == "audit_practice"
    assert by["L-08"].review_status == by["L-11"].review_status == "unreviewed"
    assert set(by["L-11"].parameters) == {"rate_drift_watch_pct", "rate_drift_exception_pct", "systemic_periods"}
    assert set(by["L-08"].parameters) == {"indirect_share_shift_pp", "min_excess_hours"}


# --------------------------------------------------------------------------- #
# rate arithmetic
# --------------------------------------------------------------------------- #
def test_rate_inputs_follow_the_spec_formulas():
    ri = rate_inputs(v_rates(), PERIOD)
    assert ri.direct_labor == D("1000000.00") and ri.direct_non_labor == D("500000.00")
    assert ri.pool("fringe") == D("281000.00")
    assert ri.pool("overhead") == D("179000.00")  # labor accrual line AND non-labor lines
    assert ri.base("direct_labor") == D("1000000.00")
    assert ri.base("total_cost_input") == D("1960000.00")  # DL + DNL + fringe + overhead
    assert ri.base("something_else") is None
    assert previous_period("2026-01") == "2025-12" and previous_period("2026-08") == "2026-07"
    # without an account-category table every direct line is labor (the pre-DCAA reading)
    plain = rate_inputs(v_rates(categories=False), PERIOD)
    assert plain.direct_labor == D("1500000.00") and plain.direct_non_labor == D("0")


def test_l11_rate_rounds_half_up_to_four_places():
    # 28,005 / 100,000 = 0.28005 exactly; half-up gives 0.2801 (round-half-even would give 0.2800)
    v = view(
        sources_present=DCAA_SOURCES,
        gl_lines=[gl("5010 Direct Labor", "100000.00", "direct"), gl("6210 Fringe", "28005.00", "indirect", "fringe")],
        rate_agreements=[agr("fringe", "direct_labor", "0.2800")],
    )
    res = run("L-11", v)
    (m,) = res.metrics
    assert m.numerator == D("0.0001") and m.denominator == D("0.2800")
    assert m.value == D("0.0004")  # 0.0001 / 0.28 = 0.000357... -> 0.0004
    assert res.result == Result.CONSISTENT and not res.findings


def test_l11_exposure_rounds_half_up_to_cents():
    # rate 110.06 / 1000.50 = 0.110005 -> 0.1100; true-up 0.0100 x 1000.50 = 10.0050 -> 10.01 (half-up, not 10.00)
    v = view(
        sources_present=DCAA_SOURCES,
        gl_lines=[gl("5010 Direct Labor", "1000.50", "direct"), gl("6110 G&A Labor", "110.06", "indirect", "overhead")],
        rate_agreements=[agr("overhead", "direct_labor", "0.1000")],
    )
    res = run("L-11", v, materiality_usd="1")
    f = res.findings[0]
    assert f.computed["actual_rate"] == D("0.1100") and f.computed["m10"] == D("0.1000")
    assert f.exposure_usd == D("10.01") == f.computed["exposure_usd"]


# --------------------------------------------------------------------------- #
# L-11 finds it
# --------------------------------------------------------------------------- #
def test_l11_finds_the_ga_drift_and_only_that():
    res = run("L-11", v_rates())
    assert res.result == Result.EXCEPTION
    assert [f.fingerprint for f in res.findings] == ["L-11|ga|2026-08"]  # fringe +0.36% and overhead -0.56% raise nothing
    f = res.findings[0]
    assert f.rule_id == "L-11" and f.metric_id == "M10" and f.metric_value == D("0.0800")
    assert f.metric_numerator == D("0.0080") and f.metric_denominator == D("0.1000")
    assert f.exposure_usd == D("15680.00")  # 0.0080 x 1,960,000.00
    assert f.exposure_entry_ids == () and f.employees == () and f.contracts == ()
    assert f.severity == Severity.HIGH and "systemic:4_periods" in f.severity_reason
    assert f.threshold_tripped == "rate_drift_exception_pct"
    assert f.headline == "G&A rate is 8.0% above its provisional billing rate (10.80% actual vs 10.00%)"
    c = f.computed
    assert (c["pool"], c["base_definition"], c["direction"]) == ("ga", "total_cost_input", "under_billed")
    assert (c["pool_amount"], c["base_amount"]) == (D("211680.00"), D("1960000.00"))
    assert (c["actual_rate"], c["provisional_rate"], c["m10"]) == (D("0.1080"), D("0.1000"), D("0.0800"))
    assert c["periods_beyond_watch"] == 4 and c["systemic"] is True and c["exposure_basis"] == "rate_true_up"
    assert [(h["period"], h["rate"], h["m10"]) for h in c["history"]] == [
        ("2026-02", D("0.1008"), D("0.0080")), ("2026-03", D("0.0996"), D("-0.0040")),
        ("2026-04", D("0.1012"), D("0.0120")), ("2026-05", D("0.1040"), D("0.0400")),
        ("2026-06", D("0.1055"), D("0.0550")), ("2026-07", D("0.1068"), D("0.0680")),
    ]
    # context vs the prior month only: pool 309,720.00 -> 211,680.00 (-31.65%), base 2,900,000.00 -> 1,960,000.00 (-32.41%)
    assert c["pool_change_pct"] == D("-31.65") and c["base_change_pct"] == D("-32.41")
    assert c["skipped_pools"] == [] and c["baseline_confidence"] == "ok"
    kinds = [e.kind for e in f.evidence]
    assert kinds == ["gl_line", "rate_agreement"]  # the pool's GL lines, then the agreement
    assert f.evidence[0].row == 8 and f.evidence[0].source_file == "qb_gl.csv"
    assert f.evidence[1].detail["source_document"] == "FY2026 provisional billing rate schedule" and f.evidence[1].row == 4
    # one M10 metric per pool, all three visible
    assert sorted(m.label.split()[0] for m in res.metrics) == ["Fringe", "G&A", "Overhead"]
    assert {m.metric_id for m in res.metrics} == {"M10"}
    by_pool = {m.label.split()[0]: m for m in res.metrics}
    assert by_pool["Fringe"].value == D("0.0036") and by_pool["Fringe"].result == Result.CONSISTENT
    assert by_pool["Overhead"].value == D("-0.0056") and by_pool["Overhead"].result == Result.CONSISTENT
    assert by_pool["G&A"].result == Result.EXCEPTION
    assert not res.logged_below_materiality


def test_l11_over_billed_direction_and_headline():
    res = run("L-11", v_rates(ga_rate="0.0900"))
    f = ga_finding(res)
    assert f.computed["direction"] == "over_billed" and f.metric_value == D("-0.1000")
    assert f.headline == "G&A rate is 10.0% below its provisional billing rate (9.00% actual vs 10.00%)"
    assert f.exposure_usd == D("19600.00")


def test_l11_clean_is_consistent_with_three_metrics():
    res = run("L-11", v_rates(ga_rate="0.1000"))
    assert res.result == Result.CONSISTENT and not res.findings and len(res.metrics) == 3


def test_l11_watch_and_exception_boundaries_are_strictly_greater():
    def at(rate: str) -> RuleResult:
        return run("L-11", v_rates(ga_rate=rate))

    assert at("0.1020").result == Result.CONSISTENT  # M10 exactly 0.0200: not > 2.0%
    watch = at("0.1021")
    assert watch.result == Result.WATCH and ga_finding(watch).threshold_tripped == "rate_drift_watch_pct"
    assert at("0.1050").result == Result.WATCH  # exactly 5.0%: not > 5.0%
    assert at("0.1051").result == Result.EXCEPTION
    assert at("0.0980").result == Result.CONSISTENT  # drift is absolute: -2.0% is not beyond Watch either
    assert at("0.0979").result == Result.WATCH


def test_l11_watch_finding_is_never_high_and_below_materiality_is_logged_only():
    res = run("L-11", v_rates(ga_rate="0.1030"))  # M10 0.03, exposure 0.0030 x 1,960,000.00 = 5,880.00 >= 2,000
    f = ga_finding(res)
    assert res.result == Result.WATCH and f.exposure_usd == D("5880.00")
    assert f.severity == Severity.LOW and f.severity_reason == "watch_stable_or_improving"  # not rising over its history
    quiet = run("L-11", v_rates(ga_rate="0.1030"), materiality_usd="10000")
    assert not quiet.findings and quiet.result == Result.CONSISTENT
    (row,) = quiet.logged_below_materiality
    assert (row["rule_id"], row["pool"], row["m10"], row["exposure_usd"]) == ("L-11", "ga", "0.0300", "5880.00")
    assert row["source_file"] == "rates.csv"
    ga_metric = next(m for m in quiet.metrics if m.label.startswith("G&A"))
    assert ga_metric.result == Result.WATCH and "below materiality" in ga_metric.note
    # an Exception is raised whatever its size
    exc = run("L-11", v_rates(ga_rate="0.1060"), materiality_usd="1000000")
    assert len(exc.findings) == 1 and exc.result == Result.EXCEPTION


def test_l11_watch_with_a_new_high_is_medium():
    res = run("L-11", v_rates(ga_rate="0.1030", ga_history={p: "0.1005" for p in GA_HISTORY}))
    f = ga_finding(res)
    assert f.severity == Severity.MEDIUM and f.severity_reason == "watch_rising_trend"


# --------------------------------------------------------------------------- #
# L-11 systemic vs not
# --------------------------------------------------------------------------- #
def test_l11_systemic_versus_not_systemic():
    big = dict(materiality_usd="1000000")  # take exposure-at-materiality out of the picture
    sys_ = ga_finding(run("L-11", v_rates(), **big))
    assert sys_.computed["periods_beyond_watch"] == 4 and sys_.severity == Severity.HIGH
    assert sys_.severity_reason == "systemic:4_periods"

    # only July (+6.8%) and August beyond Watch: 2 periods < 3
    two = {**GA_HISTORY, "2026-05": "0.1005", "2026-06": "0.1010"}
    f2 = ga_finding(run("L-11", v_rates(ga_history=two), **big))
    assert f2.computed["periods_beyond_watch"] == 2 and f2.computed["systemic"] is False
    assert f2.severity == Severity.MEDIUM and f2.severity_reason == "exception_below_materiality"
    # the threshold is a parameter: 2 periods is systemic at systemic_periods=2, 4 is not at 5
    assert ga_finding(run("L-11", v_rates(ga_history=two), systemic_periods="2", **big)).severity == Severity.HIGH
    assert ga_finding(run("L-11", v_rates(), systemic_periods="5", **big)).severity == Severity.MEDIUM
    assert ga_finding(run("L-11", v_rates(), systemic_periods="4", **big)).severity == Severity.HIGH

    # exposure at or above materiality is High on its own (FR 6), systemic or not
    f3 = ga_finding(run("L-11", v_rates(ga_history=two)))
    assert f3.severity == Severity.HIGH and f3.severity_reason == "exposure_at_or_above_materiality"


def test_l11_streak_needs_calendar_adjacent_periods_and_starts_at_the_current_one():
    big = dict(materiality_usd="1000000")
    # July retained history missing: the run cannot bridge the gap, so only August counts
    f = ga_finding(run("L-11", v_rates(skip=("2026-07",)), **big))
    assert f.computed["periods_beyond_watch"] == 1 and f.severity == Severity.MEDIUM
    # a period back in Watch range ends the run: Jun is inside Watch, so May is not reached
    calm_jun = {**GA_HISTORY, "2026-06": "0.1010"}
    assert ga_finding(run("L-11", v_rates(ga_history=calm_jun), **big)).computed["periods_beyond_watch"] == 2
    # no history at all: streak 1, low baseline confidence
    none = ga_finding(run("L-11", v_rates(history=False), **big))
    assert none.computed["periods_beyond_watch"] == 1 and none.computed["baseline_confidence"] == "low"
    assert none.computed["history"] == [] and none.computed["pool_change_pct"] is None
    # only the six periods before the current one are read
    seven = {"2026-01": "0.1500", **GA_HISTORY}
    hist = ga_finding(run("L-11", v_rates(ga_history=seven), **big)).computed["history"]
    assert [h["period"] for h in hist] == ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]


# --------------------------------------------------------------------------- #
# L-11 agreements, skipped pools, not evaluated
# --------------------------------------------------------------------------- #
def test_l11_not_evaluated_cases_are_never_a_pass():
    absent = run("L-11", without(v_rates(), "rate_data"))
    assert absent.result == Result.NOT_EVALUATED
    assert absent.not_evaluated_reason == "Provisional billing rate data was not uploaded"
    assert run("L-11", without(v_rates(), "gl")).result == Result.NOT_EVALUATED
    assert run("L-11", v_rates(gl_lines=[])).result == Result.NOT_EVALUATED
    # agreements exist but none covers the period's last day (2026-08-31)
    late = [agr(p, b, r, "2026-01-01", "2026-08-30") for p, b, r in
            (("fringe", "direct_labor", "0.28"), ("overhead", "direct_labor", "0.18"), ("ga", "total_cost_input", "0.10"))]
    res = run("L-11", v_rates(agreements=late))
    assert res.result == Result.NOT_EVALUATED and not res.findings
    assert "2026-08-31" in res.not_evaluated_reason
    assert run("L-11", v_rates(agreements=[])).result == Result.NOT_EVALUATED


def test_l11_a_pool_without_an_agreement_is_skipped_with_a_note_not_passed():
    ags = [a for a in AGREEMENTS if a.pool != "ga"]
    res = run("L-11", v_rates(agreements=ags, fringe="290000.00"))  # fringe 0.2900 vs 0.2800: 3.6%, a Watch
    assert res.result == Result.WATCH
    (f,) = res.findings
    assert f.fingerprint == "L-11|fringe|2026-08"
    assert f.computed["skipped_pools"] == [
        {"pool": "ga", "reason": "no provisional billing rate agreement covers 2026-08-31"}]
    ga_metric = next(m for m in res.metrics if m.label.startswith("G&A"))
    assert ga_metric.result == Result.NOT_EVALUATED and ga_metric.value is None and "Skipped" in ga_metric.note


def test_l11_picks_the_agreement_in_force_on_the_last_day():
    future = agr("ga", "total_cost_input", "0.0500", "2026-09-01", "2026-12-31", row=9)  # not yet effective
    ended = agr("ga", "total_cost_input", "0.0500", "2026-01-01", "2026-07-31", row=10)  # already ended
    same = run("L-11", v_rates(agreements=[*AGREEMENTS, future, ended]))
    assert ga_finding(same).computed["provisional_rate"] == D("0.1000")
    # a revised rate effective August 1 supersedes the January schedule for August
    revised = agr("ga", "total_cost_input", "0.1080", "2026-08-01", "2026-12-31", row=11)
    res = run("L-11", v_rates(agreements=[*AGREEMENTS, revised]))
    assert not [f for f in res.findings if f.computed["pool"] == "ga"]
    assert res.result == Result.CONSISTENT


def test_l11_a_pool_with_an_agreement_but_no_gl_lines_is_skipped_never_scored_as_minus_100_percent():
    """Missing pool accounts in the GL export is missing data, not a 0.0000 actual rate (a -100% Exception)."""
    lines = [
        gl("5010 Direct Labor", "600000.00", "direct", None, "C-8841", 2),
        gl("5010 Direct Labor", "400000.00", "direct", None, "C-7302", 3),
        gl("5410 Subcontractors", "500000.00", "direct", None, "C-8841", 4),
        gl("6210 Fringe and Leave Labor", "281000.00", "indirect", "fringe", "INDIRECT", 5),
        gl("6190 Payroll Accrual Adjustment", "2138.00", "indirect", "overhead", "INDIRECT", 6),
        gl("6410 Rent and Facilities", "176862.00", "indirect", "overhead", "INDIRECT", 7),
    ]  # the G&A pool has an agreement but NO lines
    res = run("L-11", v_rates(gl_lines=lines))
    assert not any(f.fingerprint.startswith("L-11|ga") for f in res.findings)
    assert res.result == Result.CONSISTENT  # fringe 0.2810 and overhead 0.1790 are inside Watch
    ga_metric = next(m for m in res.metrics if m.label.startswith("G&A"))
    assert ga_metric.result == Result.NOT_EVALUATED and ga_metric.value is None and "Skipped" in ga_metric.note
    # if that was the ONLY pool with an agreement, the rule is Not evaluated, never a pass
    only_ga = [a for a in AGREEMENTS if a.pool == "ga"]
    assert run("L-11", v_rates(gl_lines=lines, agreements=only_ga)).result == Result.NOT_EVALUATED


def test_l11_zero_base_is_skipped():
    res = run("L-11", v_rates(gl_lines=[gl("6510 Insurance", "1000.00", "indirect", "ga")]))
    assert res.result == Result.NOT_EVALUATED  # no DL / DNL: every pool's base is zero or unknown


def test_l11_without_account_categories_every_direct_line_is_labor():
    # fringe base becomes DL + DNL = 1,500,000.00 and the fringe pool 281,000.00 reads 0.1873: an Exception
    res = run("L-11", v_rates(categories=False))
    fr = next(f for f in res.findings if f.computed["pool"] == "fringe")
    assert fr.computed["base_amount"] == D("1500000.00") and fr.computed["actual_rate"] == D("0.1873")


def test_l11_is_order_independent_and_pure():
    v = v_rates()
    shuffled = replace(v, gl_lines=random.Random(7).sample(v.gl_lines, len(v.gl_lines)),
                       rate_agreements=list(reversed(v.rate_agreements)))
    assert run("L-11", shuffled) == run("L-11", v) == run("L-11", v)


# --------------------------------------------------------------------------- #
# L-08
# --------------------------------------------------------------------------- #
L08_CODES = {
    "C8841-DEV": ChargeCodeMap("C8841-DEV", "C-8841", "direct", None),
    "OH-100": ChargeCodeMap("OH-100", None, "indirect", "overhead"),
    "GA-200": ChargeCodeMap("GA-200", None, "indirect", "ga"),
    "FR-050": ChargeCodeMap("FR-050", None, "indirect", "fringe"),
}
HIST_PERIODS = ("2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07")


def hist(direct: str, indirect: str, n: int = 6) -> dict[str, tuple[Decimal, Decimal]]:
    return {p: (D(direct), D(indirect)) for p in HIST_PERIODS[-n:]}


def v_l08(direct: str = "160.0", indirect: str = "40.0", history=None, extra=(), eid: str = "E-1") -> WorkingView:
    """One employee: `direct` hours on a contract code and `indirect` hours on OH-100, one entry each (plus `extra`
    (code, hours) pairs). Baseline default: 190.0 direct + 10.0 indirect every month (share 0.05)."""
    ents = [
        entry(eid, f"{eid}-D", "2026-08-03", direct, "C8841-DEV", "n/a", "2026-08-03T17:00:00"),
        entry(eid, f"{eid}-I", "2026-08-04", indirect, "OH-100", "n/a", "2026-08-04T17:00:00"),
    ]
    for i, (code, hours) in enumerate(extra):
        ents.append(entry(eid, f"{eid}-X{i}", "2026-08-05", hours, code, "n/a", "2026-08-05T17:00:00"))
    return view(
        time_entries=ents,
        charge_codes=dict(L08_CODES),
        loaded_rates={eid: D("90.00")},
        classification_history={eid: hist("190.0", "10.0")} if history is None else history,
        sources_present=frozenset({"timekeeping", "time_edits", "contracts"}),
    )


def test_l08_finds_the_shift_with_exact_figures():
    res = run("L-08", v_l08("160.0", "40.0"))  # share 0.20 vs 0.05: +15.0 pp; 40.0 vs 10.0: +30.0 h
    assert res.result == Result.EXCEPTION
    (f,) = res.findings
    assert f.fingerprint == "L-08|E-1|2026-08" and f.metric_id == "M9" and f.employees == ("E-1",)
    assert f.exposure_usd == D("2700.00") and f.exposure_entry_ids == () and f.contracts == ()  # 30.0 h x 90.00
    assert f.severity == Severity.HIGH and f.severity_reason == "integrity_failure:indirect_share_up_15.0_pp"
    assert f.metric_value == D("0.1500") and f.metric_numerator == D("40.0") and f.metric_denominator == D("200.0")
    c = f.computed
    assert (c["direct_hours"], c["indirect_hours"]) == (D("160.0"), D("40.0"))
    assert (c["share_now"], c["baseline_share"], c["share_shift_pp"]) == (D("0.2000"), D("0.0500"), D("15.0"))
    assert (c["baseline_indirect_hours"], c["excess_hours"], c["history_periods"]) == (D("10.0"), D("30.0"), 6)
    assert c["exposure_basis"] == "loaded_cost" and c["exposure_usd"] == D("2700.00")
    assert [e.kind for e in f.evidence] == ["time_entry", "history"]
    assert f.evidence[0].ref_id == "E-1-I"  # the indirect-coded entry, not the direct one
    h = f.evidence[1]
    assert h.ref_id == "E-1" and h.detail["periods"] == list(HIST_PERIODS) and h.detail["baseline_share"] == "0.0500"
    assert res.metrics[0].metric_id == "M9" and res.metrics[0].numerator == 1 and res.metrics[0].denominator == 1
    assert "E-1" in f.headline and "15.0 percentage points" in f.headline


def test_l08_share_boundary_is_15_point_0_pp_not_14_point_9():
    assert len(run("L-08", v_l08("160.0", "40.0")).findings) == 1  # 0.2000 - 0.0500 = 0.1500 exactly: flagged
    just_under = run("L-08", v_l08("160.2", "39.8"))  # 0.1990 - 0.0500 = 0.1490: 14.9 pp
    assert just_under.result == Result.CONSISTENT and not just_under.findings
    # the parameter moves the line
    assert len(run("L-08", v_l08("160.2", "39.8"), indirect_share_shift_pp="14.9").findings) == 1
    assert not run("L-08", v_l08("160.0", "40.0"), indirect_share_shift_pp="15.1").findings


def test_l08_excess_hours_boundary_is_16_point_0_h_not_15_point_9():
    kw = dict(indirect_share_shift_pp="5")  # isolate the hours test
    assert len(run("L-08", v_l08("174.0", "26.0"), **kw).findings) == 1  # 26.0 - 10.0 = 16.0 h: flagged
    under = run("L-08", v_l08("174.1", "25.9"), **kw)  # 15.9 h
    assert under.result == Result.CONSISTENT and not under.findings
    assert len(run("L-08", v_l08("174.1", "25.9"), indirect_share_shift_pp="5", min_excess_hours="15.9").findings) == 1


def test_l08_needs_both_conditions():
    # share up 20 pp but only 8 h above baseline: not flagged
    small = v_l08("30.0", "10.0", history={p: (D("38.0"), D("2.0")) for p in HIST_PERIODS})
    assert not run("L-08", small).findings
    # 20 h above baseline but the share moved less than 15 pp (large totals)
    big = v_l08("1000.0", "120.0", history={p: (D("1000.0"), D("100.0")) for p in HIST_PERIODS})
    assert not run("L-08", big).findings


def test_l08_charge_code_map_defines_direct_and_indirect():
    # GA-200 counts as indirect; FR-050 (fringe pool) counts on neither side
    with_ga = run("L-08", v_l08("160.0", "20.0", extra=[("GA-200", "20.0")]))
    assert with_ga.findings[0].computed["indirect_hours"] == D("40.0")
    assert {e.ref_id for e in with_ga.findings[0].evidence if e.kind == "time_entry"} == {"E-1-I", "E-1-X0"}
    with_fringe = run("L-08", v_l08("160.0", "40.0", extra=[("FR-050", "80.0")]))
    c = with_fringe.findings[0].computed
    assert (c["direct_hours"], c["indirect_hours"], c["share_now"]) == (D("160.0"), D("40.0"), D("0.2000"))
    # an unmapped code is not classified either way
    unmapped = run("L-08", v_l08("160.0", "40.0", extra=[("ZZ-999", "80.0")]))
    assert unmapped.findings[0].computed["direct_hours"] == D("160.0")


def test_l08_insufficient_history_is_skipped_and_logged():
    v = v_l08("160.0", "40.0")
    v.time_entries += [
        entry("E-2", "E-2-D", "2026-08-03", "100.0", "C8841-DEV", "n/a", "2026-08-03T17:00:00"),
        entry("E-2", "E-2-I", "2026-08-04", "100.0", "OH-100", "n/a", "2026-08-04T17:00:00"),
        entry("E-3", "E-3-I", "2026-08-04", "100.0", "OH-100", "n/a", "2026-08-04T17:00:00"),
    ]
    v.classification_history["E-2"] = hist("190.0", "10.0", n=2)  # only 2 periods: not scored (3 is the minimum)
    # E-3 has no history at all
    res = run("L-08", v)
    assert [f.employees for f in res.findings] == [("E-1",)]
    skipped = {row["employee_id"]: row for row in res.logged_below_materiality}
    assert set(skipped) == {"E-2", "E-3"}
    assert skipped["E-2"]["reason"] == skipped["E-3"]["reason"] == "insufficient_history"
    assert (skipped["E-2"]["history_periods"], skipped["E-3"]["history_periods"]) == (2, 0)
    assert "2 employees not scored" in res.metrics[0].note
    # exactly 3 periods of history is enough
    v.classification_history["E-2"] = hist("190.0", "10.0", n=3)
    res3 = run("L-08", v)
    assert {f.employees[0] for f in res3.findings} == {"E-1", "E-2"}


def test_l08_history_window_is_the_latest_six_periods_before_the_current_one():
    h = {"2026-01": (D("10.0"), D("190.0")), **hist("190.0", "10.0"), "2026-08": (D("0.0"), D("500.0"))}
    res = run("L-08", v_l08("160.0", "40.0", history={"E-1": h}))
    c = res.findings[0].computed
    assert c["history_periods"] == 6 and c["baseline_indirect_hours"] == D("10.0")


def test_l08_clean_and_not_evaluated():
    clean = run("L-08", v_l08("190.0", "10.0"))
    assert clean.result == Result.CONSISTENT and not clean.findings
    assert clean.metrics[0].value == D("0.0000") and clean.metrics[0].denominator == 1
    assert run("L-08", without(v_l08(), "timekeeping")).result == Result.NOT_EVALUATED
    no_hist = run("L-08", v_l08(history={}))
    assert no_hist.result == Result.NOT_EVALUATED and not no_hist.findings  # no baseline is never a pass
    only_short = run("L-08", v_l08(history={"E-1": hist("190.0", "10.0", n=2)}))
    assert only_short.result == Result.NOT_EVALUATED and "3 prior periods" in only_short.not_evaluated_reason


def test_l08_is_order_independent():
    v = v_l08("160.0", "40.0", extra=[("GA-200", "5.0")])
    shuffled = replace(v, time_entries=list(reversed(v.time_entries)))
    assert run("L-08", shuffled) == run("L-08", v)


def test_l08_exposure_rounds_half_up_and_is_a_plain_amount_in_m13():
    # excess 30.5 h x 90.05 = 2746.525 -> 2746.53
    v = v_l08("159.5", "40.5", history={"E-1": {p: (D("190.0"), D("10.0")) for p in HIST_PERIODS}})
    v.loaded_rates["E-1"] = D("90.05")
    f = run("L-08", v).findings[0]
    assert f.exposure_usd == D("2746.53")
    f11 = ga_finding(run("L-11", v_rates()))
    assert total_exposure([f, f11]) == D("2746.53") + D("15680.00")


# --------------------------------------------------------------------------- #
# L-03 labor-only tie-out
# --------------------------------------------------------------------------- #
def v_l03_dcaa(labor: str, categories: bool) -> WorkingView:
    return view(
        pay_records=[pay("E-1", "2026-08-A", "80.0", "1000000.00")],
        gl_lines=[
            gl("5010 Direct Labor", labor, "direct", None, "C-8841", 2),
            gl("5410 Subcontractors", "500000.00", "direct", None, "C-8841", 3),
            gl("6510 Insurance", "211680.00", "indirect", "ga", "INDIRECT", 4),
        ],
        account_categories={"5010 Direct Labor": "labor", "5410 Subcontractors": "non_labor",
                            "6510 Insurance": "non_labor"} if categories else {},
    )


def test_l03_sums_labor_accounts_only_when_categories_exist():
    ok = run("L-03", v_l03_dcaa("1001100.00", categories=True))  # 0.11% on labor only
    assert ok.result == Result.CONSISTENT and not ok.findings
    assert ok.metrics[0].numerator == D("1100.00")
    # the identical GL with no category table: every line counts, exactly the old tie-out
    old = run("L-03", v_l03_dcaa("1001100.00", categories=False))
    assert old.result == Result.EXCEPTION
    assert old.findings[0].computed["gl_total"] == D("1712780.00")


def test_l03_finding_evidence_lists_only_labor_lines_and_an_unlisted_account_counts_as_labor():
    v = v_l03_dcaa("1011000.00", categories=True)  # 1.1% : Watch
    res = run("L-03", v)
    f = res.findings[0]
    assert res.result == Result.WATCH and f.computed["gl_total"] == D("1011000.00") and f.exposure_usd == D("11000.00")
    assert [e.ref_id for e in f.evidence if e.kind == "gl_line"] == ["5010 Direct Labor/C-8841"]
    # an account missing from a NON-empty table is treated as labor
    v.gl_lines.append(gl("9999 Unlisted", "1.00", "direct", None, "C-8841", 5))
    assert run("L-03", v).findings[0].computed["gl_total"] == D("1011001.00")


# --------------------------------------------------------------------------- #
# severity.classify_with_reason: systemic_periods
# --------------------------------------------------------------------------- #
def test_classify_systemic_periods_parameter_defaults_to_three():
    kw = dict(exposure=D("1"), materiality=D("1000"), employees=0)
    assert classify_with_reason(Result.EXCEPTION, periods=3, **kw)[0] == Severity.HIGH
    assert classify_with_reason(Result.EXCEPTION, periods=2, **kw)[0] == Severity.MEDIUM
    assert classify_with_reason(Result.EXCEPTION, periods=2, systemic_periods=2, **kw) == (Severity.HIGH, "systemic:2_periods")
    assert classify_with_reason(Result.EXCEPTION, periods=4, systemic_periods=5, **kw)[0] == Severity.MEDIUM
    # a Watch is never High, however long the run
    assert classify_with_reason(Result.WATCH, periods=9, systemic_periods=2, **kw)[0] == Severity.LOW


# --------------------------------------------------------------------------- #
# rollup_domains
# --------------------------------------------------------------------------- #
LABOR = Domain.LABOR.value
DCAA = Domain.DCAA_COST_ACCOUNTING.value
FLOOR = D("0.85")


def _consistent(*, skip: tuple[str, ...] = (), not_evaluated: tuple[str, ...] = ()) -> list[RuleResult]:
    out = []
    for s in all_rules():
        if s.id in skip:
            continue
        if s.id in not_evaluated:
            out.append(RuleResult(s.id, Result.NOT_EVALUATED, not_evaluated_reason=f"{s.id} lacks its source"))
        else:
            out.append(RuleResult(s.id, Result.CONSISTENT))
    return out


def test_rollup_domains_reports_coverage_per_domain_and_summed():
    overall, doms = rollup_domains(_consistent(), all_rules(), [LABOR, DCAA], coverage_floor=FLOOR)
    assert list(doms) == [LABOR, DCAA]
    assert (doms[LABOR].coverage_evaluated, doms[LABOR].coverage_applicable) == (6, 6)  # DQ-01 not counted
    assert (doms[DCAA].coverage_evaluated, doms[DCAA].coverage_applicable) == (5, 5)  # L-08, L-11, C-01, C-02, C-03
    assert doms[LABOR].domain == LABOR and doms[DCAA].domain == DCAA and overall.domain == ""
    assert (overall.coverage_evaluated, overall.coverage_applicable, overall.coverage_ratio) == (11, 11, D("1.0000"))
    assert overall.label == "No open findings" and overall.label_kind == "no_open_findings"
    assert set(overall.rule_results) == {r.id for r in all_rules()}
    assert overall.rule_results["DQ-01"] == Result.CONSISTENT  # results still reported, only coverage excludes them


def test_overall_is_incomplete_data_when_one_domain_is_thin_even_with_open_findings():
    f09 = run("L-09", view(time_entries=[
        entry("E-1", "TE-1", "2026-08-17", "8.0", "C7302-DEV", "n/a", "2026-08-21T17:00:00")], loaded_rates={"E-1": D("94.50")}
    )).findings[0]
    rr = _consistent(not_evaluated=("L-11",))
    rr = [RuleResult("L-09", Result.EXCEPTION, findings=(f09,)) if r.rule_id == "L-09" else r for r in rr]
    overall, doms = rollup_domains(rr, all_rules(), [LABOR, DCAA], coverage_floor=FLOOR)
    assert doms[LABOR].coverage_ratio == D("1.0000") and doms[LABOR].label == "1 open finding"
    assert doms[DCAA].coverage_ratio == D("0.8000") and doms[DCAA].label == "Incomplete data"  # 4 of 5, below 0.85
    assert overall.label == "Incomplete data" and overall.label_kind == "incomplete_data"  # precedence over the finding
    # The sharper point: the OVERALL ratio (10 of 11 = 0.9091) is above the floor, yet the label is still
    # Incomplete data, because one enabled domain is below it. A healthy total cannot hide a thin domain.
    assert (overall.coverage_evaluated, overall.coverage_applicable, overall.coverage_ratio) == (10, 11, D("0.9091"))
    assert overall.coverage_ratio >= FLOOR
    assert overall.open_findings == 1 and overall.high == 1
    assert overall.not_evaluated == {"L-11": "L-11 lacks its source"}
    assert overall.rule_results["L-11"] == Result.NOT_EVALUATED
    # the requirement L-11 cites is not reported as Consistent
    assert overall.requirement_results["FAR 42.704"] == Result.NOT_EVALUATED
    # ... and a labor-only rollup of the same run is unaffected by the thin dcaa domain
    only, only_doms = rollup_domains(rr, all_rules(), [LABOR], coverage_floor=FLOOR)
    assert only.label == "1 open finding" and list(only_doms) == [LABOR]


def test_overall_label_counts_findings_from_every_enabled_domain():
    f11 = ga_finding(run("L-11", v_rates()))
    f08 = run("L-08", v_l08("160.0", "40.0")).findings[0]
    rr = [r if r.rule_id not in ("L-11", "L-08") else
          RuleResult(r.rule_id, Result.EXCEPTION, findings=(f11,) if r.rule_id == "L-11" else (f08,)) for r in _consistent()]
    overall, doms = rollup_domains(rr, all_rules(), [LABOR, DCAA], coverage_floor=FLOOR)
    assert doms[LABOR].open_findings == 0 and doms[DCAA].open_findings == 2
    assert (overall.open_findings, overall.high, overall.medium, overall.low) == (2, 2, 0, 0)
    assert overall.label == "2 open findings"
    assert overall.total_exposure_usd == D("15680.00") + D("2700.00")
    # is_open flows through to every domain
    closed, closed_doms = rollup_domains(rr, all_rules(), [LABOR, DCAA], coverage_floor=FLOOR, is_open=lambda f: False)
    assert closed.label == "No open findings" and closed.total_exposure_usd == D("0.00") and closed_doms[DCAA].open_findings == 0


def test_m13_deduplicates_across_domains():
    labor = run("L-09", view(time_entries=[
        entry("E-1", "TE-1", "2026-08-17", "8.0", "C7302-DEV", "n/a", "2026-08-21T17:00:00"),
        entry("E-1", "TE-2", "2026-08-18", "8.0", "C7302-DEV", "n/a", "2026-08-21T17:00:00"),
    ], loaded_rates={"E-1": D("100.00")})).findings[0]
    assert labor.exposure_usd == D("1600.00") and len(labor.exposure_entry_ids) == 2
    # a finding in the OTHER domain that prices the very same two entries
    twin = replace(labor, rule_id="L-08", fingerprint="L-08|E-1|2026-08")
    plain = ga_finding(run("L-11", v_rates()))
    rr = [
        RuleResult("L-09", Result.EXCEPTION, findings=(labor,)),
        RuleResult("L-08", Result.EXCEPTION, findings=(twin,)),
        RuleResult("L-11", Result.EXCEPTION, findings=(plain,)),
    ]
    specs = [s for s in all_rules() if s.id in ("L-09", "L-08", "L-11")]
    overall, doms = rollup_domains(rr, specs, [LABOR, DCAA], coverage_floor=FLOOR)
    assert doms[LABOR].total_exposure_usd == D("1600.00")
    assert doms[DCAA].total_exposure_usd == D("1600.00") + D("15680.00")
    assert sum(d.total_exposure_usd for d in doms.values()) == D("18880.00")  # what naive summing would report
    assert overall.total_exposure_usd == D("1600.00") + D("15680.00") == total_exposure([labor, twin, plain])
    assert overall.open_findings == 3  # every finding is still visible


def test_a_disabled_domain_is_absent_everywhere():
    overall, doms = rollup_domains(_consistent(), all_rules(), [LABOR], coverage_floor=FLOOR)
    assert list(doms) == [LABOR]
    assert (overall.coverage_evaluated, overall.coverage_applicable) == (6, 6)
    assert not {"L-08", "L-11"} & set(overall.rule_results) and not {"L-08", "L-11"} & set(overall.not_evaluated)
    # the runner does not run a disabled domain; results that slip in are still ignored
    enabled_specs = [s for s in all_rules() if s.domain == LABOR]
    o2, d2 = rollup_domains([r for r in _consistent() if r.rule_id not in ("L-08", "L-11")], enabled_specs, [LABOR],
                            coverage_floor=FLOOR)
    assert o2 == overall
    # an enabled domain the run carries no rules for is reported thin, never silently full
    empty, edoms = rollup_domains([], [], [DCAA], coverage_floor=FLOOR)
    assert empty.label == "Incomplete data" and edoms[DCAA].coverage_applicable == 0


def test_a_single_enabled_domain_equals_the_plain_rollup():
    specs = [s for s in all_rules() if s.domain == LABOR]
    rr = [r for r in _consistent(not_evaluated=("L-06",)) if r.rule_id in {s.id for s in specs}]
    plain = rollup(rr, specs, coverage_floor=FLOOR)
    overall, doms = rollup_domains(rr, specs, [LABOR], coverage_floor=FLOOR)
    assert overall == replace(plain, domain="") and doms[LABOR] == replace(plain, domain=LABOR)
    assert overall.label == "Incomplete data" or overall.coverage_ratio >= FLOOR  # 5/6 = 0.8333 < 0.85
    assert overall.label == "Incomplete data"


def test_results_above_the_run_tier_keep_their_domain_when_only_applicable_specs_are_passed():
    # A fast run: the runner passes only fast-tier rules as `applicable`, yet every rule has a result.
    fast = [s for s in all_rules() if s.tier == Tier.FAST]
    rr = _consistent(not_evaluated=tuple(s.id for s in all_rules() if s.tier != Tier.FAST))
    overall, doms = rollup_domains(rr, fast, [LABOR, DCAA], coverage_floor=FLOOR)
    assert overall.not_evaluated.keys() == {s.id for s in all_rules() if s.tier != Tier.FAST}
    assert "L-11" in doms[DCAA].not_evaluated and "L-11" not in doms[LABOR].rule_results
    assert (doms[DCAA].coverage_evaluated, doms[DCAA].coverage_applicable) == (1, 1)  # only L-08 is in the fast tier
    assert doms[LABOR].coverage_applicable == 3  # L-01, L-02, L-05, L-06, L-09 need hris/payroll or are fast: recomputed below


# --------------------------------------------------------------------------- #
# explanations
# --------------------------------------------------------------------------- #
def _text(f: Finding) -> str:
    ex = explain(f, get_rule(f.rule_id))
    return "\n".join([f.headline, f.severity_reason, ex.what_happened, ex.why_it_matters, ex.impact, ex.recommended_action])


def test_l11_explanation_is_template_only_lint_clean_and_states_direction():
    f = ga_finding(run("L-11", v_rates()))
    ex = explain(f, get_rule("L-11"))
    text = _text(f)
    assert find_restricted(text) == [] and "{" not in text and "}" not in text
    assert "$211,680.00" in ex.what_happened and "$1,960,000.00" in ex.what_happened
    assert "10.80%" in ex.what_happened and "10.00%" in ex.what_happened and "8.0% above" in ex.what_happened
    assert "no longer tracks the actual rate" in ex.what_happened
    assert "4 consecutive periods" in ex.what_happened and "systemic pattern" in ex.what_happened
    assert "inconsistent with" in ex.why_it_matters and "under-billed" in ex.why_it_matters
    assert "contracting officer" in ex.why_it_matters
    assert "FAR 52.216-7" in ex.why_it_matters and "CAS 418 (to verify)" in ex.why_it_matters
    assert "$15,680.00" in ex.impact and "0.80% gap" in ex.impact and "under-billed" in ex.impact
    assert "violation" not in text.lower()


def test_l11_explanation_for_over_billed_and_first_period_variants():
    over = ga_finding(run("L-11", v_rates(ga_rate="0.0900", history=False)))
    ex = explain(over, get_rule("L-11"))
    assert "10.0% below" in ex.what_happened and "over-billed" in ex.why_it_matters and "over-billed" in ex.impact
    assert "first period" in ex.what_happened and find_restricted(_text(over)) == []
    two = {**GA_HISTORY, "2026-05": "0.1005", "2026-06": "0.1010"}
    mid = explain(ga_finding(run("L-11", v_rates(ga_history=two), materiality_usd="1000000")), get_rule("L-11"))
    assert "2 consecutive periods" in mid.what_happened and "systemic" not in mid.what_happened


def test_l08_explanation_is_template_only_lint_clean_and_uses_record_figures():
    f = run("L-08", v_l08("160.0", "40.0")).findings[0]
    ex = explain(f, get_rule("L-08"))
    text = _text(f)
    assert find_restricted(text) == [] and "{" not in text and "}" not in text
    assert "E-1" in ex.what_happened and "40.0 hours on indirect" in ex.what_happened
    assert "160.0 hours on direct" in ex.what_happened and "20.0%" in ex.what_happened and "5.0%" in ex.what_happened
    assert "15.0 percentage points" in ex.what_happened and "30.0 hours" in ex.what_happened
    assert "inconsistent with" in ex.why_it_matters and "DFARS 252.242-7006(c)(2)" in ex.why_it_matters
    assert "$2,700.00" in ex.impact and "30.0 hours above the employee's baseline" in ex.impact


def test_every_dcaa_wording_avoids_restricted_terms():
    fs = [
        *run("L-11", v_rates()).findings,
        *run("L-11", v_rates(ga_rate="0.1030")).findings,
        *run("L-11", v_rates(ga_rate="0.0900")).findings,
        *run("L-08", v_l08("160.0", "40.0")).findings,
    ]
    assert {f.rule_id for f in fs} == {"L-08", "L-11"}
    for f in fs:
        assert find_restricted(_text(f)) == [], f.fingerprint
    for spec in (get_rule("L-08"), get_rule("L-11")):
        assert find_restricted(spec.explanation_template + spec.recommended_action + spec.title) == []
    for res in (run("L-11", without(v_rates(), "rate_data")), run("L-08", v_l08(history={}))):
        assert find_restricted(res.not_evaluated_reason) == []
