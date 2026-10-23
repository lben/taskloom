"""Runs, the scheduler and the editor share one history file: opening it while another connection is writing to it
before the file is in WAL mode (e.g. the first run in a new Taskloom folder creating it while the editor polls) works
once that connection is done, instead of failing at once with "database is locked"."""

import sqlite3
import threading
from contextlib import closing, contextmanager

from taskloom.history import History


@contextmanager
def another_connection_writing(path):
    """A connection in the middle of a write to `path`, in SQLite's default rollback journal (as a run is while it
    creates a new history file); it commits after half a second."""
    other = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    other.execute("BEGIN IMMEDIATE")
    other.execute("CREATE TABLE IF NOT EXISTS t (x)")
    commit = threading.Timer(0.5, other.execute, ("COMMIT",))
    commit.start()
    try:
        yield
    finally:
        commit.join()
        other.close()


def journal_mode(path) -> str:
    with closing(sqlite3.connect(path)) as db:
        return db.execute("PRAGMA journal_mode").fetchone()[0]


def test_the_history_opens_while_another_connection_writes_to_it_and_the_file_ends_up_in_wal(tmp_path):
    path = tmp_path / "history.db"
    with another_connection_writing(path):
        history = History(path)
    run_id = history.start_run("nightly", str(tmp_path / "nightly.yaml"), {}, "2026-10-23T07:00:00")
    assert [run["id"] for run in history.runs()] == [run_id]
    history.close()
    History(path).close()  # the next open switches the file, if the first one left that to the other connection
    assert journal_mode(path) == "wal"


def test_the_editor_polls_while_a_run_writes_a_new_history_file(win, tmp_path):
    """An exception in the poll timer shows 'Something went wrong' in the packaged editor."""
    path = win.home.history_db
    path.parent.mkdir(parents=True, exist_ok=True)
    with another_connection_writing(path):
        win._refresh_statuses()
    win._refresh_statuses()  # the next poll
    assert journal_mode(path) == "wal"
    history = History(path)
    history.start_run("nightly", str(tmp_path / "nightly.yaml"), {}, "2026-10-23T07:00:00")  # by this process: alive
    history.close()
    win._refresh_statuses()
    assert win.running_flows.text() == "1 flow running"
