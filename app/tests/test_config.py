"""Config validation and resolution (design 5.3 rejection table; ENGINE_API config rules 1-9)."""

from __future__ import annotations

import hashlib
from decimal import Decimal

import pytest

from engine.config import (
    contract_param, resolve_domains, resolve_global, resolve_params, resolve_scoped, validate_config,
)
from engine.floors import load_floors
from tests.conftest import FLOORS, SHIPPED_CONFIG

D = Decimal

HEADER = """\
config_version: 7
customer: meridian-systems
effective_from: 2026-09-01
changed_by: a.okafor
approved_by: r.delgado
reason: "test"
"""


def cfg(body: str, header: str = HEADER) -> str:
    return header + body


def codes(res) -> list[str]:
    return [e.code for e in res.errors]


def only(res, code):
    hits = [e for e in res.errors if e.code == code]
    assert len(hits) == 1, f"expected exactly one {code}, got {res.errors}"
    return hits[0]


def spec(stub_rules, rid):
    return next(r for r in stub_rules if r.id == rid)


# --- 1. stricter accepted (by direction, never by magnitude) ----------------
def test_stricter_value_accepted(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 48\n"), registry, stub_rules)
    assert res.accepted and not res.errors
    assert res.config.rules["L-05"]["late_threshold_hours"] == D("48")
    assert any("stricter than the default (72 -> 48)" in w for w in res.warnings)


def test_strictness_uses_direction_not_magnitude(registry, stub_rules):
    # cluster_window_start_hour is higher_is_stricter: 16 (> default 12) is STRICTER, 10 is looser.
    up = validate_config(cfg("rules:\n  L-05:\n    cluster_window_start_hour: 16\n"), registry, stub_rules)
    assert up.accepted
    assert any("stricter than the default" in w for w in up.warnings)
    down = validate_config(cfg("rules:\n  L-05:\n    cluster_window_start_hour: 10\n"), registry, stub_rules)
    assert down.accepted  # allowed (within min 8) but looser than default
    assert any("looser than the default" in w for w in down.warnings)
    # coverage_floor (global) is higher_is_stricter too
    g = validate_config(cfg("defaults:\n  coverage_floor: 0.9\n"), registry, stub_rules)
    assert g.accepted and any("stricter" in w for w in g.warnings)


# --- 2. min/max and floor ---------------------------------------------------
def test_exceeds_max_rejected_with_line_and_not_clamped(registry, stub_rules):
    text = cfg("rules:\n  L-05:\n    late_threshold_hours: 200\n")
    res = validate_config(text, registry, stub_rules)
    assert not res.accepted and res.config is None
    e = only(res, "exceeds_max")
    assert e.path == "rules.L-05.late_threshold_hours"
    assert text.splitlines()[e.line - 1].strip().startswith("late_threshold_hours: 200")
    assert "200" in e.message and "168" in e.message
    # not clamped: no config is produced, so 168 can never be silently substituted
    assert res.config is None


def test_below_min_rejected(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 12\n"), registry, stub_rules)
    e = only(res, "below_min")
    assert e.path == "rules.L-05.late_threshold_hours" and e.line is not None


def test_looser_than_floor_rejected(registry, stub_rules):
    text = cfg("rules:\n  L-09:\n    out_of_pop_hours_tolerance: 5\n")
    res = validate_config(text, registry, stub_rules)
    e = only(res, "looser_than_floor")
    assert e.path == "rules.L-09.out_of_pop_hours_tolerance"
    assert text.splitlines()[e.line - 1].strip() == "out_of_pop_hours_tolerance: 5"
    assert "floor 0" in e.message


def test_looser_than_floor_on_l01(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-01:\n    unapproved_category_tolerance: 2\n"), registry, stub_rules)
    assert codes(res) == ["looser_than_floor"]


def test_value_equal_to_floor_accepted(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-09:\n    out_of_pop_hours_tolerance: 0\n"), registry, stub_rules)
    assert res.accepted


def test_non_numeric_value_rejected(registry, stub_rules):
    for bad in ('"48"', "true", "[1]", "null", ".inf"):
        res = validate_config(cfg(f"rules:\n  L-05:\n    late_threshold_hours: {bad}\n"), registry, stub_rules)
        assert codes(res) == ["bad_value"], bad


def test_global_default_out_of_range(registry, stub_rules):
    res = validate_config(cfg("defaults:\n  materiality_pct: 0.5\n"), registry, stub_rules)
    assert only(res, "exceeds_max").path == "defaults.materiality_pct"
    res = validate_config(cfg("defaults:\n  coverage_floor: 0.2\n"), registry, stub_rules)
    assert only(res, "below_min").path == "defaults.coverage_floor"


# --- 3. floor keys ----------------------------------------------------------
@pytest.mark.parametrize(
    "body,path",
    [
        ("rules:\n  L-01:\n    floor: 70\n", "rules.L-01.floor"),
        ("rules:\n  L-05:\n    late_threshold_hours: 48\n    floor: 70\n", "rules.L-05.floor"),
        ("rules:\n  L-02:\n    scope:\n      contract:\n        C-8841:\n          floor: 1\n",
         "rules.L-02.scope.contract.C-8841.floor"),
        ("defaults:\n  floor: 0.1\n", "defaults.floor"),
        ("floor: 3\n", "floor"),
    ],
)
def test_floor_key_rejected_anywhere(registry, stub_rules, body, path):
    text = cfg(body)
    res = validate_config(text, registry, stub_rules)
    assert not res.accepted
    e = only(res, "floor_not_settable")
    assert e.path == path
    assert text.splitlines()[e.line - 1].strip().startswith("floor:")


# --- 4. enabled: false ------------------------------------------------------
@pytest.mark.parametrize("rid", ["L-09", "L-01"])
def test_cannot_disable_regulatory_rule(registry, stub_rules, rid):
    text = cfg(f"rules:\n  {rid}:\n    enabled: false\n")
    res = validate_config(text, registry, stub_rules)
    e = only(res, "cannot_disable_regulatory_rule")
    assert e.path == f"rules.{rid}.enabled" and e.line == len(text.splitlines())


def test_can_disable_non_regulatory_rule_but_it_is_flagged(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-06:\n    enabled: false\n"), registry, stub_rules)
    assert res.accepted
    assert res.config.rules["L-06"]["enabled"] is False
    assert any("disabled" in w for w in res.warnings)


# --- 5. not_applicable ------------------------------------------------------
def test_not_applicable_with_reason_accepted(registry, stub_rules):
    res = validate_config(
        cfg('rules:\n  L-11:\n    status: not_applicable\n    reason: "No provisional billing rate agreement in place"\n'),
        registry,
        stub_rules,
    )
    assert res.accepted
    assert res.config.rules["L-11"]["status"] == "not_applicable"
    assert res.config.rules["L-11"]["reason"].startswith("No provisional")


def test_not_applicable_is_accepted_even_on_a_regulatory_rule(registry, stub_rules):
    res = validate_config(
        cfg('rules:\n  L-09:\n    status: not_applicable\n    reason: "No contracts with a defined PoP"\n'),
        registry,
        stub_rules,
    )
    assert res.accepted


@pytest.mark.parametrize("extra", ["", '    reason: ""\n', "    reason:\n"])
def test_not_applicable_without_reason_rejected(registry, stub_rules, extra):
    text = cfg("rules:\n  L-11:\n    status: not_applicable\n" + extra)
    res = validate_config(text, registry, stub_rules)
    e = only(res, "missing_field")
    assert e.path == "rules.L-11.reason"
    assert text.splitlines()[e.line - 1].strip() == "status: not_applicable"


def test_bad_status_rejected(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-11:\n    status: skipped\n"), registry, stub_rules)
    assert codes(res) == ["bad_value"]


# --- 7. required fields and approval ---------------------------------------
@pytest.mark.parametrize("missing", ["changed_by", "reason", "customer", "config_version", "effective_from"])
def test_required_field_missing_rejected(registry, stub_rules, missing):
    header = "\n".join(l for l in HEADER.splitlines() if not l.startswith(missing + ":")) + "\n"
    res = validate_config(header + "rules: {}\n", registry, stub_rules)
    assert [e.path for e in res.errors if e.code == "missing_field"] == [missing]


def test_missing_reason_rejected_explicitly(registry, stub_rules):
    header = HEADER.replace('reason: "test"\n', "")
    res = validate_config(header, registry, stub_rules)
    assert only(res, "missing_field").path == "reason"


def test_approved_by_not_required_when_nothing_loosens(registry, stub_rules):
    prev = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 60\n"), registry, stub_rules).config
    header = HEADER.replace("approved_by: r.delgado\n", "")
    res = validate_config(header + "rules:\n  L-05:\n    late_threshold_hours: 48\n", registry, stub_rules, previous=prev)
    assert res.accepted and res.config.approved_by is None


def test_loosening_without_approved_by_rejected(registry, stub_rules):
    prev = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 48\n"), registry, stub_rules).config
    header = HEADER.replace("approved_by: r.delgado\n", "")
    text = header + "rules:\n  L-05:\n    late_threshold_hours: 60\n"
    res = validate_config(text, registry, stub_rules, previous=prev)
    e = only(res, "missing_approval")
    assert e.path == "approved_by" and "48 -> 60" in e.message
    # the same loosening WITH approval is accepted
    ok = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 60\n"), registry, stub_rules, previous=prev)
    assert ok.accepted
    assert any("Loosened relative to the previous" in w for w in ok.warnings)


def test_removing_an_override_is_a_loosening(registry, stub_rules):
    # dropping the 48h override reverts to the 72h default: looser than previous
    prev = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 48\n"), registry, stub_rules).config
    header = HEADER.replace("approved_by: r.delgado\n", "")
    res = validate_config(header + "rules: {}\n", registry, stub_rules, previous=prev)
    assert codes(res) == ["missing_approval"]


def test_loosening_a_global_default_needs_approval(registry, stub_rules):
    prev = validate_config(cfg("defaults:\n  coverage_floor: 0.9\n"), registry, stub_rules).config
    header = HEADER.replace("approved_by: r.delgado\n", "")
    res = validate_config(header + "defaults:\n  coverage_floor: 0.8\n", registry, stub_rules, previous=prev)
    assert codes(res) == ["missing_approval"]


# --- 9. scope: strictest wins ----------------------------------------------
SCOPED = """\
rules:
  L-05:
    late_threshold_hours: 60
    scope:
      contract:
        C-8841: {late_threshold_hours: 36}
        C-7302: {late_threshold_hours: 96}
"""


def test_contract_scope_strictest_wins(registry, stub_rules):
    res = validate_config(cfg(SCOPED), registry, stub_rules)
    assert res.accepted, res.errors
    l05 = spec(stub_rules, "L-05")
    assert resolve_params(l05, res.config, registry)["late_threshold_hours"] == D("60")
    assert resolve_params(l05, res.config, registry, contract_id="C-8841")["late_threshold_hours"] == D("36")
    # C-7302's scoped 96 is looser than the rule-wide 60, so the strictest (60) wins
    assert resolve_params(l05, res.config, registry, contract_id="C-7302")["late_threshold_hours"] == D("60")
    assert resolve_params(l05, res.config, registry, contract_id="C-0000")["late_threshold_hours"] == D("60")
    scoped = resolve_scoped(l05, res.config, registry)
    assert scoped == {
        "scope:C-7302:late_threshold_hours": D("60"),
        "scope:C-8841:late_threshold_hours": D("36"),
    }
    p = {**resolve_params(l05, res.config, registry), **scoped}
    assert contract_param(p, "late_threshold_hours", "C-8841") == D("36")
    assert contract_param(p, "late_threshold_hours", "C-9999") == D("60")
    assert contract_param(p, "late_threshold_hours", None) == D("60")


def test_scope_values_are_validated_with_line_numbers(registry, stub_rules):
    text = cfg("rules:\n  L-09:\n    scope:\n      contract:\n        C-7302:\n          out_of_pop_hours_tolerance: 8\n")
    res = validate_config(text, registry, stub_rules)
    e = only(res, "looser_than_floor")
    assert e.path == "rules.L-09.scope.contract.C-7302.out_of_pop_hours_tolerance"
    assert text.splitlines()[e.line - 1].strip() == "out_of_pop_hours_tolerance: 8"


def test_scope_loosening_vs_previous_needs_approval_only_when_effective_value_loosens(registry, stub_rules):
    prev = validate_config(cfg("rules:\n  L-05:\n    late_threshold_hours: 48\n"), registry, stub_rules).config
    header = HEADER.replace("approved_by: r.delgado\n", "")
    # scoped 96 is looser than the base 48, so the strictest (48) wins: no effective loosening
    ok = validate_config(
        header + "rules:\n  L-05:\n    late_threshold_hours: 48\n    scope:\n      contract:\n        C-1: {late_threshold_hours: 96}\n",
        registry, stub_rules, previous=prev)
    assert ok.accepted


# --- misc gate behaviour ----------------------------------------------------
def test_unknown_rule_and_parameter(registry, stub_rules):
    res = validate_config(cfg("rules:\n  L-99:\n    x: 1\n"), registry, stub_rules)
    assert codes(res) == ["unknown_rule"]
    res = validate_config(cfg("rules:\n  L-05:\n    cluster_share_flag: 0.3\n"), registry, stub_rules)
    assert codes(res) == ["unknown_parameter"]
    # registry attributes are not customer-settable either
    res = validate_config(cfg("rules:\n  L-05:\n    max: 500\n"), registry, stub_rules)
    assert codes(res) == ["unknown_parameter"]


def test_yaml_syntax_error_has_a_line(registry, stub_rules):
    res = validate_config("config_version: 7\nrules: [unclosed\n", registry, stub_rules)
    assert not res.accepted
    assert res.errors[0].code == "yaml_syntax" and res.errors[0].line is not None


def test_duplicate_key_rejected(registry, stub_rules):
    text = cfg("rules:\n  L-05:\n    late_threshold_hours: 48\n    late_threshold_hours: 200\n")
    res = validate_config(text, registry, stub_rules)
    assert "bad_value" in codes(res)  # a repeated key can hide a value from review


def test_all_errors_reported_at_once_in_line_order(registry, stub_rules):
    text = cfg(
        "rules:\n"
        "  L-05:\n    late_threshold_hours: 200\n"
        "  L-09:\n    out_of_pop_hours_tolerance: 5\n"
        "  L-01:\n    floor: 70\n"
    )
    res = validate_config(text, registry, stub_rules)
    assert codes(res) == ["exceeds_max", "looser_than_floor", "floor_not_settable"]
    assert [e.line for e in res.errors] == sorted(e.line for e in res.errors)


def test_sha256_is_of_exact_text_even_when_rejected(registry, stub_rules):
    text = cfg("rules:\n  L-05:\n    late_threshold_hours: 200\n")
    res = validate_config(text, registry, stub_rules)
    assert res.sha256 == hashlib.sha256(text.encode()).hexdigest()
    ok = validate_config(cfg(""), registry, stub_rules)
    assert ok.sha256 == hashlib.sha256(cfg("").encode()).hexdigest()


def test_schedule_validation(registry, stub_rules):
    res = validate_config(cfg("schedule:\n  hourly: weekly\n"), registry, stub_rules)
    assert codes(res) == ["bad_value"]


# --- 8. the shipped config --------------------------------------------------
def test_shipped_config_validates_cleanly(registry, stub_rules):
    text = SHIPPED_CONFIG.read_text()
    res = validate_config(text, registry, stub_rules)
    assert res.accepted and res.errors == ()
    c = res.config
    assert c.config_version == 7 and c.customer == "meridian-systems"
    assert c.domains == ("labor", "dcaa_cost_accounting") and resolve_domains(c) == c.domains
    assert c.schedule == {"fast": "weekly", "pay_period": "semi_monthly", "close": "monthly"}
    assert c.defaults == {"materiality_pct": D("0.005"), "coverage_floor": D("0.85")}
    assert c.rules["L-05"]["late_threshold_hours"] == D("48")
    # L-11 must stay evaluable-as-not-evaluated, never silently not_applicable
    assert "status" not in c.rules.get("L-11", {})
    assert resolve_global(c, registry) == {"coverage_floor": D("0.85"), "materiality_pct": D("0.005")}


def test_shipped_config_enables_both_domains_and_marks_no_rule_not_applicable(registry, stub_rules):
    c = validate_config(SHIPPED_CONFIG.read_text(), registry, stub_rules).config
    assert c.domains == ("labor", "dcaa_cost_accounting")
    assert not any(body.get("status") == "not_applicable" for body in c.rules.values())
    assert c.changed_by and c.reason and c.approved_by


def test_shipped_config_validates_without_any_rule_specs():
    reg = load_floors(FLOORS)
    assert validate_config(SHIPPED_CONFIG.read_text(), reg, []).accepted


def test_resolve_params_defaults_when_no_config(registry, stub_rules):
    p = resolve_params(spec(stub_rules, "L-09"), None, registry)
    assert p == {"out_of_pop_hours_tolerance": D("0")}
    assert all(isinstance(v, Decimal) for v in resolve_params(spec(stub_rules, "L-05"), None, registry).values())


def test_resolve_refuses_to_clamp_a_looser_than_floor_value(registry, stub_rules):
    from dataclasses import replace
    from engine.results import RunBlocked

    good = validate_config(cfg(""), registry, stub_rules).config
    bad = replace(good, rules={"L-09": {"out_of_pop_hours_tolerance": D("5")}})
    with pytest.raises(RunBlocked) as ei:
        resolve_params(spec(stub_rules, "L-09"), bad, registry)
    assert ei.value.code == "config_rejected"


def test_shipped_config_validates_against_the_real_registered_rules(registry):
    """Every parameter name the rule modules declare must be one the config gate understands."""
    from engine.rules.base import all_rules

    rules = all_rules()
    res = validate_config(SHIPPED_CONFIG.read_text(), registry, rules)
    assert res.accepted and res.errors == ()
    for r in rules:
        params = resolve_params(r, res.config, registry)  # raises if a value is looser than its floor
        assert set(params) == set(r.parameters)
        for name, ps in r.parameters.items():  # RuleSpec defaults agree with the floor registry
            if registry.has_param(r.id, name):
                assert registry.param(r.id, name).default == ps.default, f"{r.id}.{name}"
                assert registry.param(r.id, name).direction == ps.direction, f"{r.id}.{name}"


def test_contract_scoped_override_warns_that_it_is_not_applied_yet():
    """Scoped overrides validate against the floors but no rule reads them in this build. The customer
    must be told, not left believing a per-contract threshold is in force."""
    from engine.config import validate_config
    from engine.floors import load_floors
    from engine.rules.base import all_rules
    from tests.conftest import SHIPPED_CONFIG

    reg = load_floors(SHIPPED_CONFIG.parent / "floors.yaml")
    text = SHIPPED_CONFIG.read_text().replace(
        "rules:\n", "rules:\n  L-06:\n    scope:\n      contract:\n        C-8841:\n          edit_rate_watch_pct: 2.0\n", 1)
    r = validate_config(text, reg, all_rules())
    assert r.accepted, r.errors
    assert any("not applied by this build" in w and "C-8841" in w for w in r.warnings), r.warnings


# --- 9. domains ---------------------------------------------------------------
def test_domains_default_to_labor_when_absent(registry, stub_rules):
    res = validate_config(cfg(""), registry, stub_rules)
    assert res.accepted and res.config.domains == ("labor",)
    assert resolve_domains(res.config) == ("labor",)
    assert resolve_domains(None) == ("labor",)  # no config at all is the default too
    assert not any("not enabled" in w for w in res.warnings)  # an absent key is the default, not a choice


def test_both_domains_accepted_in_config_order(registry, stub_rules):
    both = validate_config(cfg("domains: [labor, dcaa_cost_accounting]\n"), registry, stub_rules)
    assert both.accepted and both.config.domains == ("labor", "dcaa_cost_accounting")
    assert isinstance(both.config.domains, tuple)
    rev = validate_config(cfg("domains:\n  - dcaa_cost_accounting\n  - labor\n"), registry, stub_rules)
    assert rev.accepted and resolve_domains(rev.config) == ("dcaa_cost_accounting", "labor")
    assert not any("not enabled" in w for w in both.warnings)


def test_a_single_explicit_domain_is_accepted_and_the_omitted_one_is_flagged(registry, stub_rules):
    res = validate_config(cfg("domains: [labor]\n"), registry, stub_rules)
    assert res.accepted and res.config.domains == ("labor",)
    assert any("DCAA cost accounting is not enabled" in w for w in res.warnings)
    dcaa = validate_config(cfg("domains: [dcaa_cost_accounting]\n"), registry, stub_rules)
    assert dcaa.accepted and dcaa.config.domains == ("dcaa_cost_accounting",)
    assert any("Labor is not enabled" in w for w in dcaa.warnings)


def test_unknown_domain_rejected_with_its_line_number(registry, stub_rules):
    text = cfg("schedule:\n  fast: weekly\ndomains:\n  - labor\n  - dcaa_cost_acounting\n")
    res = validate_config(text, registry, stub_rules)
    assert not res.accepted and res.config is None
    e = only(res, "unknown_domain")
    assert e.path == "domains.1" and "dcaa_cost_acounting" in e.message
    assert "labor" in e.message and "dcaa_cost_accounting" in e.message  # tells the customer the valid ids
    assert text.splitlines()[e.line - 1].strip() == "- dcaa_cost_acounting"


def test_unknown_domain_in_flow_style_has_a_line_number_too(registry, stub_rules):
    text = cfg("domains: [labor, payroll_audit]\n")
    e = only(validate_config(text, registry, stub_rules), "unknown_domain")
    assert e.line is not None and "payroll_audit" in text.splitlines()[e.line - 1]


def test_domain_ids_are_exact_not_case_folded(registry, stub_rules):
    assert codes(validate_config(cfg("domains: [Labor]\n"), registry, stub_rules)) == ["unknown_domain"]


def test_empty_domains_rejected(registry, stub_rules):
    text = cfg("domains: []\n")
    res = validate_config(text, registry, stub_rules)
    assert not res.accepted and codes(res) == ["bad_value"]
    e = res.errors[0]
    assert e.path == "domains" and "cannot be empty" in e.message
    assert text.splitlines()[e.line - 1].startswith("domains:")


@pytest.mark.parametrize("body", [
    "domains: labor\n",                          # a bare string is not a list
    "domains:\n  labor: true\n",                 # a mapping is not a list
    "domains:\n",                                # a key with no value
    "domains: 7\n",
])
def test_non_list_domains_rejected(registry, stub_rules, body):
    res = validate_config(cfg(body), registry, stub_rules)
    assert not res.accepted and codes(res) == ["bad_value"] and res.errors[0].path == "domains"
    assert res.errors[0].line is not None


def test_duplicate_domain_rejected_at_the_repeat(registry, stub_rules):
    text = cfg("domains:\n  - labor\n  - dcaa_cost_accounting\n  - labor\n")
    res = validate_config(text, registry, stub_rules)
    assert not res.accepted
    e = only(res, "bad_value")
    assert e.path == "domains.2" and "more than once" in e.message
    assert text.splitlines()[e.line - 1].strip() == "- labor"


def test_non_text_domain_entry_rejected(registry, stub_rules):
    res = validate_config(cfg("domains: [labor, 5]\n"), registry, stub_rules)
    assert codes(res) == ["bad_value"] and res.errors[0].path == "domains.1"


def test_a_domain_error_is_reported_with_the_others_in_line_order(registry, stub_rules):
    text = cfg("domains: [nope]\nrules:\n  L-05:\n    late_threshold_hours: 200\n")
    res = validate_config(text, registry, stub_rules)
    assert codes(res) == ["unknown_domain", "exceeds_max"]
    assert [e.line for e in res.errors] == sorted(e.line for e in res.errors)


def test_domains_are_part_of_the_hashed_config_text(registry, stub_rules):
    a = validate_config(cfg("domains: [labor]\n"), registry, stub_rules)
    b = validate_config(cfg("domains: [labor, dcaa_cost_accounting]\n"), registry, stub_rules)
    assert a.sha256 != b.sha256
    assert a.sha256 == hashlib.sha256(cfg("domains: [labor]\n").encode()).hexdigest()


def test_dcaa_rule_parameters_validate_whether_or_not_the_domain_is_enabled(registry, stub_rules):
    # L-08 / L-11 have floors.yaml parameters even when the rule modules are not loaded: a typo is still caught
    ok = validate_config(cfg("domains: [labor]\nrules:\n  L-11:\n    rate_drift_watch_pct: 1.5\n"), registry, stub_rules)
    assert ok.accepted and ok.config.rules["L-11"]["rate_drift_watch_pct"] == D("1.5")
    bad = validate_config(cfg("domains: [labor]\nrules:\n  L-11:\n    rate_drift_watch_pct: 9\n"), registry, stub_rules)
    assert codes(bad) == ["exceeds_max"]


def test_domains_does_not_disturb_the_unknown_top_level_key_check(registry, stub_rules):
    res = validate_config(cfg("domain: [labor]\n"), registry, stub_rules)
    assert codes(res) == ["unknown_parameter"]
