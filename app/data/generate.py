#!/usr/bin/env python3
"""Deterministic synthetic-data generator: Meridian Systems, August 2026.

Binding spec: contracts/DATA_SPEC.md.  Run from the app/ directory:

    .venv/bin/python -m data.generate

Design notes
* One RNG (random.Random(20260920)); no wall clock; output is byte-identical.
* All hours / money are exact Decimals.
* Seeded conditions are CONSTRUCTED (patterns are forced, the L-05 exposure is
  closed by a small exact solver), not found by re-rolling seeds.
* The ground truth is computed from this generator's own arithmetic; nothing
  here imports from engine/.
"""

from __future__ import annotations

import calendar
import csv
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 20260920
OUT_DIR = Path(__file__).resolve().parent / "out"
PERIOD = "2026-08"
D = Decimal

TARGET_ENTRIES = 4120
TARGET_LATE = 128
TARGET_EDITS = 148
EDITS_LAST2 = 61  # edits on Aug 31 (last 2 days of period) -> 41.2%
PAYROLL_TOTAL = D("1986240.00")
GL_VARIANCE = D("2138.00")


# ----------------------------------------------------------------------------
# small helpers
# ----------------------------------------------------------------------------
def q2(x: Decimal) -> Decimal:
    return x.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def f1(x: Decimal) -> str:
    return f"{x:.1f}"


def f2(x: Decimal) -> str:
    return f"{x:.2f}"


def iso_dt(x: datetime) -> str:
    return x.strftime("%Y-%m-%dT%H:%M:%S")


AUG = [date(2026, 8, i) for i in range(1, 32)]
WORKDAYS = [d for d in AUG if d.weekday() < 5]
assert len(WORKDAYS) == 21 and WORKDAYS[0] == date(2026, 8, 3) and WORKDAYS[-1] == date(2026, 8, 31)
POP_END_7302 = date(2026, 8, 15)
PERIOD_END = date(2026, 8, 31)


def pay_period(d: date) -> str:
    return "2026-08-A" if d.day <= 15 else "2026-08-B"


def add_bd(d: date, n: int) -> date:
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def next_bd(d: date) -> date:
    return add_bd(d, 1)


def rand_dt(rng: random.Random, d: date, start_min: int, end_min: int) -> datetime:
    """Uniform random local time on day d between minute-of-day start..end."""
    m = rng.randint(start_min, end_min - 1)
    s = rng.randint(0, 59)
    return datetime(d.year, d.month, d.day) + timedelta(minutes=m, seconds=s)


def hm(h: int, m: int = 0) -> int:
    return h * 60 + m


# ----------------------------------------------------------------------------
# reference data
# ----------------------------------------------------------------------------
# HRIS title -> (contract category, department, direct-code kind)
TITLES = {
    "Data Engineer": ("Data Engineer II", "Data Engineering", "DEV"),
    "Sr. Data Engineer": ("Data Engineer III", "Data Engineering", "DEV"),
    "Software Engineer": ("Software Engineer II", "Software Engineering", "DEV"),
    "Sr. Software Engineer": ("Software Engineer III", "Software Engineering", "DEV"),
    "Cloud Engineer": ("Cloud Engineer II", "Cloud & Infrastructure", "DEV"),
    "DevOps Engineer": ("DevOps Engineer II", "Cloud & Infrastructure", "DEV"),
    "Systems Analyst": ("Systems Analyst II", "Systems Engineering", "DEV"),
    "Sr. Systems Analyst": ("Systems Analyst III", "Systems Engineering", "DEV"),
    "Cybersecurity Analyst": ("Cybersecurity Analyst II", "Cybersecurity", "DEV"),
    "Project Manager": ("Project Manager II", "Program Management", "PM"),
    "Business Analyst": ("Business Analyst II", "Business Analysis", "PM"),
    # held only by the 4 terminated roster rows (crosswalk completeness)
    "Sr. Cybersecurity Analyst": ("Cybersecurity Analyst III", "Cybersecurity", "DEV"),
    "Program Manager": ("Project Manager III", "Program Management", "PM"),
    "QA Engineer": ("QA Engineer II", "Software Engineering", "DEV"),
    "Technical Writer": ("Technical Writer II", "Business Analysis", "PM"),
    "Database Administrator": ("Database Administrator II", "Data Engineering", "DEV"),
}
ACTIVE_TITLES = list(TITLES)[:11]
RATE_BANDS = {  # loaded-rate band per title ($/h), tenths
    "Data Engineer": (84, 98), "Sr. Data Engineer": (100, 116),
    "Software Engineer": (82, 96), "Sr. Software Engineer": (98, 114),
    "Cloud Engineer": (86, 100), "DevOps Engineer": (84, 98),
    "Systems Analyst": (72, 86), "Sr. Systems Analyst": (88, 102),
    "Cybersecurity Analyst": (90, 106), "Project Manager": (96, 112),
    "Business Analyst": (74, 88),
}
TITLE_WEIGHTS = {
    "Data Engineer": 16, "Sr. Data Engineer": 9, "Software Engineer": 20,
    "Sr. Software Engineer": 12, "Cloud Engineer": 9, "DevOps Engineer": 8,
    "Systems Analyst": 10, "Sr. Systems Analyst": 6, "Cybersecurity Analyst": 6,
    "Project Manager": 5, "Business Analyst": 7,
}

C8841_CATEGORIES = [
    ("Data Engineer II", "142.00", "BS + 3 years"),
    ("Data Engineer III", "178.50", "BS + 7 years"),
    ("Software Engineer II", "138.00", "BS + 3 years"),
    ("Software Engineer III", "171.00", "BS + 7 years"),
    ("Cloud Engineer II", "149.50", "BS + 3 years"),
    ("DevOps Engineer II", "144.00", "BS + 3 years"),
    ("Systems Analyst II", "121.00", "BS + 2 years"),
    ("Systems Analyst III", "152.00", "BS + 6 years"),
    ("Cybersecurity Analyst II", "158.00", "BS + 4 years + Security+"),
    ("Project Manager II", "168.00", "BS + 8 years + PMP"),
    ("Business Analyst II", "112.50", "BS + 3 years"),
]
C7302_CATEGORIES = [
    ("Data Engineer II", "139.00", "BS + 3 years"),
    ("Data Engineer III", "172.50", "BS + 7 years"),
    ("Software Engineer II", "134.00", "BS + 3 years"),
    ("Software Engineer III", "165.00", "BS + 7 years"),
    ("Systems Analyst II", "117.00", "BS + 2 years"),
    ("Project Manager II", "161.00", "BS + 8 years + PMP"),
    ("Cloud Engineer II", "145.00", "BS + 3 years"),
    ("Cybersecurity Analyst II", "154.00", "BS + 4 years + Security+"),
]
CHARGE_CODES = [  # Charge, Contract, Cost Type, Pool
    ("C8841-DEV", "C-8841", "direct", ""),
    ("C8841-PM", "C-8841", "direct", ""),
    ("C7302-DEV", "C-7302", "direct", ""),
    ("C7302-PM", "C-7302", "direct", ""),
    ("OH-100", "", "indirect", "overhead"),
    ("GA-200", "", "indirect", "ga"),
    ("FR-050", "", "indirect", "fringe"),
    ("BP-300", "", "indirect", "ga"),
    ("LV-010", "", "indirect", "fringe"),
]
CODE_INFO = {c[0]: c for c in CHARGE_CODES}

FIRST = ["Aiden", "Priya", "Marcus", "Elena", "Jamal", "Sofia", "Wei", "Hannah", "Diego", "Aisha",
         "Nolan", "Mei", "Omar", "Grace", "Tobias", "Lena", "Rohan", "Camila", "Ethan", "Naomi",
         "Victor", "Ingrid", "Samir", "Chloe", "Andre", "Yuki", "Felix", "Bianca", "Kwame", "Tessa",
         "Julian", "Farah", "Owen", "Lucia", "Dmitri", "Amara", "Caleb", "Zara", "Hugo", "Renee"]
LAST = ["Alvarez", "Brennan", "Chaudhry", "Donovan", "Estrada", "Fitzgerald", "Gallagher", "Hartmann",
        "Ibrahim", "Jensen", "Kowalski", "Lindqvist", "Morales", "Nakamura", "Okafor", "Petrov",
        "Quintero", "Rasmussen", "Sandoval", "Thackeray", "Underwood", "Varga", "Whitfield", "Xu",
        "Yamamoto", "Zielinski", "Abernathy", "Bosworth", "Castellano", "Delacroix", "Eastwood",
        "Fontaine", "Grimaldi", "Holloway", "Iverson", "Jablonski", "Kessler", "Lombardi", "Mercer", "Novak"]


def rate(s: str) -> Decimal:
    return D(s)


# ----------------------------------------------------------------------------
# people
# ----------------------------------------------------------------------------
REQUIRED_IDS = ["E-0417", "E-0912", "E-1104", "E-0733", "E-0455", "E-0208"]
TEAM_ROLES = ["E-0455", "X", "Y", "T1", "T2", "T3", "T4", "T5", "T6"]  # X/Y/T* resolved to ids
TEAM_SPEC = {  # role -> (title, loaded rate)
    "E-0455": ("Sr. Software Engineer", "102.75"),
    "X": ("Data Engineer", "94.50"),
    "Y": ("Systems Analyst", "88.20"),
    "T1": ("Cloud Engineer", "88.20"),
    "T2": ("Software Engineer", "88.20"),
    "T3": ("Sr. Data Engineer", "108.30"),
    "T4": ("Project Manager", "97.60"),
    "T5": ("Cybersecurity Analyst", "91.60"),
    "T6": ("Software Engineer", "85.40"),
}
L06_SPEC = {  # role -> (title, rate)
    "X1": ("Data Engineer", "96.40"),
    "X2": ("Cloud Engineer", "96.40"),
    "X3": ("Software Engineer", "94.50"),
    "X4": ("DevOps Engineer", "81.20"),
}
REQUIRED_SPEC = {
    "E-0417": ("Data Engineer", "94.00"),
    "E-0912": ("Software Engineer", "96.40"),
    "E-1104": ("Systems Analyst", "88.20"),
    "E-0733": ("Sr. Systems Analyst", "102.75"),
    "E-0208": ("Business Analyst", "78.40"),
}


def build_people(rng: random.Random):
    """Returns (employees dict id->rec, terminated list, roles dict)."""
    pool = [i for i in range(100, 2000) if f"E-{i:04d}" not in REQUIRED_IDS]
    extra = rng.sample(pool, 136)
    ids = sorted(REQUIRED_IDS + [f"E-{i:04d}" for i in extra])
    assert len(ids) == 142 and len(set(ids)) == 142
    free = [i for i in ids if i not in REQUIRED_IDS]  # sorted, 136

    roles: dict[str, str] = {}  # role name -> employee id
    picks = rng.sample(free, 8 + 4 + 4 + 12)
    team_other = picks[:8]
    l06 = picks[8:12]
    newbies = picks[12:16]
    sups = picks[16:28]
    roles["E-0455"] = "E-0455"
    for r, i in zip(["X", "Y", "T1", "T2", "T3", "T4", "T5", "T6"], team_other):
        roles[r] = i
    for r, i in zip(["X1", "X2", "X3", "X4"], l06):
        roles[r] = i
    for n, i in enumerate(newbies, 1):
        roles[f"N{n}"] = i
    for n, i in enumerate(sups, 1):
        roles[f"S{n:02d}"] = i
    for r in REQUIRED_IDS:
        roles.setdefault(r, r)

    used_specs: dict[str, tuple[str, str]] = {}
    for r, (t, rt) in TEAM_SPEC.items():
        used_specs[roles[r]] = (t, rt)
    for r, (t, rt) in L06_SPEC.items():
        used_specs[roles[r]] = (t, rt)
    for eid, (t, rt) in REQUIRED_SPEC.items():
        used_specs[eid] = (t, rt)

    wl = list(TITLE_WEIGHTS)
    ww = [TITLE_WEIGHTS[t] for t in wl]
    team_ids = {roles[r] for r in TEAM_ROLES}
    newbie_ids = set(newbies)
    sup_ids = sups
    names_used = set()
    employees = {}
    for eid in ids:
        if eid in used_specs:
            title, rt = used_specs[eid]
            loaded = D(rt)
        else:
            title = rng.choices(wl, ww)[0]
            lo, hi = RATE_BANDS[title]
            loaded = D(rng.randint(lo * 10, hi * 10)) / D(10)
        while True:
            nm = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if nm not in names_used:
                names_used.add(nm)
                break
        exempt = rng.random() < 0.40
        if eid in newbie_ids:
            exempt = True  # 9-hour days: keep them exempt
        cat, dept, kind = TITLES[title]
        employees[eid] = {
            "id": eid, "name": nm, "title": title, "category": cat, "dept": dept,
            "kind": kind, "loaded": loaded.quantize(D("0.01")), "exempt": exempt,
            "group": "team" if eid in team_ids else "c8841",
            "in_roster": eid not in newbie_ids,
            "hire": None, "sup": None, "wage": None,
        }
    # supervisors: hire dates, approver chain
    director = roles["S01"]
    for eid in ids:
        e = employees[eid]
        yrs = rng.randint(1, 11)
        e["hire"] = date(2026, 8, 1) - timedelta(days=yrs * 365 + rng.randint(0, 364))
    for eid in newbie_ids:
        employees[eid]["hire"] = date(2026, 8, 4)
    # supervisor assignment (round-robin over sorted ids, never self)
    rest = [i for i in ids if i not in sup_ids]
    for n, eid in enumerate(rest):
        employees[eid]["sup"] = sup_ids[n % len(sup_ids)]
    for eid in sup_ids:
        employees[eid]["sup"] = director if eid != director else roles["S02"]
    for eid in sup_ids:
        employees[eid]["is_sup"] = True
    # keep the team under a single supervisor for a realistic cost centre
    for eid in team_ids:
        employees[eid]["sup"] = roles["S03"]

    # terminated roster-only people (no August time, no payroll)
    terminated = []
    used_ids = set(ids)
    tpool = [i for i in range(2000, 2400)]
    tids = rng.sample(tpool, 4)
    tspec = [("Sr. Cybersecurity Analyst", date(2026, 6, 12)), ("QA Engineer", date(2026, 7, 3)),
             ("Technical Writer", date(2026, 5, 22)), ("Database Administrator", date(2026, 7, 17))]
    for i, (title, term) in zip(tids, tspec):
        eid = f"E-{i:04d}"
        assert eid not in used_ids
        while True:
            nm = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
            if nm not in names_used:
                names_used.add(nm)
                break
        terminated.append({
            "id": eid, "name": nm, "title": title, "dept": TITLES[title][1],
            "hire": date(2026, 8, 1) - timedelta(days=rng.randint(3, 9) * 365),
            "term": term, "exempt": rng.random() < 0.4, "sup": sup_ids[rng.randint(0, 11)],
        })
    return employees, terminated, roles, ids, sup_ids


# ----------------------------------------------------------------------------
# day plans
# ----------------------------------------------------------------------------
INDIRECT_CODES = ["OH-100", "GA-200", "FR-050", "BP-300", "LV-010"]
INDIRECT_WEIGHTS = [30, 25, 15, 20, 10]


def mk(e: dict, d: date, hours, code: str, plc: str | None = None, tags=()) -> dict:
    return {"emp": e["id"], "date": d, "hours": D(hours), "code": code,
            "plc": plc or e["category"], "tags": set(tags)}


def prim(e: dict, contract: str) -> str:
    return f"{'C8841' if contract == 'C-8841' else 'C7302'}-{e['kind']}"


def alt(e: dict, contract: str) -> str:
    other = "PM" if e["kind"] == "DEV" else "DEV"
    return f"{'C8841' if contract == 'C-8841' else 'C7302'}-{other}"


def second_code(rng: random.Random, e: dict, contract: str) -> str:
    if rng.random() < 0.45:
        return alt(e, contract)
    return rng.choices(INDIRECT_CODES, INDIRECT_WEIGHTS)[0]


def split_pair(rng, e, d, contract, first_h=None, second_code_override=None):
    a = first_h if first_h is not None else rng.choice([2, 3, 4, 4, 5, 6])
    c2 = second_code_override or second_code(rng, e, contract)
    pair = [mk(e, d, a, prim(e, contract)), mk(e, d, 8 - a, c2)]
    if rng.random() < 0.5:
        pair.reverse()
    return pair


def free_day(rng, e, d, contract="C-8841", split=None):
    if split is None:
        split = rng.random() < 0.33
    if split:
        return split_pair(rng, e, d, contract)
    return [mk(e, d, 8, prim(e, contract))]


def indirect_day(rng, e, d):
    """Team members after PoP end: bench / proposal support (no C-7302 charges)."""
    r = rng.random()
    codes = ["OH-100", "BP-300", "GA-200"]
    if r < 0.6:
        return [mk(e, d, 8, rng.choices(codes, [45, 40, 15])[0])]
    c1 = rng.choices(codes, [45, 40, 15])[0]
    c2 = rng.choices(codes, [45, 40, 15])[0]
    return [mk(e, d, 4, c1), mk(e, d, 4, c2)]


def build_plans(rng, employees, roles, ids):
    plans: dict[tuple[str, date], list[dict]] = {}
    locked: set[tuple[str, date]] = set()
    team_ids = [roles[r] for r in TEAM_ROLES]
    team_set = set(team_ids)
    newbies = [roles[f"N{i}"] for i in range(1, 5)]

    # ---- non-team employees: free days --------------------------------------
    for eid in ids:
        if eid in team_set:
            continue
        e = employees[eid]
        if eid in newbies:
            days = [d for d in WORKDAYS if d >= date(2026, 8, 4)]
            a_days = [d for d in days if d.day <= 15]
            short = rng.choice(a_days)
            for d in days:
                h = 8 if (d.day > 15 or d == short) else 9
                plans[(eid, d)] = [mk(e, d, h, prim(e, "C-8841"))]
                locked.add((eid, d))
            continue
        for d in WORKDAYS:
            plans[(eid, d)] = free_day(rng, e, d)

    # ---- L-01: E-0417, 12 x 8.0 h billed as Data Engineer III --------------------
    e = employees["E-0417"]
    l01_days = sorted(rng.sample(WORKDAYS, 12))
    for d in l01_days:
        plans[("E-0417", d)] = [mk(e, d, 8, "C8841-DEV", plc="Data Engineer III", tags={"l01"})]
        locked.add(("E-0417", d))

    # ---- L-06 forced entries for X1..X4 (T1/T2 are handled with the team) ------------
    l06_targets = [("X1", 4), ("X1", 4), ("X2", 4), ("X2", 3), ("X3", 6), ("X3", 6), ("X4", 8)]
    used_days: dict[str, set] = defaultdict(set)
    for role, h in l06_targets:
        eid = roles[role]
        e = employees[eid]
        cands = [d for d in WORKDAYS if d <= date(2026, 8, 20) and (eid, d) not in locked]
        d = rng.choice(cands)
        if h == 8:
            ents = [mk(e, d, 8, prim(e, "C-8841"), tags={"l06"})]
        else:
            ents = [mk(e, d, h, prim(e, "C-8841"), tags={"l06"}),
                    mk(e, d, 8 - h, alt(e, "C-8841"))]
            if rng.random() < 0.5:
                ents.reverse()
        plans[(eid, d)] = ents
        locked.add((eid, d))

    # ---- adjust total entry count (team fixed below, added after) ------------------
    return plans, locked


def build_team(rng, employees, roles, plans, locked):
    """C-7302 team: 142 entries in period A (all C-7302), 8 L-09 entries in B.
    Solves the L-05 flagged exposure to exactly 27,518.40."""
    tm = [roles[r] for r in TEAM_ROLES]
    a_days = [d for d in WORKDAYS if d.day <= 14]
    elig_days = [d for d in a_days if d.weekday() >= 2]  # Wed/Thu/Fri
    assert len(a_days) == 10 and len(elig_days) == 6

    forced = {}  # (eid, d) -> pattern description
    t1, t2 = roles["T1"], roles["T2"]
    used = set()

    def pick_day(eid):
        c = [d for d in elig_days if (eid, d) not in used]
        d = rng.choice(c)
        used.add((eid, d))
        return d

    d_t1 = pick_day(t1)
    d_t2 = pick_day(t2)
    closers = rng.sample([m for m in tm if m not in (t1, t2)], 3)
    closer_days = [pick_day(m) for m in closers]

    patterns = {}  # (eid, d) -> bool split
    patterns[(t1, d_t1)] = True
    patterns[(t2, d_t2)] = True
    for m, d in zip(closers, closer_days):
        patterns[(m, d)] = True
    rest_elig = [(m, d) for m in tm for d in elig_days if (m, d) not in used]
    rest_non = [(m, d) for m in tm for d in a_days if d not in elig_days]
    rng.shuffle(rest_elig)
    rng.shuffle(rest_non)
    assert len(rest_elig) == 49 and len(rest_non) == 36
    for k, key in enumerate(rest_elig):
        patterns[key] = k < 40
    for k, key in enumerate(rest_non):
        patterns[key] = k < 7
    assert sum(1 for v in patterns.values() if v) == 52 and len(patterns) == 90

    for m in tm:
        e = employees[m]
        for d in a_days:
            key = (m, d)
            if key == (t1, d_t1):
                pair = [mk(e, d, 6, prim(e, "C-7302"), tags={"l06"}),
                        mk(e, d, 2, alt(e, "C-7302"))]
            elif key == (t2, d_t2):
                pair = [mk(e, d, 5, prim(e, "C-7302"), tags={"l06"}),
                        mk(e, d, 3, alt(e, "C-7302"))]
            elif patterns[key]:
                a = rng.choice([2, 3, 4, 4, 5, 6])
                c2 = alt(e, "C-7302") if rng.random() < 0.4 else prim(e, "C-7302")
                pair = [mk(e, d, a, prim(e, "C-7302")), mk(e, d, 8 - a, c2)]
            else:
                pair = [mk(e, d, 8, prim(e, "C-7302"))]
            if len(pair) == 2 and key not in [(t1, d_t1), (t2, d_t2)] and rng.random() < 0.5:
                pair.reverse()
            plans[key] = pair
            locked.add(key)

    # ---- period B: 8 L-09 entries, everything else bench / proposal support ------
    l09 = {
        roles["E-0455"]: [(date(2026, 8, 20), 8), (date(2026, 8, 21), 8)],
        roles["X"]: [(date(2026, 8, 17), 8), (date(2026, 8, 18), 8), (date(2026, 8, 19), 8)],
        roles["Y"]: [(date(2026, 8, 17), 8), (date(2026, 8, 18), 8), (date(2026, 8, 19), 6)],
    }
    for m in tm:
        e = employees[m]
        for d in [d for d in WORKDAYS if d.day > 15]:
            key = (m, d)
            spec = dict(l09.get(m, []))
            if d in spec:
                h = spec[d]
                ents = [mk(e, d, h, prim(e, "C-7302"), tags={"l09"})]
                if h < 8:
                    ents.append(mk(e, d, 8 - h, "OH-100"))
                plans[key] = ents
            else:
                plans[key] = indirect_day(rng, e, d)
            locked.add(key)

    # ---- L-05: choose flagged set & close the exposure exactly ----------------------
    def cents(eid):
        return int(employees[eid]["loaded"] * 100)

    rate_of = {m: cents(m) for m in tm}
    forced_flag = [plans[(t1, d_t1)][0 if plans[(t1, d_t1)][0]["hours"] == 6 else 1],
                   plans[(t2, d_t2)][0 if plans[(t2, d_t2)][0]["hours"] == 5 else 1]]
    for x in forced_flag:
        assert x["hours"] in (D(6), D(5))
    closer_entries = []  # (h_entry, complement_entry, member)
    for m, d in zip(closers, closer_days):
        ents = plans[(m, d)]
        closer_entries.append((ents[0], ents[1], m))
    closer_ids = {id(x) for tup in closer_entries for x in tup[:2]}
    forced_ids = {id(x) for x in forced_flag}
    pool = []
    for m in tm:
        for d in elig_days:
            for x in plans[(m, d)]:
                if id(x) not in closer_ids and id(x) not in forced_ids:
                    pool.append(x)
    assert len(pool) >= 80, len(pool)

    TARGET_L05 = 2751840  # cents, 27,518.40
    l09_e0455 = 164400  # 16.0 h * 102.75
    need_other = TARGET_L05 - l09_e0455  # 25,874.40 -> all flagged except E-0455's L-09 pair
    forced_cents = sum(int(x["hours"]) * rate_of[x["emp"]] for x in forced_flag)
    r1, r2, r3 = (rate_of[c[2]] for c in closer_entries)
    solution = None
    tries = 0
    while solution is None:
        tries += 1
        assert tries < 2_000_000, "L-05 solver failed"
        sel = rng.sample(pool, 50)
        s50 = sum(int(x["hours"]) * rate_of[x["emp"]] for x in sel)
        R = need_other - forced_cents - s50
        if R < 3 * min(r1, r2, r3) or R > 21 * max(r1, r2, r3):
            continue
        cover = defaultdict(int)
        for x in sel:
            cover[x["emp"]] += 1
        for x in forced_flag:
            cover[x["emp"]] += 1
        for tup in closer_entries:
            cover[tup[2]] += 1
        if any(cover[m] < 2 for m in tm):
            continue
        for h1 in range(1, 8):
            for h2 in range(1, 8):
                rem = R - h1 * r1 - h2 * r2
                if rem > 0 and rem % r3 == 0 and 1 <= rem // r3 <= 7:
                    solution = (sel, (h1, h2, rem // r3))
                    break
            if solution:
                break
    sel, hs = solution
    for (he, ce, m), h in zip(closer_entries, hs):
        he["hours"] = D(h)
        ce["hours"] = D(8 - h)
        he["tags"].add("l05")
    for x in sel:
        x["tags"].add("l05")
    for x in forced_flag:
        x["tags"].add("l05")
    for m in [roles["E-0455"]]:
        for d in [date(2026, 8, 20), date(2026, 8, 21)]:
            plans[(m, d)][0]["tags"].add("l05")
    return {"solver_tries": tries}


# ----------------------------------------------------------------------------
# count adjustment, hour gaps
# ----------------------------------------------------------------------------
def adjust_count(rng, employees, plans, locked, target):
    total = sum(len(v) for v in plans.values())
    free_keys = sorted(k for k in plans if k not in locked)
    if total < target:
        singles = [k for k in free_keys if len(plans[k]) == 1]
        rng.shuffle(singles)
        for k in singles:
            if total == target:
                break
            e = employees[k[0]]
            ent = plans[k][0]
            assert ent["hours"] == D(8)
            a = rng.choice([2, 3, 4, 4, 5, 6])
            first = mk(e, k[1], a, ent["code"])
            second = mk(e, k[1], 8 - a, second_code(rng, e, "C-8841"))
            plans[k] = [first, second] if rng.random() < 0.5 else [second, first]
            total += 1
    elif total > target:
        doubles = [k for k in free_keys if len(plans[k]) == 2]
        rng.shuffle(doubles)
        for k in doubles:
            if total == target:
                break
            direct = [x for x in plans[k] if CODE_INFO[x["code"]][2] == "direct"]
            keep = direct[0] if direct else plans[k][0]
            keep["hours"] = D(8)
            plans[k] = [keep]
            total -= 1
    assert total == target, (total, target)


def gap_values(rng):
    """Noise |gap| multiset (half-hour units) with exact sum 312.5 h including E-0208's 1.5."""
    k = 136
    target_half = 625 - 3  # minus E-0208's 1.5 h
    weights = [1, 2, 3, 3, 3, 3, 2]
    vals = rng.choices(range(1, 8), weights, k=k - 1)
    while sum(vals) != target_half:
        i = rng.randrange(len(vals))
        if sum(vals) < target_half and vals[i] < 7:
            vals[i] += 1
        elif sum(vals) > target_half and vals[i] > 1:
            vals[i] -= 1
    return vals


def apply_gap(rng, plans, locked, eid, period, gap: Decimal):
    days = [d for d in WORKDAYS if pay_period(d) == period and (eid, d) in plans
            and (eid, d) not in locked]
    n = int(abs(gap) / D("0.5"))
    sign = 1 if gap > 0 else -1
    chunks = []
    rem = n
    while rem:
        c = min(rem, 3 if rng.random() < 0.6 else 2)
        chunks.append(c)
        rem -= c
    if len(chunks) > len(days):
        chunks = []
        rem = n
        while rem:
            c = min(rem, 3)
            chunks.append(c)
            rem -= c
    assert len(chunks) <= len(days)
    for c, d in zip(chunks, rng.sample(days, len(chunks))):
        ents = plans[(eid, d)]
        big = max(ents, key=lambda x: x["hours"])
        nh = big["hours"] + sign * D("0.5") * c
        assert nh >= D("1.0")
        big["hours"] = nh
        big["tags"].add("gap")


def apply_all_gaps(rng, employees, roles, ids, plans, locked):
    team = {roles[r] for r in TEAM_ROLES}
    special = {roles[r] for r in ["X1", "X2", "X3", "X4", "N1", "N2", "N3", "N4"]} | {"E-0417"}
    seeded = {("E-0912", "2026-08-B"): D("12.0"), ("E-1104", "2026-08-A"): D("8.0"),
              ("E-0733", "2026-08-B"): D("-9.5")}
    gaps = dict(seeded)
    gaps[("E-0208", "2026-08-A")] = D("1.5")
    excluded = team | special | {"E-0912", "E-1104", "E-0733", "E-0208"}
    cand = [(i, p) for i in ids if i not in excluded for p in ("2026-08-A", "2026-08-B")]
    vals = gap_values(rng)
    chosen = rng.sample(cand, len(vals))
    for (i, p), v in zip(chosen, vals):
        gaps[(i, p)] = D(v) / D(2) * (1 if rng.random() < 0.5 else -1)
    for (i, p) in sorted(gaps):
        apply_gap(rng, plans, locked, i, p, gaps[(i, p)])
    return gaps


# ----------------------------------------------------------------------------
# entry timing
# ----------------------------------------------------------------------------
def assign_created(rng, employees, roles, entries):
    team = {roles[r] for r in TEAM_ROLES}
    friday_of = {5: 7, 6: 7, 7: 7, 12: 14, 13: 14, 14: 14, 20: 21, 21: 21}
    # 1. flagged (L-05) entries: bulk-entered Friday afternoon, all >= 15:00
    for x in entries:
        if "l05" in x["tags"]:
            fd = date(2026, 8, friday_of[x["date"].day])
            assert fd.weekday() == 4 and fd >= x["date"] and (fd - x["date"]).days <= 2
            x["created"] = rand_dt(rng, fd, hm(15), hm(19))
            x["window"] = True
    # 2. late entries (lag > 72h): non-team, non-special, work date <= Aug 21
    late_pool = [x for x in entries if x["emp"] not in team and not (x["tags"] & {"l01", "l06"})
                 and x["date"] <= date(2026, 8, 21)]
    late = rng.sample(late_pool, TARGET_LATE)
    for x in late:
        cd = add_bd(x["date"], rng.choice([4, 5, 6]))
        assert cd <= PERIOD_END
        x["created"] = rand_dt(rng, cd, hm(9), hm(11, 45))
        x["tags"].add("late")
    # 3. window quotas (per direct group / indirect) among non-team, non-late Friday work
    def cls(x):
        return "c8841" if x["code"].startswith("C8841") else "ind"
    non_team = [x for x in entries if x["emp"] not in team]
    for c in ("c8841", "ind"):
        members = [x for x in non_team if cls(x) == c and (c == "c8841" or CODE_INFO[x["code"]][2] == "indirect")]
        total = len(members)
        fri = [x for x in members if x["date"].weekday() == 4 and "late" not in x["tags"]]
        w = int((D(total) * D("0.14")).quantize(D("1"), rounding=ROUND_HALF_UP))
        assert w <= len(fri)
        for x in rng.sample(fri, w):
            x["created"] = rand_dt(rng, x["date"], hm(12), hm(19, 30))
            x["window"] = True
    # 4. everything else
    for x in entries:
        if "created" in x:
            continue
        d = x["date"]
        if d == PERIOD_END:
            x["created"] = rand_dt(rng, d, hm(7, 30), hm(11))
        elif d.weekday() == 4:
            x["created"] = rand_dt(rng, d, hm(8, 30), hm(12))
        elif rng.random() < 0.6:
            x["created"] = rand_dt(rng, d, hm(16), hm(19, 30))
        else:
            x["created"] = rand_dt(rng, next_bd(d), hm(7, 30), hm(11, 30))
    return len(late)


def business_time(rng, t: datetime) -> datetime:
    """Normalise to Mon-Fri 08:00-18:00."""
    while t.weekday() >= 5:
        t = rand_dt(rng, (t + timedelta(days=1)).date(), hm(8), hm(11))
    if t.hour < 8:
        t = rand_dt(rng, t.date(), hm(8), hm(10))
    elif t.hour >= 18:
        t = rand_dt(rng, next_bd(t.date()), hm(8), hm(10))
    return t


def assign_approvals(rng, employees, entries):
    limit = datetime(2026, 8, 31, 17, 59, 59)
    for x in entries:
        cr = x["created"]
        sub = cr + timedelta(minutes=rng.randint(20, 180))
        if sub.date() != cr.date() or sub.hour >= 20 or sub.weekday() >= 5:
            sub = rand_dt(rng, next_bd(cr.date()), hm(7, 45), hm(9, 45))
        appr = business_time(rng, sub + timedelta(hours=rng.randint(2, 26), minutes=rng.randint(0, 59)))
        if appr > limit:
            appr = sub + timedelta(minutes=rng.randint(30, 120))
        assert cr < sub < appr <= limit, (cr, sub, appr)
        x["submitted"] = sub
        x["approved"] = appr
        x["approver"] = employees[x["emp"]]["sup"]
        assert x["approver"] != x["emp"]


# ----------------------------------------------------------------------------
# edits
# ----------------------------------------------------------------------------
DOC_REASONS = [
    ("Corrected charge code per supervisor", 24),
    ("Timesheet correction memo 2026-08-19", 30),
    ("Hours corrected per supervisor review", 22),
    ("Employee correction approved by supervisor", 14),
    ("Corrected hours after client timesheet reconciliation", 10),
]


def build_edits(rng, employees, roles, entries):
    team = {roles[r] for r in TEAM_ROLES}
    forced = [x for x in entries if "l06" in x["tags"]]
    assert len(forced) == 9, len(forced)
    cutoff = datetime(2026, 8, 25, 23, 59, 59)
    for x in forced:
        assert x["approved"] <= cutoff and "late" not in x["tags"]
    cand = [x for x in entries
            if x["emp"] not in team and not (x["tags"] & {"l01", "l06", "late", "l05", "l09"})
            and x["date"] <= date(2026, 8, 19) and x["approved"] <= cutoff]
    documented = rng.sample(cand, TARGET_EDITS - 9)
    targets = [(x, False) for x in forced] + [(x, True) for x in documented]
    rng.shuffle(targets)
    last2 = set(rng.sample(range(len(targets)), EDITS_LAST2))
    reasons = [r for r, _ in DOC_REASONS]
    rw = [w for _, w in DOC_REASONS]
    edits = []
    for i, (x, has_reason) in enumerate(targets):
        if i in last2:
            when = rand_dt(rng, PERIOD_END, hm(8, 30), hm(17, 30))
        else:
            days = [d for d in WORKDAYS
                    if x["approved"].date() < d <= date(2026, 8, 27)]
            when = rand_dt(rng, rng.choice(days), hm(8, 30), hm(17, 30))
        assert when > x["approved"]
        new = x["hours"]
        old = new + rng.choice([D("0.5"), D("1.0"), D("-0.5"), D("-1.0"), D("2.0")])
        if old < D("0.5"):
            old = new + D("1.0")
        editor = x["approver"] if rng.random() < 0.7 else x["emp"]
        edits.append({
            "entry": x, "mod_on": when, "mod_by": editor, "old": old, "new": new,
            "reason": rng.choices(reasons, rw)[0] if has_reason else "",
        })
    edits.sort(key=lambda e: (e["mod_on"], e["entry"]["id"]))
    for n, e in enumerate(edits, 1):
        e["id"] = f"ED-{n:06d}"
        e["entry"]["edited"] = True
    return edits


# ----------------------------------------------------------------------------
# payroll and GL
# ----------------------------------------------------------------------------
def build_payroll(employees, ids):
    paid = {"2026-08-A": D("80.0"), "2026-08-B": D("88.0")}
    total_loaded = sum(employees[i]["loaded"] * D("168.0") for i in ids)
    factor = PAYROLL_TOTAL / total_loaded
    rows = []
    for i in ids:
        e = employees[i]
        e["wage"] = q2(e["loaded"] * factor)
        for p in ("2026-08-A", "2026-08-B"):
            rows.append({"emp": i, "period": p, "reg": paid[p], "gross": q2(paid[p] * e["wage"])})
    resid = int((PAYROLL_TOTAL - sum(r["gross"] for r in rows)) * 100)
    sign = 1 if resid > 0 else -1
    base, extra = divmod(abs(resid), len(rows))
    for n, r in enumerate(rows):
        r["gross"] += sign * (D(base) + (D(1) if n < extra else D(0))) / D(100)
    assert sum(r["gross"] for r in rows) == PAYROLL_TOTAL
    return rows


def class_key(code: str):
    _, contract, ctype, pool = CODE_INFO[code]
    if ctype == "direct":
        return (contract, "direct", "")
    return ("INDIRECT", "indirect", pool)


GL_ACCOUNTS = {"": "5010 Direct Labor", "overhead": "6010 Overhead Labor",
               "ga": "6110 G&A Labor", "fringe": "6210 Fringe and Leave Labor"}


def build_gl(employees, entries, payroll_rows):
    hrs = defaultdict(lambda: defaultdict(int))  # (emp, period) -> class -> tenths
    for x in entries:
        hrs[(x["emp"], pay_period(x["date"]))][class_key(x["code"])] += int(x["hours"] * 10)
    agg = defaultdict(int)  # (period, team, class) -> cents
    for r in payroll_rows:
        key = (r["emp"], r["period"])
        classes = sorted(hrs[key])
        total_h = sum(hrs[key].values())
        cents = int(r["gross"] * 100)
        alloc = {}
        left = cents
        biggest = max(classes, key=lambda c: (hrs[key][c], c))
        for c in classes:
            if c == biggest:
                continue
            alloc[c] = cents * hrs[key][c] // total_h
            left -= alloc[c]
        alloc[biggest] = left
        team = employees[r["emp"]]["sup"]
        for c in classes:
            agg[(r["period"], team, c)] += alloc[c]
    lines = []
    for (period, team, c) in sorted(agg, key=lambda k: (GL_ACCOUNTS[k[2][2]], k[2][0], k[1], k[0])):
        lines.append({
            "account": GL_ACCOUNTS[c[2]], "class": c[0], "period": PERIOD,
            "amount": D(agg[(period, team, c)]) / D(100), "cost_type": c[1], "pool": c[2],
        })
    lines.append({"account": "6190 Payroll Accrual Adjustment", "class": "INDIRECT", "period": PERIOD,
                  "amount": GL_VARIANCE, "cost_type": "indirect", "pool": "overhead"})
    total = sum(l["amount"] for l in lines)
    assert total - PAYROLL_TOTAL == GL_VARIANCE, (total, PAYROLL_TOTAL)
    return lines


# ----------------------------------------------------------------------------
# DCAA cost-accounting extension (contracts/DCAA_DATA_SPEC.md)
# ----------------------------------------------------------------------------
# Regression mechanism: the original RNG stream is never touched by anything below.
# The L-08 move is deterministic (no RNG) and is applied before build_gl(); every
# new random draw comes from a SECOND stream, random.Random(SEED + 1), created only
# after the original generation has completed.
HIST_PERIODS = ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
PROV = {"fringe": D("0.2800"), "overhead": D("0.1800"), "ga": D("0.1000")}
POOL_BASE_DEF = {"fringe": "direct_labor", "overhead": "direct_labor", "ga": "total_cost_input"}
TARGET_RATE = {"fringe": D("0.2810"), "overhead": D("0.1790"), "ga": D("0.1080")}
HIST_RATE = {  # Feb..Jul, spec 1.4
    "fringe": ["0.2792", "0.2805", "0.2811", "0.2796", "0.2803", "0.2809"],
    "overhead": ["0.1802", "0.1794", "0.1806", "0.1797", "0.1791", "0.1788"],
    "ga": ["0.1008", "0.0996", "0.1012", "0.1040", "0.1055", "0.1068"],
}
LABOR_ACCOUNTS = ["5010 Direct Labor", "6010 Overhead Labor", "6110 G&A Labor",
                  "6190 Payroll Accrual Adjustment", "6210 Fringe and Leave Labor"]
DIRECT_NONLABOR = [  # account, class, amount
    ("5410 Subcontractors", "C-8841", D("210000.00")),
    ("5410 Subcontractors", "C-7302", D("100000.00")),
    ("5420 Travel and Other Direct Costs", "C-8841", D("62500.00")),
    ("5420 Travel and Other Direct Costs", "C-7302", D("32500.00")),
]
POOL_SPLITS = {  # pool -> [(account, share)]
    "fringe": [("6310 Payroll Taxes", D("0.40")), ("6320 Health and Welfare Benefits", D("0.45")),
               ("6330 Retirement Contributions", D("0.15"))],
    "overhead": [("6410 Rent and Facilities", D("0.50")), ("6420 Equipment and Software", D("0.35")),
                 ("6430 Training and Recruiting", D("0.15"))],
    "ga": [("6510 Insurance", D("0.30")), ("6520 Legal and Accounting", D("0.35")),
           ("6530 Marketing and Proposals", D("0.20")), ("6540 Office Administration", D("0.15"))],
}
L08_MOVE_HOURS = D("40.0")

# --- DCAA cost accounting, second seed set (rules C-01, C-02, C-03) ---------------------------------------
# C-03: contractual ceiling rates. Overhead's provisional rate (0.1800) is ABOVE its ceiling (0.1750): billed above the cap.
CEILING = {"fringe": D("0.3000"), "overhead": D("0.1750"), "ga": D("0.1200")}
# C-01: costs carved OUT of existing plug lines into their own accounts. Every pool total, and so every rate
# and every L-11 figure, is unchanged: a carve-out only splits one line into two inside the same pool.
CARVE_OUTS = {
    "overhead": [("6430 Training and Recruiting", "6435 Alcoholic Beverages", D("1850.00"))],
    "ga": [("6530 Marketing and Proposals", "6535 Entertainment and Events", D("14800.00")),
           ("6530 Marketing and Proposals", "6565 Lobbying and Political Activity", D("6500.00")),
           ("6540 Office Administration", "6555 Fines and Penalties", D("2250.00")),
           ("6520 Legal and Accounting", "6525 Gifts and Employee Awards", D("4100.00"))],
}
ALLOWABILITY = {  # account -> (class, citation). Every other account is `allowable`.
    "6435 Alcoholic Beverages": ("unallowable", "FAR 31.205-51"),
    "6535 Entertainment and Events": ("unallowable", "FAR 31.205-14"),
    "6555 Fines and Penalties": ("unallowable", "FAR 31.205-15"),
    "6565 Lobbying and Political Activity": ("unallowable", "FAR 31.205-22"),
    "6525 Gifts and Employee Awards": ("conditional", "FAR 31.205-13 (to verify)"),
}
# C-02: incurred cost submissions. FY2025 (fiscal year end 2025-12-31, due 2026-06-30) has no submission recorded.
ICS_ROWS = [
    ["FY2023", "2023-12-31", "2024-06-21", "Incurred cost submission FY2023 transmittal"],
    ["FY2024", "2024-12-31", "2025-06-27", "Incurred cost submission FY2024 transmittal"],
    ["FY2025", "2025-12-31", "", "No submission recorded"],
]
PERIOD_END = date(2026, 8, 31)


def rate4(pool: Decimal, base: Decimal) -> Decimal:
    return (pool / base).quantize(D("0.0001"), rounding=ROUND_HALF_UP)


def m10_of(actual: Decimal, prov: Decimal) -> Decimal:
    return ((actual - prov) / prov).quantize(D("0.0001"), rounding=ROUND_HALF_UP)


def is_indirect_oh_ga(code: str) -> bool:
    return CODE_INFO[code][2] == "indirect" and CODE_INFO[code][3] in ("overhead", "ga")


def apply_l08_move(employees, roles, ids, entries, edits):
    """Deterministic (no RNG). Picks employee X per DCAA_DATA_SPEC 3.2 and re-codes X's
    first five qualifying 8.0 h C-8841 entries to OH-100 (Charge column only)."""
    edited = {e["entry"]["id"] for e in edits}
    seed_emps = {"E-0417", "E-0912", "E-1104", "E-0733", "E-0208", "E-0455"}
    seed_emps |= {roles[r] for r in TEAM_ROLES}
    seed_emps |= {roles[r] for r in ["X1", "X2", "X3", "X4", "N1", "N2", "N3", "N4"]}
    seed_emps |= {x["emp"] for x in entries if x["tags"] & {"l01", "l05", "l06", "l09"}}
    by_emp = defaultdict(list)
    for x in entries:
        by_emp[x["emp"]].append(x)
    chosen = None
    relaxed_c = False
    for pass_ in (1, 2):  # pass 2 (drop condition c) only if pass 1 finds nobody
        for eid in sorted(ids):
            if eid in seed_emps or not employees[eid]["in_roster"]:
                continue
            quals = [x for x in by_emp[eid]
                     if x["hours"] == D("8.0") and x["code"] in ("C8841-DEV", "C8841-PM")
                     and x["id"] not in edited]
            if len(quals) < 5:
                continue
            oh_ga = sum((x["hours"] for x in by_emp[eid] if is_indirect_oh_ga(x["code"])), D(0))
            if pass_ == 1 and oh_ga > D("6.0"):
                continue
            chosen = (eid, quals)
            relaxed_c = pass_ == 2
            break
        if chosen:
            break
    assert chosen, "no employee satisfies the L-08 seed conditions"
    eid, quals = chosen
    quals.sort(key=lambda x: (x["date"], x["id"]))
    moved = quals[:5]
    assert sum(x["hours"] for x in moved) == L08_MOVE_HOURS
    a = sum((x["hours"] for x in by_emp[eid] if is_indirect_oh_ga(x["code"])), D(0))
    d0 = sum((x["hours"] for x in by_emp[eid] if CODE_INFO[x["code"]][2] == "direct"), D(0))
    orig = {x["id"]: x["code"] for x in moved}
    for x in moved:
        x["code"] = "OH-100"
        x["tags"].add("l08")
    return {"employee": eid, "entry_ids": sorted(orig), "orig_codes": orig,
            "pre_indirect": a, "pre_direct": d0, "relaxed_c": relaxed_c,
            "pre_oh_ga_hours": a}


def add_nonlabor(gl):
    """Appends the non-labor GL lines (spec 1.3). Plugs are computed from the FINAL labor
    lines in the order fringe, overhead, ga. Returns (full_gl, info)."""
    labor = list(gl)
    dl = sum((l["amount"] for l in labor if l["cost_type"] == "direct"), D(0))
    lab_pool = {p: sum((l["amount"] for l in labor if l["cost_type"] == "indirect" and l["pool"] == p), D(0))
                for p in PROV}
    lines = list(labor)
    dnl = D(0)
    for acct, cls, amt in DIRECT_NONLABOR:
        lines.append({"account": acct, "class": cls, "period": PERIOD, "amount": amt,
                      "cost_type": "direct", "pool": ""})
        dnl += amt
    assert dnl == D("405000.00")
    pool_total: dict[str, Decimal] = {}
    plugs: dict[str, Decimal] = {}

    def emit(pool: str, base: Decimal):
        plug = q2(TARGET_RATE[pool] * base) - lab_pool[pool]
        assert plug > 0, (pool, plug)
        parts = []
        splits = POOL_SPLITS[pool]
        for acct, share in splits[:-1]:
            parts.append(q2(plug * share))
        parts.append(plug - sum(parts))
        assert sum(parts) == plug
        for (acct, _), amt in zip(splits, parts):
            lines.append({"account": acct, "class": "INDIRECT", "period": PERIOD, "amount": amt,
                          "cost_type": "indirect", "pool": pool})
        for src, new, cut in CARVE_OUTS.get(pool, []):  # split one line in two, inside the same pool
            i = next(k for k, l in enumerate(lines) if l["account"] == src and l["pool"] == pool)
            assert lines[i]["amount"] > cut, (src, cut)
            lines[i] = {**lines[i], "amount": lines[i]["amount"] - cut}
            lines.append({"account": new, "class": "INDIRECT", "period": PERIOD, "amount": cut,
                          "cost_type": "indirect", "pool": pool})
        plugs[pool] = plug
        pool_total[pool] = lab_pool[pool] + plug
        assert rate4(pool_total[pool], base) == TARGET_RATE[pool], (pool, pool_total[pool], base)

    emit("fringe", dl)
    emit("overhead", dl)
    base_ga = dl + dnl + pool_total["fringe"] + pool_total["overhead"]
    emit("ga", base_ga)
    bases = {"fringe": dl, "overhead": dl, "ga": base_ga}
    # independent recomputation straight from the assembled lines (category-driven)
    cat = {a: "labor" for a in LABOR_ACCOUNTS}
    for a, _, _ in DIRECT_NONLABOR:
        cat[a] = "non_labor"
    for p in POOL_SPLITS:
        for a, _ in POOL_SPLITS[p]:
            cat[a] = "non_labor"
    for p in CARVE_OUTS:
        for _, new, _ in CARVE_OUTS[p]:
            cat[new] = "non_labor"
    dl2 = sum((l["amount"] for l in lines if l["cost_type"] == "direct" and cat[l["account"]] == "labor"), D(0))
    dnl2 = sum((l["amount"] for l in lines if l["cost_type"] == "direct" and cat[l["account"]] == "non_labor"), D(0))
    pool2 = {p: sum((l["amount"] for l in lines if l["cost_type"] == "indirect" and l["pool"] == p), D(0))
             for p in PROV}
    assert dl2 == dl and dnl2 == dnl and pool2 == pool_total
    assert base_ga == dl2 + dnl2 + pool2["fringe"] + pool2["overhead"]
    info = {}
    for p in ("fringe", "overhead", "ga"):
        actual = rate4(pool_total[p], bases[p])
        info[p] = {"pool": pool_total[p], "base": bases[p], "actual": actual, "prov": PROV[p],
                   "m10": m10_of(actual, PROV[p]), "plug": plugs[p], "labor_lines": lab_pool[p]}
    assert [info[p]["actual"] for p in ("fringe", "overhead", "ga")] == \
        [D("0.2810"), D("0.1790"), D("0.1080")]
    return lines, cat, info, {"DL": dl, "DNL": dnl}


def rates_rows():
    return [[p, POOL_BASE_DEF[p], f"{PROV[p]:.4f}", "2026-01-01", "2026-12-31",
             "FY2026 provisional billing rate schedule", f"{CEILING[p]:.4f}"] for p in ("fringe", "overhead", "ga")]


def metric_history_dcaa(rng2, dcaa):
    """Six new history keys; second RNG only. fringe/overhead base = direct labor; G&A base =
    DL + non-labor direct + fringe pool + overhead pool (kept coherent with the formula)."""
    out = {f"M10.{k}.{p}": {} for k in ("pool", "base") for p in ("fringe", "overhead", "ga")}
    for i, per in enumerate(HIST_PERIODS):
        dl = D(rng2.randint(165_000_000, 175_000_000)) / D(100)
        dnl = D(rng2.randint(38_000_000, 43_000_000)) / D(100)
        rf, ro, rg = (D(HIST_RATE[p][i]) for p in ("fringe", "overhead", "ga"))
        pf = q2(rf * dl)
        po = q2(ro * dl)
        bg = dl + dnl + pf + po
        pg = q2(rg * bg)
        assert D("1600000") <= dl <= D("1780000") and D("2750000") <= bg <= D("3000000")
        for p, pool, base, r in (("fringe", pf, dl, rf), ("overhead", po, dl, ro), ("ga", pg, bg, rg)):
            assert rate4(pool, base) == r, (per, p)
            out[f"M10.pool.{p}"][per] = f2(pool)
            out[f"M10.base.{p}"][per] = f2(base)
    return out


def classification_history(rng2, employees, ids, entries, l08):
    """Reference table (spec 1.5). Non-seeded employees: noisy history inside the L-08 noise
    band; employee X: every month == PRE-MOVE August (indirect a, direct D0)."""
    now_dir = defaultdict(lambda: D(0))
    now_ind = defaultdict(lambda: D(0))
    for x in entries:
        if CODE_INFO[x["code"]][2] == "direct":
            now_dir[x["emp"]] += x["hours"]
        elif is_indirect_oh_ga(x["code"]) or x["code"] == "BP-300":
            now_ind[x["emp"]] += x["hours"]
    emps = {}
    for eid in ids:
        e = employees[eid]
        if not e["in_roster"]:
            continue  # the 4 DQ-01 employees: no history, not scored
        assert e["hire"] < date(2026, 2, 1)
        dnow, inow = now_dir[eid], now_ind[eid]
        if eid == l08["employee"]:
            emps[eid] = {p: {"direct_hours": f1(l08["pre_direct"]), "indirect_hours": f1(l08["pre_indirect"])}
                         for p in HIST_PERIODS}
            continue
        while True:
            hist = {}
            for p in HIST_PERIODS:
                d = D(rng2.randint(-6, 6)) / D(2)  # multiple of 0.5 in [-3, 3]
                noise = D(rng2.randint(0, 4)) / D(2)  # 0..2.0 h, multiple of 0.5
                ind = max(D(0), inow + d)
                dr = max(D(0), dnow - d + noise)
                hist[p] = (dr, ind)
            shares = [i / (dr + i) for dr, i in hist.values()]
            mean_share = sum(shares) / D(6)
            mean_ind = sum(i for _, i in hist.values()) / D(6)
            share_now = inow / (dnow + inow)
            if abs(share_now - mean_share) < D("0.05") and abs(inow - mean_ind) < D("8.0"):
                break
        emps[eid] = {p: {"direct_hours": f1(dr), "indirect_hours": f1(i)} for p, (dr, i) in hist.items()}
    return {
        "note": "Seeded stand-in for classification retained from prior runs; in production these come "
                "from the results store.",
        "definition": "direct = hours on direct charge codes; indirect = hours on overhead-pool and G&A-pool "
                      "codes (OH-100, GA-200, BP-300). Fringe-pool codes (FR-050, LV-010) are excluded.",
        "periods": HIST_PERIODS,
        "employees": emps,
    }


# ----------------------------------------------------------------------------
# file writers
# ----------------------------------------------------------------------------
def write_csv(name: str, header: list[str], rows: list[list[str]]) -> None:
    with open(OUT_DIR / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)


def write_json(name: str, obj) -> None:
    with open(OUT_DIR / name, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def contracts_json(employees, roles, ids):
    def cats(lst):
        return [{"name": n, "ceiling_rate": r, "min_qualification": q} for n, r, q in lst]
    pm8841 = sorted(i for i in ids if employees[i]["group"] == "c8841"
                    and employees[i]["title"] == "Project Manager")[0]
    return [
        {"contract_id": "C-8841", "name": "SPECTRA", "type": "T&M",
         "pop_start": "2025-10-01", "pop_end": "2026-09-30",
         "ceiling": "6000000.00", "funded_value": "4200000.00",
         "labor_categories": cats(C8841_CATEGORIES), "key_personnel": [pm8841],
         "charge_codes": ["C8841-DEV", "C8841-PM"], "source_pdf": "C-8841.pdf",
         "extraction": {"confirmed_by": "a.okafor", "confirmed_at": "2026-04-11T09:31:00Z",
                        "pages": {"pop": 3, "labor_categories": 12, "ceiling": 12}}},
        {"contract_id": "C-7302", "name": "ATLAS", "type": "CPFF",
         "pop_start": "2025-08-16", "pop_end": "2026-08-15",
         "ceiling": "3150000.00", "funded_value": "3150000.00",
         "labor_categories": cats(C7302_CATEGORIES), "key_personnel": [roles["T4"]],
         "charge_codes": ["C7302-DEV", "C7302-PM"], "source_pdf": "C-7302.pdf",
         "extraction": {"confirmed_by": "a.okafor", "confirmed_at": "2026-04-11T09:34:00Z",
                        "pages": {"pop": 2, "labor_categories": 9, "ceiling": 9}}},
    ]


def metric_history():
    periods = ["2026-02", "2026-03", "2026-04", "2026-05", "2026-06", "2026-07"]
    m5 = ["0.13", "0.15", "0.14", "0.14", "0.13", "0.15"]
    m6 = ["0.0350", "0.0362", "0.0341", "0.0374", "0.0356", "0.0347"]
    m4 = ["0.0290", "0.0310", "0.0270", "0.0330", "0.0300", "0.0280"]
    assert sum(D(v) for v in m5) / 6 == D("0.14")
    assert all(D("0.033") <= D(v) <= D("0.038") for v in m6)
    assert all(D("0.025") <= D(v) <= D("0.035") for v in m4)
    return {
        "note": "Seeded stand-in for metric values retained from prior runs; in production these "
                "come from the results store.",
        "periods": periods,
        "metrics": {
            "M5.company_window_share": dict(zip(periods, m5)),
            "M6.edit_rate": dict(zip(periods, m6)),
            "M4.late_rate": dict(zip(periods, m4)),
        },
    }


def sources_json():
    return {
        "period": PERIOD, "customer": "meridian-systems",
        "sources": {
            "timekeeping": {"file": "meridian_time_2026-08.csv", "data_as_of": "2026-08-31"},
            "time_edits": {"file": "meridian_time_edits_2026-08.csv", "data_as_of": "2026-08-31"},
            "payroll": {"file": "adp_payroll_2026-08.csv", "data_as_of": "2026-08-31"},
            "gl": {"file": "qb_gl_2026-08.csv", "data_as_of": "2026-08-31"},
            "hris": {"file": "bamboo_roster_2026-08-01.csv", "data_as_of": "2026-08-01"},
            "contracts": {"file": "contracts.json", "data_as_of": "2026-04-11"},
            "rate_data": {"file": "meridian_rates_2026-08.csv", "data_as_of": "2026-08-31"},
        },
        "absent": [],
    }


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------
def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    employees, terminated, roles, ids, sup_ids = build_people(rng)
    plans, locked = build_plans(rng, employees, roles, ids)
    team_info = build_team(rng, employees, roles, plans, locked)
    adjust_count(rng, employees, plans, locked, TARGET_ENTRIES)
    gaps = apply_all_gaps(rng, employees, roles, ids, plans, locked)

    entries = []
    for key in sorted(plans):
        entries.extend(plans[key])
    assert len(entries) == TARGET_ENTRIES
    late_n = assign_created(rng, employees, roles, entries)
    assign_approvals(rng, employees, entries)
    entries.sort(key=lambda x: (x["created"], x["emp"], x["date"], x["code"], x["hours"]))
    for n, x in enumerate(entries, 1):
        x["id"] = f"TE-{100000 + n:06d}"
    edits = build_edits(rng, employees, roles, entries)
    payroll = build_payroll(employees, ids)
    # --- DCAA extension: deterministic L-08 move (no RNG) BEFORE the GL is built ---------
    l08 = apply_l08_move(employees, roles, ids, entries, edits)
    gl = build_gl(employees, entries, payroll)  # labor lines only (from the FINAL codes)
    full_gl, acct_cat, dcaa, dcaa_tot = add_nonlabor(gl)
    # --- second RNG stream, created only now, after all original draws are done ----------
    rng2 = random.Random(SEED + 1)
    mh_extra = metric_history_dcaa(rng2, dcaa)
    class_hist = classification_history(rng2, employees, ids, entries, l08)

    # ---------------- write source files ------------------------------------
    write_csv("meridian_time_2026-08.csv",
              ["Entry ID", "Person Code", "TS Date", "Hours", "Charge", "PLC", "Created On",
               "Sub Dt", "Appr By", "Appr Dt"],
              [[x["id"], x["emp"], x["date"].isoformat(), f1(x["hours"]), x["code"], x["plc"],
                iso_dt(x["created"]), iso_dt(x["submitted"]), x["approver"], iso_dt(x["approved"])]
               for x in entries])
    write_csv("meridian_time_edits_2026-08.csv",
              ["Edit ID", "Entry ID", "Mod On", "Mod By", "Old Value", "New Value", "Reason"],
              [[e["id"], e["entry"]["id"], iso_dt(e["mod_on"]), e["mod_by"], f1(e["old"]),
                f1(e["new"]), e["reason"]] for e in edits])
    write_csv("adp_payroll_2026-08.csv",
              ["Associate ID", "Pay Period", "Reg Hrs", "OT Hrs", "PTO Hrs", "Gross Pay"],
              [[r["emp"], r["period"], f1(r["reg"]), "0.0", "0.0", f2(r["gross"])] for r in payroll])
    write_csv("qb_gl_2026-08.csv", ["Account", "Class", "Period", "Amount", "Cost Type", "Pool"],
              [[l["account"], l["class"], l["period"], f2(l["amount"]), l["cost_type"], l["pool"]]
               for l in full_gl])
    roster = []
    for i in ids:
        e = employees[i]
        if e["in_roster"]:
            roster.append([i, e["name"], e["title"], e["hire"].isoformat(), "",
                           "Exempt" if e["exempt"] else "Non-Exempt", e["dept"], e["sup"]])
    for t in terminated:
        roster.append([t["id"], t["name"], t["title"], t["hire"].isoformat(), t["term"].isoformat(),
                       "Exempt" if t["exempt"] else "Non-Exempt", t["dept"], t["sup"]])
    roster.sort(key=lambda r: r[0])
    write_csv("bamboo_roster_2026-08-01.csv",
              ["Employee #", "Name", "Job Title", "Hire Date", "Termination Date", "FLSA Status",
               "Department", "Supervisor"], roster)
    write_json("contracts.json", contracts_json(employees, roles, ids))
    write_csv("charge_codes.csv", ["Charge", "Contract", "Cost Type", "Pool"],
              [list(c) for c in CHARGE_CODES])
    write_csv("labor_category_crosswalk.csv", ["HRIS Title", "Contract Category"],
              [[t, TITLES[t][0]] for t in TITLES])
    write_csv("loaded_rates.csv", ["Employee #", "Loaded Rate"],
              [[i, f2(employees[i]["loaded"])] for i in ids])
    write_json("sources.json", sources_json())
    mh = metric_history()
    mh["metrics"].update(mh_extra)
    write_json("metric_history.json", mh)
    write_csv("meridian_rates_2026-08.csv",
              ["Pool", "Base Definition", "Provisional Rate", "Effective From", "Effective To",
               "Source Document", "Ceiling Rate"], rates_rows())
    ordered_accts = sorted(a for a in acct_cat if acct_cat[a] == "labor") + \
        sorted(a for a in acct_cat if acct_cat[a] == "non_labor")
    write_csv("account_categories.csv", ["Account", "Category"], [[a, acct_cat[a]] for a in ordered_accts])
    write_json("classification_history.json", class_hist)
    write_csv("account_allowability.csv", ["Account", "Allowability", "Citation"],
              [[a, *ALLOWABILITY.get(a, ("allowable", ""))] for a in ordered_accts])
    write_csv("ics_submissions.csv", ["Fiscal Year", "Fiscal Year End", "Submitted On", "Source Document"], ICS_ROWS)

    # ---------------- ground truth (own arithmetic) --------------------------
    gt = ground_truth(employees, terminated, roles, ids, entries, edits, payroll, gl, gaps, team_info,
                      full_gl, l08, dcaa, dcaa_tot)
    write_json("ground_truth.json", gt)


def ground_truth(employees, terminated, roles, ids, entries, edits, payroll, gl, gaps, team_info,
                 full_gl=None, l08=None, dcaa=None, dcaa_tot=None):
    rate_of = {i: employees[i]["loaded"] for i in ids}
    team = sorted(roles[r] for r in TEAM_ROLES)
    by_id = {x["id"]: x for x in entries}

    def cost(x):
        return x["hours"] * rate_of[x["emp"]]

    def eids(tag):
        return sorted(x["id"] for x in entries if tag in x["tags"])

    n = len(entries)
    basis = q2(sum(cost(x) for x in entries))
    materiality = q2(basis * D("0.005"))

    # window definition: created on a Friday at/after 12:00
    def in_window(x):
        c = x["created"]
        return c.weekday() == 4 and c.hour >= 12

    c7302 = [x for x in entries if x["code"].startswith("C7302")]
    c8841 = [x for x in entries if x["code"].startswith("C8841")]
    l05 = [x for x in c7302 if x["emp"] in team and in_window(x)]
    assert sorted(x["id"] for x in l05) == eids("l05"), "L-05 tag/definition mismatch"
    assert all(x["created"].hour >= 15 for x in l05)
    l05_usd = q2(sum(cost(x) for x in l05))
    assert l05_usd == D("27518.40"), l05_usd
    m5_7302 = D(len(l05)) / D(len(c7302))
    m5_8841 = D(sum(1 for x in c8841 if in_window(x))) / D(len(c8841))
    assert D("0.10") <= m5_8841 <= D("0.17"), m5_8841
    m5_company = D(sum(1 for x in entries if in_window(x))) / D(n)

    late = sum(1 for x in entries
               if x["created"] - datetime.combine(x["date"] + timedelta(days=1), datetime.min.time())
               > timedelta(hours=72))
    assert late == TARGET_LATE and D(late) / D(n) < D("0.05")

    # L-01
    l01 = [x for x in entries if "l01" in x["tags"]]
    assert len(l01) == 12 and sum(x["hours"] for x in l01) == D("96.0")
    l01_usd = q2((D("178.50") - D("142.00")) * sum(x["hours"] for x in l01))
    assert l01_usd == D("3504.00")

    # L-09
    l09 = [x for x in c7302 if x["date"] > POP_END_7302]
    assert sorted(x["id"] for x in l09) == eids("l09")
    assert sum(x["hours"] for x in l09) == D("62.0")
    l09_usd = q2(sum(cost(x) for x in l09))
    assert l09_usd == D("5852.40"), l09_usd

    # L-06
    undoc = [e for e in edits if e["reason"] == ""]
    assert len(edits) == TARGET_EDITS and len(undoc) == 9
    assert len({e["entry"]["id"] for e in edits}) == TARGET_EDITS
    undoc_entries = [e["entry"] for e in undoc]
    assert len({x["id"] for x in undoc_entries}) == 9
    l06_hours = sum(x["hours"] for x in undoc_entries)
    l06_usd = q2(sum(cost(x) for x in undoc_entries))
    assert l06_hours == D("46.0") and l06_usd == D("4199.80"), (l06_hours, l06_usd)
    l06_emps = sorted({x["emp"] for x in undoc_entries})
    assert len(l06_emps) == 6
    last2 = sum(1 for e in edits if e["mod_on"].date() >= date(2026, 8, 30))

    # overlaps
    s05 = {x["id"] for x in l05}
    s09 = {x["id"] for x in l09}
    s06 = {x["id"] for x in undoc_entries}
    ov59 = sorted(s05 & s09)
    ov56 = sorted(s05 & s06)
    assert not (s06 & s09)
    ov59_usd = q2(sum(cost(by_id[i]) for i in ov59))
    ov56_usd = q2(sum(cost(by_id[i]) for i in ov56))
    assert ov59_usd == D("1644.00") and ov56_usd == D("970.20")

    # L-02: time vs paid hours per employee-period
    tmap = defaultdict(lambda: D(0))
    for x in entries:
        tmap[(x["emp"], pay_period(x["date"]))] += x["hours"]
    paid = {(r["emp"], r["period"]): r["reg"] for r in payroll}
    gap = {k: tmap[k] - paid[k] for k in paid}
    sum_abs = sum(abs(v) for v in gap.values())
    assert sum_abs == D("342.0"), sum_abs
    total_paid = sum(paid.values())
    assert total_paid == D("23856.0")
    seeds = [("E-0912", "2026-08-B", D("12.0")), ("E-1104", "2026-08-A", D("8.0")),
             ("E-0733", "2026-08-B", D("-9.5"))]
    for e_, p_, g_ in seeds:
        assert gap[(e_, p_)] == g_
    seeded_keys = {(e_, p_) for e_, p_, _ in seeds}
    assert all(abs(v) < D("4.0") for k, v in gap.items() if k not in seeded_keys)
    assert gap[("E-0208", "2026-08-A")] == D("1.5")
    l02 = []
    for (e_, p_, g_), sid in zip(seeds, ["S-L02a", "S-L02b", "S-L02c"]):
        usd = q2(abs(g_) * rate_of[e_])
        l02.append({"seed_id": sid, "rule_id": "L-02", "fingerprint": f"L-02|{e_}|{p_}",
                    "employees": [e_], "entry_ids": [], "pay_period": p_, "gap_hours": f1(g_),
                    "loaded_rate": f2(rate_of[e_]), "exposure_usd": f2(usd),
                    "exposure_basis": "loaded_cost", "severity": "medium"})
    assert [x["exposure_usd"] for x in l02] == ["1156.80", "705.60", "976.13"]

    # GL vs payroll
    payroll_total = sum(r["gross"] for r in payroll)
    gl_total = sum(l["amount"] for l in gl)
    m2 = (gl_total - payroll_total) / payroll_total

    # DQ-01
    roster_ids = {employees[i]["id"] for i in ids if employees[i]["in_roster"]}
    unmatched = sorted(i for i in ids if i not in roster_ids)
    assert len(unmatched) == 4

    # M13
    non_entry = l01_usd + sum(D(x["exposure_usd"]) for x in l02)
    union = s05 | s06 | s09
    union_usd = q2(sum(cost(by_id[i]) for i in union))
    gross = l01_usd + l05_usd + l09_usd + l06_usd + sum(D(x["exposure_usd"]) for x in l02)
    m13 = non_entry + union_usd
    assert gross == D("43913.13") and union_usd == D("34956.40") and m13 == D("41298.93")

    # ---- DCAA extension: L-08 and L-11 -------------------------------------------------
    lab_m13 = m13
    xid = l08["employee"]
    l08_entries = [by_id[i] for i in l08["entry_ids"]]
    assert all("l08" in x["tags"] and x["code"] == "OH-100" for x in l08_entries)
    assert not ({x["id"] for x in l08_entries} & (s05 | s06 | s09))
    assert not ({x["id"] for x in l08_entries} & {e["entry"]["id"] for e in edits})
    x_dir = sum((x["hours"] for x in entries if x["emp"] == xid and CODE_INFO[x["code"]][2] == "direct"), D(0))
    x_ind = sum((x["hours"] for x in entries if x["emp"] == xid and is_indirect_oh_ga(x["code"])), D(0))
    assert x_ind == l08["pre_indirect"] + D("40.0") and x_dir == l08["pre_direct"] - D("40.0")
    l08_excess = x_ind - l08["pre_indirect"]  # history mean of X == pre-move August indirect
    assert l08_excess == D("40.0")
    l08_share_now = x_ind / (x_dir + x_ind)
    l08_share_hist = l08["pre_indirect"] / (l08["pre_direct"] + l08["pre_indirect"])
    assert l08_share_now - l08_share_hist >= D("0.15")
    l08_usd = q2(l08_excess * rate_of[xid])
    assert sum(x["hours"] for x in l08_entries) == D("40.0")
    assert full_gl is not None and len(full_gl) > len(gl)
    ga = dcaa["ga"]
    l11_usd = q2(abs(ga["actual"] - ga["prov"]) * ga["base"])
    assert ga["m10"] == D("0.0800") and abs(dcaa["fringe"]["m10"]) < D("0.02") and abs(dcaa["overhead"]["m10"]) < D("0.02")
    assert l11_usd == q2(D("0.0080") * ga["base"])
    # --- C-01 / C-02 / C-03: own arithmetic from the constants and the assembled GL ---------------------
    unallow: dict[str, list] = {}
    cond_lines = []
    for l in full_gl:
        a = ALLOWABILITY.get(l["account"])
        if l["cost_type"] == "indirect" and a:
            (unallow.setdefault(l["pool"], []) if a[0] == "unallowable" else cond_lines).append(l)
    c01_by_pool = {p: sum((l["amount"] for l in ls), D(0)) for p, ls in unallow.items()}
    assert c01_by_pool == {"ga": D("23550.00"), "overhead": D("1850.00")}, c01_by_pool
    c01_total = sum(c01_by_pool.values(), D(0))
    cond_total = sum((l["amount"] for l in cond_lines), D(0))
    assert cond_total == D("4100.00")
    fye = date(2025, 12, 31)  # FY2025: the one with no submission
    yy, mm = divmod(fye.year * 12 + fye.month - 1 + 6, 12)  # six months after the fiscal year end
    ics_due = date(yy, mm + 1, calendar.monthrange(yy, mm + 1)[1])
    assert ics_due == date(2026, 6, 30)
    ics_days = (PERIOD_END - ics_due).days
    assert ics_days == 62
    over = {p: PROV[p] - CEILING[p] for p in PROV if PROV[p] > CEILING[p]}
    assert list(over) == ["overhead"]
    c03_usd = q2(over["overhead"] * dcaa["overhead"]["base"])
    assert c03_usd == q2(D("0.0050") * dcaa["overhead"]["base"])
    # Overhead's ACTUAL rate (0.1790) is also above its ceiling (0.1750): context for the finding, not part of
    # its exposure. The exposure is what was BILLED above the cap, at the provisional rate.
    assert dcaa["overhead"]["actual"] > CEILING["overhead"]
    assert dcaa["ga"]["actual"] <= CEILING["ga"] and dcaa["fringe"]["actual"] <= CEILING["fringe"]
    dcaa_extra = c01_total + c03_usd  # C-02 carries no dollars
    m13_all = m13 + l08_usd + l11_usd + dcaa_extra
    non_entry_all = non_entry + l11_usd + dcaa_extra
    union_all = union_usd + l08_usd
    gross_all = gross + l08_usd + l11_usd + dcaa_extra
    assert m13_all == non_entry_all + union_all
    n_gl_labor = len(gl)

    counts = {"employees": len(ids), "time_entries": n, "edits": len(edits),
              "payroll_rows": len(payroll), "gl_lines": len(full_gl)}
    edit_rate = D(len(edits)) / D(n)
    injected = [
        {"seed_id": "S-L01", "rule_id": "L-01", "fingerprint": "L-01|E-0417|C-8841",
         "employees": ["E-0417"], "entry_ids": sorted(x["id"] for x in l01), "hours": f1(D("96.0")),
         "exposure_usd": f2(l01_usd), "exposure_basis": "rate_difference", "severity": "high"},
        {"seed_id": "S-L05", "rule_id": "L-05", "fingerprint": "L-05|C-7302", "employees": team,
         "entry_ids": sorted(s05), "hours": f1(sum(x["hours"] for x in l05)),
         "exposure_usd": f2(l05_usd), "exposure_basis": "loaded_cost", "severity": "high",
         "group_entries": len(c7302), "flagged_entries": len(l05),
         "window_share": f"{m5_7302:.4f}", "baseline": "0.14"},
        {"seed_id": "S-L09", "rule_id": "L-09", "fingerprint": "L-09|C-7302",
         "employees": sorted({x["emp"] for x in l09}), "entry_ids": sorted(s09),
         "hours": f1(sum(x["hours"] for x in l09)), "exposure_usd": f2(l09_usd),
         "exposure_basis": "loaded_cost", "severity": "high"},
        *l02,
        {"seed_id": "S-L06", "rule_id": "L-06", "fingerprint": "L-06|2026-08", "employees": l06_emps,
         "entry_ids": sorted(s06), "edit_ids": sorted(e["id"] for e in undoc),
         "hours": f1(l06_hours), "exposure_usd": f2(l06_usd), "exposure_basis": "loaded_cost",
         "severity": "low"},
        {"seed_id": "S-DQ01", "rule_id": "DQ-01", "fingerprint": "DQ-01|2026-08",
         "employees": unmatched, "entry_ids": [], "exposure_usd": "0.00",
         "exposure_basis": "none", "severity": "medium"},
        {"seed_id": "S-L11-GA", "rule_id": "L-11", "fingerprint": "L-11|ga|2026-08",
         "exposure_usd": f2(l11_usd), "severity": "high", "exposure_basis": "rate_true_up",
         "pool": "ga", "base_usd": f2(ga["base"]), "pool_usd": f2(ga["pool"]),
         "actual_rate": f"{ga['actual']:.4f}", "provisional_rate": f"{ga['prov']:.4f}",
         "m10": f"{ga['m10']:.4f}", "direction": "under_billed",
         "consecutive_watch_periods": 4},
        {"seed_id": "S-L08", "rule_id": "L-08", "fingerprint": f"L-08|{xid}|2026-08",
         "employees": [xid], "entry_ids": list(l08["entry_ids"]), "hours": f1(D("40.0")),
         "exposure_usd": f2(l08_usd), "severity": "high", "exposure_basis": "loaded_cost",
         "original_charge_codes": {i: l08["orig_codes"][i] for i in l08["entry_ids"]},
         "new_charge_code": "OH-100", "loaded_rate": f2(rate_of[xid]),
         "indirect_now_hours": f1(x_ind), "indirect_hist_mean_hours": f1(l08["pre_indirect"]),
         "excess_hours": f1(l08_excess), "direct_now_hours": f1(x_dir),
         "share_now": f"{l08_share_now:.4f}", "share_hist_mean": f"{l08_share_hist:.4f}"},
        {"seed_id": "S-C01-GA", "rule_id": "C-01", "fingerprint": "C-01|ga|2026-08",
         "exposure_usd": f2(c01_by_pool["ga"]), "severity": "high", "exposure_basis": "unallowable_amount",
         "pool": "ga", "accounts": {l["account"]: f2(l["amount"]) for l in unallow["ga"]},
         "citations": {l["account"]: ALLOWABILITY[l["account"]][1] for l in unallow["ga"]}},
        {"seed_id": "S-C01-OH", "rule_id": "C-01", "fingerprint": "C-01|overhead|2026-08",
         "exposure_usd": f2(c01_by_pool["overhead"]), "severity": "medium", "exposure_basis": "unallowable_amount",
         "pool": "overhead", "accounts": {l["account"]: f2(l["amount"]) for l in unallow["overhead"]},
         "citations": {l["account"]: ALLOWABILITY[l["account"]][1] for l in unallow["overhead"]}},
        {"seed_id": "S-C02", "rule_id": "C-02", "fingerprint": "C-02|FY2025|2026-08",
         "exposure_usd": "0.00", "severity": "high", "exposure_basis": "none",
         "fiscal_year": "FY2025", "fiscal_year_end": fye.isoformat(), "due_date": ics_due.isoformat(),
         "days_overdue": ics_days, "as_of": PERIOD_END.isoformat()},
        {"seed_id": "S-C03", "rule_id": "C-03", "fingerprint": "C-03|overhead|2026-08",
         "exposure_usd": f2(c03_usd), "severity": "high", "exposure_basis": "rate_above_ceiling",
         "pool": "overhead", "provisional_rate": f"{PROV['overhead']:.4f}", "ceiling_rate": f"{CEILING['overhead']:.4f}",
         "base_usd": f2(dcaa["overhead"]["base"])},
    ]
    dcaa_rates = {p: {"pool_usd": f2(dcaa[p]["pool"]), "base_usd": f2(dcaa[p]["base"]),
                      "actual_rate": f"{dcaa[p]['actual']:.4f}",
                      "provisional_rate": f"{dcaa[p]['prov']:.4f}", "m10": f"{dcaa[p]['m10']:.4f}"}
                  for p in ("fringe", "overhead", "ga")}
    l08_dev = (
        f"L-08 employee X = {xid}: five 8.0 h C-8841 entries ({', '.join(l08['entry_ids'])}) were re-coded "
        "to OH-100 (Charge column only; PLC, timestamps and approvals unchanged, the existing convention "
        "for indirect rows), moving 40.0 h from direct to indirect. Because the C-8841 entries drop by "
        "five, the C-8841 Friday-window share (M5_C-8841) is re-derived after the move.")
    if l08["relaxed_c"]:
        l08_dev += (" Condition (c) (<= 6.0 h overhead/G&A hours) was RELAXED: no employee satisfied "
                    "every condition.")
    return {
        "period": PERIOD, "seed": SEED, "counts": counts,
        "materiality_basis_usd": f2(basis), "materiality_usd": f2(materiality),
        "materiality_basis_definition": "sum(entry hours x that employee's loaded rate) over all "
                                        "August time entries, rounded half-up to the cent",
        "expected_metrics": {
            "M1": f"{sum_abs / total_paid:.4f}", "M2": f"{m2:.4f}", "M4": f"{D(late) / D(n):.4f}",
            "M5_C-7302": f"{m5_7302:.4f}", "M5_C-8841": f"{m5_8841:.4f}",
            "M5_company": f"{m5_company:.4f}", "M6": f"{edit_rate:.4f}",
            "M10_fringe": f"{dcaa['fringe']['m10']:.4f}", "M10_overhead": f"{dcaa['overhead']['m10']:.4f}",
            "M10_ga": f"{dcaa['ga']['m10']:.4f}",
        },
        "expected_metric_detail": {
            "M1_sum_abs_gap_hours": f1(sum_abs), "M1_total_paid_hours": f1(total_paid),
            "M2_payroll_gross_usd": f2(payroll_total), "M2_gl_total_usd": f2(gl_total),
            "M2_variance_usd": f2(gl_total - payroll_total),
            "M2_basis": "labor accounts only (account_categories.csv category = labor)",
            "M2_gl_total_all_accounts_usd": f2(sum(l["amount"] for l in full_gl)),
            "GL_lines_labor": n_gl_labor, "GL_lines_non_labor": len(full_gl) - n_gl_labor,
            "DL_usd": f2(dcaa_tot["DL"]), "DNL_usd": f2(dcaa_tot["DNL"]),
            "M4_late_entries": late, "M4_late_threshold_hours": 72,
            "M5_C-7302_window_entries": len(l05), "M5_C-7302_entries": len(c7302),
            "M5_baseline_mean": "0.14", "M5_cluster_index_C-7302": f"{m5_7302 / D('0.14'):.2f}",
            "M6_edits": len(edits), "M6_undocumented": len(undoc),
            "M6_undocumented_rate": f"{D(len(undoc)) / D(len(edits)):.4f}",
            "M6_last2days_edits": last2, "M6_last2days_share": f"{D(last2) / D(len(edits)):.4f}",
        },
        "injected": injected,
        "expected_findings": 14,
        "expected_findings_by_severity": {"high": 8, "medium": 5, "low": 1},
        "expected_by_severity": {"high": 8, "medium": 5, "low": 1},
        "expected_total_exposure_usd": f2(m13_all),
        "expected_gross_exposure_before_dedup_usd": f2(gross_all),
        "expected_non_entry_exposure_usd": f2(non_entry_all),
        "expected_entry_union_exposure_usd": f2(union_all),
        "labor_domain_total_exposure_usd": f2(lab_m13),
        "dcaa_domain_total_exposure_usd": f2(l08_usd + l11_usd + dcaa_extra),
        "overlaps": [
            {"between": ["L-05", "L-09"], "entry_ids": ov59, "usd": f2(ov59_usd)},
            {"between": ["L-05", "L-06"], "entry_ids": ov56, "usd": f2(ov56_usd)},
        ],
        "expected_not_evaluated": [],
        "expected_consistent": ["L-03"],
        "expected_coverage": {"evaluated": 11, "applicable": 11, "pct": "100.0"},
        "domains": {"labor": {"rules": ["L-01", "L-02", "L-03", "L-05", "L-06", "L-09"], "coverage": [6, 6]},
                    "dcaa_cost_accounting": {"rules": ["L-08", "L-11", "C-01", "C-02", "C-03"], "coverage": [5, 5]}},
        "dcaa_rates": dcaa_rates,
        "logged_below_materiality": [{"rule_id": "L-02", "employee_id": "E-0208", "gap_hours": "1.5"},
                                     {"rule_id": "C-01", "account": "6525 Gifts and Employee Awards",
                                      "pool": "ga", "amount": f2(cond_total), "allowability": "conditional"}],
        "roster": {"rows": 142, "active": 138, "terminated": 4,
                   "terminated_ids": sorted(t["id"] for t in terminated),
                   "data_as_of": "2026-08-01"},
        "notes": [
            "Pay periods for the L-02 seeds: E-0912 -> 2026-08-B (per spec fingerprint), E-1104 -> "
            "2026-08-A, E-0733 -> 2026-08-B (spec silent; chosen here).",
            "Late entry = entered_at - end_of(work_date) > 72 h (L-05 default late_threshold_hours). All "
            "non-late entries have lag < 45 h, all late entries > 80 h, so any threshold between 48 h and "
            "80 h yields the same 128 late entries.",
            "Cluster window = created on a Friday at or after 12:00. C-7302 team flagged entries were all "
            "created Fri >= 15:00; every other entry created on a Friday is before 12:00 unless it falls in "
            "the ~14% baseline window share.",
            "'Edits in the last 2 days' = Mod On on 2026-08-30 or 2026-08-31; all 61 fall on 2026-08-31 and "
            "no edit is dated 2026-08-28..30, so a last-2-working-days reading gives the same figure.",
            "The 4 roster-absent employees started 2026-08-04 with 9.0 h days so their period-A hours "
            "equal the 80.0 h paid.",
            "Entry hours in the CSV are the post-edit values; edits' New Value equals the entry hours.",
        ],
        "deviations_from_design_doc": [
            "M4 late-entry rate is 3.11% (128 / 4,120), not the design doc's 5.19% Watch (214 / 4,120); "
            "DATA_SPEC requires < 5% so that L-05 raises only the cluster finding.",
            "Edits are on 148 DISTINCT entries (spec); design doc 15.5 says the 148 edits link to 139 distinct entries.",
            "Materiality basis = sum(entry hours x loaded rate) (spec 2), not the design doc's period labor "
            "cost of 1,986,240.00 (-> 9,931.20); here basis and materiality are recorded in "
            "materiality_basis_usd / materiality_usd.",
            "Roster has 142 rows (138 active + 4 terminated) with the 4 active employees absent; the design "
            "doc's 146 rows (142 active + 4 terminated) would not produce a DQ-01 finding.",
            "Payroll is delivered as CSV (adp_payroll_2026-08.csv), not XLSX; contracts are delivered as "
            "confirmed JSON (contracts.json), not PDFs.",
            f"GL has {n_gl_labor} labor lines (design doc: 1,806); a single line (6190 Payroll Accrual "
            "Adjustment) carries the 2,138.00 payroll-to-GL variance. Payroll gross total is set to "
            "1,986,240.00 and the labor GL total is 1,988,378.00. The DCAA extension appends "
            f"{len(full_gl) - n_gl_labor} non-labor lines (counts.gl_lines = {len(full_gl)}, all accounts).",
            "Expected coverage is 6 of 7 applicable L-rules (85.7%) with L-11 not evaluated (spec 4), not the "
            "design doc's 11 of 12 (91.7%); L-07, L-08, L-10, L-12, L-13 are not implemented in this build.",
            "M4 p90 lag (5.2 d in the design doc) is not reproduced; p90 lag in this data is under one day.",
            l08_dev,
            "The 6190 Payroll Accrual Adjustment line (2138.00, Pool = overhead) is part of the overhead pool "
            "as the GL reports it; the overhead plug was computed net of it.",
            "expected_coverage was updated from 6/7 to 8/8 (100.0) because L-11 is now evaluated "
            "(expected_not_evaluated = []); expected_gross_exposure_before_dedup_usd, "
            "expected_non_entry_exposure_usd and expected_entry_union_exposure_usd now include L-11 "
            "(non-entry) and L-08 (entry union); the pre-DCAA labor-only total is kept in "
            "labor_domain_total_exposure_usd (41298.93).",
            "M2 (L-03) is computed on labor accounts only via account_categories.csv; the non-labor GL lines "
            "would otherwise break the payroll tie.",
        ],
        "generator": {"l05_solver_tries": team_info["solver_tries"]},
    }


if __name__ == "__main__":
    main()
