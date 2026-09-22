# Code-Level Calculation Flow - Sadhik AI

## Overview

This document maps the **exact code locations** where calculations happen and traces how rule execution is triggered from user action to final results.

---

## Table of Contents

1. [Complete Execution Flow](#complete-execution-flow)
2. [Entry Point: User Clicks "Run Now"](#entry-point-user-clicks-run-now)
3. [Rule Registration & Discovery](#rule-registration--discovery)
4. [Rule Execution: The evaluate() Function](#rule-execution-the-evaluate-function)
5. [Calculation Examples by Rule](#calculation-examples-by-rule)
6. [Helper Functions & Utilities](#helper-functions--utilities)
7. [Result Aggregation](#result-aggregation)

---

## Complete Execution Flow

```
USER ACTION                    FILE                           FUNCTION
    │
    ├─> Click "Run Now"        web/src/pages/StatusPage.tsx  handleRunNow()
    │
    ├─> POST /api/runs         web/src/api/client.ts         api.createRun()
    │
    ├─> FastAPI endpoint       api/main.py                   run_create()
    │
    ├─> Service layer          api/service.py                create_run()
    │   ├─> Load config        api/service.py                _active_config()
    │   ├─> Call engine        engine/runner.py              run()
    │   │   │
    │   │   ├─> Ingest         engine/ingest.py              load_working_view()
    │   │   │   ├─> Read CSV   engine/ingest.py              _load_csv()
    │   │   │   ├─> Hash files engine/manifest.py            canonical_hash()
    │   │   │   └─> Build view engine/ingest.py              WorkingView()
    │   │   │
    │   │   ├─> Select rules   engine/runner.py              _filter_rules()
    │   │   │   ├─> By domain
    │   │   │   ├─> By tier
    │   │   │   └─> By enabled
    │   │   │
    │   │   ├─> For each rule:
    │   │   │   ├─> Resolve    engine/config.py              resolve_params()
    │   │   │   │   params
    │   │   │   │
    │   │   │   └─> Execute    engine/rules/{rule}.py        evaluate()
    │   │   │       rule       ↓
    │   │   │                  CALCULATIONS HAPPEN HERE
    │   │   │                  ↓
    │   │   │                  - Filter data from WorkingView
    │   │   │                  - Compute metrics (Decimal math)
    │   │   │                  - Compare to thresholds
    │   │   │                  - Generate findings
    │   │   │                  - Collect evidence with lineage
    │   │   │                  - Return RuleResult
    │   │   │
    │   │   ├─> Rollup         engine/rollup.py              rollup()
    │   │   │   ├─> M13        engine/metrics.py             deduplicate_exposure()
    │   │   │   ├─> 4-layer    engine/rollup.py              _worst()
    │   │   │   └─> Coverage   engine/rollup.py              coverage calculation
    │   │   │
    │   │   └─> Return         engine/runner.py              RunOutput
    │   │
    │   └─> Persist            api/service.py                _save_run()
    │       ├─> Insert run     api/db.py                     insert_run()
    │       └─> Insert finds   api/db.py                     insert_findings()
    │
    └─> Response               api/main.py                   JSON response
```

---

## Entry Point: User Clicks "Run Now"

### Frontend: StatusPage.tsx

```typescript
// File: app/web/src/pages/StatusPage.tsx
async function handleRunNow() {
  setRunning(true);
  try {
    // Call API to create run
    const result = await api.createRun(period, tier);
    
    // Refresh status after run completes
    await loadStatus();
    
    navigate(`/runs/${result.run_id}`);
  } catch (err) {
    setError(err.message);
  } finally {
    setRunning(false);
  }
}
```

### API Client

```typescript
// File: app/web/src/api/client.ts
export const api = {
  createRun: (period: string, tier: string) => 
    call<RunCreated>('POST', '/api/runs', { period, tier }),
};
```

### FastAPI Endpoint

```python
# File: app/api/main.py
@app.post("/api/runs")
def run_create(body: RunBody) -> dict[str, Any]:
    """
    Create and execute a new run.
    
    Args:
        body.period: e.g. "2026-08"
        body.tier: "fast" | "pay_period" | "close"
    
    Returns:
        { "run_id": "...", "status": "completed", ... }
    """
    return svc().create_run(body.period, body.tier)
```

### Service Layer

```python
# File: app/api/service.py (line ~450)
def create_run(self, period: str, tier: str) -> dict[str, Any]:
    """
    Orchestrate a full run: validate, execute engine, persist, rollup.
    """
    # 1. Load active config
    config_yaml, config_version = self._active_config()
    
    # 2. Call engine
    from engine.runner import run
    output = run(
        data_dir=self.data_dir,
        period=period,
        tier=tier,
        config_yaml=config_yaml,
        executed_at=self.clock.now(),
        floors_path=self.floors_path
    )
    
    # 3. Persist results
    run_id = self._save_run(output, config_version)
    
    # 4. Return summary
    return {
        "run_id": run_id,
        "status": "completed",
        "findings_count": len(output.findings),
        "total_exposure": str(output.total_exposure)
    }
```

---

## Rule Registration & Discovery

### How Rules are Registered

Every rule file (e.g., `l03.py`, `l05.py`) registers itself at import time:

```python
# File: app/engine/rules/l03.py (last lines)
SPEC = register(
    RuleSpec(
        id="L-03",
        version="1.0.0",
        title="Payroll vs. general ledger labor dollars",
        authorities=("FAR 31.201-2", "SF 1408 labor distribution"),
        basis="audit_practice",
        required_sources=("payroll", "gl"),
        parameters={
            "dollar_variance_watch_pct": ParamSpec(...),
            "dollar_variance_exception_pct": ParamSpec(...),
        },
        evaluate=evaluate,  # <-- THE CALCULATION FUNCTION
        explanation_template=EXPLANATION_TEMPLATE,
        recommended_action=RECOMMENDED_ACTION,
        review_status="unreviewed",
    )
)
```

### Rule Registry

```python
# File: app/engine/rules/base.py (lines 240-270)
_REGISTRY: dict[str, RuleSpec] = {}

def register(spec: RuleSpec) -> RuleSpec:
    """Register a rule at import time."""
    if spec.id in _REGISTRY:
        raise ValueError(f"duplicate rule id {spec.id}")
    _REGISTRY[spec.id] = spec
    return spec

def all_rules() -> list[RuleSpec]:
    """Load all rule modules and return sorted list."""
    _load_rule_modules()  # Import l*.py files
    return sorted(_REGISTRY.values(), key=lambda r: r.id)

def _load_rule_modules() -> None:
    """
    Auto-discover and import all rule modules.
    Finds: l01.py, l02.py, l03.py, ..., c01.py, etc.
    """
    import importlib
    import pkgutil
    import engine.rules as pkg
    
    for m in pkgutil.iter_modules(pkg.__path__):
        if m.name == "base":
            continue
        importlib.import_module(f"engine.rules.{m.name}")
```

---

## Rule Execution: The evaluate() Function

### Engine Runner: Rule Selection & Execution

```python
# File: app/engine/runner.py (lines 80-180)
def run(
    data_dir: Path,
    period: str,
    tier: str,
    config_yaml: str,
    executed_at: datetime,
    floors_path: Path | None = None
) -> RunOutput:
    """
    Main engine entry point.
    Pure function: same inputs → same outputs.
    """
    # 1. Load floors registry
    floors = load_floors(floors_path or FLOORS_PATH)
    
    # 2. Validate config
    config_result = validate_config(config_yaml, floors)
    if not config_result.accepted:
        raise RunBlocked("invalid_config", config_result.errors)
    config = config_result.config
    
    # 3. Ingest data → WorkingView
    view = load_working_view(data_dir, period, tier)
    
    # 4. Select rules to run
    all_specs = all_rules()  # Load from registry
    applicable = [
        s for s in all_specs
        if s.domain in config.domains          # Domain filter
        and TIER_ORDER[s.tier] <= TIER_ORDER[tier]  # Tier filter
        and _is_enabled(config, s.id)          # Enabled filter
    ]
    
    # 5. Execute each rule
    rule_results: list[RuleResult] = []
    for spec in applicable:
        # Resolve parameters (floor → default → customer)
        params = resolve_params(spec, config, floors)
        
        # CALL THE RULE'S evaluate() FUNCTION
        result = spec.evaluate(view, params)
        
        rule_results.append(result)
    
    # 6. Rollup results
    status = rollup(
        rule_results,
        applicable,
        coverage_floor=config.defaults.get("coverage_floor", Decimal("0.85"))
    )
    
    # 7. Build manifest for replay
    manifest = build_manifest(data_dir, period, tier, config_result.sha256)
    
    return RunOutput(
        period=period,
        tier=tier,
        executed_at=executed_at,
        manifest=manifest,
        rule_results=rule_results,
        status=status,
        findings=[f for r in rule_results for f in r.findings],
        total_exposure=status.total_exposure
    )
```

### Parameter Resolution

```python
# File: app/engine/config.py (lines 400-450)
def resolve_params(
    spec: RuleSpec,
    config: CustomerConfig,
    floors: FloorRegistry
) -> ResolvedParams:
    """
    Three-layer resolution: floor → default → customer.
    Returns flat dict of parameter name → Decimal value.
    """
    params: dict[str, Decimal] = {}
    
    for param_name, param_spec in spec.parameters.items():
        # Layer 1: Floor (if present)
        floor_value = None
        if floors.has_param(spec.id, param_name):
            floor_param = floors.param(spec.id, param_name)
            floor_value = floor_param.floor
        
        # Layer 2: Default from rule spec
        default_value = param_spec.default
        
        # Layer 3: Customer override
        customer_value = None
        if spec.id in config.rules:
            rule_config = config.rules[spec.id]
            customer_value = rule_config.get(param_name)
        
        # Validate customer value against floor
        if customer_value is not None and floor_value is not None:
            if is_looser(customer_value, floor_value, param_spec.direction):
                raise ValidationError(
                    f"Cannot loosen floor: {customer_value} > {floor_value}"
                )
        
        # Resolve to strictest applicable value
        if customer_value is not None:
            params[param_name] = customer_value
        else:
            params[param_name] = default_value
    
    # Add global defaults (materiality, coverage floor)
    params["materiality_usd"] = config.defaults.get(
        "materiality_pct", Decimal("0.005")
    ) * _period_labor_cost(view)
    
    return params
```

---

## Calculation Examples by Rule

### Example 1: L-03 (Payroll vs GL Reconciliation)

**Location:** `app/engine/rules/l03.py`

```python
def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    """
    CALCULATION: Compare payroll gross pay to GL labor accounts.
    
    Metric: M2 = |GL_labor - Payroll| / Payroll
    """
    # 1. Check required sources present
    missing = [s for s in REQUIRED if s not in view.sources_present]
    if missing:
        return not_evaluated(RULE_ID, f"Required source not present: {', '.join(missing)}")
    
    # 2. Get resolved parameters
    watch_pct = p["dollar_variance_watch_pct"]      # e.g., 0.5%
    exc_pct = p["dollar_variance_exception_pct"]    # e.g., 2.0%
    materiality = p["materiality_usd"]              # e.g., $2,500
    
    # 3. Filter data from WorkingView
    # Pay records for this period
    pay = [r for r in view.pay_records 
           if r.pay_period.startswith(view.period)]
    
    # GL lines for this period (labor accounts only)
    gl = [g for g in view.gl_lines 
          if g.period == view.period 
          and is_labor_line(view, g.account)]
    
    # 4. CORE CALCULATION: Sum totals using Decimal arithmetic
    payroll_total = sum((r.gross_pay for r in pay), Decimal("0"))
    gl_total = sum((g.amount for g in gl), Decimal("0"))
    
    # Check for no data
    if payroll_total == 0 and gl_total == 0:
        return not_evaluated(RULE_ID, "No payroll or GL labor amounts found")
    
    # 5. CALCULATE VARIANCE
    diff = abs(gl_total - payroll_total)
    
    # 6. COMPARE TO THRESHOLDS
    result = Result.CONSISTENT
    if diff * Decimal("100") > exc_pct * payroll_total:
        result = Result.EXCEPTION
    elif diff * Decimal("100") > watch_pct * payroll_total:
        result = Result.WATCH
    
    # 7. CALCULATE METRIC VALUE (ratio)
    value = ratio(diff, payroll_total) if payroll_total else Decimal("1.0000")
    # ratio() uses fixed-point division: (diff / payroll).quantize(0.0001)
    
    # 8. Build metric result
    metric = MetricResult(
        metric_id="M2",
        label="Payroll vs GL dollar variance",
        value=value,
        numerator=money(diff),        # Round to cents
        denominator=money(payroll_total),
        result=result,
        watch_threshold=f"> {watch_pct}%",
        exception_threshold=f"> {exc_pct}%",
    )
    
    # 9. If consistent, return early (no finding)
    if result == Result.CONSISTENT:
        return RuleResult(
            rule_id=RULE_ID, 
            result=result, 
            metrics=(metric,)
        )
    
    # 10. GENERATE FINDING
    # Check trend history
    history = baseline_history(view, "M2.dollar_variance")
    rising = has_baseline(history) and is_rising(value, history)
    
    # Calculate exposure (the dollar difference)
    exposure = money(diff)
    
    # Classify severity
    sev, reason = classify_with_reason(
        result,
        exposure=exposure,
        materiality=materiality,
        employees=len({r.employee_id for r in pay}),
        trend_rising=rising,
        no_trend_history=not has_baseline(history),
    )
    
    # 11. COLLECT EVIDENCE with lineage
    evidence = [
        EvidenceRef(
            kind="gl_line",
            ref_id=f"{g.account}/{g.project}",
            source_file=g.lineage.source_file,  # e.g., "quickbooks_gl_2026-08.csv"
            sha256=g.lineage.sha256,
            row=g.lineage.row,                  # e.g., 1247
            detail={
                "account": g.account,
                "project": g.project,
                "amount": str(g.amount),
                "cost_type": g.cost_type,
                "pool": g.pool,
            },
        )
        for g in gl
    ] + [
        EvidenceRef(
            kind="pay_record",
            ref_id=f"{r.employee_id}/{r.pay_period}",
            source_file=r.lineage.source_file,  # e.g., "bamboo_payroll_2026-08.csv"
            sha256=r.lineage.sha256,
            row=r.lineage.row,                  # e.g., 142
            detail={
                "pay_period": r.pay_period,
                "gross_pay": str(r.gross_pay)
            },
        )
        for r in sorted(pay, key=lambda r: (r.employee_id, r.pay_period))
    ]
    
    # 12. CREATE FINDING
    finding = Finding(
        rule_id=RULE_ID,
        rule_version="1.0.0",
        period=view.period,
        authorities=("FAR 31.201-2", "SF 1408 labor distribution"),
        basis="audit_practice",
        severity=sev,
        severity_reason=reason,
        headline=f"General-ledger labor differs from payroll gross pay by {fmt_money(diff)} ({fmt_pct(value, 2)})",
        metric_id="M2",
        metric_value=value,
        metric_numerator=money(diff),
        metric_denominator=money(payroll_total),
        threshold_tripped=(
            "dollar_variance_exception_pct" if result == Result.EXCEPTION 
            else "dollar_variance_watch_pct"
        ),
        computed={
            "kind": "primary",
            "payroll_total": money(payroll_total),
            "gl_total": money(gl_total),
            "variance": exposure,
            "variance_ratio": value,
            "exposure_usd": exposure,
            "exposure_basis": "dollar_difference",
            "baseline_confidence": "ok" if has_baseline(history) else "low",
        },
        exposure_usd=exposure,
        exposure_entry_ids=(),  # Not entry-based
        employees=(),
        contracts=(),
        evidence=tuple(evidence),
        fingerprint=f"L-03|{view.period}",  # For deduplication
    )
    
    # 13. RETURN RESULT
    return RuleResult(
        rule_id=RULE_ID,
        result=result,
        metrics=(metric,),
        findings=(finding,)
    )
```

**Calculation Flow for L-03:**

```
Input Data:
  view.pay_records = [
    PayRecord(employee_id="E-0001", pay_period="2026-08-A", gross_pay=4567.89, ...),
    PayRecord(employee_id="E-0002", pay_period="2026-08-A", gross_pay=5234.12, ...),
    ... (324 records total)
  ]
  view.gl_lines = [
    GLLine(account="5100-LABOR", period="2026-08", amount=502345.67, ...),
    GLLine(account="5110-BENEFITS", period="2026-08", amount=75234.89, ...),
    ... (4521 records total)
  ]

Parameters (resolved):
  watch_pct = 0.5
  exc_pct = 2.0
  materiality = 2511.73

Calculation Steps:
  1. Sum payroll: 502345.67
  2. Sum GL labor: 503593.49
  3. diff = |503593.49 - 502345.67| = 1247.82
  4. Check threshold:
     diff * 100 = 124782
     exc_pct * payroll = 2.0 * 502345.67 = 1004691.34
     watch_pct * payroll = 0.5 * 502345.67 = 251172.84
     
     124782 < 251172.84 → Result = CONSISTENT
     
     (In a different scenario where diff was larger,
      it would be WATCH or EXCEPTION)
  
  5. value = 1247.82 / 502345.67 = 0.0025 (0.25%)

Result:
  RuleResult(
    rule_id="L-03",
    result=CONSISTENT,
    metrics=[
      MetricResult(
        metric_id="M2",
        value=0.0025,
        numerator=1247.82,
        denominator=502345.67,
        result=CONSISTENT
      )
    ],
    findings=[]  # Empty because result is CONSISTENT
  )
```

---

### Example 2: L-05 (Late Entry Patterns)

**Location:** `app/engine/rules/l05.py`

```python
def evaluate(view: WorkingView, p: ResolvedParams) -> RuleResult:
    """
    TWO CALCULATIONS:
    1. M4: Late entry rate
    2. M5: Entry-time clustering per contract
    """
    # Get parameters
    late_hours = p["late_threshold_hours"]          # e.g., 48
    late_watch_pct = p["late_rate_watch_pct"]       # e.g., 5.0
    start_hour = p["cluster_window_start_hour"]     # e.g., 12 (noon)
    idx_watch = p["cluster_index_watch"]            # e.g., 1.5
    idx_exc = p["cluster_index_exception"]          # e.g., 2.5
    min_group = p["cluster_min_group_entries"]      # e.g., 30
    materiality = p["materiality_usd"]
    
    entries = sorted(view.time_entries, key=lambda e: e.entry_id)
    total = len(entries)
    
    findings: list[Finding] = []
    metrics: list[MetricResult] = []
    
    # ===== M4: LATE ENTRY RATE ===== #
    
    # 1. FILTER: Find entries created >48h after work date
    late = [e for e in entries if lag_hours(e) > late_hours]
    # lag_hours(e) = (e.entered_at - e.work_date).total_seconds() / 3600
    
    # 2. CALCULATE RATE
    m4_value = ratio(len(late), total)  # e.g., 47 / 1847 = 0.0254
    
    # 3. COMPARE TO THRESHOLD
    m4_result = (
        Result.WATCH 
        if Decimal(len(late)) * Decimal("100") > late_watch_pct * total
        else Result.CONSISTENT
    )
    # Example: 47 * 100 = 4700
    #          5.0 * 1847 = 9235
    #          4700 < 9235 → CONSISTENT
    
    metrics.append(
        MetricResult(
            metric_id="M4",
            label="Late-entry rate",
            value=m4_value,
            numerator=Decimal(len(late)),
            denominator=Decimal(total),
            result=m4_result,
            watch_threshold=f"> {late_watch_pct}% of entries created > {late_hours} h after work date",
        )
    )
    
    # If Watch, generate finding with evidence
    if m4_result == Result.WATCH:
        # Calculate exposure: sum(hours * loaded_rate) for late entries
        exposure = money(
            sum((e.hours * view.loaded_rate(e.employee_id) for e in late), 
                Decimal("0"))
        )
        
        findings.append(Finding(...))  # Create finding
    
    # ===== M5: CLUSTERING PER CONTRACT ===== #
    
    # 1. CALCULATE COMPANY-WIDE BASELINE
    window_all = [e for e in entries if _in_window(e, start_hour)]
    # _in_window checks: e.entered_at.weekday() == 4 (Friday)
    #                    AND hour >= start_hour (e.g., noon)
    
    company_share = ratio(len(window_all), total, "0.000001")
    
    # 2. GET HISTORICAL BASELINE
    history = baseline_history(view, "M5.company_window_share")
    # history = [0.0823, 0.0791, 0.0847, 0.0812, 0.0836, 0.0799]
    # (last 6 periods from view.baselines)
    
    if has_baseline(history):
        baseline = baseline_mean(history)  # Mean of 6 values
        # baseline = (0.0823 + 0.0791 + ... + 0.0799) / 6 = 0.0818
    else:
        baseline = company_share  # Fallback to current
    
    # 3. GROUP ENTRIES BY CONTRACT
    groups: dict[str, list[TimeEntry]] = {}
    for e in entries:
        m = view.charge_codes.get(e.charge_code)
        if m and m.contract_id:
            groups.setdefault(m.contract_id, []).append(e)
    
    # 4. FOR EACH CONTRACT GROUP, CALCULATE INDEX
    for contract_id in sorted(groups):
        g = groups[contract_id]  # All entries for this contract
        
        # Skip small groups
        if len(g) < min_group:
            continue
        
        # Find Friday afternoon entries in this group
        flagged = [e for e in g if _in_window(e, start_hour)]
        
        # CALCULATE GROUP'S WINDOW SHARE
        share = Decimal(len(flagged)) / Decimal(len(g))
        # Example: 45 / 234 = 0.1923
        
        # CALCULATE CLUSTER INDEX
        index = ratio(share, baseline, "0.0001")
        # Example: 0.1923 / 0.0818 = 2.3509
        
        # COMPARE TO THRESHOLDS
        if index > idx_exc:           # > 2.5
            result = Result.EXCEPTION
        elif index > idx_watch:       # > 1.5
            result = Result.WATCH
        else:
            result = Result.CONSISTENT
        
        metrics.append(
            MetricResult(
                metric_id=f"M5.{contract_id}",
                label=f"Cluster index, {contract_id}",
                value=index,
                numerator=Decimal(len(flagged)),
                denominator=Decimal(len(g)),
                result=result,
                watch_threshold=f"> {idx_watch}x baseline",
                exception_threshold=f"> {idx_exc}x baseline",
            )
        )
        
        if result != Result.CONSISTENT:
            # Calculate exposure for flagged entries
            exposure = money(
                sum((e.hours * view.loaded_rate(e.employee_id) for e in flagged),
                    Decimal("0"))
            )
            
            findings.append(Finding(...))  # Create finding
    
    # 5. RETURN OVERALL RESULT
    overall = max(scored_results, key=lambda r: RESULT_RANK[r])
    return RuleResult(
        rule_id=RULE_ID,
        result=overall,
        metrics=tuple(metrics),
        findings=tuple(findings)
    )
```

**Calculation Flow for L-05 (M5 Clustering):**

```
Input Data:
  entries = 1847 time entries
  Contract "CTR-2024-NASA" has 234 entries
  
Parameters:
  start_hour = 12 (noon)
  idx_watch = 1.5
  idx_exc = 2.5
  
Historical Baseline:
  history = [0.0823, 0.0791, 0.0847, 0.0812, 0.0836, 0.0799]
  baseline = mean(history) = 0.0818

Calculation for CTR-2024-NASA:
  1. Total entries in group: 234
  2. Entries created Friday ≥ noon: 45
  3. Group window share: 45 / 234 = 0.1923 (19.23%)
  4. Cluster index: 0.1923 / 0.0818 = 2.3509
  5. Compare to thresholds:
     2.3509 < 2.5 (not Exception)
     2.3509 > 1.5 (is Watch)
     
     Result = WATCH
  
  6. Exposure: sum(hours × loaded_rate) for 45 entries
     = 45 entries × ~8 hours × ~$75/hr
     = $27,000

Finding Generated:
  Finding(
    rule_id="L-05",
    metric_id="M5",
    metric_value=2.3509,
    severity="medium",
    headline="Entry-time clustering on CTR-2024-NASA team at 2.35x baseline",
    exposure_usd=27000.00,
    evidence=[
      EvidenceRef(source="meridian_time_2026-08.csv:1142", ...),
      EvidenceRef(source="meridian_time_2026-08.csv:1143", ...),
      ... (45 evidence refs)
    ]
  )
```

---

## Helper Functions & Utilities

### Fixed-Point Arithmetic

```python
# File: app/engine/rules/base.py

def money(x: Decimal | int | str) -> Decimal:
    """
    Round to cents, half-up.
    
    Example:
      money(102.755) → 102.76
      money(102.745) → 102.75
    """
    return Decimal(x).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

def ratio(num: Decimal | int, den: Decimal | int, places: str = "0.0001") -> Decimal:
    """
    Deterministic division with rounding.
    Returns 0 on zero denominator (never divide-by-zero error).
    
    Example:
      ratio(1247, 502345, "0.0001") → 0.0025
      ratio(100, 0) → 0.0000
    """
    den = Decimal(den)
    if den == 0:
        return Decimal("0").quantize(Decimal(places))
    return (Decimal(num) / den).quantize(Decimal(places), rounding=ROUND_HALF_UP)
```

### Metric Calculations

```python
# File: app/engine/metrics.py

def baseline_history(view: WorkingView, key: str) -> list[Decimal]:
    """
    Get historical metric values for trend detection.
    
    Args:
      view: WorkingView with .baselines dict
      key: Metric key like "M2.dollar_variance"
    
    Returns:
      Last 6 periods' values, e.g., [0.0025, 0.0031, 0.0028, ...]
    """
    if key not in view.baselines:
        return []
    return view.baselines[key][-BASELINE_WINDOW:]  # Last 6

def is_rising(current: Decimal, history: list[Decimal]) -> bool:
    """
    Check if current value is rising above historical mean.
    
    Returns:
      True if current > mean(history) * 1.2
    """
    if not history:
        return False
    mean = sum(history, Decimal("0")) / len(history)
    return current > mean * Decimal("1.2")

def entry_costs(view: WorkingView, entries: list[TimeEntry]) -> list[dict]:
    """
    Calculate loaded cost for each entry.
    
    Returns:
      [
        {"entry_id": "T-001", "hours": 8.0, "loaded_rate": 75.00, "cost": 600.00},
        ...
      ]
    """
    return [
        {
            "entry_id": e.entry_id,
            "hours": str(e.hours),
            "loaded_rate": str(view.loaded_rate(e.employee_id)),
            "cost": str(money(e.hours * view.loaded_rate(e.employee_id))),
        }
        for e in entries
    ]

def lag_hours(entry: TimeEntry) -> Decimal:
    """
    Hours between work date and entry creation.
    
    Example:
      work_date = 2026-08-15
      entered_at = 2026-08-17 14:30
      
      lag = (2026-08-17 14:30 - 2026-08-15 00:00).total_seconds() / 3600
          = 62.5 hours
    """
    delta = entry.entered_at - datetime.combine(
        entry.work_date, 
        time.min
    )
    return Decimal(delta.total_seconds()) / Decimal("3600")
```

### Severity Classification

```python
# File: app/engine/severity.py

def classify_with_reason(
    result: Result,
    exposure: Decimal,
    materiality: Decimal,
    employees: int,
    trend_rising: bool = False,
    no_trend_history: bool = False,
) -> tuple[Severity, str]:
    """
    Determine finding severity based on multiple factors.
    
    Rules (from design §6.3):
      1. exposure >= materiality → HIGH
      2. trend_rising → HIGH
      3. employees >= 10 → HIGH
      4. result == EXCEPTION → MEDIUM (default)
      5. result == WATCH → LOW (default)
    
    Returns:
      (severity, reason)
      
    Examples:
      - (HIGH, "Exposure $5,234.56 exceeds materiality $2,500.00")
      - (HIGH, "Pattern rising above historical baseline")
      - (HIGH, "Affects 15 employees")
      - (MEDIUM, "Exception threshold exceeded")
      - (LOW, "Watch threshold exceeded")
    """
    # 1. Check exposure vs materiality
    if exposure >= materiality:
        return (
            Severity.HIGH,
            f"Exposure {fmt_money(exposure)} exceeds materiality {fmt_money(materiality)}"
        )
    
    # 2. Check trend
    if trend_rising:
        return (
            Severity.HIGH,
            "Pattern rising above historical baseline"
        )
    
    # 3. Check employee count
    if employees >= 10:
        return (
            Severity.HIGH,
            f"Affects {employees} employees"
        )
    
    # 4. Default by result
    if result == Result.EXCEPTION:
        return (Severity.MEDIUM, "Exception threshold exceeded")
    else:  # WATCH
        return (Severity.LOW, "Watch threshold exceeded")
```

---

## Result Aggregation

### M13 Exposure Deduplication

```python
# File: app/engine/metrics.py

def deduplicate_exposure(findings: list[Finding]) -> Decimal:
    """
    M13: Prevent double-counting when multiple rules flag same evidence.
    
    Algorithm:
      1. Build evidence graph (findings → entry_ids)
      2. Find connected components (overlapping evidence)
      3. Take max exposure per component
      4. Sum component maxes
    
    Example:
      Finding 1 (L-03): exposure=$1,247.82, entry_ids=[]
      Finding 2 (L-05): exposure=$850.00, entry_ids=[T-001, T-002, ...]
      Finding 3 (L-08): exposure=$650.00, entry_ids=[T-001, T-002, ...]
      
      Component 1: [Finding 1] → $1,247.82 (no overlap)
      Component 2: [Finding 2, Finding 3] → max($850, $650) = $850 (overlap)
      
      Total: $1,247.82 + $850.00 = $2,097.82
    """
    # Build entry_id → finding mapping
    entry_to_findings: dict[str, list[int]] = {}
    for i, f in enumerate(findings):
        for entry_id in f.exposure_entry_ids:
            entry_to_findings.setdefault(entry_id, []).append(i)
    
    # Find connected components using union-find
    components: list[set[int]] = []
    visited = set()
    
    for i, f in enumerate(findings):
        if i in visited:
            continue
        
        # BFS to find all connected findings
        component = set()
        queue = [i]
        while queue:
            curr = queue.pop(0)
            if curr in component:
                continue
            component.add(curr)
            visited.add(curr)
            
            # Find all findings sharing entry_ids with current
            for entry_id in findings[curr].exposure_entry_ids:
                for neighbor in entry_to_findings.get(entry_id, []):
                    if neighbor not in component:
                        queue.append(neighbor)
        
        components.append(component)
    
    # Sum max exposure per component
    total = Decimal("0")
    for component in components:
        max_exp = max(findings[i].exposure_usd for i in component)
        total += max_exp
    
    return money(total)
```

### Four-Layer Rollup

```python
# File: app/engine/rollup.py

def rollup(
    rule_results: list[RuleResult],
    applicable: list[RuleSpec],
    coverage_floor: Decimal = Decimal("0.85")
) -> StatusRollup:
    """
    Four-layer worst-case status rollup.
    
    Layer 1: Metric results (inside each rule)
    Layer 2: Rule results → worst among rule's metrics
    Layer 3: Requirement results → worst among rules mapped to authority
    Layer 4: Domain/Overall status → coverage-aware label
    """
    by_id = {r.rule_id: r for r in rule_results}
    
    # === LAYER 2: Calculate coverage === #
    # Only count rules with counts_toward_coverage=True
    cov_specs = [s for s in applicable if s.counts_toward_coverage]
    evaluated = [
        r for r in rule_results
        if r.result not in (Result.NOT_EVALUATED, Result.NOT_APPLICABLE)
        and by_id[r.rule_id] in cov_specs
    ]
    coverage = Decimal(len(evaluated)) / Decimal(len(cov_specs)) if cov_specs else Decimal("0")
    
    # === LAYER 3: Group by authority (requirement) === #
    by_authority: dict[str, list[RuleResult]] = {}
    for r in rule_results:
        spec = get_rule(r.rule_id)
        for auth in spec.authorities:
            by_authority.setdefault(auth, []).append(r)
    
    requirement_results = {}
    for auth, results in by_authority.items():
        requirement_results[auth] = _worst([r.result for r in results])
    
    # === LAYER 4: Determine status label === #
    open_findings = [
        f for r in rule_results 
        for f in r.findings
    ]
    
    # M13 deduplication
    total_exposure = deduplicate_exposure(open_findings)
    
    # Status label logic
    if coverage < coverage_floor:
        status = "Incomplete data"
    elif open_findings:
        count = len(open_findings)
        status = f"{count} open finding{'s' if count != 1 else ''}"
    else:
        status = "No open findings"
    
    return StatusRollup(
        status=status,
        coverage=coverage,
        total_exposure=total_exposure,
        findings_count=len(open_findings),
        by_severity={
            "high": len([f for f in open_findings if f.severity == Severity.HIGH]),
            "medium": len([f for f in open_findings if f.severity == Severity.MEDIUM]),
            "low": len([f for f in open_findings if f.severity == Severity.LOW]),
        },
        by_domain={...},  # Group by spec.domain
        requirement_results=requirement_results
    )

def _worst(results: list[Result]) -> Result:
    """
    Worst-case among results.
    
    Priority: EXCEPTION > WATCH > NOT_EVALUATED > CONSISTENT > NOT_APPLICABLE
    
    Special case: If any NOT_EVALUATED, never return CONSISTENT
    """
    active = [r for r in results if r != Result.NOT_APPLICABLE]
    if not active:
        return Result.NOT_APPLICABLE
    
    top = max(active, key=lambda r: RESULT_RANK[r])
    
    # NOT_EVALUATED blocks CONSISTENT
    if top in (Result.EXCEPTION, Result.WATCH):
        return top
    if Result.NOT_EVALUATED in active:
        return Result.NOT_EVALUATED
    
    return Result.CONSISTENT
```

---

## Summary: Calculation Trigger Chain

```
1. USER CLICKS "RUN NOW"
   └─> web/src/pages/StatusPage.tsx :: handleRunNow()

2. API CALL
   └─> web/src/api/client.ts :: api.createRun(period, tier)

3. FASTAPI ENDPOINT
   └─> api/main.py :: run_create(body)

4. SERVICE ORCHESTRATION
   └─> api/service.py :: create_run(period, tier)
       └─> Load active config
       └─> Call engine

5. ENGINE ENTRY POINT
   └─> engine/runner.py :: run(data_dir, period, tier, config, ...)
       │
       ├─> Load floors registry
       ├─> Validate config
       ├─> Ingest data → WorkingView
       │   └─> engine/ingest.py :: load_working_view()
       │       └─> Read CSVs with mappings
       │       └─> Calculate SHA-256 hashes
       │       └─> Attach lineage to every record
       │
       ├─> Select rules to run
       │   └─> Filter by domain, tier, enabled
       │
       └─> For each rule:
           ├─> Resolve parameters
           │   └─> engine/config.py :: resolve_params()
           │       └─> Three-layer resolution
           │
           └─> EXECUTE RULE
               └─> engine/rules/{rule}.py :: evaluate(view, params)
                   │
                   ├─> Check required sources
                   ├─> Extract parameters
                   ├─> Filter data from WorkingView
                   │
                   ├─> *** CALCULATIONS HAPPEN HERE ***
                   │   ├─> Sum/aggregate using Decimal
                   │   ├─> Calculate metrics/ratios
                   │   ├─> Compare to thresholds
                   │   └─> Determine result
                   │
                   ├─> If not consistent:
                   │   ├─> Calculate exposure
                   │   ├─> Classify severity
                   │   ├─> Collect evidence with lineage
                   │   └─> Generate Finding
                   │
                   └─> Return RuleResult

6. AGGREGATION
   └─> engine/rollup.py :: rollup(rule_results, ...)
       ├─> M13 exposure deduplication
       ├─> Calculate coverage
       ├─> Four-layer rollup
       └─> Generate status label

7. PERSISTENCE
   └─> api/service.py :: _save_run(output)
       ├─> Insert run record
       └─> Insert findings

8. RESPONSE
   └─> api/main.py :: JSON response to frontend

9. UI UPDATE
   └─> web/src/pages/StatusPage.tsx :: loadStatus()
       └─> Refresh dashboard with new findings
```

---

## Key Takeaways

1. **Calculations are pure functions**: Each rule's `evaluate()` function takes `(WorkingView, ResolvedParams)` and returns `RuleResult`. No I/O, no side effects.

2. **Decimal arithmetic throughout**: All money and hours use Python's `Decimal` type. Fixed-point rounding (half-up) to cents.

3. **Lineage is preserved**: Every canonical record carries `Lineage(file, hash, row)`. Findings reference evidence with exact source locations.

4. **Three-layer parameter resolution**: floor → default → customer, validated for strictness before rule execution.

5. **Deterministic execution**: Same inputs (data + config) → Same outputs (findings). Replay verification uses SHA-256 hashes.

6. **Rule isolation**: Rules never see each other's output. Each gets its own copy of WorkingView and runs independently.

7. **M13 deduplication**: Exposure is deduplicated at rollup time using graph-based connected components algorithm.

8. **Coverage-aware status**: "Incomplete data" takes precedence over clean results when coverage < floor.

---

**Document Version:** 1.0  
**Last Updated:** September 22, 2026  
**Maintained By:** Sadhik AI Engineering Team
