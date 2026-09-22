"""Regression: a fast-tier run must not make the status page forget what the last close run knew.

Found by opening the real UI against the real data, not by any sample-payload test: after a fast
run (which never loads payroll, HRIS or the GL) the status page
  1. dropped the 50-day-old HRIS roster from "oldest source", and
  2. explained L-11 as "not part of the fast run" instead of "rate data was not uploaded".
Both were the same mistake: treating the LATEST run as the whole picture.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.main import create_app

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "out"

pytestmark = pytest.mark.skipif(not (DATA / "sources.json").exists(), reason="data/out not generated")


class _Clock:
    def __call__(self) -> datetime:
        return datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def client(tmp_path):
    """Meridian WITHOUT the rate file. L-11 now has its data in the shipped dataset, so the regression this
    file guards (a Not evaluated rule keeps its REAL reason after a later fast run) needs a rule that is
    genuinely missing its source: the rate file is declared absent in a scratch copy."""
    import json
    import shutil

    d = tmp_path / "data"
    shutil.copytree(DATA, d)
    src = json.loads((d / "sources.json").read_text())
    src["sources"].pop("rate_data", None)
    src["absent"] = sorted(set(src.get("absent", [])) | {"rate_data"})
    (d / "sources.json").write_text(json.dumps(src))
    app = create_app(db_path=tmp_path / "t.db", data_dir=d, clock=_Clock())
    with TestClient(app) as c:
        yield c


def _run(c, tier):
    r = c.post("/api/runs", json={"period": "2026-08", "tier": tier})
    assert r.status_code in (200, 201), r.text


def _status(c):
    return c.get("/api/status", params={"as_of": "2026-09-20"}).json()


def test_oldest_source_survives_a_later_fast_run(client):
    _run(client, "close")
    before = _status(client)["oldest_source"]
    assert before["source"] == "hris" and before["age_days"] == 50

    _run(client, "fast")  # loads only timekeeping / edits / contracts
    after = _status(client)["oldest_source"]
    assert after == before, "a fast run must not hide the stale HRIS roster"


def test_not_evaluated_reason_is_the_real_one_after_a_fast_run(client):
    _run(client, "close")
    _run(client, "fast")
    s = _status(client)
    l11 = next(r for r in s["rules"] if r["rule_id"] == "L-11")
    assert l11["result"] == "Not evaluated"
    assert "not part of the fast run" not in l11["reason"]
    assert "not uploaded" in l11["reason"]
    close = next(t for t in s["tiers"] if t["tier"] == "close")
    assert "not part of the fast run" not in (close["note"] or "")


def test_fast_run_alone_never_reports_out_of_scope_rules_as_consistent(client):
    _run(client, "fast")
    s = _status(client)
    by = {r["rule_id"]: r["result"] for r in s["rules"]}
    for rid in ("L-01", "L-02", "L-03", "L-11"):
        assert by[rid] == "Not evaluated", rid
    # With nothing but a fast run, the honest coverage is below the floor
    assert s["coverage"]["evaluated"] < s["coverage"]["applicable"]
