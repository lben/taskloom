"""Run history in SQLite: runs, block results and per-block logs."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    flow TEXT NOT NULL, flow_path TEXT NOT NULL, params TEXT NOT NULL,
    status TEXT NOT NULL, started TEXT NOT NULL, finished TEXT
);
CREATE TABLE IF NOT EXISTS blocks (
    run_id INTEGER NOT NULL, block TEXT NOT NULL, status TEXT NOT NULL,
    started TEXT, finished TEXT, attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT, summary TEXT, tables TEXT, PRIMARY KEY (run_id, block)
);
CREATE TABLE IF NOT EXISTS logs (
    run_id INTEGER NOT NULL, block TEXT, ts TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_run ON logs (run_id, block);
"""


class History:
    """Thread-safe: blocks log from worker threads."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        try:  # history files from before the tables column existed
            self._db.execute("ALTER TABLE blocks ADD COLUMN tables TEXT")
        except sqlite3.OperationalError:
            pass
        self._lock = threading.Lock()

    def _exec(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    def start_run(self, flow: str, flow_path: str, params: dict, ts: str) -> int:
        cur = self._exec(
            "INSERT INTO runs (flow, flow_path, params, status, started) VALUES (?, ?, ?, 'running', ?)",
            (flow, flow_path, json.dumps(params, default=str), ts),
        )
        return cur.lastrowid

    def __call__(self, event: dict):
        """Event sink."""
        kind, run_id, ts = event["event"], event["run_id"], event["ts"]
        if kind == "block_started":
            self._exec(
                "INSERT INTO blocks (run_id, block, status, started, attempts) VALUES (?, ?, 'running', ?, ?) "
                "ON CONFLICT (run_id, block) DO UPDATE SET status='running', attempts=excluded.attempts",
                (run_id, event["block"], ts, event["attempt"]),
            )
        elif kind == "block_finished":
            self._exec(
                "INSERT INTO blocks (run_id, block, status, finished, attempts, error, summary, tables) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id, block) DO UPDATE SET status=excluded.status, finished=excluded.finished, "
                "attempts=excluded.attempts, error=excluded.error, summary=excluded.summary, tables=excluded.tables",
                (run_id, event["block"], event["status"], ts, event.get("attempts", 0), event.get("error"),
                 event.get("summary"), json.dumps(event.get("tables") or {})),
            )
        elif kind == "log":
            self._exec(
                "INSERT INTO logs (run_id, block, ts, level, message) VALUES (?, ?, ?, ?, ?)",
                (run_id, event.get("block"), ts, event["level"], event["message"]),
            )
        elif kind == "run_finished":
            self._exec("UPDATE runs SET status=?, finished=? WHERE id=?", (event["status"], ts, run_id))

    def running_ids(self) -> set[int]:
        return {row[0] for row in self._exec("SELECT id FROM runs WHERE status='running'")}

    # --- reading, for the editor ---------------------------------------------

    def runs(self, flow_path: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = "SELECT id, flow, flow_path, params, status, started, finished FROM runs", []
        if flow_path is not None:
            sql, args = sql + " WHERE flow_path=?", [flow_path]
        rows = self._exec(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
        return [dict(zip(("id", "flow", "flow_path", "params", "status", "started", "finished"), r)) for r in rows]

    def run(self, run_id: int) -> dict:
        row = self._exec("SELECT id, flow, flow_path, params, status, started, finished FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(zip(("id", "flow", "flow_path", "params", "status", "started", "finished"), row))

    def blocks(self, run_id: int) -> dict:
        rows = self._exec("SELECT block, status, attempts, error, summary, tables FROM blocks WHERE run_id=?", (run_id,))
        return {r[0]: {"status": r[1], "attempts": r[2], "error": r[3], "summary": r[4],
                       "tables": json.loads(r[5] or "{}")} for r in rows}

    def logs(self, run_id: int) -> list[dict]:
        rows = self._exec("SELECT block, ts, level, message FROM logs WHERE run_id=? ORDER BY rowid", (run_id,))
        return [dict(zip(("block", "ts", "level", "message"), r)) for r in rows]

    def mark_crashed(self, run_id: int, ts: str):
        """Record a run whose process died without finishing (e.g. it was killed)."""
        self._exec("UPDATE runs SET status='failed', finished=? WHERE id=? AND status='running'", (ts, run_id))
        self._exec("UPDATE blocks SET status='failed', error='the run process stopped unexpectedly' "
                   "WHERE run_id=? AND status='running'", (run_id,))

    def close(self):
        self._db.close()
