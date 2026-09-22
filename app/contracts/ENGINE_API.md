# ENGINE_API — function contracts between engine agents and the backend (binding)

Already written and FROZEN (import, do not edit): `engine/canonical.py`, `engine/rules/base.py`,
`engine/results.py`, `config/floors.yaml`. Read them first.

Python 3.13, stdlib + `pyyaml` + `pydantic` only inside `engine/` (no pandas, no numpy, no requests).
All money/hours `Decimal`, never float. Rules and metrics are pure: no I/O, no clock, no randomness,
no environment reads, no network, no LLM. Run everything from `app/` with `.venv/bin/python`.

## Agent B — engine core

| Module | Public API |
|---|---|
| `engine/floors.py` | `load_floors(path: Path) -> FloorRegistry` · `FloorRegistry.param(rule_id, name) -> FloorParam` (fields: direction, floor, default, min, max, basis, citation) · `registry.version: str` |
| `engine/config.py` | `validate_config(text: str, registry, rules: list[RuleSpec], previous: CustomerConfig \| None = None) -> ConfigResult` · `resolve_params(rule: RuleSpec, config: CustomerConfig, registry, contract_id: str \| None = None) -> ResolvedParams` · `resolve_global(config, registry) -> dict[str, Decimal]` |
| `engine/ingest.py` | `load_working_view(data_dir: Path, period: str, tier: Tier) -> IngestResult` |
| `engine/manifest.py` | `build_manifest(...) -> dict` · `replay(manifest: dict, data_dir: Path, config_text: str) -> ReplayResult` |
| `engine/runner.py` | `execute_run(data_dir: Path, config_text: str, period: str, tier: Tier, run_id: str, executed_at: str \| None = None) -> RunOutput` — raises `RunBlocked` |

```python
@dataclass(frozen=True)
class ConfigError:  line: int | None; path: str; code: str; message: str
    # codes: looser_than_floor, exceeds_max, below_min, floor_not_settable, cannot_disable_regulatory_rule,
    #        unknown_rule, unknown_parameter, missing_field, missing_approval, bad_value, yaml_syntax
@dataclass(frozen=True)
class CustomerConfig:  config_version: int; customer: str; effective_from: date; changed_by: str;
    approved_by: str | None; reason: str; schedule: dict[str,str]; defaults: dict[str,Decimal];
    rules: dict[str, dict[str, Any]]   # raw per-rule overrides incl. scope + status
@dataclass(frozen=True)
class ConfigResult:  accepted: bool; errors: tuple[ConfigError,...]; warnings: tuple[str,...];
    config: CustomerConfig | None; sha256: str    # sha256 of the exact submitted text
@dataclass(frozen=True)
class InputFile:  source: str; file: str; sha256: str; rows: int; data_as_of: str | None
@dataclass(frozen=True)
class IngestResult:  view: WorkingView; inputs: tuple[InputFile,...]; sources_present: frozenset[str];
    sources_absent: frozenset[str]; mapping_versions: dict[str,int]; warnings: tuple[str,...]
@dataclass(frozen=True)
class ReplayResult:  identical: bool; findings_compared: int; diff: tuple[str,...]; reason: str | None
```

**Config rules (design §5.3)** — implement ALL of these; each is a test:
1. Stricter-than-default value: accepted. Compared by the parameter's `direction`, never raw magnitude.
2. Looser than a numeric `floor`: rejected `looser_than_floor` (line-level). Outside `min`/`max`: `exceeds_max`/`below_min`.
3. Any key named `floor` anywhere in a customer file: rejected `floor_not_settable`.
4. `enabled: false` on a rule whose params have `basis: regulatory` or `contract`: rejected `cannot_disable_regulatory_rule`.
5. `status: not_applicable` requires `reason`; accepted; the rule is excluded from coverage and listed with its reason.
6. NEVER silently clamp. A refused value is refused with its line number.
7. `changed_by` and `reason` are required. `approved_by` is required ONLY IF any resolved value is looser than in `previous`.
8. `config/meridian-rules.yaml` (B writes it; config_version 6) must validate cleanly with the floors registry. Include a `schedule:` block
   (`fast: weekly`, `pay_period: semi_monthly`, `close: monthly`), `defaults` (`materiality_pct: 0.005`, `coverage_floor: 0.85`),
   and a few STRICTER overrides (e.g. L-05 `late_threshold_hours: 48`).
9. Scope: `rules.<id>.scope.contract.<contract_id>.<param>` overrides for that contract; the STRICTEST applicable value wins.

**Ingest** — mapping files `config/mappings/{unanet,adp,quickbooks,bamboo}.yaml` map source columns → canonical fields per
`contracts/DATA_SPEC.md`; each has `mapping_id`, `version`, `source`, `columns:`. Every canonical record carries `Lineage(source_file, sha256, row)`
with `row` = the 1-based CSV row number INCLUDING the header (first data row = 2), so evidence reads `meridian_time_2026-08.csv:1188`.
Tier gating: load only sources in `TIER_SOURCES[tier]` that also exist per `sources.json`; the rest are reported absent (never an error).
Confirmed reference tables (`charge_codes.csv`, `labor_category_crosswalk.csv`, `loaded_rates.csv`, `contracts.json`, `metric_history.json`) always load.
`load_working_view` raises `RunBlocked("unmapped_charge_code", ...)` if any time entry's charge code is not in `charge_codes.csv`.
Set `view.baselines` from `metric_history.json` as `{period: {metric_key: Decimal}}`.
`view.sources_present` = the sources actually loaded.

**Manifest** (JSON-safe dict): `run_id, period, tier, executed_at, inputs[{source,file,sha256,rows,data_as_of}], sources_absent[], mapping_versions{},
rule_versions{rule_id: version}, config_file{config_version, sha256}, floor_registry_version, declared_schedule{}, materiality{basis_usd, pct, usd}`.
`replay()` (a) refuses with `RunBlocked("inputs_changed")` if any input file's current sha256 differs from the manifest, (b) re-runs with the manifest's
tier/period/config, (c) returns `identical` from comparing `RunOutput.canonical()` to a fresh run; the caller supplies the original canonical string via
`replay(..., expected_canonical=...)` — B: add that parameter.

**Runner** — `execute_run`: load config (raise `RunBlocked("config_rejected")` if not accepted) → `load_working_view` → for every rule in `all_rules()` (stable id order)
resolve params (inject reserved key `materiality_usd` = `materiality_pct` × Σ(entry hours × loaded rate); missing source ⇒ `not_evaluated(...)`;
rule with config `status: not_applicable` ⇒ `Result.NOT_APPLICABLE`) → run each evaluator INDEPENDENTLY (no shared mutable state; result must not depend on order) →
collect findings, sort deterministically `(severity high→low, rule order [L- before DQ-], exposure desc, fingerprint)` → `metrics.total_exposure(findings)` →
`rollup.rollup(...)` → `RunOutput`. Runner never computes a metric or a threshold itself.

## Agent C — rules and reconciliation logic

| Module | Public API |
|---|---|
| `engine/rules/l01.py l02.py l03.py l05.py l06.py l09.py l11.py dq01.py` | each calls `register(RuleSpec(...))` at import; evaluator `(view: WorkingView, p: ResolvedParams) -> RuleResult` |
| `engine/severity.py` | `classify(result: Result, *, exposure: Decimal, materiality: Decimal, employees: int, periods: int = 1, integrity_failure: bool = False, trend_rising: bool = False) -> Severity` (FR §6: High = Exception with exposure ≥ materiality OR systemic (≥3 periods or ≥5 employees) OR integrity failure; Medium = Exception below those, or Watch with rising trend; Low = stable/improving Watch) |
| `engine/metrics.py` | `total_exposure(findings: Sequence[Finding]) -> Decimal` (M13, de-duplicated) + metric helpers used by rules |
| `engine/rollup.py` | `rollup(rule_results: list[RuleResult], applicable: list[RuleSpec], *, coverage_floor: Decimal, is_open: Callable[[Finding], bool] = lambda f: True) -> StatusRollup` |
| `engine/explain.py` | `explain(finding: Finding, spec: RuleSpec) -> Explanation` — templates only, no model |
| `engine/copy_lint.py` | `RESTRICTED_TERMS: tuple[str,...]` · `find_restricted(text: str) -> list[str]` (case-insensitive; covers `compliant` incl. `non-compliant`, `certified`, `audit-ready`, `DCAA-approved`, `attest`/`attestation`) |

**M13 de-duplication (binding).** A finding whose exposure is `Σ hours × loaded_rate` over entries lists those `entry_id`s in `exposure_entry_ids` (L-05, L-06, L-09).
Findings priced any other way (L-01 rate difference, L-02 gap hours, DQ-01) leave `exposure_entry_ids` EMPTY. `total_exposure` = Σ `exposure_usd` of the empty-list findings
+ Σ over the UNION of entry ids of `money(hours × loaded_rate)` for the entry-based findings. An entry counted by two findings contributes once; both findings stay visible.
Round each entry's cost with `money()` only at the end of summing per finding to keep each finding's own `exposure_usd` = `money(Σ hours×rate)`.

**Rule behavior** — see `contracts/DATA_SPEC.md` §3 for the seeded conditions each rule must find, and `config/floors.yaml` for each rule's parameter names
(a rule must read its thresholds ONLY from `p[...]`, using EXACTLY those names). Also required:
- Every RuleSpec sets `authorities`, `basis`, `required_sources`, `parameters` (as `ParamSpec`, matching floors.yaml defaults/ranges), `explanation_template`, `recommended_action`, `review_status="unreviewed"`.
- **L-01** required `timekeeping, hris, contracts`. Mismatch = charged category ≠ crosswalk(HRIS title). Exposure `max(0, charged_rate − approved_rate) × hours`. Metric M3. Integrity failure ⇒ High.
- **L-02** required `timekeeping, payroll`. Per employee-pay-period gap; M1; per-employee finding when `|gap| > employee_gap_materiality_hours`; Exception when `|gap| > employee_gap_exception_hours`, else Watch. Sub-materiality gaps go to `logged_below_materiality`, no finding.
- **L-03** required `payroll, gl`. M2 by period. Consistent below Watch.
- **L-05** required `timekeeping`. M4 late rate; M5 cluster index per direct contract group = group window-share ÷ mean of the last 6 `M5.company_window_share` in `view.baselines` (if < 3 history periods: conservative default + `baseline_confidence="low"` in `computed`). Groups smaller than `cluster_min_group_entries` are not scored.
- **L-06** required `time_edits`. M6 edit rate, undocumented share, last-2-days share; trend from `M6.edit_rate` history (rising ⇒ Medium; stable ⇒ Low). One finding per period; exposure over the entries with an undocumented edit.
- **L-09** required `timekeeping, contracts`. Charge on/after PoP end, before PoP start, or to a contract the code doesn't map to. Exception on any hours over `out_of_pop_hours_tolerance`. Integrity failure ⇒ High.
- **L-11** required `rate_data`; always `not_evaluated` in the MVP (`"Provisional billing rate data was not uploaded"`); the evaluator is otherwise unimplemented.
- **DQ-01** required `timekeeping, hris`. One finding listing employees in timekeeping/payroll but absent from HRIS; Medium; exposure 0.00; not counted in coverage.
- **Coverage** counts only `L-` rules with tier ≤ run tier context as the runner defines: `applicable` = all `L-` rules not `NOT_APPLICABLE` (DQ- excluded); `evaluated` = those whose result ≠ `NOT_EVALUATED`.
- `Finding.evidence` must include, per finding, at least the underlying source records (kind/ref_id/source_file/sha256/row/detail) — for L-01 also the `crosswalk` and `contract_term` refs; every `EvidenceRef.row` is the CSV row from `Lineage.row`.
- `Finding.fingerprint`: exactly as in DATA_SPEC §4. `Finding.headline`: one plain sentence. All explanation text must pass `copy_lint.find_restricted` (return `[]`).

## Backend (Wave 2) consumes exactly the above
`execute_run(...)`, `replay(...)`, `validate_config(...)`, `all_rules()`, `resolve_params(...)`, `explain(...)`, `rollup(...)`, `jsonable/canonical_json`. The backend never re-implements a rule, threshold or metric.

## Disclaimer exemption (binding — Agent C `copy_lint.py`, Agent E footer, `tests/test_copy.py`)
The mandated footer (design FR §8.3) necessarily contains a restricted word, so it is exempted by EXACT string match only:
```python
DISCLAIMER = "This is an analysis of submitted data. It is not an audit opinion or an attestation."
```
`copy_lint.py` exports `DISCLAIMER`; `find_restricted(text)` removes every exact occurrence of `DISCLAIMER` BEFORE scanning. Nothing else is exempt.
The frontend footer and every export must use this string verbatim.
