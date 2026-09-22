"""CSV/JSON ingestion into the canonical per-run working view (design 11).

Reads only files named by `sources.json` for the tiers' sources, applies the
customer-confirmed column mappings (config/mappings/*.yaml), and attaches
`Lineage(source_file, sha256, row)` to every record. `row` is the 1-based CSV row
number INCLUDING the header, so the first data row is 2 and evidence reads
`meridian_time_2026-08.csv:1188`. For contracts.json `row` is the 1-based array index.

Nothing here reads a clock, the environment or the network. The engine never reads
ground_truth.json.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from engine.canonical import (
    SOURCES,
    ChargeCodeMap,
    Contract,
    Employee,
    GLLine,
    IcsRecord,
    LaborCategory,
    Lineage,
    PayRecord,
    RateAgreement,
    TimeEdit,
    TimeEntry,
    WorkingView,
)
from engine.results import RunBlocked
from engine.rules.base import TIER_SOURCES, Tier

MAPPINGS_DIR = Path(__file__).resolve().parents[1] / "config" / "mappings"
SOURCES_INDEX = "sources.json"

# Confirmed reference tables: always loaded, never tier-gated, headers fixed by DATA_SPEC 1.7.
CHARGE_CODES_FILE = "charge_codes.csv"
CROSSWALK_FILE = "labor_category_crosswalk.csv"
LOADED_RATES_FILE = "loaded_rates.csv"
CONTRACTS_FILE = "contracts.json"
METRIC_HISTORY_FILE = "metric_history.json"
# Optional confirmed reference tables (DCAA cost accounting). Loaded whenever the file exists, silently
# empty when it does not, so a data folder from before the DCAA domain keeps working unchanged.
ACCOUNT_CATEGORIES_FILE = "account_categories.csv"
CLASSIFICATION_HISTORY_FILE = "classification_history.json"
ACCOUNT_ALLOWABILITY_FILE = "account_allowability.csv"
ICS_SUBMISSIONS_FILE = "ics_submissions.csv"
OPTIONAL_REFERENCE_FILES = (ACCOUNT_CATEGORIES_FILE, CLASSIFICATION_HISTORY_FILE, ACCOUNT_ALLOWABILITY_FILE,
                            ICS_SUBMISSIONS_FILE)
ALLOWABILITY_CLASSES = ("allowable", "unallowable", "conditional")

# Closed vocabularies of the provisional rate file. The engine, not the file, knows how each base is computed.
RATE_POOLS = ("fringe", "overhead", "ga")
RATE_BASES = ("direct_labor", "total_cost_input")
ACCOUNT_CATEGORIES = ("labor", "non_labor")

# source -> canonical dataset name inside a mapping file's `columns:` block.
# The two timekeeping datasets live in one mapping file (unanet.yaml).
DATASET_FOR_SOURCE = {
    "timekeeping": "timekeeping",
    "time_edits": "time_edits",
    "payroll": "payroll",
    "gl": "gl",
    "hris": "hris",
    "rate_data": "rate_data",
}
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "timekeeping": ("entry_id", "employee_id", "work_date", "hours", "charge_code", "labor_category",
                    "entered_at", "submitted_at", "approved_by", "approved_at"),
    "time_edits": ("edit_id", "entry_id", "edited_at", "editor_id", "old_value", "new_value", "reason"),
    "payroll": ("employee_id", "pay_period", "regular_hours", "overtime_hours", "pto_hours", "gross_pay"),
    "gl": ("account", "project", "period", "amount", "cost_type", "pool"),
    "hris": ("employee_id", "name", "title", "hire_date", "term_date", "exempt_status", "home_department"),
    "rate_data": ("pool", "base_definition", "provisional_rate", "effective_from", "effective_to", "source_document"),
}


@dataclass(frozen=True)
class InputFile:
    source: str
    file: str
    sha256: str
    rows: int
    data_as_of: str | None


@dataclass(frozen=True)
class IngestResult:
    view: WorkingView
    inputs: tuple[InputFile, ...]
    sources_present: frozenset[str]
    sources_absent: frozenset[str]
    mapping_versions: dict[str, int]
    warnings: tuple[str, ...]
    # Confirmed reference tables pinned for replay (they change results, so they are hashed too).
    reference_tables: tuple[InputFile, ...] = ()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Mapping files
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class _Mapping:
    mapping_id: str
    version: int
    source: str
    columns: dict[str, str]  # source column -> canonical field, for one dataset
    value_maps: dict[str, dict[str, str]]
    # Columns that MAY be absent from the file (read as "" when they are). A mapped `columns` entry that is
    # missing still blocks the run; an `optional_columns` entry does not.
    optional_columns: dict[str, str] = field(default_factory=dict)


def _load_mappings(mappings_dir: Path) -> dict[str, _Mapping]:
    """dataset -> mapping. A mapping file must carry mapping_id, version, source, columns."""
    found: dict[str, _Mapping] = {}
    for path in sorted(Path(mappings_dir).glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not all(k in doc for k in ("mapping_id", "version", "source", "columns")):
            raise RunBlocked("bad_mapping", f"{path.name} must define mapping_id, version, source and columns")
        if not isinstance(doc["version"], int) or not isinstance(doc["columns"], dict):
            raise RunBlocked("bad_mapping", f"{path.name}: version must be a whole number and columns a mapping")
        vmaps = doc.get("value_maps") or {}
        for dataset, cols in doc["columns"].items():
            if not isinstance(cols, dict):
                raise RunBlocked("bad_mapping", f"{path.name}: columns.{dataset} must map source column to canonical field")
            found[str(dataset)] = _Mapping(
                mapping_id=str(doc["mapping_id"]),
                version=doc["version"],
                source=str(doc["source"]),
                columns={str(k): str(v) for k, v in cols.items()},
                value_maps={str(k): {str(a): str(b) for a, b in v.items()} for k, v in (vmaps.get(dataset) or {}).items()},
                optional_columns={str(k): str(v) for k, v in ((doc.get("optional_columns") or {}).get(dataset) or {}).items()},
            )
    return found


# --------------------------------------------------------------------------- #
# Low-level readers
# --------------------------------------------------------------------------- #
def _read_csv(path: Path) -> tuple[list[str], list[tuple[int, list[str]]]]:
    """(header, [(row_number, cells)]) with row_number 1-based including the header."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            header = [h.strip() for h in next(reader)]
        except StopIteration:
            raise RunBlocked("schema_mismatch", f"{path.name} is empty (no header row)") from None
        rows = [(i, cells) for i, cells in enumerate(reader, start=2) if any(c.strip() for c in cells)]
    return header, rows


def _records(path: Path, columns: dict[str, str] | None, *, fixed: tuple[str, ...] | None = None,
             optional: dict[str, str] | None = None):
    """Yield (row_number, {canonical_or_header: stripped str}). With `columns` (a mapping)
    source headers are renamed to canonical fields; with `fixed` the raw headers are kept."""
    header, rows = _read_csv(path)
    wanted = list(columns) if columns is not None else list(fixed or ())
    missing = [c for c in wanted if c not in header]
    if missing:
        raise RunBlocked(
            "schema_mismatch",
            f"{path.name} is missing expected column(s) {', '.join(repr(m) for m in missing)}; "
            "the confirmed mapping no longer matches the file",
        )
    idx = {h: i for i, h in enumerate(header)}
    for rownum, cells in rows:
        rec = {}
        for col in wanted:
            i = idx[col]
            rec[columns[col] if columns is not None else col] = cells[i].strip() if i < len(cells) else ""
        for col, name in (optional or {}).items():  # may be absent from the file: "" then
            i = idx.get(col)
            rec[name] = cells[i].strip() if i is not None and i < len(cells) else ""
        yield rownum, rec


class _Bad(Exception):
    pass


def _dec(s: str) -> Decimal:
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise _Bad(f"{s!r} is not a number") from None
    if not d.is_finite():
        raise _Bad(f"{s!r} is not a finite number")
    return d


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError:
        raise _Bad(f"{s!r} is not an ISO date") from None


def _dt(s: str) -> datetime:
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        raise _Bad(f"{s!r} is not an ISO datetime") from None


def _req(rec: dict[str, str], key: str) -> str:
    v = rec[key]
    if v == "":
        raise _Bad(f"{key} is empty")
    return v


def _opt(v: str) -> str | None:
    return v or None


def _guard(file: str, rownum: int, fn):
    try:
        return fn()
    except _Bad as exc:
        raise RunBlocked("bad_value", f"{file}:{rownum}: {exc}") from None


# --------------------------------------------------------------------------- #
# Builders (canonical records)
# --------------------------------------------------------------------------- #
def _lineage(file: str, sha: str, row: int) -> Lineage:
    return Lineage(source_file=file, sha256=sha, row=row)


def _build_time_entries(path: Path, sha: str, m: _Mapping) -> list[TimeEntry]:
    out = []
    for row, r in _records(path, m.columns):
        out.append(
            _guard(
                path.name,
                row,
                lambda r=r, row=row: TimeEntry(
                    entry_id=_req(r, "entry_id"),
                    employee_id=_req(r, "employee_id"),
                    work_date=_date(_req(r, "work_date")),
                    hours=_dec(_req(r, "hours")),
                    charge_code=_req(r, "charge_code"),
                    labor_category=_req(r, "labor_category"),
                    entered_at=_dt(_req(r, "entered_at")),
                    submitted_at=_dt(r["submitted_at"]) if r["submitted_at"] else None,
                    approved_by=_opt(r["approved_by"]),
                    approved_at=_dt(r["approved_at"]) if r["approved_at"] else None,
                    lineage=_lineage(path.name, sha, row),
                ),
            )
        )
    return out


def _build_time_edits(path: Path, sha: str, m: _Mapping) -> list[TimeEdit]:
    out = []
    for row, r in _records(path, m.columns):
        out.append(
            _guard(
                path.name,
                row,
                lambda r=r, row=row: TimeEdit(
                    edit_id=_req(r, "edit_id"),
                    entry_id=_req(r, "entry_id"),
                    edited_at=_dt(_req(r, "edited_at")),
                    editor_id=_req(r, "editor_id"),
                    old_value=r["old_value"],
                    new_value=r["new_value"],
                    reason=_opt(r["reason"]),  # None == undocumented edit
                    lineage=_lineage(path.name, sha, row),
                ),
            )
        )
    return out


def _build_pay(path: Path, sha: str, m: _Mapping) -> list[PayRecord]:
    out = []
    for row, r in _records(path, m.columns):
        out.append(
            _guard(
                path.name,
                row,
                lambda r=r, row=row: PayRecord(
                    employee_id=_req(r, "employee_id"),
                    pay_period=_req(r, "pay_period"),
                    regular_hours=_dec(_req(r, "regular_hours")),
                    overtime_hours=_dec(_req(r, "overtime_hours")),
                    pto_hours=_dec(_req(r, "pto_hours")),
                    gross_pay=_dec(_req(r, "gross_pay")),
                    lineage=_lineage(path.name, sha, row),
                ),
            )
        )
    return out


def _build_gl(path: Path, sha: str, m: _Mapping) -> list[GLLine]:
    out = []
    for row, r in _records(path, m.columns):

        def build(r=r, row=row) -> GLLine:
            cost_type = _req(r, "cost_type").lower()
            if cost_type not in ("direct", "indirect"):
                raise _Bad(f"cost type {cost_type!r} is not direct or indirect")
            return GLLine(
                account=_req(r, "account"),
                project=_req(r, "project"),
                period=_req(r, "period"),
                amount=_dec(_req(r, "amount")),
                cost_type=cost_type,
                pool=_opt(r["pool"]),
                lineage=_lineage(path.name, sha, row),
            )

        out.append(_guard(path.name, row, build))
    return out


def _build_rates(path: Path, sha: str, m: _Mapping) -> list[RateAgreement]:
    out = []
    for row, r in _records(path, m.columns, optional=m.optional_columns):

        def build(r=r, row=row) -> RateAgreement:
            pool = _req(r, "pool")
            if pool not in RATE_POOLS:
                raise _Bad(f"pool {pool!r} is not one of {', '.join(RATE_POOLS)}")
            base = _req(r, "base_definition")
            if base not in RATE_BASES:
                raise _Bad(f"base definition {base!r} is not one of {', '.join(RATE_BASES)}")
            rate = _dec(_req(r, "provisional_rate"))
            if not (Decimal(0) < rate < Decimal(1)):
                raise _Bad(f"provisional rate {r['provisional_rate']!r} must be a fraction between 0 and 1 (for example 0.1000 for 10%)")
            start, end = _date(_req(r, "effective_from")), _date(_req(r, "effective_to"))
            if start > end:
                raise _Bad(f"effective from {start.isoformat()} is after effective to {end.isoformat()}")
            ceiling = None
            if r.get("ceiling_rate"):
                ceiling = _dec(r["ceiling_rate"])
                if not (Decimal(0) < ceiling < Decimal(1)):
                    raise _Bad(f"ceiling rate {r['ceiling_rate']!r} must be a fraction between 0 and 1 (for example 0.1200 for 12%)")
            return RateAgreement(
                pool=pool,
                base_definition=base,
                provisional_rate=rate,
                effective_from=start,
                effective_to=end,
                source_document=r["source_document"],
                lineage=_lineage(path.name, sha, row),
                ceiling_rate=ceiling,
            )

        out.append(_guard(path.name, row, build))
    return out


def _build_employees(path: Path, sha: str, m: _Mapping) -> list[Employee]:
    vmap = m.value_maps.get("exempt_status", {})
    out = []
    for row, r in _records(path, m.columns):

        def build(r=r, row=row) -> Employee:
            raw = _req(r, "exempt_status")
            status = vmap.get(raw) or vmap.get(raw.title())
            if status is None:
                raise _Bad(f"FLSA status {raw!r} has no confirmed mapping")
            return Employee(
                employee_id=_req(r, "employee_id"),
                name=r["name"],
                title=_req(r, "title"),
                hire_date=_date(_req(r, "hire_date")),
                term_date=_date(r["term_date"]) if r["term_date"] else None,
                exempt_status=status,
                home_department=r["home_department"],
                lineage=_lineage(path.name, sha, row),
            )

        out.append(_guard(path.name, row, build))
    return out


def _load_contracts(path: Path, sha: str) -> list[Contract]:
    try:
        arr = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise RunBlocked("bad_value", f"{path.name}: not valid JSON ({exc.msg} at line {exc.lineno})") from None
    if not isinstance(arr, list):
        raise RunBlocked("bad_value", f"{path.name} must be an array of contracts")
    out = []
    for i, c in enumerate(arr, start=1):
        try:
            out.append(
                Contract(
                    contract_id=c["contract_id"],
                    name=c["name"],
                    type=c["type"],
                    pop_start=_date(c["pop_start"]),
                    pop_end=_date(c["pop_end"]),
                    ceiling=_dec(str(c["ceiling"])),
                    funded_value=_dec(str(c["funded_value"])),
                    labor_categories=tuple(
                        LaborCategory(lc["name"], _dec(str(lc["ceiling_rate"])), lc.get("min_qualification", ""))
                        for lc in c.get("labor_categories", [])
                    ),
                    key_personnel=tuple(c.get("key_personnel", [])),
                    charge_codes=tuple(c.get("charge_codes", [])),
                    lineage=_lineage(path.name, sha, i),
                )
            )
        except (KeyError, TypeError, _Bad) as exc:
            raise RunBlocked("bad_value", f"{path.name}[{i}]: invalid contract ({exc})") from None
    return out


def _load_baselines(path: Path, period: str, warnings: list[str]) -> dict[str, dict[str, Decimal]]:
    doc = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    out: dict[str, dict[str, Decimal]] = {}
    dropped = set()
    for metric_key, by_period in (doc.get("metrics") or {}).items():
        for p, v in by_period.items():
            if p >= period:  # history is PRIOR runs only; never let the current period leak into its own baseline
                dropped.add(p)
                continue
            out.setdefault(p, {})[metric_key] = _dec(str(v))
    if dropped:
        warnings.append(f"metric_history.json periods {', '.join(sorted(dropped))} are not before {period} and were ignored")
    return out


def _load_account_categories(path: Path) -> tuple[dict[str, str], int]:
    """account -> "labor" | "non_labor". An account listed twice is refused rather than silently overridden."""
    rows = list(_records(path, None, fixed=("Account", "Category")))
    out: dict[str, str] = {}
    for row, r in rows:
        account, category = r["Account"], r["Category"]
        if not account:
            raise RunBlocked("bad_value", f"{path.name}:{row}: Account is empty")
        if category not in ACCOUNT_CATEGORIES:
            raise RunBlocked(
                "bad_value", f"{path.name}:{row}: category {category!r} is not one of {', '.join(ACCOUNT_CATEGORIES)}"
            )
        if account in out:
            raise RunBlocked("bad_value", f"{path.name}:{row}: account {account!r} is listed more than once")
        out[account] = category
    return out, len(rows)


def _load_account_allowability(path: Path) -> tuple[dict[str, tuple[str, str]], int]:
    """account -> (allowability, citation). Closed vocabulary; an account listed twice is refused."""
    rows = list(_records(path, None, fixed=("Account", "Allowability", "Citation")))
    out: dict[str, tuple[str, str]] = {}
    for row, r in rows:
        account, cls = r["Account"], r["Allowability"]
        if not account:
            raise RunBlocked("bad_value", f"{path.name}:{row}: Account is empty")
        if cls not in ALLOWABILITY_CLASSES:
            raise RunBlocked(
                "bad_value", f"{path.name}:{row}: allowability {cls!r} is not one of {', '.join(ALLOWABILITY_CLASSES)}"
            )
        if account in out:
            raise RunBlocked("bad_value", f"{path.name}:{row}: account {account!r} is listed more than once")
        out[account] = (cls, r["Citation"])
    return out, len(rows)


def _load_ics_submissions(path: Path, sha: str) -> tuple[list[IcsRecord], int]:
    """Incurred cost submissions by fiscal year. `Submitted On` may be blank (not yet submitted)."""
    rows = list(_records(path, None, fixed=("Fiscal Year", "Fiscal Year End", "Submitted On", "Source Document")))
    out: list[IcsRecord] = []
    seen: set[str] = set()
    for row, r in rows:

        def build(r=r, row=row) -> IcsRecord:
            fy = r["Fiscal Year"]
            if not fy:
                raise _Bad("Fiscal Year is empty")
            if fy in seen:
                raise _Bad(f"fiscal year {fy!r} is listed more than once")
            seen.add(fy)
            return IcsRecord(
                fiscal_year=fy,
                fiscal_year_end=_date(r["Fiscal Year End"]),
                submitted_on=_date(r["Submitted On"]) if r["Submitted On"] else None,
                source_document=r["Source Document"],
                lineage=_lineage(path.name, sha, row),
            )

        out.append(_guard(path.name, row, build))
    return out, len(rows)


def _load_classification_history(
    path: Path, period: str, warnings: list[str]
) -> tuple[dict[str, dict[str, tuple[Decimal, Decimal]]], int]:
    """employee_id -> period -> (direct_hours, indirect_hours). Like the metric baselines this is PRIOR runs
    only: a period that is not before the run period is ignored, so the current month never scores itself."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise RunBlocked("bad_value", f"{path.name}: not valid JSON ({exc.msg} at line {exc.lineno})") from None
    emps = doc.get("employees") if isinstance(doc, dict) else None
    if not isinstance(emps, dict):
        raise RunBlocked("bad_value", f"{path.name} must be an object with an 'employees' object")
    out: dict[str, dict[str, tuple[Decimal, Decimal]]] = {}
    dropped: set[str] = set()
    for emp_id in sorted(emps):
        by_period = emps[emp_id]
        if not isinstance(by_period, dict):
            raise RunBlocked("bad_value", f"{path.name}[{emp_id}]: must map periods to hours")
        kept: dict[str, tuple[Decimal, Decimal]] = {}
        for p in sorted(by_period):
            hours = by_period[p]
            try:
                direct, indirect = _dec(str(hours["direct_hours"])), _dec(str(hours["indirect_hours"]))
                if direct < 0 or indirect < 0:
                    raise _Bad("hours cannot be negative")
            except (KeyError, TypeError):
                raise RunBlocked(
                    "bad_value", f"{path.name}[{emp_id}][{p}]: needs direct_hours and indirect_hours"
                ) from None
            except _Bad as exc:
                raise RunBlocked("bad_value", f"{path.name}[{emp_id}][{p}]: {exc}") from None
            if p >= period:
                dropped.add(p)
                continue
            kept[p] = (direct, indirect)
        if kept:
            out[emp_id] = kept
    if dropped:
        warnings.append(
            f"{path.name} periods {', '.join(sorted(dropped))} are not before {period} and were ignored"
        )
    return out, len(emps)


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
def load_working_view(
    data_dir: Path, period: str, tier: Tier, *, mappings_dir: Path | None = None
) -> IngestResult:
    data_dir = Path(data_dir)
    warnings: list[str] = []

    index_path = data_dir / SOURCES_INDEX
    if not index_path.exists():
        raise RunBlocked("missing_input", f"{SOURCES_INDEX} not found in {data_dir}")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if index.get("period") not in (None, period):
        raise RunBlocked("period_mismatch", f"{SOURCES_INDEX} is for {index.get('period')}, not {period}")
    declared: dict[str, dict[str, Any]] = index.get("sources") or {}

    mappings = _load_mappings(mappings_dir or MAPPINGS_DIR)
    reference: list[InputFile] = []

    def ref_path(name: str, required: bool = True) -> Path | None:
        p = data_dir / name
        if not p.exists():
            if required:
                raise RunBlocked("missing_reference_table", f"{name} not found in {data_dir}")
            return None
        return p

    def pin_ref(p: Path, source: str, rows: int) -> str:
        sha = file_sha256(p)
        reference.append(InputFile(source, p.name, sha, rows, None))
        return sha

    # ---- tier gating: a source loads only if the tier includes it AND sources.json has it
    loadable = TIER_SOURCES[tier]
    to_load = [s for s in SOURCES if s in loadable and s in declared]
    # contracts.json is a confirmed reference table: always loaded when present
    if "contracts" not in to_load and (data_dir / CONTRACTS_FILE).exists():
        to_load.append("contracts")
    present: set[str] = set()
    inputs: list[InputFile] = []
    mapping_versions: dict[str, int] = {}
    view = WorkingView(period=period)

    # ---- reference tables (always loaded)
    cc_path = ref_path(CHARGE_CODES_FILE)
    cc_rows = list(_records(cc_path, None, fixed=("Charge", "Contract", "Cost Type", "Pool")))
    pin_ref(cc_path, "charge_codes", len(cc_rows))
    for row, r in cc_rows:
        cost_type = r["Cost Type"].lower()
        if cost_type not in ("direct", "indirect"):
            raise RunBlocked("bad_value", f"{cc_path.name}:{row}: cost type {r['Cost Type']!r} is not direct or indirect")
        view.charge_codes[r["Charge"]] = ChargeCodeMap(r["Charge"], _opt(r["Contract"]), cost_type, _opt(r["Pool"]))

    xw_path = ref_path(CROSSWALK_FILE)
    xw_rows = list(_records(xw_path, None, fixed=("HRIS Title", "Contract Category")))
    pin_ref(xw_path, "category_crosswalk", len(xw_rows))
    view.category_crosswalk = {r["HRIS Title"]: r["Contract Category"] for _, r in xw_rows}

    lr_path = ref_path(LOADED_RATES_FILE)
    lr_rows = list(_records(lr_path, None, fixed=("Employee #", "Loaded Rate")))
    pin_ref(lr_path, "loaded_rates", len(lr_rows))
    for row, r in lr_rows:
        view.loaded_rates[r["Employee #"]] = _guard(lr_path.name, row, lambda r=r: _dec(r["Loaded Rate"]))

    mh_path = ref_path(METRIC_HISTORY_FILE, required=False)
    if mh_path is not None:
        view.baselines = _load_baselines(mh_path, period, warnings)
        pin_ref(mh_path, "metric_history", sum(len(v) for v in view.baselines.values()))
    else:
        warnings.append(f"{METRIC_HISTORY_FILE} not found; trend and baseline metrics have no history")

    ac_path = ref_path(ACCOUNT_CATEGORIES_FILE, required=False)
    if ac_path is not None:
        view.account_categories, ac_rows = _load_account_categories(ac_path)
        pin_ref(ac_path, "account_categories", ac_rows)

    ch_path = ref_path(CLASSIFICATION_HISTORY_FILE, required=False)
    if ch_path is not None:
        view.classification_history, ch_rows = _load_classification_history(ch_path, period, warnings)
        pin_ref(ch_path, "classification_history", ch_rows)

    aa_path = ref_path(ACCOUNT_ALLOWABILITY_FILE, required=False)
    if aa_path is not None:
        view.account_allowability, aa_rows = _load_account_allowability(aa_path)
        pin_ref(aa_path, "account_allowability", aa_rows)

    ics_path = ref_path(ICS_SUBMISSIONS_FILE, required=False)
    if ics_path is not None:
        view.ics_submissions, ics_rows = _load_ics_submissions(ics_path, file_sha256(ics_path))
        pin_ref(ics_path, "ics_submissions", ics_rows)

    index_sha = file_sha256(index_path)
    reference.append(InputFile("sources_index", SOURCES_INDEX, index_sha, len(declared), None))

    # ---- sources
    for source in to_load:
        entry = declared.get(source, {})
        fname = entry.get("file", CONTRACTS_FILE if source == "contracts" else None)
        if not fname:
            raise RunBlocked("missing_input", f"{SOURCES_INDEX} lists {source} without a file name")
        path = data_dir / fname
        if not path.exists():
            raise RunBlocked("missing_input", f"{source} is declared as {fname} but that file is not in {data_dir}")
        sha = file_sha256(path)

        if source == "contracts":
            view.contracts = _load_contracts(path, sha)
            rows = len(view.contracts)
        else:
            m = mappings.get(DATASET_FOR_SOURCE[source])
            if m is None:
                raise RunBlocked("bad_mapping", f"no confirmed mapping for {source} in {mappings_dir or MAPPINGS_DIR}")
            unmapped = [f for f in REQUIRED_FIELDS[source] if f not in m.columns.values()]
            if unmapped:
                raise RunBlocked("bad_mapping", f"mapping {m.mapping_id} does not map required field(s) {', '.join(unmapped)}")
            mapping_versions[m.source] = m.version
            if source == "timekeeping":
                view.time_entries = _build_time_entries(path, sha, m)
                rows = len(view.time_entries)
            elif source == "time_edits":
                view.time_edits = _build_time_edits(path, sha, m)
                rows = len(view.time_edits)
            elif source == "payroll":
                view.pay_records = _build_pay(path, sha, m)
                rows = len(view.pay_records)
            elif source == "gl":
                view.gl_lines = _build_gl(path, sha, m)
                rows = len(view.gl_lines)
            elif source == "rate_data":
                view.rate_agreements = _build_rates(path, sha, m)
                rows = len(view.rate_agreements)
            else:  # hris
                view.employees = _build_employees(path, sha, m)
                rows = len(view.employees)
        present.add(source)
        inputs.append(InputFile(source, fname, sha, rows, entry.get("data_as_of")))

    # ---- charge codes: an unmapped code blocks the run (FR 3.2 / 2.4)
    unmapped_codes: dict[str, int] = {}
    for e in view.time_entries:
        if e.charge_code not in view.charge_codes:
            unmapped_codes.setdefault(e.charge_code, e.lineage.row)
    if unmapped_codes:
        first = next((e.lineage.source_file for e in view.time_entries), "timekeeping")
        listing = ", ".join(f"{c} ({first}:{r})" for c, r in sorted(unmapped_codes.items()))
        raise RunBlocked(
            "unmapped_charge_code",
            f"Charge code(s) not in {CHARGE_CODES_FILE}: {listing}. Map them before the run can proceed.",
        )

    # ---- a GL account the chart-of-accounts table does not know is read as labor (the pre-DCAA behaviour);
    # say so, because that is exactly the assumption the table exists to replace.
    if view.account_categories:
        unknown = sorted({g.account for g in view.gl_lines if g.account not in view.account_categories})
        if unknown:
            warnings.append(
                f"GL account(s) {', '.join(unknown)} are not in {ACCOUNT_CATEGORIES_FILE} and are treated as labor"
            )

    if view.account_allowability:
        unlisted = sorted({g.account for g in view.gl_lines if g.account not in view.account_allowability})
        if unlisted:
            warnings.append(
                f"GL account(s) {', '.join(unlisted)} are not in {ACCOUNT_ALLOWABILITY_FILE} and are treated as allowable"
            )

    view.sources_present = frozenset(present)
    return IngestResult(
        view=view,
        inputs=tuple(inputs),
        sources_present=frozenset(present),
        sources_absent=frozenset(SOURCES) - frozenset(present),
        mapping_versions=mapping_versions,
        warnings=tuple(warnings),
        reference_tables=tuple(reference),
    )
