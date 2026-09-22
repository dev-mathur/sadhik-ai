"""Canonical working-view record types.

Per docs/hld/system-design.md §11: these are a PER-RUN working view, rebuilt
from confirmed mappings on each run and discarded. They are not a system of
record and not a persistent ontology.

Every record carries lineage (source_file, sha256, row) so any finding traces
back to a raw uploaded cell (§2 invariant 5).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

SOURCES = ("timekeeping", "time_edits", "payroll", "gl", "hris", "contracts", "rate_data")


@dataclass(frozen=True)
class Lineage:
    """Where a canonical record came from in the raw upload."""

    source_file: str
    sha256: str
    row: int

    def cite(self) -> str:
        return f"{self.source_file}:{self.row}"


@dataclass(frozen=True)
class Employee:
    employee_id: str
    name: str
    title: str
    hire_date: date
    term_date: date | None
    exempt_status: str  # "exempt" | "non_exempt"
    home_department: str
    lineage: Lineage


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
    lineage: Lineage


@dataclass(frozen=True)
class TimeEdit:
    edit_id: str
    entry_id: str
    edited_at: datetime
    editor_id: str
    old_value: str
    new_value: str
    reason: str | None  # None == undocumented edit
    lineage: Lineage


@dataclass(frozen=True)
class PayRecord:
    employee_id: str
    pay_period: str  # e.g. "2026-08-A"
    regular_hours: Decimal
    overtime_hours: Decimal
    pto_hours: Decimal
    gross_pay: Decimal
    lineage: Lineage

    @property
    def total_hours(self) -> Decimal:
        return self.regular_hours + self.overtime_hours + self.pto_hours


@dataclass(frozen=True)
class GLLine:
    account: str
    project: str
    period: str  # "2026-08"
    amount: Decimal
    cost_type: str  # "direct" | "indirect"
    pool: str | None  # overhead | ga | fringe | None for direct
    lineage: Lineage


@dataclass(frozen=True)
class LaborCategory:
    name: str
    ceiling_rate: Decimal
    min_qualification: str


@dataclass(frozen=True)
class Contract:
    contract_id: str
    name: str
    type: str  # "T&M" | "CPFF" | "FFP"
    pop_start: date
    pop_end: date
    ceiling: Decimal
    funded_value: Decimal
    labor_categories: tuple[LaborCategory, ...]
    key_personnel: tuple[str, ...]
    charge_codes: tuple[str, ...]
    lineage: Lineage

    def category(self, name: str) -> LaborCategory | None:
        return next((c for c in self.labor_categories if c.name == name), None)


@dataclass(frozen=True)
class RateAgreement:
    """A provisional billing rate for one indirect pool (FR section 2, "Indirect rate data").

    `base_definition` is a closed vocabulary the engine understands; the rate file names the base,
    the engine (not the file) knows how to compute it from the GL:
      direct_labor      = GL lines with cost_type direct in a labor account
      total_cost_input  = direct labor + direct non-labor + fringe pool + overhead pool
    """

    pool: str  # "fringe" | "overhead" | "ga"
    base_definition: str  # "direct_labor" | "total_cost_input"
    provisional_rate: Decimal  # a fraction, e.g. Decimal("0.1000") for 10.00%
    effective_from: date
    effective_to: date
    source_document: str
    lineage: Lineage
    # Contractual ceiling on the billing rate for this pool (a cap in the contract), or None when the customer's
    # rate file carries none. Optional column "Ceiling Rate".
    ceiling_rate: Decimal | None = None


@dataclass(frozen=True)
class IcsRecord:
    """One fiscal year's incurred cost submission (FAR 52.216-7(d)): when the fiscal year ended and, if it has
    been made, when the submission went in. `submitted_on is None` means none is recorded."""

    fiscal_year: str
    fiscal_year_end: date
    submitted_on: date | None
    source_document: str
    lineage: Lineage


@dataclass(frozen=True)
class ChargeCodeMap:
    """Confirmed mapping of a timekeeping charge code to a contract or indirect
    account (FR §3.2). An unmapped code blocks the run."""

    charge_code: str
    contract_id: str | None  # None for indirect
    cost_type: str  # "direct" | "indirect"
    pool: str | None


@dataclass
class WorkingView:
    """The per-run projection the rules read. Rules never touch raw source rows."""

    period: str  # "2026-08"
    employees: list[Employee] = field(default_factory=list)
    time_entries: list[TimeEntry] = field(default_factory=list)
    time_edits: list[TimeEdit] = field(default_factory=list)
    pay_records: list[PayRecord] = field(default_factory=list)
    gl_lines: list[GLLine] = field(default_factory=list)
    contracts: list[Contract] = field(default_factory=list)
    charge_codes: dict[str, ChargeCodeMap] = field(default_factory=dict)
    # HRIS title -> contract labor category, confirmed by the customer (FR §3.3)
    category_crosswalk: dict[str, str] = field(default_factory=dict)
    # Loaded internal cost rate per employee, used for exposure on cost-type work
    loaded_rates: dict[str, Decimal] = field(default_factory=dict)
    # Trailing history for baseline metrics: period -> metric_id -> value
    baselines: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    # Which sources were actually present for this run
    sources_present: frozenset[str] = frozenset()
    # --- DCAA cost accounting domain (additive; empty means "not provided") ---
    # Provisional billing rates from the `rate_data` source.
    rate_agreements: list[RateAgreement] = field(default_factory=list)
    # Confirmed chart-of-accounts categories: account -> "labor" | "non_labor". EMPTY means the table was
    # not provided, and every GL line is then treated as labor (the pre-DCAA behaviour).
    account_categories: dict[str, str] = field(default_factory=dict)
    # Seeded stand-in for classification retained from prior runs (same technique as `baselines`):
    # employee_id -> period -> (direct_hours, indirect_hours). "Indirect" here means overhead-pool and
    # G&A-pool hours only; fringe-pool codes (fringe administration, leave) are excluded on both sides.
    classification_history: dict[str, dict[str, tuple[Decimal, Decimal]]] = field(default_factory=dict)
    # Confirmed allowability of each GL account: account -> (class, citation), class in
    # allowable | unallowable | conditional. EMPTY means the table was not provided (C-01 is then Not evaluated).
    account_allowability: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Incurred cost submissions by fiscal year. EMPTY means no schedule was provided (C-02 is then Not evaluated).
    ics_submissions: list[IcsRecord] = field(default_factory=list)

    def employee(self, employee_id: str) -> Employee | None:
        return next((e for e in self.employees if e.employee_id == employee_id), None)

    def contract(self, contract_id: str) -> Contract | None:
        return next((c for c in self.contracts if c.contract_id == contract_id), None)

    def contract_for_charge_code(self, charge_code: str) -> Contract | None:
        m = self.charge_codes.get(charge_code)
        if m is None or m.contract_id is None:
            return None
        return self.contract(m.contract_id)

    def loaded_rate(self, employee_id: str) -> Decimal:
        return self.loaded_rates.get(employee_id, Decimal("0.00"))
