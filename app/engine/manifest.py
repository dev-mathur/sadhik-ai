"""Run manifest and replay (design 5.5, 15.6).

The manifest pins everything a run depended on: input file hashes (sources and reference
tables), mapping versions, rule versions, the enabled compliance domains, the config file
hash, the floor registry version and the declared schedule. `replay` re-executes a stored manifest against the same files and
proves the result is byte-identical, or refuses because an input changed.

Deterministic: `executed_at` is a caller-supplied string; nothing here reads a clock.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from engine.config import ConfigResult
from engine.floors import FloorRegistry, fmt
from engine.ingest import OPTIONAL_REFERENCE_FILES, InputFile, IngestResult, file_sha256
from engine.results import RunBlocked, canonical_json
from engine.rules.base import RuleSpec, Tier, money


@dataclass(frozen=True)
class ReplayResult:
    identical: bool
    findings_compared: int
    diff: tuple[str, ...]
    reason: str | None


def _input_dict(i: InputFile) -> dict[str, Any]:
    return {"source": i.source, "file": i.file, "sha256": i.sha256, "rows": i.rows, "data_as_of": i.data_as_of}


def build_manifest(
    *,
    run_id: str,
    period: str,
    tier: Tier,
    executed_at: str | None,
    ingest: IngestResult,
    rules: list[RuleSpec],
    config_result: ConfigResult,
    registry: FloorRegistry,
    materiality_basis_usd: Decimal,
    materiality_pct: Decimal,
    materiality_usd: Decimal,
    canonical_sha256: str | None = None,
) -> dict[str, Any]:
    """JSON-safe manifest. `canonical_sha256` is the hash of RunOutput.canonical(); with
    it a manifest alone is enough to prove a replay identical."""
    assert config_result.config is not None, "a manifest is only built from an accepted config"
    cfg = config_result.config
    manifest: dict[str, Any] = {
        "run_id": run_id,
        "period": period,
        "tier": tier.value,
        "executed_at": executed_at,
        "inputs": [_input_dict(i) for i in ingest.inputs],
        "sources_absent": sorted(ingest.sources_absent),
        "mapping_versions": dict(sorted(ingest.mapping_versions.items())),
        "rule_versions": {r.id: r.version for r in sorted(rules, key=lambda r: r.id)},
        "config_file": {"config_version": cfg.config_version, "sha256": config_result.sha256},
        "floor_registry_version": registry.version,
        "declared_schedule": dict(cfg.schedule),
        "materiality": {
            "basis_usd": str(money(materiality_basis_usd)),
            "pct": fmt(materiality_pct),
            "usd": str(money(materiality_usd)),
        },
        "reference_tables": [_input_dict(i) for i in ingest.reference_tables],
        # The resolved compliance domains, in config order. Rules of any other domain were not run.
        "enabled_domains": list(cfg.domains),
    }
    if canonical_sha256 is not None:
        manifest["canonical_sha256"] = canonical_sha256
    return manifest


def canonical_hash(canonical: str) -> str:
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Replay
# --------------------------------------------------------------------------- #
def _check_inputs(manifest: dict[str, Any], data_dir: Path, config_text: str) -> None:
    changed: list[str] = []
    for entry in list(manifest.get("inputs", [])) + list(manifest.get("reference_tables", [])):
        path = Path(data_dir) / entry["file"]
        if not path.exists():
            changed.append(f"{entry['file']} is missing")
        elif file_sha256(path) != entry["sha256"]:
            changed.append(f"{entry['file']} no longer matches the hash recorded in the manifest")
    # An optional reference table that was ABSENT at run time but is present now would silently change
    # the result (it is loaded whenever the file exists), so it counts as a changed input.
    pinned_files = {e["file"] for e in list(manifest.get("inputs", [])) + list(manifest.get("reference_tables", []))}
    for name in OPTIONAL_REFERENCE_FILES:
        if name not in pinned_files and (Path(data_dir) / name).exists():
            changed.append(f"{name} was not part of the original run but is present now")
    pinned = manifest.get("config_file", {}).get("sha256")
    if pinned and hashlib.sha256(config_text.encode("utf-8")).hexdigest() != pinned:
        changed.append("the rules config file no longer matches the hash recorded in the manifest")
    if changed:
        raise RunBlocked(
            "inputs_changed",
            "Replay refused, because the pinned inputs changed: " + "; ".join(changed),
        )


def _diff_canonical(original: dict[str, Any], fresh: dict[str, Any]) -> list[str]:
    diff: list[str] = []
    for key in ("period", "tier", "total_exposure_usd"):
        if original.get(key) != fresh.get(key):
            diff.append(f"{key}: {original.get(key)} -> {fresh.get(key)}")

    def by_key(fs: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        return {(f["rule_id"], f["fingerprint"]): f for f in fs}

    a, b = by_key(original.get("findings", [])), by_key(fresh.get("findings", []))
    for k in sorted(a.keys() - b.keys()):
        diff.append(f"finding {k[0]} {k[1]} is in the original run but not in the replay")
    for k in sorted(b.keys() - a.keys()):
        diff.append(f"finding {k[0]} {k[1]} is in the replay but not in the original run")
    for k in sorted(a.keys() & b.keys()):
        fields = sorted(f for f in a[k].keys() | b[k].keys() if a[k].get(f) != b[k].get(f))
        if fields:
            diff.append(f"finding {k[0]} {k[1]} differs in: {', '.join(fields)}")
    ra = {r["rule_id"]: r for r in original.get("rule_results", [])}
    rb = {r["rule_id"]: r for r in fresh.get("rule_results", [])}
    for rid in sorted(ra.keys() | rb.keys()):
        if ra.get(rid) != rb.get(rid):
            diff.append(f"rule {rid} result differs: {ra.get(rid, {}).get('result')} -> {rb.get(rid, {}).get('result')}")
    if original.get("rollup") != fresh.get("rollup"):
        diff.append("rollup differs")
    return diff


def _diff_manifest(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    diff = []
    for rid in sorted(set(old.get("rule_versions", {})) | set(new.get("rule_versions", {}))):
        o, n = old.get("rule_versions", {}).get(rid), new.get("rule_versions", {}).get(rid)
        if o != n:
            diff.append(f"rule {rid} version {o} -> {n}")
    for key in ("mapping_versions", "floor_registry_version", "sources_absent", "materiality", "enabled_domains"):
        if old.get(key) != new.get(key):
            diff.append(f"manifest {key} differs")
    return diff


def replay(
    manifest: dict[str, Any],
    data_dir: Path,
    config_text: str,
    expected_canonical: str | None = None,
) -> ReplayResult:
    """Re-run `manifest` and compare. Raises RunBlocked("inputs_changed") if any pinned
    input (or the config) no longer hashes to what the manifest recorded.

    Compares against `expected_canonical` (the original RunOutput.canonical() string)
    when given, else against the manifest's own `canonical_sha256`. With neither there
    is nothing to compare, and the result says so rather than claiming identity."""
    from engine.runner import execute_run  # local: runner imports this module

    data_dir = Path(data_dir)
    _check_inputs(manifest, data_dir, config_text)
    fresh = execute_run(
        data_dir,
        config_text,
        manifest["period"],
        Tier(manifest["tier"]),
        manifest["run_id"],
        manifest.get("executed_at"),
    )
    fresh_canonical = fresh.canonical()
    compared = len(fresh.findings)

    if expected_canonical is not None:
        identical = fresh_canonical == expected_canonical
        diff: list[str] = []
        if not identical:
            try:
                diff = _diff_canonical(json.loads(expected_canonical), json.loads(fresh_canonical))
            except (ValueError, KeyError, TypeError):
                diff = ["the expected canonical string could not be parsed for a field-level diff"]
    elif manifest.get("canonical_sha256"):
        identical = canonical_hash(fresh_canonical) == manifest["canonical_sha256"]
        diff = [] if identical else ["canonical result hash differs from the one recorded in the manifest"]
    else:
        return ReplayResult(False, compared, (), "no baseline to compare: the manifest carries no result hash and no expected result was supplied")

    if identical:
        return ReplayResult(True, compared, (), None)
    diff = list(diff) + _diff_manifest(manifest, fresh.manifest)
    return ReplayResult(False, compared, tuple(diff), "the replayed run differs from the original")
