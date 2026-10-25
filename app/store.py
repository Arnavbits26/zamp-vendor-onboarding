"""
Run history + vendor records, in one SQLite file (data/runs.db).

  runs     every pipeline run, with its full result. This is the audit trail the problem
           statement says is missing today ("the only audit trail is someone's inbox"),
           and the data the dashboard reads.
  vendors  vendors approved BY THIS PROCESS. They join the vendor master, so a later
           submission that reuses their bank account is caught like any other duplicate.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.config import ROOT

DEFAULT_DB = ROOT / "data" / "runs.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs(
    run_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    submission_id TEXT NOT NULL,
    version       INTEGER NOT NULL,
    vendor_name   TEXT,
    status        TEXT,
    headline      TEXT,
    created_at    TEXT,
    result_json   TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_sub ON runs(submission_id, version);
CREATE TABLE IF NOT EXISTS vendors(
    vendor_id TEXT PRIMARY KEY, submission_id TEXT UNIQUE, legal_name TEXT,
    pan TEXT, gstin TEXT, ifsc TEXT, account_number TEXT, approved_at TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------------ runs
    def save_run(self, result: dict) -> int:
        with self._lock, self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(submission_id, version, vendor_name, status, headline, created_at, result_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (result["submission_id"], result.get("version", 1), result["vendor_name"], result["status"],
                 result["headline"], _now(), json.dumps(result)))
        return cur.lastrowid

    def _all(self, sql: str, args: tuple = ()) -> list:
        with self._lock:
            return self.conn.execute(sql, args).fetchall()

    def list_runs(self, limit: int = 200) -> list[dict]:
        rows = self._all(
            "SELECT run_id, submission_id, version, vendor_name, status, headline, created_at "
            "FROM runs ORDER BY run_id DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def get_run(self, run_id: int) -> dict | None:
        rows = self._all("SELECT result_json FROM runs WHERE run_id=?", (run_id,))
        return json.loads(rows[0]["result_json"]) if rows else None

    def previous_run(self, submission_id: str, before_version: int) -> dict | None:
        """The most recent run of an EARLIER version of this submission."""
        rows = self._all(
            "SELECT result_json FROM runs WHERE submission_id=? AND version<? "
            "ORDER BY version DESC, run_id DESC LIMIT 1", (submission_id, before_version))
        return json.loads(rows[0]["result_json"]) if rows else None

    def versions(self, submission_id: str) -> list[dict]:
        """Latest run of each version, oldest first."""
        rows = self._all(
            "SELECT result_json FROM runs WHERE run_id IN "
            "(SELECT MAX(run_id) FROM runs WHERE submission_id=? GROUP BY version) ORDER BY version",
            (submission_id,))
        return [json.loads(r["result_json"]) for r in rows]

    # --------------------------------------------------------------- vendors
    def add_vendor(self, sub: dict) -> str:
        vid = f"NEW-{sub['submission_id']}"
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO vendors VALUES (?,?,?,?,?,?,?,?)",
                (vid, sub["submission_id"], sub["company"]["vendor_name"], sub["tax"]["pan"], sub["tax"]["gstin"],
                 sub["bank"]["ifsc"], sub["bank"]["account_number"], _now()))
        return vid

    def approved_vendors(self, exclude_submission: str | None = None) -> list[dict]:
        """Vendors approved by this process, shaped like vendor-master entries."""
        rows = self._all("SELECT * FROM vendors WHERE submission_id IS NOT ?", (exclude_submission,))
        return [{"vendor_id": r["vendor_id"], "legal_name": r["legal_name"], "pan": r["pan"],
                 "gstin": r["gstin"], "ifsc": r["ifsc"], "account_number": r["account_number"],
                 "status": "approved"} for r in rows]

    def reset(self) -> None:
        with self._lock, self.conn:
            self.conn.executescript("DELETE FROM runs; DELETE FROM vendors; DELETE FROM sqlite_sequence;")
