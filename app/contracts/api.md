# REST API contract (binding for Agent D backend and Agent E frontend)

Base path `/api`. JSON. **All money and hours are STRINGS** (`"3504.00"`, `"96.0"`) so no float ever touches a
figure; the UI formats them for display but never does arithmetic on them. Timestamps ISO-8601.
Ratios/percentages are strings too (`"0.0143"`). One customer (Meridian) in the MVP; no auth.
Errors: `{"error":{"code":"...","message":"..."}}` with 400/404/409/422.
Sample response for every endpoint: `contracts/sample_payloads/<name>.json`.

| Method | Path | Body / query | Sample | Notes |
|---|---|---|---|---|
| GET | `/api/status` | `?period=2026-08&as_of=2026-09-03` | `status.json` | `as_of` overrides "today" for staleness (demo/test determinism) |
| GET | `/api/findings` | `?severity=&rule=&status=&period=` | `findings_list.json` | sorted severity, rule, exposure desc |
| GET | `/api/findings/{id}` | | `finding_detail.json` | full record + `explanation` + `history` |
| GET | `/api/findings/{id}/evidence` | `?page=1&page_size=50` | `evidence.json` | |
| POST | `/api/findings/{id}/disposition` | `{disposition, reason_code?, note?, actor}` | `disposition_response.json` | strict state machine |
| GET | `/api/rules` | | `rules_list.json` | catalog incl. resolved params + last result |
| GET | `/api/rules/{id}` | | `rule_detail.json` | |
| GET | `/api/config` | | `config_get.json` | active config text + version + history |
| POST | `/api/config/validate` | `{yaml}` | `config_rejected.json` / `config_accepted.json` | dry-run, persists nothing |
| PUT | `/api/config` | `{yaml}` | same | persists a new version only if accepted; else 422 with errors |
| GET | `/api/runs` | | `runs_list.json` | |
| GET | `/api/runs/{run_id}` | | `run_detail.json` | includes full manifest + per-rule results |
| POST | `/api/runs` | `{period, tier}` | `run_created.json` | tier in `fast\|pay_period\|close`; synchronous in the MVP |
| POST | `/api/runs/{run_id}/replay` | | `replay_result.json` | |
| GET | `/api/health` | | | `{"ok":true}` |

## Finding lifecycle (design §8.3) — enforced by the API
States: `open, in_review, confirmed, legit_exception, data_error, remediated, closed`.
Allowed `disposition` transitions (anything else → **409** `invalid_transition`):
```
open            -> in_review
in_review       -> confirmed | legit_exception | data_error
confirmed       -> remediated
data_error      -> remediated
legit_exception -> closed
remediated      -> closed
closed          -> open        (system only, when the same fingerprint recurs in a later run)
```
`legit_exception` requires `reason_code` AND `note` (else **422** `reason_required`). Every transition appends a history row `{from,to,actor,at,reason_code,note}`.
"Open findings" for the status label = status in `{open, in_review, confirmed, data_error}`; `legit_exception`, `remediated`, `closed` are not counted.
Reason codes: `documented_correction, approved_exception, source_data_error, policy_change, other`.

## Identity and compliance memory
A finding is identified by its `fingerprint` (`Finding.fingerprint`) within a period. Re-running the same period/tier does NOT create duplicates and does NOT
reset dispositions: the existing finding id and status are kept and `latest_run_id` advances. New fingerprints get the next id, sequential from **F-3311**
(deterministic order: severity high→low, rule id with `L-` before `DQ-`, exposure desc). If a `closed` finding's fingerprint reappears, it reopens (`closed -> open`, actor `system`).

## Status semantics
`label_kind`: `open_findings` ("8 open findings"), `no_open_findings` ("No open findings"), `incomplete_data` ("Incomplete data" — coverage below `coverage_floor`, takes precedence).
Never emit the words compliant/certified/audit-ready/DCAA-approved/attest anywhere in any response.
Per-rule status combines runs: for each rule take its most recent run in the period that EVALUATED it; if none, "Not evaluated".
`tiers[]` reports per-tier state from run history vs the config's declared `schedule` and `as_of`: `state` in `current | overdue | never_run`;
tier membership of a rule = `RuleSpec.tier`. `oldest_source` = the source with the earliest `data_as_of` across the latest run's inputs.

## Clarifications (added after the frontend's contract review — binding)
1. **Tier state.** `next_due = date(last_run_at) + cadence`, with cadence days `weekly=7, biweekly=14, semi_monthly=15, monthly=30`. `state = "overdue"` iff `as_of > next_due`,
   `"current"` otherwise, `"never_run"` if the tier has no run. `as_of` defaults to today's date (the API layer may read the clock; the engine never does).
2. **`status: not_applicable`** is the only thing that removes a rule from `coverage.applicable`. L-11 is ACTIVE; with the DCAA extension's rate file it is
   evaluated, so the Meridian dataset reads **8 of 8** (labor 6 of 6, DCAA cost accounting 2 of 2). Without the rate file L-11 is *Not evaluated* and coverage is 7 of 8.
   The shipped `config/meridian-rules.yaml` must NOT mark L-11 not_applicable, and `rules_list.status` / `config_get.yaml` / `status.coverage` must agree.
3. **Config errors.** (`sha256` is always the hash of the submitted text, even when rejected; `config_version` is null when rejected.) `POST /config/validate` and `PUT /config` ALWAYS return a `ConfigResult` body (`accepted`, `errors[]`, `warnings[]`, `sha256`, `config_version`) — never the
   `{error:{...}}` envelope — including for YAML syntax errors (`code: "yaml_syntax"`). HTTP: validate → 200 either way; PUT → 200 accepted, 422 rejected.
   `errors[].line` is **1-based** within the submitted text, or null when not attributable.
4. **`replay_result.diff`** is an array of plain strings, one per differing finding, e.g. `"F-3312: exposure_usd 27518.40 != 27518.00"`.
5. **Disposition** responds with the FULL finding detail (incl. `history`, new `allowed_transitions`). `actor` is a required free-text string (no auth in the MVP).
6. **Periods.** No periods endpoint. `status.period` defaults to the period in `sources.json` of the data dir; `POST /runs` takes any period the data supports.
7. **Lists are complete in the real API.** The sample `rules_list` / `run_detail.rules` show a subset; the real responses include every registered rule including `DQ-01`.
8. **Evidence `detail`** keys vary by `kind`; values may be strings or numbers; `row` is null when the source has no row (contract terms).
9. **`severity_reason`** is a machine code. Known limitation in the MVP; the UI shows it raw.
10. **Not supported in the MVP:** disposition attachments (design §8.3), auth, multi-tenancy.

## DCAA additions (binding for Agents D2 and E2)
Sample payloads under `sample_payloads/` are updated to this shape at the START of wave 2 (before D2 and E2 launch),
so the shape tests stay green while the engine agents work. Until then the samples describe the labor-only shape.

1. **Domains.** ids `labor`, `dcaa_cost_accounting` (each enabled per the config's `domains:` list; default `[labor]`), and the
   placeholders `cmmc_evidence` and `proposals`, which are never enabled. Names: `Labor`, `DCAA cost accounting`, `CMMC evidence`, `Proposals`.
2. **`GET /api/status`.** The top-level fields keep their meaning but now span the ENABLED domains: `label` is
   "Incomplete data" if ANY enabled domain's coverage is below its floor (precedence), else "N open findings" /
   "No open findings"; `open_findings`, `by_severity`, `total_exposure_usd` (M13 de-duplicated across domains) and
   `coverage` (`evaluated` / `applicable` summed) likewise. `domains[]` becomes:
   ```json
   {"id":"dcaa_cost_accounting","name":"DCAA cost accounting","enabled":true,"available":true,
    "state":"2 open findings","label_kind":"open_findings","open_findings":2,
    "by_severity":{"high":2,"medium":0,"low":0},
    "coverage":{"evaluated":2,"applicable":2,"ratio":"1.0000","floor":"0.85"}}
   ```
   For a disabled-but-available domain: `enabled:false`, `state:"Not enabled"`, counts 0, `coverage:null`. For an unavailable
   placeholder: `available:false`, `state:"coming later"`. `rules[]` items gain `domain`. `tiers[].rules_in_tier` and
   `rules_evaluated` count `counts_toward_coverage` rules of ENABLED domains. Enabled domains come from the latest run's
   manifest when there is one, else from the active config.
3. **Findings.** List items and detail gain `domain` and `domain_name`. `GET /api/findings?domain=<id>` filters; an unknown
   id is 400 `bad_filter`. Finding ids stay sequential in the engine's deterministic order.
4. **Rules.** Items gain `domain` and `domain_name`; `GET /api/rules?domain=` filters.
5. **`computed` on a finding is `Record<string,string>`.** Lists are joined with `"; "`, nested dicts are omitted (as `entry_costs`
   already is), so nothing structured reaches the UI.
6. **Run detail.** `manifest` gains `enabled_domains: string[]`, and `inputs[]` may include `rate_data`; `reference_tables[]` may
   include `account_categories` and `classification_history`.
7. **Config.** The active config text may carry `domains:`; `POST /config/validate` and `PUT /config` return `unknown_domain` with
   a 1-based line number for an unknown id.

8. **Frontend contract review, settled.**
   - `status.domains[].label_kind` is one of `open_findings`, `no_open_findings`, `incomplete_data`, `not_enabled` (an available domain the config has not enabled), `unavailable` (a placeholder).
   - An ENABLED domain that no run has covered yet reports `coverage: {evaluated: 0, applicable: N, ratio: "0.0000", floor: ...}` and state "Incomplete data", never `null`. `coverage` is `null` only for a disabled or unavailable domain.
   - `run_detail.rules[]` items carry `domain` (an id).
   - `manifest.enabled_domains` (ids) and `manifest.reference_tables` are ALWAYS present; `reference_tables` may be an empty list. `inputs[]` may include `rate_data`.
   - `status.total_exposure_usd` is the engine's de-duplicated M13 over open findings, so it is **not** the sum of the listed exposures (an entry counted by two findings contributes once). Meridian: findings sum to 71042.47, total is 68428.27.
   - `errors[].line` on a config error is 1-based or `null`.
