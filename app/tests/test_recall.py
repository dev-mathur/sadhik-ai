"""Integration gate: the engine must find exactly what the generator injected.

FR §7 layer 2 (seeded-violation recall) plus layer 3 (determinism), the tiering
contract (§7.1) and the copy guardrail (invariant 8), all run against the REAL
synthetic Meridian dataset in data/out/.

`data/out/ground_truth.json` is written by the generator from its OWN arithmetic
(data/verify_ground_truth.py cross-checks it with no engine imports), so this is a
genuine independent comparison and not the engine grading itself.

Skipped until `python -m data.generate` has produced data/out/.
"""

from __future__ import annotations

import json
import random
import shutil
from decimal import Decimal
from pathlib import Path

import pytest

from engine.copy_lint import find_restricted
from engine.explain import explain
from engine.manifest import replay
from engine.rules.base import Result, Tier, get_rule
from engine.runner import execute_run

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "out"
CONFIG = (ROOT / "config" / "meridian-rules.yaml").read_text()
PERIOD = "2026-08"
D = Decimal

pytestmark = pytest.mark.skipif(
    not (DATA / "ground_truth.json").exists(),
    reason="data/out not generated yet (run: python -m data.generate)",
)


@pytest.fixture(scope="module")
def truth() -> dict:
    return json.loads((DATA / "ground_truth.json").read_text())


@pytest.fixture(scope="module")
def close_run():
    return execute_run(DATA, CONFIG, PERIOD, Tier.CLOSE, "RUN-TEST-CLOSE")


def _by_fp(findings):
    return {f.fingerprint: f for f in findings}


# --------------------------------------------------------------------------- #
# Recall: detected == injected
# --------------------------------------------------------------------------- #
def test_detected_set_equals_injected_set(close_run, truth):
    injected = {i["fingerprint"] for i in truth["injected"]}
    detected = {f.fingerprint for f in close_run.findings}
    assert detected == injected, (
        f"missed: {sorted(injected - detected)}  unexpected: {sorted(detected - injected)}"
    )
    assert len(close_run.findings) == truth["expected_findings"]


def test_every_finding_matches_its_injected_exposure_to_the_cent(close_run, truth):
    got = _by_fp(close_run.findings)
    for inj in truth["injected"]:
        f = got[inj["fingerprint"]]
        assert f.exposure_usd == D(inj["exposure_usd"]), (
            f"{inj['fingerprint']}: engine {f.exposure_usd} != injected {inj['exposure_usd']}"
        )


def test_severity_matches_expected(close_run, truth):
    got = _by_fp(close_run.findings)
    for inj in truth["injected"]:
        assert got[inj["fingerprint"]].severity.value == inj["severity"], inj["fingerprint"]


def test_affected_employees_match_injected(close_run, truth):
    got = _by_fp(close_run.findings)
    for inj in truth["injected"]:
        assert set(got[inj["fingerprint"]].employees) == set(inj.get("employees", [])), inj["fingerprint"]


def test_total_exposure_is_deduplicated_and_matches(close_run, truth):
    """M13: an entry caught by two rules counts once. The two overlaps in the answer
    key ($1,644.00 L-05/L-09 and $970.20 L-05/L-06) must come out of the gross."""
    gross = sum(D(i["exposure_usd"]) for i in truth["injected"])
    overlap = sum(D(o["usd"]) for o in truth["overlaps"])
    assert close_run.total_exposure_usd == D(truth["expected_total_exposure_usd"])
    assert close_run.total_exposure_usd == gross - overlap
    assert close_run.total_exposure_usd < gross  # de-dup actually did something


def test_status_rollup_matches(close_run, truth):
    r = close_run.rollup
    by_sev = {"high": 0, "medium": 0, "low": 0}
    for i in truth["injected"]:
        by_sev[i["severity"]] += 1
    assert (r.high, r.medium, r.low) == (by_sev["high"], by_sev["medium"], by_sev["low"])
    assert r.label_kind == "open_findings"
    assert r.open_findings == truth["expected_findings"]
    # Coverage is per domain. Labor no longer carries L-11 (it moved to DCAA cost accounting), and
    # L-11 now has its rate data, so everything applicable was evaluated. DQ-01 never counts.
    for dom, want in truth["domains"].items():
        d = close_run.domain_rollups[dom]
        assert [d.coverage_evaluated, d.coverage_applicable] == want["coverage"], dom
    assert (r.coverage_evaluated, r.coverage_applicable) == (truth["expected_coverage"]["evaluated"], truth["expected_coverage"]["applicable"]) == (11, 11)


def test_clean_rules_are_consistent_and_missing_data_is_not_a_pass(close_run, truth):
    results = close_run.rollup.rule_results
    for rid in truth["expected_consistent"]:
        assert results[rid] == Result.CONSISTENT, rid
    for rid in truth["expected_not_evaluated"]:
        assert results[rid] == Result.NOT_EVALUATED, rid
        assert results[rid] != Result.CONSISTENT  # the whole point of §6.2


def test_no_accidental_findings_from_baseline_noise(close_run, truth):
    """The generator promises noise creates no findings. This catches a rule that
    over-fires (e.g. the separate L-05 late-entries finding)."""
    assert not [f for f in close_run.findings if f.fingerprint.startswith("L-05|late_entries")]
    expected = {i["fingerprint"] for i in truth["injected"]}
    assert {f.fingerprint for f in close_run.findings} <= expected


def test_sub_materiality_gap_is_logged_but_raises_no_finding(close_run, truth):
    logged = [x for r in close_run.rule_results if r.rule_id == "L-02" for x in r.logged_below_materiality]
    assert any("E-0208" in json.dumps(x, default=str) for x in logged), "E-0208 gap should be logged"
    assert "L-02|E-0208|2026-08-A" not in {f.fingerprint for f in close_run.findings}
    assert not any(f.employees == ("E-0208",) for f in close_run.findings)


# --------------------------------------------------------------------------- #
# Determinism (FR §7 layer 3)
# --------------------------------------------------------------------------- #
def test_same_inputs_same_output_byte_for_byte(close_run):
    again = execute_run(DATA, CONFIG, PERIOD, Tier.CLOSE, "RUN-TEST-CLOSE-2")
    assert again.canonical() == close_run.canonical()


def _comparable(out):
    return sorted(
        (f.fingerprint, str(f.exposure_usd), f.severity.value, tuple(sorted(f.employees)))
        for f in out.findings
    ) + [("TOTAL", str(out.total_exposure_usd), "", ())]


def test_row_order_does_not_change_findings(close_run, tmp_path):
    """Shuffle the rows of every big export. Evidence row numbers legitimately move,
    so compare the findings themselves, not their evidence citations."""
    shuffled = tmp_path / "shuffled"
    shutil.copytree(DATA, shuffled)
    rng = random.Random(1)
    for name in sorted(p.name for p in shuffled.glob("*.csv")):
        if name in {"charge_codes.csv", "labor_category_crosswalk.csv", "loaded_rates.csv"}:
            continue
        path = shuffled / name
        lines = path.read_text().splitlines()
        header, rows = lines[0], lines[1:]
        rng.shuffle(rows)
        path.write_text("\n".join([header, *rows]) + "\n")
    out = execute_run(shuffled, CONFIG, PERIOD, Tier.CLOSE, "RUN-TEST-SHUFFLED")
    assert _comparable(out) == _comparable(close_run)


def test_replay_reproduces_the_run(close_run):
    res = replay(close_run.manifest, DATA, CONFIG, expected_canonical=close_run.canonical())
    assert res.identical, res.diff
    assert res.findings_compared == len(close_run.findings)


def test_replay_refuses_when_an_input_changed(close_run, tmp_path):
    from engine.results import RunBlocked

    changed = tmp_path / "changed"
    shutil.copytree(DATA, changed)
    tk = changed / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text() + "\n")
    with pytest.raises(RunBlocked) as e:
        replay(close_run.manifest, changed, CONFIG, expected_canonical=close_run.canonical())
    assert e.value.code == "inputs_changed"


# --------------------------------------------------------------------------- #
# Tiering (design §7.1): a partial run never reports a pass for what it did not check
# --------------------------------------------------------------------------- #
def test_fast_tier_evaluates_only_timekeeping_rules(truth):
    out = execute_run(DATA, CONFIG, PERIOD, Tier.FAST, "RUN-TEST-FAST")
    res = out.rollup.rule_results
    for rid in ("L-05", "L-06", "L-08", "L-09"):  # L-08 needs timekeeping only: fast tier
        assert res[rid] != Result.NOT_EVALUATED, rid
    for rid in ("L-01", "L-02", "L-03", "L-11"):
        assert res[rid] == Result.NOT_EVALUATED, f"{rid} must be Not evaluated at the fast tier, never a pass"
        assert res[rid] != Result.CONSISTENT

    fast_fps = {f.fingerprint for f in out.findings}
    assert fast_fps == {"L-05|C-7302", "L-06|2026-08", "L-09|C-7302", "L-08|E-0143|2026-08"}
    # same conditions, same dollars as the full run
    want = {i["fingerprint"]: D(i["exposure_usd"]) for i in truth["injected"]}
    for f in out.findings:
        assert f.exposure_usd == want[f.fingerprint]
    # a run's own coverage is scoped to its tier: 4 rules were in scope and all 4 ran (the API status
    # measures against every rule across runs, which is where "Incomplete data" comes from)
    assert (out.rollup.coverage_evaluated, out.rollup.coverage_applicable) == (4, 4)


# --------------------------------------------------------------------------- #
# Copy guardrail (invariant 8) on every generated sentence
# --------------------------------------------------------------------------- #
def test_generated_text_contains_no_restricted_terms(close_run):
    for f in close_run.findings:
        e = explain(f, get_rule(f.rule_id))
        for text in (f.headline, e.what_happened, e.why_it_matters, e.impact, e.recommended_action):
            assert find_restricted(text) == [], (f.fingerprint, find_restricted(text), text)


# --------------------------------------------------------------------------- #
# DCAA cost accounting domain
# --------------------------------------------------------------------------- #
def _scratch(tmp_path, name="scratch") -> Path:
    d = tmp_path / name
    shutil.copytree(DATA, d)
    return d


def _rewrite_csv(path: Path, fn):
    import csv
    import io

    rows = list(csv.reader(path.open()))
    rows = fn(rows)
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    path.write_text(buf.getvalue())


def test_domain_totals_split_cleanly(close_run, truth):
    """Labor keeps its original 41,298.93. DCAA cost accounting carries L-08, L-11 and the C-rules' findings."""
    lab, dcaa = close_run.domain_rollups["labor"], close_run.domain_rollups["dcaa_cost_accounting"]
    assert lab.total_exposure_usd == D("41298.93") == D(truth["labor_domain_total_exposure_usd"]) and lab.open_findings == 8
    dcaa_rules = set(truth["domains"]["dcaa_cost_accounting"]["rules"])
    want = sum(D(i["exposure_usd"]) for i in truth["injected"] if i["rule_id"] in dcaa_rules)
    assert dcaa.total_exposure_usd == want == D(truth["dcaa_domain_total_exposure_usd"]) == D("61192.58")
    assert dcaa.open_findings == 6  # L-08, L-11, C-01 (two pools), C-02, C-03
    assert lab.total_exposure_usd + dcaa.total_exposure_usd == close_run.total_exposure_usd  # no cross-domain overlap


def test_l03_stays_a_labor_only_tie_out_now_that_non_labor_cost_lines_exist(close_run):
    assert close_run.rollup.rule_results["L-03"] == Result.CONSISTENT
    assert not any(f.rule_id == "L-03" for f in close_run.findings)


def test_l03_without_the_confirmed_account_table_sees_non_labor_cost_as_labor(tmp_path):
    """Why account_categories.csv exists: without it the tie-out compares payroll to ALL GL cost."""
    d = _scratch(tmp_path)
    (d / "account_categories.csv").unlink()
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-NOCAT")
    l03 = [f for f in out.findings if f.rule_id == "L-03"]
    assert l03 and l03[0].exposure_usd == D("1265370.97")


def test_removing_the_rate_file_makes_l11_not_evaluated_and_never_a_pass(tmp_path):
    d = _scratch(tmp_path)
    src = json.loads((d / "sources.json").read_text())
    src["sources"].pop("rate_data")
    src["absent"] = sorted(set(src.get("absent", [])) | {"rate_data"})
    (d / "sources.json").write_text(json.dumps(src))
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-NORATES")
    assert out.rollup.rule_results["L-11"] == Result.NOT_EVALUATED
    assert not any(f.rule_id == "L-11" for f in out.findings)
    for rid in ("L-11", "C-02", "C-03"):  # all three need the rate data
        assert out.rollup.rule_results[rid] == Result.NOT_EVALUATED, rid
    assert not any(f.rule_id in ("L-11", "C-02", "C-03") for f in out.findings)
    dcaa = out.domain_rollups["dcaa_cost_accounting"]
    assert (dcaa.coverage_evaluated, dcaa.coverage_applicable) == (2, 5)  # L-08 (timekeeping) and C-01 (GL) still ran
    # thin data never reads clean: one thin domain makes the overall label Incomplete data
    assert dcaa.label_kind == "incomplete_data" and out.rollup.label_kind == "incomplete_data"
    # labor is untouched, and the rules that did not need rate data still found what they found
    assert out.domain_rollups["labor"].total_exposure_usd == D("41298.93")
    assert out.total_exposure_usd == D("41298.93") + D("3652.00") + D("25400.00")  # + L-08 + C-01


def test_a_provisional_rate_closer_to_actual_removes_the_l11_finding(tmp_path):
    """Mutation: provisional G&A 0.1000 -> 0.1060 makes the drift (0.1080-0.1060)/0.1060 = 1.9%, below Watch."""
    d = _scratch(tmp_path)
    _rewrite_csv(d / "meridian_rates_2026-08.csv",
                 lambda rows: [[*r[:2], "0.1060", *r[3:]] if r[0] == "ga" else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-PROV")
    assert not any(f.rule_id == "L-11" for f in out.findings)
    assert out.total_exposure_usd == D("102491.51") - D("23477.34")


def test_moving_the_hours_back_removes_the_l08_finding(tmp_path):
    d = _scratch(tmp_path)
    moved = {"TE-100104", "TE-100855", "TE-101097", "TE-101331", "TE-102324"}
    _rewrite_csv(d / "meridian_time_2026-08.csv",
                 lambda rows: [[*r[:4], "C8841-DEV", *r[5:]] if r[0] in moved else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-BACK")
    assert not any(f.rule_id == "L-08" for f in out.findings)
    assert out.total_exposure_usd == D("102491.51") - D("3652.00")


def test_a_partial_reversal_shrinks_the_l08_exposure_by_exactly_the_hours(tmp_path):
    """Move two of the five entries back: excess 40.0 -> 24.0 h, still flagged; 24.0 x 91.30 = 2191.20."""
    d = _scratch(tmp_path)
    back = {"TE-100104", "TE-100855"}
    _rewrite_csv(d / "meridian_time_2026-08.csv",
                 lambda rows: [[*r[:4], "C8841-DEV", *r[5:]] if r[0] in back else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-PART")
    l08 = next(f for f in out.findings if f.rule_id == "L-08")
    assert l08.exposure_usd == D("2191.20")


def test_a_disabled_domain_is_not_run_and_drops_out_of_the_totals(tmp_path):
    import re

    labor_only = re.sub(r"(?m)^domains:.*\n(?:[ \t]+.*\n)*", "domains: [labor]\n", CONFIG)
    out = execute_run(DATA, labor_only, PERIOD, Tier.CLOSE, "RUN-LABORONLY")
    assert set(out.domain_rollups) == {"labor"}
    assert not any(f.rule_id in ("L-08", "L-11") for f in out.findings)
    assert "L-08" not in out.rollup.rule_results and "L-11" not in out.rollup.rule_results
    assert out.total_exposure_usd == D("41298.93")


# --------------------------------------------------------------------------- #
# C-01 unallowable cost, C-02 incurred cost submission, C-03 rate ceiling: mutation checks
# --------------------------------------------------------------------------- #
def test_new_rules_findings_and_severity_match_the_key(close_run, truth):
    got = {f.fingerprint: f for f in close_run.findings}
    for fp, cents, sev in (("C-01|ga|2026-08", "23550.00", "high"), ("C-01|overhead|2026-08", "1850.00", "medium"),
                           ("C-02|FY2025|2026-08", "0.00", "high"), ("C-03|overhead|2026-08", "8663.24", "high")):
        assert got[fp].exposure_usd == D(cents) and got[fp].severity.value == sev, fp
    assert got["C-02|FY2025|2026-08"].computed["days_overdue"] == D("62")
    assert got["C-03|overhead|2026-08"].computed["actual_above_ceiling"] is True


def test_the_conditional_gifts_line_is_logged_with_its_row_never_raised(close_run):
    logged = [x for r in close_run.rule_results if r.rule_id == "C-01" for x in r.logged_below_materiality]
    assert [(x["account"], x["amount"]) for x in logged] == [("6525 Gifts and Employee Awards", "4100.00")]
    assert not any(f.fingerprint == "C-01|ga|conditional|2026-08" for f in close_run.findings)


def test_reclassifying_an_account_as_allowable_removes_exactly_its_amount(tmp_path):
    """Mark Entertainment (14,800.00) allowable: the G&A finding falls from 23,550.00 to 8,750.00."""
    d = _scratch(tmp_path)
    _rewrite_csv(d / "account_allowability.csv",
                 lambda rows: [[r[0], "allowable", ""] if r[0] == "6535 Entertainment and Events" else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-ALLOW")
    ga = next(f for f in out.findings if f.fingerprint == "C-01|ga|2026-08")
    assert ga.exposure_usd == D("8750.00")  # 6,500 lobbying + 2,250 fines
    assert out.total_exposure_usd == D("102491.51") - D("14800.00")


def test_without_the_allowability_table_c01_is_not_evaluated_and_never_a_pass(tmp_path):
    d = _scratch(tmp_path)
    (d / "account_allowability.csv").unlink()
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-NOALLOW")
    assert out.rollup.rule_results["C-01"] == Result.NOT_EVALUATED
    assert not any(f.rule_id == "C-01" for f in out.findings)
    dcaa = out.domain_rollups["dcaa_cost_accounting"]
    assert (dcaa.coverage_evaluated, dcaa.coverage_applicable) == (4, 5) and dcaa.label_kind == "incomplete_data"
    assert out.rollup.label_kind == "incomplete_data"  # 4 of 5 = 0.80 is below the 0.85 floor
    assert out.total_exposure_usd == D("102491.51") - D("25400.00")


def test_a_submission_made_on_time_removes_the_c02_finding(tmp_path):
    d = _scratch(tmp_path)
    _rewrite_csv(d / "ics_submissions.csv", lambda rows: [[*r[:2], "2026-06-15", r[3]] if r[0] == "FY2025" else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-ICSOK")
    assert not any(f.rule_id == "C-02" for f in out.findings) and out.rollup.rule_results["C-02"] == Result.CONSISTENT
    assert out.total_exposure_usd == D("102491.51")  # C-02 carries no dollars
    assert len(out.findings) == 13 and out.rollup.high == 7


def test_a_late_submission_is_history_not_a_finding(tmp_path):
    d = _scratch(tmp_path)
    _rewrite_csv(d / "ics_submissions.csv", lambda rows: [[*r[:2], "2026-07-14", r[3]] if r[0] == "FY2025" else r for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-ICSLATE")
    assert not any(f.rule_id == "C-02" for f in out.findings)
    (log,) = [x for r in out.rule_results if r.rule_id == "C-02" for x in r.logged_below_materiality]
    assert (log["fiscal_year"], log["days_late"]) == ("FY2025", 14)


def test_raising_the_ceiling_to_the_provisional_rate_removes_c03_and_one_basis_point_costs_173_26(tmp_path):
    for ceiling, expect in (("0.1800", None), ("0.1799", D("173.26"))):  # 0.0001 x 1,732,648.59 = 173.2648...
        d = _scratch(tmp_path, f"ceil{ceiling}")
        _rewrite_csv(d / "meridian_rates_2026-08.csv", lambda rows: [[*r[:6], ceiling] if r[0] == "overhead" else r for r in rows])
        out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-CEIL")
        c03 = [f for f in out.findings if f.rule_id == "C-03"]
        if expect is None:
            assert not c03 and out.total_exposure_usd == D("102491.51") - D("8663.24")
        else:
            assert c03[0].exposure_usd == expect and out.total_exposure_usd == D("102491.51") - D("8663.24") + expect


def test_a_rate_file_without_the_ceiling_column_still_loads_and_c03_is_not_evaluated(tmp_path):
    """Backward compatibility: Ceiling Rate is optional. An older rate file must not block the run."""
    d = _scratch(tmp_path)
    _rewrite_csv(d / "meridian_rates_2026-08.csv", lambda rows: [r[:6] for r in rows])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-NOCEIL")
    assert out.rollup.rule_results["C-03"] == Result.NOT_EVALUATED
    assert not any(f.rule_id == "C-03" for f in out.findings)
    assert any(f.rule_id == "L-11" for f in out.findings)  # the rest of the rate data still works
    assert out.total_exposure_usd == D("102491.51") - D("8663.24")


def test_a_pool_that_lost_its_gl_lines_does_not_become_a_phantom_c01_or_l11_finding(tmp_path):
    """No GL lines for G&A at all: L-11 skips the pool (it is missing data, not a -100% drift)."""
    d = _scratch(tmp_path)
    _rewrite_csv(d / "qb_gl_2026-08.csv", lambda rows: [r for r in rows if r[5] != "ga"])
    out = execute_run(d, CONFIG, PERIOD, Tier.CLOSE, "RUN-NOGA")
    assert not any(f.fingerprint.startswith(("L-11|ga", "C-01|ga")) for f in out.findings)
