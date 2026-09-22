#!/usr/bin/env python3
"""Standalone verifier for data/out/ground_truth.json.

Recomputes every expected figure from the generated CSV/JSON files ALONE and asserts it
equals ground_truth.json.  Imports nothing from engine/ or api/ and shares no code with
generate.py.  Prints PASS/FAIL per check; exits non-zero if any check fails.

    .venv/bin/python -m data.verify_ground_truth
"""

from __future__ import annotations

import calendar
import csv
import hashlib
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

D = Decimal
OUT = Path(__file__).resolve().parent / "out"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, bool(ok), detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"   [{detail}]" if detail else ""))


def eq(name: str, got, want) -> None:
    check(name, got == want, f"got={got} want={want}")


def q(x: Decimal, places: str) -> Decimal:
    return x.quantize(D(places), rounding=ROUND_HALF_UP)


def money(x: Decimal) -> Decimal:
    return q(x, "0.01")


def rd(name: str) -> list[dict]:
    with open(OUT / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def rj(name: str):
    with open(OUT / name, encoding="utf-8") as fh:
        return json.load(fh)


def dt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")


def main() -> int:
    gt = rj("ground_truth.json")
    time_rows = rd("meridian_time_2026-08.csv")
    edit_rows = rd("meridian_time_edits_2026-08.csv")
    pay_rows = rd("adp_payroll_2026-08.csv")
    gl_rows = rd("qb_gl_2026-08.csv")
    roster = rd("bamboo_roster_2026-08-01.csv")
    charge_rows = rd("charge_codes.csv")
    xwalk = {r["HRIS Title"]: r["Contract Category"] for r in rd("labor_category_crosswalk.csv")}
    loaded = {r["Employee #"]: D(r["Loaded Rate"]) for r in rd("loaded_rates.csv")}
    contracts = {c["contract_id"]: c for c in rj("contracts.json")}
    rates_file = rd("meridian_rates_2026-08.csv")
    acct_cat_rows = rd("account_categories.csv")
    hist = rj("metric_history.json")
    chist = rj("classification_history.json")
    sources = rj("sources.json")
    inj = {i["seed_id"]: i for i in gt["injected"]}

    # ------------------------------------------------------------------ regression
    spec_hashes = {
        "adp_payroll_2026-08.csv": "1567e996d890ce13e9a17da86a0712473ab327754b0c6c5a0ae50c8bbe09090b",
        "bamboo_roster_2026-08-01.csv": "8920aced672026ba6f06166cfce6b00bb64ac9f828c82a01bd036d58bf618306",
        "charge_codes.csv": "f72191f3abf506fe2be6e0d1a27fe43f6f567074cf017bf1cce64ceda48b56c4",
        "contracts.json": "2e71147985cf35e87bac5f5c0148fd7e31ff11daaa310c8702b5bc3796325ecf",
        "labor_category_crosswalk.csv": "6ed2b91daef81ae62675adb61dbdfee6f927bdb3679ba554b510a0291d622dba",
        "loaded_rates.csv": "281d78081678359fcc33d8218233713451ba5780cdb9f47d5e95059e81c496c2",
        "meridian_time_edits_2026-08.csv": "da348e6606f4277aae246e1ca24d2b497dce577c086f3ba41d58301d12982c4f",
    }
    for fn, h in spec_hashes.items():
        got = hashlib.sha256((OUT / fn).read_bytes()).hexdigest()
        check(f"regression: {fn} byte-identical (DCAA_DATA_SPEC section 0)", got == h, got[:12])

    # ------------------------------------------------------------------ parse
    code_info = {r["Charge"]: r for r in charge_rows}
    entries = []
    for r in time_rows:
        entries.append({
            "id": r["Entry ID"], "emp": r["Person Code"], "date": date.fromisoformat(r["TS Date"]),
            "hours": D(r["Hours"]), "code": r["Charge"], "plc": r["PLC"], "created": dt(r["Created On"]),
            "sub": dt(r["Sub Dt"]), "appr_by": r["Appr By"], "appr": dt(r["Appr Dt"]),
        })
    by_id = {x["id"]: x for x in entries}
    emps = sorted({x["emp"] for x in entries})
    cost = lambda x: x["hours"] * loaded[x["emp"]]  # noqa: E731

    def contract_of(code: str) -> str:
        return code_info[code]["Contract"]

    def ctype(code: str) -> str:
        return code_info[code]["Cost Type"]

    def pool(code: str) -> str:
        return code_info[code]["Pool"]

    # ------------------------------------------------------------------ counts & hygiene
    eq("counts.employees", len(emps), gt["counts"]["employees"])
    eq("counts.time_entries", len(entries), gt["counts"]["time_entries"])
    eq("counts.edits", len(edit_rows), gt["counts"]["edits"])
    eq("counts.payroll_rows", len(pay_rows), gt["counts"]["payroll_rows"])
    eq("counts.gl_lines", len(gl_rows), gt["counts"]["gl_lines"])
    check("employees == 142, edits == 148, payroll rows == 284",
          (len(emps), len(edit_rows), len(pay_rows)) == (142, 148, 284))
    check("all charge codes in timekeeping are mapped in charge_codes.csv",
          all(x["code"] in code_info for x in entries))
    check("no self-approval; Appr Dt after Created On; approver present",
          all(x["appr_by"] and x["appr_by"] != x["emp"] and x["created"] < x["sub"] < x["appr"]
              for x in entries))
    per_day = defaultdict(lambda: D(0))
    for x in entries:
        per_day[(x["emp"], x["date"])] += x["hours"]
    check("no impossible hours (> 24 h/day)", all(v <= D(24) for v in per_day.values()))
    term = {r["Employee #"]: r["Termination Date"] for r in roster if r["Termination Date"]}
    check("no time entries for terminated roster employees", not any(e in term for e in emps))
    check("21 working days present", len({x["date"] for x in entries}) == 21)

    basis = money(sum(cost(x) for x in entries))
    eq("materiality_basis_usd", f"{basis:.2f}", gt["materiality_basis_usd"])
    eq("materiality_usd (basis x 0.005)", f"{money(basis * D('0.005')):.2f}", gt["materiality_usd"])
    materiality = money(basis * D("0.005"))

    # ------------------------------------------------------------------ L-02 / M1
    paid = {(r["Associate ID"], r["Pay Period"]): D(r["Reg Hrs"]) + D(r["OT Hrs"]) + D(r["PTO Hrs"])
            for r in pay_rows}

    def pp(d: date) -> str:
        return "2026-08-A" if d.day <= 15 else "2026-08-B"

    tk = defaultdict(lambda: D(0))
    for x in entries:
        tk[(x["emp"], pp(x["date"]))] += x["hours"]
    gap = {k: tk[k] - paid[k] for k in paid}
    sum_abs = sum(abs(v) for v in gap.values())
    total_paid = sum(paid.values())
    eq("sum |timekeeping - payroll| hours = 342.0", f"{sum_abs:.1f}", "342.0")
    eq("payroll total paid hours = 23856.0", f"{total_paid:.1f}", "23856.0")
    eq("M1 = 0.0143", f"{q(sum_abs / total_paid, '0.0001'):.4f}", gt["expected_metrics"]["M1"])
    seeds02 = {("E-0912", "2026-08-B"): D("12.0"), ("E-1104", "2026-08-A"): D("8.0"),
               ("E-0733", "2026-08-B"): D("-9.5")}
    check("L-02 seeded gaps exact", all(gap[k] == v for k, v in seeds02.items()))
    check("L-02 every other employee-period |gap| < 4.0 h",
          all(abs(v) < D("4.0") for k, v in gap.items() if k not in seeds02))
    eq("E-0208 gap 1.5 h (below materiality)", gap[("E-0208", "2026-08-A")], D("1.5"))
    check("E-0208 exposure below materiality", D("1.5") * loaded["E-0208"] < materiality)
    for sid in ("S-L02a", "S-L02b", "S-L02c"):
        i = inj[sid]
        e_, p_ = i["employees"][0], i["pay_period"]
        want = money(abs(gap[(e_, p_)]) * loaded[e_])
        eq(f"{sid} {i['fingerprint']} exposure", f"{want:.2f}", i["exposure_usd"])
    eq("L-02 exposures 1156.80 / 705.60 / 976.13",
       [inj[s]["exposure_usd"] for s in ("S-L02a", "S-L02b", "S-L02c")], ["1156.80", "705.60", "976.13"])
    check("L-02: no other pay period reaches materiality",
          all(money(abs(v) * loaded[k[0]]) < materiality for k, v in gap.items() if k not in seeds02))

    # ------------------------------------------------------------------ L-01
    title = {r["Employee #"]: r["Job Title"] for r in roster}
    cat_rate = {(cid, c["name"]): D(c["ceiling_rate"]) for cid, ct in contracts.items()
                for c in ct["labor_categories"]}
    l01 = []
    for x in entries:
        if x["emp"] not in title or ctype(x["code"]) != "direct":
            continue
        mapped = xwalk[title[x["emp"]]]
        if x["plc"] != mapped:
            l01.append(x)
    l01_emps = sorted({x["emp"] for x in l01})
    l01_hours = sum(x["hours"] for x in l01)
    l01_usd = money(sum(x["hours"] * (cat_rate[(contract_of(x["code"]), x["plc"])]
                                       - cat_rate[(contract_of(x["code"]), xwalk[title[x["emp"]]])])
                        for x in l01))
    check("L-01 only E-0417 has a category mismatch", l01_emps == ["E-0417"], str(l01_emps))
    eq("L-01 12 entries / 96.0 h", (len(l01), f"{l01_hours:.1f}"), (12, "96.0"))
    eq("L-01 exposure 3504.00", f"{l01_usd:.2f}", inj["S-L01"]["exposure_usd"])
    eq("L-01 entry ids", sorted(x["id"] for x in l01), inj["S-L01"]["entry_ids"])
    eq("L-01 = 3504.00", f"{l01_usd:.2f}", "3504.00")

    # ------------------------------------------------------------------ L-05 / M5
    def in_window(x) -> bool:
        return x["created"].weekday() == 4 and x["created"].hour >= 12

    c7302 = [x for x in entries if contract_of(x["code"]) == "C-7302"]
    c8841 = [x for x in entries if contract_of(x["code"]) == "C-8841"]
    team = sorted({x["emp"] for x in c7302})
    eq("L-05 team size 9", len(team), 9)
    eq("L-05 team ids", team, inj["S-L05"]["employees"])
    l05 = [x for x in c7302 if in_window(x)]
    l05_usd = money(sum(cost(x) for x in l05))
    eq("L-05 flagged exposure 27518.40", f"{l05_usd:.2f}", inj["S-L05"]["exposure_usd"])
    eq("L-05 = 27518.40", f"{l05_usd:.2f}", "27518.40")
    eq("L-05 flagged entry ids", sorted(x["id"] for x in l05), inj["S-L05"]["entry_ids"])
    check("L-05 flagged entries all created Fri >= 15:00 and all by team members",
          all(x["created"].hour >= 15 and x["emp"] in team for x in l05))
    m5_7302 = D(len(l05)) / D(len(c7302))
    m5_8841 = D(sum(1 for x in c8841 if in_window(x))) / D(len(c8841))
    m5_company = D(sum(1 for x in entries if in_window(x))) / D(len(entries))
    baseline = sum(D(v) for v in hist["metrics"]["M5.company_window_share"].values()) / D(6)
    eq("M5 baseline mean (history) = 0.14", f"{baseline:.2f}", "0.14")
    eq("M5_C-7302 = 0.3800", f"{q(m5_7302, '0.0001'):.4f}", gt["expected_metrics"]["M5_C-7302"])
    eq("M5_C-8841 matches ground truth", f"{q(m5_8841, '0.0001'):.4f}", gt["expected_metrics"]["M5_C-8841"])
    check("M5_C-8841 window share within 0.10-0.17 (index < 1.5)", D("0.10") <= m5_8841 <= D("0.17"),
          f"{m5_8841:.4f}")
    eq("M5_company", f"{q(m5_company, '0.0001'):.4f}", gt["expected_metrics"]["M5_company"])
    eq("L-05 cluster index 2.71", f"{q(m5_7302 / D('0.14'), '0.01'):.2f}", "2.71")
    check("C-8841 flagged: none beyond baseline (index < 1.5 => no second L-05 finding)",
          m5_8841 / D("0.14") < D("1.5"))

    # ------------------------------------------------------------------ L-09
    pop_end = date.fromisoformat(contracts["C-7302"]["pop_end"])
    l09 = [x for x in c7302 if x["date"] > pop_end]
    l09_usd = money(sum(cost(x) for x in l09))
    eq("L-09 62.0 h", f"{sum(x['hours'] for x in l09):.1f}", "62.0")
    eq("L-09 exposure 5852.40", f"{l09_usd:.2f}", inj["S-L09"]["exposure_usd"])
    eq("L-09 = 5852.40", f"{l09_usd:.2f}", "5852.40")
    eq("L-09 entry ids", sorted(x["id"] for x in l09), inj["S-L09"]["entry_ids"])
    eq("L-09 three employees", len({x["emp"] for x in l09}), 3)
    check("L-09 C-8841 has no out-of-PoP charges",
          not [x for x in c8841 if x["date"] > date.fromisoformat(contracts["C-8841"]["pop_end"])])

    # ------------------------------------------------------------------ L-06
    edits = [{"id": r["Edit ID"], "entry": r["Entry ID"], "mod_on": dt(r["Mod On"]), "reason": r["Reason"]}
             for r in edit_rows]
    undoc = [e for e in edits if e["reason"] == ""]
    eq("L-06 148 edits on 148 distinct entries", (len(edits), len({e["entry"] for e in edits})), (148, 148))
    eq("L-06 9 undocumented on 9 distinct entries", (len(undoc), len({e["entry"] for e in undoc})), (9, 9))
    u_entries = [by_id[e["entry"]] for e in undoc]
    l06_usd = money(sum(cost(x) for x in u_entries))
    eq("L-06 46.0 h", f"{sum(x['hours'] for x in u_entries):.1f}", "46.0")
    eq("L-06 exposure 4199.80", f"{l06_usd:.2f}", inj["S-L06"]["exposure_usd"])
    eq("L-06 = 4199.80", f"{l06_usd:.2f}", "4199.80")
    eq("L-06 six employees", len({x["emp"] for x in u_entries}), 6)
    eq("L-06 edit ids", sorted(e["id"] for e in undoc), inj["S-L06"]["edit_ids"])
    m6 = D(len(edits)) / D(len(entries))
    eq("M6 edit rate", f"{q(m6, '0.0001'):.4f}", gt["expected_metrics"]["M6"])
    check("M6 in Watch band (3.0%-5.0% region; low severity)", D("0.03") <= m6 < D("0.05"), f"{m6:.4f}")
    check("L-06 undocumented rate < 10%", D(len(undoc)) / D(len(edits)) < D("0.10"))
    last2 = sum(1 for e in edits if e["mod_on"].date() >= date(2026, 8, 30))
    check("L-06 edits in last 2 days < 50%", D(last2) / D(len(edits)) < D("0.50"),
          f"{D(last2) / D(len(edits)):.4f}")

    # ------------------------------------------------------------------ overlaps
    s05, s06, s09 = {x["id"] for x in l05}, {x["id"] for x in u_entries}, {x["id"] for x in l09}
    ov59, ov56 = sorted(s05 & s09), sorted(s05 & s06)
    eq("overlap L-05/L-09 = 1644.00", f"{money(sum(cost(by_id[i]) for i in ov59)):.2f}", "1644.00")
    eq("overlap L-05/L-06 = 970.20", f"{money(sum(cost(by_id[i]) for i in ov56)):.2f}", "970.20")
    eq("overlap entry ids match ground truth",
       [o["entry_ids"] for o in gt["overlaps"]], [ov59, ov56])
    check("no L-06/L-09 overlap", not (s06 & s09))

    # ------------------------------------------------------------------ M4
    late = sum(1 for x in entries
               if x["created"] - datetime.combine(x["date"] + timedelta(days=1), datetime.min.time())
               > timedelta(hours=72))
    eq("M4 late entries = 128", late, gt["expected_metric_detail"]["M4_late_entries"])
    check("M4 late rate < 5% (Ok band)", D(late) / D(len(entries)) < D("0.05"), f"{D(late) / D(len(entries)):.4f}")
    eq("M4 in ground truth", f"{q(D(late) / D(len(entries)), '0.0001'):.4f}", gt["expected_metrics"]["M4"])

    # ------------------------------------------------------------------ DQ-01
    roster_ids = {r["Employee #"] for r in roster}
    unmatched = sorted(e for e in emps if e not in roster_ids)
    pay_ids = {r["Associate ID"] for r in pay_rows}
    check("DQ-01 four active employees absent from roster", len(unmatched) == 4 and set(unmatched) <= pay_ids,
          str(unmatched))
    eq("DQ-01 unmatched IDs", unmatched, inj["S-DQ01"]["employees"])
    eq("roster rows 142", len(roster), 142)

    # ------------------------------------------------------------------ GL: labor vs non-labor
    cat = {r["Account"]: r["Category"] for r in acct_cat_rows}
    check("account_categories.csv: each GL account exactly once, closed vocabulary",
          len(cat) == len(acct_cat_rows) and set(cat) == {r["Account"] for r in gl_rows}
          and set(cat.values()) <= {"labor", "non_labor"})
    labor_accts = {"5010 Direct Labor", "6010 Overhead Labor", "6110 G&A Labor",
                   "6190 Payroll Accrual Adjustment", "6210 Fringe and Leave Labor"}
    check("existing labor accounts categorised labor; every other account non_labor",
          all((cat[a] == "labor") == (a in labor_accts) for a in cat))
    gl = [{"acct": r["Account"], "cls": r["Class"], "amt": D(r["Amount"]), "ct": r["Cost Type"],
           "pool": r["Pool"]} for r in gl_rows]
    payroll_gross = sum(D(r["Gross Pay"]) for r in pay_rows)
    gl_labor = sum(l["amt"] for l in gl if cat[l["acct"]] == "labor")
    eq("labor-only GL minus payroll gross = 2138.00", f"{gl_labor - payroll_gross:.2f}", "2138.00")
    eq("M2_variance_usd in ground truth", f"{gl_labor - payroll_gross:.2f}",
       gt["expected_metric_detail"]["M2_variance_usd"])
    m2 = (gl_labor - payroll_gross) / payroll_gross
    check("GL vs payroll variance 0.11% (< 0.5% Watch: L-03 consistent)",
          f"{q(m2, '0.0001'):.4f}" == "0.0011" and m2 < D("0.005"), f"{m2:.5f}")
    eq("M2 in ground truth", f"{q(m2, '0.0001'):.4f}", gt["expected_metrics"]["M2"])
    check("total (all-accounts) GL would NOT tie to payroll (non-labor must be excluded)",
          sum(l["amt"] for l in gl) - payroll_gross != D("2138.00"))

    # ------------------------------------------------------------------ rates / L-11
    rates = {r["Pool"]: r for r in rates_file}
    eq("rates file pools + provisional rates",
       {p: (r["Base Definition"], r["Provisional Rate"]) for p, r in rates.items()},
       {"fringe": ("direct_labor", "0.2800"), "overhead": ("direct_labor", "0.1800"),
        "ga": ("total_cost_input", "0.1000")})
    DL = sum(l["amt"] for l in gl if l["ct"] == "direct" and cat[l["acct"]] == "labor")
    DNL = sum(l["amt"] for l in gl if l["ct"] == "direct" and cat[l["acct"]] == "non_labor")
    eq("direct non-labor total = 405000.00", f"{DNL:.2f}", "405000.00")
    pools = {p: sum(l["amt"] for l in gl if l["ct"] == "indirect" and l["pool"] == p)
             for p in ("fringe", "overhead", "ga")}
    bases = {"fringe": DL, "overhead": DL, "ga": DL + DNL + pools["fringe"] + pools["overhead"]}
    prov = {p: D(rates[p]["Provisional Rate"]) for p in rates}
    target = {"fringe": D("0.2810"), "overhead": D("0.1790"), "ga": D("0.1080")}
    actual, m10 = {}, {}
    for p in ("fringe", "overhead", "ga"):
        actual[p] = q(pools[p] / bases[p], "0.0001")
        m10[p] = q((actual[p] - prov[p]) / prov[p], "0.0001")
        eq(f"{p}: actual rate = target", f"{actual[p]:.4f}", f"{target[p]:.4f}")
        rec = gt["dcaa_rates"][p]
        eq(f"{p}: pool_usd / base_usd in ground truth", (f"{pools[p]:.2f}", f"{bases[p]:.2f}"),
           (rec["pool_usd"], rec["base_usd"]))
        eq(f"{p}: actual_rate / provisional_rate / M10 in ground truth",
           (f"{actual[p]:.4f}", f"{prov[p]:.4f}", f"{m10[p]:.4f}"),
           (rec["actual_rate"], rec["provisional_rate"], rec["m10"]))
    eq("M10 fringe / overhead / ga", [f"{m10[p]:.4f}" for p in ("fringe", "overhead", "ga")],
       ["0.0036", "-0.0056", "0.0800"])
    check("fringe and overhead below Watch (|M10| <= 0.02): no L-11 finding",
          abs(m10["fringe"]) <= D("0.02") and abs(m10["overhead"]) <= D("0.02"))
    check("ga M10 > 0.05 (Exception)", m10["ga"] > D("0.05"))
    check("6190 Payroll Accrual Adjustment (2138.00) sits in the overhead pool",
          any(l["acct"].startswith("6190") and l["amt"] == D("2138.00") and l["pool"] == "overhead" for l in gl))
    check("G&A base is in [2.75M, 3.00M]; DL base in [1.60M, 1.78M]",
          D("2750000") <= bases["ga"] <= D("3000000") and D("1600000") <= DL <= D("1780000"),
          f"DL={DL} ga_base={bases['ga']}")
    l11_usd = money(abs(actual["ga"] - prov["ga"]) * bases["ga"])
    eq("L-11 exposure = 0.0080 x G&A base", f"{l11_usd:.2f}", inj["S-L11-GA"]["exposure_usd"])
    eq("L-11 fingerprint", inj["S-L11-GA"]["fingerprint"], "L-11|ga|2026-08")
    eq("L-11 direction / severity / basis", (inj["S-L11-GA"]["severity"], inj["S-L11-GA"]["exposure_basis"]),
       ("high", "rate_true_up"))
    check("L-11 fringe/overhead true-ups (Watch not reached) would not be raised",
          abs(m10["fringe"]) < D("0.02") and abs(m10["overhead"]) < D("0.02"))
    eq("L-11 G&A M10 in expected_metrics", f"{m10['ga']:.4f}", gt["expected_metrics"]["M10_ga"])

    # G&A drift series from the history file
    periods = hist["periods"]
    mm = hist["metrics"]
    series = {}
    ok_hist = True
    for p in ("fringe", "overhead", "ga"):
        s = []
        for per in periods:
            pl, bs = D(mm[f"M10.pool.{p}"][per]), D(mm[f"M10.base.{p}"][per])
            s.append(q(pl / bs, "0.0001"))
        series[p] = s
    want_rates = {
        "fringe": ["0.2792", "0.2805", "0.2811", "0.2796", "0.2803", "0.2809"],
        "overhead": ["0.1802", "0.1794", "0.1806", "0.1797", "0.1791", "0.1788"],
        "ga": ["0.1008", "0.0996", "0.1012", "0.1040", "0.1055", "0.1068"],
    }
    for p in series:
        eq(f"history {p} pool/base rates Feb-Jul", [f"{v:.4f}" for v in series[p]], want_rates[p])
    ga_m10 = [q((r - prov["ga"]) / prov["ga"], "0.0001") for r in series["ga"]] + [m10["ga"]]
    eq("G&A M10 drift series Feb..Aug", [f"{v:.4f}" for v in ga_m10],
       ["0.0080", "-0.0040", "0.0120", "0.0400", "0.0550", "0.0680", "0.0800"])
    run = 0
    for v in reversed(ga_m10):
        if abs(v) > D("0.02"):
            run += 1
        else:
            break
    eq("G&A consecutive Watch-or-worse periods ending Aug = 4 (May-Aug)", run, 4)
    eq("consecutive_watch_periods in ground truth", inj["S-L11-GA"]["consecutive_watch_periods"], run)
    check("history bases: fringe/overhead 1.60M-1.78M, G&A 2.75M-3.00M",
          all(D("1600000") <= D(mm[f"M10.base.{p}"][per]) <= D("1780000")
              for p in ("fringe", "overhead") for per in periods)
          and all(D("2750000") <= D(mm["M10.base.ga"][per]) <= D("3000000") for per in periods))
    check("history: original three keys untouched in shape (M5 mean 0.14, M6 0.033-0.038, M4 0.025-0.035)",
          sum(D(v) for v in mm["M5.company_window_share"].values()) / 6 == D("0.14")
          and all(D("0.033") <= D(v) <= D("0.038") for v in mm["M6.edit_rate"].values())
          and all(D("0.025") <= D(v) <= D("0.035") for v in mm["M4.late_rate"].values()))
    check("sources.json: rate_data present, absent == []",
          sources["sources"].get("rate_data", {}).get("file") == "meridian_rates_2026-08.csv"
          and sources["absent"] == [])

    # ------------------------------------------------------------------ L-08 (and noise band)
    hist_emps = chist["employees"]
    check("classification_history: 138 employees; the 4 DQ-01 employees have none",
          len(hist_emps) == 138 and not (set(unmatched) & set(hist_emps)))
    check("classification_history: 6 periods each", all(len(v) == 6 for v in hist_emps.values())
          and chist["periods"] == periods)
    now_dir, now_ind = defaultdict(lambda: D(0)), defaultdict(lambda: D(0))
    for x in entries:
        if ctype(x["code"]) == "direct":
            now_dir[x["emp"]] += x["hours"]
        elif pool(x["code"]) in ("overhead", "ga"):
            now_ind[x["emp"]] += x["hours"]
    flagged, band_bad, worst_share, worst_ind = [], [], D(0), D(0)
    for e, h in sorted(hist_emps.items()):
        shares, inds = [], []
        for per in periods:
            dr, ind = D(h[per]["direct_hours"]), D(h[per]["indirect_hours"])
            shares.append(ind / (dr + ind))
            inds.append(ind)
        mean_share, mean_ind = sum(shares) / 6, sum(inds) / 6
        s_now = now_ind[e] / (now_dir[e] + now_ind[e])
        d_share, d_ind = s_now - mean_share, now_ind[e] - mean_ind
        if d_share >= D("0.15") and d_ind >= D("16.0"):
            flagged.append((e, d_ind))
        else:
            worst_share, worst_ind = max(worst_share, abs(d_share)), max(worst_ind, abs(d_ind))
            if not (abs(d_share) < D("0.05") and abs(d_ind) < D("8.0")):
                band_bad.append(e)
    xid = inj["S-L08"]["employees"][0]
    eq("L-08 flags exactly one employee (X)", [f for f, _ in flagged], [xid])
    check("L-08 noise band holds for EVERY non-seeded employee (|dshare| < 0.05, |dindirect| < 8.0 h)",
          not band_bad, f"violations={band_bad} worst dshare={worst_share:.4f} worst dind={worst_ind}")
    eq("L-08 excess hours = 40.0", f"{dict(flagged)[xid]:.1f}", inj["S-L08"]["excess_hours"])
    eq("L-08 excess hours = 40.0 (literal)", f"{dict(flagged)[xid]:.1f}", "40.0")
    l08_usd = money(dict(flagged)[xid] * loaded[xid])
    eq("L-08 exposure = 40.0 x loaded rate", f"{l08_usd:.2f}", inj["S-L08"]["exposure_usd"])
    eq("L-08 fingerprint", inj["S-L08"]["fingerprint"], f"L-08|{xid}|2026-08")
    moved = inj["S-L08"]["entry_ids"]
    check("L-08 moved entries: 5 x 8.0 h on OH-100 for X, none edited, none in L-05/L-06/L-09",
          len(moved) == 5 and all(by_id[i]["code"] == "OH-100" and by_id[i]["hours"] == D("8.0")
                                  and by_id[i]["emp"] == xid for i in moved)
          and not (set(moved) & {e["entry"] for e in edits})
          and not (set(moved) & (s05 | s06 | s09)))
    xh = hist_emps[xid]
    check("L-08 X history: every month indirect == a, direct == D0 (pre-move August)",
          all(D(xh[p]["indirect_hours"]) == now_ind[xid] - D("40.0")
              and D(xh[p]["direct_hours"]) == now_dir[xid] + D("40.0") for p in periods))
    check("L-08 X is not any other seed and is on the roster",
          xid in roster_ids and xid not in {"E-0417", "E-0912", "E-1104", "E-0733", "E-0208", "E-0455"}
          and xid not in team and xid not in {x["emp"] for x in u_entries} and xid not in set(unmatched))
    check("L-08 shares: now - hist_mean >= 0.15",
          D(inj["S-L08"]["share_now"]) - D(inj["S-L08"]["share_hist_mean"]) >= D("0.15"))

    # ------------------------------------------------------------------ C-01 unallowable cost screening
    allow_rows = rd("account_allowability.csv")
    allow = {r["Account"]: (r["Allowability"], r["Citation"]) for r in allow_rows}
    check("account_allowability.csv: each GL account exactly once, closed vocabulary",
          len(allow) == len(allow_rows) and set(allow) == {r["Account"] for r in gl_rows}
          and {a for a, _ in allow.values()} <= {"allowable", "unallowable", "conditional"})
    c01_pool: dict[str, Decimal] = defaultdict(D)
    c01_cond = D(0)
    for r in gl_rows:
        cls = allow[r["Account"]][0]
        if r["Cost Type"] == "indirect" and cls == "unallowable":
            c01_pool[r["Pool"]] += D(r["Amount"])
        elif r["Cost Type"] == "indirect" and cls == "conditional":
            c01_cond += D(r["Amount"])
    eq("C-01 unallowable amount by pool", {p: f"{v:.2f}" for p, v in c01_pool.items()},
       {"ga": "23550.00", "overhead": "1850.00"})
    eq("C-01 exposure per pool matches the key",
       (f"{c01_pool['ga']:.2f}", f"{c01_pool['overhead']:.2f}"),
       (inj["S-C01-GA"]["exposure_usd"], inj["S-C01-OH"]["exposure_usd"]))
    eq("C-01 conditional (Gifts) amount is below the 0.5% materiality basis, so it is logged, not raised",
       (f"{c01_cond:.2f}", c01_cond < money(materiality)), ("4100.00", True))
    check("C-01 unallowable accounts carry a FAR 31.205 citation",
          all(allow[a][1].startswith("FAR 31.205-") for a in allow if allow[a][0] != "allowable"))
    check("C-01 carve-outs leave every pool total, and so every rate, unchanged (rates re-verified above)",
          all(rate_ok for rate_ok in [actual["fringe"] == D("0.2810"), actual["overhead"] == D("0.1790"),
                                      actual["ga"] == D("0.1080")]))
    eq("C-01 fingerprints", (inj["S-C01-GA"]["fingerprint"], inj["S-C01-OH"]["fingerprint"]),
       ("C-01|ga|2026-08", "C-01|overhead|2026-08"))
    c01_total = c01_pool["ga"] + c01_pool["overhead"]

    # ------------------------------------------------------------------ C-02 incurred cost submission deadline
    ics = rd("ics_submissions.csv")
    period_end = date(2026, 8, 31)
    overdue = []
    for r in ics:
        fy_end = date.fromisoformat(r["Fiscal Year End"])
        yy, mm = divmod(fy_end.year * 12 + fy_end.month - 1 + 6, 12)
        due = date(yy, mm + 1, calendar.monthrange(yy, mm + 1)[1])
        sub_on = date.fromisoformat(r["Submitted On"]) if r["Submitted On"] else None
        if due <= period_end and (sub_on is None or sub_on > due):
            overdue.append((r["Fiscal Year"], due.isoformat(), (period_end - due).days, sub_on is None))
    eq("C-02 exactly one overdue submission: FY2025, due 2026-06-30, 62 days, never submitted",
       overdue, [("FY2025", "2026-06-30", 62, True)])
    eq("C-02 key matches", (inj["S-C02"]["fiscal_year"], inj["S-C02"]["due_date"], inj["S-C02"]["days_overdue"],
                            inj["S-C02"]["exposure_usd"]), ("FY2025", "2026-06-30", 62, "0.00"))

    # ------------------------------------------------------------------ C-03 billing rate above a contractual ceiling
    ceil = {r["Pool"]: D(r["Ceiling Rate"]) for r in rates_file}
    above = {p: prov[p] - ceil[p] for p in prov if prov[p] > ceil[p]}
    eq("C-03 only overhead is billed above its ceiling", sorted(above), ["overhead"])
    c03_usd = money(above["overhead"] * bases["overhead"])
    eq("C-03 exposure = (0.1800 - 0.1750) x direct labor", f"{c03_usd:.2f}", inj["S-C03"]["exposure_usd"])
    eq("C-03 = 0.0050 x base", f"{money(D('0.0050') * bases['overhead']):.2f}", f"{c03_usd:.2f}")
    check("C-03 context: overhead ACTUAL rate is also above its ceiling; no other pool crosses either",
          actual["overhead"] > ceil["overhead"] and actual["ga"] <= ceil["ga"] and actual["fringe"] <= ceil["fringe"]
          and prov["ga"] <= ceil["ga"] and prov["fringe"] <= ceil["fringe"])
    eq("C-03 fingerprint", inj["S-C03"]["fingerprint"], "C-03|overhead|2026-08")

    # ------------------------------------------------------------------ M13 / findings roll-up
    dcaa_extra = c01_total + c03_usd
    non_entry = l01_usd + sum(D(inj[s]["exposure_usd"]) for s in ("S-L02a", "S-L02b", "S-L02c")) + l11_usd + dcaa_extra
    union = s05 | s06 | s09 | set(moved)
    union_usd = money(sum(cost(by_id[i]) for i in union))
    gross = l01_usd + l05_usd + l09_usd + l06_usd + l08_usd + l11_usd + dcaa_extra + \
        sum(D(inj[s]["exposure_usd"]) for s in ("S-L02a", "S-L02b", "S-L02c"))
    m13 = non_entry + union_usd
    eq("M13 (expected_total_exposure_usd)", f"{m13:.2f}", gt["expected_total_exposure_usd"])
    eq("M13 gross before dedup", f"{gross:.2f}", gt["expected_gross_exposure_before_dedup_usd"])
    eq("M13 non-entry / entry-union parts", (f"{non_entry:.2f}", f"{union_usd:.2f}"),
       (gt["expected_non_entry_exposure_usd"], gt["expected_entry_union_exposure_usd"]))
    eq("M13 = pre-DCAA labor total 41298.93 + L-08 + L-11 + C-01 + C-02 (0.00) + C-03",
       f"{D('41298.93') + l08_usd + l11_usd + c01_total + c03_usd:.2f}", f"{m13:.2f}")
    eq("DCAA-domain total = L-08 + L-11 + C-01 + C-03",
       f"{l08_usd + l11_usd + c01_total + c03_usd:.2f}", gt["dcaa_domain_total_exposure_usd"])
    eq("labor-domain total (L-01,02,05,06,09) unchanged = 41298.93",
       f"{(l01_usd + sum(D(inj[s]['exposure_usd']) for s in ('S-L02a', 'S-L02b', 'S-L02c')) + money(sum(cost(by_id[i]) for i in (s05 | s06 | s09)))):.2f}",
       "41298.93")
    eq("gross - overlaps = M13",
       f"{gross - money(sum(cost(by_id[i]) for i in ov59)) - money(sum(cost(by_id[i]) for i in ov56)):.2f}",
       f"{m13:.2f}")
    sev = defaultdict(int)
    for i in gt["injected"]:
        sev[i["severity"]] += 1
    eq("expected_findings = number of injected seeds = 14", (gt["expected_findings"], len(gt["injected"])), (14, 14))
    eq("expected_by_severity", dict(sev), gt["expected_by_severity"])
    eq("expected_by_severity = 8/5/1", gt["expected_by_severity"], {"high": 8, "medium": 5, "low": 1})
    eq("fingerprints",
       sorted(i["fingerprint"] for i in gt["injected"]),
       sorted(["L-01|E-0417|C-8841", "L-05|C-7302", "L-09|C-7302", "L-02|E-0912|2026-08-B",
               "L-02|E-1104|2026-08-A", "L-02|E-0733|2026-08-B", "L-06|2026-08", "DQ-01|2026-08",
               "L-11|ga|2026-08", f"L-08|{xid}|2026-08", "C-01|ga|2026-08", "C-01|overhead|2026-08",
               "C-02|FY2025|2026-08", "C-03|overhead|2026-08"]))
    eq("expected_not_evaluated / expected_consistent", (gt["expected_not_evaluated"], gt["expected_consistent"]),
       ([], ["L-03"]))
    eq("domains", gt["domains"],
       {"labor": {"rules": ["L-01", "L-02", "L-03", "L-05", "L-06", "L-09"], "coverage": [6, 6]},
        "dcaa_cost_accounting": {"rules": ["L-08", "L-11", "C-01", "C-02", "C-03"], "coverage": [5, 5]}})
    check("original labor exposures unchanged in ground truth",
          [inj[s]["exposure_usd"] for s in ("S-L01", "S-L05", "S-L09", "S-L06", "S-DQ01")]
          == ["3504.00", "27518.40", "5852.40", "4199.80", "0.00"])

    bad = [r for r in RESULTS if not r[1]]
    print()
    print(f"{len(RESULTS) - len(bad)} passed, {len(bad)} failed, {len(RESULTS)} checks")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
