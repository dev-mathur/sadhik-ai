"""Ingest: mapping, lineage, tier gating, blocking conditions (contracts/DATA_SPEC.md)."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from engine.ingest import load_working_view
from engine.results import RunBlocked
from engine.rules.base import Tier
from tests.conftest import FIXTURES, make_dcaa_data

MINI = FIXTURES / "mini"
D = Decimal


def sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture()
def data(tmp_path):
    dst = tmp_path / "data"
    shutil.copytree(MINI, dst)
    return dst


def test_close_tier_loads_everything_present():
    r = load_working_view(MINI, "2026-08", Tier.CLOSE)
    v = r.view
    assert r.sources_present == frozenset({"timekeeping", "time_edits", "payroll", "gl", "hris", "contracts"})
    assert r.sources_absent == frozenset({"rate_data"})
    assert v.sources_present == r.sources_present
    assert (len(v.time_entries), len(v.time_edits), len(v.pay_records), len(v.gl_lines)) == (5, 2, 3, 2)
    assert (len(v.employees), len(v.contracts)) == (2, 2)
    assert r.mapping_versions == {"unanet": 3, "adp": 2, "quickbooks": 2, "bamboo": 1}


def test_mapping_turns_source_names_into_canonical_fields():
    v = load_working_view(MINI, "2026-08", Tier.CLOSE).view
    e = v.time_entries[0]
    assert e.entry_id == "TE-000001" and e.employee_id == "E-0001" and e.charge_code == "C8841-DEV"
    assert e.labor_category == "Data Engineer II" and e.hours == D("8.0")
    # the trap: Sub Dt is the SUBMISSION timestamp, not a work date
    assert e.work_date == date(2026, 8, 3)
    assert e.submitted_at == datetime(2026, 8, 7, 9, 0, 0)
    assert e.entered_at == datetime(2026, 8, 3, 17, 5, 0)
    assert e.approved_by == "E-0009" and e.approved_at == datetime(2026, 8, 8, 10, 0, 0)
    # empty submission / approval cells become None, not "" or an epoch
    last = v.time_entries[4]
    assert last.submitted_at is None and last.approved_by is None and last.approved_at is None
    assert all(isinstance(x.hours, Decimal) for x in v.time_entries)


def test_other_sources_are_mapped():
    v = load_working_view(MINI, "2026-08", Tier.CLOSE).view
    assert v.time_edits[0].reason == "Timesheet correction memo 2026-08-10"
    assert v.time_edits[1].reason is None  # empty Reason == undocumented edit
    assert v.time_edits[1].editor_id == "E-0002" and v.time_edits[1].old_value == "8.0"
    p = v.pay_records[1]
    assert (p.employee_id, p.pay_period, p.total_hours, p.gross_pay) == ("E-0001", "2026-08-B", D("88.0"), D("4400.00"))
    g = v.gl_lines
    assert g[0].pool is None and g[0].cost_type == "direct" and g[1].pool == "overhead" and g[1].amount == D("1200.50")
    a, b = v.employees
    assert a.exempt_status == "exempt" and b.exempt_status == "non_exempt"
    assert a.term_date is None and b.term_date == date(2026, 9, 30) and a.hire_date == date(2021, 3, 1)
    assert a.title == "Data Engineer" and a.home_department == "Engineering"


def test_lineage_row_is_one_based_including_header():
    r = load_working_view(MINI, "2026-08", Tier.CLOSE)
    v = r.view
    tk = MINI / "meridian_time_2026-08.csv"
    assert [e.lineage.row for e in v.time_entries] == [2, 3, 4, 5, 6]  # first data row is 2
    assert v.time_entries[0].lineage.cite() == "meridian_time_2026-08.csv:2"
    assert all(e.lineage.sha256 == sha(tk) for e in v.time_entries)
    lines = tk.read_text().splitlines()
    assert lines[v.time_entries[2].lineage.row - 1].startswith("TE-000003")
    assert [x.lineage.row for x in v.time_edits] == [2, 3]
    assert [x.lineage.row for x in v.pay_records] == [2, 3, 4]
    assert [x.lineage.row for x in v.employees] == [2, 3]
    assert v.gl_lines[1].lineage.source_file == "qb_gl_2026-08.csv"
    assert [c.lineage.row for c in v.contracts] == [1, 2]


def test_reference_tables_and_contracts_and_baselines():
    v = load_working_view(MINI, "2026-08", Tier.CLOSE).view
    assert v.charge_codes["OH-100"].contract_id is None and v.charge_codes["OH-100"].pool == "overhead"
    assert v.charge_codes["C8841-DEV"].contract_id == "C-8841" and v.charge_codes["C8841-DEV"].cost_type == "direct"
    assert v.category_crosswalk == {"Data Engineer": "Data Engineer II", "Sr. Data Engineer": "Data Engineer III"}
    assert v.loaded_rates["E-0002"] == D("102.75")
    c = v.contract("C-8841")
    assert c.pop_end == date(2026, 9, 30) and c.ceiling == D("6000000.00")
    assert c.category("Data Engineer III").ceiling_rate == D("178.50")
    assert v.contract("C-7302").pop_end == date(2026, 8, 15)
    assert v.baselines == {
        "2026-06": {"M5.company_window_share": D("0.13"), "M6.edit_rate": D("0.0350")},
        "2026-07": {"M5.company_window_share": D("0.15"), "M6.edit_rate": D("0.0360")},
    }
    assert v.contract_for_charge_code("C7302-DEV").contract_id == "C-7302"


def test_inputs_carry_hash_rows_and_data_as_of():
    r = load_working_view(MINI, "2026-08", Tier.CLOSE)
    by = {i.source: i for i in r.inputs}
    assert by["timekeeping"].file == "meridian_time_2026-08.csv"
    assert by["timekeeping"].sha256 == sha(MINI / "meridian_time_2026-08.csv")
    assert by["timekeeping"].rows == 5 and by["hris"].data_as_of == "2026-08-01"
    assert by["contracts"].rows == 2
    refs = {i.source for i in r.reference_tables}
    assert {"charge_codes", "category_crosswalk", "loaded_rates", "metric_history"} <= refs


def test_tier_gating_reports_the_rest_absent_never_an_error():
    r = load_working_view(MINI, "2026-08", Tier.FAST)
    assert r.sources_present == frozenset({"timekeeping", "time_edits", "contracts"})
    assert r.sources_absent == frozenset({"payroll", "gl", "hris", "rate_data"})
    assert r.view.pay_records == [] and r.view.gl_lines == [] and r.view.employees == []
    assert r.mapping_versions == {"unanet": 3}
    pp = load_working_view(MINI, "2026-08", Tier.PAY_PERIOD)
    assert pp.sources_present == frozenset({"timekeeping", "time_edits", "contracts", "payroll", "hris"})
    assert "gl" in pp.sources_absent and pp.view.gl_lines == []


def test_source_missing_from_sources_json_is_absent_not_an_error(data):
    import json

    idx = json.loads((data / "sources.json").read_text())
    del idx["sources"]["gl"]
    idx["absent"].append("gl")
    (data / "sources.json").write_text(json.dumps(idx))
    (data / "qb_gl_2026-08.csv").unlink()
    r = load_working_view(data, "2026-08", Tier.CLOSE)
    assert "gl" in r.sources_absent and r.view.gl_lines == []


def test_declared_source_with_missing_file_blocks(data):
    (data / "adp_payroll_2026-08.csv").unlink()
    with pytest.raises(RunBlocked) as ei:
        load_working_view(data, "2026-08", Tier.CLOSE)
    assert ei.value.code == "missing_input"


def test_unmapped_charge_code_blocks_the_run(data):
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text() + "TE-000006,E-0001,2026-08-06,8.0,ZZ-999,Data Engineer II,2026-08-06T17:00:00,,,\n")
    with pytest.raises(RunBlocked) as ei:
        load_working_view(data, "2026-08", Tier.CLOSE)
    assert ei.value.code == "unmapped_charge_code"
    assert "ZZ-999" in ei.value.message and "meridian_time_2026-08.csv:7" in ei.value.message


def test_unmapped_charge_code_blocks_even_at_fast_tier(data):
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text() + "TE-000006,E-0001,2026-08-06,8.0,ZZ-999,Data Engineer II,2026-08-06T17:00:00,,,\n")
    with pytest.raises(RunBlocked):
        load_working_view(data, "2026-08", Tier.FAST)


def test_header_drift_blocks_rather_than_silently_misreading(data):
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text().replace("Sub Dt", "Submitted"))
    with pytest.raises(RunBlocked) as ei:
        load_working_view(data, "2026-08", Tier.CLOSE)
    assert ei.value.code == "schema_mismatch" and "Sub Dt" in ei.value.message


def test_unparseable_value_blocks_with_file_and_row(data):
    tk = data / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text().replace(",7.5,", ",seven,"))
    with pytest.raises(RunBlocked) as ei:
        load_working_view(data, "2026-08", Tier.CLOSE)
    assert ei.value.code == "bad_value" and "meridian_time_2026-08.csv:4" in ei.value.message


def test_period_mismatch_blocks(data):
    with pytest.raises(RunBlocked) as ei:
        load_working_view(data, "2026-09", Tier.CLOSE)
    assert ei.value.code == "period_mismatch"


def test_missing_sources_index_blocks(tmp_path):
    with pytest.raises(RunBlocked) as ei:
        load_working_view(tmp_path, "2026-08", Tier.CLOSE)
    assert ei.value.code == "missing_input"


def test_current_period_history_is_not_used_as_its_own_baseline(data):
    (data / "metric_history.json").write_text(
        '{"metrics":{"M4.late_rate":{"2026-07":"0.03","2026-08":"0.99"}}}'
    )
    r = load_working_view(data, "2026-08", Tier.CLOSE)
    assert r.view.baselines == {"2026-07": {"M4.late_rate": D("0.03")}}
    assert any("2026-08" in w for w in r.warnings)


def test_ingest_is_deterministic():
    a = load_working_view(MINI, "2026-08", Tier.CLOSE)
    b = load_working_view(MINI, "2026-08", Tier.CLOSE)
    assert a.view == b.view and a.inputs == b.inputs


# --- DCAA additions: rate_data, account_categories, classification_history -----
@pytest.fixture()
def dcaa(tmp_path):
    return make_dcaa_data(tmp_path / "dcaa")


def _edit(path, old, new):
    text = path.read_text()
    assert old in text, (old, text)
    path.write_text(text.replace(old, new, 1))


def test_rate_data_is_mapped_into_rate_agreements(dcaa):
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    v = r.view
    assert "rate_data" in r.sources_present and r.sources_absent == frozenset()
    assert [(a.pool, a.base_definition, a.provisional_rate) for a in v.rate_agreements] == [
        ("fringe", "direct_labor", D("0.2800")), ("overhead", "direct_labor", D("0.1800")),
        ("ga", "total_cost_input", D("0.1000")),
    ]
    ga = v.rate_agreements[2]
    assert ga.effective_from == date(2026, 1, 1) and ga.effective_to == date(2026, 12, 31)
    assert ga.source_document == "FY2026 provisional billing rate schedule"
    assert isinstance(ga.provisional_rate, Decimal) and str(ga.provisional_rate) == "0.1000"  # exact, not a float
    assert r.mapping_versions["rate_data"] == 1


def test_rate_data_lineage_is_the_one_based_csv_row_including_header(dcaa):
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    f = dcaa / "meridian_rates_2026-08.csv"
    assert [a.lineage.row for a in v.rate_agreements] == [2, 3, 4]
    assert v.rate_agreements[2].lineage.cite() == "meridian_rates_2026-08.csv:4"
    assert all(a.lineage.sha256 == sha(f) for a in v.rate_agreements)
    assert f.read_text().splitlines()[v.rate_agreements[1].lineage.row - 1].startswith("overhead,")


def test_rate_data_is_an_input_with_hash_rows_and_data_as_of(dcaa):
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    by = {i.source: i for i in r.inputs}
    i = by["rate_data"]
    assert (i.file, i.rows, i.data_as_of) == ("meridian_rates_2026-08.csv", 3, "2026-08-31")
    assert i.sha256 == sha(dcaa / "meridian_rates_2026-08.csv")


def test_rate_data_loads_only_at_the_close_tier(dcaa):
    for tier in (Tier.FAST, Tier.PAY_PERIOD):
        r = load_working_view(dcaa, "2026-08", tier)
        assert "rate_data" in r.sources_absent and r.view.rate_agreements == []
        assert "rate_data" not in r.mapping_versions
    # a malformed rate file cannot block a run that never reads it
    (dcaa / "meridian_rates_2026-08.csv").write_text("garbage\n")
    assert load_working_view(dcaa, "2026-08", Tier.PAY_PERIOD).view.rate_agreements == []


def test_rate_data_only_loads_when_sources_json_declares_it(dcaa):
    idx = json.loads((dcaa / "sources.json").read_text())
    del idx["sources"]["rate_data"]
    (dcaa / "sources.json").write_text(json.dumps(idx))
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)  # the file is still on disk, but not declared
    assert "rate_data" in r.sources_absent and r.view.rate_agreements == []


def test_declared_rate_file_that_is_missing_blocks(dcaa):
    (dcaa / "meridian_rates_2026-08.csv").unlink()
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "missing_input" and "rate_data" in ei.value.message


@pytest.mark.parametrize("old,new,why", [
    ("fringe,direct_labor", "payroll,direct_labor", "pool 'payroll'"),
    ("fringe,direct_labor", "Fringe,direct_labor", "pool 'Fringe'"),
    ("fringe,direct_labor", ",direct_labor", "pool is empty"),
    ("fringe,direct_labor", "fringe,gross_receipts", "base definition 'gross_receipts'"),
    ("fringe,direct_labor", "fringe,", "base_definition is empty"),
    ("0.2800", "abc", "'abc' is not a number"),
    ("0.2800", "", "provisional_rate is empty"),
    ("0.2800", "28", "between 0 and 1"),
    ("0.2800", "1", "between 0 and 1"),
    ("0.2800", "1.0000", "between 0 and 1"),
    ("0.2800", "0", "between 0 and 1"),
    ("0.2800", "0.0000", "between 0 and 1"),
    ("0.2800", "-0.2800", "between 0 and 1"),
    ("0.2800", "NaN", "not a finite number"),
    ("0.2800", "28%", "'28%' is not a number"),
    ("2026-01-01,2026-12-31", "2027-01-01,2026-12-31", "is after effective to"),
    ("2026-01-01,2026-12-31", "2026-01-01,2025-12-31", "is after effective to"),
    ("2026-01-01,2026-12-31", "01/01/2026,2026-12-31", "not an ISO date"),
    ("2026-01-01,2026-12-31", "2026-01-01,", "effective_to is empty"),
])
def test_rate_data_bad_values_block_with_file_and_row(dcaa, old, new, why):
    _edit(dcaa / "meridian_rates_2026-08.csv", old, new)  # always hits the first data row (row 2)
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "bad_value"
    assert "meridian_rates_2026-08.csv:2" in ei.value.message and why in ei.value.message


def test_rate_data_error_names_the_offending_row(dcaa):
    _edit(dcaa / "meridian_rates_2026-08.csv", "ga,total_cost_input,0.1000", "ga,total_cost_input,10")
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "bad_value" and "meridian_rates_2026-08.csv:4" in ei.value.message


def test_rate_data_boundaries_are_accepted(dcaa):
    f = dcaa / "meridian_rates_2026-08.csv"
    f.write_text(
        "Pool,Base Definition,Provisional Rate,Effective From,Effective To,Source Document\n"
        "fringe,direct_labor,0.0001,2026-08-31,2026-08-31,one day\n"      # from == to is allowed
        "overhead,direct_labor,0.9999,2026-01-01,2026-12-31,\n"           # empty source document is allowed
    )
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    assert [str(a.provisional_rate) for a in v.rate_agreements] == ["0.0001", "0.9999"]
    assert v.rate_agreements[1].source_document == ""


def test_rate_header_drift_blocks(dcaa):
    _edit(dcaa / "meridian_rates_2026-08.csv", "Provisional Rate", "Rate")
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "schema_mismatch" and "Provisional Rate" in ei.value.message


def test_rate_mapping_that_does_not_map_a_required_field_blocks(dcaa, tmp_path):
    src = Path(__file__).resolve().parents[1] / "config" / "mappings"
    m = tmp_path / "mappings"
    shutil.copytree(src, m)
    _edit(m / "ratemodel.yaml", '    "Source Document": source_document\n', "")
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE, mappings_dir=m)
    assert ei.value.code == "bad_mapping" and "source_document" in ei.value.message


# --- optional reference tables ---
def test_optional_tables_absent_are_silently_empty():
    r = load_working_view(MINI, "2026-08", Tier.CLOSE)  # tests/fixtures/mini has neither
    assert r.view.account_categories == {} and r.view.classification_history == {}
    assert r.view.rate_agreements == []
    assert r.warnings == ()
    assert {"account_categories", "classification_history"}.isdisjoint(i.source for i in r.reference_tables)
    assert all(n not in [i.file for i in r.reference_tables] for n in ("account_categories.csv", "classification_history.json"))


def test_optional_tables_load_at_every_tier_when_present(dcaa):
    for tier in (Tier.FAST, Tier.PAY_PERIOD, Tier.CLOSE):
        v = load_working_view(dcaa, "2026-08", tier).view
        assert v.account_categories == {"6000": "labor", "6100": "labor", "6900": "non_labor"}
        assert set(v.classification_history) == {"E-0001", "E-0002"}


def test_each_optional_table_is_independent(dcaa):
    (dcaa / "classification_history.json").unlink()
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    assert v.account_categories and v.classification_history == {}
    (dcaa / "account_categories.csv").unlink()
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    assert v.account_categories == {} and v.classification_history == {}


def test_classification_history_shape_and_exact_decimals(dcaa):
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    assert v.classification_history["E-0001"] == {
        "2026-06": (D("150.0"), D("6.0")), "2026-07": (D("148.5"), D("7.5")),
    }
    assert v.classification_history["E-0002"] == {"2026-06": (D("140.0"), D("0.0"))}
    direct, indirect = v.classification_history["E-0001"]["2026-07"]
    assert isinstance(direct, Decimal) and isinstance(indirect, Decimal)  # (direct_hours, indirect_hours)


def test_classification_history_accepts_json_numbers_without_a_float(dcaa):
    (dcaa / "classification_history.json").write_text(
        '{"employees":{"E-0001":{"2026-07":{"direct_hours":148.5,"indirect_hours":7}}}}'
    )
    v = load_working_view(dcaa, "2026-08", Tier.CLOSE).view
    assert v.classification_history == {"E-0001": {"2026-07": (D("148.5"), D("7"))}}


def test_classification_history_never_lets_the_current_period_score_itself(dcaa):
    (dcaa / "classification_history.json").write_text(json.dumps({"employees": {
        "E-0001": {"2026-07": {"direct_hours": "1", "indirect_hours": "2"},
                   "2026-08": {"direct_hours": "3", "indirect_hours": "4"},
                   "2026-09": {"direct_hours": "5", "indirect_hours": "6"}},
        "E-0002": {"2026-08": {"direct_hours": "3", "indirect_hours": "4"}},
    }}))
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert r.view.classification_history == {"E-0001": {"2026-07": (D("1"), D("2"))}}
    assert any("classification_history.json" in w and "2026-08" in w and "2026-09" in w for w in r.warnings)


def test_reference_tables_are_pinned_with_hash_and_row_count(dcaa):
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    by = {i.source: i for i in r.reference_tables}
    ac, ch = by["account_categories"], by["classification_history"]
    assert (ac.file, ac.rows, ac.sha256) == ("account_categories.csv", 3, sha(dcaa / "account_categories.csv"))
    assert (ch.file, ch.rows, ch.sha256) == ("classification_history.json", 2, sha(dcaa / "classification_history.json"))
    assert ac.data_as_of is None and ch.data_as_of is None
    assert {"charge_codes", "category_crosswalk", "loaded_rates", "metric_history", "sources_index"} <= set(by)


@pytest.mark.parametrize("old,new,why", [
    ("6900,non_labor", "6900,other", "'other' is not one of labor, non_labor"),
    ("6900,non_labor", "6900,", "'' is not one of"),
    ("6900,non_labor", "6000,non_labor", "account '6000' is listed more than once"),
    ("6900,non_labor", ",non_labor", "Account is empty"),
])
def test_account_categories_bad_values_block_with_file_and_row(dcaa, old, new, why):
    _edit(dcaa / "account_categories.csv", old, new)
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "bad_value" and "account_categories.csv:4" in ei.value.message and why in ei.value.message


def test_account_categories_header_drift_blocks(dcaa):
    _edit(dcaa / "account_categories.csv", "Category", "Kind")
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "schema_mismatch" and "Category" in ei.value.message


@pytest.mark.parametrize("content,why", [
    ("{not json", "not valid JSON"),
    ("[]", "'employees' object"),
    ('{"employees": []}', "'employees' object"),
    ('{"note": "x"}', "'employees' object"),
    ('{"employees": {"E-0001": []}}', "[E-0001]"),
    ('{"employees": {"E-0001": {"2026-07": {"direct_hours": "1"}}}}', "[E-0001][2026-07]: needs direct_hours and indirect_hours"),
    ('{"employees": {"E-0001": {"2026-07": "lots"}}}', "needs direct_hours and indirect_hours"),
    ('{"employees": {"E-0001": {"2026-07": {"direct_hours": "x", "indirect_hours": "1"}}}}', "'x' is not a number"),
    ('{"employees": {"E-0001": {"2026-07": {"direct_hours": "1", "indirect_hours": "-1"}}}}', "cannot be negative"),
])
def test_classification_history_bad_content_blocks(dcaa, content, why):
    (dcaa / "classification_history.json").write_text(content)
    with pytest.raises(RunBlocked) as ei:
        load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert ei.value.code == "bad_value" and "classification_history.json" in ei.value.message and why in ei.value.message


def test_a_gl_account_missing_from_the_category_table_is_flagged_not_hidden(dcaa):
    (dcaa / "account_categories.csv").write_text("Account,Category\n6000,labor\n")  # 6100 is in the GL but not here
    r = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert any("6100" in w and "account_categories.csv" in w and "labor" in w for w in r.warnings)
    # with no table there is nothing to say (the pre-DCAA behaviour)
    (dcaa / "account_categories.csv").unlink()
    assert load_working_view(dcaa, "2026-08", Tier.CLOSE).warnings == ()


def test_dcaa_ingest_is_deterministic(dcaa):
    a = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    b = load_working_view(dcaa, "2026-08", Tier.CLOSE)
    assert a.view == b.view and a.inputs == b.inputs and a.reference_tables == b.reference_tables


def test_shipped_rate_mapping_is_the_specified_one():
    import yaml
    doc = yaml.safe_load((Path(__file__).resolve().parents[1] / "config" / "mappings" / "ratemodel.yaml").read_text())
    assert doc["version"] == 1 and doc["source"] == "rate_data" and doc["mapping_id"]
    assert doc["columns"]["rate_data"] == {
        "Pool": "pool", "Base Definition": "base_definition", "Provisional Rate": "provisional_rate",
        "Effective From": "effective_from", "Effective To": "effective_to", "Source Document": "source_document",
    }
