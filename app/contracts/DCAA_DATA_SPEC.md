# DCAA_DATA_SPEC — DCAA cost-accounting extension of the Meridian dataset (binding for Agent A2)

Extends `contracts/DATA_SPEC.md`. Everything there still applies (determinism, exact decimals, no wall clock,
column headers, ground-truth-from-own-arithmetic). Where this file and DATA_SPEC.md disagree, THIS FILE wins
for the DCAA additions. **The existing eight labor findings must not change.**

Writer: `data/generate.py`. Independent checker: `data/verify_ground_truth.py` (STANDALONE, imports nothing
from `engine/`; this file was promised for the labor build and never delivered — it is required now, and it
must cover the original labor checks as well as the new ones).

## 0. Regression contract (non-negotiable)

Run `python -m data.generate` twice: identical bytes both times. Then:

**These files must be BYTE-IDENTICAL to the current output** (SHA-256 of the current `data/out/`):

| File | SHA-256 |
|---|---|
| `adp_payroll_2026-08.csv` | `1567e996d890ce13e9a17da86a0712473ab327754b0c6c5a0ae50c8bbe09090b` |
| `bamboo_roster_2026-08-01.csv` | `8920aced672026ba6f06166cfce6b00bb64ac9f828c82a01bd036d58bf618306` |
| `charge_codes.csv` | `f72191f3abf506fe2be6e0d1a27fe43f6f567074cf017bf1cce64ceda48b56c4` |
| `contracts.json` | `2e71147985cf35e87bac5f5c0148fd7e31ff11daaa310c8702b5bc3796325ecf` |
| `labor_category_crosswalk.csv` | `6ed2b91daef81ae62675adb61dbdfee6f927bdb3679ba554b510a0291d622dba` |
| `loaded_rates.csv` | `281d78081678359fcc33d8218233713451ba5780cdb9f47d5e95059e81c496c2` |
| `meridian_time_edits_2026-08.csv` | `da348e6606f4277aae246e1ca24d2b497dce577c086f3ba41d58301d12982c4f` |

**These files change, and ONLY as described:**
- `meridian_time_2026-08.csv`: exactly **5 rows** differ (the L-08 seed, section 3.2): the `Charge` value only.
- `qb_gl_2026-08.csv`: the lines whose amounts follow from those 5 rows, plus appended non-labor lines (3.1).
- `metric_history.json`: the three existing keys (`M5.company_window_share`, `M6.edit_rate`, `M4.late_rate`)
  keep their exact values; six keys are added (3.3).
- `sources.json`, `ground_truth.json`: extended.

**Mechanism.** Do NOT change the order or count of draws from the existing `random.Random(SEED)`. Apply the
L-08 move deterministically (no RNG) BEFORE `build_gl`, and draw all new data (history noise, non-labor
splits) from a SECOND stream `random.Random(SEED + 1)` created after the original generation completes.
That is what keeps the byte-identical files identical.

**These original results must be unchanged** (fingerprint, exposure):
`L-01|E-0417|C-8841` 3504.00 · `L-05|C-7302` 27518.40 · `L-09|C-7302` 5852.40 ·
`L-02|E-0912|2026-08-B` 1156.80 · `L-02|E-1104|2026-08-A` 705.60 · `L-02|E-0733|2026-08-B` 976.13 ·
`L-06|2026-08` 4199.80 · `DQ-01|2026-08` 0.00; sum of |timekeeping − payroll| = 342.0 h; GL − payroll = 2138.00;
the C-8841 Friday-window share stays within 0.10–0.17; M4 late-entry rate stays < 5%.

## 1. New and changed files

### 1.1 `meridian_rates_2026-08.csv` (NEW; source `rate_data`)
```
Pool,Base Definition,Provisional Rate,Effective From,Effective To,Source Document
fringe,direct_labor,0.2800,2026-01-01,2026-12-31,FY2026 provisional billing rate schedule
overhead,direct_labor,0.1800,2026-01-01,2026-12-31,FY2026 provisional billing rate schedule
ga,total_cost_input,0.1000,2026-01-01,2026-12-31,FY2026 provisional billing rate schedule
```
`Base Definition` is a closed vocabulary: `direct_labor`, `total_cost_input`. Rates are fractions, 4 places.

### 1.2 `account_categories.csv` (NEW; confirmed reference table, analogous to `charge_codes.csv`)
`Account,Category` — `Category` in `labor|non_labor`. Every GL account appears exactly once. Existing accounts
(`5010 Direct Labor`, `6010 Overhead Labor`, `6110 G&A Labor`, `6190 Payroll Accrual Adjustment`,
`6210 Fringe and Leave Labor`) are `labor`; every account added in 1.3 is `non_labor`.
Purpose: L-03 must keep tying payroll to GL LABOR only once non-labor cost lines exist.

### 1.3 `qb_gl_2026-08.csv` (CHANGED): append non-labor lines
Headers unchanged (`Account,Class,Period,Amount,Cost Type,Pool`). Existing lines stay in place (their amounts
change only where the L-08 move forces it, 3.2); new lines are APPENDED after them in this order.

| Account | Class | Cost Type | Pool | Amount |
|---|---|---|---|---|
| `5410 Subcontractors` | C-8841 | direct | | 210000.00 |
| `5410 Subcontractors` | C-7302 | direct | | 100000.00 |
| `5420 Travel and Other Direct Costs` | C-8841 | direct | | 62500.00 |
| `5420 Travel and Other Direct Costs` | C-7302 | direct | | 32500.00 |
| `6310 Payroll Taxes` / `6320 Health and Welfare Benefits` / `6330 Retirement Contributions` | INDIRECT | indirect | fringe | split 40% / 45% / 15% of the fringe plug |
| `6410 Rent and Facilities` / `6420 Equipment and Software` / `6430 Training and Recruiting` | INDIRECT | indirect | overhead | split 50% / 35% / 15% of the overhead plug |
| `6510 Insurance` / `6520 Legal and Accounting` / `6530 Marketing and Proposals` / `6540 Office Administration` | INDIRECT | indirect | ga | split 30% / 35% / 20% / 15% of the G&A plug |

Direct non-labor total = **405,000.00** exactly. Each split line is rounded to cents; the LAST line of a group
absorbs the rounding remainder so the plug is exact. Period is `2026-08` on every line.

**The rate formulas (the ONLY definitions; the engine uses the same ones):**
```
DL      = sum(Amount)  where Cost Type = direct   and account category = labor
DNL     = sum(Amount)  where Cost Type = direct   and account category = non_labor
Pool_p  = sum(Amount)  where Cost Type = indirect and Pool = p                 (labor AND non-labor lines)
base(fringe)   = DL
base(overhead) = DL
base(ga)       = DL + DNL + Pool_fringe + Pool_overhead                        (total cost input)
rate_p  = round_half_up(Pool_p / base_p, 4)
```
**Targets for August** (the generator computes each plug = `round(target * base) - existing labor lines in that
pool`, in the order fringe, overhead, then G&A because G&A's base contains the other two pools; then it
verifies `rate_p == target`): **fringe 0.2810 · overhead 0.1790 · G&A 0.1080**.
Note the overhead pool already contains `6190 Payroll Accrual Adjustment` (2138.00) — leave it there, it is part
of the pool as the GL reports it.
DL is the DIRECT labor after the L-08 move (3.2), so compute the plugs from the FINAL labor lines.

### 1.4 `metric_history.json` (CHANGED): add six keys
Existing keys untouched. Add, for periods `2026-02 … 2026-07` (dollars, 2 decimals, strings):
`M10.pool.fringe  M10.base.fringe  M10.pool.overhead  M10.base.overhead  M10.pool.ga  M10.base.ga`.
Constraint: `round_half_up(pool/base, 4)` must equal these rates for each month:

| Pool (prov.) | Feb | Mar | Apr | May | Jun | Jul | **Aug (from GL)** |
|---|---|---|---|---|---|---|---|
| fringe (0.2800) | 0.2792 | 0.2805 | 0.2811 | 0.2796 | 0.2803 | 0.2809 | **0.2810** |
| overhead (0.1800) | 0.1802 | 0.1794 | 0.1806 | 0.1797 | 0.1791 | 0.1788 | **0.1790** |
| **G&A (0.1000)** | 0.1008 | 0.0996 | 0.1012 | **0.1040** | **0.1055** | **0.1068** | **0.1080** |

G&A M10 = (rate − 0.1000) / 0.1000 = +0.8%, −0.4%, +1.2%, **+4.0%, +5.5%, +6.8%, +8.0%**: the design's drift case.
Bases: fringe/overhead base between 1.60M and 1.78M, G&A base between 2.75M and 3.00M, varying month to month
by a deterministic pattern from the second RNG; `pool = round(rate * base, 2)`.

### 1.5 `classification_history.json` (NEW; confirmed reference table)
```json
{"note":"Seeded stand-in for classification retained from prior runs; in production these come from the results store.",
 "definition":"direct = hours on direct charge codes; indirect = hours on overhead-pool and G&A-pool codes (OH-100, GA-200, BP-300). Fringe-pool codes (FR-050, LV-010) are excluded.",
 "periods":["2026-02","2026-03","2026-04","2026-05","2026-06","2026-07"],
 "employees":{"E-0001":{"2026-02":{"direct_hours":"150.0","indirect_hours":"6.0"}, "...":{}}}}
```
- Present for every employee who worked all of August and was hired before 2026-02-01 (all but the 4 DQ-01
  employees onboarded 2026-08-04, who get NO entry: fewer than 3 periods of history means "not scored").
- **Non-seeded employees:** for each month, `indirect = max(0, August_indirect + d)` with `d` a multiple of 0.5
  drawn uniformly in [-3.0, +3.0], and `direct = August_direct - d` (plus 0 to 2.0 h of noise, multiple of 0.5).
  Required for EVERY non-seeded employee, checked by the verifier:
  `|share_now - mean(share_hist)| < 0.05` AND `|indirect_now - mean(indirect_hist)| < 8.0 h`.
- **Employee X (the seed, 3.2):** every one of the six months holds `indirect_hours == a` and
  `direct_hours == D0`, where `a` and `D0` are X's own PRE-MOVE August indirect and direct hours. The mean is
  exactly `a`, so the excess in August is exactly the moved hours.

### 1.6 `sources.json` (CHANGED)
Add `"rate_data":{"file":"meridian_rates_2026-08.csv","data_as_of":"2026-08-31"}` to `sources`; `absent` becomes `[]`.
`account_categories.csv` and `classification_history.json` are reference tables, not sources (like
`charge_codes.csv`).

## 2. What the engine will compute (so the answer key is comparable)

- **L-11:** for each pool, `actual_rate = rate_p` (formula in 1.3); `M10 = round_half_up((actual_rate - prov) / prov, 4)`;
  Watch when `|M10| > 0.02`, Exception when `|M10| > 0.05`. A finding is raised for an Exception, and for a Watch
  whose exposure is at or above materiality. `exposure = money(|actual_rate - prov| * base_p)` (the true-up at
  provisional rates), reported with `direction` `under_billed` (actual above provisional) or `over_billed`.
  Severity High when `|M10| > 0.05` AND the drift has exceeded Watch for >= 3 consecutive periods ending at the
  current one (May–Aug = 4).
- **L-08:** per employee with >= 3 history periods: `indirect_now` and `direct_now` from August entries by the
  charge-code map (overhead and G&A pools = indirect; fringe pool excluded); `share = indirect / (direct + indirect)`.
  Flag when `share_now - mean(share_hist) >= 0.15` AND `indirect_now - mean(indirect_hist) >= 16.0`.
  `excess = indirect_now - mean(indirect_hist)`; `exposure = money(excess * loaded_rate)`. Flagged = Exception,
  treated as an integrity failure (High), like L-01.

## 3. The seeded defects (each detectable AND the only cause of its finding)

### 3.1 L-11 — one finding, G&A
Fringe (+0.36%) and overhead (−0.56%) stay below Watch: **no finding**. G&A: pool/base = 0.1080, M10 = **+0.0800**,
Exception, High. Expected exposure = `money(0.0080 * base_ga)`; the generator computes it from the FINAL GL lines.
Fingerprint `L-11|ga|2026-08`.

### 3.2 L-08 — one finding
Pick employee **X** deterministically: the FIRST employee in ascending `employee_id` order who (a) has at least five
August entries of exactly 8.0 h on `C8841-DEV` or `C8841-PM`, none of them appearing in `meridian_time_edits`, (b) is not
in any other seed (`E-0417 E-0912 E-1104 E-0733 E-0208 E-0455`, the L-05 team, the L-09 employees, the L-06
employees, the 4 DQ-01 employees), (c) has at most 6.0 h of overhead-or-G&A hours in August (so the baseline is
plausible), and (d) is on the roster. Move X's **first five** such entries (chronological, then `Entry ID`) by
changing ONLY the `Charge` column to `OH-100` → **40.0 h**. Leave `PLC`, timestamps and approvals as they are (that
is the existing convention for indirect rows). Expected: `excess = 40.0`, `exposure = money(40.0 * loaded_rate(X))`,
Exception, High. Fingerprint `L-08|<X>|2026-08`. Record X, the five entry IDs and their original charge codes.
The moved entries are NOT in the L-05, L-06 or L-09 sets, so M13 adds L-08 with no overlap.

## 4. `ground_truth.json` (CHANGED)
Keep every existing key and value; additionally:
- `injected` gains `S-L11-GA` (`fingerprint`, `exposure_usd`, `severity: "high"`, `exposure_basis: "rate_true_up"`, `pool`,
  `base_usd`, `pool_usd`, `actual_rate`, `provisional_rate`, `m10`) and `S-L08` (`fingerprint`, `employees:[X]`,
  `entry_ids`, `hours: "40.0"`, `exposure_usd`, `severity: "high"`, `exposure_basis: "loaded_cost"`).
- `expected_findings: 10`; `expected_total_exposure_usd` = previous 41298.93 + L-08 + L-11 (there are no new overlaps).
- `expected_not_evaluated: []` (L-11 is now evaluated); `expected_consistent: ["L-03"]`.
- `expected_by_severity: {"high": 5, "medium": 4, "low": 1}`.
- `domains: {"labor": {"rules": ["L-01","L-02","L-03","L-05","L-06","L-09"], "coverage": [6, 6]},
  "dcaa_cost_accounting": {"rules": ["L-08","L-11"], "coverage": [2, 2]}}`.
- `dcaa_rates: {pool: {"pool_usd","base_usd","actual_rate","provisional_rate","m10"}}` for all three pools (for the
  mutation checks and the standalone verifier).
- `deviations_from_design_doc`: append anything you had to decide (e.g. the accrual line sitting in the overhead pool).

## 5. Acceptance (Agent A2 self-check; paste the output)
1. `python -m data.generate` twice → `shasum -a 256 data/out/*` identical; the 7 files in section 0 match the
   hashes above exactly; `meridian_time_2026-08.csv` differs from the current file in exactly 5 rows.
2. `python -m data.verify_ground_truth` exits 0, each check printed PASS, with NO engine imports. It must recompute
   from the CSV/JSON alone: every original labor figure (counts, Σ|gap|=342.0, L-01/L-05/L-09/L-06 figures, both M13
   overlaps, the 4 unmatched IDs, GL variance 0.11%, M4 < 5%, M5 baseline mean 0.14, C-8841 window share), plus: the
   three pool/base/rate results and M10 values, the G&A drift series, L-11 exposure, the L-08 excess and exposure,
   the noise-band condition for every non-seeded employee (1.5), payroll-to-GL still tying on labor accounts only
   (2138.00), and the final M13.
3. Confirm labor GL total still equals payroll gross + 2138.00 when only `labor` accounts are summed.

## 6. Second seed set: C-01, C-02, C-03 (added after the first DCAA build)

Everything in sections 0-5 still holds, including the seven byte-identical files. This set adds three rules' data
without moving any pool total, rate or labor figure.

**New reference tables** (`data/out/`, always loaded when present, silently empty when absent):
- `account_allowability.csv` — `Account,Allowability,Citation`. Closed vocabulary `allowable|unallowable|conditional`. Every GL
  account exactly once. Unallowable and conditional rows carry a FAR 31.205 citation (conditional ones marked "to verify").
- `ics_submissions.csv` — `Fiscal Year,Fiscal Year End,Submitted On,Source Document`. `Submitted On` blank means none recorded.

**Changed file:** `meridian_rates_2026-08.csv` gains an optional LAST column `Ceiling Rate` (a fraction; blank allowed). The
mapping reads it as an OPTIONAL column, so a rate file without it still loads.

**C-01 seed.** Five lines are CARVED OUT of existing plug lines into their own accounts, inside the same pool, so every pool
total and every rate is unchanged: G&A `6535 Entertainment and Events` 14,800.00, `6565 Lobbying and Political Activity` 6,500.00,
`6555 Fines and Penalties` 2,250.00 (all unallowable) and `6525 Gifts and Employee Awards` 4,100.00 (conditional); overhead
`6435 Alcoholic Beverages` 1,850.00 (unallowable). Expected: `C-01|ga|2026-08` 23,550.00 High; `C-01|overhead|2026-08` 1,850.00
Medium; the 4,100.00 conditional line is logged below materiality and raises nothing.

**C-02 seed.** FY2023 (submitted 2024-06-21) and FY2024 (submitted 2025-06-27) are on time; FY2025 (year end 2025-12-31, due
2026-06-30) has no submission. As of the last day of the period (2026-08-31) it is 62 days overdue. Expected:
`C-02|FY2025|2026-08`, exposure 0.00, High. A filing due on the as-of date is not yet late.

**C-03 seed.** Ceilings: fringe 0.3000, overhead 0.1750, G&A 0.1200. Only overhead's provisional rate (0.1800) is above its ceiling.
Expected: `C-03|overhead|2026-08`, exposure `money(0.0050 x 1,732,648.59)` = 8,663.24, High. Overhead's ACTUAL rate (0.1790) is
also above the ceiling: context, not part of the exposure. Overhead's drift from its provisional rate is -0.56%, so L-11 is
quiet there and the two findings do not overlap.

**Totals.** 14 findings (8 high, 5 medium, 1 low); M13 = 102,491.51 (= 68,428.27 + 25,400.00 + 0.00 + 8,663.24); DCAA-domain
total 61,192.58; coverage 11 of 11 (labor 6 of 6, DCAA 5 of 5). `data/verify_ground_truth.py` recomputes all of it from the
files alone (154 checks).
