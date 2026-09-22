# DATA_SPEC — Meridian Systems synthetic data (binding)

Writer: **Agent A** (`data/generate.py`). Readers: **Agent B** (`engine/ingest.py` +
`config/mappings/*.yaml`), **Agent C** (rules), and `tests/test_recall.py`.

Source design: `docs/hld/system-design.md` §15 (Meridian Systems, August 2026).
**Where §15 is internally inconsistent or conflicts with this file, THIS FILE WINS**, and
`ground_truth.json` must record the deviation under `deviations_from_design_doc`.

## 0. Rules of the generator

- **Deterministic.** `random.Random(20260920)`; output byte-identical across runs. No wall clock.
- **All money/hours are exact decimals** written with fixed places (hours `x.x`, money `x.xx`).
- **Output dir:** `data/out/`. Entry point: `python -m data.generate` (run from `app/`).
- **The engine must never read `ground_truth.json` or `sources.json`'s expectations.** Ground
  truth is written by the generator from its OWN arithmetic, independent of engine code.
- Column headers below are exact (case, spacing). Source systems are deliberately named
  differently from canonical fields — that gap is what the mapping layer exists to close.

## 1. Files (all in `data/out/`)

### 1.1 `meridian_time_2026-08.csv` — Unanet-style timekeeping
`Entry ID,Person Code,TS Date,Hours,Charge,PLC,Created On,Sub Dt,Appr By,Appr Dt`

| Column | Format | Canonical field |
|---|---|---|
| Entry ID | `TE-######` | entry_id |
| Person Code | `E-####` | employee_id |
| TS Date | ISO date | work_date |
| Hours | `0.0` decimal | hours |
| Charge | charge code, e.g. `C8841-DEV` | charge_code |
| PLC | labor category name | labor_category |
| Created On | ISO datetime `YYYY-MM-DDTHH:MM:SS` (local, no tz) | entered_at |
| Sub Dt | ISO datetime | submitted_at |
| Appr By | Person Code of approver | approved_by |
| Appr Dt | ISO datetime | approved_at |

**Note the trap:** `Sub Dt` looks like a work date but is the SUBMISSION timestamp. A naive
mapping to `work_date` silently breaks timeliness. `mappings/unanet.yaml` must map it to
`submitted_at`.

### 1.2 `meridian_time_edits_2026-08.csv`
`Edit ID,Entry ID,Mod On,Mod By,Old Value,New Value,Reason`
Empty `Reason` = undocumented edit. Maps to TimeEdit (edit_id, entry_id, edited_at, editor_id,
old_value, new_value, reason).

### 1.3 `adp_payroll_2026-08.csv`
`Associate ID,Pay Period,Reg Hrs,OT Hrs,PTO Hrs,Gross Pay`
Pay periods `2026-08-A` (Aug 1–15, **80.0 h**) and `2026-08-B` (Aug 16–31, **88.0 h**) per employee.
Every employee is paid exactly 80.0 / 88.0 regular hours, OT 0.0, PTO 0.0 (168.0 h/month).

### 1.4 `qb_gl_2026-08.csv`
`Account,Class,Period,Amount,Cost Type,Pool`
`Class` = project/contract id or `INDIRECT`. `Cost Type` in `direct|indirect`. `Pool` in
`overhead|ga|fringe|` (empty for direct). Period = `2026-08`.

### 1.5 `bamboo_roster_2026-08-01.csv`
`Employee #,Name,Job Title,Hire Date,Termination Date,FLSA Status,Department,Supervisor`
`FLSA Status` in `Exempt|Non-Exempt`. Empty Termination Date = active.

### 1.6 `contracts.json` — stands in for extracted-and-human-confirmed PDFs
Array of objects:
```json
{"contract_id":"C-8841","name":"SPECTRA","type":"T&M","pop_start":"2025-10-01","pop_end":"2026-09-30",
 "ceiling":"6000000.00","funded_value":"4200000.00",
 "labor_categories":[{"name":"Data Engineer II","ceiling_rate":"142.00","min_qualification":"BS + 3 years"}, ...],
 "key_personnel":["E-0xxx"],"charge_codes":["C8841-DEV","C8841-PM"],
 "source_pdf":"C-8841.pdf",
 "extraction":{"confirmed_by":"a.okafor","confirmed_at":"2026-04-11T09:31:00Z",
               "pages":{"pop":3,"labor_categories":12,"ceiling":12}}}
```
- **C-8841**: T&M, PoP 2025-10-01 → 2026-09-30, ceiling 6,000,000.00, funded 4,200,000.00,
  11 labor categories incl. `Data Engineer II` (142.00, "BS + 3 years") and `Data Engineer III`
  (178.50, "BS + 7 years"). Invent the other 9 plausible IT categories/rates.
- **C-7302**: CPFF, PoP 2025-08-16 → **2026-08-15**, ceiling/funded 3,150,000.00, its own categories
  (must include `Data Engineer II`, `Data Engineer III`, others).

### 1.7 Confirmed reference tables (always loaded; not "sources" for tiering)
- `charge_codes.csv`: `Charge,Contract,Cost Type,Pool` — every charge code used in timekeeping
  appears exactly once. Direct codes name a contract (`C-8841`/`C-7302`); indirect codes: `OH-100`
  overhead, `GA-200` ga, `FR-050` fringe, `BP-300` ga(B&P), `LV-010` fringe(leave).
- `labor_category_crosswalk.csv`: `HRIS Title,Contract Category` (e.g. `Data Engineer,Data Engineer II`;
  `Sr. Data Engineer,Data Engineer III`; ~16 rows). Every HRIS title of a directly charging employee
  must appear.
- `loaded_rates.csv`: `Employee #,Loaded Rate` — loaded internal cost rate ($/h), stand-in for a
  customer rate model. Used ONLY for cost-basis exposure and materiality.

### 1.8 `sources.json`
```json
{"period":"2026-08","customer":"meridian-systems",
 "sources":{"timekeeping":{"file":"meridian_time_2026-08.csv","data_as_of":"2026-08-31"},
            "time_edits":{"file":"meridian_time_edits_2026-08.csv","data_as_of":"2026-08-31"},
            "payroll":{"file":"adp_payroll_2026-08.csv","data_as_of":"2026-08-31"},
            "gl":{"file":"qb_gl_2026-08.csv","data_as_of":"2026-08-31"},
            "hris":{"file":"bamboo_roster_2026-08-01.csv","data_as_of":"2026-08-01"},
            "contracts":{"file":"contracts.json","data_as_of":"2026-04-11"}},
 "absent":["rate_data"]}
```
`rate_data` is deliberately NOT generated (exercises *Not evaluated*). `hris` `data_as_of` is
2026-08-01 — 30 days stale relative to a 2026-08-31 period end.

### 1.9 `metric_history.json` — stand-in for retained results of 6 prior runs
```json
{"note":"Seeded stand-in for metric values retained from prior runs; in production these come from the results store.",
 "periods":["2026-02","2026-03","2026-04","2026-05","2026-06","2026-07"],
 "metrics":{"M5.company_window_share":{"2026-02":"0.13",...},
            "M6.edit_rate":{"2026-02":"0.0350",...},
            "M4.late_rate":{...}}}
```
- `M5.company_window_share` (share of ALL entries created in Fri ≥ 12:00): the six values MUST
  average exactly **0.14** (e.g. 0.13, 0.15, 0.14, 0.14, 0.13, 0.15).
- `M6.edit_rate`: six values between 0.033 and 0.038 (stable, so L-06 severity is Low).
- `M4.late_rate`: six values between 0.025 and 0.035.

### 1.10 `ground_truth.json` — the answer key
See §4.

## 2. Population and baseline (all numbers exact)

- **142 employees** in timekeeping and payroll (IDs `E-####`, not sequential; must include
  `E-0417 E-0912 E-1104 E-0733 E-0455`). Roster (`hris`) has **142 rows**: 138 of the active
  employees + 4 terminated (no August time). The **4 remaining active employees are ABSENT from
  the roster** (onboarded 2026-08-04): that is the DQ-01 seed.
- **21 working days** (Aug 3–7, 10–14, 17–21, 24–28, 31; Aug 1 is a Saturday).
- **≈4,120 time entries** (±40 is fine; ground truth records the exact count). Hours per employee
  per pay period sum to payroll hours ± the seeded/noise gaps in §3-L02.
- Every entry: approved by a supervisor ≠ the employee, `Appr Dt` after `Created On`, no
  missing approvals, no self-approvals, no impossible hours (>24/day), no overlap with
  termination, all charge codes mapped. (L-07, L-08, L-10 are not implemented; keep them clean.)
- Exempt/non-exempt mix ~40/60. Loaded rates between 70.00 and 120.00.
- **GL** total labor within **0.11%** of payroll gross (`|GL − payroll| ≈ 2,138.00` on ≈1.99M).
  Split into direct/indirect lines; a single line carries the 2,138.00 variance.
- **Materiality basis** = Σ(entry hours × that employee's loaded rate) over all August entries.
  Record it in ground truth as `materiality_basis_usd` and `materiality_usd` (= basis × 0.005).

## 3. Seeded conditions (each must be detectable AND be the ONLY cause of its finding)

Write the record IDs and expected dollars for each into `ground_truth.json`.

### L-01 — labor category mismatch → 1 finding, **$3,504.00**
Employee `E-0417`, HRIS title `Data Engineer` (crosswalk → `Data Engineer II`, 142.00) charges
`Data Engineer III` (178.50) on **C-8841**: **12 entries × 8.0 h = 96.0 h**.
Exposure = (178.50 − 142.00) × 96.0 = 3,504.00. No other employee has a category mismatch.

### L-05 — Friday-afternoon entry clustering → 1 finding, **$27,518.40**
Team = **9 employees** whose direct charges are on **C-7302**. Their C-7302 entries are created
in bulk on Fridays (Created On ≥ 12:00, all ≥ 15:00) so that **38%** of the team's C-7302 entries
fall in the window (Fri ≥ 12:00) vs the 0.14 company baseline → cluster index 2.71 (Exception).
- Flagged entries (all the team's C-7302 entries created in the window) priced at hours × each
  employee's loaded rate must sum to exactly **27,518.40**. Calibrate hours/rates to hit it.
- The other direct group (C-8841) must have window share within 0.10–0.17 (index < 1.5, unflagged).
- Late-entry rate M4 must be **< 5.0%** (target 3.1%, e.g. 128 of ~4,120) so L-05 raises only the
  cluster finding. **DEVIATION from §15.7** (which has 5.19% Watch); record it.

### L-09 — out-of-period-of-performance charges → 1 finding, **$5,852.40**
**62.0 h** charged to C-7302 dated 2026-08-17..08-21 (after PoP end 2026-08-15) by **3 employees**:
- `E-0455`: 16.0 h @ 102.75 = 1,644.00 — **these entries MUST also be in the L-05 flagged set**
  (created Fri ≥ 15:00) → the 1,644.00 overlap for M13 de-duplication.
- employee X: 24.0 h @ 94.50 = 2,268.00; employee Y: 22.0 h @ 88.20 = 1,940.40 — **NOT** in the
  L-05 flagged set (created outside the Fri window).
Sum: 1,644.00 + 2,268.00 + 1,940.40 = 5,852.40. X and Y are also C-7302 team members.

### L-02 — timekeeping vs payroll hours → 3 findings, **1,156.80 / 705.60 / 976.13**
Gap = time hours − paid hours for an employee-pay-period; exposure = |gap| × loaded rate.
| Employee | Gap | Rate | Exposure |
|---|---|---|---|
| E-0912 | +12.0 h | 96.40 | 1,156.80 |
| E-1104 | +8.0 h | 88.20 | 705.60 |
| E-0733 | −9.5 h | 102.75 | 976.125 → **976.13** (half-up; deliberate rounding probe) |
- **Noise:** every OTHER employee-pay-period has |gap| < 4.0 h, and Σ|gap| over ALL employee-pay-periods
  = **342.0 h** exactly (so M1 = 342.0 / 23,856.0 = 1.43%, a Watch). Seeded three sum to 29.5 h;
  noise sums to 312.5 h. Include one employee `E-0208` with a 1.5 h gap (logged below materiality).
- Payroll total paid hours = 142 × 168.0 = **23,856.0**.

### L-03 — payroll vs GL → **Consistent** (no finding)
M2 ≈ 0.11% (< 0.5% Watch).

### L-06 — post-submission edits → 1 finding, **$4,199.80**
**148 edits** on distinct submitted/approved entries (edit rate = 148 / total entries ≈ 3.6%,
a Watch). **9 undocumented** (empty Reason) = 6.1% (< 10%). Edits made in the last 2 days of the
period ≈ 41% (< 50%). The 9 undocumented edits sit on **9 distinct entries** of **6 employees**,
totalling 46.0 h and **4,199.80** at loaded rates. Suggested calibration (any equivalent works):
| Employee | Rate | Entries | Hours | Cost |
|---|---|---|---|---|
| T1 (C-7302 team, in L-05 flagged set) | 88.20 | 1 | 6.0 | 529.20 |
| T2 (C-7302 team, in L-05 flagged set) | 88.20 | 1 | 5.0 | 441.00 |
| X1 | 96.40 | 2 | 8.0 (4+4) | 771.20 |
| X2 | 96.40 | 2 | 7.0 (4+3) | 674.80 |
| X3 | 94.50 | 2 | 12.0 (6+6) | 1,134.00 |
| X4 | 81.20 | 1 | 8.0 | 649.60 |
Total 46.0 h, **4,199.80**. The T1/T2 entries (11.0 h, 970.20) MUST be in the L-05 flagged set
(the second M13 overlap). None of the L-06 entries may be in the L-09 set.
Also, the 148 edits' `Old Value`/`New Value` are plausible hour changes; documented edits carry
reasons such as `Corrected charge code per supervisor` or `Timesheet correction memo 2026-08-19`.

### DQ-01 — unmatched employee IDs → 1 finding (no dollar exposure)
The **4 active employees absent from the roster** (§2), all with August time and payroll rows.

### L-11 — not evaluable
`rate_data` absent → *Not evaluated*.

## 4. `ground_truth.json`

```json
{
 "period":"2026-08","seed":20260920,
 "counts":{"employees":142,"time_entries":<exact>,"edits":148,"payroll_rows":284,"gl_lines":<exact>},
 "materiality_basis_usd":"<exact>","materiality_usd":"<exact>",
 "expected_metrics":{"M1":"0.0143","M4":"<exact>","M5_C-7302":"<exact>","M6":"<exact>"},
 "injected":[
  {"seed_id":"S-L01","rule_id":"L-01","fingerprint":"L-01|E-0417|C-8841","employees":["E-0417"],
   "entry_ids":[...],"hours":"96.0","exposure_usd":"3504.00","exposure_basis":"rate_difference","severity":"high"},
  {"seed_id":"S-L05","rule_id":"L-05","fingerprint":"L-05|C-7302", "employees":[9 ids],
   "entry_ids":[...],"exposure_usd":"27518.40","exposure_basis":"loaded_cost","severity":"high"},
  {"seed_id":"S-L09","rule_id":"L-09","fingerprint":"L-09|C-7302", ...,"exposure_usd":"5852.40","severity":"high"},
  {"seed_id":"S-L02a","rule_id":"L-02","fingerprint":"L-02|E-0912|2026-08-B", ...,"exposure_usd":"1156.80","severity":"medium"},
  ... L02b (E-1104), L02c (E-0733) ...,
  {"seed_id":"S-L06","rule_id":"L-06","fingerprint":"L-06|2026-08", ...,"exposure_usd":"4199.80","severity":"low"},
  {"seed_id":"S-DQ01","rule_id":"DQ-01","fingerprint":"DQ-01|2026-08","employees":[4 ids],"exposure_usd":"0.00","severity":"medium"}],
 "expected_findings":8,
 "expected_total_exposure_usd":"41298.93",
 "overlaps":[{"between":["L-05","L-09"],"entry_ids":[...],"usd":"1644.00"},
             {"between":["L-05","L-06"],"entry_ids":[...],"usd":"970.20"}],
 "expected_not_evaluated":["L-11"],
 "expected_consistent":["L-03"],
 "logged_below_materiality":[{"rule_id":"L-02","employee_id":"E-0208","gap_hours":"1.5"}],
 "deviations_from_design_doc":[ "..." ]
}
```
**Fingerprints** are the stable identity of a finding across runs. Use exactly:
- L-01 `L-01|<employee_id>|<contract_id>`
- L-02 `L-02|<employee_id>|<pay_period>`
- L-05 `L-05|<contract_id>`
- L-06 `L-06|<period>`
- L-09 `L-09|<contract_id>`
- DQ-01 `DQ-01|<period>`

**Expected total exposure (M13)** = 43,913.13 gross − 1,644.00 − 970.20 = **41,298.93**, computed as:
Σ non-entry-based findings (L-01, L-02×3) + Σ over the UNION of entries priced at hours × rate for the
entry-based findings (L-05, L-06, L-09). (3,504.00 + 1,156.80 + 705.60 + 976.13 = 6,342.53 non-entry;
union of entry-based = 34,956.40.)

Expected findings by severity: **high 3** (L-01, L-05, L-09), **medium 4** (L-02 ×3, DQ-01), **low 1** (L-06).
Expected coverage at the close tier: **6 of 7** applicable L-rules evaluated (L-11 not evaluated) = 85.7%.

## 5. Acceptance (Agent A self-check before reporting done)

`python -m data.generate` twice → identical SHA-256 for every file. A standalone
`data/verify_ground_truth.py` (NO engine imports) recomputes every expected figure from the CSVs
alone and asserts it equals `ground_truth.json`: entry counts, Σ|gap| = 342.0, L-01 96.0 h,
L-05 flagged exposure 27,518.40, L-09 62.0 h / 5,852.40, both overlaps, L-06 46.0 h / 4,199.80,
M13 = 41,298.93, the 4 unmatched IDs, M4 < 5%, the M5 baseline mean = 0.14.
