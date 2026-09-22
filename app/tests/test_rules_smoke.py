"""Smoke tests for the deterministic rules, severity, metrics, rollup, explain and copy lint.

Hand-built WorkingViews with a handful of records each. Not exhaustive: for each
rule one case that finds the problem, one clean case, one not-evaluated case.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from engine.canonical import (
    ChargeCodeMap,
    Contract,
    Employee,
    LaborCategory,
    Lineage,
    PayRecord,
    GLLine,
    TimeEdit,
    TimeEntry,
    WorkingView,
)
from engine.copy_lint import DISCLAIMER, RESTRICTED_TERMS, find_restricted
from engine.explain import explain
from engine.metrics import total_exposure
from engine.rollup import rollup
from engine.rules.base import (
    Direction,
    Finding,
    Result,
    RuleResult,
    Severity,
    all_rules,
    get_rule,
)
from engine.severity import classify

D = Decimal
PERIOD = "2026-08"
MAT = D("2000.00")  # materiality_usd injected by the runner
FLOORS = Path(__file__).resolve().parent.parent / "config" / "floors.yaml"


# --------------------------------------------------------------------------- #
# builders
# --------------------------------------------------------------------------- #
def lin(name: str, row: int) -> Lineage:
    return Lineage(f"{name}.csv", "sha-" + name, row)


def emp(eid: str, title: str = "Data Engineer", row: int = 2) -> Employee:
    return Employee(eid, f"Name {eid}", title, date(2024, 1, 1), None, "non_exempt", "ENG", lin("hris", row))


_row = {"n": 1}


def entry(eid: str, eno: str, work: str, hours: str, code: str, cat: str, created: str) -> TimeEntry:
    _row["n"] += 1
    return TimeEntry(
        entry_id=eno,
        employee_id=eid,
        work_date=date.fromisoformat(work),
        hours=D(hours),
        charge_code=code,
        labor_category=cat,
        entered_at=datetime.fromisoformat(created),
        submitted_at=None,
        approved_by="E-9999",
        approved_at=None,
        lineage=lin("time", _row["n"]),
    )


def contracts() -> list[Contract]:
    cats = (
        LaborCategory("Data Engineer II", D("142.00"), "BS + 3 years"),
        LaborCategory("Data Engineer III", D("178.50"), "BS + 7 years"),
    )
    return [
        Contract("C-8841", "SPECTRA", "T&M", date(2025, 10, 1), date(2026, 9, 30), D("6000000.00"),
                 D("4200000.00"), cats, (), ("C8841-DEV",), lin("contracts", 1)),
        Contract("C-7302", "ORBIT", "CPFF", date(2025, 8, 16), date(2026, 8, 15), D("3150000.00"),
                 D("3150000.00"), cats, (), ("C7302-DEV",), lin("contracts", 2)),
    ]


ALL_SOURCES = frozenset({"timekeeping", "time_edits", "payroll", "gl", "hris", "contracts"})


def view(**kw) -> WorkingView:
    base = dict(
        period=PERIOD,
        contracts=contracts(),
        charge_codes={
            "C8841-DEV": ChargeCodeMap("C8841-DEV", "C-8841", "direct", None),
            "C7302-DEV": ChargeCodeMap("C7302-DEV", "C-7302", "direct", None),
            "OH-100": ChargeCodeMap("OH-100", None, "indirect", "overhead"),
        },
        category_crosswalk={"Data Engineer": "Data Engineer II"},
        sources_present=ALL_SOURCES,
    )
    base.update(kw)
    return WorkingView(**base)


def params(rule_id: str, **override: str) -> dict[str, Decimal]:
    spec = get_rule(rule_id)
    p = {n: ps.default for n, ps in spec.parameters.items()}
    p["materiality_usd"] = MAT
    p.update({k: D(v) for k, v in override.items()})
    return p


def run(rule_id: str, v: WorkingView, **override: str) -> RuleResult:
    return get_rule(rule_id).evaluate(v, params(rule_id, **override))


def without(v: WorkingView, source: str) -> WorkingView:
    return replace(v, sources_present=v.sources_present - {source})


def pay(eid: str, pp: str, reg: str, gross: str = "1000.00", row: int = 2) -> PayRecord:
    return PayRecord(eid, pp, D(reg), D("0.0"), D("0.0"), D(gross), lin("adp", row))


# --------------------------------------------------------------------------- #
# scenarios (each returns a WorkingView)
# --------------------------------------------------------------------------- #
def v_l01(charged: str = "Data Engineer III") -> WorkingView:
    return view(
        employees=[emp("E-1")],
        time_entries=[
            entry("E-1", "TE-1", "2026-08-03", "8.0", "C8841-DEV", charged, "2026-08-03T17:00:00"),
            entry("E-1", "TE-2", "2026-08-04", "8.0", "C8841-DEV", charged, "2026-08-04T17:00:00"),
            entry("E-1", "TE-3", "2026-08-05", "8.0", "OH-100", charged, "2026-08-05T17:00:00"),
        ],
        loaded_rates={"E-1": D("90.00")},
    )


def v_l02() -> WorkingView:
    ents, pays, rates = [], [], {}
    # E-1: +12.0 h; E-2: -9.5 h at 102.75 (976.125 -> 976.13); E-3: +1.5 h (below materiality); E-4: exact
    for i, (eid, worked, rate) in enumerate(
        [("E-1", "92.0", "96.40"), ("E-2", "70.5", "102.75"), ("E-3", "81.5", "80.00"), ("E-4", "80.0", "80.00")]
    ):
        ents.append(entry(eid, f"TE-{i}", "2026-08-05", worked, "OH-100", "n/a", "2026-08-05T17:00:00"))
        pays.append(pay(eid, "2026-08-A", "80.0", row=i + 2))
        rates[eid] = D(rate)
    return view(employees=[emp(e) for e in ("E-1", "E-2", "E-3", "E-4")], time_entries=ents, pay_records=pays, loaded_rates=rates)


def v_l02_aggregate() -> WorkingView:
    """Ten employees each 3.5 h over: every gap is below the per-employee materiality (4.0 h), yet
    35.0 / 800.0 = 4.375% exceeds the 3% aggregate Exception threshold."""
    ids = [f"E-{i}" for i in range(1, 11)]
    return view(
        employees=[emp(e) for e in ids],
        time_entries=[entry(e, f"TE-{i}", "2026-08-05", "83.5", "OH-100", "n/a", "2026-08-05T17:00:00") for i, e in enumerate(ids)],
        pay_records=[pay(e, "2026-08-A", "80.0", row=i + 2) for i, e in enumerate(ids)],
        loaded_rates={e: D("80.00") for e in ids},
    )


def v_l03(gl_total: str) -> WorkingView:
    return view(
        pay_records=[pay("E-1", "2026-08-A", "80.0", "1000000.00")],
        gl_lines=[GLLine("6000", "C-8841", PERIOD, D(gl_total), "direct", None, lin("qb", 2))],
    )


FRI = "2026-08-07"  # a Friday


def v_l05(window_entries: int = 4, history: bool = True) -> WorkingView:
    ents = []
    for i in range(10):
        eid = f"E-{i % 3 + 1}"
        if i < window_entries:  # Thursday work created Friday afternoon
            ents.append(entry(eid, f"TE-{i:02d}", "2026-08-06", "8.0", "C7302-DEV", "n/a", f"{FRI}T15:30:00"))
        else:  # created the same Tuesday
            ents.append(entry(eid, f"TE-{i:02d}", "2026-08-04", "8.0", "C7302-DEV", "n/a", "2026-08-04T10:00:00"))
    base = {f"2026-0{m}": {"M5.company_window_share": D("0.10")} for m in range(2, 8)} if history else {}
    return view(time_entries=ents, loaded_rates={f"E-{i}": D("100.00") for i in (1, 2, 3)}, baselines=base)


def v_overlap() -> WorkingView:
    """4 C-7302 entries dated after PoP end, created in the Friday window: both L-05 and L-09 price them."""
    ents = []
    for i, eid in enumerate(["E-1", "E-1", "E-2", "E-2"]):
        ents.append(entry(eid, f"TE-{i:02d}", f"2026-08-{19 + i % 3}", "8.0", "C7302-DEV", "n/a", "2026-08-21T15:30:00"))
    for i in range(4, 10):
        ents.append(entry("E-3", f"TE-{i:02d}", "2026-08-10", "8.0", "C7302-DEV", "n/a", "2026-08-10T17:00:00"))
    base = {f"2026-0{m}": {"M5.company_window_share": D("0.10")} for m in range(2, 8)}
    return view(time_entries=ents, loaded_rates={"E-1": D("100.00"), "E-2": D("95.50"), "E-3": D("90.00")}, baselines=base)


def v_l06(edits: int, undocumented: int, history: str | None, last_days: int = 0) -> WorkingView:
    ents = [
        entry(f"E-{i % 4 + 1}", f"TE-{i:03d}", "2026-08-05", "4.0", "C8841-DEV", "n/a", "2026-08-05T17:00:00")
        for i in range(100)
    ]
    eds = []
    for i in range(edits):
        eds.append(
            TimeEdit(
                f"ED-{i:03d}", f"TE-{i:03d}",
                datetime(2026, 8, 31 if i < last_days else 12, 9, 0),
                "E-9", "4.0", "5.0", None if i < undocumented else "Timesheet correction memo",
                lin("edits", i + 2),
            )
        )
    base = {f"2026-0{m}": {"M6.edit_rate": D(history)} for m in range(2, 8)} if history else {}
    return view(
        time_entries=ents, time_edits=eds, baselines=base,
        loaded_rates={f"E-{i}": D("80.00") for i in range(1, 5)},
    )


def v_l09(work: str = "2026-08-17") -> WorkingView:
    return view(
        time_entries=[
            entry("E-1", "TE-1", work, "8.0", "C7302-DEV", "n/a", "2026-08-21T17:00:00"),
            entry("E-1", "TE-2", "2026-08-03", "8.0", "C7302-DEV", "n/a", "2026-08-03T17:00:00"),
        ],
        loaded_rates={"E-1": D("94.50")},
    )


def v_dq(unmatched: bool = True) -> WorkingView:
    ents = [entry("E-1", "TE-1", "2026-08-03", "8.0", "OH-100", "n/a", "2026-08-03T17:00:00")]
    if unmatched:
        ents.append(entry("E-77", "TE-2", "2026-08-03", "6.0", "OH-100", "n/a", "2026-08-03T17:00:00"))
    return view(employees=[emp("E-1")], time_entries=ents, pay_records=[pay("E-1", "2026-08-A", "80.0")])


# --------------------------------------------------------------------------- #
# registry / parameters
# --------------------------------------------------------------------------- #
def test_parameters_match_floors_yaml():
    floors = yaml.safe_load(FLOORS.read_text())["rules"]
    specs = {r.id: r for r in all_rules()}
    for rid, yparams in floors.items():
        assert set(specs[rid].parameters) == set(yparams), rid
        for name, y in yparams.items():
            ps = specs[rid].parameters[name]
            assert ps.default == D(str(y["default"])), (rid, name)
            assert ps.direction == Direction(y["direction"]), (rid, name)
            assert ps.min == (None if y.get("min") is None else D(str(y["min"]))), (rid, name)
            assert ps.max == (None if y.get("max") is None else D(str(y["max"]))), (rid, name)


def test_specs_are_complete_and_unreviewed():
    ids = {r.id for r in all_rules()}
    assert ids == {"L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "C-01", "C-02", "C-03", "DQ-01"}
    for r in all_rules():
        assert r.review_status == "unreviewed"
        assert r.authorities and r.basis and r.required_sources
        assert r.explanation_template and r.recommended_action


# --------------------------------------------------------------------------- #
# L-01
# --------------------------------------------------------------------------- #
def test_l01_finds_mismatch():
    res = run("L-01", v_l01())
    assert res.result == Result.EXCEPTION and len(res.findings) == 1
    f = res.findings[0]
    assert f.fingerprint == "L-01|E-1|C-8841"
    assert f.exposure_usd == D("584.00")  # (178.50 - 142.00) x 16.0 h; the indirect entry is ignored
    assert f.exposure_entry_ids == ()
    assert f.severity == Severity.HIGH and f.severity_reason.startswith("integrity_failure")
    assert f.computed["hours"] == D("16.0") and f.computed["rate_difference"] == D("36.50")
    kinds = {e.kind for e in f.evidence}
    assert {"time_entry", "crosswalk", "contract_term"} <= kinds
    assert all(e.row is not None for e in f.evidence if e.kind == "time_entry")


def test_l01_clean_and_not_evaluated():
    clean = run("L-01", v_l01("Data Engineer II"))
    assert clean.result == Result.CONSISTENT and not clean.findings
    ne = run("L-01", without(v_l01(), "hris"))
    assert ne.result == Result.NOT_EVALUATED and not ne.findings and ne.not_evaluated_reason


# --------------------------------------------------------------------------- #
# L-02
# --------------------------------------------------------------------------- #
def test_l02_finds_gaps_rounds_half_up_and_logs_small_gap():
    res = run("L-02", v_l02())
    by_fp = {f.fingerprint: f for f in res.findings}
    assert set(by_fp) == {"L-02|E-1|2026-08-A", "L-02|E-2|2026-08-A"}
    assert by_fp["L-02|E-1|2026-08-A"].exposure_usd == D("1156.80")
    assert by_fp["L-02|E-2|2026-08-A"].exposure_usd == D("976.13")  # 9.5 x 102.75 = 976.125, half-up
    assert by_fp["L-02|E-2|2026-08-A"].headline == "Paid hours exceed timekeeping by 9.5 h for E-2"
    assert by_fp["L-02|E-1|2026-08-A"].headline == "Timekeeping exceeds paid hours by 12.0 h for E-1"
    for f in res.findings:
        assert f.exposure_entry_ids == () and f.severity == Severity.MEDIUM
    assert [x["employee_id"] for x in res.logged_below_materiality] == ["E-3"]
    assert res.result == Result.EXCEPTION
    m1 = res.metrics[0]
    assert m1.metric_id == "M1" and m1.numerator == D("23.0") and m1.denominator == D("320.0")


def test_l02_boundary_gap_at_exception_hours_is_watch():
    v = v_l02()
    v.time_entries[0] = replace(v.time_entries[0], hours=D("88.0"))  # +8.0 h exactly
    res = run("L-02", v, employee_gap_exception_hours="8.0")
    f = next(f for f in res.findings if f.employees == ("E-1",))
    assert f.threshold_tripped == "employee_gap_materiality_hours"


def test_l02_aggregate_exception_priced_without_double_counting():
    res = run("L-02", v_l02_aggregate())
    assert res.result == Result.EXCEPTION and len(res.findings) == 1
    f = res.findings[0]
    assert f.fingerprint == "L-02|2026-08|aggregate" and f.exposure_usd == D("2800.00")  # 35.0 h x 80.00
    assert len(res.logged_below_materiality) == 10 and f.severity == Severity.HIGH  # 10 employees: systemic


def test_l02_clean_and_not_evaluated():
    v = v_l02()
    clean = replace(
        v,
        time_entries=[replace(e, hours=D("80.0")) for e in v.time_entries],
    )
    res = run("L-02", clean)
    assert res.result == Result.CONSISTENT and not res.findings and not res.logged_below_materiality
    assert run("L-02", without(v, "payroll")).result == Result.NOT_EVALUATED


# --------------------------------------------------------------------------- #
# L-03
# --------------------------------------------------------------------------- #
def test_l03_watch_and_consistent_and_not_evaluated():
    res = run("L-03", v_l03("1011000.00"))  # 1.1% > 0.5% watch, < 2.0% exception
    assert res.result == Result.WATCH and len(res.findings) == 1
    assert res.findings[0].exposure_usd == D("11000.00") and res.findings[0].exposure_entry_ids == ()
    ok = run("L-03", v_l03("1001100.00"))  # 0.11%
    assert ok.result == Result.CONSISTENT and not ok.findings
    assert run("L-03", without(v_l03("1001100.00"), "gl")).result == Result.NOT_EVALUATED


# --------------------------------------------------------------------------- #
# L-05
# --------------------------------------------------------------------------- #
def test_l05_cluster_finding():
    res = run("L-05", v_l05(4), cluster_min_group_entries="10")
    assert res.result == Result.EXCEPTION
    f = next(f for f in res.findings if f.fingerprint == "L-05|C-7302")
    assert f.computed["cluster_index"] == D("4.0000")  # 0.40 / 0.10
    assert f.computed["baseline_confidence"] == "ok" and f.computed["baseline_periods"] == 6
    assert f.exposure_usd == D("3200.00")  # 4 entries x 8.0 h x 100.00
    assert len(f.exposure_entry_ids) == 4 and set(f.computed["entry_costs"]) == set(f.exposure_entry_ids)
    assert f.employees == ("E-1", "E-2", "E-3")
    assert f.severity == Severity.HIGH
    assert f.headline == "Entry-time clustering on the C-7302 team at 4.00x the 6-period baseline"


def test_l05_clean_low_baseline_small_group_and_not_evaluated():
    clean = run("L-05", v_l05(1), cluster_min_group_entries="10")
    assert clean.result == Result.CONSISTENT and not clean.findings
    # small group is not scored (default min group is 30)
    small = run("L-05", v_l05(4))
    assert small.result == Result.CONSISTENT and not small.findings
    assert any(m.result == Result.NOT_EVALUATED for m in small.metrics)
    # no history: falls back to this run's company share and says so
    low = run("L-05", v_l05(4, history=False), cluster_min_group_entries="10", cluster_index_watch="1.1")
    for f in low.findings:
        assert f.computed["baseline_confidence"] == "low"
    assert run("L-05", without(v_l05(4), "timekeeping")).result == Result.NOT_EVALUATED


def test_l05_late_entries_watch():
    v = v_l05(0)
    v.time_entries = [replace(e, entered_at=datetime(2026, 8, 20, 9, 0)) for e in v.time_entries]
    res = run("L-05", v, cluster_min_group_entries="10")
    late = next(f for f in res.findings if f.computed["kind"] == "late_entries")
    assert late.fingerprint == "L-05|late_entries|2026-08"
    assert res.result == Result.WATCH


def test_l05_order_independent():
    v = v_l05(4)
    a = run("L-05", v, cluster_min_group_entries="10")
    v2 = replace(v, time_entries=list(reversed(v.time_entries)))
    b = run("L-05", v2, cluster_min_group_entries="10")
    assert a.findings == b.findings


# --------------------------------------------------------------------------- #
# L-06
# --------------------------------------------------------------------------- #
def test_l06_watch_stable_history_is_low():
    res = run("L-06", v_l06(edits=12, undocumented=1, history="0.1200"))
    assert res.result == Result.WATCH and len(res.findings) == 1
    f = res.findings[0]
    assert f.fingerprint == "L-06|2026-08" and f.severity == Severity.LOW
    assert f.exposure_usd == D("320.00") and f.exposure_entry_ids == ("TE-000",)  # 4.0 h x 80.00
    assert f.computed["undocumented_count"] == 1
    assert f.headline == "Post-submission edit rate 12.0%, with 1 edit lacking a reason code"


def test_l06_rising_is_medium_exception_and_states():
    rising = run("L-06", v_l06(edits=12, undocumented=1, history="0.0500")).findings[0]
    assert rising.severity == Severity.MEDIUM and rising.severity_reason == "watch_rising_trend"
    exc = run("L-06", v_l06(edits=12, undocumented=3, history="0.1200"))
    assert exc.result == Result.EXCEPTION  # 25% undocumented > 10%
    clean = run("L-06", v_l06(edits=1, undocumented=0, history="0.0100"))
    assert clean.result == Result.CONSISTENT and not clean.findings
    ne = run("L-06", without(v_l06(edits=12, undocumented=1, history=None), "time_edits"))
    assert ne.result == Result.NOT_EVALUATED


def test_l06_last_two_days_exception():
    res = run("L-06", v_l06(edits=12, undocumented=0, history="0.1200", last_days=8))  # 66% > 50%
    assert res.result == Result.EXCEPTION
    assert res.findings[0].threshold_tripped == "edits_last_two_days_exception_pct"


# --------------------------------------------------------------------------- #
# L-09
# --------------------------------------------------------------------------- #
def test_l09_out_of_pop():
    res = run("L-09", v_l09())
    assert res.result == Result.EXCEPTION and len(res.findings) == 1
    f = res.findings[0]
    assert f.fingerprint == "L-09|C-7302"
    assert f.exposure_usd == D("756.00")  # 8.0 h x 94.50
    assert f.exposure_entry_ids == ("TE-1",)
    assert f.severity == Severity.HIGH and f.severity_reason.startswith("integrity_failure")
    assert f.headline == "8.0 hours charged to C-7302 after its period of performance ended 2026-08-15"


def test_l09_clean_boundary_tolerance_and_not_evaluated():
    on_end = run("L-09", v_l09("2026-08-15"))  # PoP end date itself is inside the PoP
    assert on_end.result == Result.CONSISTENT and not on_end.findings
    within = run("L-09", v_l09(), out_of_pop_hours_tolerance="10")
    assert not within.findings and len(within.logged_below_materiality) == 1
    assert run("L-09", without(v_l09(), "contracts")).result == Result.NOT_EVALUATED


# --------------------------------------------------------------------------- #
# L-11 / DQ-01
# --------------------------------------------------------------------------- #
def test_l11_never_passes_without_its_inputs():
    absent = run("L-11", without(view(), "rate_data"))
    assert absent.result == Result.NOT_EVALUATED
    assert absent.not_evaluated_reason == "Provisional billing rate data was not uploaded"
    # rate data declared but no GL lines and no agreements: still not a pass (full L-11 cases: test_dcaa_rules.py)
    present = run("L-11", view(sources_present=ALL_SOURCES | {"rate_data"}))
    assert present.result == Result.NOT_EVALUATED and not present.findings


def test_dq01():
    res = run("DQ-01", v_dq())
    assert res.result == Result.EXCEPTION and len(res.findings) == 1
    f = res.findings[0]
    assert f.fingerprint == "DQ-01|2026-08" and f.employees == ("E-77",)
    assert f.exposure_usd == D("0.00") and f.severity == Severity.MEDIUM and f.exposure_entry_ids == ()
    assert run("DQ-01", v_dq(unmatched=False)).result == Result.CONSISTENT
    assert run("DQ-01", without(v_dq(), "hris")).result == Result.NOT_EVALUATED


# --------------------------------------------------------------------------- #
# M13 de-duplication
# --------------------------------------------------------------------------- #
def test_total_exposure_dedupes_shared_entries():
    v = v_overlap()
    kw = dict(cluster_min_group_entries="10", late_threshold_hours="168")
    l05 = run("L-05", v, **kw).findings
    l09 = run("L-09", v).findings
    f05 = next(f for f in l05 if f.fingerprint == "L-05|C-7302")
    f09 = l09[0]
    assert len(f05.exposure_entry_ids) == 4 and set(f09.exposure_entry_ids) <= set(f05.exposure_entry_ids)
    # 8.0 h x (100 + 100 + 95.50 + 95.50) = 3164.00; the L-09 entries are the same four entries
    assert f05.exposure_usd == f09.exposure_usd == D("3128.00")
    assert total_exposure([f05, f09]) == D("3128.00")  # counted once, both findings still visible
    assert len([f05, f09]) == 2


def test_total_exposure_mixes_entry_and_non_entry_findings():
    v = v_overlap()
    f05 = next(f for f in run("L-05", v, cluster_min_group_entries="10", late_threshold_hours="168").findings
               if f.fingerprint == "L-05|C-7302")
    f09 = run("L-09", v).findings[0]
    f01 = run("L-01", v_l01()).findings[0]
    assert total_exposure([f01, f05, f09]) == D("584.00") + f05.exposure_usd
    assert total_exposure([]) == D("0.00")


# --------------------------------------------------------------------------- #
# severity
# --------------------------------------------------------------------------- #
def test_classify_fr6():
    kw = dict(exposure=D("500"), materiality=D("1000"), employees=1)
    assert classify(Result.EXCEPTION, **{**kw, "exposure": D("1000")}) == Severity.HIGH  # >= materiality
    assert classify(Result.EXCEPTION, **kw) == Severity.MEDIUM
    assert classify(Result.EXCEPTION, **{**kw, "employees": 4}) == Severity.MEDIUM
    assert classify(Result.EXCEPTION, **{**kw, "employees": 5}) == Severity.HIGH  # systemic
    assert classify(Result.EXCEPTION, **kw, periods=3) == Severity.HIGH  # systemic
    assert classify(Result.EXCEPTION, **kw, integrity_failure=True) == Severity.HIGH
    assert classify(Result.WATCH, **kw, trend_rising=True) == Severity.MEDIUM
    assert classify(Result.WATCH, **kw) == Severity.LOW
    assert classify(Result.WATCH, **{**kw, "employees": 9, "exposure": D("9999")}) == Severity.LOW  # Watch is never High
    with pytest.raises(ValueError):
        classify(Result.CONSISTENT, **kw)


# --------------------------------------------------------------------------- #
# rollup
# --------------------------------------------------------------------------- #
def _results(v: WorkingView) -> list[RuleResult]:
    return [r.evaluate(v, params(r.id)) for r in all_rules()]


def test_rollup_incomplete_data_takes_precedence():
    l_specs = [r for r in all_rules() if r.counts_toward_coverage]
    n = len(l_specs)
    rr = [RuleResult(s.id, Result.NOT_EVALUATED, not_evaluated_reason="missing") for s in l_specs]
    rr = [RuleResult("L-01", Result.CONSISTENT) if r.rule_id == "L-01" else r for r in rr]  # 1 evaluated, nothing found
    out = rollup(rr, l_specs, coverage_floor=D("0.85"))
    assert out.label == "Incomplete data" and out.label_kind == "incomplete_data"
    assert out.coverage_evaluated == 1 and out.coverage_applicable == n
    assert "L-01" not in out.not_evaluated and len(out.not_evaluated) == n - 1
    # even with an open finding, thin data outranks it
    f = run("L-09", v_l09()).findings[0]
    rr[[s.id for s in l_specs].index("L-09")] = RuleResult("L-09", Result.EXCEPTION, findings=(f,))
    assert rollup(rr, l_specs, coverage_floor=D("0.85")).label == "Incomplete data"


def test_rollup_counts_dq_out_and_not_evaluated_never_consistent():
    all_specs = all_rules()
    l_specs = [r for r in all_specs if r.counts_toward_coverage]
    rr = [RuleResult(s.id, Result.CONSISTENT) for s in l_specs]
    rr = [r if r.rule_id != "L-11" else RuleResult("L-11", Result.NOT_EVALUATED, not_evaluated_reason="no rate data") for r in rr]
    rr.append(RuleResult("DQ-01", Result.EXCEPTION))
    out = rollup(rr, all_specs, coverage_floor=D("0.85"))  # DQ-01 in `applicable` must still not count
    n = len(l_specs)  # every rule that counts toward coverage; DQ-01 is not one of them
    assert out.coverage_applicable == n and out.coverage_evaluated == n - 1
    assert out.coverage_ratio == (D(n - 1) / D(n)).quantize(D("0.0001"))
    assert out.label == "No open findings" and out.open_findings == 0
    assert out.rule_results["L-11"] == Result.NOT_EVALUATED
    assert out.not_evaluated == {"L-11": "no rate data"}
    # the authority behind the unevaluated rule is not reported as Consistent
    assert out.requirement_results["FAR 42.704"] == Result.NOT_EVALUATED


def test_rollup_open_findings_and_not_applicable():
    l_specs = [r for r in all_rules() if r.counts_toward_coverage]
    v = v_l09()
    f = run("L-09", v).findings[0]
    rr = [RuleResult(s.id, Result.CONSISTENT) for s in l_specs if s.id not in ("L-09", "L-11")]
    rr.append(RuleResult("L-09", Result.EXCEPTION, findings=(f,)))
    rr.append(RuleResult("L-11", Result.NOT_APPLICABLE))
    out = rollup(rr, l_specs, coverage_floor=D("0.85"))
    n = len(l_specs)
    assert out.coverage_applicable == n - 1 and out.coverage_evaluated == n - 1  # Not applicable is excluded
    assert (out.label, out.open_findings, out.high) == ("1 open finding", 1, 1)
    assert out.total_exposure_usd == f.exposure_usd
    closed = rollup(rr, l_specs, coverage_floor=D("0.85"), is_open=lambda x: False)
    assert closed.label == "No open findings"


# --------------------------------------------------------------------------- #
# copy lint
# --------------------------------------------------------------------------- #
def test_copy_lint():
    assert find_restricted(DISCLAIMER) == []
    assert find_restricted("Footer: " + DISCLAIMER + " End.") == []
    assert find_restricted("This company is compliant") == ["compliant"]
    assert find_restricted("Non-Compliant") == ["Non-Compliant"]
    for bad in ("certified", "audit-ready", "DCAA-approved", "we attest", "an attestation"):
        assert find_restricted(bad), bad
    assert find_restricted("Compliance memory and consistent data") == []
    # the exemption is exact-match only
    assert find_restricted("This is an analysis of submitted data. It is an attestation.")
    assert set(RESTRICTED_TERMS) >= {"compliant", "non-compliant", "certified", "audit-ready", "DCAA-approved", "attest", "attestation"}


# --------------------------------------------------------------------------- #
# explain: every rule and every finding kind, template-only, lint-clean
# --------------------------------------------------------------------------- #
def _sample_findings() -> list[Finding]:
    fs: list[Finding] = []
    fs += run("L-01", v_l01()).findings
    fs += run("L-02", v_l02()).findings
    fs += run("L-03", v_l03("1011000.00")).findings
    fs += run("L-05", v_l05(4), cluster_min_group_entries="10").findings
    fs += run("L-05", v_l05(4, history=False), cluster_min_group_entries="10").findings
    late_view = v_l05(0)
    late_view.time_entries = [replace(e, entered_at=datetime(2026, 8, 20, 9, 0)) for e in late_view.time_entries]
    fs += [f for f in run("L-05", late_view, cluster_min_group_entries="10").findings if f.computed["kind"] == "late_entries"]
    fs += run("L-06", v_l06(edits=12, undocumented=1, history="0.1200")).findings
    fs += run("L-09", v_l09()).findings
    fs += run("DQ-01", v_dq()).findings
    fs += run("L-02", v_l02_aggregate()).findings
    return fs  # the DCAA rules (L-08, L-11) are explained and lint-checked in tests/test_dcaa_rules.py


def test_explain_every_rule_is_lint_clean_and_uses_record_figures():
    seen = set()
    for f in _sample_findings():
        spec = get_rule(f.rule_id)
        ex = explain(f, spec)
        seen.add(f.rule_id)
        text = "\n".join([f.headline, f.severity_reason, ex.what_happened, ex.why_it_matters, ex.impact, ex.recommended_action])
        assert find_restricted(text) == [], (f.rule_id, find_restricted(text))
        assert "{" not in text and "}" not in text
        assert all([ex.what_happened, ex.why_it_matters, ex.impact, ex.recommended_action])
        assert f"${f.exposure_usd:,.2f}" in ex.impact or f.exposure_usd == 0
    assert seen == {"L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01"}


def test_explain_l01_matches_worked_example_shape():
    f = run("L-01", v_l01()).findings[0]
    ex = explain(f, get_rule("L-01"))
    assert "($178.50 - $142.00 = $36.50 per hour x 16.0 hours)" in ex.impact
    assert "$584.00" in ex.impact and "inconsistent with" in ex.why_it_matters
