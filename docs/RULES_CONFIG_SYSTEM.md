# Rules Configuration System - Deep Dive

## Overview

The Sadhik AI rules configuration system is a **three-layer parameter resolution engine** that enforces regulatory compliance while allowing customer customization. It prevents customers from loosening regulatory requirements while enabling them to set stricter internal policies.

---

## Table of Contents

1. [Configuration Architecture](#configuration-architecture)
2. [Three-Layer Resolution](#three-layer-resolution)
3. [Strictness Enforcement](#strictness-enforcement)
4. [Configuration Lifecycle](#configuration-lifecycle)
5. [Rule Triggering Process](#rule-triggering-process)
6. [Domain & Tier Management](#domain--tier-management)
7. [Validation Engine](#validation-engine)
8. [Examples](#examples)

---

## Configuration Architecture

### File Structure

```
app/config/
├── floors.yaml              # Sadhik-owned regulatory floors (immutable)
├── meridian-rules.yaml      # Customer configuration (validated)
└── mappings/                # Source system column mappings
    ├── meridian.yaml
    ├── bamboo.yaml
    ├── quickbooks.yaml
    └── adp.yaml
```

### Configuration Components

```
┌─────────────────────────────────────────────────────────────────────┐
│                    CONFIGURATION HIERARCHY                           │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 1: FLOORS (floors.yaml) - Sadhik-owned, immutable            │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  Rule: L-01 (Labor category compliance)                       │ │
│  │  Parameter: unapproved_category_tolerance                     │ │
│  │    - floor: 0 (zero tolerance)                                │ │
│  │    - direction: lower_is_stricter                             │ │
│  │    - basis: regulatory                                        │ │
│  │    - citation: "FAR 52.232-7"                                 │ │
│  │    - note: "Customer may NOT loosen this"                     │ │
│  └───────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  Rule: L-05 (Late entry patterns)                             │ │
│  │  Parameter: late_threshold_hours                              │ │
│  │    - floor: null (no regulatory floor)                        │ │
│  │    - default: 72 hours                                        │ │
│  │    - min: 24, max: 168                                        │ │
│  │    - direction: lower_is_stricter                             │ │
│  │    - basis: customer_policy                                   │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 2: REGISTRY DEFAULTS - Built into rule definitions           │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  Each rule defines its default parameters via ParamSpec:      │ │
│  │                                                                │ │
│  │  class L05_LateEntryPatterns(Rule):                           │ │
│  │    def param_spec(self) -> list[ParamSpec]:                   │ │
│  │      return [                                                  │ │
│  │        ParamSpec(                                              │ │
│  │          name="late_threshold_hours",                         │ │
│  │          type=int,                                            │ │
│  │          direction=Direction.LOWER_IS_STRICTER,               │ │
│  │          default_value=72  # Registry default                 │ │
│  │        )                                                       │ │
│  │      ]                                                         │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────────┐
│  LAYER 3: CUSTOMER CONFIG (meridian-rules.yaml) - Validated         │
│  ┌───────────────────────────────────────────────────────────────┐ │
│  │  config_version: 7                                            │ │
│  │  customer: meridian-systems                                   │ │
│  │  effective_from: 2026-09-01                                   │ │
│  │  changed_by: a.okafor                                         │ │
│  │  approved_by: r.delgado                                       │ │
│  │  reason: "Enabled DCAA cost accounting domain"                │ │
│  │                                                                │ │
│  │  domains:                                                      │ │
│  │    - labor                                                     │ │
│  │    - dcaa_cost_accounting                                     │ │
│  │                                                                │ │
│  │  defaults:                                                     │ │
│  │    materiality_pct: 0.005                                     │ │
│  │    coverage_floor: 0.85                                       │ │
│  │                                                                │ │
│  │  rules:                                                        │ │
│  │    L-05:                                                       │ │
│  │      late_threshold_hours: 48  # Stricter than default (72)   │ │
│  │    L-02:                                                       │ │
│  │      hours_variance_watch_pct: 0.75  # Stricter (default 1.0) │ │
│  └───────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Three-Layer Resolution

### Resolution Algorithm

```python
def resolve_parameter(rule_id: str, param_name: str) -> Decimal:
    """
    Resolve parameter value using three-layer precedence.
    
    Resolution order:
      1. Check floor (if present, this is the minimum strictness)
      2. Get registry default from rule definition
      3. Apply customer override (if stricter than default)
      4. Apply contract-scoped override (if stricter than customer)
    
    Returns: STRICTEST value among applicable layers
    """
    
    # Layer 1: Floor (regulatory minimum)
    floor_param = floors_registry.get(rule_id, param_name)
    floor_value = floor_param.floor if floor_param else None
    
    # Layer 2: Registry default
    rule_spec = get_rule_spec(rule_id)
    default_value = rule_spec.parameters[param_name].default_value
    
    # Layer 3: Customer override
    customer_value = customer_config.rules.get(rule_id, {}).get(param_name)
    
    # Validate customer value against floor
    if customer_value is not None and floor_value is not None:
        if is_looser(customer_value, floor_value, direction):
            raise ValidationError(f"Cannot loosen floor: {customer_value} > {floor_value}")
    
    # Resolve to strictest applicable value
    values = [v for v in [floor_value, default_value, customer_value] if v is not None]
    return strictest(values, direction=floor_param.direction)
```

### Example: late_threshold_hours Resolution

```
Rule: L-05 (Late entry patterns)
Parameter: late_threshold_hours
Direction: LOWER_IS_STRICTER (smaller number = stricter)

┌──────────────────────────────────────────────────────────────┐
│  Layer 1: Floor                                               │
│    Value: null (no regulatory requirement)                   │
│    Result: No constraint from regulatory floor               │
└──────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  Layer 2: Registry Default                                    │
│    Value: 72 hours                                           │
│    Result: Use 72 if customer doesn't override              │
└──────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌──────────────────────────────────────────────────────────────┐
│  Layer 3: Customer Config                                     │
│    Scenario A: Customer sets 48 hours                        │
│      → 48 < 72 (stricter) → ✅ ACCEPTED, use 48             │
│                                                               │
│    Scenario B: Customer sets 96 hours                        │
│      → 96 > 72 (looser) → ❌ REJECTED with error            │
│      → "Value 96 is looser than default 72                   │
│          (lower is stricter)"                                │
│                                                               │
│    Scenario C: Customer doesn't specify                      │
│      → Use default: 72 hours                                 │
└──────────────────────────────────────────────────────────────┘

Final Resolved Value: 48 hours (customer override accepted)
```

---

## Strictness Enforcement

### Direction Types

Every parameter has a `direction` that defines which way is stricter:

```python
class Direction(Enum):
    LOWER_IS_STRICTER = "lower_is_stricter"
    HIGHER_IS_STRICTER = "higher_is_stricter"
```

### Strictness Comparison Matrix

| Parameter Type | Direction | Floor | Default | Customer | Accepted? |
|----------------|-----------|-------|---------|----------|-----------|
| **late_threshold_hours** | LOWER_IS_STRICTER | null | 72 | 48 | ✅ Yes (48 < 72) |
| **late_threshold_hours** | LOWER_IS_STRICTER | null | 72 | 96 | ❌ No (96 > 72) |
| **coverage_floor** | HIGHER_IS_STRICTER | null | 0.85 | 0.90 | ✅ Yes (0.90 > 0.85) |
| **coverage_floor** | HIGHER_IS_STRICTER | null | 0.85 | 0.75 | ❌ No (0.75 < 0.85) |
| **unapproved_category_tolerance** | LOWER_IS_STRICTER | 0 | 0 | 0 | ✅ Yes (equal) |
| **unapproved_category_tolerance** | LOWER_IS_STRICTER | 0 | 0 | 1 | ❌ No (violates floor) |

### Zero-Tolerance Rules

Some rules have `floor: 0` which means **absolute zero tolerance**:

```yaml
# floors.yaml
rules:
  L-01:  # Labor category compliance
    unapproved_category_tolerance:
      floor: 0
      direction: lower_is_stricter
      basis: regulatory
      citation: "FAR 52.232-7"
      note: "Zero tolerance. Customer may NOT loosen."
  
  L-09:  # Period of performance
    out_of_pop_hours_tolerance:
      floor: 0
      direction: lower_is_stricter
      basis: contract
      citation: "FAR 31.201-4; FAR 52.216-7"
      note: "Any charge outside PoP is an Exception."
  
  C-01:  # Unallowable costs
    unallowable_tolerance_usd:
      floor: 0
      direction: lower_is_stricter
      basis: regulatory
      citation: "FAR 31.205"
      note: "Unallowable costs must not sit in indirect pools."
```

**Customers cannot:**
- Set these to any value other than 0
- Disable these rules (they're contractually required)
- Mark them as "not_applicable" without documented reason

---

## Configuration Lifecycle

### 1. Configuration Upload/Edit

```
┌────────────────────────────────────────────────────────────────┐
│  USER ACTION: Edit config in UI or upload YAML file            │
└────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  POST /api/config/validate                                      │
│  Request: { "yaml": "config_version: 8\n..." }                 │
└────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  VALIDATION ENGINE (engine/config.py)                           │
│                                                                 │
│  Phase 1: YAML Syntax                                          │
│    ✓ Parse YAML with line tracking                            │
│    ✓ Check for duplicate keys                                 │
│    ✓ Validate structure                                        │
│                                                                 │
│  Phase 2: Schema Validation                                    │
│    ✓ Required fields: config_version, customer, effective_from │
│    ✓ Valid domain names                                        │
│    ✓ Valid rule IDs                                            │
│    ✓ Valid parameter names                                     │
│                                                                 │
│  Phase 3: Strictness Validation                                │
│    ✓ Compare each parameter vs floor                          │
│    ✓ Compare each parameter vs default                        │
│    ✓ Check direction (lower/higher is stricter)               │
│    ✓ Reject if looser than allowed                            │
│                                                                 │
│  Phase 4: Business Rules                                       │
│    ✓ Zero-tolerance rules cannot be disabled                   │
│    ✓ Approved_by required if loosening any value              │
│    ✓ Reason required for all changes                          │
│    ✓ Domains must be recognized                               │
└────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  VALIDATION RESULT                                              │
│                                                                 │
│  If ACCEPTED:                                                  │
│    {                                                            │
│      "accepted": true,                                         │
│      "version": 8,                                             │
│      "warnings": []                                            │
│    }                                                            │
│                                                                 │
│  If REJECTED:                                                  │
│    {                                                            │
│      "accepted": false,                                        │
│      "errors": [                                               │
│        {                                                        │
│          "line": 42,                                           │
│          "path": "rules.L-05.params.late_threshold_hours",     │
│          "code": "too_loose",                                  │
│          "message": "Value 96 is looser than default 72        │
│                      (lower is stricter)"                      │
│        }                                                        │
│      ]                                                          │
│    }                                                            │
└────────────────────────────────────────────────────────────────┘
                        │
                        ▼ (if accepted)
┌────────────────────────────────────────────────────────────────┐
│  PUT /api/config                                                │
│  Request: { "yaml": "config_version: 8\n..." }                 │
└────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌────────────────────────────────────────────────────────────────┐
│  PERSISTENCE (api/service.py)                                   │
│    1. Calculate SHA-256 hash of YAML content                   │
│    2. Insert into config_versions table:                       │
│       - version: 8                                             │
│       - yaml_content: (full text)                              │
│       - created_at: 2026-09-22T23:31:19Z                       │
│       - created_by: a.okafor                                   │
│       - validation_result: (stored for audit)                  │
│       - is_active: true                                        │
│    3. Mark previous version as inactive                        │
└────────────────────────────────────────────────────────────────┘
```

### 2. Configuration Activation

```
┌────────────────────────────────────────────────────────────────┐
│  Config becomes active immediately after save                   │
│  - Next run uses new config                                    │
│  - Previous runs remain tied to their config version           │
│  - Replay uses original config version (not current)           │
└────────────────────────────────────────────────────────────────┘
```

---

## Rule Triggering Process

### Trigger Flow: From Config to Execution

```
┌─────────────────────────────────────────────────────────────────┐
│  USER CLICKS "RUN NOW"                                          │
│  - Selects period: "2026-08"                                   │
│  - Selects tier: "close"                                       │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  POST /api/runs                                                 │
│  Request: { "period": "2026-08", "tier": "close" }            │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  SERVICE LAYER: Load Active Config                             │
│                                                                 │
│  SELECT * FROM config_versions                                  │
│  WHERE is_active = true                                        │
│  ORDER BY version DESC LIMIT 1                                 │
│                                                                 │
│  Result:                                                        │
│    version: 7                                                  │
│    yaml_content: "config_version: 7\n..."                     │
│    domains: ["labor", "dcaa_cost_accounting"]                 │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  ENGINE RUNNER: Rule Selection (engine/runner.py)               │
│                                                                 │
│  Step 1: Load ALL rules from registry                          │
│    all_rules = [L-01, L-02, L-03, L-05, L-06, L-08, L-09,     │
│                 L-11, C-01, C-02, C-03, DQ-01]                 │
│                                                                 │
│  Step 2: Filter by ENABLED DOMAINS                             │
│    config.domains = ["labor", "dcaa_cost_accounting"]          │
│                                                                 │
│    domain_filter = [r for r in all_rules                       │
│                     if r.domain in config.domains]             │
│                                                                 │
│    Result: [L-01, L-02, L-03, L-05, L-06, L-08, L-09,         │
│             L-11, C-01, C-02, C-03, DQ-01]                     │
│                                                                 │
│    (No other domain rules are filtered out since we only       │
│     have labor and DCAA rules)                                 │
│                                                                 │
│  Step 3: Filter by TIER                                        │
│    tier = "close"                                              │
│    Tier hierarchy: close includes [fast, pay_period, close]    │
│                                                                 │
│    tier_filter = [r for r in domain_filter                     │
│                   if r.tier in ["fast", "pay_period", "close"]]│
│                                                                 │
│    Result: ALL 12 rules (close tier runs everything)           │
│                                                                 │
│  Step 4: Filter by ENABLED status                             │
│    For each rule:                                              │
│      if config.rules[rule_id].enabled == false:                │
│        skip rule                                               │
│      if config.rules[rule_id].status == "not_applicable":      │
│        skip rule (with documented reason)                      │
│                                                                 │
│    Default: all rules enabled unless explicitly disabled       │
│                                                                 │
│  Step 5: Check zero-tolerance rules                            │
│    Zero-tolerance rules CANNOT be disabled:                    │
│      - L-01 (unapproved labor categories)                     │
│      - L-09 (out of period of performance)                    │
│      - C-01 (unallowable costs)                               │
│                                                                 │
│    If customer tries: validation rejects config                │
│                                                                 │
│  FINAL RULE LIST: [L-01, L-02, L-03, L-05, L-06, L-08, L-09, │
│                     L-11, C-01, C-02, C-03, DQ-01]             │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  PARAMETER RESOLUTION (engine/config.py)                        │
│                                                                 │
│  For each selected rule:                                        │
│                                                                 │
│    Example: L-05 (Late entry patterns)                         │
│                                                                 │
│    Parameters needed:                                           │
│      - late_threshold_hours                                    │
│      - late_rate_watch_pct                                     │
│      - cluster_window_start_hour                               │
│      - cluster_index_watch                                     │
│      - cluster_index_exception                                 │
│      - cluster_min_group_entries                               │
│                                                                 │
│    Resolve late_threshold_hours:                               │
│      ┌──────────────────────────────────────────┐             │
│      │ Floor: null (no regulatory requirement)  │             │
│      │ Default: 72                               │             │
│      │ Customer: 48 (from meridian-rules.yaml)  │             │
│      │ Direction: LOWER_IS_STRICTER              │             │
│      │                                           │             │
│      │ Validation:                               │             │
│      │   48 < 72 → Stricter → ✅ Use 48         │             │
│      └──────────────────────────────────────────┘             │
│                                                                 │
│    Resolve late_rate_watch_pct:                                │
│      ┌──────────────────────────────────────────┐             │
│      │ Floor: null                               │             │
│      │ Default: 5.0                              │             │
│      │ Customer: (not specified)                 │             │
│      │ Direction: LOWER_IS_STRICTER              │             │
│      │                                           │             │
│      │ Resolution: Use default 5.0               │             │
│      └──────────────────────────────────────────┘             │
│                                                                 │
│    Resolved parameters for L-05:                               │
│      {                                                          │
│        "late_threshold_hours": 48,                             │
│        "late_rate_watch_pct": 5.0,                             │
│        "cluster_window_start_hour": 12,                        │
│        "cluster_index_watch": 1.5,                             │
│        "cluster_index_exception": 2.5,                         │
│        "cluster_min_group_entries": 30                         │
│      }                                                          │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  RULE EXECUTION (engine/runner.py)                              │
│                                                                 │
│  For each rule in deterministic order:                          │
│                                                                 │
│    1. Get resolved parameters                                  │
│    2. Call rule.check(working_view, params)                    │
│    3. Collect RuleResult                                       │
│                                                                 │
│  Example execution: L-05.check()                               │
│    Input:                                                       │
│      - working_view: (all canonical records with lineage)      │
│      - params: { "late_threshold_hours": 48, ... }            │
│                                                                 │
│    Logic:                                                       │
│      1. Filter time entries where:                             │
│         (submitted_at - work_date) > 48 hours                  │
│                                                                 │
│      2. Calculate metrics:                                     │
│         late_entries = 47 entries                              │
│         total_entries = 1847                                   │
│         late_rate = 47 / 1847 = 2.54%                          │
│                                                                 │
│      3. Compare to threshold:                                  │
│         late_rate_watch_pct = 5.0%                             │
│         2.54% < 5.0% → Below watch threshold                   │
│                                                                 │
│      4. Generate finding if threshold exceeded:                │
│         Result: CONSISTENT (no finding)                        │
│                                                                 │
│      5. Attach evidence with lineage:                          │
│         Each late entry references:                            │
│           - source: "meridian_time_2026-08.csv:1142"          │
│           - hash: "a3f5d89b..."                                │
│           - employee_id, work_date, submitted_at               │
│                                                                 │
│    Output: RuleResult                                          │
│      {                                                          │
│        "rule_id": "L-05",                                      │
│        "result": "CONSISTENT",                                 │
│        "findings": [],                                         │
│        "metrics": {                                            │
│          "late_entries": 47,                                   │
│          "late_rate_pct": 2.54                                 │
│        },                                                       │
│        "message": null                                         │
│      }                                                          │
└─────────────────────────────────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────────┐
│  ROLLUP & AGGREGATION (engine/rollup.py)                        │
│  (See main SYSTEM_DESIGN.md for 4-layer rollup details)        │
└─────────────────────────────────────────────────────────────────┘
```

### Tier-Based Rule Selection

```yaml
# Tier hierarchy (each includes all previous)
Tiers:
  - fast           # Quick checks (minutes)
  - pay_period     # Pay period validation (minutes)
  - close          # Full reconciliation (10 minutes)

Rule → Tier Mapping:
  Fast:
    - L-05 (Late entries)
    - L-06 (Post-submission edits)
    - L-08 (Direct/indirect classification)
    - L-09 (Period of performance)
  
  Pay Period:
    - L-01 (Labor category compliance)
    - L-02 (Timekeeping vs payroll hours)
    - DQ-01 (Employee roster completeness)
  
  Close:
    - L-03 (Payroll vs GL reconciliation)
    - L-11 (Provisional rate variance)
    - C-01 (Unallowable costs)
    - C-02 (ICS submission timeliness)
    - C-03 (Rate ceiling compliance)

Selection Logic:
  if tier == "fast":
    run_rules = [L-05, L-06, L-08, L-09]
  
  if tier == "pay_period":
    run_rules = [L-05, L-06, L-08, L-09,  # Fast tier
                 L-01, L-02, DQ-01]        # Pay Period tier
  
  if tier == "close":
    run_rules = ALL_RULES  # All tiers combined
```

---

## Domain & Tier Management

### Domain Configuration

```yaml
# Customer config: Enable specific compliance domains
domains:
  - labor                    # Labor compliance rules (L-01 through L-09)
  - dcaa_cost_accounting    # DCAA cost accounting rules (L-11, C-01, C-02, C-03)

# If a domain is NOT listed, its rules are not run at all
# (Not "Not evaluated" - they're simply not selected)
```

### Domain Impact Matrix

| Domains Enabled | Rules Run | Coverage Calculation |
|-----------------|-----------|---------------------|
| `- labor` only | L-01, L-02, L-03, L-05, L-06, L-09, DQ-01 | 6/6 labor rules |
| `- dcaa_cost_accounting` only | L-08, L-11, C-01, C-02, C-03 | 5/5 DCAA rules |
| Both | All 12 rules | 11/11 (DQ-01 doesn't count toward coverage) |
| Neither | ERROR: At least one domain required | N/A |

### Per-Domain Status

```json
{
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
  ]
}
```

**Important**: "Incomplete data" in ANY domain forces overall status to "Incomplete data", even if other domains are clean.

---

## Validation Engine

### Validation Phases

```python
def validate_config(yaml_text: str, floors: FloorRegistry) -> ConfigResult:
    """
    Multi-phase validation with line-number error reporting.
    
    Phase 1: YAML Syntax
      - Parse YAML safely
      - Track line numbers for all keys
      - Detect duplicate keys
      - Return early on syntax errors
    
    Phase 2: Schema Validation
      - Required fields present
      - Valid data types
      - Recognized domain names
      - Valid rule IDs (must exist in registry)
      - Valid parameter names (must exist in rule spec)
    
    Phase 3: Strictness Validation
      - For each parameter override:
        a. Get floor (if exists)
        b. Get default from rule spec
        c. Compare customer value vs floor (reject if looser)
        d. Compare customer value vs default (reject if looser)
        e. Check direction (lower/higher is stricter)
    
    Phase 4: Business Rules
      - Zero-tolerance rules cannot be disabled
      - approved_by required if loosening any value
      - reason required for all changes
      - effective_from cannot be in the past
      - config_version must increment
    
    Returns:
      ConfigResult(
        accepted: bool,
        errors: list[ConfigError],  # With line numbers
        warnings: list[str],
        config: CustomerConfig | None,
        sha256: str
      )
    """
    pass
```

### Error Reporting with Line Numbers

```python
# Example validation error
ConfigError(
    line=42,
    path="rules.L-05.params.late_threshold_hours",
    code="too_loose",
    message="Value 96 is looser than default 72 (lower is stricter)"
)

# UI displays:
"""
Line 42: rules.L-05.params.late_threshold_hours
  Value 96 is looser than default 72 (lower is stricter)
  
  Current value: 96
  Default value: 72
  Direction: LOWER_IS_STRICTER
  
  To fix: Set a value ≤ 72, or remove this line to use the default.
"""
```

---

## Examples

### Example 1: Making a Rule Stricter (Accepted)

```yaml
# Customer wants to catch late entries sooner
rules:
  L-05:
    late_threshold_hours: 24  # Default is 72
```

**Validation:**
- Floor: null (no regulatory requirement)
- Default: 72 hours
- Customer: 24 hours
- Direction: LOWER_IS_STRICTER
- Comparison: 24 < 72 → **Stricter** → ✅ **ACCEPTED**

**Effect:**
- L-05 will flag entries submitted >24 hours after work date
- More findings will be raised (earlier detection)
- Exposure may increase (catching more issues)

---

### Example 2: Trying to Loosen a Rule (Rejected)

```yaml
# Customer tries to extend the late entry window
rules:
  L-05:
    late_threshold_hours: 120  # Default is 72
```

**Validation:**
- Floor: null
- Default: 72 hours
- Customer: 120 hours
- Direction: LOWER_IS_STRICTER
- Comparison: 120 > 72 → **Looser** → ❌ **REJECTED**

**Error Message:**
```
Line 42: rules.L-05.params.late_threshold_hours
  Value 120 is looser than default 72 (lower is stricter)
```

---

### Example 3: Attempting to Disable Zero-Tolerance Rule (Rejected)

```yaml
# Customer tries to disable labor category check
rules:
  L-01:
    enabled: false
```

**Validation:**
- L-01 has floor: 0 (zero tolerance)
- Basis: regulatory (FAR 52.232-7)
- Zero-tolerance rules cannot be disabled
- Result: ❌ **REJECTED**

**Error Message:**
```
Line 38: rules.L-01.enabled
  Rule L-01 (Labor category compliance) cannot be disabled.
  It has a regulatory floor (FAR 52.232-7) and is zero-tolerance.
```

---

### Example 4: Marking Rule as Not Applicable (Accepted with Reason)

```yaml
# Customer has no cost-plus contracts
rules:
  C-03:
    status: not_applicable
    reason: "Company only has FFP contracts; no provisional rates"
```

**Validation:**
- Not the same as `enabled: false`
- Requires documented reason
- Rule shows as "Not Applicable" in status
- Does not count toward coverage
- Result: ✅ **ACCEPTED**

---

### Example 5: Enabling a New Domain

```yaml
# Customer adds DCAA cost accounting domain
config_version: 8
domains:
  - labor
  - dcaa_cost_accounting  # NEW
```

**Effect:**
- Next run will execute DCAA rules: L-08, L-11, C-01, C-02, C-03
- Coverage denominator increases from 6 to 11
- New findings may appear
- Overall status may change to "Incomplete data" if DCAA coverage < 0.85

---

### Example 6: Contract-Scoped Override (Future Feature)

```yaml
# Stricter threshold for a specific contract
rules:
  L-05:
    late_threshold_hours: 48  # Global default
    scope:
      contract:
        "CTR-2024-001":  # NASA contract
          late_threshold_hours: 24  # Stricter for this contract
```

**Resolution:**
- When evaluating entries charged to CTR-2024-001:
  - Use 24 hours (contract-specific)
- When evaluating entries charged to other contracts:
  - Use 48 hours (global customer setting)
- Strictest applicable value wins

---

## Configuration API Usage

### Validate Config

```bash
POST /api/config/validate
Content-Type: application/json

{
  "yaml": "config_version: 8\ncustomer: meridian-systems\n..."
}
```

**Response (Accepted):**
```json
{
  "accepted": true,
  "errors": [],
  "warnings": [
    "Parameter 'late_threshold_hours' is stricter than default (24 vs 72)"
  ],
  "sha256": "d8a9f3e5c7b2d4f6a8e1c3b5d7f9a2c4"
}
```

**Response (Rejected):**
```json
{
  "accepted": false,
  "errors": [
    {
      "line": 42,
      "path": "rules.L-05.params.late_threshold_hours",
      "code": "too_loose",
      "message": "Value 96 is looser than default 72 (lower is stricter)"
    },
    {
      "line": 38,
      "path": "rules.L-01.enabled",
      "code": "cannot_disable",
      "message": "Rule L-01 cannot be disabled (zero tolerance)"
    }
  ],
  "warnings": []
}
```

### Save Config

```bash
PUT /api/config
Content-Type: application/json

{
  "yaml": "config_version: 8\ncustomer: meridian-systems\n..."
}
```

**Response:**
```json
{
  "accepted": true,
  "version": 8,
  "effective_from": "2026-09-22",
  "sha256": "d8a9f3e5c7b2d4f6a8e1c3b5d7f9a2c4"
}
```

---

## Summary

### Key Principles

1. **Three-Layer Resolution**: Floor → Default → Customer (strictest wins)
2. **Strictness Enforcement**: Customers can only make rules stricter, never looser
3. **Zero-Tolerance Protection**: Regulatory requirements cannot be disabled or loosened
4. **Line-Level Validation**: Errors reported with exact line numbers in YAML
5. **Domain-Based Activation**: Rules only run if their domain is enabled
6. **Tier-Based Execution**: Higher tiers include all lower tiers
7. **Audit Trail**: Every config version stored with SHA-256 hash
8. **Deterministic Replay**: Runs tied to specific config version

### Configuration Controls What Runs

```
Config → Domains → Rules → Parameters → Execution → Findings
   ↓
- Which domains are active?
- Which rules are enabled?
- What are the thresholds?
- What is materiality?
- What is coverage floor?
   ↓
Only configured domains' rules execute
   ↓
Only enabled rules run
   ↓
Parameters resolved via 3-layer system
   ↓
Findings generated per resolved thresholds
   ↓
Status rolled up per coverage floor
```

---

**Document Version:** 1.0  
**Last Updated:** September 22, 2026  
**Maintained By:** Sadhik AI Engineering Team
