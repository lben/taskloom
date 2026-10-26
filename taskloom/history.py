"""Run history in SQLite: runs, block results and per-block logs."""

from __future__ import annotations

import json
import os
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
    error TEXT, summary TEXT, tables TEXT, previews TEXT, PRIMARY KEY (run_id, block)
);
CREATE TABLE IF NOT EXISTS logs (
    run_id INTEGER NOT NULL, block TEXT, ts TEXT NOT NULL,
    level TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS logs_run ON logs (run_id, block);
CREATE TABLE IF NOT EXISTS keeps (
    flow_path TEXT NOT NULL, block TEXT NOT NULL, keep TEXT, keep_failed INTEGER NOT NULL,
    PRIMARY KEY (flow_path, block)
);
"""


RUN_COLUMNS = ("id", "flow", "flow_path", "params", "status", "started", "finished", "started_by")


def run_label(run_id: int, started: str | None) -> str:
    """'run #12 · 2026-10-15 14:03:22': a run's number and when it started."""
    return f"run #{run_id}" + (f" · {started.replace('T', ' ')[:19]}" if started else "")


def connect(path: Path) -> sqlite3.Connection:
    """Several runs, the scheduler and the editor share one history file: wait for your turn
    (up to 30 s) instead of failing with "database is locked"."""
    db = sqlite3.connect(path, timeout=30, check_same_thread=False, isolation_level=None)
    if db.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        try:
            db.execute("PRAGMA journal_mode=WAL")  # needs the file to itself, so only once
        except sqlite3.OperationalError as e:
            # Another connection is using the file, and the busy timeout does not wait for that: a later open
            # switches it (and this connection follows then).
            if e.sqlite_errorcode != sqlite3.SQLITE_BUSY:
                raise
    return db


class History:
    """Thread-safe: blocks log from worker threads."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._db = connect(path)
        self._db.executescript(SCHEMA)
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(runs)")}
        # A history file from before runs recorded their process, what started them, or the start blocks they
        # count for: it gets the column.
        for name, kind in (("pid", "INTEGER"), ("started_by", "TEXT NOT NULL DEFAULT ''"),
                           ("counts_for", "TEXT NOT NULL DEFAULT ''")):
            if name not in columns:
                try:
                    self._db.execute(f"ALTER TABLE runs ADD COLUMN {name} {kind}")
                except sqlite3.OperationalError:
                    pass  # another process added it just now
        self._lock = threading.Lock()

    def _exec(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args)

    def start_run(self, flow: str, flow_path: str, params: dict, ts: str, started_by: str = "",
                  counts_for=(), keeps: dict | None = None) -> int:
        """`started_by`: the start blocks that started it ("manual, manual_2"), or "only load" / "from load".
        `counts_for`: the start blocks it counts for. `keeps`: the flow's start blocks, with their Keep runs and
        Always keep failed runs ({block: (keep, keep_failed)}); from now on these decide for all their runs."""
        for block, (kept, failed) in (keeps or {}).items():  # first: a run is never without its start blocks' keeps
            self._exec("INSERT INTO keeps (flow_path, block, keep, keep_failed) VALUES (?, ?, ?, ?) ON CONFLICT "
                       "(flow_path, block) DO UPDATE SET keep=excluded.keep, keep_failed=excluded.keep_failed",
                       (flow_path, block, kept, failed))
        cur = self._exec(
            "INSERT INTO runs (flow, flow_path, params, status, started, pid, started_by, counts_for) "
            "VALUES (?, ?, ?, 'running', ?, ?, ?, ?)",
            (flow, flow_path, json.dumps(params, default=str, ensure_ascii=False), ts, os.getpid(), started_by,
             ", ".join(counts_for)),
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
                "INSERT INTO blocks (run_id, block, status, finished, attempts, error, summary, tables, previews) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (run_id, block) DO UPDATE SET status=excluded.status, finished=excluded.finished, "
                "attempts=excluded.attempts, error=excluded.error, summary=excluded.summary, tables=excluded.tables, "
                "previews=excluded.previews",
                (run_id, event["block"], event["status"], ts, event.get("attempts", 0), event.get("error"),
                 event.get("summary"), json.dumps(event.get("tables") or {}), json.dumps(event.get("previews") or {})),
            )
        elif kind == "log":
            self._exec(
                "INSERT INTO logs (run_id, block, ts, level, message) VALUES (?, ?, ?, ?, ?)",
                (run_id, event.get("block"), ts, event["level"], event["message"]),
            )
        elif kind == "run_finished":
            self._exec("UPDATE runs SET status=?, finished=? WHERE id=?", (event["status"], ts, run_id))

    def keeps(self) -> dict:
        """How each flow file's start blocks keep their runs, as the flow's newest run had it:
        {(flow_path, block): (keep, keep_failed)}."""
        rows = self._exec("SELECT flow_path, block, keep, keep_failed FROM keeps")
        return {(flow_path, block): (keep, bool(failed)) for flow_path, block, keep, failed in rows}

    def finished_runs(self, before: str, flow_paths: list[str]):
        """The finished runs, newest first, that started before `before` or are runs of one of `flow_paths`:
        (id, flow_path, status, started, the start blocks it counts for). One at a time: they can be millions."""
        rows = self._exec("SELECT id, flow_path, status, started, counts_for FROM runs WHERE status != 'running' "
                          f"AND (started < ? OR flow_path IN ({', '.join('?' * len(flow_paths))})) ORDER BY id DESC",
                          (before, *flow_paths))
        for run_id, flow_path, status, started, counts_for in rows:
            yield run_id, flow_path, status, started, counts_for.split(", ") if counts_for else []

    def forget(self, run_id: int):
        """Forget a run, with its block results, values and logs."""
        self._exec("DELETE FROM logs WHERE run_id=?", (run_id,))
        self._exec("DELETE FROM blocks WHERE run_id=?", (run_id,))
        self._exec("DELETE FROM runs WHERE id=?", (run_id,))

    # --- reading, for the editor ---------------------------------------------

    def runs(self, flow_path: str | None = None, limit: int = 200) -> list[dict]:
        sql, args = f"SELECT {', '.join(RUN_COLUMNS)} FROM runs", []
        if flow_path is not None:
            sql, args = sql + " WHERE flow_path=?", [flow_path]
        rows = self._exec(sql + " ORDER BY id DESC LIMIT ?", args + [limit])
        return [dict(zip(RUN_COLUMNS, r)) for r in rows]

    def last_run(self, flow_path: str, started_by: list[str]) -> dict | None:
        """The newest finished run of a flow file that one of `started_by` started: a start block (also
        together with others, e.g. manual in "manual, manual_2"), or a re-run such as "only load"."""
        match = " OR ".join(["instr(', ' || started_by || ', ', ?) > 0"] * len(started_by))
        row = self._exec(f"SELECT {', '.join(RUN_COLUMNS)} FROM runs WHERE flow_path=? AND status != 'running' "
                         f"AND ({match}) ORDER BY id DESC LIMIT 1",
                         [flow_path, *(f", {name}, " for name in started_by)]).fetchone()
        return dict(zip(RUN_COLUMNS, row)) if row else None

    def label(self, run_id: int) -> str:
        """'run #12 · 2026-10-15 14:03:22' (when it started), or 'run #12' if it is not known."""
        run = self.run(run_id)
        return run_label(run_id, run["started"] if run else None)

    def run(self, run_id: int) -> dict | None:
        row = self._exec(f"SELECT {', '.join(RUN_COLUMNS)} FROM runs WHERE id=?", (run_id,)).fetchone()
        return dict(zip(RUN_COLUMNS, row)) if row else None

    def blocks(self, run_id: int) -> dict:
        rows = self._exec("SELECT block, status, attempts, error, summary, tables, previews FROM blocks WHERE run_id=?",
                          (run_id,))
        return {r[0]: {"status": r[1], "attempts": r[2], "error": r[3], "summary": r[4],
                       "tables": json.loads(r[5] or "{}"), "previews": json.loads(r[6] or "{}")} for r in rows}

    def latest_block_results(self, flow_path: str, after_run_id: int = 0) -> dict:
        """Each block's result in the latest run of the flow file (after run `after_run_id`) it took part in:
        {block: {run_id, started (the run's), started_by, status, attempts, error, summary, tables, previews}}.
        Runs still going count with the blocks they have finished."""
        rows = self._block_results("r.flow_path=? AND r.id>? ORDER BY r.id", (flow_path, after_run_id))
        return {block: {k: v for k, v in result.items() if k != "finished"} for block, result in rows}

    def block_results(self, flow_path: str, block: str, limit: int = 200) -> list[dict]:
        """A block's results in the runs of the flow file it took part in, newest first: as
        latest_block_results gives them, plus when the block finished."""
        return [result for _, result in self._block_results("r.flow_path=? AND b.block=? ORDER BY r.id DESC LIMIT ?",
                                                             (flow_path, block, limit))]

    def finished_blocks(self, run_id: int) -> list[tuple[str, dict]]:
        """The blocks a run finished, in the order they finished: (block, result as block_results gives it)."""
        return self._block_results("b.run_id=? ORDER BY b.finished", (run_id,))

    def _block_results(self, where: str, args) -> list[tuple[str, dict]]:
        rows = self._exec("SELECT b.block, b.run_id, r.started, r.started_by, b.status, b.attempts, b.error, b.summary, "
                          "b.tables, b.previews, b.finished FROM blocks b JOIN runs r ON r.id = b.run_id "
                          f"WHERE b.status != 'running' AND {where}", args)
        return [(r[0], {"run_id": r[1], "started": r[2], "started_by": r[3], "status": r[4], "attempts": r[5],
                        "error": r[6], "summary": r[7], "tables": json.loads(r[8] or "{}"),
                        "previews": json.loads(r[9] or "{}"), "finished": r[10]}) for r in rows]

    def logs(self, run_id: int) -> list[dict]:
        rows = self._exec("SELECT block, ts, level, message FROM logs WHERE run_id=? ORDER BY rowid", (run_id,))
        return [dict(zip(("block", "ts", "level", "message"), r)) for r in rows]

    def running(self) -> list[dict]:
        """Runs in progress, of any flow, with the process running each (None: from before runs said)."""
        rows = self._exec("SELECT id, flow, flow_path, started, pid FROM runs WHERE status='running' ORDER BY id")
        return [dict(zip(("id", "flow", "flow_path", "started", "pid"), r)) for r in rows]

    def mark_crashed(self, run_id: int, ts: str):
        """Record a run whose process died without finishing (e.g. it was killed)."""
        self._exec("UPDATE runs SET status='failed', finished=? WHERE id=? AND status='running'", (ts, run_id))
        self._exec("UPDATE blocks SET status='failed', error='the run process stopped unexpectedly' "
                   "WHERE run_id=? AND status='running'", (run_id,))

    def close(self):
        self._db.close()
