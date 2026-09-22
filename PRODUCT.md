# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

React 18.3.1 with Vite, TypeScript, React Router DOM 6.30.6. Existing codebase at `/app/web`.

## Users

| Persona | Role | What they need from the product |
| --- | --- | --- |
| CFO / Controller | Buyer | One overall status, dollars at risk, trend since last period |
| In-house accountant / GovCon bookkeeper | Daily user | Prioritized queue, evidence to resolve each finding, one-click disposition |
| GovCon CPA / advisory firm | Channel partner | Multi-client view, review and sign-off workflow, exportable evidence |
| AI agent (via MCP) | Programmatic user | Query status, findings, evidence and rules; run checks; record dispositions |

## Product Purpose

Sadhik AI is a compliance reconciliation tool for government contracting labor cost compliance. It ingests exports from timekeeping, payroll, GL, HRIS, and contracts, runs deterministic rules mapped to FAR/DCAA requirements, and returns lineage-tagged findings through a web interface.

The system reports whether data is *consistent or inconsistent with a stated requirement*. It never states that a company "is compliant" or uses certification language. Success means finding errors before an audit and helping customers remediate them with documented evidence.

## Positioning

Time saved and errors caught before audit. The product's unique position is deterministic compliance checks over multi-system data with full lineage from finding to raw source file, row number, and timestamp. Competitors may offer rule engines or audit tools, but Sadhik's strength is the deterministic reconciliation engine with complete traceability.

## Operating Context

**Workflow:**
1. Customer uploads six source types: timekeeping, payroll, GL, HRIS, contracts, indirect rate data
2. LLM proposes column-to-field mappings; human confirms once and mappings are reused
3. Engine builds canonical working views, runs deterministic rules, computes metrics
4. Status is derived in four layers: metric → rule → requirement → domain/overall
5. User reviews findings in prioritized queue, adds disposition (confirmed/legit exception/data error)
6. Dispositions feed into customer compliance memory and suppress identical repeats

**Environment:** Batch processing product with daily/weekly runs. Users work in browser during normal business hours. Runs take under 10 minutes for 500 employees.

## Capabilities and Constraints

**Confirmed capabilities:**
- Six source uploads: timekeeping (with metadata), payroll, GL, HRIS, contracts (PDF), indirect rate data
- 13 catalog rules (L-01 through L-13 plus DQ-01), plus DCAA cost accounting rules (C-01, C-02, C-03)
- Four-tier severity model: Critical, High, Medium, Low
- Four-state finding lifecycle: Open → InReview → Confirmed/LegitException/DataError → Remediated/Closed
- Materiality-based floors to reduce noise
- Exposure de-duplication across rules hitting the same entry
- Run replay for deterministic verification
- Period-over-period trend tracking

**Technical constraints:**
- No real customer data in demo (synthetic data only)
- No authentication, multi-tenancy, LLM calls, or MCP server in MVP demo
- 8 of 13 design catalog rules implemented (L-04, L-07, L-10, L-12, L-13 missing)
- Every rule marked `review_status: unreviewed`
- Prior-period history is seeded, not stored from previous runs
- Map to L-01, L-02, L-03, L-05, L-06, L-09 for concierge phase

**Explicitly undecided:**
- Materiality threshold value (currently 0.5% of period labor cost)
- Default threshold calibration method on design-partner data
- CAS applicability: per-customer setting vs inferred from contracts

## Brand Commitments

**Name:** Sadhik AI (Sanskrit: "one who seeks truth")

**Voice:** Professional, precise, non-attestational. Never use "compliant," "DCAA-approved," "certified," or imply certification.

**Logo/Visual:** Brand name is "Sadhik AI" with subtitle "Compliance reconciliation" in header. Existing design uses trust blue (#2563EB) and orange CTA (#EA580C) with Plus Jakarta Sans fonts and glassmorphism style.

## Evidence on Hand

- **Synthetic data generator:** `data/generate.py` produces deterministic company data with injected violations
- **Ground truth manifest:** `data/out/ground_truth.json` with known violations and expected findings
- **Test suite:** 381 engine tests, 75 web tests; gate is `tests/test_recall.py` confirming 99%+ detection
- **Design documents:** `docs/hld/system-design.md` with full spec, Appendices A (labor) and B (DCAA) with worked examples
- **Sample exports:** CSV templates and field checklists for each source type

## Product Principles

1. **Deterministic first:** All metrics and verdicts are computed in code with fixed-point math. LLMs may map schemas, explain findings, or answer Q&A—but never compute or override.
2. **Lineage is evidence:** Every finding traces to raw file hash, sheet/page, and row number so auditors can verify.
3. **Explanation before evidence:** Plain-language what/why/impact/action first; supporting data follows.
4. **No certification language:** Status labels avoid implying certification; use "No open findings" or "N open findings."
5. **Compliance memory:** Legitimate exceptions suppress identical repeats; confirmed findings raise rule weight.

## Accessibility & Inclusion

- WCAG 2.1 AA compliance target
- Status never conveyed by color alone (every badge has text label and glyph)
- Focus states visible for keyboard navigation
- Reduced motion supported via `prefers-reduced-motion`
- Skip-to-main-content link for screen readers
- Table captions and aria-labels on interactive elements
- High contrast mode supported
