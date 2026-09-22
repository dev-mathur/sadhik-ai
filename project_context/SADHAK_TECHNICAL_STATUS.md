# Sadhik AI — Technical Status (handoff for Claude Code)

_As of 2026-09-20. Owner: Dev (solo founder). Stage: **pre-software**. Nothing has been built._

## 1. What we're building

A continuous reconciliation harness for U.S. government **services** contractors (IT, data, engineering, staffing; 50–500 employees; ~$25M–$150M revenue; cost-reimbursable and/or T&M contracts with DCAA exposure). It ingests data from the contractor's existing systems and checks whether they agree with each other, with contract terms, with FAR/DCAA requirements, and with the contractor's own history. Disagreements become **findings**: what broke, which rule fired, dollars and employees affected, and the underlying records as evidence.

One-liner: *Find the problems before the government does.*

Not: a system of record, a BI dashboard, a clause/FAR tracker, a GRC platform, timekeeping software, or a CPA replacement.

## 2. Current state

| Area | Status |
|---|---|
| Code | None. No repo, no prototype. |
| Rules catalog | Check list defined at headline level (section 5); no formal per-rule spec yet. |
| Finding/lineage schema | Principle defined (section 4); no concrete schema yet. |
| HLD / LLD | Not started. Deliberately deferred. |
| Concierge MVP | Shape defined (section 6); data-request template and report format not yet written. |
| Customer discovery | Not started (no interviews logged, no design partners). |
| Funding / team | Bootstrapping through at least Jan 2027; no technical co-founder. |

## 3. Priority order (what to define, and when)

1. Discovery hypotheses and interview guide. Blocks everything.
2. Buyer/channel decision (see open decision D1).
3. **Rules catalog v0** as a spec, not an engine.
4. **Concierge MVP definition**: data-request template, findings report format, scope/limits statement, design-partner terms.
5. Finding schema and lineage format (one page).
6. Rules engine, **only after 2–3 concierge runs** show which checks produce real findings.
7. HLD (V1 batch upload), after schema and rules have met real data.
8. LLD, per component, just before building.

Parallel, non-blocking: positioning one-pager; technical co-founder search.

## 4. Architecture principles (binding)

1. **Schema-light, agent-native, not ontology-first.** No persistent all-purpose data model before the product delivers value. Read and reason over source data on demand.
2. **Deterministic code does the math; AI reasons around it.** Rate calculations, thresholds, reconciliation math, and transaction matching are deterministic and never LLM-inferred. AI is for: mapping messy schemas across systems, reading unstructured contracts/policies, plain-language explanations, and follow-up investigation of a flag.
3. **Export-first ingestion.** V1 takes uploads/exports (QuickBooks exports, timesheet CSVs, cost reports). Live connectors come later.
4. **CUI-tier data stays in the customer's environment.** Any live connector runs inside their boundary or GovCloud tenant; only findings and minimal metadata return to the hosted layer. FedRAMP is a later milestone, not a V1 blocker.
5. **Findings are lineage-tagged.** Each finding records source record(s), rule/comparison triggered, timestamp, resolution status. The evidence trail is a customer deliverable.
6. **Customer-specific compliance memory compounds.** Reviewer verdicts (confirmed real vs. legitimate exception) are captured and reused. This is the near-term moat, not cross-customer prediction.

## 5. First domain: labor reconciliation (rules to spec)

All checks are deterministic. Systems: timekeeping (with metadata: timestamps, edit history, approvals), payroll, GL, HRIS, contracts (often unstructured PDFs); billing later.

| ID | Check | Comparison |
|---|---|---|
| L1 | Labor category | Charged category vs. HR job title vs. contract-approved categories |
| L2 | Hours and dollars | Recorded hours (timekeeping) vs. paid hours (payroll) vs. labor dollars (GL) |
| L3 | Entry timing | Entry timestamp vs. work date; clustering (e.g., Friday-afternoon pattern) |
| L4 | Post-submission edits | Volume, documentation, clustering near period end |
| L5 | Direct/indirect consistency | Classification of the same employee/activity over time |
| L6 | Assignment and period of performance | Charges to unassigned contracts or closed/expired POPs |
| L7 | Indirect rate drift | Actual vs. provisional billing rates; pool/base consistency month to month |

**Each rule spec (to write) must define:** required input fields per source system; exact test logic; thresholds; known false-positive exceptions; dollar-exposure calculation; regulatory/policy citation; evidence records returned.

## 6. Build phasing

| Phase | Ingestion | Reasoning | UI | Scope |
|---|---|---|---|---|
| Concierge MVP | Manual export, human-run checks | Founder + scripts + Claude-assisted analysis | Emailed PDF | Labor only, 1–2 design partners |
| V1 | Batch upload | Deterministic rules + LLM explanation | Status page + drill-down | Labor only |
| V2 | In-environment agent, live sync | + conversational Q&A | + ask-a-question | Labor + broader DCAA cost accounting |
| V3 | Broader sources | Predictive risk scoring (per-customer first) | Risk trajectory | + CMMC evidence |

**Do not write integrations before the manual process shows which checks yield real, previously unknown findings.**

## 7. UI principles (for V1 design later)

- Status-first, not analytics-first: top-level state ("Audit-Ready" / "3 issues found"), then domains (Labor now).
- Finding drill-down order: plain-language explanation (what, why it matters, dollars/employees/contracts, citation, recommended action) first, evidence and charts underneath.
- Frame domains as continuous **compliance domains**, not certifications.
- Conversational follow-up on any finding via the reasoning agent (tool use/MCP is plumbing, not the interface).

## 8. Constraints and guardrails

- Never claim or imply CPA attestation, an audit opinion, or "DCAA-approved". These are legally restricted terms. Applies to code strings, report templates, and UI copy.
- Do not build clause tracking, FAR database, or audit-package generation (GovComply's lane).
- Do not build positioning on CMMC or DFARS-withhold urgency (regulatory softening). Pitch on time saved and errors caught.
- Do not overclaim predictive capability before outcome data exists.
- Do not position as CPA-replacement software.

## 9. Open decisions

- **D1: Channel/buyer.** Project brief lists GovCon CPA/advisory firms as the channel; Dev's latest stated GTM decision is **direct to contractors** (CFO/Controller buyer; in-house accountant or outsourced GovCon bookkeeper as user), seeing no benefit for CPA firms. Treat direct as current; the brief needs updating. Interviews should still test the CPA/bookkeeper's role (buyer, user, or obstacle).
- **D2: Data handling terms** for concierge (NDA, where customer data lives, retention and deletion).
- **D3: Design-partner offer** (pricing/free pilot, scope, timeline).
- **D4: Technical co-founder** (needed for any venture-scale pitch; not blocking now).

## 10. Suggested first tasks for Claude Code

Work in docs and small scripts only. No product code yet.

1. Create repo skeleton:
   ```
   /docs/rules/        # one file per rule L1–L7 using the spec template in section 5
   /docs/concierge/    # data-request template, report template, scope/limits statement
   /docs/schema/       # finding + lineage schema (one page)
   /scripts/           # throwaway concierge analysis scripts
   /fixtures/          # synthetic sample exports (timesheet, payroll, GL, HRIS) for testing rules
   ```
2. Draft rule specs L1–L7 (section 5 template), flagging assumptions that need practitioner validation.
3. Draft the concierge data-request checklist and findings-report template (PDF-ready, guardrail-compliant wording).
4. Draft the finding/lineage schema.
5. Generate synthetic fixtures with seeded defects so scripts can be tested before real data exists.

Stop and ask Dev before: adding any connector, persistent data model, UI, or anything in section 8's "do not" list.

## 11. Provenance and gaps

Compiled from the Sadhik AI project brief and stated memory. No other sessions or project docs were available when this was written, so any decisions made elsewhere are not reflected. Reconcile if found.
