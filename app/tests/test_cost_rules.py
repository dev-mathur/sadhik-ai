"""DCAA cost accounting, second seed set: C-01 unallowable cost screening, C-02 incurred cost submission
deadline, C-03 billing rate above a contractual ceiling. Small hand-built WorkingViews, exact arithmetic.
"""

from __future__ import annotations

import random
from dataclasses import replace
from datetime import date
from decimal import Decimal

from engine.canonical import IcsRecord
from engine.copy_lint import find_restricted
from engine.explain import explain
from engine.metrics import total_exposure
from engine.rules.base import Domain, Result, Severity, Tier, get_rule
from engine.rules.c02 import add_months
from tests.test_dcaa_rules import DCAA_SOURCES, AGREEMENTS, gl, v_rates
from tests.test_rules_smoke import MAT, PERIOD, D, lin, run, view

# --------------------------------------------------------------------------- #
# C-01 unallowable cost screening
# --------------------------------------------------------------------------- #
ALLOW = {
    "6510 Insurance": ("allowable", ""),
    "6410 Rent and Facilities": ("allowable", ""),
    "6535 Entertainment and Events": ("unallowable", "FAR 31.205-14"),
    "6565 Lobbying and Political Activity": ("unallowable", "FAR 31.205-22"),
    "6435 Alcoholic Beverages": ("unallowable", "FAR 31.205-51"),
    "6525 Gifts and Employee Awards": ("conditional", "FAR 31.205-13 (to verify)"),
}


def v_c01(*, conditional: str = "4100.00", allowance=None, lines=None):
    base = lines if lines is not None else [
        gl("6510 Insurance", "50000.00", "indirect", "ga", "INDIRECT", 2),
        gl("6535 Entertainment and Events", "14800.00", "indirect", "ga", "INDIRECT", 3),
        gl("6565 Lobbying and Political Activity", "6500.00", "indirect", "ga", "INDIRECT", 4),
        gl("6525 Gifts and Employee Awards", conditional, "indirect", "ga", "INDIRECT", 5),
        gl("6410 Rent and Facilities", "100000.00", "indirect", "overhead", "INDIRECT", 6),
        gl("6435 Alcoholic Beverages", "1850.00", "indirect", "overhead", "INDIRECT", 7),
        gl("5010 Direct Labor", "900000.00", "direct", None, "C-8841", 8),
    ]
    return view(gl_lines=base, account_allowability=dict(ALLOW if allowance is None else allowance),
                sources_present=DCAA_SOURCES)


def by_fp(res):
    return {f.fingerprint: f for f in res.findings}


def test_c01_is_in_the_dcaa_domain_at_the_close_tier():
    spec = get_rule("C-01")
    assert spec.domain == Domain.DCAA_COST_ACCOUNTING.value and spec.tier == Tier.CLOSE and spec.review_status == "unreviewed"
    assert spec.counts_toward_coverage


def test_c01_finds_unallowable_cost_per_pool_with_exact_amounts():
    res = run("C-01", v_c01(), materiality_usd="11111.90")
    fs = by_fp(res)
    assert set(fs) == {"C-01|ga|2026-08", "C-01|overhead|2026-08"}
    ga, oh = fs["C-01|ga|2026-08"], fs["C-01|overhead|2026-08"]
    assert (ga.exposure_usd, oh.exposure_usd) == (D("21300.00"), D("1850.00"))  # 14,800 + 6,500 ; gifts are NOT included
    assert (ga.severity, oh.severity) == (Severity.HIGH, Severity.MEDIUM)  # >= materiality vs below it
    assert res.result == Result.EXCEPTION
    assert ga.exposure_entry_ids == () and oh.exposure_entry_ids == () and ga.employees == ()
    assert ga.computed["pool_amount"] == D("75400.00")  # 50,000 + 14,800 + 6,500 + 4,100
    assert ga.computed["unallowable_share"] == D("0.2825")  # 21,300 / 75,400 rounded half-up
    assert ga.computed["account_count"] == 2
    assert any("FAR 31.205-14" in a for a in ga.computed["accounts"])


def test_c01_evidence_lists_the_gl_lines_with_their_rows_and_citations():
    ga = by_fp(run("C-01", v_c01()))["C-01|ga|2026-08"]
    rows = {e.detail["account"]: (e.row, e.detail["citation"], e.kind) for e in ga.evidence}
    assert rows == {"6535 Entertainment and Events": (3, "FAR 31.205-14", "gl_line"),
                    "6565 Lobbying and Political Activity": (4, "FAR 31.205-22", "gl_line")}


def test_c01_conditional_cost_below_materiality_is_logged_not_raised():
    res = run("C-01", v_c01(), materiality_usd="11111.90")
    assert "C-01|ga|conditional|2026-08" not in by_fp(res)
    (log,) = res.logged_below_materiality
    assert (log["account"], log["amount"], log["allowability"], log["row"]) == ("6525 Gifts and Employee Awards", "4100.00", "conditional", 5)


def test_c01_conditional_at_or_above_materiality_is_a_watch_and_coexists_with_unallowable():
    res = run("C-01", v_c01(conditional="11111.90"), materiality_usd="11111.90")  # exactly materiality
    fs = by_fp(res)
    assert {"C-01|ga|2026-08", "C-01|ga|conditional|2026-08"} <= set(fs)  # distinct fingerprints, one pool
    cond = fs["C-01|ga|conditional|2026-08"]
    assert cond.exposure_usd == D("11111.90") and cond.severity != Severity.HIGH  # a Watch is never High
    assert cond.computed["kind"] == "conditional" and not res.logged_below_materiality
    just_below = run("C-01", v_c01(conditional="11111.89"), materiality_usd="11111.90")
    assert "C-01|ga|conditional|2026-08" not in by_fp(just_below)


def test_c01_only_conditional_cost_is_a_watch_result():
    lines = [gl("6510 Insurance", "50000.00", "indirect", "ga", "INDIRECT", 2),
             gl("6525 Gifts and Employee Awards", "9000.00", "indirect", "ga", "INDIRECT", 3)]
    res = run("C-01", v_c01(lines=lines), materiality_usd="2000.00")
    assert res.result == Result.WATCH and list(by_fp(res)) == ["C-01|ga|conditional|2026-08"]


def test_c01_tolerance_is_strictly_greater_than():
    m = dict(materiality_usd="11111.90")  # the demo's materiality: the 4,100.00 conditional line stays below it
    at = run("C-01", v_c01(), unallowable_tolerance_usd="1850.00", **m)  # overhead holds exactly 1,850.00
    assert set(by_fp(at)) == {"C-01|ga|2026-08"}
    below = run("C-01", v_c01(), unallowable_tolerance_usd="1849.99", **m)
    assert set(by_fp(below)) == {"C-01|ga|2026-08", "C-01|overhead|2026-08"}


def test_c01_clean_and_missing_inputs_are_never_a_pass():
    clean = v_c01(lines=[gl("6510 Insurance", "50000.00", "indirect", "ga", "INDIRECT", 2),
                         gl("6410 Rent and Facilities", "100000.00", "indirect", "overhead", "INDIRECT", 3)])
    assert run("C-01", clean).result == Result.CONSISTENT and not run("C-01", clean).findings
    assert run("C-01", v_c01(allowance={})).result == Result.NOT_EVALUATED  # no confirmed table
    no_gl = replace(v_c01(), sources_present=DCAA_SOURCES - {"gl"})
    assert run("C-01", no_gl).result == Result.NOT_EVALUATED
    assert run("C-01", v_c01(lines=[])).result == Result.NOT_EVALUATED  # no GL lines for the period
    direct_only = [gl("5010 Direct Labor", "900000.00", "direct", None, "C-8841", 2)]
    assert run("C-01", v_c01(lines=direct_only)).result == Result.NOT_EVALUATED  # no indirect pool screened: not a pass


def test_c01_an_account_missing_from_the_table_is_treated_as_allowable():
    lines = [gl("9999 Mystery", "40000.00", "indirect", "ga", "INDIRECT", 2)]
    assert run("C-01", v_c01(lines=lines)).result == Result.CONSISTENT


def test_c01_ignores_direct_lines_and_lines_from_other_periods():
    lines = [gl("6535 Entertainment and Events", "14800.00", "direct", None, "C-8841", 2),
             gl("6535 Entertainment and Events", "9999.00", "indirect", "ga", "INDIRECT", 3, period="2026-07")]
    assert run("C-01", v_c01(lines=lines)).result == Result.NOT_EVALUATED  # nothing in this period at all
    lines.append(gl("6510 Insurance", "1.00", "indirect", "ga", "INDIRECT", 4))
    assert run("C-01", v_c01(lines=lines)).result == Result.CONSISTENT


def test_c01_is_order_independent_and_its_exposure_is_a_plain_amount_in_m13():
    v = v_c01()
    shuffled = list(v.gl_lines)
    random.Random(3).shuffle(shuffled)
    a = run("C-01", v, materiality_usd="11111.90")
    b = run("C-01", replace(v, gl_lines=shuffled), materiality_usd="11111.90")
    assert {(f.fingerprint, f.exposure_usd, f.severity) for f in a.findings} == {(f.fingerprint, f.exposure_usd, f.severity) for f in b.findings}
    assert total_exposure(a.findings) == D("23150.00")  # 21,300 + 1,850: no entries, so nothing to de-duplicate


# --------------------------------------------------------------------------- #
# C-02 incurred cost submission deadline
# --------------------------------------------------------------------------- #
def ics(fy: str, fye: str, sub: str | None, row: int = 2) -> IcsRecord:
    return IcsRecord(fy, date.fromisoformat(fye), date.fromisoformat(sub) if sub else None, f"{fy} transmittal", lin("ics", row))


SCHEDULE = [ics("FY2023", "2023-12-31", "2024-06-21", 2), ics("FY2024", "2024-12-31", "2025-06-27", 3),
            ics("FY2025", "2025-12-31", None, 4)]


def v_c02(schedule=None, period=PERIOD, sources=DCAA_SOURCES):
    return view(period=period, ics_submissions=list(SCHEDULE if schedule is None else schedule), sources_present=sources)


def test_c02_is_in_the_dcaa_domain_and_the_floor_is_six_months():
    spec = get_rule("C-02")
    assert spec.domain == Domain.DCAA_COST_ACCOUNTING.value and spec.tier == Tier.CLOSE
    assert spec.parameters["ics_due_months"].default == D("6") and spec.parameters["ics_due_months"].max == D("6")


def test_c02_add_months_keeps_the_day_and_clamps_to_the_month_end():
    assert add_months(date(2025, 12, 31), 6) == date(2026, 6, 30)
    assert add_months(date(2025, 8, 31), 6) == date(2026, 2, 28)
    assert add_months(date(2024, 8, 31), 6) == date(2025, 2, 28)
    assert add_months(date(2024, 8, 29), 6) == date(2025, 2, 28)
    assert add_months(date(2025, 6, 15), 6) == date(2025, 12, 15)
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)


def test_c02_finds_the_overdue_submission_with_exact_days():
    res = run("C-02", v_c02())
    (f,) = res.findings
    assert f.fingerprint == "C-02|FY2025|2026-08" and res.result == Result.EXCEPTION
    assert f.computed["due_date"] == "2026-06-30" and f.computed["days_overdue"] == D("62")
    assert f.exposure_usd == D("0.00") and f.severity == Severity.HIGH and f.severity_reason.startswith("integrity_failure")
    assert f.metric_id == "M16" and f.metric_value == D("62")
    (ev,) = f.evidence
    assert (ev.kind, ev.row, ev.detail["due_date"]) == ("ics_record", 4, "2026-06-30")
    assert res.metrics[0].value == D("62") and not res.logged_below_materiality  # the earlier two were on time


def test_c02_due_date_boundary_is_late_from_the_day_after():
    assert not run("C-02", v_c02(period="2026-06")).findings  # as-of 2026-06-30 == due: still on time
    (f,) = run("C-02", v_c02(period="2026-07")).findings  # as-of 2026-07-31: 31 days after 2026-06-30
    assert f.computed["days_overdue"] == D("31")
    assert run("C-02", v_c02(period="2026-06")).result == Result.CONSISTENT


def test_c02_a_year_not_yet_due_is_ignored():
    sched = [ics("FY2026", "2026-12-31", None, 2)]
    assert run("C-02", v_c02(schedule=sched)).result == Result.CONSISTENT and not run("C-02", v_c02(schedule=sched)).findings


def test_c02_a_late_but_submitted_filing_is_history_only():
    sched = [ics("FY2024", "2024-12-31", "2025-07-14", 2)]  # 14 days after 2025-06-30
    res = run("C-02", v_c02(schedule=sched))
    assert not res.findings and res.result == Result.CONSISTENT
    (log,) = res.logged_below_materiality
    assert (log["fiscal_year"], log["days_late"], log["reason"]) == ("FY2024", 14, "submitted_late_history_only")


def test_c02_a_customer_may_set_an_earlier_deadline():
    sched = [ics("FY2025", "2025-12-31", "2026-06-10", 2)]  # on time at 6 months (due 06-30) ...
    assert not run("C-02", v_c02(schedule=sched)).findings
    res = run("C-02", v_c02(schedule=sched), ics_due_months="5")  # ... but after a 5-month internal deadline (05-31)
    assert not res.findings and res.logged_below_materiality[0]["days_late"] == 10
    (f,) = run("C-02", v_c02(schedule=[ics("FY2025", "2025-12-31", None, 2)]), ics_due_months="5").findings
    assert f.computed["due_date"] == "2026-05-31" and f.computed["days_overdue"] == D("92")


def test_c02_missing_inputs_are_never_a_pass():
    assert run("C-02", v_c02(schedule=[])).result == Result.NOT_EVALUATED
    assert run("C-02", v_c02(sources=DCAA_SOURCES - {"rate_data"})).result == Result.NOT_EVALUATED
    assert "not uploaded" in run("C-02", v_c02(sources=DCAA_SOURCES - {"rate_data"})).not_evaluated_reason


def test_c02_is_order_independent():
    a = run("C-02", v_c02())
    b = run("C-02", v_c02(schedule=list(reversed(SCHEDULE))))
    assert [(f.fingerprint, f.exposure_usd) for f in a.findings] == [(f.fingerprint, f.exposure_usd) for f in b.findings]


# --------------------------------------------------------------------------- #
# C-03 billing rate above a contractual ceiling
# --------------------------------------------------------------------------- #
def with_ceilings(**ceil: str):
    return [replace(a, ceiling_rate=D(ceil[a.pool])) if a.pool in ceil else a for a in AGREEMENTS]


CEILINGS = dict(fringe="0.3000", overhead="0.1750", ga="0.1200")


def test_c03_is_in_the_dcaa_domain_at_the_close_tier():
    spec = get_rule("C-03")
    assert spec.domain == Domain.DCAA_COST_ACCOUNTING.value and spec.tier == Tier.CLOSE
    assert set(spec.required_sources) == {"gl", "rate_data"}


def test_c03_finds_a_provisional_rate_above_its_ceiling_with_exact_exposure():
    res = run("C-03", v_rates(agreements=with_ceilings(**CEILINGS)))
    (f,) = res.findings
    assert f.fingerprint == "C-03|overhead|2026-08" and res.result == Result.EXCEPTION
    # 0.1800 - 0.1750 = 0.0050 on a direct-labor base of 1,000,000.00
    assert f.exposure_usd == D("5000.00") and f.computed["excess"] == D("0.0050")
    assert f.severity == Severity.HIGH and f.severity_reason.startswith("integrity_failure")
    assert f.computed["base_amount"] == D("1000000.00") and f.exposure_entry_ids == ()
    assert f.computed["actual_above_ceiling"] is True  # 0.1790 is also above 0.1750: context, not exposure
    assert "also above the ceiling" in f.computed["actual_sentence"]
    assert len(res.metrics) == 3 and {m.result for m in res.metrics} == {Result.EXCEPTION, Result.CONSISTENT}


def test_c03_boundaries_equal_is_fine_and_one_basis_point_over_is_not():
    eq = with_ceilings(fringe="0.3000", overhead="0.1800", ga="0.1200")  # provisional == ceiling
    assert not run("C-03", v_rates(agreements=eq)).findings and run("C-03", v_rates(agreements=eq)).result == Result.CONSISTENT
    one_bp = with_ceilings(fringe="0.3000", overhead="0.1799", ga="0.1200")
    (f,) = run("C-03", v_rates(agreements=one_bp)).findings
    assert f.exposure_usd == D("100.00")  # 0.0001 x 1,000,000.00


def test_c03_tolerance_is_strictly_greater_than():
    ags = with_ceilings(**CEILINGS)
    assert not run("C-03", v_rates(agreements=ags), ceiling_excess_tolerance="0.0050").findings
    assert run("C-03", v_rates(agreements=ags), ceiling_excess_tolerance="0.0049").findings


def test_c03_actual_below_ceiling_is_described_as_such():
    ags = with_ceilings(fringe="0.3000", overhead="0.1750", ga="0.1200")
    v = v_rates(agreements=ags, overhead_nl="150000.00")  # overhead pool 152,138 / 1,000,000 = 0.1521 < 0.1750
    (f,) = run("C-03", v).findings
    assert f.computed["actual_above_ceiling"] is False and "at or below the ceiling" in f.computed["actual_sentence"]
    assert f.exposure_usd == D("5000.00")  # the exposure is what was BILLED above the cap, whatever the actual rate


def test_c03_missing_inputs_are_never_a_pass():
    ags = with_ceilings(**CEILINGS)
    assert run("C-03", v_rates(agreements=AGREEMENTS)).result == Result.NOT_EVALUATED  # no ceiling anywhere
    assert "ceiling" in run("C-03", v_rates(agreements=AGREEMENTS)).not_evaluated_reason
    v = v_rates(agreements=ags)
    assert run("C-03", replace(v, sources_present=DCAA_SOURCES - {"rate_data"})).result == Result.NOT_EVALUATED
    assert run("C-03", replace(v, sources_present=DCAA_SOURCES - {"gl"})).result == Result.NOT_EVALUATED


def test_c03_a_pool_without_a_ceiling_or_a_covering_agreement_is_skipped_not_passed():
    only_oh = [replace(a, ceiling_rate=D("0.1750")) if a.pool == "overhead" else a for a in AGREEMENTS]
    res = run("C-03", v_rates(agreements=only_oh))
    (f,) = res.findings
    assert f.computed["skipped_pools"] == [  # fringe and ga carry no ceiling; both are named, in pool order
        {"pool": "fringe", "reason": "no ceiling rate applies for this period"},
        {"pool": "ga", "reason": "no ceiling rate applies for this period"}]
    assert res.result == Result.EXCEPTION  # the pool that WAS checked still trips
    future = [replace(a, effective_from=date(2026, 9, 1), effective_to=date(2026, 12, 31), ceiling_rate=D("0.1000")) for a in AGREEMENTS]
    assert run("C-03", v_rates(agreements=future)).result == Result.NOT_EVALUATED  # nothing in force in August


def test_c03_every_finding_lists_every_skipped_pool_even_one_that_sorts_after_it():
    """Overhead trips; the G&A agreement (sorts BEFORE overhead) and a hypothetical pool that sorts AFTER it both
    have no ceiling. A finding built before the later pool was examined would have omitted it."""
    ags = [replace(a, ceiling_rate=D("0.1750")) if a.pool == "overhead" else a for a in AGREEMENTS]
    ags.append(replace(AGREEMENTS[0], pool="zzz_pool"))
    (f,) = run("C-03", v_rates(agreements=ags)).findings
    assert [s["pool"] for s in f.computed["skipped_pools"]] == ["fringe", "ga", "zzz_pool"]


def test_c03_is_order_independent():
    ags = with_ceilings(**CEILINGS)
    a = run("C-03", v_rates(agreements=ags))
    b = run("C-03", v_rates(agreements=list(reversed(ags))))
    assert [(f.fingerprint, f.exposure_usd) for f in a.findings] == [(f.fingerprint, f.exposure_usd) for f in b.findings]


# --------------------------------------------------------------------------- #
# explanations: template-only, lint-clean, figures from the record
# --------------------------------------------------------------------------- #
def _all_new_findings():
    out = []
    out += run("C-01", v_c01(), materiality_usd="11111.90").findings
    out += run("C-01", v_c01(conditional="11111.90"), materiality_usd="11111.90").findings
    out += run("C-02", v_c02()).findings
    out += run("C-03", v_rates(agreements=with_ceilings(**CEILINGS))).findings
    return out


def test_new_rule_explanations_are_lint_clean_and_use_record_figures():
    seen = set()
    for f in _all_new_findings():
        ex = explain(f, get_rule(f.rule_id))
        text = "\n".join([f.headline, f.severity_reason, ex.what_happened, ex.why_it_matters, ex.impact, ex.recommended_action])
        assert find_restricted(text) == [], (f.fingerprint, find_restricted(text))
        assert "{" not in text and "}" not in text and all([ex.what_happened, ex.why_it_matters, ex.impact, ex.recommended_action])
        assert f"${f.exposure_usd:,.2f}" in ex.impact or f.exposure_usd == 0
        if f.computed.get("kind") == "conditional":
            # a conditional amount makes NO inconsistency claim: allowability depends on the facts
            assert "depends" in ex.what_happened and "inconsistent" not in ex.why_it_matters
        else:
            assert "inconsistent with" in ex.why_it_matters  # never a verdict, always "consistent or inconsistent with"
        seen.add(f.fingerprint)
    assert {"C-01|ga|2026-08", "C-01|ga|conditional|2026-08", "C-02|FY2025|2026-08", "C-03|overhead|2026-08"} <= seen


def test_c02_explanation_states_the_deadline_and_never_a_dollar_figure():
    ex = explain(run("C-02", v_c02()).findings[0], get_rule("C-02"))
    assert "2026-06-30" in ex.what_happened and "62 days" in ex.what_happened and "$" not in ex.impact
