# Sadhik AI - System Design Documentation

**Version:** 1.0  
**Date:** September 22, 2026  
**Application:** Government Contracting Compliance Reconciliation Platform

---

## Table of Contents

1. [Executive Summary](#executive-summary)
2. [System Architecture](#system-architecture)
3. [Data Model](#data-model)
4. [Data Flow Diagrams](#data-flow-diagrams)
5. [Component Details](#component-details)
6. [Rule Engine Architecture](#rule-engine-architecture)
7. [API Specification](#api-specification)
8. [Deployment Architecture](#deployment-architecture)

---

## Executive Summary

Sadhik AI is a **deterministic compliance reconciliation platform** for government contractors. It ingests data from multiple source systems (timekeeping, payroll, GL, HRIS, contracts, rate data), runs FAR/DCAA-mapped compliance rules, and produces findings with complete lineage traceability.

### Key Characteristics

- **Deterministic**: All computations use fixed-point arithmetic; no probabilistic outputs
- **Lineage-driven**: Every finding traces to source file, row number, and hash
- **Multi-domain**: Supports Labor compliance and DCAA cost accounting domains
- **Batch processing**: Daily/weekly runs under 10 minutes for 500 employees
- **Zero-certification language**: Reports inconsistencies, never certifies compliance

### Technology Stack

- **Backend**: Python 3.13, FastAPI, SQLite
- **Frontend**: React 18.3, TypeScript, Vite, React Router
- **Data Processing**: Pandas-style canonical views, YAML configuration
- **Deployment**: Render (free tier), containerized services

---

## System Architecture

### High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                         USER INTERFACE LAYER                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │   React SPA (Port 5173 / Render Web Service)                 │  │
│  │   - Status Dashboard      - Findings Queue                   │  │
│  │   - Finding Detail        - Rules & Config Management        │  │
│  │   - Run History & Replay  - Evidence Viewer                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    │ HTTPS/JSON
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                          API LAYER (FastAPI)                         │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  REST Endpoints                                              │  │
│  │  /api/status    /api/findings    /api/rules                 │  │
│  │  /api/runs      /api/config      /api/evidence              │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Service Layer (api/service.py - 52.8KB)                    │  │
│  │  - Run orchestration          - Finding lifecycle           │  │
│  │  - Disposition management     - Config validation           │  │
│  │  - Status rollup calculation  - Evidence pagination         │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        PERSISTENCE LAYER                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  SQLite Database (sadhik.db)                                 │  │
│  │  - Runs (manifest, metadata, timestamps)                     │  │
│  │  - Findings (status, disposition, severity, exposure)        │  │
│  │  - Config versions (YAML snapshots, validation results)      │  │
│  │  - Evidence references (lineage, source hashes)              │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                        RULE ENGINE LAYER                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Runner (engine/runner.py)                                   │  │
│  │  - Tier orchestration (Fast → Pay Period → Close)           │  │
│  │  - Rule selection and execution                              │  │
│  │  - Result aggregation and rollup                             │  │
│  │  - Manifest generation (deterministic replay)                │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Rule Registry (13 rules across 2 domains)                   │  │
│  │  Labor Domain: L-01, L-02, L-03, L-05, L-06, L-08, L-09     │  │
│  │  DCAA Domain: L-11, C-01, C-02, C-03                        │  │
│  │  Data Quality: DQ-01                                         │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Rollup Engine (engine/rollup.py)                           │  │
│  │  4-Layer Status: Metric → Rule → Requirement → Domain       │  │
│  │  M13 Exposure Deduplication                                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      INGESTION & CANONICAL LAYER                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Ingest Engine (engine/ingest.py)                           │  │
│  │  - CSV/JSON parsing with schema mapping                     │  │
│  │  - Hash calculation (SHA-256 per file)                      │  │
│  │  - Lineage attachment (file:row references)                 │  │
│  │  - Working view construction (per-run, ephemeral)           │  │
│  └──────────────────────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Canonical Models (engine/canonical.py)                     │  │
│  │  TimeEntry • PayRecord • GLLine • Employee • Contract        │  │
│  │  TimeEdit • RateAgreement • LaborCategory • ChargeCodeMap    │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                                    │
                                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                         SOURCE SYSTEMS LAYER                         │
│  ┌──────────────┬──────────────┬──────────────┬──────────────────┐ │
│  │ Timekeeping  │   Payroll    │      GL      │       HRIS       │ │
│  │   (CSV)      │    (CSV)     │    (CSV)     │      (CSV)       │ │
│  │              │              │              │                  │ │
│  │ • Time       │ • Pay        │ • Account    │ • Employee       │ │
│  │   entries    │   records    │   lines      │   roster         │ │
│  │ • Edit       │ • Hours      │ • Periods    │ • Titles         │ │
│  │   history    │ • Gross pay  │ • Projects   │ • Hire dates     │ │
│  └──────────────┴──────────────┴──────────────┴──────────────────┘ │
│  ┌──────────────┬──────────────────────────────────────────────┐   │
│  │  Contracts   │         Rate Data & Reference Tables         │   │
│  │   (JSON)     │              (CSV/JSON)                      │   │
│  │              │                                              │   │
│  │ • Terms      │ • Provisional rates    • Charge codes       │   │
│  │ • PoP dates  │ • Pool definitions     • Category crosswalk │   │
│  │ • Ceiling    │ • ICS submission dates • Allowability       │   │
│  └──────────────┴──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Architecture Principles

1. **Separation of Concerns**
   - UI layer only renders; no business logic
   - API layer orchestrates; delegates computation to engine
   - Engine layer is pure functions; no I/O, no clock, no randomness

2. **Deterministic Processing**
   - Fixed-point decimal arithmetic (no floating point)
   - Sorted input processing for order-independence
   - Manifest captures all inputs for replay verification

3. **Lineage as First-Class Citizen**
   - Every canonical record carries `Lineage(file, hash, row)`
   - Findings reference evidence via lineage
   - Auditors can trace to original source cells

4. **Four-Layer Rollup**
   - Metric (computed by rule logic)
   - Rule (aggregates metrics, produces finding)
   - Requirement (worst result among mapped rules)
   - Domain/Overall (coverage-aware status label)

---

## Data Model

### Entity Relationship Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                         DATABASE SCHEMA (SQLite)                     │
└─────────────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│       runs           │
├──────────────────────┤
│ id (PK)              │───┐
│ period               │   │
│ tier                 │   │
│ executed_at          │   │
│ manifest (JSON)      │   │
│ config_hash          │   │
│ result (JSON)        │   │
│ status               │   │
└──────────────────────┘   │
                           │
                           │ 1:N
                           │
┌──────────────────────┐   │
│      findings        │   │
├──────────────────────┤   │
│ id (PK)              │   │
│ run_id (FK)          │───┘
│ rule_id              │
│ severity             │
│ result               │
│ exposure             │
│ headline             │
│ explanation (JSON)   │
│ evidence_refs (JSON) │
│ fingerprint          │
│ disposition          │
│ disposition_at       │
│ disposition_by       │
│ disposition_note     │
│ reason_code          │
└──────────────────────┘

┌──────────────────────┐
│   config_versions    │
├──────────────────────┤
│ version (PK)         │
│ yaml_content         │
│ created_at           │
│ created_by           │
│ validation_result    │
│ is_active            │
└──────────────────────┘
```

### Canonical Working View (Per-Run, Ephemeral)

```python
@dataclass(frozen=True)
class WorkingView:
    """Per-run canonical view built from source CSVs"""
    
    # Core entities
    employees: list[Employee]           # From HRIS
    time_entries: list[TimeEntry]       # From timekeeping
    time_edits: list[TimeEdit]          # From timekeeping edit log
    pay_records: list[PayRecord]        # From payroll
    gl_lines: list[GLLine]              # From general ledger
    
    # Contract and rate data
    contracts: list[Contract]           # From contracts JSON
    labor_categories: list[LaborCategory]
    charge_code_map: dict[str, ChargeCodeMap]
    rate_agreements: list[RateAgreement]
    
    # Reference tables (confirmed)
    account_allowability: dict[str, AccountAllowability]
    filing_schedule: dict[str, FilingSchedule]
    
    # Historical baselines (for trend detection)
    metric_history: dict[str, list[MetricSnapshot]]
    classification_history: dict[str, EmployeeClassificationHistory]
```

### Domain Models

#### 1. Source System Entities

```python
@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    title: str
    hire_date: date
    term_date: date | None
    exempt_status: str  # "exempt" | "non_exempt"
    home_department: str
    lineage: Lineage  # trace to HRIS CSV

@dataclass(frozen=True)
class TimeEntry:
    entry_id: str
    employee_id: str
    work_date: date
    hours: Decimal
    charge_code: str
    labor_category: str
    entered_at: datetime
    submitted_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    lineage: Lineage  # trace to timekeeping CSV

@dataclass(frozen=True)
class PayRecord:
    employee_id: str
    pay_period: str  # "2026-08-A"
    regular_hours: Decimal
    overtime_hours: Decimal
    pto_hours: Decimal
    gross_pay: Decimal
    lineage: Lineage  # trace to payroll CSV

@dataclass(frozen=True)
class GLLine:
    account: str
    project: str
    period: str  # "2026-08"
    amount: Decimal
    cost_type: str  # "direct" | "indirect"
    pool: str | None  # overhead | ga | fringe
    lineage: Lineage  # trace to GL CSV
```

#### 2. Rule Engine Entities

```python
@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    result: Result  # CONSISTENT | EXCEPTION | WATCH | NOT_EVALUATED | NOT_APPLICABLE
    findings: list[Finding]
    metrics: dict[str, Any]
    message: str | None

@dataclass(frozen=True)
class Finding:
    rule_id: str
    severity: Severity  # CRITICAL | HIGH | MEDIUM | LOW
    exposure: Decimal  # dollar amount at risk
    headline: str
    explanation: Explanation
    evidence: list[EvidenceRef]
    fingerprint: str  # for deduplication

@dataclass(frozen=True)
class EvidenceRef:
    source_file: str
    sha256: str
    row: int
    columns: dict[str, Any]  # relevant field values
```

#### 3. Configuration Entities

```python
@dataclass(frozen=True)
class CustomerConfig:
    config_version: int
    customer: str
    effective_from: date
    domains: list[str]  # ["labor", "dcaa_cost_accounting"]
    defaults: dict[str, Any]  # tier-level defaults
    rules: dict[str, RuleConfig]  # per-rule overrides

@dataclass(frozen=True)
class RuleConfig:
    enabled: bool
    status: str  # "active" | "not_applicable"
    reason: str | None
    params: dict[str, Decimal | int | str]
    scope: dict[str, dict[str, Any]]  # contract-level overrides
```

---

## Data Flow Diagrams

### 1. End-to-End Run Flow

```
┌─────────────┐
│    USER     │
│  Clicks     │
│ "Run Now"   │
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  FRONTEND (React)                                             │
│  POST /api/runs { period: "2026-08", tier: "close" }        │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  API SERVICE LAYER                                            │
│  1. Validate period and tier                                 │
│  2. Load active config from DB                               │
│  3. Call engine.runner.run()                                 │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  INGESTION PHASE (engine/ingest.py)                          │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 1. Read sources.json (manifest of uploaded files)      │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 2. Hash each CSV/JSON (SHA-256)                        │ │
│  │    - meridian_time_2026-08.csv                         │ │
│  │    - bamboo_payroll_2026-08.csv                        │ │
│  │    - quickbooks_gl_2026-08.csv                         │ │
│  │    - adp_hris.csv                                      │ │
│  │    - contracts.json                                    │ │
│  │    - rate_data.json                                    │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 3. Apply column mappings (config/mappings/*.yaml)      │ │
│  │    - Meridian → canonical TimeEntry                    │ │
│  │    - Bamboo → canonical PayRecord                      │ │
│  │    - QuickBooks → canonical GLLine                     │ │
│  │    - ADP → canonical Employee                          │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 4. Attach lineage (file:row) to every record           │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 5. Build WorkingView (in-memory canonical dataset)     │ │
│  └───┬────────────────────────────────────────────────────┘ │
└──────┼────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  RULE EXECUTION PHASE (engine/runner.py)                     │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 1. Filter rules by tier and enabled domains            │ │
│  │    Tier: "close" → runs Fast + Pay Period + Close      │ │
│  │    Domains: ["labor", "dcaa_cost_accounting"]          │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 2. For each rule (in deterministic order):             │ │
│  │    a. Resolve parameters (floor → default → customer)  │ │
│  │    b. Execute rule.check(view, params)                 │ │
│  │    c. Collect findings with evidence                   │ │
│  │                                                         │ │
│  │    Example: L-03 (Payroll vs GL reconciliation)        │ │
│  │    - Sum payroll.gross_pay by period                   │ │
│  │    - Sum gl_lines where account in labor_accounts      │ │
│  │    - If abs(payroll - gl) > threshold → Finding        │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 3. M13 Exposure Deduplication                          │ │
│  │    - Group findings by overlapping evidence            │ │
│  │    - Take max exposure per evidence group              │ │
│  │    - Prevents double-counting same dollar              │ │
│  └───┬────────────────────────────────────────────────────┘ │
└──────┼────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  ROLLUP PHASE (engine/rollup.py)                             │
│                                                               │
│  Four-Layer Status Rollup:                                   │
│                                                               │
│  Layer 1: METRIC RESULTS                                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ L-03: payroll_vs_gl_variance = $1,247.82            │   │
│  │       threshold = $500.00                             │   │
│  │       result = EXCEPTION                              │   │
│  └──────────────────────────────────────────────────────┘   │
│           │                                                   │
│           ▼                                                   │
│  Layer 2: RULE RESULTS                                       │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ L-03: 1 finding, severity=HIGH, exposure=$1,247.82   │   │
│  │       result = EXCEPTION                              │   │
│  └──────────────────────────────────────────────────────┘   │
│           │                                                   │
│           ▼                                                   │
│  Layer 3: REQUIREMENT RESULTS (grouped by FAR authority)     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ FAR 31.201-2: worst(L-03, L-08, L-11) = EXCEPTION    │   │
│  └──────────────────────────────────────────────────────┘   │
│           │                                                   │
│           ▼                                                   │
│  Layer 4: DOMAIN & OVERALL STATUS                            │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ Labor: 8 open findings, $41,298.93, coverage 6/6     │   │
│  │ DCAA: 6 open findings, $61,192.58, coverage 5/5      │   │
│  │                                                        │   │
│  │ OVERALL: "14 open findings"                           │   │
│  │          $102,491.51 total exposure                   │   │
│  │          Coverage: 11 of 11 (1.00)                    │   │
│  └──────────────────────────────────────────────────────┘   │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  PERSISTENCE PHASE                                            │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 1. Build manifest (source hashes, config hash)         │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 2. Insert run record into SQLite                       │ │
│  │    - run_id, period, tier, executed_at                 │ │
│  │    - manifest JSON, config_hash                        │ │
│  │    - result JSON (rollup summary)                      │ │
│  └───┬────────────────────────────────────────────────────┘ │
│      │                                                        │
│      ▼                                                        │
│  ┌────────────────────────────────────────────────────────┐ │
│  │ 3. Insert findings with lineage                        │ │
│  │    - Each finding → separate row                       │ │
│  │    - evidence_refs JSON (file:row citations)           │ │
│  │    - fingerprint for deduplication                     │ │
│  └───┬────────────────────────────────────────────────────┘ │
└──────┼────────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  API RESPONSE                                                 │
│  {                                                            │
│    "run_id": "run_20260922_151423",                          │
│    "status": "completed",                                    │
│    "findings_count": 14,                                     │
│    "total_exposure": "102491.51"                             │
│  }                                                            │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  FRONTEND UPDATE                                              │
│  - Refresh status dashboard                                  │
│  - Update findings queue                                     │
│  - Show success notification                                 │
└──────────────────────────────────────────────────────────────┘
```

### 2. Finding Detail & Disposition Flow

```
┌─────────────┐
│    USER     │
│   Clicks    │
│  Finding    │
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  GET /api/findings/{finding_id}                              │
│                                                               │
│  Returns:                                                     │
│  {                                                            │
│    "finding_id": "L03_2026-08_001",                          │
│    "rule_id": "L-03",                                        │
│    "severity": "high",                                       │
│    "exposure": "1247.82",                                    │
│    "headline": "Payroll and GL labor cost mismatch",         │
│    "explanation": {                                          │
│      "what": "Payroll reported $502,345.67...",             │
│      "why": "FAR 31.201-2 requires...",                     │
│      "impact": "$1,247.82 unreconciled variance",           │
│      "action": "Review GL coding..."                        │
│    },                                                         │
│    "evidence_summary": {                                     │
│      "total_rows": 1247,                                    │
│      "sources": ["bamboo_payroll_2026-08.csv",             │
│                  "quickbooks_gl_2026-08.csv"]              │
│    },                                                         │
│    "disposition": null,                                      │
│    "status": "open"                                          │
│  }                                                            │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  GET /api/findings/{finding_id}/evidence?page=1              │
│                                                               │
│  Returns paginated evidence with lineage:                    │
│  {                                                            │
│    "total": 1247,                                            │
│    "page": 1,                                                │
│    "page_size": 50,                                          │
│    "items": [                                                │
│      {                                                        │
│        "source": "bamboo_payroll_2026-08.csv:142",          │
│        "employee_id": "E-0089",                             │
│        "gross_pay": "4567.89",                              │
│        "hash": "a3f5d..."                                   │
│      },                                                       │
│      {                                                        │
│        "source": "quickbooks_gl_2026-08.csv:876",           │
│        "account": "5100-LABOR",                             │
│        "amount": "4568.12",                                 │
│        "hash": "a3f5d..."                                   │
│      }                                                        │
│    ]                                                          │
│  }                                                            │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  USER REVIEWS EVIDENCE                                        │
│  - Sees source file:row for each data point                 │
│  - Can verify against original uploads                       │
│  - Decides: "This is a legitimate exception"                │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  POST /api/findings/{finding_id}/disposition                 │
│  {                                                            │
│    "disposition": "legitimate_exception",                    │
│    "reason_code": "timing_difference",                       │
│    "note": "Payroll accrual vs cash basis GL",              │
│    "actor": "jane.smith@company.com"                        │
│  }                                                            │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  SERVICE LAYER PROCESSING                                     │
│                                                               │
│  1. Validate disposition and reason_code                     │
│  2. Update finding record:                                   │
│     - disposition = "legitimate_exception"                   │
│     - disposition_at = current_timestamp                     │
│     - disposition_by = "jane.smith@company.com"             │
│     - disposition_note = "Payroll accrual..."               │
│     - reason_code = "timing_difference"                     │
│  3. Recalculate rollup:                                      │
│     - Subtract exposure from domain total                    │
│     - Update status if no open findings remain               │
│  4. Store in compliance memory:                              │
│     - Fingerprint → disposition mapping                      │
│     - Suppress identical findings in future runs             │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  RESPONSE & UI UPDATE                                         │
│  - Finding status changes to "Legitimate Exception"          │
│  - Total exposure decreases: $102,491.51 → $101,243.69      │
│  - Status remains "14 open findings" → "13 open findings"   │
│  - UI shows disposition badge with timestamp and actor       │
└──────────────────────────────────────────────────────────────┘
```

### 3. Deterministic Replay Flow

```
┌─────────────┐
│    USER     │
│   Clicks    │
│  "Replay"   │
└──────┬──────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  POST /api/runs/{run_id}/replay                              │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  LOAD ORIGINAL RUN MANIFEST                                   │
│  {                                                            │
│    "sources": {                                              │
│      "meridian_time_2026-08.csv": "a3f5d89b...",           │
│      "bamboo_payroll_2026-08.csv": "b7e2c41...",           │
│      ...                                                      │
│    },                                                         │
│    "config_hash": "d8a9f3e...",                             │
│    "executed_at": "2026-09-15T10:30:00Z"                    │
│  }                                                            │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  VERIFY CURRENT FILES MATCH MANIFEST                          │
│                                                               │
│  For each source file:                                       │
│    1. Read current file from data/out/                       │
│    2. Calculate SHA-256 hash                                 │
│    3. Compare to manifest hash                               │
│                                                               │
│  If ANY hash differs:                                        │
│    → Return { "identical": false,                            │
│               "changed_files": ["bamboo_payroll..."] }      │
│    → ABORT (no re-run)                                       │
└──────┬───────────────────────────────────────────────────────┘
       │ All hashes match
       ▼
┌──────────────────────────────────────────────────────────────┐
│  RE-RUN ENGINE WITH IDENTICAL INPUTS                          │
│  - Same source files (verified by hash)                      │
│  - Same config version (verified by hash)                    │
│  - Same tier and period                                      │
│  - Different execution timestamp (now)                       │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  COMPARE RESULTS                                              │
│                                                               │
│  Original:                          Replay:                  │
│  - 14 findings                      - 14 findings            │
│  - $102,491.51 exposure            - $102,491.51 exposure   │
│  - Fingerprints: [f1, f2, ...]     - Fingerprints: [f1, f2] │
│                                                               │
│  If results differ:                                          │
│    → { "identical": false, "diff": {...} }                  │
│                                                               │
│  If results match:                                           │
│    → { "identical": true }                                   │
└──────┬───────────────────────────────────────────────────────┘
       │
       ▼
┌──────────────────────────────────────────────────────────────┐
│  RESPONSE                                                     │
│  {                                                            │
│    "identical": true,                                        │
│    "original_run": "run_20260915_103000",                   │
│    "replay_run": "run_20260922_151500",                     │
│    "verified": {                                             │
│      "sources": 6,                                          │
│      "findings": 14,                                        │
│      "exposure": "102491.51"                                │
│    }                                                          │
│  }                                                            │
└──────────────────────────────────────────────────────────────┘
```

---

## Component Details

### 1. Rule Engine Components

#### Rule Base Class
```python
class Rule:
    """Base class for all compliance rules"""
    
    id: str              # "L-03"
    name: str            # "Payroll vs GL reconciliation"
    domain: str          # "labor" | "dcaa_cost_accounting"
    tier: Tier           # FAST | PAY_PERIOD | CLOSE
    authorities: list[str]  # ["FAR 31.201-2", "DCAA CAM 6-404"]
    
    def check(self, view: WorkingView, params: dict) -> RuleResult:
        """Execute rule logic, return findings"""
        pass
    
    def param_spec(self) -> list[ParamSpec]:
        """Define configurable parameters with floors"""
        pass
```

#### Implemented Rules

**Labor Domain (6 rules):**

| Rule ID | Name | Tier | What It Checks |
|---------|------|------|----------------|
| **L-01** | Labor category compliance | Pay Period | HR title vs charged labor category vs contract-approved categories |
| **L-02** | Timekeeping vs payroll hours | Pay Period | Hours in timekeeping system match hours in payroll |
| **L-03** | Payroll vs GL reconciliation | Close | Payroll gross wages match GL labor accounts |
| **L-05** | Late entry patterns | Fast | Entries submitted >48 hours after work date, Friday clustering |
| **L-06** | Post-submission edits | Fast | Edits made after timesheet approval without documented reason |
| **L-09** | Period of performance | Fast | Charges outside contract PoP dates |

**DCAA Cost Accounting Domain (5 rules):**

| Rule ID | Name | Tier | What It Checks |
|---------|------|------|----------------|
| **L-08** | Direct/indirect consistency | Fast | Employee charging pattern vs historical classification |
| **L-11** | Provisional rate variance | Close | Actual indirect rates vs provisional billing rates |
| **C-01** | Unallowable costs | Close | FAR 31.205 unallowable costs in indirect pools |
| **C-02** | ICS submission timeliness | Close | Incurred cost submission filed within 6 months of FYE |
| **C-03** | Rate ceiling compliance | Close | Provisional rates within contractual ceilings |

**Data Quality (1 rule):**

| Rule ID | Name | Tier | What It Checks |
|---------|------|------|----------------|
| **DQ-01** | Employee roster completeness | Pay Period | All employee IDs in timekeeping exist in HRIS |

#### Rule Execution Model

```python
def execute_rule(rule: Rule, view: WorkingView, params: dict) -> RuleResult:
    """
    Pure function: no I/O, no side effects
    
    Inputs:
      - rule: Rule instance
      - view: Working view with lineage-tagged records
      - params: Resolved parameters (floor → default → customer)
    
    Process:
      1. Filter view to relevant records
      2. Compute metrics (always Decimal)
      3. Apply thresholds
      4. Generate findings with evidence
      5. Return RuleResult
    
    Output:
      - Result enum (CONSISTENT | EXCEPTION | WATCH | NOT_EVALUATED)
      - List of findings (empty if consistent)
      - Metrics dict (for trend tracking)
      - Message (if not evaluated, explain why)
    """
    pass
```

### 2. Configuration System

#### Three-Layer Resolution

```
┌────────────────────────────────────────────────────────────┐
│  LAYER 1: FLOORS (config/floors.yaml)                      │
│  - Regulatory minimums (cannot be loosened)                │
│  - Zero-tolerance thresholds                               │
│  - Example: L-01 threshold_hours = 0 (no unauthorized)     │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼ (stricter wins)
┌────────────────────────────────────────────────────────────┐
│  LAYER 2: REGISTRY DEFAULTS                                │
│  - Rule-defined defaults                                   │
│  - Example: L-05 late_threshold_hours = 48                 │
└────────────────────────────────────────────────────────────┘
                          │
                          ▼ (can be stricter)
┌────────────────────────────────────────────────────────────┐
│  LAYER 3: CUSTOMER CONFIG (meridian-rules.yaml)            │
│  - Customer-specific overrides                             │
│  - Example: L-05 late_threshold_hours = 24 (stricter)      │
│  - Contract-scoped overrides (future)                      │
└────────────────────────────────────────────────────────────┘
```

#### Parameter Strictness Enforcement

```python
class ParamSpec:
    name: str
    type: type  # Decimal | int | bool | str
    direction: Direction  # LOWER_IS_STRICTER | HIGHER_IS_STRICTER
    floor_value: Any
    default_value: Any

# Example: Late entry threshold
ParamSpec(
    name="late_threshold_hours",
    type=int,
    direction=Direction.LOWER_IS_STRICTER,
    floor_value=None,  # No regulatory floor
    default_value=48    # Registry default
)

# Customer tries to set 72 hours → REJECTED (looser than 48)
# Customer tries to set 24 hours → ACCEPTED (stricter than 48)
```

### 3. Status Rollup Engine

#### Four-Layer Rollup Logic

```python
def rollup(
    rule_results: list[RuleResult],
    applicable: list[RuleSpec],
    coverage_floor: Decimal = Decimal("0.85")
) -> StatusRollup:
    """
    Layer 1: Metric Results (internal to each rule)
    Layer 2: Rule Results → worst result from rule's metrics
    Layer 3: Requirement Results → worst result from rules mapped to same authority
    Layer 4: Domain/Overall Status → "Incomplete data" | "N open" | "No open"
    """
    
    # Calculate coverage (only rules with counts_toward_coverage=True)
    evaluated = [r for r in rule_results if r.result not in (NOT_EVALUATED, NOT_APPLICABLE)]
    coverage = len(evaluated) / len(applicable) if applicable else Decimal("0")
    
    # Determine status label
    if coverage < coverage_floor:
        status = "Incomplete data"
    elif any(r.result in (EXCEPTION, WATCH) for r in evaluated):
        open_count = sum(len(r.findings) for r in evaluated)
        status = f"{open_count} open finding{'s' if open_count != 1 else ''}"
    else:
        status = "No open findings"
    
    # M13: Deduplicate exposure across findings sharing evidence
    total_exposure = deduplicate_exposure([
        f for r in rule_results for f in r.findings
    ])
    
    return StatusRollup(
        status=status,
        coverage=coverage,
        total_exposure=total_exposure,
        findings_count=open_count,
        by_severity={...},
        by_domain={...}
    )
```

#### M13 Exposure Deduplication

```python
def deduplicate_exposure(findings: list[Finding]) -> Decimal:
    """
    Prevents double-counting when multiple rules flag same evidence.
    
    Example:
      L-03: Payroll/GL mismatch → $1,247.82 on employee E-0089
      L-08: E-0089 classification error → $850.00 on same pay record
      
      Without M13: $1,247.82 + $850.00 = $2,097.82 (WRONG)
      With M13: max($1,247.82, $850.00) = $1,247.82 (correct)
    
    Algorithm:
      1. Build evidence graph (findings → evidence refs)
      2. Find connected components (overlapping evidence)
      3. Take max exposure per component
      4. Sum component maxes
    """
    pass
```

### 4. Manifest & Replay System

#### Manifest Structure

```json
{
  "run_id": "run_20260922_151423",
  "period": "2026-08",
  "tier": "close",
  "executed_at": "2026-09-22T15:14:23.807Z",
  "sources": {
    "meridian_time_2026-08.csv": {
      "sha256": "a3f5d89b2c7e1f4a8b6d9e3c5a7f2b8d4e6c1a9f7b5d3e8c6a4f2b9d7e5c3a1f",
      "size_bytes": 245678,
      "row_count": 1847
    },
    "bamboo_payroll_2026-08.csv": {
      "sha256": "b7e2c41d9f8a3b5c7d2e4f6a8c1b9d3e5f7a2c4b6d8e1f3a5c7b9d2e4f6a8c1b",
      "size_bytes": 98432,
      "row_count": 324
    },
    "quickbooks_gl_2026-08.csv": {
      "sha256": "c8d3f52e1a9b4c6d8e3f5a7b9c2d4e6f8a1c3b5d7e9f2a4c6b8d1e3f5a7b9c2d",
      "size_bytes": 567890,
      "row_count": 4521
    },
    "adp_hris.csv": {
      "sha256": "d9e4f63a2b1c5d7e9f4a6b8c3d5e7f9a2c4b6d8e1f3a5c7b9d2e4f6a8c1b3d5e",
      "size_bytes": 45612,
      "row_count": 178
    },
    "contracts.json": {
      "sha256": "e1f5a74b3c2d6e8f1a5b7c9d4e6f8a1c3b5d7e9f2a4c6b8d1e3f5a7b9c2d4e6f",
      "size_bytes": 23456
    },
    "rate_data.json": {
      "sha256": "f2a6b85c4d3e7f9a2b6c8d1e5f7a9c2d4b6e8f1a3c5d7e9b2f4a6c8d1e3f5a7b",
      "size_bytes": 12345
    }
  },
  "config": {
    "version": 12,
    "hash": "d8a9f3e5c7b2d4f6a8e1c3b5d7f9a2c4e6b8d1f3a5c7b9e2d4f6a8c1b3d5e7f9a"
  },
  "tiers_executed": ["fast", "pay_period", "close"],
  "domains": ["labor", "dcaa_cost_accounting"]
}
```

#### Replay Verification

```python
def replay_run(run_id: str) -> ReplayResult:
    """
    Deterministic verification: re-run and compare results
    
    Steps:
      1. Load original manifest
      2. Verify current files match original hashes
      3. If match: re-run engine with same inputs
      4. Compare findings (fingerprints, exposure, severity)
      5. Return identical=True only if exact match
    
    Use cases:
      - Verify engine determinism (same input → same output)
      - Detect data tampering (hash mismatch)
      - Audit trail (prove results from specific inputs)
    """
    pass
```

---

## API Specification

### Endpoints

#### Status & Overview

```
GET /api/health
Response: { "ok": true }

GET /api/status?period=2026-08&as_of=2026-09-22
Response: {
  "status": "14 open findings",
  "label": "exception",
  "total_exposure": "102491.51",
  "coverage": "1.00",
  "by_severity": {
    "high": { "count": 8, "exposure": "89243.69" },
    "medium": { "count": 5, "exposure": "13147.82" },
    "low": { "count": 1, "exposure": "100.00" }
  },
  "domains": [
    {
      "id": "labor",
      "name": "Labor",
      "status": "8 open findings",
      "coverage": "1.00",
      "exposure": "41298.93",
      "rules_evaluated": 6,
      "rules_total": 6
    },
    {
      "id": "dcaa_cost_accounting",
      "name": "DCAA Cost Accounting",
      "status": "6 open findings",
      "coverage": "1.00",
      "exposure": "61192.58",
      "rules_evaluated": 5,
      "rules_total": 5
    }
  ],
  "tiers": [
    { "tier": "fast", "run_id": "run_xyz", "executed_at": "..." },
    { "tier": "pay_period", "run_id": "run_xyz", "executed_at": "..." },
    { "tier": "close", "run_id": "run_xyz", "executed_at": "..." }
  ]
}
```

#### Findings

```
GET /api/findings?severity=high&domain=labor&status=open
Response: {
  "total": 8,
  "items": [
    {
      "finding_id": "L03_2026-08_001",
      "rule_id": "L-03",
      "rule_name": "Payroll vs GL reconciliation",
      "severity": "high",
      "exposure": "1247.82",
      "headline": "Payroll and GL labor cost mismatch",
      "status": "open",
      "created_at": "2026-09-22T15:14:23Z",
      "run_id": "run_20260922_151423"
    },
    ...
  ]
}

GET /api/findings/{finding_id}
Response: {
  "finding_id": "L03_2026-08_001",
  "rule_id": "L-03",
  "severity": "high",
  "exposure": "1247.82",
  "headline": "Payroll and GL labor cost mismatch",
  "explanation": {
    "what": "Payroll reported $502,345.67 in gross wages...",
    "why": "FAR 31.201-2 requires that costs be adequately supported...",
    "impact": "$1,247.82 unreconciled variance between systems",
    "action": "Review GL coding for labor accounts..."
  },
  "authorities": ["FAR 31.201-2", "DCAA CAM 6-404.2"],
  "evidence_summary": {
    "total_rows": 1247,
    "sources": ["bamboo_payroll_2026-08.csv", "quickbooks_gl_2026-08.csv"]
  },
  "disposition": null,
  "status": "open"
}

GET /api/findings/{finding_id}/evidence?page=1&page_size=50
Response: {
  "total": 1247,
  "page": 1,
  "page_size": 50,
  "items": [
    {
      "source": "bamboo_payroll_2026-08.csv:142",
      "hash": "a3f5d89b...",
      "employee_id": "E-0089",
      "pay_period": "2026-08-A",
      "gross_pay": "4567.89"
    },
    ...
  ]
}

POST /api/findings/{finding_id}/disposition
Request: {
  "disposition": "legitimate_exception",
  "reason_code": "timing_difference",
  "note": "Payroll accrual vs cash basis GL",
  "actor": "jane.smith@company.com"
}
Response: { (updated finding) }
```

#### Rules & Configuration

```
GET /api/rules?domain=labor
Response: {
  "items": [
    {
      "rule_id": "L-01",
      "name": "Labor category compliance",
      "domain": "labor",
      "tier": "pay_period",
      "authorities": ["FAR 52.222-42", "Contract clause H.8"],
      "enabled": true,
      "review_status": "unreviewed"
    },
    ...
  ]
}

GET /api/config
Response: {
  "version": 12,
  "customer": "Meridian Systems",
  "effective_from": "2026-09-01",
  "domains": ["labor", "dcaa_cost_accounting"],
  "yaml": "config_version: 12\ncustomer: Meridian Systems\n..."
}

POST /api/config/validate
Request: { "yaml": "config_version: 13\n..." }
Response: {
  "accepted": false,
  "errors": [
    {
      "line": 42,
      "path": "rules.L-05.params.late_threshold_hours",
      "code": "too_loose",
      "message": "Value 72 is looser than default 48 (lower is stricter)"
    }
  ]
}

PUT /api/config
Request: { "yaml": "config_version: 13\n..." }
Response: { "accepted": true, "version": 13 }
```

#### Runs

```
GET /api/runs
Response: {
  "items": [
    {
      "run_id": "run_20260922_151423",
      "period": "2026-08",
      "tier": "close",
      "executed_at": "2026-09-22T15:14:23Z",
      "status": "completed",
      "findings_count": 14,
      "total_exposure": "102491.51"
    },
    ...
  ]
}

POST /api/runs
Request: { "period": "2026-08", "tier": "close" }
Response: {
  "run_id": "run_20260922_151423",
  "status": "completed",
  "findings_count": 14,
  "total_exposure": "102491.51"
}

GET /api/runs/{run_id}
Response: {
  "run_id": "run_20260922_151423",
  "period": "2026-08",
  "tier": "close",
  "executed_at": "2026-09-22T15:14:23Z",
  "manifest": { (full manifest JSON) },
  "result": { (rollup summary) }
}

POST /api/runs/{run_id}/replay
Response: {
  "identical": true,
  "original_run": "run_20260922_151423",
  "replay_run": "run_20260922_163045",
  "verified": {
    "sources": 6,
    "findings": 14,
    "exposure": "102491.51"
  }
}
```

---

## Deployment Architecture

### Render Free Tier Deployment

```
┌───────────────────────────────────────────────────────────────┐
│                        RENDER PLATFORM                         │
└───────────────────────────────────────────────────────────────┘

┌──────────────────────────────────┐  ┌───────────────────────┐
│   Frontend Service (sadhik-ui)   │  │  Backend Service      │
│                                  │  │  (sadhik-api)         │
│  Region: Ohio (US East)          │  │                       │
│  Runtime: Node 20.19             │  │  Region: Ohio         │
│  Port: Auto-assigned by Render   │  │  Runtime: Python 3.13 │
│                                  │  │  Port: Auto-assigned  │
│  Build Command:                  │  │                       │
│    cd app/web &&                 │  │  Build Command:       │
│    npm install &&                │  │    cd app &&          │
│    npm run build                 │  │    pip install -r     │
│                                  │  │      requirements.txt │
│  Start Command:                  │  │    python -m          │
│    npx serve -s dist -l $PORT    │  │      data.generate    │
│                                  │  │                       │
│  Environment:                    │  │  Start Command:       │
│    VITE_API_URL=                 │  │    uvicorn            │
│      https://sadhik-api.         │  │      api.main:app     │
│      onrender.com                │  │      --host 0.0.0.0   │
│                                  │  │      --port $PORT     │
│  Auto-Deploy: ✅ Enabled         │  │                       │
│  Health Check: /                 │  │  Environment:         │
│                                  │  │    SADHIK_DATA_DIR=   │
│  URL:                            │  │      data/out         │
│  https://sadhik-ui.onrender.com  │  │    SADHIK_DB=         │
│                                  │  │      sadhik.db        │
│                                  │  │                       │
│                                  │  │  Auto-Deploy: ✅      │
│                                  │  │  Health Check:        │
│                                  │  │    /api/health        │
│                                  │  │                       │
│                                  │  │  URL:                 │
│                                  │  │  https://sadhik-api.  │
│                                  │  │    onrender.com       │
└──────────────────────────────────┘  └───────────────────────┘
                 │                                  │
                 │                                  │
                 │    HTTPS API Calls               │
                 └──────────────────────────────────┘
                            │
                            │ CORS: *.onrender.com allowed
                            │
┌───────────────────────────────────────────────────────────────┐
│                    GITHUB REPOSITORY                           │
│              https://github.com/dev-mathur/sadhik-ai           │
│                                                                │
│  On push to main:                                             │
│    1. Webhook triggers Render                                 │
│    2. Both services redeploy automatically                    │
│    3. Zero-downtime deployment (free tier has brief pause)    │
└───────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────┐
│                    DATA PERSISTENCE                            │
│                                                                │
│  SQLite Database (ephemeral on free tier):                    │
│    - Stored in container filesystem                           │
│    - Resets on code deploys                                   │
│    - Data regenerated via data.generate on each build         │
│                                                                │
│  Source Data (data/out/):                                     │
│    - Generated during build from data.generate                │
│    - Deterministic: same bytes every time                     │
│    - 17 files: CSVs + JSON manifests                          │
└───────────────────────────────────────────────────────────────┘
```

### Free Tier Constraints

| Resource | Limit | Impact |
|----------|-------|--------|
| **Compute** | 0.1 CPU, 512 MB RAM | Sufficient for demo (10min runs for 500 employees) |
| **Bandwidth** | 100 GB/month | Adequate for moderate usage |
| **Sleep Policy** | After 15 min inactivity | 30s cold start on first request |
| **Persistent Disk** | Not available | Database resets on deploy (acceptable for demo) |
| **Build Time** | ~5 minutes | Acceptable for CI/CD |

---

## Summary

### System Characteristics

✅ **Deterministic**: Fixed-point math, sorted processing, manifest-based replay  
✅ **Lineage-driven**: Every finding traces to source file:row with hash  
✅ **Multi-domain**: Labor and DCAA cost accounting on shared engine  
✅ **Batch-oriented**: Daily/weekly runs, not real-time monitoring  
✅ **Configuration-driven**: Customer config validated against regulatory floors  
✅ **Testable**: 381 engine tests + 75 UI tests, ground truth verification  
✅ **Deployable**: Containerized FastAPI + React on Render free tier  

### Design Principles

1. **Explanation before evidence**: What/why/impact/action first, then data
2. **No certification language**: Reports inconsistencies, never certifies compliance
3. **Coverage-aware status**: "Incomplete data" blocks pass when rules can't run
4. **M13 deduplication**: Prevents double-counting shared evidence
5. **Compliance memory**: Dispositions suppress identical future findings

### Next Steps for Production

- [ ] Add authentication (OAuth2 + JWT)
- [ ] Multi-tenancy (customer isolation)
- [ ] Persistent storage (PostgreSQL)
- [ ] LLM integration (schema mapping, finding explanations)
- [ ] MCP server (agent access)
- [ ] Remaining catalog rules (L-04, L-07, L-10, L-12, L-13)
- [ ] CPA review of all authorities and floors
- [ ] Contract-scoped config overrides
- [ ] Disposition attachments
- [ ] Export to PDF/Excel

---

**Document Version:** 1.0  
**Last Updated:** September 22, 2026  
**Maintained By:** Sadhik AI Engineering Team
