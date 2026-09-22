# Sadhik AI Rules Engine: High-Level Design

**Status:** Draft v0.1 | **Date:** 2026-09-20 | **Owner:** Dev
**Scope:** Labor reconciliation domain only (V1). Design should not preclude later domains.

---

## 1. Purpose

The rules engine is the deterministic core of Sadhik AI. It takes exports from a contractor's timekeeping, payroll, GL, HRIS and contract data, runs a versioned library of labor-compliance checks, and emits **findings**: what broke, which rule and citation it maps to, the dollars and employees affected, and the source records as evidence.

Two requirements shape the whole design:

1. **Deterministic and auditable.** Same inputs + same rule version + same configuration = same findings. Math is never LLM-inferred.
2. **Customer-tunable with regulatory floors.** Customers can set their own thresholds, but never looser than the minimum required by regulation or contract.

## 2. Goals and non-goals

**Goals**
- Run independent, versioned rules over uploaded exports (export-first ingestion).
- Emit lineage-tagged findings that are themselves a customer deliverable.
- Let customers tighten thresholds within enforced floors, with full change history.
- Provide clean tool interfaces so an LLM can explain, investigate and assist without deciding.

**Non-goals (V1)**
- Live connectors and in-environment agents (V2).
- A persistent all-purpose data model or ontology.
- Forward-chaining or Rete-style inference. Rules do not trigger other rules.
- Clause tracking, FAR database, audit-package generation, CMMC, proposals.
- Any claim of CPA attestation, audit opinion, or "DCAA-approved" output.
- Predictive risk scoring (V3, after real outcome data exists).

## 3. Design principles

1. **Custom thin engine over a general product.** Sadhik's checks are stateless comparisons over data, not inference problems. General engines (Drools, etc.) add chaining and implicit control flow that are hard to reason about and audit. Revisit only if rule count and interdependence grow substantially (rough guide: beyond 50 to 100 interdependent rules).
2. **Rules are data plus small pure functions.** A rule is a declarative spec (metadata, inputs, parameters, citation) bound to a pure evaluator function.
3. **No chaining.** Each rule reads normalized inputs and emits zero or more findings. Ordering never affects results.
4. **Every finding carries lineage.** Source record IDs, rule ID and version, config snapshot, computed values, timestamp, status.
5. **Floors are immutable to customers.** Only Sadhik (with expert review) changes a floor.
6. **The LLM proposes and explains; code decides.**

## 4. Architecture overview

```
 Customer exports (timesheets, payroll, GL, HRIS, contracts)
                    |
                    v
        +-----------------------+
        |  Ingestion & Mapping  |  LLM-assisted schema mapping, human-confirmed once,
        +-----------+-----------+  then saved as a deterministic mapping
                    |
                    v
        +-----------------------+
        |  Canonical Views      |  thin, on-demand normalized tables
        +-----------+-----------+  (no persistent ontology)
                    |
   +----------------+-----------------+
   |                                  |
   v                                  v
+---------------+           +----------------------+
| Rule Library  |           | Parameter Resolver   |
| (versioned    |           | floor -> default ->  |
|  specs + fns) |           | customer value       |
+-------+-------+           +----------+-----------+
        |                              |
        +--------------+---------------+
                       v
             +-------------------+
             |  Execution Runner |  independent rules, replayable
             +---------+---------+
                       v
             +-------------------+
             |  Findings Store   |  lineage, status, review decisions
             +----+---------+----+
                  |         |
                  v         v
        +-------------+  +-------------------------+
        | Status UI / |  | LLM Layer (tools)       |
        | Reports     |  | explain, investigate,   |
        +-------------+  | suggest thresholds      |
                         +-------------------------+
```

## 5. Core components

### 5.1 Ingestion and mapping
- Accepts CSV/XLSX/PDF exports. Contracts are unstructured PDFs.
- An LLM proposes a mapping from customer columns to canonical fields. A human confirms it once. The confirmed mapping is stored and applied deterministically afterward.
- Contract extraction (approved labor categories, rates, ceilings, periods of performance) produces typed records with page citations. A human confirms extracted values before rules use them.

### 5.2 Canonical views
Small normalized views built on demand per run: `time_entries` (with timestamps, edit history, approvals), `payroll_lines`, `gl_labor`, `employees`, `contract_terms`. These are working views, not a system of record.

### 5.3 Rule library
Each rule has:

| Field | Meaning |
|---|---|
| `id`, `version` | Stable ID, semantic version |
| `domain` | `labor` in V1 |
| `citation` | Regulation or clause, or "audit practice" / "customer policy" |
| `basis` | `regulatory`, `contract`, `audit_practice`, `customer_policy` |
| `inputs` | Canonical views and fields required |
| `parameters` | Tunable values (see 5.4) |
| `evaluator` | Pure function: (inputs, resolved params) -> findings |
| `severity_map` | How results map to warning vs violation |
| `tests` | Fixtures with known-bad and known-good cases |
| `review_status` | Whether a GovCon CPA/consultant has reviewed the citation and interpretation |

Rules are implemented as Python functions or SQL over the canonical views in V1. A declarative layer (e.g., JSON Logic) can be added later where non-engineers need to edit simple conditions.

### 5.4 Parameter resolver: floors and customer thresholds

Each tunable parameter resolves through three layers:

1. **Floor**: strictest-allowed limit from regulation or contract. Owned by Sadhik, versioned, cited.
2. **Default**: Sadhik's recommended value. At least as strict as the floor.
3. **Customer value**: chosen by the customer. Validated to be at least as strict as the floor.

Each parameter declares a `direction` (`lower_is_stricter` or `higher_is_stricter`) so validation compares strictness, not raw numbers.

```yaml
rule: TM-CEILING-NOTICE
citation: FAR 52.232-7
basis: regulatory
parameter: ceiling_notice_pct
direction: lower_is_stricter
floor: 85
default: 80
customer_value: 75
floor_version: 2026-09-01
```

Behavior:
- **Two tiers per rule.** A customer early-warning threshold (adjustable, stricter than or equal to the floor) and a floor breach (a violation that cannot be suppressed).
- **Only some parameters have numeric floors.** Binary rules (for example, qualification match, GL reconciliation) are zero-tolerance; customers may add an earlier-warning tier but not loosen them. Audit-practice rules (late entries, edit clustering, day-of-week patterns) have no regulatory number; their baseline is the customer's own written timekeeping policy, recorded as `customer_policy`.
- **Materiality filters affect display and notification only.** Sub-threshold findings are still logged with lineage.
- **Scope and precedence.** Parameters can be set at global, contract and employee-group scope; the strictest applicable value wins. Contract terms can set contract-level floors stricter than the general one.
- **Floor changes.** When a regulation changes and a floor moves, customer values that become invalid are flagged for review, never silently rewritten.

### 5.5 Execution runner
- Loads a run manifest: input file hashes, mapping version, rule versions, and a config snapshot.
- Executes rules independently (parallelizable). No shared mutable state.
- Deterministic: rerunning a manifest reproduces identical findings.

### 5.6 Findings store
Each finding records:
- `finding_id`, `rule_id`, `rule_version`, `run_id`
- Triggering values and comparison (computed by code)
- Source record references (file, row, record ID)
- Dollars, employees and contracts affected
- Severity tier (warning vs violation), citation
- Status: open, confirmed, legitimate exception, resolved; reviewer, timestamp, note

Reviewer decisions feed **customer-specific compliance memory**, reused to reduce repeat noise and improve explanations over time.

### 5.7 Governance and audit trail
- All threshold changes logged: who, when, old and new value, required reason. Approval by CFO/Controller.
- Rule and floor changes go through versioned review, with CPA/consultant sign-off recorded in `review_status`.
- Findings and the evidence trail are exportable as a customer deliverable.

## 6. LLM interaction model

The LLM is not a decision-maker. Roles, from lowest to highest risk:

| Role | LLM does | Guardrail |
|---|---|---|
| **Explain findings** | Turns a finding record into a plain-language explanation and recommended action | Sees only the finding and evidence; all numbers come from the engine |
| **Schema mapping** | Proposes column-to-field mappings | Human confirms once; then deterministic |
| **Contract extraction** | Reads PDFs into typed records with page citations | Human confirms before rules use them |
| **Threshold suggestions** | Explains baselines and proposes values (for example, "your Friday-afternoon share is X% vs Y% historical") | Numbers computed deterministically; proposals pass the same validator; no write access to floors |
| **Investigate follow-ups** | Answers questions by calling engine tools and narrating results | Read-only tools; every answer cites finding and record IDs |
| **Draft rules from regulation text** (offline) | Extracts candidate IF-THEN logic into a validated schema | Proposals only; SME review before entering the library; never at runtime |

**Tool interface** (also usable via MCP):
- `list_findings(filters)`
- `get_finding(id)` and `get_evidence(id)`
- `get_rule(id)` (spec, citation, resolved parameters)
- `run_rule(id, params)` (sandboxed, read-only what-if; cannot persist)
- `get_baseline(metric, scope)`
- `propose_threshold(rule, param, value)` (returns validation result only)

All LLM outputs pass typed-schema validation. Low-confidence outputs route to a human.

## 7. Seed rule catalog (V1 labor)

Basis and citation status are noted. "Verified" means the clause text was reviewed for this design; everything else needs source verification and CPA review before encoding.

| # | Check | Basis / citation | Numeric floor? |
|---|---|---|---|
| 1 | Labor category charged vs. HR title vs. contract-approved categories | FAR 52.232-7 (qualifications), verified | Zero tolerance |
| 2 | Timekeeping hours vs. payroll hours vs. GL labor dollars | DFARS 252.242-7006(c)(5),(6),(9),(10), verified | Tolerance is customer-set; no regulatory number |
| 3 | Entry timestamp vs. work date (late or reconstructed entries) | Audit practice; customer timekeeping policy | None; customer policy |
| 4 | Entry timestamp clustering (for example, Friday afternoon) | Audit practice | None; baseline-driven |
| 5 | Post-submission timesheet edits: volume, documentation, period-end clustering | FAR 31.201-2 record support, verified via secondary source; DFARS 252.242-7006(c)(7) | Documentation required; volume thresholds customer-set |
| 6 | Supervisor changes without employee concurrence; approval patterns | Audit practice | None |
| 7 | Direct/indirect classification consistency for same employee/activity | DFARS 252.242-7006(c)(2), verified; CAS 402/418 (to verify) | Zero tolerance on inconsistency; sensitivity customer-set |
| 8 | Charges to contracts employee is not assigned to, or outside period of performance | Contract terms | Zero tolerance |
| 9 | Provisional vs. actual indirect rate drift | FAR 52.216-7, verified | Drift tolerance customer-set |
| 10 | T&M ceiling notification | FAR 52.232-7, verified | 85% floor |
| 11 | Final indirect rate proposal deadline | FAR 52.216-7, verified | 6 months after fiscal year-end |
| 12 | Monthly cost determination per contract | DFARS 252.242-7006(c)(11), verified | Monthly |
| 13 | Voucher frequency vs. allowed cadence (T&M) | FAR 52.232-7, verified | About every two weeks |
| 14 | Uncompensated overtime consistency | FAR 31.205-6 (to verify) | To verify |

## 8. Phasing

| Phase | Engine scope |
|---|---|
| **Concierge MVP** | Rule catalog as a spreadsheet; checks as plain Python scripts on CSV exports; manual thresholds; findings in emailed PDF. Goal: learn which checks find real, previously unknown issues. |
| **V1** | Rule spec + parameter resolver + runner + findings store; batch upload; status page and drill-down; LLM explanations; customer thresholds with floors. |
| **V2** | In-environment agent and live sync (CUI stays in customer boundary; only findings and minimal metadata return); conversational Q&A via tool interface. |
| **V3** | Broader sources; per-customer then pooled predictive scoring, only after outcome data exists. |

Do not build engine infrastructure before the concierge stage has validated which checks matter.

## 9. Key risks and mitigations

| Risk | Mitigation |
|---|---|
| Misinterpreting a regulation into a floor | CPA/consultant review per rule; `review_status` gating; cite basis honestly |
| Customers set lax thresholds and assume they are compliant | Two-tier severity; floors enforced; UI language: "meets Sadhik's enforced minimum," never "compliant" or "DCAA-approved" |
| Alert fatigue from overly strict or noisy rules | Customer compliance memory; materiality filters for display; baseline-driven suggestions |
| LLM error in mapping or extraction | Human confirmation gates; deterministic evaluation after |
| Regulatory churn (floors move) | Versioned floors; flag invalid customer values instead of rewriting; time and errors-saved positioning rather than penalties avoided |
| Config visible to auditors | Required reasons and approvals make tolerances defensible |
| Rule sprawl and interdependence | No chaining; independent rules; revisit engine choice at scale |

## 10. Open questions

1. Which of the 14 seed checks are worth building first after design-partner data review?
2. Rule authoring format for V1: pure Python vs. Python plus a declarative spec (JSON Logic or YAML)?
3. Tenancy and storage model for findings and config snapshots once hosted.
4. Who signs off on floors: named external CPA/consultant relationship?
5. Contract-level floors: how much contract data can be reliably extracted in V1 versus entered manually?
6. Default tolerances for reconciliation checks (2, 9): how to derive from baselines rather than guesses?

## 11. References reviewed

- DFARS 252.242-7006 Accounting System Administration (acquisition.gov)
- FAR 52.232-7 Payments under T&M and Labor-Hour Contracts (acquisition.gov)
- FAR 52.216-7 Allowable Cost and Payment (acquisition.gov)
- DCAA Contract Audit Manual (dcaa.mil), chapter list only
- Cherry Bekaert, DCAA Timekeeping Requirements (secondary source)
- Martin Fowler, "Rules Engine" bliki
- HackerNoon, "Decision Engines in Production: JSON Logic, Rules Engines, and When to Scale"
- Brain Co., "LLM-Generated Rules Engines"
- arXiv 2606.13405, "Neuro-Symbolic Agents for Regulated Process Automation"
