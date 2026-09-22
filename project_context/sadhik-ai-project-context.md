# Sadhik AI — Project Context

## What this company is

Sadhik AI builds a continuous reconciliation harness for U.S. government contractors — specifically mid-market **services** contractors (IT, data, engineering, staffing) with 50–500 employees running cost-reimbursable and/or T&M contracts with DCAA exposure.

**Core function:** the product connects to a contractor's existing systems (timekeeping, payroll, general ledger, HRIS, contracts) and continuously checks whether they agree with each other, with the contract terms, with FAR/DCAA requirements, and with the contractor's own historical baseline. When they don't agree, it surfaces a finding — what broke, which rule or comparison it maps to, the dollars and employees affected, and the underlying records as evidence.

**What it is not:** not a system of record (doesn't replace Costpoint, Unanet, QuickBooks, ADP, etc.), not a BI/analytics dashboard, not a clause-tracking or FAR-database product, not a general GRC platform, not timekeeping software.

**One-line description:** Find the problems before the government does.

## Why this exists (the gap)

- Mid-market services contractors carry the same DCAA/FAR compliance burden as large primes without the internal compliance staff to manage it.
- Data lives scattered across disconnected systems; nothing checks whether those systems agree with each other. A labor category mismatch between HRIS and timekeeping, or a Friday-afternoon timestamp pattern across a whole pay period, is invisible in any single system and only surfaces at audit time.
- The closest direct competitor, GovComply, targets defense **manufacturers** (CPSR, supplier quality, flowdown). The labor/cost-reconciliation gap for **services** contractors is the identified opening.
- Legacy ERPs (Deltek Costpoint, Unanet, JAMIS, PROCAS) are systems of record, not reconciliation layers — they don't audit themselves against other systems.
- CMMC-only tools (Vanta, Drata, Hyperproof) and pre-award tools (GovDash, TechnoMile) don't touch this specific problem.

## Target customer

- **ICP:** Mid-market government services contractors, ~$25M–$150M revenue, 50–500 employees, cost-reimbursable/T&M contract exposure, lean or outsourced finance function.
- **Buyer:** CFO or Controller.
- **User:** in-house accountant or outsourced GovCon bookkeeper.
- **Channel:** GovCon-specialist CPA/advisory firms (Redstone GCI, CohnReznick, Diener & Associates, RKI Accounting, Jameson CPA-type firms) — potential distribution partners, not competitors.

## First domain: Labor reconciliation

Labor is the highest-stakes, most data-detectable, most services-contractor-specific exposure in govcon compliance (DCAA floor checks / MAAR 6), and the least well-served by existing tools. Everything below should be scoped to labor first; do not expand into CMMC, proposals, or clause-tracking until this is validated.

**Core checks (deterministic, not LLM-judged):**
- Labor category charged vs. HR job title vs. contract-approved categories
- Recorded hours vs. paid hours (timekeeping ↔ payroll) vs. labor dollars in the GL
- Entry timestamp vs. work date (late/reconstructed entries); clustering analysis (e.g., disproportionate Friday-afternoon entries)
- Post-submission timesheet edits — volume, documentation, clustering near period-end
- Direct/indirect classification consistency over time for the same employee/activity
- Charges to contracts the employee isn't assigned to, or to closed/expired periods of performance
- Indirect rate drift: actual vs. provisional billing rates, pool/base consistency month to month

**Systems to reconcile across:** timekeeping (incl. metadata: timestamps, edit history, approvals), payroll, general ledger, HRIS, contracts (often unstructured PDFs), and — later — billing/invoicing.

## Architecture principles

1. **Schema-light, agent-native, not ontology-first.** Do not build a persistent, all-purpose data model (Foundry-style) before the product delivers value. Read and reason over source data on demand.
2. **Deterministic code does the math. AI does the reasoning around it.** Rate calculations, threshold tests, reconciliation math, and transaction matching must be deterministic — never LLM-inferred. Use AI/agentic reasoning for: mapping messy or inconsistent schemas across systems, reading unstructured contracts and policies, explaining findings in plain language, and investigating a flag when a reviewer asks a follow-up question.
3. **Export-first ingestion before live connectors.** V1 accepts exports/uploads (QuickBooks exports, timesheet CSVs, cost reports). Live, in-environment connectors are a later-stage capability.
4. **CUI-tier data does not need to leave the customer's environment.** Where a live connector exists, it runs inside the customer's boundary (or their GovCloud tenant); only findings and minimal metadata return to the hosted layer. Full FedRAMP authorization is a later milestone (FedRAMP 20x, once funded), not a V1 blocker.
5. **Findings are lineage-tagged.** Every finding records its source record(s), the rule/comparison triggered, timestamp, and resolution status. This evidence trail is itself a customer deliverable, not just internal bookkeeping.
6. **Customer-specific compliance memory compounds over time.** CPA/controller review of each finding (confirmed real vs. legitimate exception) should be captured and reused — this is a stronger near-term moat than a cross-customer predictive model, which requires data the business won't have early on.

## UI principles

- **Status-first, not BI/analytics-first.** The interface answers "what's wrong and what do I do about it," not "explore this data yourself." If a screen requires interpreting a chart to know whether something's wrong, it's the wrong design.
- Top level: one overall state (e.g., "Audit-Ready" / "3 issues found").
- Domain list below that (Labor now; DCAA cost-accounting broadly, then CMMC, then proposals as future domains) — frame as **compliance domains**, not "certifications" (DCAA is continuous-state, not a one-time cert like CMMC).
- Each finding, drilled into: plain-language explanation first (what happened, why it matters, dollars/employees/contracts affected, regulation or policy cited, recommended action), supporting evidence/chart underneath — never the reverse order.
- Conversational follow-up on any finding, powered by the reasoning agent over the same underlying data (internal tool use / MCP as plumbing, not the primary interface).

## Build phasing

| Phase | Ingestion | Reasoning | UI | Scope |
|---|---|---|---|---|
| Concierge MVP | Manual export, human-run checks | Founder + scripts + Claude-assisted analysis | Emailed PDF | Labor only, 1–2 design partners |
| V1 product | Batch upload | Deterministic rules + LLM explanation | Status page + drill-down | Labor only |
| V2 | In-environment agent, live sync | + conversational Q&A | + ask-a-question | Labor + broader DCAA cost-accounting |
| V3 | Broader source coverage | Tier 2: predictive risk scoring (per-customer, then pooled) begins | Risk trajectory added | + CMMC evidence |

**Current stage (as of this writing): pre-software.** No product has been built. The immediate priority is customer discovery — interviews with GovCon CPA firms, controllers/CFOs, and DCAA consultants — and a fully manual concierge delivery to 1–2 design partners, sourced via CPA-firm relationships and personal network introductions. Do not over-build; do not write integrations before the manual process has validated which checks actually produce real, previously-unknown findings.

## Constraints and things to avoid

- Do not position this as CPA-replacement software. DCAA reconciliation work does not legally require a licensed CPA, but the sharper and more defensible pitch is that this makes the CPA relationship faster and cheaper, not obsolete.
- Do not build clause-tracking, FAR-database, or audit-package-generation features as a starting point — that's GovComply's territory and a crowded lane.
- Do not lean on CMMC urgency or DFARS business-system withhold urgency in positioning — both have seen recent regulatory softening (CMMC Phase 2 suspended July 2026; CAS thresholds raised Oct 2026, narrowing DFARS 252.242-7005 reach). Build the pitch on time saved and errors caught, which survives regulatory churn — not penalties avoided, which doesn't.
- Do not claim or imply CPA attestation, an audit opinion, or "DCAA-approved" certification for any output — these are legally restricted terms.
- Do not overclaim predictive/Tier 2 capability to early customers before real outcome data exists to back it.

## Founder context

Solo founder (Dev), financial-services engineering background (PGIM Fixed Income data management, EY FSO consulting), currently a Forward Deployed Engineer at a B2B analytics startup — direct experience building lineage-tracked, audit-defensible data pipelines and deploying software inside customer environments. No technical co-founder yet (a real gap for any venture-scale pitch). Bootstrapping through at least January 2027 before evaluating accelerator/funding paths (Spring 2027 batch timing, not Winter).
