"""API tests: FastAPI TestClient over the tiny `mini` fixture, real engine, tmp SQLite.

The frontend was built against contracts/sample_payloads/*.json, so the first block is the
compatibility gate: every endpoint's real JSON must have the same keys and value types as its
sample. The rest covers the lifecycle, identity across re-runs, config gating, replay, tiers,
the exposure total, and the copy linter over every string the API emits.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app
from engine.copy_lint import find_restricted
from engine.metrics import total_exposure
from engine.results import jsonable
from engine.rules.base import Tier
from engine.runner import execute_run
from tests.conftest import FIXTURES, ROOT, SHIPPED_CONFIG, make_dcaa_data

MINI = FIXTURES / "mini"
SAMPLES = ROOT / "contracts" / "sample_payloads"
CONFIG_TEXT = SHIPPED_CONFIG.read_text()
PERIOD = "2026-08"


# --------------------------------------------------------------------------- helpers
class FakeClock:
    """Injectable clock: the API layer reads it, the engine never does."""

    def __init__(self, start: str = "2026-09-03T14:22:07+00:00"):
        self.now = datetime.fromisoformat(start)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


def _no_float(x: str) -> float:
    raise AssertionError(f"a float appeared in a response: {x}")


class Api:
    """TestClient wrapper that records every response body (for the copy and float sweeps)."""

    def __init__(self, client: TestClient):
        self.client = client
        self.bodies: list[tuple[str, object]] = []

    def call(self, method: str, path: str, **kw):
        r = self.client.request(method, path, **kw)
        body = json.loads(r.text, parse_float=_no_float) if r.text else None
        self.bodies.append((f"{method} {path}", body))
        r.json_body = body  # type: ignore[attr-defined]
        return r

    def get(self, path, **kw):
        return self.call("GET", path, **kw)

    def post(self, path, **kw):
        return self.call("POST", path, **kw)

    def put(self, path, **kw):
        return self.call("PUT", path, **kw)

    def run(self, tier="close", period=PERIOD):
        r = self.post("/api/runs", json={"period": period, "tier": tier})
        assert r.status_code == 200, r.text
        return r.json_body

    def dispose(self, fid, disposition, actor="a.okafor", **kw):
        return self.post(f"/api/findings/{fid}/disposition", json={"disposition": disposition, "actor": actor, **kw})

    def findings(self, **params):
        return self.get("/api/findings", params=params).json_body["items"]

    def status_of(self, fid) -> str:
        return self.get(f"/api/findings/{fid}").json_body["status"]


def make_api(tmp_path: Path, monkeypatch, data_dir: Path = MINI, clock: FakeClock | None = None, name="t.db"):
    """An app configured the production way: through SADHIK_DB / SADHIK_DATA_DIR."""
    monkeypatch.setenv("SADHIK_DB", str(tmp_path / name))
    monkeypatch.setenv("SADHIK_DATA_DIR", str(data_dir))
    app = create_app(clock=clock or FakeClock())
    return Api(TestClient(app))


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def api(tmp_path, monkeypatch, clock):
    return make_api(tmp_path, monkeypatch, clock=clock)


@pytest.fixture()
def data_copy(tmp_path):
    dst = tmp_path / "data"
    shutil.copytree(MINI, dst)
    return dst


@pytest.fixture()
def api_copy(tmp_path, monkeypatch, clock, data_copy):
    return make_api(tmp_path, monkeypatch, data_dir=data_copy, clock=clock)


def full_coverage_data(dst: Path) -> Path:
    """`mini` + the DCAA overlay + three prior periods of classification history for E-0001, so L-08 evaluates
    (an employee needs 3 prior periods). With the rate file this puts all 11 coverage rules in play. The overlay's
    allowability table, filing schedule and ceilings are all CLEAN, so C-01, C-02 and C-03 evaluate to Consistent and add
    no findings: finding counts in the tests that use this data are unchanged."""
    make_dcaa_data(dst)
    periods = ("2026-05", "2026-06", "2026-07")
    history = {"periods": list(periods), "employees": {
        "E-0001": {p: {"direct_hours": "150.0", "indirect_hours": "6.0"} for p in periods}}}
    (dst / "classification_history.json").write_text(json.dumps(history))
    return dst


def sample(name: str):
    return json.loads((SAMPLES / f"{name}.json").read_text())


# Objects whose keys vary by design (per rule, per source kind, per contract); only their type is checked.
FREE_DICTS = (".computed", ".detail", ".mapping_versions", ".rule_versions", ".declared_schedule")
# Keys the engine's manifest carries beyond the hand-written sample (real, useful, harmless to a TS reader).
# `reference_tables` and `enabled_domains` are now IN the samples, so they are no longer extras.
MANIFEST_EXTRAS = {"canonical_sha256"}
# A rule row in status.rules / run_detail.rules carries `reason` only when the rule is Not evaluated or Not
# applicable (the TS type says `reason?`). The updated samples show a fully evaluated period, so no sample
# element has it; it is optional, not unexpected.
OPTIONAL_RULE_ROW_KEYS = {"reason"}
# The engine describes a reference table exactly as it describes an input ({source, file, sha256, rows,
# data_as_of}); the sample's hand-written {name, sha256} is the subset the frontend reads. The API serves the
# engine's keys plus `name` (= source), so both readers are satisfied.
REFERENCE_TABLE_EXTRAS = {"source", "file", "rows", "data_as_of"}


def _kind(v) -> str:
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    return type(v).__name__


def assert_same_shape(actual, samples, path="$"):
    """Same keys and value types at every level. `samples` is one sample value or a list of them (all
    the sample elements at this position). Rules: null in the sample means any; null in the actual is
    a legitimate nullable value; a list is checked element by element against the union of the sample
    elements' keys (a key absent from some sample elements is optional); free-form dicts are only
    checked to be dicts."""
    cands = samples if isinstance(samples, list) and getattr(samples, "_multi", False) else [samples]
    cands = [c for c in cands if c is not None]
    if not cands or actual is None:
        return
    first = cands[0]
    assert _kind(actual) == _kind(first), f"{path}: type {_kind(actual)} != sample {_kind(first)}"
    if isinstance(first, dict):
        if any(path.endswith(s) for s in FREE_DICTS):
            return
        keys_all = set().union(*(c.keys() for c in cands))
        keys_req = set.intersection(*(set(c.keys()) for c in cands))
        extra = MANIFEST_EXTRAS if path.endswith(".manifest") else set()
        if re.search(r"\.rules\[\d+\]$", path):
            extra = extra | OPTIONAL_RULE_ROW_KEYS
        if re.search(r"\.reference_tables\[\d+\]$", path):
            extra = extra | REFERENCE_TABLE_EXTRAS
        assert set(actual) - extra <= keys_all, f"{path}: unexpected keys {sorted(set(actual) - extra - keys_all)}"
        assert keys_req <= set(actual), f"{path}: missing keys {sorted(keys_req - set(actual))}"
        for k in actual:
            if k in keys_all:
                assert_same_shape(actual[k], _Multi(c[k] for c in cands if k in c), f"{path}.{k}")
    elif isinstance(first, list):
        elems = [e for c in cands for e in c]
        if elems:
            multi = _Multi(elems)
            for i, a in enumerate(actual):
                assert_same_shape(a, multi, f"{path}[{i}]")


class _Multi(list):
    _multi = True


def check(actual, name):
    assert_same_shape(actual, sample(name), name)


def walk_strings(x):
    if isinstance(x, str):
        yield x
    elif isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk_strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk_strings(v)


def rejected_yaml() -> str:
    """The shipped config with four different refusals: a value over max, a value looser than the
    floor, a `floor:` key, and enabled:false on a contract/regulatory rule."""
    return (
        CONFIG_TEXT.replace("late_threshold_hours: 48     # default 72 -> stricter, accepted", "late_threshold_hours: 200")
        + "  L-09:\n    out_of_pop_hours_tolerance: 5\n    enabled: false\n  L-01:\n    floor: 0\n"
    )


# =========================================================================== 1. shape gate
def test_health(api):
    r = api.get("/api/health")
    assert r.status_code == 200 and r.json_body == {"ok": True}


def test_every_endpoint_matches_its_sample_shape(api):
    created = api.run("close")
    check(created, "run_created")

    check(api.get("/api/status").json_body, "status")
    check(api.get("/api/findings").json_body, "findings_list")
    fid = api.get("/api/findings").json_body["items"][0]["finding_id"]
    assert fid == "F-3311"
    check(api.get(f"/api/findings/{fid}").json_body, "finding_detail")
    check(api.get(f"/api/findings/{fid}/evidence").json_body, "evidence")
    check(api.dispose(fid, "in_review", note="Assigned to the project accountant").json_body, "disposition_response")

    check(api.get("/api/rules").json_body, "rules_list")
    check(api.get("/api/rules/L-05").json_body, "rule_detail")
    check(api.get("/api/rules/DQ-01").json_body, "rule_detail")

    check(api.get("/api/config").json_body, "config_get")
    ok = api.get("/api/config").json_body["yaml"].replace("late_threshold_hours: 48", "late_threshold_hours: 40")
    v = api.post("/api/config/validate", json={"yaml": ok})
    assert v.status_code == 200
    check(v.json_body, "config_accepted")
    bad = api.post("/api/config/validate", json={"yaml": rejected_yaml()})
    assert bad.status_code == 200  # validate is a dry run: 200 either way
    check(bad.json_body, "config_rejected")
    put_ok = api.put("/api/config", json={"yaml": ok})
    assert put_ok.status_code == 200
    check(put_ok.json_body, "config_accepted")
    put_bad = api.put("/api/config", json={"yaml": rejected_yaml()})
    assert put_bad.status_code == 422
    check(put_bad.json_body, "config_rejected")

    check(api.get("/api/runs").json_body, "runs_list")
    check(api.get(f"/api/runs/{created['run_id']}").json_body, "run_detail")
    rep = api.post(f"/api/runs/{created['run_id']}/replay")
    assert rep.status_code == 200
    check(rep.json_body, "replay_result")


def test_no_float_or_restricted_word_in_any_response(api, data_copy, tmp_path):
    """Sweeps every body the API emitted in a broad scenario, errors included."""
    api.run("close")
    api.run("fast")
    api.get("/api/status")
    api.get("/api/status", params={"as_of": "2026-10-30"})
    for r in api.get("/api/findings").json_body["items"]:
        api.get(f"/api/findings/{r['finding_id']}")
        api.get(f"/api/findings/{r['finding_id']}/evidence")
    api.dispose("F-3311", "in_review", note="Assigned for review")
    api.dispose("F-3311", "legit_exception")  # 422
    api.dispose("F-3311", "closed")  # 409
    api.get("/api/findings/F-9999")  # 404
    api.get("/api/nope")  # 404 envelope
    api.get("/api/rules")
    for rid in ("L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "DQ-01"):
        api.get(f"/api/rules/{rid}")
    api.get("/api/rules", params={"domain": "dcaa_cost_accounting"})
    api.get("/api/findings", params={"domain": "labor"})
    api.get("/api/findings", params={"domain": "nope"})  # 400
    api.get("/api/rules/X-99")
    api.get("/api/config")
    api.post("/api/config/validate", json={"yaml": rejected_yaml()})
    api.post("/api/config/validate", json={"yaml": "rules: [unclosed"})
    api.post("/api/runs", json={"period": PERIOD, "tier": "weekly"})
    api.post("/api/runs", json={"period": "August", "tier": "close"})
    api.post("/api/runs", json={"period": "2026-07", "tier": "close"})  # engine: period mismatch
    api.get("/api/runs")
    api.get("/api/runs/RUN-2026-08-MER-001")
    api.post("/api/runs/RUN-2026-08-MER-001/replay")
    api.post("/api/runs/RUN-nope/replay")
    assert len(api.bodies) > 40
    offenders = [(where, s, find_restricted(s)) for where, body in api.bodies for s in walk_strings(body) if find_restricted(s)]
    assert offenders == []


def test_errors_use_the_envelope(api):
    for method, path, kw, status, code in [
        ("GET", "/api/findings/F-9999", {}, 404, "finding_not_found"),
        ("GET", "/api/rules/X-99", {}, 404, "rule_not_found"),
        ("GET", "/api/runs/RUN-nope", {}, 404, "run_not_found"),
        ("POST", "/api/runs/RUN-nope/replay", {}, 404, "run_not_found"),
        ("GET", "/api/nope", {}, 404, "not_found"),
        ("GET", "/api/findings?severity=urgent", {}, 400, "bad_filter"),
        ("GET", "/api/findings?status=maybe", {}, 400, "bad_filter"),
        ("GET", "/api/status?as_of=tomorrow", {}, 400, "bad_filter"),
        ("GET", "/api/status?period=Aug", {}, 400, "bad_filter"),
        ("POST", "/api/runs", {"json": {"period": PERIOD, "tier": "weekly"}}, 422, "invalid_tier"),
        ("POST", "/api/runs", {"json": {"period": "Aug", "tier": "close"}}, 422, "invalid_period"),
        ("POST", "/api/runs", {"json": {"period": PERIOD}}, 422, "invalid_request"),
        ("PUT", "/api/config", {"json": {}}, 422, "invalid_request"),
    ]:
        r = api.call(method, path, **kw)
        assert r.status_code == status, (path, r.text)
        assert set(r.json_body) == {"error"} and r.json_body["error"]["code"] == code, (path, r.json_body)
        assert r.json_body["error"]["message"]


def test_cors_allows_localhost_dev_origins(api):
    r = api.client.options(
        "/api/status",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )
    assert r.status_code == 200 and r.headers["access-control-allow-origin"] == "http://localhost:5173"


# =========================================================================== run + persistence
def test_run_ids_finding_ids_and_persisted_detail(api):
    a = api.run("close")
    assert a["run_id"] == "RUN-2026-08-MER-001" and a["new_findings"] == a["findings"] == 9
    assert a["executed_at"] == "2026-09-03T14:22:07Z"
    items = api.get("/api/findings").json_body["items"]
    ids = [i["finding_id"] for i in items]
    assert ids == [f"F-{n}" for n in range(3311, 3311 + 9)]  # sequential, in list order for a first run
    # list order: severity high->low, then coverage rules before the DQ data-quality gates, then rule id
    sev_rank = {"high": 0, "medium": 1, "low": 2}
    keys = [(sev_rank[i["severity"]], i["rule_id"].startswith("DQ-"), i["rule_id"]) for i in items]
    assert keys == sorted(keys)

    d = api.get("/api/findings/F-3311").json_body
    assert d["run_id"] == d["latest_run_id"] == "RUN-2026-08-MER-001"
    assert d["status"] == "open" and d["allowed_transitions"] == ["in_review"]
    assert d["history"] == [
        {"from": None, "to": "open", "actor": "system", "at": "2026-09-03T14:22:07Z", "reason_code": None,
         "note": "Opened by RUN-2026-08-MER-001"}
    ]
    assert set(d["explanation"]) == {"what_happened", "why_it_matters", "impact", "recommended_action"}
    assert all(d["explanation"].values())
    assert isinstance(d["affected"]["entries"], int)
    assert "entry_costs" not in d["computed"]  # engine-internal M13 input, not served
    assert all(isinstance(v, str) for v in d["computed"].values())  # Record<string,string>: never null, never nested
    assert (d["domain"], d["domain_name"]) == ("labor", "Labor")
    ev = api.get("/api/findings/F-3311/evidence").json_body
    assert ev["total"] == d["evidence_count"] == len(ev["items"]) and ev["page"] == 1 and ev["page_size"] == 50

    # run detail: manifest from the engine, per-rule results including DQ-01 and the Not evaluated rule
    rd = api.get(f"/api/runs/{a['run_id']}").json_body
    assert rd["manifest"]["run_id"] == a["run_id"] and rd["manifest"]["executed_at"] == a["executed_at"]
    by_rule = {r["rule_id"]: r for r in rd["rules"]}
    assert set(by_rule) == {"L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "C-01", "C-02", "C-03", "DQ-01"}
    assert by_rule["L-11"]["result"] == "Not evaluated" and by_rule["L-11"]["reason"]
    # `mini` has no rate file, no account allowability table and no filing schedule: the three C-rules cannot evaluate
    assert all(by_rule[r]["result"] == "Not evaluated" and by_rule[r]["reason"] for r in ("C-01", "C-02", "C-03"))
    assert "allowability" in by_rule["C-01"]["reason"].lower()
    # `mini` has no classification-history file, so L-08 cannot evaluate: the engine says so, never "Consistent"
    assert by_rule["L-08"]["result"] == "Not evaluated" and "classification history" in by_rule["L-08"]["reason"].lower()
    assert {r["rule_id"]: r["domain"] for r in rd["rules"]} == {
        **{rid: "labor" for rid in ("L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01")},
        **{rid: "dcaa_cost_accounting" for rid in ("L-08", "L-11", "C-01", "C-02", "C-03")}}
    assert rd["manifest"]["enabled_domains"] == ["labor", "dcaa_cost_accounting"]
    # Both domains are enabled but `mini` gives DCAA cost accounting none of its data (0 of 5 rules evaluated),
    # so the engine's run label is "Incomplete data" (a thin domain takes precedence), not "9 open findings".
    assert rd["findings"] == ids and rd["label"] == a["label"] == "Incomplete data"
    assert [r["run_id"] for r in api.get("/api/runs").json_body["items"]] == [a["run_id"]]


def test_evidence_paging(api):
    api.run("close")
    fid = next(i["finding_id"] for i in api.findings() if i["rule_id"] == "L-02")
    full = api.get(f"/api/findings/{fid}/evidence").json_body
    p1 = api.get(f"/api/findings/{fid}/evidence", params={"page": 1, "page_size": 1}).json_body
    p9 = api.get(f"/api/findings/{fid}/evidence", params={"page": 9, "page_size": 1}).json_body
    assert p1["total"] == full["total"] and len(p1["items"]) == 1 and p1["items"][0] == full["items"][0]
    assert p9["items"] == [] and p9["total"] == full["total"]
    assert api.get(f"/api/findings/{fid}/evidence", params={"page": 0}).status_code == 400


def test_findings_filters(api):
    api.run("close")
    allf = api.findings()
    assert {i["severity"] for i in api.findings(severity="medium")} == {"medium"}
    assert [i["rule_id"] for i in api.findings(rule="DQ-01")] == ["DQ-01"]
    assert api.findings(status="closed") == []
    assert len(api.findings(status="open")) == len(allf) == len(api.findings(period=PERIOD))
    assert api.findings(period="2026-07") == []
    assert api.get("/api/findings", params={"rule": "L-02"}).json_body["total"] == len(api.findings(rule="L-02"))


def test_a_blocked_run_is_a_422_and_persists_nothing(api_copy, data_copy):
    tk = data_copy / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text() + "TE-000006,E-0001,2026-08-06,8.0,ZZ-999,Data Engineer II,2026-08-06T17:00:00,,,\n")
    r = api_copy.post("/api/runs", json={"period": PERIOD, "tier": "close"})
    assert r.status_code == 422 and r.json_body["error"]["code"] == "unmapped_charge_code"
    assert "ZZ-999" in r.json_body["error"]["message"]
    assert api_copy.get("/api/runs").json_body["items"] == []
    assert api_copy.get("/api/findings").json_body["total"] == 0
    r = api_copy.post("/api/runs", json={"period": "2026-07", "tier": "close"})
    assert r.status_code == 422 and r.json_body["error"]["code"] == "period_mismatch"


# =========================================================================== 2. lifecycle
LEGAL_PATH = [
    ("in_review", {}),
    ("confirmed", {}),
    ("remediated", {"note": "Corrected the timesheet and the invoice"}),
    ("closed", {}),
]


def test_every_legal_transition_and_history_accumulates(api, clock):
    api.run("close")
    findings = [i["finding_id"] for i in api.findings()][:4]
    # 1) open -> in_review -> confirmed -> remediated -> closed
    f = findings[0]
    expected_from = "open"
    for i, (to, extra) in enumerate(LEGAL_PATH, start=1):
        clock.advance(minutes=5)
        r = api.dispose(f, to, **extra)
        assert r.status_code == 200, r.text
        body = r.json_body
        assert body["finding_id"] == f and body["status"] == to  # the FULL finding detail
        assert {"explanation", "history", "allowed_transitions", "computed", "metric", "affected"} <= set(body)
        assert len(body["history"]) == 1 + i
        last = body["history"][-1]
        assert (last["from"], last["to"], last["actor"]) == (expected_from, to, "a.okafor")
        assert last["at"] == clock().strftime("%Y-%m-%dT%H:%M:%SZ")
        expected_from = to
    assert body["allowed_transitions"] == []
    # 2) in_review -> data_error -> remediated
    g = findings[1]
    api.dispose(g, "in_review")
    r = api.dispose(g, "data_error", reason_code="source_data_error", note="Roster export was stale")
    assert r.json_body["status"] == "data_error" and r.json_body["allowed_transitions"] == ["remediated"]
    assert r.json_body["history"][-1]["reason_code"] == "source_data_error"
    assert api.dispose(g, "remediated").json_body["status"] == "remediated"
    # 3) in_review -> legit_exception -> closed
    h = findings[2]
    api.dispose(h, "in_review")
    r = api.dispose(h, "legit_exception", reason_code="approved_exception", note="Approved by the contracting officer")
    body = r.json_body
    assert body["status"] == "legit_exception" and body["allowed_transitions"] == ["closed"]
    assert (body["history"][-1]["reason_code"], body["history"][-1]["note"]) == (
        "approved_exception", "Approved by the contracting officer")
    assert api.dispose(h, "closed").json_body["status"] == "closed"
    # allowed_transitions per state, as offered by the detail endpoint
    assert api.get(f"/api/findings/{findings[3]}").json_body["allowed_transitions"] == ["in_review"]
    api.dispose(findings[3], "in_review")
    assert api.get(f"/api/findings/{findings[3]}").json_body["allowed_transitions"] == [
        "confirmed", "legit_exception", "data_error"]


@pytest.mark.parametrize(
    "path,target",
    [
        ([], "confirmed"),  # open -> confirmed skips review
        ([], "closed"),
        ([], "open"),
        (["in_review"], "remediated"),
        (["in_review"], "closed"),
        (["in_review"], "in_review"),
        (["in_review", "confirmed"], "closed"),
        (["in_review", "confirmed"], "legit_exception"),
        (["in_review", "confirmed", "remediated", "closed"], "open"),  # closed -> open is system-only
        (["in_review", "confirmed", "remediated", "closed"], "in_review"),
        (["in_review", "data_error"], "closed"),
    ],
)
def test_illegal_transitions_are_409(api, path, target):
    api.run("close")
    f = "F-3311"
    for step in path:
        assert api.dispose(f, step).status_code == 200
    before = api.get(f"/api/findings/{f}").json_body
    r = api.dispose(f, target, reason_code="other", note="x")
    assert r.status_code == 409, r.text
    assert r.json_body["error"]["code"] == "invalid_transition"
    after = api.get(f"/api/findings/{f}").json_body
    assert after["status"] == before["status"] and after["history"] == before["history"]  # nothing written


def test_legit_exception_needs_reason_code_and_note(api):
    api.run("close")
    api.dispose("F-3311", "in_review")
    for extra in ({}, {"reason_code": "approved_exception"}, {"note": "Approved"}, {"reason_code": "approved_exception", "note": "  "}):
        r = api.dispose("F-3311", "legit_exception", **extra)
        assert r.status_code == 422 and r.json_body["error"]["code"] == "reason_required", extra
    assert api.status_of("F-3311") == "in_review"
    r = api.dispose("F-3311", "legit_exception", reason_code="not_a_code", note="Approved")
    assert r.status_code == 422 and r.json_body["error"]["code"] == "invalid_reason_code"
    assert api.dispose("F-3311", "legit_exception", reason_code="approved_exception", note="Approved").status_code == 200


def test_disposition_request_validation(api):
    api.run("close")
    assert api.post("/api/findings/F-3311/disposition", json={"disposition": "in_review"}).status_code == 422  # no actor
    assert api.post("/api/findings/F-3311/disposition", json={"disposition": "in_review", "actor": " "}).status_code == 422
    r = api.dispose("F-3311", "bogus")
    assert r.status_code == 422 and r.json_body["error"]["code"] == "invalid_disposition"
    assert api.dispose("F-9999", "in_review").status_code == 404


# =========================================================================== 3. identity
def test_rerun_keeps_ids_and_dispositions_and_advances_latest_run(api, clock):
    first = api.run("close")
    before = {i["finding_id"]: i for i in api.findings()}
    api.dispose("F-3311", "in_review", note="Assigned")
    api.dispose("F-3312", "in_review")
    api.dispose("F-3312", "legit_exception", reason_code="approved_exception", note="Approved exception")
    clock.advance(days=1)
    second = api.run("close")
    assert second["run_id"] == "RUN-2026-08-MER-002" and second["new_findings"] == 0
    assert second["findings"] == first["findings"] == 9
    assert second["total_exposure_usd"] == first["total_exposure_usd"] and second["label"] == first["label"]

    after = {i["finding_id"]: i for i in api.findings()}
    assert set(after) == set(before)  # no duplicates
    assert api.status_of("F-3311") == "in_review" and api.status_of("F-3312") == "legit_exception"
    d = api.get("/api/findings/F-3311").json_body
    assert d["run_id"] == "RUN-2026-08-MER-001" and d["latest_run_id"] == "RUN-2026-08-MER-002"
    assert [h["to"] for h in d["history"]] == ["open", "in_review"]  # a re-run adds no history to a live finding
    untouched = api.get("/api/findings/F-3313").json_body
    assert untouched["latest_run_id"] == "RUN-2026-08-MER-002" and len(untouched["history"]) == 1
    assert api.get("/api/runs/RUN-2026-08-MER-002").json_body["findings"] == list(before)
    assert [r["run_id"] for r in api.get("/api/runs").json_body["items"]] == [
        "RUN-2026-08-MER-002", "RUN-2026-08-MER-001"]


def test_a_closed_finding_reopens_when_its_fingerprint_recurs(api, clock):
    api.run("close")
    api.dispose("F-3311", "in_review")
    api.dispose("F-3311", "legit_exception", reason_code="approved_exception", note="Approved exception")
    assert api.dispose("F-3311", "closed").json_body["status"] == "closed"
    open_before = api.get("/api/status").json_body["open_findings"]
    clock.advance(days=2)
    r = api.run("close")
    assert r["new_findings"] == 0
    d = api.get("/api/findings/F-3311").json_body
    assert d["status"] == "open" and d["latest_run_id"] == "RUN-2026-08-MER-002"
    last = d["history"][-1]
    assert (last["from"], last["to"], last["actor"]) == ("closed", "open", "system")
    assert last["at"] == "2026-09-05T14:22:07Z" and "RUN-2026-08-MER-002" in last["note"]
    assert d["allowed_transitions"] == ["in_review"]
    assert api.get("/api/status").json_body["open_findings"] == open_before + 1


def test_findings_of_rules_a_later_run_did_not_evaluate_are_left_alone(api, clock):
    api.run("close")
    before = {i["finding_id"]: i for i in api.findings()}
    clock.advance(days=1)
    fast = api.run("fast")  # pay_period and close rules are Not evaluated in a fast run
    assert fast["new_findings"] == 0
    assert {i["finding_id"] for i in api.findings()} == set(before)  # nothing deleted or auto-closed
    l02 = next(i["finding_id"] for i in api.findings() if i["rule_id"] == "L-02")
    d = api.get(f"/api/findings/{l02}").json_body
    assert d["status"] == "open" and d["latest_run_id"] == "RUN-2026-08-MER-001"  # untouched by the fast run
    l09 = next(i["finding_id"] for i in api.findings() if i["rule_id"] == "L-09")
    assert api.get(f"/api/findings/{l09}").json_body["latest_run_id"] == "RUN-2026-08-MER-002"


def test_a_fingerprint_absent_from_a_run_that_evaluated_its_rule_is_not_deleted(api_copy, data_copy, clock):
    api_copy.run("close")
    l09 = next(i["finding_id"] for i in api_copy.findings() if i["rule_id"] == "L-09")
    # The period of performance is extended, so the out-of-PoP charge no longer exists.
    path = data_copy / "contracts.json"
    path.write_text(path.read_text().replace('"pop_end":"2026-08-15"', '"pop_end":"2026-12-31"'))
    clock.advance(days=1)
    r = api_copy.run("close")
    assert r["findings"] == 8 and r["new_findings"] == 0
    d = api_copy.get(f"/api/findings/{l09}").json_body
    assert d["status"] == "open" and d["latest_run_id"] == "RUN-2026-08-MER-001"  # kept, unchanged
    assert api_copy.get("/api/findings").json_body["total"] == 9
    # the run detail lists what THAT run found, and the rule's own result shows it evaluated cleanly
    assert l09 not in api_copy.get("/api/runs/RUN-2026-08-MER-002").json_body["findings"]


# =========================================================================== 4. config
def test_config_get_is_the_seeded_version(api):
    c = api.get("/api/config").json_body
    assert c["config_version"] == 7 and c["yaml"] == CONFIG_TEXT  # the shipped file declares config_version: 7
    assert c["floor_registry_version"] == "2026-09-01"
    assert [h["config_version"] for h in c["history"]] == [7]
    assert c["history"][0]["changed_by"] == "a.okafor" and c["history"][0]["approved_by"] == "r.delgado"
    assert c["history"][0]["effective_from"] == "2026-09-01" and c["history"][0]["sha256"] == c["sha256"]


def test_validate_accepts_a_stricter_value_and_persists_nothing(api):
    text = CONFIG_TEXT.replace("late_threshold_hours: 48", "late_threshold_hours: 36")
    r = api.post("/api/config/validate", json={"yaml": text})
    body = r.json_body
    assert r.status_code == 200 and body["accepted"] is True and body["errors"] == []
    assert body["config_version"] == 8  # the next number after the seeded 7
    assert any("late_threshold_hours" in w and "stricter" in w for w in body["warnings"])
    import hashlib

    assert body["sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert api.get("/api/config").json_body["config_version"] == 7  # dry run
    assert len(api.get("/api/config").json_body["history"]) == 1


def test_validate_rejects_with_line_numbers(api):
    text = rejected_yaml()
    lines = text.splitlines()
    body = api.post("/api/config/validate", json={"yaml": text}).json_body
    assert body["accepted"] is False and body["config_version"] is None
    by_code = {e["code"]: e for e in body["errors"]}
    assert {"exceeds_max", "looser_than_floor", "floor_not_settable", "cannot_disable_regulatory_rule"} <= set(by_code)
    assert by_code["exceeds_max"]["path"] == "rules.L-05.late_threshold_hours"
    assert by_code["looser_than_floor"]["path"] == "rules.L-09.out_of_pop_hours_tolerance"
    assert by_code["floor_not_settable"]["path"] == "rules.L-01.floor"
    assert by_code["cannot_disable_regulatory_rule"]["path"] == "rules.L-09.enabled"
    for e in body["errors"]:
        assert isinstance(e["line"], int)  # 1-based line in the SUBMITTED text
        assert e["path"].split(".")[-1] in lines[e["line"] - 1], (e, lines[e["line"] - 1])
    import hashlib

    assert body["sha256"] == hashlib.sha256(text.encode()).hexdigest()  # the hash of what was submitted, even when refused


def test_yaml_syntax_error_is_a_config_result_not_an_error_envelope(api):
    r = api.post("/api/config/validate", json={"yaml": "rules: [unclosed\n  - x: : :"})
    assert r.status_code == 200 and r.json_body["accepted"] is False
    assert r.json_body["errors"][0]["code"] == "yaml_syntax" and "error" not in r.json_body
    r = api.put("/api/config", json={"yaml": "rules: [unclosed"})
    assert r.status_code == 422 and r.json_body["accepted"] is False and r.json_body["errors"][0]["code"] == "yaml_syntax"


def test_loosening_needs_approval(api):
    api.put("/api/config", json={"yaml": CONFIG_TEXT.replace("late_threshold_hours: 48", "late_threshold_hours: 36")})
    loosened = CONFIG_TEXT.replace("late_threshold_hours: 48", "late_threshold_hours: 60").replace(
        "approved_by: r.delgado           # Controller; required for any loosening\n", "")
    body = api.post("/api/config/validate", json={"yaml": loosened}).json_body
    assert body["accepted"] is False and [e["code"] for e in body["errors"]] == ["missing_approval"]
    approved = loosened.replace("changed_by: a.okafor", "changed_by: a.okafor\napproved_by: r.delgado")
    assert api.post("/api/config/validate", json={"yaml": approved}).json_body["accepted"] is True


def test_put_rejected_creates_no_version_and_put_accepted_creates_the_next(api):
    r = api.put("/api/config", json={"yaml": rejected_yaml()})
    assert r.status_code == 422
    assert r.json_body["accepted"] is False and r.json_body["errors"] and r.json_body["config_version"] is None
    c = api.get("/api/config").json_body
    assert c["config_version"] == 7 and c["yaml"] == CONFIG_TEXT and len(c["history"]) == 1

    shipped_reason = next(line for line in CONFIG_TEXT.splitlines() if line.startswith("reason:"))
    text = CONFIG_TEXT.replace("late_threshold_hours: 48", "late_threshold_hours: 40").replace(
        shipped_reason, 'reason: "Tightened again"')
    assert 'reason: "Tightened again"' in text  # the replace above must have matched
    r = api.put("/api/config", json={"yaml": text})
    assert r.status_code == 200 and r.json_body["accepted"] is True and r.json_body["config_version"] == 8
    c = api.get("/api/config").json_body
    assert c["config_version"] == 8 and c["yaml"] == text and c["sha256"] == r.json_body["sha256"]
    assert [h["config_version"] for h in c["history"]] == [8, 7]
    assert c["history"][0]["reason"] == "Tightened again"
    # The next run uses the new version, and the rules catalog shows the new effective value.
    run = api.run("close")
    assert api.get("/api/runs").json_body["items"][0]["config_version"] == 8
    late = next(p for p in api.get("/api/rules/L-05").json_body["parameters"] if p["name"] == "late_threshold_hours")
    assert late["effective"] == "40" and late["default"] == "72"
    assert run["run_id"] == "RUN-2026-08-MER-001"


def test_a_file_declaring_another_config_version_is_saved_under_the_next_number_with_a_warning(api):
    r = api.put("/api/config", json={"yaml": CONFIG_TEXT})  # declares config_version: 7, the active version
    assert r.status_code == 200 and r.json_body["config_version"] == 8
    assert any("config_version 7" in w and "version 8" in w for w in r.json_body["warnings"])


# =========================================================================== rules catalog
def test_rules_catalog_is_complete_and_joined_with_config_and_floors(api):
    items = api.get("/api/rules").json_body["items"]
    # every registered rule, coverage rules first (sorted by id, so the C- rules lead), the data-quality gate last
    assert [i["id"] for i in items] == ["C-01", "C-02", "C-03", "L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "DQ-01"]
    by = {i["id"]: i for i in items}
    assert {i["status"] for i in items} == {"active"} and by["L-11"]["status"] == "active"  # not not_applicable
    assert {i["last_result"] for i in items} == {"Not evaluated"}  # nothing has run yet
    assert by["L-05"]["tier"] == "fast" and by["L-11"]["tier"] == "close" and by["DQ-01"]["parameters"] == []
    assert by["L-08"]["tier"] == "fast" and by["L-08"]["required_sources"] == ["timekeeping"]  # timekeeping only
    assert {i["id"]: i["domain"] for i in items} == {
        **{rid: "labor" for rid in ("L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01")},
        **{rid: "dcaa_cost_accounting" for rid in ("L-08", "L-11", "C-01", "C-02", "C-03")}}
    assert {i["domain_name"] for i in items} == {"Labor", "DCAA cost accounting"}
    assert all(by[r]["tier"] == "close" for r in ("C-01", "C-02", "C-03"))  # GL and/or rate data: the close tier
    assert {p["name"] for p in by["C-01"]["parameters"]} == {"unallowable_tolerance_usd"}
    assert {p["name"] for p in by["C-02"]["parameters"]} == {"ics_due_months"}
    assert {p["name"] for p in by["C-03"]["parameters"]} == {"ceiling_excess_tolerance"}
    ics = by["C-02"]["parameters"][0]  # the regulatory floor: six months, never later
    assert (ics["default"], ics["floor"], ics["max"], ics["direction"]) == ("6", "6", "6", "lower_is_stricter")
    assert {p["name"] for p in by["L-08"]["parameters"]} == {"indirect_share_shift_pp", "min_excess_hours"}
    assert {p["name"] for p in by["L-11"]["parameters"]} == {
        "rate_drift_watch_pct", "rate_drift_exception_pct", "systemic_periods"}
    late = next(p for p in by["L-05"]["parameters"] if p["name"] == "late_threshold_hours")
    assert late["default"] == "72" and late["effective"] == "48" and late["direction"] == "lower_is_stricter"
    assert (late["min"], late["max"], late["floor"]) == ("24", "168", None)
    tol = by["L-09"]["parameters"][0]
    assert tol["floor"] == "0" and tol["effective"] == "0"
    api.run("close")
    after = {i["id"]: i["last_result"] for i in api.get("/api/rules").json_body["items"]}
    assert after["L-11"] == "Not evaluated" and after["L-02"] == "Exception" and after["L-01"] == "Consistent"
    # `mini` has no rate file, no classification history, no allowability table and no filing schedule:
    # none of the five DCAA rules can evaluate
    assert {after[r] for r in ("L-08", "L-11", "C-01", "C-02", "C-03")} == {"Not evaluated"}


# =========================================================================== 5. replay
def test_replay_is_identical_after_a_run(api):
    created = api.run("close")
    r = api.post(f"/api/runs/{created['run_id']}/replay")
    assert r.status_code == 200
    assert r.json_body == {"run_id": created["run_id"], "identical": True, "findings_compared": 9, "diff": [], "reason": None}


def test_replay_survives_a_later_config_change(api):
    created = api.run("close")
    api.put("/api/config", json={"yaml": CONFIG_TEXT.replace("late_threshold_hours: 48", "late_threshold_hours: 40")})
    assert api.post(f"/api/runs/{created['run_id']}/replay").json_body["identical"] is True  # pinned config version


def test_replay_after_an_input_changed_reports_the_reason_and_does_not_crash(api_copy, data_copy):
    created = api_copy.run("close")
    tk = data_copy / "meridian_time_2026-08.csv"
    tk.write_text(tk.read_text().replace("8.0,C8841-DEV,Data Engineer II,2026-08-03", "7.0,C8841-DEV,Data Engineer II,2026-08-03"))
    r = api_copy.post(f"/api/runs/{created['run_id']}/replay")
    assert r.status_code == 200
    body = r.json_body
    assert body["identical"] is False and body["findings_compared"] == 0
    assert "meridian_time_2026-08.csv" in body["reason"] and "changed" in body["reason"]
    assert body["diff"] == ["meridian_time_2026-08.csv no longer matches the hash recorded in the manifest"]
    # a file that has gone missing is reported the same way
    (data_copy / "adp_payroll_2026-08.csv").unlink()
    body = api_copy.post(f"/api/runs/{created['run_id']}/replay").json_body
    assert body["identical"] is False and any("adp_payroll_2026-08.csv is missing" in d for d in body["diff"])


def test_replay_reports_a_result_difference_with_string_diffs(api, monkeypatch):
    created = api.run("close")
    import api.service as svc_mod
    from engine.manifest import ReplayResult

    monkeypatch.setattr(
        svc_mod, "engine_replay",
        lambda *a, **k: ReplayResult(False, 9, ("F-3312: exposure_usd 27518.40 != 27518.00",), "the replayed run differs from the original"),
    )
    body = api.post(f"/api/runs/{created['run_id']}/replay").json_body
    assert body["identical"] is False and body["diff"] == ["F-3312: exposure_usd 27518.40 != 27518.00"]
    assert body["reason"] == "the replayed run differs from the original"


# =========================================================================== 6. tiers
def rule_results(api, rid_map=True):
    return {r["rule_id"]: r for r in api.get("/api/status", params={"as_of": "2026-09-04"}).json_body["rules"]}


def test_fast_run_reports_out_of_tier_rules_as_not_evaluated_never_consistent(api):
    api.run("fast")
    rules = rule_results(api)
    for rid in ("L-01", "L-02", "L-03", "L-11", "C-01", "C-02", "C-03"):  # pay_period and close rules
        assert rules[rid]["result"] == "Not evaluated", rid
        assert rules[rid]["reason"]
    assert "not part of the fast run" in rules["L-02"]["reason"]
    # L-08 IS a fast-tier rule (timekeeping only), so it was in scope; `mini` has no classification history,
    # so it is Not evaluated for THAT reason, not because of the tier.
    assert rules["L-08"]["result"] == "Not evaluated" and "classification history" in rules["L-08"]["reason"].lower()
    assert "not part of the fast run" not in rules["L-08"]["reason"]
    assert rules["L-08"]["domain"] == "dcaa_cost_accounting" and rules["L-05"]["domain"] == "labor"
    assert all(r["result"] != "Consistent" for r in rules.values()
               if r["rule_id"] in ("L-01", "L-02", "L-03", "L-11", "C-01", "C-02", "C-03"))
    assert {rules[r]["result"] for r in ("L-05", "L-06", "L-09")} <= {"Consistent", "Watch", "Exception"}
    rd = api.get("/api/runs/RUN-2026-08-MER-001").json_body
    assert {r["rule_id"]: r["result"] for r in rd["rules"]}["L-03"] == "Not evaluated"
    assert api.get("/api/rules/L-03").json_body["last_result"] == "Not evaluated"
    # Coverage is measured against EVERY applicable coverage rule of the ENABLED domains (design 7.2), so a
    # fast-only period has checked 3 of 11, not "3 of 3". The 11 are L-01, L-02, L-03, L-05, L-06, L-09 (labor)
    # and L-08, L-11, C-01, C-02, C-03 (DCAA cost accounting); DQ-01 is a data-quality gate and does not count. The 3 evaluated
    # are L-05, L-06, L-09: L-08 is in the fast tier but has no history to read in `mini`. Tier-scoped
    # progress lives in tiers[], not in the overall coverage figure.
    st = api.get("/api/status").json_body
    cov = st["coverage"]
    assert (cov["evaluated"], cov["applicable"]) == (3, 11)
    dom = {d["id"]: d for d in st["domains"]}
    assert (dom["labor"]["coverage"]["evaluated"], dom["labor"]["coverage"]["applicable"]) == (3, 6)
    assert (dom["dcaa_cost_accounting"]["coverage"]["evaluated"], dom["dcaa_cost_accounting"]["coverage"]["applicable"]) == (0, 5)
    assert st["label_kind"] == "incomplete_data"


def test_close_run_after_fast_combines_results_per_rule(api, clock):
    api.run("fast")
    clock.advance(hours=1)
    api.run("close")
    rules = rule_results(api)
    assert rules["L-02"]["result"] == "Exception" and rules["L-11"]["result"] == "Not evaluated"
    st = api.get("/api/status").json_body
    cov = st["coverage"]
    # 6 of 11: the six labor rules evaluated; `mini` gives the five DCAA rules none of their data (no history for
    # L-08, no rate file for L-11/C-02/C-03, no allowability table for C-01). 6/11 = 0.5455, far below the 0.85 floor.
    assert (cov["evaluated"], cov["applicable"], cov["ratio"], cov["floor"]) == (6, 11, "0.5455", "0.85")
    assert rules["L-08"]["result"] == "Not evaluated"
    dom = {d["id"]: d for d in st["domains"]}
    assert dom["labor"]["coverage"] == {"evaluated": 6, "applicable": 6, "ratio": "1.0000", "floor": "0.85"}
    assert dom["dcaa_cost_accounting"]["coverage"] == {"evaluated": 0, "applicable": 5, "ratio": "0.0000", "floor": "0.85"}
    # and a later fast run does not erase the close-tier results (most recent run that EVALUATED the rule)
    clock.advance(hours=1)
    api.run("fast")
    rules = rule_results(api)
    assert rules["L-02"]["result"] == "Exception" and rules["L-03"]["result"] == "Exception"


def test_tier_state_rule_from_the_api_contract(api, clock):
    api.run("fast")  # 2026-09-03; fast cadence weekly -> due 2026-09-10
    t = {x["tier"]: x for x in api.get("/api/status", params={"as_of": "2026-09-10"}).json_body["tiers"]}
    assert [x for x in t] == ["fast", "pay_period", "close"]
    assert t["fast"]["state"] == "current" and t["fast"]["next_due"] == "2026-09-10"  # overdue only if as_of > next_due
    assert t["fast"]["last_run_id"] == "RUN-2026-08-MER-001" and t["fast"]["last_run_at"] == "2026-09-03T14:22:07Z"
    # fast now holds four coverage rules of enabled domains: L-05, L-06, L-09 and L-08. Three evaluated: `mini`
    # has no classification history, so L-08 is Not evaluated.
    assert (t["fast"]["rules_in_tier"], t["fast"]["rules_evaluated"]) == (4, 3)
    assert t["fast"]["cadence"] == "weekly" and t["fast"]["label"] == "Fast"
    for never in ("pay_period", "close"):
        assert t[never]["state"] == "never_run" and t[never]["last_run_id"] is None and t[never]["next_due"] is None
    assert (t["pay_period"]["rules_in_tier"], t["pay_period"]["rules_evaluated"]) == (2, 0)

    t = {x["tier"]: x for x in api.get("/api/status", params={"as_of": "2026-09-11"}).json_body["tiers"]}
    assert t["fast"]["state"] == "overdue"
    assert t["fast"]["note"] == (
        "Declared weekly; last run 2026-09-03, so the next run was due 2026-09-10. "
        "L-08 not evaluated: classification history from prior periods was not available")

    api.run("close")  # a close run also covers the fast and pay-period tiers
    t = {x["tier"]: x for x in api.get("/api/status", params={"as_of": "2026-09-18"}).json_body["tiers"]}
    assert t["fast"]["state"] == "overdue" and t["fast"]["next_due"] == "2026-09-10"  # both runs happened on 09-03
    assert t["pay_period"]["next_due"] == "2026-09-18" and t["pay_period"]["state"] == "current"  # semi_monthly = 15 days
    assert t["close"]["next_due"] == "2026-10-03" and t["close"]["state"] == "current"  # monthly = 30 days
    # The close tier now holds five coverage rules (L-03, L-11, C-01, C-02, C-03). On `mini` L-03 evaluates and the
    # other four cannot: each is named with its own reason, in rule order.
    assert t["close"]["note"] == (
        "C-01 not evaluated: no confirmed account allowability table was provided. "
        "C-02 not evaluated: provisional billing rate data was not uploaded. "
        "C-03 not evaluated: provisional billing rate data was not uploaded. "
        "L-11 not evaluated: provisional billing rate data was not uploaded")
    assert (t["close"]["rules_in_tier"], t["close"]["rules_evaluated"]) == (5, 1)
    t = {x["tier"]: x for x in api.get("/api/status", params={"as_of": "2026-09-19"}).json_body["tiers"]}
    assert t["pay_period"]["state"] == "overdue" and t["close"]["state"] == "current"
    t = {x["tier"]: x for x in api.get("/api/status", params={"as_of": "2026-10-04"}).json_body["tiers"]}
    assert t["close"]["state"] == "overdue"


def test_as_of_defaults_to_the_injected_clock_and_drives_source_age(api, clock):
    api.run("close")
    s = api.get("/api/status").json_body
    assert s["as_of"] == "2026-09-03"
    o = s["oldest_source"]
    assert (o["source"], o["file"], o["data_as_of"]) == ("hris", "bamboo_roster_2026-08-01.csv", "2026-08-01")
    assert o["age_days"] == 33
    assert api.get("/api/status", params={"as_of": "2026-09-20"}).json_body["oldest_source"]["age_days"] == 50


def test_status_before_any_run_is_incomplete_data(api):
    s = api.get("/api/status").json_body
    assert s["label"] == "Incomplete data" and s["label_kind"] == "incomplete_data"
    assert s["open_findings"] == 0 and s["total_exposure_usd"] == "0.00" and s["last_run_id"] is None
    assert s["oldest_source"] is None and {t["state"] for t in s["tiers"]} == {"never_run"}
    assert s["coverage"]["evaluated"] == 0 and s["coverage"]["applicable"] == 11
    assert all(r["result"] == "Not evaluated" for r in s["rules"])
    # no run yet: the enabled domains come from the ACTIVE CONFIG (both, in the shipped file)
    doms = {d["id"]: d for d in s["domains"]}
    assert doms["labor"]["enabled"] and doms["dcaa_cost_accounting"]["enabled"]
    assert doms["labor"]["state"] == doms["dcaa_cost_accounting"]["state"] == "Incomplete data"
    assert [r["rule_id"] for r in s["rules"]] == [
        "C-01", "C-02", "C-03", "L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "DQ-01"]


# =========================================================================== 7. exposure and labels
def engine_open_findings(exclude=()):
    """Independent path: run the engine directly and keep the findings not in `exclude`."""
    out = execute_run(MINI, CONFIG_TEXT, PERIOD, Tier.CLOSE, "RUN-X", "2026-09-03T14:22:07Z")
    return out, [f for f in out.findings if f.fingerprint not in exclude]


def test_status_exposure_equals_engine_total_exposure_over_open_findings(api):
    created = api.run("close")
    out, _ = engine_open_findings()
    s = api.get("/api/status").json_body
    assert s["total_exposure_usd"] == str(total_exposure(out.findings)) == str(out.total_exposure_usd) == created["total_exposure_usd"]
    # `mini` has no rate file and no classification history, so DCAA cost accounting is enabled but has 0 of its 2
    # rules evaluated: below the floor. "Incomplete data" takes precedence over the 9 open labor findings.
    assert s["open_findings"] == 9 and s["label"] == "Incomplete data" and s["label_kind"] == "incomplete_data"
    assert sum(s["by_severity"].values()) == 9
    doms = {d["id"]: d for d in s["domains"]}
    assert list(doms) == ["labor", "dcaa_cost_accounting", "cmmc_evidence", "proposals"]
    assert {i: (d["enabled"], d["available"]) for i, d in doms.items()} == {
        "labor": (True, True), "dcaa_cost_accounting": (True, True),
        "cmmc_evidence": (False, False), "proposals": (False, False)}
    assert doms["labor"]["state"] == "9 open findings" and doms["labor"]["open_findings"] == 9
    assert doms["dcaa_cost_accounting"]["state"] == "Incomplete data" and doms["dcaa_cost_accounting"]["open_findings"] == 0
    assert all(doms[i]["state"] == "coming later" and doms[i]["coverage"] is None for i in ("cmmc_evidence", "proposals"))

    # the entry-priced L-06 / L-09 / L-05 findings share time entry TE-000003: M13 counts its dollars once
    by_fp = {f.fingerprint: f for f in out.findings}
    shared = [f for f in out.findings if "TE-000003" in f.exposure_entry_ids]
    assert len(shared) >= 2
    assert Decimal(s["total_exposure_usd"]) < sum((f.exposure_usd for f in out.findings), Decimal(0))

    # A legit_exception leaves the open set; the total is the engine's M13 over what remains.
    fid_by_fp = {}
    for i in api.findings():
        fid_by_fp[api.get(f"/api/findings/{i['finding_id']}").json_body["fingerprint"]] = i["finding_id"]

    def except_it(fp):
        fid = fid_by_fp[fp]
        api.dispose(fid, "in_review")
        r = api.dispose(fid, "legit_exception", reason_code="approved_exception", note="Approved by the contracting officer")
        assert r.status_code == 200

    # (a) L-09 shares TE-000003 with the open L-06 and L-05 findings, so those dollars stay counted:
    #     the count drops, the de-duplicated total does not.
    except_it("L-09|C-7302")
    _, remaining = engine_open_findings(exclude={"L-09|C-7302"})
    s2 = api.get("/api/status").json_body
    assert s2["total_exposure_usd"] == str(total_exposure(remaining)) == s["total_exposure_usd"]
    assert s2["open_findings"] == 8 == len(remaining)
    assert {d["id"]: d["state"] for d in s2["domains"]}["labor"] == "8 open findings"  # the labor tile counts down
    assert s2["label"] == "Incomplete data"  # ... while the thin DCAA domain still takes precedence overall
    assert sum(s2["by_severity"].values()) == 8

    # (b) L-03 is priced on its own (dollar difference), so excepting it lowers the total by its exposure.
    l03 = by_fp["L-03|2026-08"].exposure_usd
    except_it("L-03|2026-08")
    _, remaining = engine_open_findings(exclude={"L-09|C-7302", "L-03|2026-08"})
    s3 = api.get("/api/status").json_body
    assert s3["total_exposure_usd"] == str(total_exposure(remaining))
    assert Decimal(s3["total_exposure_usd"]) == Decimal(s["total_exposure_usd"]) - l03
    assert s3["open_findings"] == 7 == len(remaining)


def test_every_status_counts_the_right_states_as_open(api):
    api.run("close")
    ids = [i["finding_id"] for i in api.findings()]
    api.dispose(ids[0], "in_review")  # open state
    api.dispose(ids[1], "in_review")
    api.dispose(ids[1], "confirmed")  # open state
    api.dispose(ids[2], "in_review")
    api.dispose(ids[2], "data_error")  # open state
    assert api.get("/api/status").json_body["open_findings"] == 9
    api.dispose(ids[1], "remediated")  # not open
    api.dispose(ids[2], "remediated")
    api.dispose(ids[3], "in_review")
    api.dispose(ids[3], "legit_exception", reason_code="policy_change", note="Policy updated")
    assert api.get("/api/status").json_body["open_findings"] == 6
    api.dispose(ids[1], "closed")
    assert api.get("/api/status").json_body["open_findings"] == 6


def _except_all(api) -> None:
    for fid in [i["finding_id"] for i in api.findings()]:
        assert api.dispose(fid, "in_review").status_code == 200
        r = api.dispose(fid, "legit_exception", reason_code="approved_exception", note="Approved")
        assert r.status_code == 200, r.text


def test_label_is_no_open_findings_when_everything_is_dispositioned_and_coverage_is_met(tmp_path, monkeypatch, clock):
    """`mini` cannot reach the floor any more (its DCAA domain has no data: 0 of 5), so the scenario runs on a
    scratch copy of `mini` plus the DCAA overlay (rate file with ceilings, account categories, allowability table,
    filing schedule) and a three-period classification history for E-0001. Then every one of the 11 coverage rules
    evaluates (11 of 11), and the label depends only on the open findings."""
    api = make_api(tmp_path, monkeypatch, data_dir=full_coverage_data(tmp_path / "full"), clock=clock)
    api.run("close")
    s = api.get("/api/status").json_body
    assert (s["coverage"]["evaluated"], s["coverage"]["applicable"], s["coverage"]["ratio"]) == (11, 11, "1.0000")
    # 9 labor + the overhead-pool L-11 finding. The overlay's C-rules are deliberately clean (all-allowable table,
    # an on-time filing, ceilings above the provisional rates), so they add coverage and no findings.
    assert (s["label"], s["open_findings"]) == ("10 open findings", 10)
    _except_all(api)
    s = api.get("/api/status").json_body
    assert (s["label"], s["label_kind"], s["open_findings"], s["total_exposure_usd"]) == (
        "No open findings", "no_open_findings", 0, "0.00")
    assert {d["id"]: d["state"] for d in s["domains"] if d["enabled"]} == {
        "labor": "No open findings", "dcaa_cost_accounting": "No open findings"}


def test_a_thin_domain_keeps_incomplete_data_even_when_nothing_is_open(api):
    """`mini` alone: labor is fully covered (6 of 6) but DCAA cost accounting has no data (0 of 2)."""
    api.run("close")
    _except_all(api)
    s = api.get("/api/status").json_body
    assert (s["label"], s["label_kind"], s["open_findings"]) == ("Incomplete data", "incomplete_data", 0)
    doms = {d["id"]: d for d in s["domains"]}
    assert doms["labor"]["state"] == "No open findings" and doms["labor"]["label_kind"] == "no_open_findings"
    assert doms["dcaa_cost_accounting"]["state"] == "Incomplete data"


# =========================================================================== persistence across app restarts
def test_fast_only_period_never_reads_as_clean_even_when_every_finding_is_dispositioned(api):
    """Design 7.2: a fast-tier run that finds nothing must not render the same as a full run that
    finds nothing. Coverage is 3 of 7 here, below the floor, so 'Incomplete data' takes precedence."""
    api.run("fast")
    for fid in [i["finding_id"] for i in api.findings()]:
        api.dispose(fid, "in_review")
        api.dispose(fid, "legit_exception", reason_code="approved_exception", note="Approved")
    s = api.get("/api/status").json_body
    assert (s["label"], s["label_kind"]) == ("Incomplete data", "incomplete_data")
    assert s["open_findings"] == 0  # the count is still reported; only the label changes


def test_state_survives_an_app_restart_and_the_seed_is_not_repeated(tmp_path, monkeypatch, clock):
    a = make_api(tmp_path, monkeypatch, clock=clock)
    a.run("close")
    a.dispose("F-3311", "in_review", note="Assigned")
    b = make_api(tmp_path, monkeypatch, clock=clock)  # a new app on the same database file
    assert b.status_of("F-3311") == "in_review"
    assert [h["config_version"] for h in b.get("/api/config").json_body["history"]] == [7]
    assert b.get("/api/runs").json_body["items"][0]["run_id"] == "RUN-2026-08-MER-001"
    assert b.run("close")["run_id"] == "RUN-2026-08-MER-002"


def test_stored_money_is_text_never_a_float(tmp_path, monkeypatch, clock):
    a = make_api(tmp_path, monkeypatch, clock=clock)
    a.run("close")
    import sqlite3

    con = sqlite3.connect(tmp_path / "t.db")
    for table in ("runs", "findings", "config_versions", "finding_history", "run_rules"):
        for row in con.execute(f"SELECT * FROM {table}"):
            assert not any(isinstance(v, float) for v in row), table
    con.close()


# =========================================================================== 8. domains (DCAA cost accounting)
REAL = ROOT / "data" / "out"
needs_real = pytest.mark.skipif(not (REAL / "sources.json").exists(), reason="data/out not generated")
LABOR_ONLY_CONFIG = CONFIG_TEXT.replace("  - dcaa_cost_accounting\n", "")
ENABLED = ("labor", "dcaa_cost_accounting")


def domains_of(status_body) -> dict:
    return {d["id"]: d for d in status_body["domains"]}


def cov(evaluated, applicable, ratio):
    return {"evaluated": evaluated, "applicable": applicable, "ratio": ratio, "floor": "0.85"}


def fid_of(api, rule_id, exposure=None):
    """The id of the one finding for `rule_id` (optionally with this exposure). Ids are sequential in the engine's
    order, which moves whenever a rule is added, so tests look findings up by rule and never hardcode F-33xx."""
    items = [i for i in api.findings() if i["rule_id"] == rule_id and (exposure is None or i["exposure_usd"] == exposure)]
    assert len(items) == 1, items
    return items[0]["finding_id"]


@pytest.fixture()
def real_api(tmp_path, monkeypatch, clock):
    """The generated Meridian dataset (data/out), copied so a test can change it."""
    shutil.copytree(REAL, tmp_path / "real")
    return make_api(tmp_path, monkeypatch, data_dir=tmp_path / "real", clock=clock)


@pytest.fixture()
def dcaa_api(tmp_path, monkeypatch, clock):
    """`mini` + the DCAA overlay + classification history: all 11 coverage rules in play, one L-11 finding
    (the overhead pool), and no dependency on data/out."""
    return make_api(tmp_path, monkeypatch, data_dir=full_coverage_data(tmp_path / "dcaa"), clock=clock)


def test_the_computed_block_is_flattened_to_strings():
    from api.service import _api_computed

    flat = _api_computed({
        "text": "x", "none": None, "yes": True, "no": False, "count": 4, "empty_list": [],
        "scalars": ["a", 1, None, True], "nested": {"a": {"b": "c"}}, "entry_costs": {"TE-1": "1.00"},
        "history": [{"period": "2026-05", "rate": "0.1040", "m10": "0.0400", "skip": None, "deep": {"x": "y"}},
                    {"period": "2026-06", "rate": "0.1055", "m10": "0.0550", "skip": None, "deep": {}}],
    })
    assert flat == {
        "text": "x", "none": "", "yes": "true", "no": "false", "count": "4", "empty_list": "",
        "scalars": "a; 1; ; true",
        "history": "period=2026-05, rate=0.1040, m10=0.0400, skip=; period=2026-06, rate=0.1055, m10=0.0550, skip=",
    }  # a nested mapping (incl. entry_costs) is omitted; nothing is null, a float or structured
    assert all(isinstance(v, str) for v in flat.values())


def test_dcaa_responses_match_the_dcaa_samples(dcaa_api):
    """The samples show the DCAA shape (a rate_data input, reference tables, an L-11 finding, domain tiles).
    `mini` + overlay produces every one of those with real engine output."""
    created = dcaa_api.run("close")
    check(created, "run_created")
    check(dcaa_api.get("/api/status").json_body, "status")
    l11 = next(i for i in dcaa_api.findings() if i["rule_id"] == "L-11")
    check(dcaa_api.get("/api/findings").json_body, "findings_list")
    detail = dcaa_api.get(f"/api/findings/{l11['finding_id']}").json_body
    check(detail, "finding_detail_l11")
    assert (detail["domain"], detail["domain_name"]) == ("dcaa_cost_accounting", "DCAA cost accounting")
    # `mini` has no earlier M10 history, so the list is empty and reads as the empty string (never null)
    assert detail["computed"]["history"] == "" and all(isinstance(v, str) for v in detail["computed"].values())
    assert (detail["computed"]["pool"], detail["computed"]["base_definition"]) == ("overhead", "direct_labor")
    check(dcaa_api.get("/api/rules").json_body, "rules_list")
    check(dcaa_api.get("/api/rules/L-08").json_body, "rule_detail")
    check(dcaa_api.get("/api/rules/L-11").json_body, "rule_detail")
    run = dcaa_api.get(f"/api/runs/{created['run_id']}").json_body
    check(run, "run_detail")
    m = run["manifest"]
    assert m["enabled_domains"] == ["labor", "dcaa_cost_accounting"]
    assert "rate_data" in [i["source"] for i in m["inputs"]]  # the rate input, as the engine produced it
    refs = {t["name"]: t for t in m["reference_tables"]}
    assert {"account_categories", "classification_history"} <= set(refs)
    assert all(t["sha256"] and t["source"] == t["name"] for t in refs.values())
    assert {r["rule_id"]: r["domain"] for r in run["rules"]}["L-11"] == "dcaa_cost_accounting"
    check(dcaa_api.post(f"/api/runs/{created['run_id']}/replay").json_body, "replay_result")
    # A disposition returns the full detail, domain fields included
    assert dcaa_api.dispose(l11["finding_id"], "in_review").json_body["domain"] == "dcaa_cost_accounting"


def test_domain_filter_on_findings_and_rules(dcaa_api):
    dcaa_api.run("close")
    every = dcaa_api.findings()
    labor = dcaa_api.findings(domain="labor")
    dcaa = dcaa_api.findings(domain="dcaa_cost_accounting")
    assert len(every) == 10 and len(labor) == 9 and [i["rule_id"] for i in dcaa] == ["L-11"]
    assert {i["domain"] for i in labor} == {"labor"} and {i["domain_name"] for i in labor} == {"Labor"}
    assert (dcaa[0]["domain"], dcaa[0]["domain_name"]) == ("dcaa_cost_accounting", "DCAA cost accounting")
    assert {i["finding_id"] for i in labor + dcaa} == {i["finding_id"] for i in every}  # the two domains partition it
    # filters combine; a placeholder domain is a known id with nothing in it
    assert dcaa_api.findings(domain="dcaa_cost_accounting", rule="L-02") == []
    assert dcaa_api.findings(domain="dcaa_cost_accounting", severity="high") == dcaa or dcaa[0]["severity"] != "high"
    assert dcaa_api.get("/api/findings", params={"domain": "cmmc_evidence"}).json_body == {"total": 0, "items": []}
    bad = dcaa_api.get("/api/findings", params={"domain": "payroll"})
    assert bad.status_code == 400 and bad.json_body["error"]["code"] == "bad_filter"
    assert dcaa_api.get("/api/findings", params={"domain": "payroll", "severity": "urgent"}).status_code == 400

    rules = lambda **p: [i["id"] for i in dcaa_api.get("/api/rules", params=p).json_body["items"]]  # noqa: E731
    assert rules(domain="labor") == ["L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01"]
    assert rules(domain="dcaa_cost_accounting") == ["C-01", "C-02", "C-03", "L-08", "L-11"]
    assert rules(domain="proposals") == [] and len(rules()) == 12
    bad = dcaa_api.get("/api/rules", params={"domain": "payroll"})
    assert bad.status_code == 400 and bad.json_body["error"]["code"] == "bad_filter"
    detail = dcaa_api.get("/api/rules/L-08").json_body
    assert (detail["domain"], detail["domain_name"]) == ("dcaa_cost_accounting", "DCAA cost accounting")
    assert dcaa_api.get("/api/rules/DQ-01").json_body["domain"] == "labor"  # a data-quality gate belongs to labor


@needs_real
def test_status_domains_on_the_real_dataset(real_api):
    created = real_api.run("close")
    assert (created["findings"], created["total_exposure_usd"], created["label"]) == (14, "102491.51", "14 open findings")
    s = real_api.get("/api/status").json_body
    assert (s["label"], s["label_kind"], s["open_findings"], s["total_exposure_usd"]) == (
        "14 open findings", "open_findings", 14, "102491.51")
    assert s["coverage"] == cov(11, 11, "1.0000") and s["by_severity"] == {"high": 8, "medium": 5, "low": 1}
    doms = domains_of(s)
    labor, dcaa = doms["labor"], doms["dcaa_cost_accounting"]
    assert (labor["name"], labor["enabled"], labor["available"]) == ("Labor", True, True)
    assert (labor["state"], labor["label_kind"], labor["open_findings"]) == ("8 open findings", "open_findings", 8)
    assert labor["by_severity"] == {"high": 3, "medium": 4, "low": 1} and labor["coverage"] == cov(6, 6, "1.0000")
    assert (dcaa["name"], dcaa["enabled"], dcaa["available"]) == ("DCAA cost accounting", True, True)
    # L-08, L-11, C-01 (G&A and overhead pools), C-02, C-03: five high and one medium (the small overhead C-01)
    assert (dcaa["state"], dcaa["label_kind"], dcaa["open_findings"]) == ("6 open findings", "open_findings", 6)
    assert dcaa["by_severity"] == {"high": 5, "medium": 1, "low": 0} and dcaa["coverage"] == cov(5, 5, "1.0000")
    for pid, name in (("cmmc_evidence", "CMMC evidence"), ("proposals", "Proposals")):
        assert doms[pid] == {
            "id": pid, "name": name, "enabled": False, "available": False, "state": "coming later",
            "label_kind": "unavailable", "open_findings": 0, "by_severity": {"high": 0, "medium": 0, "low": 0},
            "coverage": None}
    # tiers count coverage rules of the enabled domains: fast holds L-05, L-06, L-08, L-09; close holds L-03, L-11 and
    # the three C-rules (GL and/or rate data)
    t = {x["tier"]: (x["rules_in_tier"], x["rules_evaluated"]) for x in s["tiers"]}
    assert t == {"fast": (4, 4), "pay_period": (2, 2), "close": (5, 5)}
    assert {r["rule_id"]: r["domain"] for r in s["rules"]}["L-08"] == "dcaa_cost_accounting"
    assert [r["rule_id"] for r in s["rules"]] == [
        "C-01", "C-02", "C-03", "L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "DQ-01"]
    assert {r["result"] for r in s["rules"] if r["rule_id"] in ("L-08", "L-11", "C-01", "C-02", "C-03")} == {"Exception"}

    # the six DCAA findings, by domain filter, in the engine's order: severity, then rule id, then exposure
    items = real_api.findings(domain="dcaa_cost_accounting")
    assert [(i["rule_id"], i["severity"], i["exposure_usd"]) for i in items] == [
        ("C-01", "high", "23550.00"), ("C-02", "high", "0.00"), ("C-03", "high", "8663.24"),
        ("L-08", "high", "3652.00"), ("L-11", "high", "23477.34"), ("C-01", "medium", "1850.00")]
    l11 = next(i for i in items if i["rule_id"] == "L-11")
    assert l11["headline"] == "G&A rate is 8.0% above its provisional billing rate (10.80% actual vs 10.00%)"
    assert len(real_api.findings(domain="labor")) == 8
    # exposure across the two domains is the engine's M13, not a sum the API made up
    out = execute_run(REAL, CONFIG_TEXT, PERIOD, Tier.CLOSE, "RUN-X", "2026-09-03T14:22:07Z")
    assert s["total_exposure_usd"] == str(total_exposure(out.findings))


@needs_real
def test_dcaa_finding_details_carry_flat_string_computed_values(real_api):
    real_api.run("close")
    l11_id = fid_of(real_api, "L-11")
    d = real_api.get(f"/api/findings/{l11_id}").json_body
    check(d, "finding_detail_l11")
    assert (d["rule_id"], d["domain"], d["domain_name"]) == ("L-11", "dcaa_cost_accounting", "DCAA cost accounting")
    assert d["metric"]["id"] == "M10" and d["exposure_basis"] == "rate_true_up"
    c = d["computed"]
    assert all(isinstance(v, str) for v in c.values())  # Record<string,string>, no null, list or object
    assert (c["pool"], c["actual_rate"], c["provisional_rate"], c["direction"]) == ("ga", "0.1080", "0.1000", "under_billed")
    assert c["systemic"] == "true"  # a bool becomes text
    assert c["skipped_pools"] == ""  # an empty list becomes the empty string
    # a list of mappings: one "k=v, k=v" item per period, joined with "; ", in the engine's key order
    hist = c["history"].split("; ")
    assert len(hist) == 6 and hist[0] == "period=2026-02, rate=0.1008, m10=0.0080"
    assert hist[-1] == "period=2026-07, rate=0.1068, m10=0.0680"
    assert d["affected"] == {"employees": [], "contracts": [], "entries": 0}
    # evidence = every indirect G&A line in the GL (labor lines, the plug lines and the carve-outs) plus the agreement
    import csv as _csv

    ga_lines = sum(1 for r in _csv.DictReader(open(REAL / "qb_gl_2026-08.csv")) if r["Cost Type"] == "indirect" and r["Pool"] == "ga")
    assert d["evidence_count"] == ga_lines + 1 == real_api.get(f"/api/findings/{l11_id}/evidence").json_body["total"]
    kinds = {e["kind"] for e in real_api.get(f"/api/findings/{l11_id}/evidence", params={"page_size": 100}).json_body["items"]}
    assert kinds == {"gl_line", "rate_agreement"}
    l08 = real_api.get(f"/api/findings/{fid_of(real_api, 'L-08')}").json_body
    assert (l08["domain"], l08["metric"]["id"]) == ("dcaa_cost_accounting", "M9")
    assert l08["affected"] == {"employees": ["E-0143"], "contracts": [], "entries": 6}
    assert all(isinstance(v, str) for v in l08["computed"].values())

    # the three C-rule findings: flat strings, the right metric, and the figures the answer key records
    c01 = real_api.get(f"/api/findings/{fid_of(real_api, 'C-01', '23550.00')}").json_body
    check(c01, "finding_detail_l11")
    assert (c01["domain"], c01["metric"]["id"], c01["exposure_basis"], c01["severity"]) == (
        "dcaa_cost_accounting", "M15", "unallowable_amount", "high")
    assert all(isinstance(v, str) for v in c01["computed"].values())
    assert c01["computed"]["pool"] == "ga" and c01["computed"]["account_count"] == "3"
    assert "6535 Entertainment and Events: $14,800.00 (FAR 31.205-14)" in c01["computed"]["accounts"]  # "; "-joined list
    c01_kinds = {e["kind"] for e in real_api.get(f"/api/findings/{c01['finding_id']}/evidence").json_body["items"]}
    assert c01_kinds == {"gl_line"} and c01["evidence_count"] == 3  # the three unallowable lines, and nothing else
    c02 = real_api.get(f"/api/findings/{fid_of(real_api, 'C-02')}").json_body
    assert (c02["metric"]["id"], c02["exposure_usd"], c02["computed"]["days_overdue"], c02["computed"]["due_date"]) == (
        "M16", "0.00", "62", "2026-06-30")
    assert all(isinstance(v, str) for v in c02["computed"].values())
    c03 = real_api.get(f"/api/findings/{fid_of(real_api, 'C-03')}").json_body
    assert (c03["metric"]["id"], c03["exposure_usd"], c03["computed"]["pool"]) == ("M17", "8663.24", "overhead")
    assert c03["computed"]["actual_above_ceiling"] == "true"  # a bool becomes text


@needs_real
def test_a_labor_only_config_leaves_the_dcaa_domain_out_of_the_run(real_api):
    assert LABOR_ONLY_CONFIG != CONFIG_TEXT and "dcaa_cost_accounting" not in LABOR_ONLY_CONFIG
    put = real_api.put("/api/config", json={"yaml": LABOR_ONLY_CONFIG})
    assert put.status_code == 200 and put.json_body["accepted"] is True and put.json_body["config_version"] == 8
    # before any run the tiles follow the active config
    doms = domains_of(real_api.get("/api/status").json_body)
    assert doms["labor"]["enabled"] and not doms["dcaa_cost_accounting"]["enabled"]

    created = real_api.run("close")
    assert created["findings"] == 8 and created["total_exposure_usd"] == "41298.93"
    run = real_api.get(f"/api/runs/{created['run_id']}").json_body
    assert run["manifest"]["enabled_domains"] == ["labor"]
    # a domain that was not enabled is not run: its rules are absent, not "Not evaluated"
    assert {r["rule_id"] for r in run["rules"]} == {"L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01"}
    assert {"L-08", "L-11", "C-01", "C-02", "C-03"}.isdisjoint(run["manifest"]["rule_versions"])

    s = real_api.get("/api/status").json_body
    assert (s["label"], s["label_kind"], s["open_findings"], s["total_exposure_usd"]) == (
        "8 open findings", "open_findings", 8, "41298.93")
    assert s["coverage"] == cov(6, 6, "1.0000")
    doms = domains_of(s)
    assert doms["dcaa_cost_accounting"] == {
        "id": "dcaa_cost_accounting", "name": "DCAA cost accounting", "enabled": False, "available": True,
        "state": "Not enabled", "label_kind": "not_enabled", "open_findings": 0,
        "by_severity": {"high": 0, "medium": 0, "low": 0}, "coverage": None}
    assert doms["labor"]["state"] == "8 open findings" and doms["labor"]["coverage"] == cov(6, 6, "1.0000")
    assert {r["rule_id"] for r in s["rules"]} == {"L-01", "L-02", "L-03", "L-05", "L-06", "L-09", "DQ-01"}
    t = {x["tier"]: (x["rules_in_tier"], x["rules_evaluated"]) for x in s["tiers"]}
    assert t == {"fast": (3, 3), "pay_period": (2, 2), "close": (1, 1)}  # the five DCAA rules are in no tier
    assert real_api.findings(domain="dcaa_cost_accounting") == [] and len(real_api.findings()) == 8
    # the catalog still describes every registered rule, whatever the customer switched on
    assert len(real_api.get("/api/rules").json_body["items"]) == 12


@needs_real
def test_the_latest_run_decides_which_domains_are_enabled_not_the_config_alone(real_api, clock):
    real_api.run("close")
    l11_id = fid_of(real_api, "L-11")
    real_api.put("/api/config", json={"yaml": LABOR_ONLY_CONFIG})
    s = real_api.get("/api/status").json_body  # config changed, but no run has used it yet
    assert domains_of(s)["dcaa_cost_accounting"]["enabled"] and s["open_findings"] == 14
    clock.advance(hours=1)
    real_api.run("close")
    s = real_api.get("/api/status").json_body
    assert not domains_of(s)["dcaa_cost_accounting"]["enabled"]
    assert (s["open_findings"], s["total_exposure_usd"]) == (8, "41298.93")
    # the DCAA findings are kept (nothing is deleted), just outside this status
    assert real_api.status_of(l11_id) == "open" and real_api.get(f"/api/findings/{l11_id}").json_body["latest_run_id"] == "RUN-2026-08-MER-001"


@needs_real
def test_one_thin_domain_makes_the_overall_label_incomplete_data(tmp_path, monkeypatch, clock):
    """The rate file is declared absent in a scratch copy. Three DCAA rules need it (L-11, C-02, C-03), so DCAA coverage
    is 2 of 5 (L-08 needs timekeeping; C-01 needs the GL and the allowability table) and labor is still 6 of 6.
    Overall 8 of 11 = 0.7273: below the floor either way. The sharper case (overall ABOVE the floor while one
    domain is below it) is the next test."""
    d = tmp_path / "thin"
    shutil.copytree(REAL, d)
    src = json.loads((d / "sources.json").read_text())
    src["sources"].pop("rate_data", None)
    src["absent"] = sorted(set(src.get("absent", [])) | {"rate_data"})
    (d / "sources.json").write_text(json.dumps(src))
    api = make_api(tmp_path, monkeypatch, data_dir=d, clock=clock)
    api.run("close")
    s = api.get("/api/status").json_body
    assert s["coverage"] == cov(8, 11, "0.7273")
    assert (s["label"], s["label_kind"]) == ("Incomplete data", "incomplete_data")
    doms = domains_of(s)
    assert doms["labor"]["coverage"] == cov(6, 6, "1.0000") and doms["labor"]["state"] == "8 open findings"
    assert doms["dcaa_cost_accounting"]["coverage"] == cov(2, 5, "0.4000")
    assert (doms["dcaa_cost_accounting"]["state"], doms["dcaa_cost_accounting"]["label_kind"]) == (
        "Incomplete data", "incomplete_data")
    assert doms["dcaa_cost_accounting"]["open_findings"] == 3  # L-08 and the two C-01 findings are still counted
    rules = {r["rule_id"]: r for r in s["rules"]}
    for rid in ("L-11", "C-02", "C-03"):
        assert rules[rid]["result"] == "Not evaluated" and rules[rid]["reason"] == "Provisional billing rate data was not uploaded", rid
    assert rules["L-08"]["result"] == "Exception" and rules["C-01"]["result"] == "Exception"
    assert s["open_findings"] == 11  # 8 labor + L-08 + C-01 (G&A) + C-01 (overhead)
    # the exposure is still the engine's M13 over what IS open: labor 41298.93 + L-08 3652.00 + C-01 (23550.00 + 1850.00)
    out = execute_run(d, CONFIG_TEXT, PERIOD, Tier.CLOSE, "RUN-X", "2026-09-03T14:22:07Z")
    assert s["total_exposure_usd"] == str(total_exposure(out.findings)) == "70350.93"


@needs_real
def test_overall_above_the_floor_still_reads_incomplete_when_one_domain_is_below_it(tmp_path, monkeypatch, clock):
    """Only the account allowability table is missing: C-01 is Not evaluated. Overall that is 10 of 11 = 0.9091, ABOVE
    the 0.85 floor, so a summed ratio alone would pass; but DCAA is 4 of 5 = 0.8000, below it, so the label is
    'Incomplete data'. A healthy total must not hide a thin domain."""
    d = tmp_path / "noallow"
    shutil.copytree(REAL, d)
    (d / "account_allowability.csv").unlink()
    api = make_api(tmp_path, monkeypatch, data_dir=d, clock=clock)
    api.run("close")
    s = api.get("/api/status").json_body
    assert s["coverage"] == cov(10, 11, "0.9091") and Decimal(s["coverage"]["ratio"]) >= Decimal(s["coverage"]["floor"])
    assert (s["label"], s["label_kind"]) == ("Incomplete data", "incomplete_data")
    doms = domains_of(s)
    assert doms["dcaa_cost_accounting"]["coverage"] == cov(4, 5, "0.8000") and doms["dcaa_cost_accounting"]["state"] == "Incomplete data"
    assert doms["labor"]["state"] == "8 open findings"  # labor is unaffected
    rules = {r["rule_id"]: r for r in s["rules"]}
    assert rules["C-01"]["result"] == "Not evaluated" and "allowability" in rules["C-01"]["reason"].lower()
    assert s["open_findings"] == 12  # 14 less the two C-01 findings that could not be raised


@needs_real
def test_a_fast_run_on_the_real_dataset_never_reads_as_clean(real_api):
    real_api.run("fast")
    s = real_api.get("/api/status").json_body
    doms = domains_of(s)
    assert doms["labor"]["coverage"] == cov(3, 6, "0.5000")
    # only L-08 is a fast-tier DCAA rule; L-11, C-01, C-02 and C-03 all need the GL and/or the rate data
    assert doms["dcaa_cost_accounting"]["coverage"] == cov(1, 5, "0.2000")
    assert (s["label"], s["open_findings"]) == ("Incomplete data", 4)
    assert doms["dcaa_cost_accounting"]["open_findings"] == 1  # the L-08 finding was found by the fast run
    rules = {r["rule_id"]: r for r in s["rules"]}
    assert rules["L-08"]["result"] == "Exception"
    assert all(rules[r]["result"] == "Not evaluated" for r in ("L-11", "C-01", "C-02", "C-03"))


@needs_real
def test_dispositioning_a_dcaa_finding_reduces_exposure_by_exactly_its_amount(real_api):
    real_api.run("close")
    before = real_api.get("/api/status").json_body
    l11_id = fid_of(real_api, "L-11")
    assert real_api.dispose(l11_id, "in_review").status_code == 200
    r = real_api.dispose(l11_id, "legit_exception", reason_code="approved_exception", note="Approved by the contracting officer")
    assert r.status_code == 200 and r.json_body["status"] == "legit_exception"
    after = real_api.get("/api/status").json_body
    # L-11 is a rate true-up priced on its own (no time entries), so M13 removes exactly its 23477.34
    assert Decimal(before["total_exposure_usd"]) - Decimal(after["total_exposure_usd"]) == Decimal("23477.34")
    assert after["total_exposure_usd"] == "79014.17"  # 102491.51 - 23477.34
    assert (after["open_findings"], before["open_findings"]) == (13, 14) and after["label"] == "13 open findings"
    b, a = domains_of(before), domains_of(after)
    assert (b["dcaa_cost_accounting"]["open_findings"], a["dcaa_cost_accounting"]["open_findings"]) == (6, 5)
    assert a["dcaa_cost_accounting"]["state"] == "5 open findings"
    assert a["dcaa_cost_accounting"]["by_severity"] == {"high": 4, "medium": 1, "low": 0}
    assert a["labor"] == b["labor"]  # the other domain is untouched
    assert after["by_severity"] == {"high": 7, "medium": 5, "low": 1}
    out = execute_run(REAL, CONFIG_TEXT, PERIOD, Tier.CLOSE, "RUN-X", "2026-09-03T14:22:07Z")
    remaining = [f for f in out.findings if f.fingerprint != "L-11|ga|2026-08"]
    assert after["total_exposure_usd"] == str(total_exposure(remaining))
    # ... and excepting every other DCAA finding empties the DCAA tile: clean, with full coverage, labor untouched
    for i in real_api.findings(domain="dcaa_cost_accounting"):
        if i["status"] == "open":
            real_api.dispose(i["finding_id"], "in_review")
            real_api.dispose(i["finding_id"], "legit_exception", reason_code="approved_exception",
                             note="Approved by the contracting officer")
    last = real_api.get("/api/status").json_body
    assert domains_of(last)["dcaa_cost_accounting"]["state"] == "No open findings"
    assert domains_of(last)["dcaa_cost_accounting"]["coverage"] == cov(5, 5, "1.0000")
    assert last["total_exposure_usd"] == "41298.93"  # exactly the labor domain's exposure


def test_an_unknown_domain_in_a_config_is_rejected_with_its_line(api):
    text = CONFIG_TEXT.replace("  - dcaa_cost_accounting\n", "  - payroll\n")
    lines = text.splitlines()
    for r in (api.post("/api/config/validate", json={"yaml": text}), api.put("/api/config", json={"yaml": text})):
        body = r.json_body
        assert body["accepted"] is False and body["config_version"] is None
        err = next(e for e in body["errors"] if e["code"] == "unknown_domain")
        assert "payroll" in lines[err["line"] - 1] and "payroll" in err["message"]
    assert api.get("/api/config").json_body["config_version"] == 7


@needs_real
def test_every_string_in_every_dcaa_response_is_clean_and_no_float_appears(real_api):
    """The whole surface over the real dataset, both domains, errors included. Every response is parsed with a
    float trap (see `Api.call`); every string, key and value, must pass the copy linter."""
    real_api.run("close")
    real_api.run("fast")
    real_api.get("/api/status")
    real_api.get("/api/status", params={"as_of": "2026-10-30"})
    for i in real_api.get("/api/findings").json_body["items"]:
        d = real_api.get(f"/api/findings/{i['finding_id']}").json_body
        real_api.get(f"/api/findings/{i['finding_id']}/evidence", params={"page_size": 100})
        assert all(isinstance(v, str) for v in d["computed"].values()), i["finding_id"]
        assert isinstance(d["domain"], str) and isinstance(d["domain_name"], str)
    for dom in ("labor", "dcaa_cost_accounting", "cmmc_evidence", "proposals", "payroll"):
        real_api.get("/api/findings", params={"domain": dom})
        real_api.get("/api/rules", params={"domain": dom})
    for rid in ("L-01", "L-02", "L-03", "L-05", "L-06", "L-08", "L-09", "L-11", "DQ-01"):
        real_api.get(f"/api/rules/{rid}")
    real_api.dispose("F-3315", "in_review", note="Assigned for review")
    real_api.get("/api/config")
    real_api.post("/api/config/validate", json={"yaml": CONFIG_TEXT.replace("  - dcaa_cost_accounting\n", "  - payroll\n")})
    real_api.get("/api/runs")
    for run_id in ("RUN-2026-08-MER-001", "RUN-2026-08-MER-002"):
        real_api.get(f"/api/runs/{run_id}")
        real_api.post(f"/api/runs/{run_id}/replay")
    assert len(real_api.bodies) > 50
    offenders = [(where, s, find_restricted(s)) for where, body in real_api.bodies for s in walk_strings(body) if find_restricted(s)]
    assert offenders == []
