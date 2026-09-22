"""SQLite persistence for the Sadhik API (stdlib sqlite3 only).

One short-lived connection per operation (WAL, foreign keys on). Everything the API
serves is either stored here verbatim from the engine (manifest, canonical string,
finding JSON, evidence) or is a lookup over these rows. No figure is computed here.

Money and hours are stored as TEXT (the engine's Decimal rendered as a string); there
is no REAL column anywhere.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS config_versions (
    version        INTEGER PRIMARY KEY,
    yaml           TEXT NOT NULL,
    sha256         TEXT NOT NULL,
    changed_by     TEXT NOT NULL,
    approved_by    TEXT,
    reason         TEXT NOT NULL,
    effective_from TEXT NOT NULL,
    created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    seq                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id              TEXT NOT NULL UNIQUE,
    period              TEXT NOT NULL,
    tier                TEXT NOT NULL,
    executed_at         TEXT NOT NULL,
    manifest_json       TEXT NOT NULL,
    canonical           TEXT NOT NULL,
    label               TEXT NOT NULL,
    total_exposure_usd  TEXT NOT NULL,
    config_version      INTEGER NOT NULL REFERENCES config_versions(version),
    findings_count      INTEGER NOT NULL,
    new_findings        INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS runs_period ON runs(period);

CREATE TABLE IF NOT EXISTS run_rules (
    run_id       TEXT NOT NULL REFERENCES runs(run_id),
    ord          INTEGER NOT NULL,
    rule_id      TEXT NOT NULL,
    result       TEXT NOT NULL,
    findings     INTEGER NOT NULL,
    reason       TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    PRIMARY KEY (run_id, rule_id)
);

CREATE TABLE IF NOT EXISTS findings (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id       TEXT NOT NULL UNIQUE,
    fingerprint      TEXT NOT NULL,
    period           TEXT NOT NULL,
    rule_id          TEXT NOT NULL,
    first_run_id     TEXT NOT NULL REFERENCES runs(run_id),
    latest_run_id    TEXT NOT NULL REFERENCES runs(run_id),
    status           TEXT NOT NULL,
    finding_json     TEXT NOT NULL,   -- the engine Finding as JSON, without evidence
    evidence_json    TEXT NOT NULL,   -- the Finding's evidence list
    evidence_count   INTEGER NOT NULL,
    explanation_json TEXT NOT NULL,   -- engine explain() output, computed with the real Decimals
    created_at       TEXT NOT NULL,
    UNIQUE (period, fingerprint)
);
CREATE INDEX IF NOT EXISTS findings_rule ON findings(rule_id);

CREATE TABLE IF NOT EXISTS finding_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    finding_id  TEXT NOT NULL REFERENCES findings(finding_id),
    from_status TEXT,
    to_status   TEXT NOT NULL,
    actor       TEXT NOT NULL,
    at          TEXT NOT NULL,
    reason_code TEXT,
    note        TEXT
);
CREATE INDEX IF NOT EXISTS history_finding ON finding_history(finding_id);

CREATE TABLE IF NOT EXISTS run_findings (
    run_id     TEXT NOT NULL REFERENCES runs(run_id),
    ord        INTEGER NOT NULL,
    finding_id TEXT NOT NULL REFERENCES findings(finding_id),
    PRIMARY KEY (run_id, finding_id)
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)  # explicit transactions
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init(self) -> None:
        parent = Path(self.path).parent
        if str(parent) not in ("", "."):
            parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
            except sqlite3.DatabaseError:
                pass
            conn.executescript(SCHEMA)
        finally:
            conn.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One immediate transaction: all of it commits, or none of it does."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()
