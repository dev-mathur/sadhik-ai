"""Metric helpers and the M13 exposure de-duplication.

Pure functions only: no I/O, no clock, no randomness. All money and hours are
Decimal; money is rounded only with `money()` (half-up, cents).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Iterable, Sequence

from engine.canonical import Employee, TimeEntry, WorkingView
from engine.rules.base import EvidenceRef, Finding, money, ratio

ZERO = Decimal("0")
HUNDRED = Decimal("100")

# Design FR 6.2: fewer than 3 prior periods of history is "low baseline confidence".
MIN_HISTORY_PERIODS = 3
# Design 15.7: baselines are the mean of up to the last six retained periods.
BASELINE_WINDOW = 6


# --------------------------------------------------------------------------- #
# Formatting (used in headlines and explanations; never in arithmetic)
# --------------------------------------------------------------------------- #
def fmt_hours(x: Decimal | int | str) -> str:
    d = Decimal(x)
    q = d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == d:
        return str(q)
    return format(d.normalize(), "f")


def fmt_money(x: Decimal | int | str) -> str:
    return f"${money(x):,.2f}"


def fmt_pct(fraction: Decimal | int | str, places: int = 1) -> str:
    """0.0359 -> '3.6%' (half-up)."""
    q = Decimal(1).scaleb(-places)
    return f"{(Decimal(fraction) * HUNDRED).quantize(q, rounding=ROUND_HALF_UP)}%"


def fmt_x(x: Decimal | int | str, places: int = 2) -> str:
    """2.7143 -> '2.71' (half-up)."""
    return str(Decimal(x).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one}" if n == 1 else f"{n} {many or one + 's'}"


# --------------------------------------------------------------------------- #
# Dates and pay periods
# --------------------------------------------------------------------------- #
def pay_period_of(d: date) -> str:
    """Semi-monthly pay period id, e.g. 2026-08-A (days 1-15) / 2026-08-B (16-end)."""
    return f"{d.year}-{d.month:02d}-{'A' if d.day <= 15 else 'B'}"


def period_bounds(period: str) -> tuple[date, date]:
    """First and last calendar day of a 'YYYY-MM' period."""
    year, month = int(period[:4]), int(period[5:7])
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def in_period(d: date, period: str) -> bool:
    return f"{d.year}-{d.month:02d}" == period


def lag_hours(entry: TimeEntry) -> Decimal:
    """Hours between the end of the work date and when the entry was created.
    Negative when the entry was created on or before the end of the work day."""
    end_of_day = datetime.combine(entry.work_date + timedelta(days=1), time.min)
    delta = entry.entered_at - end_of_day
    seconds = delta.days * 86400 + delta.seconds
    return Decimal(seconds) / Decimal(3600)


# --------------------------------------------------------------------------- #
# Cost and exposure
# --------------------------------------------------------------------------- #
def entry_cost(view: WorkingView, entry: TimeEntry) -> Decimal:
    """Unrounded hours x the employee's loaded rate."""
    return entry.hours * view.loaded_rate(entry.employee_id)


def entry_costs(view: WorkingView, entries: Iterable[TimeEntry]) -> dict[str, Decimal]:
    """entry_id -> unrounded cost, sorted by entry id (deterministic). Stored on
    a finding's `computed["entry_costs"]` so M13 can de-duplicate without the view."""
    return {e.entry_id: entry_cost(view, e) for e in sorted(entries, key=lambda x: x.entry_id)}


def labor_cost_basis(view: WorkingView) -> Decimal:
    """Sum of entry hours x loaded rate over all entries: the materiality basis
    (materiality_usd = materiality_pct x this)."""
    return money(sum((entry_cost(view, e) for e in view.time_entries), ZERO))


def total_exposure(findings: Sequence[Finding]) -> Decimal:
    """M13: total exposure, de-duplicated.

    Findings priced any way other than hours x loaded rate over entries leave
    `exposure_entry_ids` empty and are simply summed. Entry-priced findings list
    their entry ids; an entry counted by two findings contributes once (union).
    Rounding is applied once, to the union sum, so a single entry-priced finding
    always totals to exactly its own `exposure_usd`.
    """
    non_entry = ZERO
    union: dict[str, Decimal] = {}
    for f in findings:
        if not f.exposure_entry_ids:
            non_entry += f.exposure_usd
            continue
        costs = f.computed.get("entry_costs")
        if not isinstance(costs, dict):
            raise ValueError(
                f"finding {f.fingerprint} lists exposure_entry_ids but has no computed['entry_costs']"
            )
        for eid in f.exposure_entry_ids:
            if eid not in costs:
                raise ValueError(f"finding {f.fingerprint} has no entry cost for {eid}")
            union[eid] = Decimal(str(costs[eid]))
    return money(non_entry) + money(sum(union.values(), ZERO))


# --------------------------------------------------------------------------- #
# Baselines and trend
# --------------------------------------------------------------------------- #
def baseline_history(view: WorkingView, key: str, n: int = BASELINE_WINDOW) -> list[Decimal]:
    """The last `n` retained values of metric `key` from periods BEFORE view.period,
    oldest first."""
    periods = sorted(p for p, m in view.baselines.items() if p < view.period and key in m)
    return [Decimal(view.baselines[p][key]) for p in periods[-n:]]


def baseline_mean(history: Sequence[Decimal]) -> Decimal | None:
    if not history:
        return None
    return ratio(sum(history, ZERO), len(history), "0.000001")


def has_baseline(history: Sequence[Decimal]) -> bool:
    return len(history) >= MIN_HISTORY_PERIODS


def is_rising(current: Decimal, history: Sequence[Decimal]) -> bool:
    """A metric is rising when the current value is a new high over the retained
    history. With no history it is not called rising (callers treat that case as
    'no trend history', which classify() handles conservatively)."""
    return bool(history) and current > max(history)


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #
def entry_evidence(e: TimeEntry) -> EvidenceRef:
    return EvidenceRef(
        kind="time_entry",
        ref_id=e.entry_id,
        source_file=e.lineage.source_file,
        sha256=e.lineage.sha256,
        row=e.lineage.row,
        detail={
            "employee_id": e.employee_id,
            "work_date": e.work_date.isoformat(),
            "hours": str(e.hours),
            "charge_code": e.charge_code,
            "labor_category": e.labor_category,
            "entered_at": e.entered_at.isoformat(),
        },
    )


def entries_evidence(entries: Iterable[TimeEntry]) -> tuple[EvidenceRef, ...]:
    return tuple(entry_evidence(e) for e in sorted(entries, key=lambda x: x.entry_id))


def employee_evidence(emp: Employee) -> EvidenceRef:
    return EvidenceRef(
        kind="employee",
        ref_id=emp.employee_id,
        source_file=emp.lineage.source_file,
        sha256=emp.lineage.sha256,
        row=emp.lineage.row,
        detail={"name": emp.name, "title": emp.title, "exempt_status": emp.exempt_status},
    )


def contracts_of_entries(view: WorkingView, entries: Iterable[TimeEntry]) -> tuple[str, ...]:
    ids: set[str] = set()
    for e in entries:
        m = view.charge_codes.get(e.charge_code)
        if m is not None and m.contract_id:
            ids.add(m.contract_id)
    return tuple(sorted(ids))


def sorted_unique(items: Iterable[Any]) -> tuple[Any, ...]:
    return tuple(sorted(set(items)))


# --------------------------------------------------------------------------- #
# DCAA cost accounting: indirect rate inputs (DCAA_DATA_SPEC section 1.3)
# --------------------------------------------------------------------------- #
def previous_period(period: str) -> str:
    """The calendar month before a 'YYYY-MM' period, e.g. '2026-01' -> '2025-12'."""
    year, month = int(period[:4]), int(period[5:7])
    return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"


def is_labor_line(view: WorkingView, account: str) -> bool:
    """GL account category. An account absent from a non-empty category table, and every account when the
    table is empty (not provided), is treated as labor: the pre-DCAA behaviour."""
    return view.account_categories.get(account, "labor") == "labor"


@dataclass(frozen=True)
class RateInputs:
    """Period totals the indirect rate formulas need. All Decimal, unrounded (GL amounts are cents)."""

    direct_labor: Decimal  # cost type direct, labor account
    direct_non_labor: Decimal  # cost type direct, non-labor account
    pools: dict[str, Decimal]  # pool -> sum of indirect lines (labor and non-labor)

    def pool(self, name: str) -> Decimal:
        return self.pools.get(name, ZERO)

    def base(self, base_definition: str) -> Decimal | None:
        """The rate base for a closed-vocabulary base definition, or None when the engine does not know it."""
        if base_definition == "direct_labor":
            return self.direct_labor
        if base_definition == "total_cost_input":
            return self.direct_labor + self.direct_non_labor + self.pool("fringe") + self.pool("overhead")
        return None


def rate_inputs(view: WorkingView, period: str) -> RateInputs:
    dl = dnl = ZERO
    pools: dict[str, Decimal] = {}
    for g in view.gl_lines:
        if g.period != period:
            continue
        if g.cost_type == "direct":
            if is_labor_line(view, g.account):
                dl += g.amount
            else:
                dnl += g.amount
        elif g.cost_type == "indirect" and g.pool:
            pools[g.pool] = pools.get(g.pool, ZERO) + g.amount
    return RateInputs(dl, dnl, pools)
