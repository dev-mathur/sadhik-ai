"""Customer rules-config validation and parameter resolution (design 5.2, 5.3).

The customer file is UNTRUSTED input. `validate_config` is a gate, not an editor:
  * strictness is compared by each parameter's `direction`, never by magnitude;
  * a refused value is reported with its line number and is NEVER clamped;
  * the file has no syntax for floors (any `floor` key is refused);
  * a zero-tolerance (regulatory / contract) rule cannot be switched off;
  * `status: not_applicable` (with a reason) is accepted and is not `enabled: false`.

Resolution has three layers: floor (validation only) -> registry default -> customer
value; a contract scope overrides for that contract and the STRICTEST applicable
value wins. No clock, no randomness, no I/O here.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import yaml

from engine.floors import GLOBAL, FloorParam, FloorRegistry, fmt, is_looser, strictest, to_decimal
from engine.results import RunBlocked
from engine.rules.base import DEFAULT_ENABLED_DOMAINS, DOMAIN_NAMES, ParamSpec, RuleSpec

TIER_KEYS = ("fast", "pay_period", "close")
TOP_LEVEL_KEYS = frozenset(
    {
        "config_version", "customer", "effective_from", "changed_by", "approved_by", "reason", "schedule",
        "domains", "defaults", "rules",
    }
)
RULE_META_KEYS = frozenset({"enabled", "status", "reason", "scope"})
SCOPE_PREFIX = "scope"  # reserved ResolvedParams key prefix: "scope:<contract_id>:<param>"


@dataclass(frozen=True)
class ConfigError:
    line: int | None
    path: str
    code: str
    message: str


@dataclass(frozen=True)
class CustomerConfig:
    config_version: int
    customer: str
    effective_from: date
    changed_by: str
    approved_by: str | None
    reason: str
    schedule: dict[str, str]
    defaults: dict[str, Decimal]
    # rule_id -> {param: Decimal | "enabled": bool | "status": str | "reason": str |
    #             "scope": {"contract": {contract_id: {param: Decimal}}}}
    rules: dict[str, dict[str, Any]]
    # Compliance domains this customer has switched on, in the order given. A rule whose domain is not
    # listed is not run at all (it is not "Not evaluated"). Absent from the file means labor only.
    domains: tuple[str, ...] = DEFAULT_ENABLED_DOMAINS


@dataclass(frozen=True)
class ConfigResult:
    accepted: bool
    errors: tuple[ConfigError, ...]
    warnings: tuple[str, ...]
    config: CustomerConfig | None
    sha256: str  # sha256 of the exact submitted text


# --------------------------------------------------------------------------- #
# Line-tracking YAML load
# --------------------------------------------------------------------------- #
class _Loaded:
    def __init__(self) -> None:
        self.data: Any = None
        self.lines: dict[tuple, int] = {}
        self.errors: list[ConfigError] = []


def _dotted(path: tuple) -> str:
    return ".".join(str(p) for p in path)


def _build(loader: yaml.SafeLoader, node: yaml.Node, path: tuple, out: _Loaded, depth: int = 0) -> Any:
    if depth > 40:
        out.errors.append(ConfigError(node.start_mark.line + 1, _dotted(path), "bad_value", "nesting is too deep"))
        return None
    if isinstance(node, yaml.MappingNode):
        loader.flatten_mapping(node)
        result: dict[str, Any] = {}
        for knode, vnode in node.value:
            if not isinstance(knode, yaml.ScalarNode):
                out.errors.append(
                    ConfigError(knode.start_mark.line + 1, _dotted(path), "bad_value", "mapping keys must be plain names")
                )
                continue
            key = str(loader.construct_object(knode, deep=True))
            kpath = path + (key,)
            line = knode.start_mark.line + 1
            if key in result:
                out.errors.append(
                    ConfigError(line, _dotted(kpath), "bad_value", f"duplicate key '{key}' (a repeated key silently overrides the first)")
                )
            out.lines[kpath] = line
            result[key] = _build(loader, vnode, kpath, out, depth + 1)
        return result
    if isinstance(node, yaml.SequenceNode):
        items = []
        for i, item in enumerate(node.value):
            out.lines[path + (i,)] = item.start_mark.line + 1
            items.append(_build(loader, item, path + (i,), out, depth + 1))
        return items
    return loader.construct_object(node, deep=True)


def _load_yaml(text: str) -> _Loaded:
    out = _Loaded()
    loader = yaml.SafeLoader(text)
    try:
        node = loader.get_single_node()
        if node is None:
            out.errors.append(ConfigError(None, "", "bad_value", "the config file is empty"))
            return out
        out.data = _build(loader, node, (), out)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        out.errors.append(ConfigError(line, "", "yaml_syntax", f"YAML could not be read: {problem}"))
        out.data = None
    finally:
        loader.dispose()
    return out


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _floor_param_for(registry: FloorRegistry, spec: RuleSpec | None, rule_id: str, name: str) -> FloorParam | None:
    """Registry entry if present, else derived from the rule's own ParamSpec
    (no floor), else None (unknown parameter)."""
    if registry.has_param(rule_id, name):
        return registry.param(rule_id, name)
    if spec is not None and name in spec.parameters:
        ps: ParamSpec = spec.parameters[name]
        return FloorParam(rule_id, name, ps.direction, None, ps.default, ps.min, ps.max, spec.basis, "")
    return None


def _param_names(registry: FloorRegistry, spec: RuleSpec | None, rule_id: str) -> list[str]:
    names = set(registry.rules.get(rule_id, {}))
    if spec is not None:
        names |= set(spec.parameters)
    return sorted(names)


def _number(raw: Any) -> Decimal | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
        return None
    try:
        d = to_decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


class _Ctx:
    def __init__(self, loaded: _Loaded) -> None:
        self.lines = loaded.lines
        self.errors: list[ConfigError] = list(loaded.errors)
        self.warnings: list[str] = []

    def err(self, path: tuple, code: str, message: str, line_path: tuple | None = None) -> None:
        line = self.lines.get(line_path if line_path is not None else path)
        self.errors.append(ConfigError(line, _dotted(path), code, message))

    def value(self, path: tuple, raw: Any, fp: FloorParam) -> Decimal | None:
        """Validate one numeric parameter value. Returns the Decimal if accepted,
        None if refused. The value is never adjusted."""
        d = _number(raw)
        if d is None:
            self.err(path, "bad_value", f"{path[-1]} must be a number (got {raw!r})")
            return None
        v = fp.violation(d)
        if v is not None:
            self.err(path, v[0], v[1])
            return None
        return d


def _scan_floor_keys(node: Any, path: tuple, ctx: _Ctx) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            p = path + (k,)
            if str(k).strip().lower() == "floor":
                ctx.err(p, "floor_not_settable", "floors are owned by Sadhik and cannot be set in a customer config")
            _scan_floor_keys(v, p, ctx)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _scan_floor_keys(v, path + (i,), ctx)


def _req_str(doc: dict, key: str, ctx: _Ctx) -> str | None:
    if key not in doc or doc[key] is None or (isinstance(doc[key], str) and not doc[key].strip()):
        ctx.errors.append(ConfigError(None, key, "missing_field", f"'{key}' is required"))
        return None
    if not isinstance(doc[key], str):
        ctx.err((key,), "bad_value", f"'{key}' must be text")
        return None
    return doc[key].strip()


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_config(
    text: str,
    registry: FloorRegistry,
    rules: list[RuleSpec],
    previous: CustomerConfig | None = None,
) -> ConfigResult:
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    loaded = _load_yaml(text)
    ctx = _Ctx(loaded)
    doc = loaded.data
    if doc is None or not isinstance(doc, dict):
        if doc is not None:
            ctx.errors.append(ConfigError(1, "", "bad_value", "the config file must be a mapping at the top level"))
        return _result(ctx, None, sha)

    specs = {r.id: r for r in rules}
    _scan_floor_keys(doc, (), ctx)

    for key in doc:
        if key not in TOP_LEVEL_KEYS:
            ctx.err((key,), "unknown_parameter", f"unknown top-level key '{key}'")

    # ---- required scalar fields
    cv = doc.get("config_version")
    if "config_version" not in doc or cv is None:
        ctx.errors.append(ConfigError(None, "config_version", "missing_field", "'config_version' is required"))
        cv = None
    elif isinstance(cv, bool) or not isinstance(cv, int) or cv < 1:
        ctx.err(("config_version",), "bad_value", "'config_version' must be a positive whole number")
        cv = None
    customer = _req_str(doc, "customer", ctx)
    changed_by = _req_str(doc, "changed_by", ctx)
    reason = _req_str(doc, "reason", ctx)

    eff = doc.get("effective_from")
    eff_date: date | None = None
    if "effective_from" not in doc or eff is None:
        ctx.errors.append(ConfigError(None, "effective_from", "missing_field", "'effective_from' is required"))
    elif isinstance(eff, datetime):
        eff_date = eff.date()
    elif isinstance(eff, date):
        eff_date = eff
    elif isinstance(eff, str):
        try:
            eff_date = date.fromisoformat(eff)
        except ValueError:
            ctx.err(("effective_from",), "bad_value", "'effective_from' must be an ISO date (YYYY-MM-DD)")
    else:
        ctx.err(("effective_from",), "bad_value", "'effective_from' must be an ISO date (YYYY-MM-DD)")

    approved = doc.get("approved_by")
    if approved is not None and not isinstance(approved, str):
        ctx.err(("approved_by",), "bad_value", "'approved_by' must be text")
        approved = None
    approved = approved.strip() if isinstance(approved, str) and approved.strip() else None

    # ---- schedule
    schedule: dict[str, str] = {}
    sched_raw = doc.get("schedule")
    if sched_raw is not None:
        if not isinstance(sched_raw, dict):
            ctx.err(("schedule",), "bad_value", "'schedule' must be a mapping of tier to cadence")
        else:
            for k, v in sched_raw.items():
                if k not in TIER_KEYS:
                    ctx.err(("schedule", k), "bad_value", f"unknown schedule tier '{k}' (expected one of {', '.join(TIER_KEYS)})")
                elif not isinstance(v, str) or not v.strip():
                    ctx.err(("schedule", k), "bad_value", f"schedule.{k} must be a cadence name such as 'weekly'")
                else:
                    schedule[k] = v.strip()

    # ---- domains
    domains = _validate_domains(ctx, doc)

    # ---- defaults (global parameters)
    defaults: dict[str, Decimal] = {}
    defaults_raw = doc.get("defaults")
    if defaults_raw is not None:
        if not isinstance(defaults_raw, dict):
            ctx.err(("defaults",), "bad_value", "'defaults' must be a mapping")
        else:
            for name, raw in defaults_raw.items():
                p = ("defaults", name)
                if name == "floor":
                    continue  # already reported by the floor scan
                if not registry.has_param(GLOBAL, name):
                    ctx.err(p, "unknown_parameter", f"unknown default '{name}'")
                    continue
                d = ctx.value(p, raw, registry.global_param(name))
                if d is not None:
                    defaults[name] = d

    # ---- per-rule overrides
    rule_cfg: dict[str, dict[str, Any]] = {}
    rules_raw = doc.get("rules")
    if rules_raw is not None and not isinstance(rules_raw, dict):
        ctx.err(("rules",), "bad_value", "'rules' must be a mapping of rule id to overrides")
        rules_raw = None
    for rule_id, body in (rules_raw or {}).items():
        rp = ("rules", rule_id)
        spec = specs.get(rule_id)
        if spec is None and rule_id not in registry.rules:
            ctx.err(rp, "unknown_rule", f"unknown rule '{rule_id}'")
            continue
        if body is None:
            body = {}
        if not isinstance(body, dict):
            ctx.err(rp, "bad_value", f"rules.{rule_id} must be a mapping")
            continue
        entry = _validate_rule(ctx, registry, spec, rule_id, body)
        rule_cfg[rule_id] = entry

    if ctx.errors:
        return _result(ctx, None, sha)

    assert cv is not None and customer and changed_by and reason and eff_date is not None
    cfg = CustomerConfig(
        config_version=cv,
        customer=customer,
        effective_from=eff_date,
        changed_by=changed_by,
        approved_by=approved,
        reason=reason,
        schedule=schedule,
        defaults=defaults,
        rules=rule_cfg,
        domains=domains,
    )
    _check_loosening(ctx, cfg, previous, registry, specs, doc)
    _add_warnings(ctx, cfg, registry, specs)
    return _result(ctx, cfg, sha)


def _validate_domains(ctx: _Ctx, doc: dict) -> tuple[str, ...]:
    """`domains: [labor, dcaa_cost_accounting]`. Absent means labor only. An unknown id, an empty list, a
    non-list or a repeated id is refused (never guessed at): a typo must not silently switch a domain off."""
    if "domains" not in doc:
        return DEFAULT_ENABLED_DOMAINS
    known = ", ".join(DOMAIN_NAMES)
    raw = doc["domains"]
    dp = ("domains",)
    if not isinstance(raw, list):
        ctx.err(dp, "bad_value", f"'domains' must be a list of domain ids (one or more of: {known})")
        return DEFAULT_ENABLED_DOMAINS
    if not raw:
        ctx.err(dp, "bad_value", f"'domains' cannot be empty: enable at least one domain (one or more of: {known})")
        return DEFAULT_ENABLED_DOMAINS
    seen: list[str] = []
    for i, item in enumerate(raw):
        ip = dp + (i,)
        if not isinstance(item, str) or not item.strip():
            ctx.err(ip, "bad_value", f"each entry in 'domains' must be a domain id (one of: {known})")
        elif item not in DOMAIN_NAMES:
            ctx.err(ip, "unknown_domain", f"unknown domain '{item}' (expected one of: {known})")
        elif item in seen:
            ctx.err(ip, "bad_value", f"domain '{item}' is listed more than once")
        else:
            seen.append(item)
    for d in DOMAIN_NAMES:
        if d not in seen:
            ctx.warnings.append(
                f"{DOMAIN_NAMES[d]} is not enabled: its rules are not run and it does not appear in the status"
            )
    return tuple(seen)


def _validate_rule(ctx: _Ctx, registry: FloorRegistry, spec: RuleSpec | None, rule_id: str, body: dict) -> dict[str, Any]:
    entry: dict[str, Any] = {}
    rp = ("rules", rule_id)
    known = set(_param_names(registry, spec, rule_id))

    # enabled
    if "enabled" in body:
        en = body["enabled"]
        if not isinstance(en, bool):
            ctx.err(rp + ("enabled",), "bad_value", "'enabled' must be true or false")
        elif en is False:
            if registry.is_regulatory(rule_id):
                ctx.err(
                    rp + ("enabled",),
                    "cannot_disable_regulatory_rule",
                    f"{rule_id} is regulatory or contract-based and cannot be disabled; "
                    "use status: not_applicable with a reason",
                )
            else:
                entry["enabled"] = False
                ctx.warnings.append(
                    f"{rule_id} is disabled: it will be reported as Not evaluated and stays in the coverage count"
                )
        else:
            entry["enabled"] = True

    # status / reason
    status = body.get("status")
    if "status" in body:
        if status not in ("not_applicable", "active"):
            ctx.err(rp + ("status",), "bad_value", "'status' must be 'not_applicable' (or omitted)")
        elif status == "not_applicable":
            why = body.get("reason")
            if not isinstance(why, str) or not why.strip():
                ctx.err(
                    rp + ("reason",),
                    "missing_field",
                    f"{rule_id} is marked not_applicable but gives no reason",
                    line_path=rp + ("status",),
                )
            else:
                entry["status"] = "not_applicable"
                entry["reason"] = why.strip()
                ctx.warnings.append(f"{rule_id} is not applicable ({why.strip()}) and is excluded from coverage")
    elif "reason" in body and not isinstance(body["reason"], str):
        ctx.err(rp + ("reason",), "bad_value", "'reason' must be text")

    # scope
    if "scope" in body:
        scope = body["scope"]
        sp = rp + ("scope",)
        if not isinstance(scope, dict):
            ctx.err(sp, "bad_value", "'scope' must be a mapping")
        else:
            for sk, sv in scope.items():
                if sk != "contract":
                    if str(sk).lower() != "floor":
                        ctx.err(sp + (sk,), "unknown_parameter", f"unknown scope '{sk}' (only 'contract' is supported)")
                    continue
                cp = sp + ("contract",)
                if not isinstance(sv, dict):
                    ctx.err(cp, "bad_value", "'scope.contract' must map contract ids to overrides")
                    continue
                out_contracts: dict[str, dict[str, Decimal]] = {}
                for cid, params in sv.items():
                    ccp = cp + (cid,)
                    if not isinstance(params, dict):
                        ctx.err(ccp, "bad_value", f"scope for contract {cid} must be a mapping of parameters")
                        continue
                    vals: dict[str, Decimal] = {}
                    for name, raw in params.items():
                        if str(name).lower() == "floor":
                            continue
                        pp = ccp + (name,)
                        fp = _floor_param_for(registry, spec, rule_id, name) if name in known else None
                        if fp is None:
                            ctx.err(pp, "unknown_parameter", f"'{name}' is not a parameter of {rule_id}")
                            continue
                        d = ctx.value(pp, raw, fp)
                        if d is not None:
                            vals[name] = d
                    if vals:
                        out_contracts[str(cid)] = vals
                if out_contracts:
                    entry["scope"] = {"contract": out_contracts}
                    # Validated against the floors, but no rule reads scoped values yet (rules take the flat
                    # ResolvedParams only). Say so, rather than let a customer believe a per-contract
                    # threshold is in force when the run will ignore it.
                    ctx.warnings.append(
                        f"{rule_id}: contract-scoped values ({', '.join(sorted(out_contracts))}) are valid but "
                        "are not applied by this build; the rule-wide value is used for every contract"
                    )

    # parameters
    for name, raw in body.items():
        if name in RULE_META_KEYS or str(name).lower() == "floor":
            continue
        pp = rp + (name,)
        fp = _floor_param_for(registry, spec, rule_id, name) if name in known else None
        if fp is None:
            ctx.err(pp, "unknown_parameter", f"'{name}' is not a parameter of {rule_id}")
            continue
        d = ctx.value(pp, raw, fp)
        if d is not None:
            entry[name] = d
    return entry


def _result(ctx: _Ctx, cfg: CustomerConfig | None, sha: str) -> ConfigResult:
    errors = tuple(sorted(ctx.errors, key=lambda e: (e.line if e.line is not None else 0, e.path, e.code)))
    return ConfigResult(
        accepted=not errors,
        errors=errors,
        warnings=tuple(ctx.warnings),
        config=None if errors else cfg,
        sha256=sha,
    )


# --------------------------------------------------------------------------- #
# Approval (loosening vs previous) and warnings
# --------------------------------------------------------------------------- #
def _contract_ids(*configs: CustomerConfig | None) -> list[str]:
    ids: set[str] = set()
    for c in configs:
        if c is None:
            continue
        for body in c.rules.values():
            ids |= set(body.get("scope", {}).get("contract", {}))
    return sorted(ids)


def _effective_values(
    cfg: CustomerConfig | None, registry: FloorRegistry, specs: dict[str, RuleSpec], contract_ids: list[str]
) -> dict[tuple[str, str, str | None], tuple[Decimal, FloorParam]]:
    out: dict[tuple[str, str, str | None], tuple[Decimal, FloorParam]] = {}
    for name, fp in registry.globals.items():
        v = cfg.defaults.get(name, fp.default) if cfg else fp.default
        out[(GLOBAL, name, None)] = (v, fp)
    rule_ids = set(specs) | set(registry.rules)
    for rid in sorted(rule_ids):
        body = cfg.rules.get(rid, {}) if cfg else {}
        for name in _param_names(registry, specs.get(rid), rid):
            fp = _floor_param_for(registry, specs.get(rid), rid, name)
            if fp is None:
                continue
            base = body.get(name, fp.default)
            out[(rid, name, None)] = (base, fp)
            scoped = body.get("scope", {}).get("contract", {})
            for cid in contract_ids:
                if name in scoped.get(cid, {}):
                    out[(rid, name, cid)] = (strictest(fp.direction, base, scoped[cid][name]), fp)
                else:
                    out[(rid, name, cid)] = (base, fp)
    return out


def _check_loosening(
    ctx: _Ctx,
    cfg: CustomerConfig,
    previous: CustomerConfig | None,
    registry: FloorRegistry,
    specs: dict[str, RuleSpec],
    doc: dict,
) -> None:
    if previous is None:
        return  # nothing to loosen relative to; defaults-relative changes are warned below
    cids = _contract_ids(cfg, previous)
    new = _effective_values(cfg, registry, specs, cids)
    old = _effective_values(previous, registry, specs, cids)
    loosened: list[str] = []
    for key, (nv, fp) in new.items():
        if key in old and is_looser(fp.direction, nv, old[key][0]):
            rid, name, cid = key
            label = f"{rid}.{name}" if rid != GLOBAL else f"defaults.{name}"
            if cid:
                label += f" (contract {cid})"
            loosened.append(f"{label} {fmt(old[key][0])} -> {fmt(nv)}")
    if loosened and not cfg.approved_by:
        line = ctx.lines.get(("approved_by",)) or ctx.lines.get(("changed_by",))
        ctx.errors.append(
            ConfigError(
                line,
                "approved_by",
                "missing_approval",
                "approved_by is required because this change loosens: " + "; ".join(loosened),
            )
        )
    elif loosened:
        ctx.warnings.append("Loosened relative to the previous version (approved): " + "; ".join(loosened))


def _add_warnings(ctx: _Ctx, cfg: CustomerConfig, registry: FloorRegistry, specs: dict[str, RuleSpec]) -> None:
    for name, v in sorted(cfg.defaults.items()):
        fp = registry.global_param(name)
        _cmp_warning(ctx, f"defaults.{name}", v, fp)
    for rid in sorted(cfg.rules):
        for name, v in sorted(cfg.rules[rid].items()):
            if not isinstance(v, Decimal):
                continue
            fp = _floor_param_for(registry, specs.get(rid), rid, name)
            if fp is not None:
                _cmp_warning(ctx, f"{rid}.{name}", v, fp)


def _cmp_warning(ctx: _Ctx, label: str, v: Decimal, fp: FloorParam) -> None:
    if v == fp.default:
        return
    kind = "looser" if is_looser(fp.direction, v, fp.default) else "stricter"
    ctx.warnings.append(f"{label} is {kind} than the default ({fmt(fp.default)} -> {fmt(v)})")


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
def resolve_params(
    rule: RuleSpec,
    config: CustomerConfig | None,
    registry: FloorRegistry,
    contract_id: str | None = None,
) -> dict[str, Decimal]:
    """Flat name -> Decimal for one rule. Layers: registry default -> customer value;
    with `contract_id`, a contract-scope value for that contract joins and the
    STRICTEST applicable value wins. Never clamps: a resolved value looser than a
    floor means the config bypassed validation, and the run is blocked."""
    body = config.rules.get(rule.id, {}) if config else {}
    out: dict[str, Decimal] = {}
    for name in sorted(rule.parameters):
        fp = _floor_param_for(registry, rule, rule.id, name)
        assert fp is not None
        value = body.get(name, fp.default)
        if contract_id is not None:
            scoped = body.get("scope", {}).get("contract", {}).get(contract_id, {})
            if name in scoped:
                value = strictest(fp.direction, value, scoped[name])
        if fp.looser_than_floor(value):
            raise RunBlocked(
                "config_rejected",
                f"{rule.id}.{name} resolves to {fmt(value)}, looser than the floor {fmt(fp.floor)}",  # type: ignore[arg-type]
            )
        out[name] = value
    return out


def resolve_scoped(rule: RuleSpec, config: CustomerConfig | None, registry: FloorRegistry) -> dict[str, Decimal]:
    """Contract-scope values as reserved ResolvedParams keys `scope:<contract_id>:<param>`
    (already the strictest of base and scoped). Rules that iterate per contract read them
    through `contract_param`; rules that ignore them are unaffected."""
    if config is None:
        return {}
    scoped = config.rules.get(rule.id, {}).get("scope", {}).get("contract", {})
    out: dict[str, Decimal] = {}
    for cid in sorted(scoped):
        resolved = resolve_params(rule, config, registry, contract_id=cid)
        for name in scoped[cid]:
            if name in resolved:
                out[f"{SCOPE_PREFIX}:{cid}:{name}"] = resolved[name]
    return out


def contract_param(p: dict[str, Decimal], name: str, contract_id: str | None) -> Decimal:
    """The value of `name` for a contract: its scoped value if the customer set one,
    else the rule-wide value."""
    if contract_id is not None:
        key = f"{SCOPE_PREFIX}:{contract_id}:{name}"
        if key in p:
            return p[key]
    return p[name]


def resolve_domains(config: CustomerConfig | None) -> tuple[str, ...]:
    """The compliance domains to run, in config order. No config means the default (labor only)."""
    return config.domains if config is not None else DEFAULT_ENABLED_DOMAINS


def resolve_global(config: CustomerConfig | None, registry: FloorRegistry) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for name, fp in sorted(registry.globals.items()):
        out[name] = config.defaults.get(name, fp.default) if config else fp.default
    return out
