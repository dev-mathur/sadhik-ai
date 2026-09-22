# DCAA_ENGINE_API — engine contract for the DCAA cost-accounting domain (binding for Agents B2, C2, D2)

Extends `contracts/ENGINE_API.md` (same rules: pure rules, `Decimal` only, no clock/randomness/LLM in `engine/`).
Data formats and seeded numbers: `contracts/DCAA_DATA_SPEC.md`. Already added and FROZEN this step (import, do not
edit): `engine/canonical.py` (`RateAgreement`, `WorkingView.rate_agreements / account_categories /
classification_history`), `engine/rules/base.py` (`Domain`, `DOMAIN_NAMES`, `DEFAULT_ENABLED_DOMAINS`,
`RuleSpec.counts_toward_coverage`), `engine/results.py` (`StatusRollup.domain`, `RunOutput.domain_rollups`),
`config/floors.yaml` (L-08 and extended L-11 parameters).

## 0. Two tests are RED until this work lands — that is intended
`tests/test_rules_smoke.py::test_parameters_match_floors_yaml` fails with `KeyError: 'L-08'` because `floors.yaml`
now declares parameters for rules that do not exist yet. **Do not edit `floors.yaml` and do not weaken the test:**
Agent C2 makes it pass by registering L-08 and giving L-11 the three params below with EXACTLY the floors names,
defaults, ranges and directions. C2 must also update the expected id set in `test_specs_are_complete_and_unreviewed`
(adds `L-08`). Nothing else in the existing 183 tests should need to change except the `6 of 7` coverage assertions,
which the integrator (not you) updates.

## 1. Domain model
- Every `RuleSpec` sets `domain`: L-01, L-02, L-03, L-05, L-06, L-09 and DQ-01 → `"labor"` (already the default);
  **L-08 and L-11 → `Domain.DCAA_COST_ACCOUNTING.value`**. IDs are unchanged (design §14 #5 stays open).
- DQ-01 sets `counts_toward_coverage=False`; everything else keeps the default `True`. **The `id.startswith("L-")`
  proxy is retired**: "counts toward coverage" is `spec.counts_toward_coverage`, "belongs to a domain" is `spec.domain`.
- Customer config gains a top-level key **`domains: [labor, dcaa_cost_accounting]`**. Default when absent: `["labor"]`.
  An unknown id is rejected `unknown_domain` with its line number; an empty list is rejected `bad_value`; a duplicate is
  rejected `bad_value`. `CustomerConfig.domains: tuple[str, ...]` (in order given). It is part of the hashed config text.
- **A disabled domain is not run.** Its rules are not executed and do not appear in `rule_results` (they are not
  "Not evaluated" — the customer did not ask for them). `RunOutput.domain_rollups` has one entry per ENABLED domain.

## 2. Agent B2 — engine core
**Owns** `engine/ingest.py`, `engine/config.py`, `engine/runner.py`, `engine/manifest.py`,
`config/mappings/ratemodel.yaml`, `config/meridian-rules.yaml`, and their tests (`tests/test_config.py`,
`tests/test_ingest.py`, `tests/test_runner.py` — add cases; the coverage `(6, 7)` assertion in `test_runner.py:148`
belongs to the integrator, leave it).
- **Ingest.** New source `rate_data` → `meridian_rates_2026-08.csv` via `ratemodel.yaml` (`mapping_id`, `version: 1`,
  `source: rate_data`, `columns:`), building `view.rate_agreements`. Validation: `Pool` ∈ {fringe, overhead, ga}, `Base
  Definition` ∈ {direct_labor, total_cost_input}, rate a decimal fraction in (0, 1), `Effective From <= Effective To`;
  otherwise `RunBlocked("bad_value", ...)`. Tier gating as today: it loads only at the close tier (`TIER_SOURCES`
  already lists `rate_data`) and only if declared in `sources.json`.
  Two NEW OPTIONAL reference tables, always loaded when the file exists, silently empty when it does not (older
  fixtures such as `tests/fixtures/mini` have neither): `account_categories.csv` → `view.account_categories`;
  `classification_history.json` → `view.classification_history` as `{employee_id: {period: (Decimal, Decimal)}}`.
  `Lineage.row` for `rate_data` is the 1-based CSV row incl. header, as for the other CSVs.
- **Manifest.** `reference_tables[]` gains `account_categories` and `classification_history` when present;
  replay hashes them like the existing ones. `rate_data` appears under `inputs[]` like any source.
  The manifest also gains `enabled_domains: [..]` (the resolved list, in config order), pinned like the rest.
- **Config.** Implement `domains` per section 1; expose `resolve_domains(config) -> tuple[str, ...]`.
  `config/meridian-rules.yaml` gets `domains: [labor, dcaa_cost_accounting]` and must still validate. Bump
  `config_version` to 7 and keep the changed_by/reason/approval fields valid. Do NOT mark L-11 `not_applicable`.
- **Runner.** After resolving `enabled = resolve_domains(config)`: run only rules whose `spec.domain in enabled`;
  replace the two prefix checks (`engine/runner.py:86` sort key and `:147` coverage list) with
  `spec.counts_toward_coverage`; keep the finding sort key otherwise IDENTICAL (severity, checks before data-quality
  rules, rule id, exposure desc, fingerprint) so the original eight findings keep their relative order. Call
  `rollup.rollup_domains(...)` (section 3) and set `RunOutput.rollup` = its overall result and
  `RunOutput.domain_rollups` = its per-domain dict. Coverage `applicable` is per domain: its `counts_toward_coverage`
  rules that are not `NOT_APPLICABLE` and whose tier is at or below the run tier.
- **Tests to add:** `domains` accepted / unknown rejected with line / empty rejected / default `[labor]`; a disabled
  domain's rules are absent from `rule_results`; `rate_data` ingest incl. each bad_value; optional tables absent ⇒
  empty and no error; replay reproduces a run with the new tables and refuses if one changed.

## 3. Agent C2 — rules and reconciliation logic
**Owns** `engine/rules/l08.py` (new), `l11.py`, `l03.py`, `dq01.py`, `engine/metrics.py`, `engine/rollup.py`,
`engine/severity.py`, `engine/explain.py`, `tests/test_rules_smoke.py` (+ new `tests/test_dcaa_rules.py`).
- **`rollup.rollup_domains`** (new; keep the existing `rollup(...)` untouched and call it per domain):
```python
def rollup_domains(rule_results: list[RuleResult], specs: list[RuleSpec], enabled_domains: Sequence[str], *,
                   coverage_floor: Decimal, is_open: Callable[[Finding], bool] = lambda f: True,
                   ) -> tuple[StatusRollup, dict[str, StatusRollup]]:
```
  Per enabled domain: `rollup()` over that domain's specs and results, with `StatusRollup.domain` set. Overall
  (`domain=""`): `open_findings/high/medium/low` summed; `total_exposure_usd` = `metrics.total_exposure` over ALL open
  findings of ALL enabled domains (M13 de-duplicates across domains); coverage summed; `rule_results` /
  `requirement_results` / `not_evaluated` merged; **label: "Incomplete data" if ANY enabled domain's coverage ratio is
  below `coverage_floor` (takes precedence), else "N open findings" / "No open findings"**. Not-applicable rules are
  excluded from coverage exactly as in `rollup()`.
- **L-03** sums GL LABOR only: `view.account_categories.get(g.account, "labor") == "labor"`; when
  `view.account_categories` is empty every line counts (the old behaviour — the mini fixture depends on it).
- **L-11** (`domain = dcaa_cost_accounting`, `required_sources = ("gl", "rate_data")` ⇒ close tier,
  `authorities = ("FAR 52.216-7", "FAR 42.704", "CAS 418 (to verify)")`, `basis = "audit_practice"`). Parameters EXACTLY
  as `floors.yaml`: `rate_drift_watch_pct`, `rate_drift_exception_pct`, `systemic_periods`.
  Computation is fixed in `DCAA_DATA_SPEC.md` section 1.3 (formulas) and section 2 (M10, exposure, severity).
  Missing `rate_data` ⇒ `not_evaluated(..., "Provisional billing rate data was not uploaded")`; missing `gl` ⇒
  `not_evaluated`; a pool with no agreement covering the period end is skipped with a note in `computed` and, if EVERY
  pool is skipped, the rule is `not_evaluated`. Provisional rate = the agreement for that pool whose
  `[effective_from, effective_to]` contains the period's last day. Prior periods come from
  `view.baselines[period]["M10.pool.<pool>"]` / `["M10.base.<pool>"]` for the six periods before `view.period`, using the
  same provisional rate. One finding per pool that breaches; fingerprint `L-11|<pool>|<period>`; headline one plain
  sentence such as "G&A rate is 8.0% above its provisional billing rate (10.80% actual vs 10.00%)". `computed` carries
  `pool, base_definition, pool_amount, base_amount, actual_rate, provisional_rate, m10, direction, periods_beyond_watch,
  history` (list of `{period, rate, m10}`), `pool_change_pct` and `base_change_pct` vs the prior month (context only —
  the composition trigger is deferred). `evidence`: the GL lines making up the pool (`kind="gl_line"`, with `row` from
  `Lineage`) and the agreement (`kind="rate_agreement"`). Watch below materiality is logged in
  `logged_below_materiality`, no finding. Metric `M10` per pool.
- **L-08** (`domain = dcaa_cost_accounting`, `required_sources = ("timekeeping",)` ⇒ **fast tier**,
  `authorities = ("DFARS 252.242-7006(c)(2)", "CAS 402 (to verify)")`, `basis = "audit_practice"`). Parameters EXACTLY as
  `floors.yaml`: `indirect_share_shift_pp`, `min_excess_hours`. The history minimum of 3 periods is a constant in the
  rule, not a parameter. Definition in `DCAA_DATA_SPEC.md` section 2. Employees with fewer than 3 history periods are
  skipped and listed in `logged_below_materiality` with reason `insufficient_history`. Exposure entry-priced? **No** —
  `exposure_entry_ids = ()` (excess hours are not tied to specific entries), so M13 counts it as a plain amount.
  `evidence`: the employee's indirect-coded entries for the period (`kind="time_entry"`) and one `kind="history"`
  record summarising the baseline. Fingerprint `L-08|<employee_id>|<period>`; severity via
  `classify_with_reason(..., integrity_failure=True)` (High), `severity_reason` naming the shift. Metric `M9`.
  Missing `timekeeping` ⇒ `not_evaluated`. No `time_edits`, `gl` or `hris` dependency.
- **`explain.py`**: templates for both new rules in the existing style; every string must pass `copy_lint.find_restricted`.
  Wording constraints: describe data as "consistent or inconsistent with" a requirement; for L-11 say the provisional
  rate "no longer tracks" the actual rate and that a rate revision may be worth raising with the contracting officer — never
  that anything is a violation, never certify or approve. Include direction (over- or under-billed).
- **`dq01.py`**: set `counts_toward_coverage=False` (nothing else changes).
- **Tests to add (`tests/test_dcaa_rules.py`):** per new rule a finds-it, a clean, and a not-evaluated case built from small
  hand-made `WorkingView`s; the exact rate arithmetic and half-up rounding; L-11 systemic vs non-systemic severity;
  L-08 threshold boundaries (14.9 pp / 15.0 pp, 15.9 h / 16.0 h) and the insufficient-history skip; L-03 with and without
  `account_categories`; `rollup_domains` (per-domain coverage, overall Incomplete-data precedence when one domain is thin,
  cross-domain M13 de-duplication, a disabled domain absent); explanations pass `find_restricted`.

## 4. Agent D2 — backend (wave 2; do not start before B2 and C2 are done)
Replace every domain-proxy site: `api/service.py` lines 411, 496, 766, 890 and 968 (`startswith("L-")`) with
`spec.domain` / `spec.counts_toward_coverage`; build the status response from `RunOutput.rollup` and
`domain_rollups` per `contracts/api.md` "DCAA additions"; persist the enabled-domain list with each run; add the
`domain` field to findings and rules and the `?domain=` filter.

## 5. Agent E2 — frontend (wave 2)
Build against `contracts/api.md` "DCAA additions" and the updated `contracts/sample_payloads/*.json`.
