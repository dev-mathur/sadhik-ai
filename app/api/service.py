"""Service layer: wraps the deterministic engine and persists what comes back.

Nothing here re-implements a rule, threshold, metric, rounding rule, status rollup or the
M13 exposure de-duplication. The engine is called for all of it:

  * runs            -> engine.runner.execute_run
  * replay          -> engine.manifest.replay
  * config          -> engine.config.validate_config / resolve_params
  * explanations    -> engine.explain.explain (called at run time, with the real Decimals)
  * status rollup   -> engine.rollup.rollup_domains (per enabled domain + overall), fed persisted
                       statuses through `is_open`
  * total exposure  -> engine.metrics.total_exposure (via rollup, over the OPEN findings)

What lives here is bookkeeping the engine deliberately does not own: identity across runs
(fingerprints), the disposition state machine, the clock, tier cadence dates, and HTTP shapes.
All money, hours and ratios leave this module as strings.
"""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from api.db import Database
from engine.config import CustomerConfig, resolve_domains, resolve_global, resolve_params, validate_config
from engine.explain import explain
from engine.floors import FloorParam, FloorRegistry, fmt, load_floors
from engine.manifest import replay as engine_replay
from engine.results import RunBlocked, jsonable
from engine.rollup import rollup_domains
from engine.rules.base import (
    DEFAULT_ENABLED_DOMAINS,
    DOMAIN_NAMES,
    TIER_ORDER,
    EvidenceRef,
    Finding,
    Result,
    RuleResult,
    RuleSpec,
    Severity,
    Tier,
    all_rules,
)
from engine.runner import execute_run

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "api" / "sadhik.db"
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "out"
DEFAULT_FLOORS = REPO_ROOT / "config" / "floors.yaml"
DEFAULT_SEED_CONFIG = REPO_ROOT / "config" / "meridian-rules.yaml"

FIRST_FINDING_NUMBER = 3311
CUSTOMER_CODE = "MER"

# ---- finding lifecycle (contracts/api.md, design 8.3) ------------------------------------------
STATES = ("open", "in_review", "confirmed", "legit_exception", "data_error", "remediated", "closed")
OPEN_STATES = frozenset({"open", "in_review", "confirmed", "data_error"})
# Human-triggered transitions. `closed -> open` is system-only (a recurring fingerprint), so it is
# deliberately absent here and `closed` offers no transition to a person.
TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("in_review",),
    "in_review": ("confirmed", "legit_exception", "data_error"),
    "confirmed": ("remediated",),
    "data_error": ("remediated",),
    "legit_exception": ("closed",),
    "remediated": ("closed",),
    "closed": (),
}
REASON_CODES = ("documented_correction", "approved_exception", "source_data_error", "policy_change", "other")
SEVERITIES = ("high", "medium", "low")
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}

CADENCE_DAYS = {"weekly": 7, "biweekly": 14, "semi_monthly": 15, "monthly": 30}
TIER_LABELS = {"fast": "Fast", "pay_period": "Pay period", "close": "Close"}
TIERS = tuple(TIER_LABELS)
_PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Domains the API can name. `DOMAIN_NAMES` (engine) holds the ones with rules; the two placeholders exist only so
# the status page can show them as "coming later". They are never enabled and have no rules.
PLACEHOLDER_DOMAINS: dict[str, str] = {"cmmc_evidence": "CMMC evidence", "proposals": "Proposals"}
DOMAIN_ORDER: tuple[str, ...] = (*DOMAIN_NAMES, *PLACEHOLDER_DOMAINS)
ALL_DOMAIN_NAMES: dict[str, str] = {**DOMAIN_NAMES, **PLACEHOLDER_DOMAINS}

_NOT_EVALUATED = Result.NOT_EVALUATED.value
_NOT_APPLICABLE = Result.NOT_APPLICABLE.value

Clock = Callable[[], datetime]

# Listing and status never need a finding's evidence or explanation (large); leave those columns unread.
_LIGHT_COLS = "seq, finding_id, fingerprint, period, rule_id, first_run_id, latest_run_id, status, finding_json"


class ApiError(Exception):
    """An error with an HTTP status and the `{error:{code,message}}` envelope. `body` replaces
    the envelope when the contract requires another shape (a ConfigResult)."""

    def __init__(self, status: int, code: str, message: str, body: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.body = body

    def payload(self) -> dict[str, Any]:
        return self.body if self.body is not None else {"error": {"code": self.code, "message": self.message}}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec_or_none(v: Any) -> Decimal | None:
    return None if v is None else Decimal(str(v))


def finding_from_json(d: dict[str, Any], evidence: list[dict[str, Any]] | tuple[()] = ()) -> Finding:
    """Rebuild an engine `Finding` from the stored JSON so the engine's own functions (rollup,
    total_exposure) can run over persisted findings. `computed` is kept as stored: its values are
    strings, and `total_exposure` reads `computed["entry_costs"]` through Decimal(str(...))."""
    return Finding(
        rule_id=d["rule_id"],
        rule_version=d["rule_version"],
        period=d["period"],
        authorities=tuple(d["authorities"]),
        basis=d["basis"],
        severity=Severity(d["severity"]),
        severity_reason=d["severity_reason"],
        headline=d["headline"],
        metric_id=d["metric_id"],
        metric_value=Decimal(str(d["metric_value"])),
        metric_numerator=_dec_or_none(d["metric_numerator"]),
        metric_denominator=_dec_or_none(d["metric_denominator"]),
        threshold_tripped=d["threshold_tripped"],
        computed=d["computed"],
        exposure_usd=Decimal(str(d["exposure_usd"])),
        exposure_entry_ids=tuple(d["exposure_entry_ids"]),
        employees=tuple(d["employees"]),
        contracts=tuple(d["contracts"]),
        evidence=tuple(EvidenceRef(**e) for e in evidence),
        fingerprint=d["fingerprint"],
        below_materiality=bool(d.get("below_materiality", False)),
    )


def _scalar(v: Any) -> str:
    """One scalar as text: null is empty, booleans are true/false, everything else its string form.
    (Engine figures are already strings; an int or a float, if one ever slips in, leaves as text too.)"""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _api_value(v: Any) -> str | None:
    """A `computed` value as the frontend renders it: always a string (contracts/api.md, DCAA additions 5).
    A list of scalars is joined with "; "; a list of mappings becomes "k=v, k=v" per item, joined with "; ";
    a nested mapping is omitted (None here, dropped by `_api_computed`)."""
    if isinstance(v, dict):
        return None
    if isinstance(v, (list, tuple)):
        parts = []
        for x in v:
            if isinstance(x, dict):
                parts.append(", ".join(f"{k}={_scalar(y)}" for k, y in x.items() if not isinstance(y, (dict, list, tuple))))
            elif isinstance(x, (list, tuple)):
                continue  # a list inside a list is not rendered
            else:
                parts.append(_scalar(x))
        return "; ".join(parts)
    return _scalar(v)


def _api_computed(c: dict[str, Any]) -> dict[str, str]:
    # Nested mappings are omitted. That includes `entry_costs`, the engine's private input to M13
    # de-duplication (one number per time entry, hundreds of them); it stays in storage.
    out: dict[str, str] = {}
    for k, v in c.items():
        rendered = _api_value(v)
        if rendered is not None:
            out[k] = rendered
    return out


def _config_error(e: Any) -> dict[str, Any]:
    return {"line": e.line, "path": e.path, "code": e.code, "message": e.message}


def _lower_first(s: str) -> str:
    return s[:1].lower() + s[1:] if s[1:2].islower() else s


class Service:
    def __init__(
        self,
        db_path: str | Path,
        data_dir: str | Path,
        floors_path: str | Path | None = None,
        seed_config_path: str | Path | None = None,
        clock: Clock | None = None,
    ):
        self.db = Database(db_path)
        self.data_dir = Path(data_dir)
        self.floors_path = Path(floors_path) if floors_path else DEFAULT_FLOORS
        self.seed_config_path = Path(seed_config_path) if seed_config_path else DEFAULT_SEED_CONFIG
        self.clock: Clock = clock or _utc_now
        self.registry: FloorRegistry = load_floors(self.floors_path)
        self._lock = threading.RLock()
        self.db.init()
        self._seed_config()

    # ------------------------------------------------------------------ helpers
    def _specs(self) -> list[RuleSpec]:
        return all_rules()

    def _spec_map(self) -> dict[str, RuleSpec]:
        return {s.id: s for s in self._specs()}

    def _domain_of(self, rule_id: str) -> str:
        """A finding's or rule's domain comes from its RuleSpec. A stored rule id the engine no longer
        registers reads as labor (the domain every rule had before domains existed)."""
        spec = self._spec_map().get(rule_id)
        return spec.domain if spec is not None else DEFAULT_ENABLED_DOMAINS[0]

    def _domain_fields(self, rule_id: str) -> dict[str, str]:
        d = self._domain_of(rule_id)
        return {"domain": d, "domain_name": ALL_DOMAIN_NAMES.get(d, d)}

    @staticmethod
    def _check_domain(domain: str | None) -> None:
        if domain is not None and domain not in ALL_DOMAIN_NAMES:
            raise ApiError(400, "bad_filter", f"Unknown domain; use one of {', '.join(ALL_DOMAIN_NAMES)}")

    def _enabled_domains(self, cfg: CustomerConfig | None, latest_manifest: dict[str, Any] | None) -> tuple[str, ...]:
        """The domains the latest run of the period actually ran (its manifest), else the active config."""
        if latest_manifest is not None:
            listed = latest_manifest.get("enabled_domains")
            if isinstance(listed, list) and listed:
                return tuple(str(d) for d in listed)
        return resolve_domains(cfg)

    def _today(self) -> date:
        return self.clock().astimezone(timezone.utc).date()

    def _parse_config(self, text: str) -> CustomerConfig | None:
        return validate_config(text, self.registry, self._specs(), None).config

    def _seed_config(self) -> None:
        with self._lock, self.db.write() as conn:
            if conn.execute("SELECT 1 FROM config_versions LIMIT 1").fetchone():
                return
            text = self.seed_config_path.read_text(encoding="utf-8")
            res = validate_config(text, self.registry, self._specs(), None)
            if not res.accepted or res.config is None:
                first = "; ".join(e.message for e in res.errors[:3])
                raise RuntimeError(f"the seed rules config {self.seed_config_path} was rejected: {first}")
            cfg = res.config
            # The seed keeps the version number the file declares (the sample history starts there);
            # every later version is the previous number plus one.
            self._insert_config(conn, cfg.config_version, text, res.sha256, cfg)

    def _insert_config(self, conn, version: int, text: str, sha: str, cfg: CustomerConfig) -> None:
        conn.execute(
            "INSERT INTO config_versions (version, yaml, sha256, changed_by, approved_by, reason,"
            " effective_from, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                version,
                text,
                sha,
                cfg.changed_by,
                cfg.approved_by,
                cfg.reason,
                cfg.effective_from.isoformat(),
                _ts(self.clock()),
            ),
        )

    def _active_config_row(self, conn):
        return conn.execute("SELECT * FROM config_versions ORDER BY version DESC LIMIT 1").fetchone()

    def default_period(self, conn=None) -> str:
        """The period in the data dir's sources.json; else the newest run's period; else this month."""
        idx = self.data_dir / "sources.json"
        try:
            period = json.loads(idx.read_text(encoding="utf-8")).get("period")
            if isinstance(period, str) and _PERIOD_RE.match(period):
                return period
        except (OSError, ValueError):
            pass
        if conn is not None:
            row = conn.execute("SELECT period FROM runs ORDER BY seq DESC LIMIT 1").fetchone()
            if row:
                return row["period"]
        return self._today().strftime("%Y-%m")

    @staticmethod
    def _check_period(period: str | None) -> None:
        if period is not None and not _PERIOD_RE.match(period):
            raise ApiError(400, "bad_filter", "period must look like 2026-08")

    # ------------------------------------------------------------------ health
    def health(self) -> dict[str, Any]:
        return {"ok": True}

    # ------------------------------------------------------------------ config
    def get_config(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM config_versions ORDER BY version DESC").fetchall()
        active = rows[0]
        return {
            "config_version": active["version"],
            "sha256": active["sha256"],
            "yaml": active["yaml"],
            "floor_registry_version": self.registry.version,
            "history": [
                {
                    "config_version": r["version"],
                    "changed_by": r["changed_by"],
                    "approved_by": r["approved_by"],
                    "reason": r["reason"],
                    "effective_from": r["effective_from"],
                    "sha256": r["sha256"],
                }
                for r in rows
            ],
        }

    def _config_result(self, text: str, conn) -> tuple[dict[str, Any], Any]:
        """Run the engine's gate against `text`, with the active config as `previous` so approval
        on loosening is enforced. Returns (ConfigResult body, engine result)."""
        active = self._active_config_row(conn)
        previous = self._parse_config(active["yaml"])
        res = validate_config(text, self.registry, self._specs(), previous)
        next_version = active["version"] + 1
        warnings = list(res.warnings)
        if res.accepted and res.config is not None and res.config.config_version != next_version:
            warnings.append(
                f"The file declares config_version {res.config.config_version}; "
                f"it will be saved as version {next_version}"
            )
        body = {
            "accepted": res.accepted,
            "errors": [_config_error(e) for e in res.errors],
            "warnings": warnings,
            "sha256": res.sha256,
            "config_version": next_version if res.accepted else None,
        }
        return body, res

    def validate_config(self, text: str) -> dict[str, Any]:
        """Dry run: always a ConfigResult body, persists nothing."""
        with self.db.read() as conn:
            body, _ = self._config_result(text, conn)
        return body

    def put_config(self, text: str) -> dict[str, Any]:
        with self._lock, self.db.write() as conn:
            body, res = self._config_result(text, conn)
            if not res.accepted or res.config is None:
                raise ApiError(422, "config_rejected", "The rules config was rejected", body=body)
            self._insert_config(conn, body["config_version"], text, res.sha256, res.config)
        return body

    # ------------------------------------------------------------------ persisted -> engine objects
    def _period_findings(self, conn, period: str) -> list[tuple[Any, Finding]]:
        out = []
        for row in conn.execute(f"SELECT {_LIGHT_COLS} FROM findings WHERE period=? ORDER BY seq", (period,)):
            out.append((row, finding_from_json(json.loads(row["finding_json"]))))
        return out

    def _rule_state(self, conn, period: str) -> dict[str, dict[str, str]]:
        """Per rule: the most recent run in the period that EVALUATED it (a Not applicable rule counts
        as decided); a rule no run evaluated is Not evaluated.

        The reason reported for a Not evaluated rule comes from the most recent run in which the rule was
        IN SCOPE (run tier >= rule tier). A fast run reports "not part of the fast run" for every
        pay-period and close rule; that is a scoping note, not a reason, and must not hide the real one
        ("rate data was not uploaded") from an earlier close run. Falls back to the newest run's reason
        only when no run has ever put the rule in scope."""
        rows = conn.execute(
            "SELECT rr.rule_id, rr.result, rr.reason, rr.run_id, r.tier AS run_tier FROM run_rules rr"
            " JOIN runs r ON r.run_id = rr.run_id WHERE r.period=? ORDER BY r.seq DESC",
            (period,),
        ).fetchall()
        rule_tier = {s.id: s.tier for s in all_rules()}
        decided: dict[str, dict[str, str]] = {}
        undecided: dict[str, dict[str, str]] = {}
        undecided_in_scope: dict[str, dict[str, str]] = {}
        for r in rows:
            entry = {"result": r["result"], "reason": r["reason"], "run_id": r["run_id"]}
            if r["result"] == _NOT_EVALUATED:
                undecided.setdefault(r["rule_id"], entry)
                rt = rule_tier.get(r["rule_id"])
                if rt is not None and TIER_ORDER[Tier(r["run_tier"])] >= TIER_ORDER[rt]:
                    undecided_in_scope.setdefault(r["rule_id"], entry)
            else:
                decided.setdefault(r["rule_id"], entry)
        undecided = {rid: undecided_in_scope.get(rid, e) for rid, e in undecided.items()}
        for rid, entry in undecided.items():
            decided.setdefault(rid, entry)
        return decided

    # ------------------------------------------------------------------ rules
    def _param_view(self, spec: RuleSpec, cfg: CustomerConfig | None) -> list[dict[str, Any]]:
        try:
            effective = resolve_params(spec, cfg, self.registry)
        except RunBlocked:  # a value that bypassed validation; show the defaults rather than fail the page
            effective = resolve_params(spec, None, self.registry)
        out = []
        for name, ps in spec.parameters.items():
            fp: FloorParam | None = self.registry.rules.get(spec.id, {}).get(name)
            if fp is not None:
                default, direction, lo, hi, floor = fp.default, fp.direction, fp.min, fp.max, fp.floor
            else:  # no registry entry: the rule's own declaration (no floor)
                default, direction, lo, hi, floor = ps.default, ps.direction, ps.min, ps.max, None
            out.append(
                {
                    "name": name,
                    "default": fmt(default),
                    "effective": fmt(effective[name]),
                    "direction": direction.value,
                    "min": None if lo is None else fmt(lo),
                    "max": None if hi is None else fmt(hi),
                    "floor": None if floor is None else fmt(floor),
                    "unit": ps.unit,
                    "description": ps.description,
                }
            )
        return out

    def _rule_dict(self, spec: RuleSpec, cfg: CustomerConfig | None, state: dict[str, dict[str, str]]) -> dict[str, Any]:
        body = cfg.rules.get(spec.id, {}) if cfg else {}
        status = "not_applicable" if body.get("status") == "not_applicable" else (
            "disabled" if body.get("enabled") is False else "active"
        )
        return {
            "id": spec.id,
            "version": spec.version,
            "title": spec.title,
            "authorities": list(spec.authorities),
            "basis": spec.basis,
            "tier": spec.tier.value,
            "required_sources": list(spec.required_sources),
            "review_status": spec.review_status,
            "status": status,
            "last_result": state.get(spec.id, {}).get("result", _NOT_EVALUATED),
            "parameters": self._param_view(spec, cfg),
            "domain": spec.domain,
            "domain_name": ALL_DOMAIN_NAMES.get(spec.domain, spec.domain),
        }

    @staticmethod
    def _rule_order(spec: RuleSpec) -> tuple:
        # Rules that count toward coverage first, data-quality gates after, each by id.
        return (0 if spec.counts_toward_coverage else 1, spec.id)

    def list_rules(self, domain: str | None = None) -> dict[str, Any]:
        self._check_domain(domain)
        with self.db.read() as conn:
            cfg = self._parse_config(self._active_config_row(conn)["yaml"])
            state = self._rule_state(conn, self.default_period(conn))
        specs = sorted((s for s in self._specs() if domain is None or s.domain == domain), key=self._rule_order)
        return {
            "floor_registry_version": self.registry.version,
            "items": [self._rule_dict(s, cfg, state) for s in specs],
        }

    def get_rule(self, rule_id: str) -> dict[str, Any]:
        spec = self._spec_map().get(rule_id)
        if spec is None:
            raise ApiError(404, "rule_not_found", f"No rule with id {rule_id}")
        with self.db.read() as conn:
            cfg = self._parse_config(self._active_config_row(conn)["yaml"])
            state = self._rule_state(conn, self.default_period(conn))
        return self._rule_dict(spec, cfg, state)

    # ------------------------------------------------------------------ runs
    def _blocked(self, exc: RunBlocked, active_sha: str) -> ApiError:
        if exc.code == "config_rejected":
            errors = [_config_error(e) for e in getattr(exc, "errors", ())]
            # The ConfigResult keys, plus the standard envelope so every client can read a message.
            body = {
                "accepted": False,
                "errors": errors,
                "warnings": [],
                "sha256": active_sha,
                "config_version": None,
                "error": {"code": "config_rejected", "message": exc.message},
            }
            return ApiError(422, "config_rejected", exc.message, body=body)
        if exc.code == "inputs_changed":
            return ApiError(409, exc.code, exc.message)
        if exc.code == "rule_failed":
            return ApiError(500, exc.code, exc.message)
        return ApiError(422, exc.code, exc.message)

    def create_run(self, period: str, tier: str) -> dict[str, Any]:
        if not _PERIOD_RE.match(period or ""):
            raise ApiError(422, "invalid_period", "period must look like 2026-08")
        if tier not in TIERS:
            raise ApiError(422, "invalid_tier", "tier must be one of fast, pay_period, close")
        with self._lock:
            with self.db.read() as conn:
                active = self._active_config_row(conn)
                n = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE period=?", (period,)).fetchone()["n"]
            run_id = f"RUN-{period}-{CUSTOMER_CODE}-{n + 1:03d}"
            executed_at = _ts(self.clock())
            try:
                out = execute_run(
                    self.data_dir,
                    active["yaml"],
                    period,
                    Tier(tier),
                    run_id,
                    executed_at,
                    floors_path=self.floors_path,
                )
            except RunBlocked as exc:
                raise self._blocked(exc, active["sha256"]) from exc

            specs = self._spec_map()
            try:
                explanations = {f.fingerprint: jsonable(explain(f, specs[f.rule_id])) for f in out.findings}
            except Exception as exc:  # an explanation that cannot be built must not be silently dropped
                raise ApiError(500, "explanation_failed", f"An explanation could not be built: {exc}") from exc

            new_findings = self._persist_run(out, executed_at, active["version"], explanations)
        return {
            "run_id": out.run_id,
            "period": out.period,
            "tier": out.tier.value,
            "executed_at": executed_at,
            "findings": len(out.findings),
            "new_findings": new_findings,
            "total_exposure_usd": str(out.total_exposure_usd),
            "label": out.rollup.label,
        }

    def _persist_run(self, out, executed_at: str, config_version: int, explanations: dict[str, Any]) -> int:
        now = executed_at
        spec_by_id = self._spec_map()
        rule_order = sorted(
            out.rule_results,
            key=lambda r: (0 if r.rule_id not in spec_by_id or spec_by_id[r.rule_id].counts_toward_coverage else 1, r.rule_id),
        )
        with self.db.write() as conn:
            existing = {
                r["fingerprint"]: r
                for r in conn.execute("SELECT finding_id, fingerprint, status FROM findings WHERE period=?", (out.period,))
            }
            top = conn.execute("SELECT finding_id FROM findings ORDER BY seq DESC LIMIT 1").fetchone()
            next_num = int(top["finding_id"].split("-")[1]) + 1 if top else FIRST_FINDING_NUMBER
            # The run row first: findings reference it.
            conn.execute(
                "INSERT INTO runs (run_id, period, tier, executed_at, manifest_json, canonical, label,"
                " total_exposure_usd, config_version, findings_count, new_findings)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,0)",
                (
                    out.run_id,
                    out.period,
                    out.tier.value,
                    executed_at,
                    json.dumps(out.manifest, sort_keys=True),
                    out.canonical(),
                    out.rollup.label,
                    str(out.total_exposure_usd),
                    config_version,
                    len(out.findings),
                ),
            )
            for i, r in enumerate(rule_order):
                metrics = [
                    {
                        "id": m.metric_id,
                        "value": None if m.value is None else str(m.value),
                        "result": m.result.value,
                    }
                    for m in r.metrics
                ]
                conn.execute(
                    "INSERT INTO run_rules (run_id, ord, rule_id, result, findings, reason, metrics_json)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (out.run_id, i, r.rule_id, r.result.value, len(r.findings), r.not_evaluated_reason, json.dumps(metrics)),
                )

            new_count = 0
            # Identity and compliance memory (contracts/api.md): a finding is its (period, fingerprint).
            #  * known fingerprint  -> keep its id and status, advance latest_run_id, refresh the record;
            #                          a `closed` one reopens (closed -> open, actor "system").
            #  * new fingerprint    -> next id, status open.
            #  * a stored finding whose fingerprint is ABSENT from this run is left completely
            #    unchanged, even when this run evaluated its rule: the platform never deletes or
            #    auto-closes a finding, because closing one is a person's disposition and the
            #    history must show who made it.
            for i, f in enumerate(out.findings):
                # Stored in the engine's own key order (not sorted): `computed` lists such as L-11's `history`
                # are rendered item by item, and "period, rate, m10" reads better than "m10, period, rate".
                fj = jsonable(f)
                evidence = fj.pop("evidence")
                prior = existing.get(f.fingerprint)
                if prior is None:
                    fid = f"F-{next_num}"
                    next_num += 1
                    conn.execute(
                        "INSERT INTO findings (finding_id, fingerprint, period, rule_id, first_run_id, latest_run_id,"
                        " status, finding_json, evidence_json, evidence_count, explanation_json, created_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            fid,
                            f.fingerprint,
                            f.period,
                            f.rule_id,
                            out.run_id,
                            out.run_id,
                            "open",
                            json.dumps(fj),
                            json.dumps(evidence),
                            len(evidence),
                            json.dumps(explanations[f.fingerprint]),
                            now,
                        ),
                    )
                    conn.execute(
                        "INSERT INTO finding_history (finding_id, from_status, to_status, actor, at, reason_code, note)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (fid, None, "open", "system", now, None, f"Opened by {out.run_id}"),
                    )
                    existing[f.fingerprint] = {"finding_id": fid, "fingerprint": f.fingerprint, "status": "open"}
                    new_count += 1
                else:
                    fid = prior["finding_id"]
                    status = prior["status"]
                    if status == "closed":
                        status = "open"
                        conn.execute(
                            "INSERT INTO finding_history (finding_id, from_status, to_status, actor, at, reason_code, note)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (fid, "closed", "open", "system", now, None, f"Reopened: the same fingerprint recurred in {out.run_id}"),
                        )
                    conn.execute(
                        "UPDATE findings SET latest_run_id=?, status=?, finding_json=?, evidence_json=?,"
                        " evidence_count=?, explanation_json=? WHERE finding_id=?",
                        (
                            out.run_id,
                            status,
                            json.dumps(fj),
                            json.dumps(evidence),
                            len(evidence),
                            json.dumps(explanations[f.fingerprint]),
                            fid,
                        ),
                    )
                    existing[f.fingerprint] = {**dict(prior), "status": status}
                conn.execute(
                    "INSERT OR IGNORE INTO run_findings (run_id, ord, finding_id) VALUES (?,?,?)", (out.run_id, i, fid)
                )
            conn.execute("UPDATE runs SET new_findings=? WHERE run_id=?", (new_count, out.run_id))
        return new_count

    def list_runs(self) -> dict[str, Any]:
        with self.db.read() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY seq DESC").fetchall()
        return {
            "items": [
                {
                    "run_id": r["run_id"],
                    "period": r["period"],
                    "tier": r["tier"],
                    "executed_at": r["executed_at"],
                    "findings": r["findings_count"],
                    "total_exposure_usd": r["total_exposure_usd"],
                    "label": r["label"],
                    "config_version": r["config_version"],
                }
                for r in rows
            ]
        }

    def _run_row(self, conn, run_id: str):
        row = conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise ApiError(404, "run_not_found", f"No run with id {run_id}")
        return row

    def get_run(self, run_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = self._run_row(conn, run_id)
            rules = conn.execute("SELECT * FROM run_rules WHERE run_id=? ORDER BY ord", (run_id,)).fetchall()
            fids = conn.execute("SELECT finding_id FROM run_findings WHERE run_id=? ORDER BY ord", (run_id,)).fetchall()
        rule_items = []
        for r in rules:
            item: dict[str, Any] = {
                "rule_id": r["rule_id"],
                "domain": self._domain_of(r["rule_id"]),
                "result": r["result"],
                "findings": r["findings"],
            }
            if r["reason"]:
                item["reason"] = r["reason"]
            item["metrics"] = json.loads(r["metrics_json"])
            rule_items.append(item)
        manifest = json.loads(row["manifest_json"])
        # The manifest is served as the engine produced it. The one addition is `name` on each reference
        # table (a copy of `source`): the sample payload, and so the frontend, identifies a reference table
        # by `name`, while the engine's entry carries `source`. Nothing is removed or changed, and the STORED
        # manifest (which replay hashes) is untouched.
        for t in manifest.get("reference_tables", []):
            t.setdefault("name", t.get("source"))
        return {
            "run_id": row["run_id"],
            "period": row["period"],
            "tier": row["tier"],
            "executed_at": row["executed_at"],
            "manifest": manifest,
            "rules": rule_items,
            "total_exposure_usd": row["total_exposure_usd"],
            "label": row["label"],
            "findings": [f["finding_id"] for f in fids],
        }

    def replay_run(self, run_id: str) -> dict[str, Any]:
        with self.db.read() as conn:
            row = self._run_row(conn, run_id)
            cfg_row = conn.execute("SELECT yaml FROM config_versions WHERE version=?", (row["config_version"],)).fetchone()
        manifest = json.loads(row["manifest_json"])
        base = {"run_id": run_id}
        try:
            res = engine_replay(manifest, self.data_dir, cfg_row["yaml"], expected_canonical=row["canonical"])
        except RunBlocked as exc:
            # A pinned input changed, or the data can no longer be read: the replay is refused with the
            # engine's own reason. Reported as a non-identical result, never as a crash.
            prefix = "Replay refused, because the pinned inputs changed: "
            diff = exc.message[len(prefix):].split("; ") if exc.message.startswith(prefix) else []
            return {**base, "identical": False, "findings_compared": 0, "diff": diff, "reason": exc.message}
        return {
            **base,
            "identical": res.identical,
            "findings_compared": res.findings_compared,
            "diff": list(res.diff),
            "reason": res.reason,
        }

    # ------------------------------------------------------------------ findings
    def _history(self, conn, fid: str) -> list[dict[str, Any]]:
        rows = conn.execute("SELECT * FROM finding_history WHERE finding_id=? ORDER BY id", (fid,)).fetchall()
        return [
            {
                "from": r["from_status"],
                "to": r["to_status"],
                "actor": r["actor"],
                "at": r["at"],
                "reason_code": r["reason_code"],
                "note": r["note"],
            }
            for r in rows
        ]

    def _detail(self, conn, row) -> dict[str, Any]:
        fj = json.loads(row["finding_json"])
        computed = fj["computed"]
        entries = computed.get("entry_count")
        if not isinstance(entries, int) or isinstance(entries, bool):
            evidence = json.loads(row["evidence_json"])
            entries = sum(1 for e in evidence if e.get("kind") == "time_entry") or len(fj["exposure_entry_ids"])
        return {
            "finding_id": row["finding_id"],
            "fingerprint": row["fingerprint"],
            "run_id": row["first_run_id"],
            "latest_run_id": row["latest_run_id"],
            "rule_id": fj["rule_id"],
            "rule_version": fj["rule_version"],
            "period": fj["period"],
            "headline": fj["headline"],
            "authorities": fj["authorities"],
            "basis": fj["basis"],
            "severity": fj["severity"],
            "severity_reason": fj["severity_reason"],
            "status": row["status"],
            "metric": {
                "id": fj["metric_id"],
                "value": fj["metric_value"],
                "numerator": fj["metric_numerator"],
                "denominator": fj["metric_denominator"],
                "threshold_tripped": fj["threshold_tripped"],
                "baseline_confidence": str(computed.get("baseline_confidence") or "n/a"),
            },
            "computed": _api_computed(computed),
            "exposure_usd": fj["exposure_usd"],
            "exposure_basis": str(computed.get("exposure_basis", "")),
            "affected": {"employees": fj["employees"], "contracts": fj["contracts"], "entries": entries},
            "explanation": json.loads(row["explanation_json"]),
            "evidence_count": row["evidence_count"],
            "created_at": row["created_at"],
            "history": self._history(conn, row["finding_id"]),
            "allowed_transitions": list(TRANSITIONS.get(row["status"], ())),
            **self._domain_fields(fj["rule_id"]),
        }

    def _finding_row(self, conn, fid: str):
        row = conn.execute("SELECT * FROM findings WHERE finding_id=?", (fid,)).fetchone()
        if row is None:
            raise ApiError(404, "finding_not_found", f"No finding with id {fid}")
        return row

    def list_findings(
        self,
        severity: str | None = None,
        rule: str | None = None,
        status: str | None = None,
        period: str | None = None,
        domain: str | None = None,
    ) -> dict[str, Any]:
        if severity and severity not in SEVERITIES:
            raise ApiError(400, "bad_filter", "Unknown severity; use high, medium or low")
        if status and status not in STATES:
            raise ApiError(400, "bad_filter", "Unknown status")
        self._check_period(period)
        self._check_domain(domain)
        with self.db.read() as conn:
            rows = conn.execute(f"SELECT {_LIGHT_COLS} FROM findings").fetchall()
        specs = self._spec_map()
        items = []
        for row in rows:
            if rule and row["rule_id"] != rule:
                continue
            if domain and self._domain_of(row["rule_id"]) != domain:
                continue
            if status and row["status"] != status:
                continue
            if period and row["period"] != period:
                continue
            fj = json.loads(row["finding_json"])
            if severity and fj["severity"] != severity:
                continue
            items.append(
                (
                    (
                        _SEVERITY_RANK[fj["severity"]],
                        0 if fj["rule_id"] not in specs or specs[fj["rule_id"]].counts_toward_coverage else 1,
                        fj["rule_id"],
                        -Decimal(fj["exposure_usd"]),
                        row["fingerprint"],
                    ),
                    {
                        "finding_id": row["finding_id"],
                        "rule_id": fj["rule_id"],
                        "headline": fj["headline"],
                        "severity": fj["severity"],
                        "status": row["status"],
                        "exposure_usd": fj["exposure_usd"],
                        "employee_count": len(fj["employees"]),
                        "employees": fj["employees"],
                        "contracts": fj["contracts"],
                        "period": fj["period"],
                        **self._domain_fields(fj["rule_id"]),
                    },
                )
            )
        items.sort(key=lambda t: t[0])
        return {"total": len(items), "items": [t[1] for t in items]}

    def get_finding(self, fid: str) -> dict[str, Any]:
        with self.db.read() as conn:
            return self._detail(conn, self._finding_row(conn, fid))

    def get_evidence(self, fid: str, page: int, page_size: int) -> dict[str, Any]:
        with self.db.read() as conn:
            row = self._finding_row(conn, fid)
        evidence = json.loads(row["evidence_json"])
        start = (page - 1) * page_size
        return {
            "finding_id": fid,
            "total": len(evidence),
            "page": page,
            "page_size": page_size,
            "items": [
                {
                    "kind": e["kind"],
                    "ref_id": e["ref_id"],
                    "source_file": e["source_file"],
                    "sha256": e["sha256"],
                    "row": e["row"],
                    "detail": e["detail"],
                }
                for e in evidence[start : start + page_size]
            ],
        }

    def disposition(
        self, fid: str, disposition: str, reason_code: str | None, note: str | None, actor: str
    ) -> dict[str, Any]:
        reason_code = (reason_code or "").strip() or None
        note = (note or "").strip() or None
        with self._lock, self.db.write() as conn:
            row = self._finding_row(conn, fid)
            current = row["status"]
            if disposition not in STATES:
                raise ApiError(422, "invalid_disposition", f"Unknown disposition; use one of {', '.join(STATES)}")
            allowed = TRANSITIONS.get(current, ())
            if disposition not in allowed:
                where = ", ".join(allowed) if allowed else "none (a closed finding reopens only when the same fingerprint recurs in a later run)"
                raise ApiError(
                    409,
                    "invalid_transition",
                    f"A finding in status {current} cannot move to {disposition}. Allowed from {current}: {where}",
                )
            if reason_code is not None and reason_code not in REASON_CODES:
                raise ApiError(422, "invalid_reason_code", f"Unknown reason code; use one of {', '.join(REASON_CODES)}")
            if disposition == "legit_exception" and (reason_code is None or note is None):
                raise ApiError(422, "reason_required", "A legit_exception needs both a reason_code and a note")
            conn.execute("UPDATE findings SET status=? WHERE finding_id=?", (disposition, fid))
            conn.execute(
                "INSERT INTO finding_history (finding_id, from_status, to_status, actor, at, reason_code, note)"
                " VALUES (?,?,?,?,?,?,?)",
                (fid, current, disposition, actor, _ts(self.clock()), reason_code, note),
            )
            return self._detail(conn, self._finding_row(conn, fid))

    # ------------------------------------------------------------------ status
    def status(self, period: str | None = None, as_of: str | None = None) -> dict[str, Any]:
        self._check_period(period)
        if as_of:
            try:
                today = date.fromisoformat(as_of)
            except ValueError:
                raise ApiError(400, "bad_filter", "as_of must be a date like 2026-09-03") from None
        else:
            today = self._today()

        with self.db.read() as conn:
            period = period or self.default_period(conn)
            cfg = self._parse_config(self._active_config_row(conn)["yaml"])
            runs = conn.execute("SELECT * FROM runs WHERE period=? ORDER BY seq DESC", (period,)).fetchall()
            state = self._rule_state(conn, period)
            persisted = self._period_findings(conn, period)

        latest = runs[0] if runs else None
        manifests = [json.loads(r["manifest_json"]) for r in runs]  # newest first
        # Domains in play: what the newest run of the period actually ran, else what the active config
        # enables. A domain that is not enabled is not run, so its rules are not "Not evaluated"; they
        # are simply outside this status (and outside every count below).
        enabled = self._enabled_domains(cfg, manifests[0] if manifests else None)
        specs = sorted((s for s in self._specs() if s.domain in enabled), key=self._rule_order)
        by_rule: dict[str, list[Finding]] = {}
        status_of: dict[str, str] = {}
        for row, f in persisted:
            by_rule.setdefault(f.rule_id, []).append(f)
            status_of[f.fingerprint] = row["status"]

        # Layer 2 per rule: the most recent run that evaluated it. Fed to the engine's own rollup
        # together with the persisted findings; `is_open` reads PERSISTED statuses, so the labels,
        # severity counts, coverage and the M13 totals all come from the engine.
        rule_results = []
        for s in specs:
            st = state.get(s.id)
            rule_results.append(
                RuleResult(
                    rule_id=s.id,
                    result=Result(st["result"]) if st else Result.NOT_EVALUATED,
                    findings=tuple(by_rule.get(s.id, ())),
                    not_evaluated_reason=(st["reason"] if st else "No run has evaluated this rule yet"),
                )
            )
        # Coverage is measured against EVERY applicable `counts_toward_coverage` rule of the enabled
        # domains, never only the tiers that have run. Scoping it to the tiers reached would let a
        # fast-only period read "No open findings" at 100% coverage while the payroll and GL checks had
        # not run at all: exactly what design 7.2 forbids ("a fast-tier run that finds nothing must not
        # render the same as a full run that finds nothing"). The per-tier table (`tiers[]`) is where
        # tier-scoped progress is shown. With no run at all nothing is evaluated, so the label is
        # "Incomplete data". `rollup_domains` makes the overall label "Incomplete data" if ANY enabled
        # domain is below the floor, and de-duplicates exposure (M13) across domains.
        applicable = [s for s in specs if s.counts_toward_coverage]
        floor = resolve_global(cfg, self.registry)["coverage_floor"]
        roll, by_domain = rollup_domains(
            rule_results,
            applicable,
            enabled,
            coverage_floor=floor,
            is_open=lambda f: status_of.get(f.fingerprint) in OPEN_STATES,
        )

        return {
            "period": period,
            "as_of": today.isoformat(),
            "label": roll.label,
            "label_kind": roll.label_kind,
            "open_findings": roll.open_findings,
            "by_severity": {"high": roll.high, "medium": roll.medium, "low": roll.low},
            "total_exposure_usd": str(roll.total_exposure_usd),
            "coverage": self._coverage(roll),
            "last_run_id": latest["run_id"] if latest else None,
            "last_run_at": latest["executed_at"] if latest else None,
            "tiers": self._tiers(cfg, runs, specs, rule_results, today),
            "oldest_source": self._oldest_source(manifests, today),
            "domains": [self._domain_tile(d, enabled, by_domain) for d in DOMAIN_ORDER],
            "rules": [self._status_rule(r) for r in rule_results],
        }

    @staticmethod
    def _coverage(roll) -> dict[str, Any]:
        return {
            "evaluated": roll.coverage_evaluated,
            "applicable": roll.coverage_applicable,
            "ratio": str(roll.coverage_ratio),
            "floor": str(roll.coverage_floor),
        }

    def _domain_tile(self, domain_id: str, enabled: tuple[str, ...], by_domain: dict[str, Any]) -> dict[str, Any]:
        """One entry of `status.domains[]`: an enabled domain shows its own engine rollup; an available
        domain that is not enabled shows "Not enabled"; a placeholder shows "coming later"."""
        tile: dict[str, Any] = {
            "id": domain_id,
            "name": ALL_DOMAIN_NAMES[domain_id],
            "enabled": domain_id in enabled and domain_id in by_domain,
            "available": domain_id in DOMAIN_NAMES,
        }
        roll = by_domain.get(domain_id) if tile["enabled"] else None
        if roll is not None:
            tile.update(
                state=roll.label,
                label_kind=roll.label_kind,
                open_findings=roll.open_findings,
                by_severity={"high": roll.high, "medium": roll.medium, "low": roll.low},
                coverage=self._coverage(roll),
            )
            return tile
        tile.update(
            state="Not enabled" if tile["available"] else "coming later",
            label_kind="not_enabled" if tile["available"] else "unavailable",
            open_findings=0,
            by_severity={"high": 0, "medium": 0, "low": 0},
            coverage=None,
        )
        return tile

    def _status_rule(self, r: RuleResult) -> dict[str, Any]:
        item: dict[str, Any] = {"rule_id": r.rule_id, "result": r.result.value}
        if r.result in (Result.NOT_EVALUATED, Result.NOT_APPLICABLE) and r.not_evaluated_reason:
            item["reason"] = r.not_evaluated_reason
        item["domain"] = self._domain_of(r.rule_id)
        return item

    @staticmethod
    def _oldest_source(manifests: list[dict[str, Any]], today: date) -> dict[str, Any] | None:
        """Oldest `data_as_of` across the newest load of each source in the period.

        Using only the latest run is wrong: a fast run never loads payroll, HRIS or the GL, so after one
        the staleness line would silently drop the very sources that bound how current the findings are.
        `manifests` is newest first, so the first time a source appears is its newest load."""
        newest: dict[str, dict[str, Any]] = {}
        for m in manifests:
            for i in m.get("inputs", []):
                newest.setdefault(i["source"], i)
        # `contracts` is a confirmed reference document, not a periodic feed: its as-of date is when the
        # terms were confirmed, so it would always read as the stalest source. The sample status payload
        # (hris, not contracts) makes the same choice.
        dated = [i for i in newest.values() if i.get("data_as_of") and i.get("source") != "contracts"]
        if not dated:
            return None
        oldest = min(dated, key=lambda i: (i["data_as_of"], i["source"]))
        return {
            "source": oldest["source"],
            "file": oldest["file"],
            "data_as_of": oldest["data_as_of"],
            "age_days": (today - date.fromisoformat(oldest["data_as_of"])).days,
        }

    def _tiers(self, cfg, runs, specs, rule_results, today: date) -> list[dict[str, Any]]:
        """`specs` are the rules of the ENABLED domains; a tier counts those that count toward coverage.
        Per tier, api.md "Clarifications" 1: next_due = date(last run) + cadence days; overdue iff
        as_of > next_due; never_run when no run covers the tier. A run of a higher tier covers the
        tiers below it (a close run also evaluates the fast and pay-period rules)."""
        schedule = dict(cfg.schedule) if cfg else {}
        result_of = {r.rule_id: r for r in rule_results}
        out = []
        for tier in TIERS:
            in_tier = [s for s in specs if s.counts_toward_coverage and s.tier.value == tier]
            evaluated = [
                s for s in in_tier if result_of[s.id].result not in (Result.NOT_EVALUATED, Result.NOT_APPLICABLE)
            ]
            not_evaluated = [s for s in in_tier if result_of[s.id].result == Result.NOT_EVALUATED]
            cover = next((r for r in runs if TIER_ORDER[Tier(r["tier"])] >= TIER_ORDER[Tier(tier)]), None)
            cadence = schedule.get(tier)
            notes: list[str] = []
            next_due: str | None = None
            if cover is None:
                state = "never_run"
                notes.append(f"No run has covered the {TIER_LABELS[tier].lower()} tier for this period yet")
            else:
                state = "current"
                last_day = date.fromisoformat(cover["executed_at"][:10])
                days = CADENCE_DAYS.get(cadence or "")
                if days is None:
                    notes.append(
                        f"Declared cadence {cadence!r} is not one the schedule check knows"
                        if cadence
                        else "No cadence is declared for this tier in the rules config"
                    )
                else:
                    due = last_day + timedelta(days=days)
                    next_due = due.isoformat()
                    if today > due:
                        state = "overdue"
                        notes.append(
                            f"Declared {cadence}; last run {last_day.isoformat()}, so the next run was due {next_due}"
                        )
            for s in not_evaluated:
                reason = result_of[s.id].not_evaluated_reason
                notes.append(f"{s.id} not evaluated: {_lower_first(reason)}" if reason else f"{s.id} not evaluated")
            out.append(
                {
                    "tier": tier,
                    "label": TIER_LABELS[tier],
                    "cadence": cadence or "not declared",
                    "last_run_id": cover["run_id"] if cover else None,
                    "last_run_at": cover["executed_at"] if cover else None,
                    "rules_in_tier": len(in_tier),
                    "rules_evaluated": len(evaluated),
                    "state": state,
                    "next_due": next_due,
                    "note": ". ".join(notes) if notes else None,
                }
            )
        return out
