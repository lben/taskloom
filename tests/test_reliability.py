"""Stress tests: random editor use, random flows against the engine's rules, a busy scheduler."""

import datetime as dt
import random
import subprocess
import sys
from pathlib import Path

import pytest

from taskloom.config import Home
from taskloom.engine import run_flow
from taskloom.flow import load_flow
from taskloom.history import History
from taskloom.registry import discover
from taskloom.scheduler import Scheduler

HERE = Path(__file__).parent


@pytest.mark.parametrize("seed", range(6))
def test_random_editor_use_never_crashes_and_saves_valid_flows(tmp_path, seed):
    proc = subprocess.run([sys.executable, str(HERE / "fuzz_editor.py"), str(seed), "250", str(tmp_path)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0 and proc.stdout.strip().endswith("ok"), proc.stdout[-2000:] + proc.stderr[-4000:]
    assert "Traceback" not in proc.stderr, proc.stderr[-4000:]


def _random_flow(rnd: random.Random, n: int) -> dict:
    """Python blocks that succeed or fail, wired forward at random with data, after, and error edges."""
    blocks, edges = {}, []
    for i in range(n):
        fails = rnd.random() < 0.25
        blocks[f"b{i}"] = {"type": "logic.python", "config": {
            "inputs": ["a", "b"], "outputs": ["output"],
            "code": "raise RuntimeError('planned')" if fails else "output = 1"}}
    for j in range(1, n):
        for _ in range(rnd.randint(0, 3)):
            i = rnd.randrange(j)
            port = rnd.choices(["output", "error"], [4, 1])[0]
            edge = f"b{i}.{port} -> b{j}.{rnd.choice(['a', 'b', 'after'])}"
            if edge not in edges:
                edges.append(edge)
    return {"blocks": blocks, "edges": edges}


@pytest.mark.parametrize("seed", range(40))
def test_random_flows_never_run_anything_downstream_of_a_failure(tmp_path, seed):
    import yaml

    rnd = random.Random(seed)
    data = _random_flow(rnd, rnd.randint(4, 14))
    path = tmp_path / "flow.yaml"
    path.write_text(yaml.safe_dump(data))
    home = Home(tmp_path / "home")
    result = run_flow(load_flow(path), discover(None), home)
    history = History(home.history_db)
    status = {b: info["status"] for b, info in history.blocks(result.run_id).items()}
    history.close()

    edges = [(s.split(".")[0], s.split(".")[1].split(" ")[0], d.split(".")[0]) for s, d in
             (e.split(" -> ") for e in data["edges"])]
    # Safety: a block reached from a failed block through normal (non-error) edges never succeeds.
    tainted = {b for b, s in status.items() if s == "failed"}
    changed = True
    while changed:
        changed = False
        for src, port, dst in edges:
            if src in tainted and port != "error" and dst not in tainted:
                tainted.add(dst)
                changed = True
    for b in tainted:
        assert status[b] != "success" or b not in {d for s, p, d in edges if s in tainted and p != "error"}, (b, status)
    # Liveness: with no failure anywhere before it, a block whose data comes only from blocks that ran, runs.
    for name, spec in data["blocks"].items():
        incoming = [(s, p) for s, p, d in edges if d == name]
        upstream_ok = all(status[s] == "success" and p == "output" for s, p in incoming)
        if upstream_ok:
            assert status[name] in ("success", "failed"), (name, status, incoming)


def test_a_busy_scheduler_runs_every_due_run_exactly_once(tmp_path):
    home = Home(tmp_path / "home")
    home.root.mkdir(parents=True)
    (home.root / "settings.yaml").write_text("max_concurrent_runs: 6\n")
    flows = []
    for i in range(12):
        path = tmp_path / f"flow{i}.yaml"
        path.write_text(f"""
name: flow{i}
overlap: queue
params:
  when: {{type: datetime, expr: "now()"}}
blocks:
  tick:
    type: trigger.schedule
    config: {{cron: "* * * * *"}}
  work:
    type: logic.python
    config: {{inputs: [], outputs: [], code: "import time; time.sleep(0.2)"}}
edges:
  - tick.start -> work.after
""")
        home.set_scheduled(path, True)
        flows.append(path)
    notes = []
    scheduler = Scheduler(home, lambda title, message: notes.append((title, message)))
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    for minute in range(5):
        scheduler.tick(t0 + dt.timedelta(minutes=minute))
    scheduler.wait_for_runs(timeout=300)

    history = History(home.history_db)
    runs = history.runs(limit=1000)
    history.close()
    assert not scheduler.runs and not any(scheduler.queued.values()) and not scheduler.waiting
    assert notes == []
    assert len(runs) == 12 * 4 and all(r["status"] == "success" for r in runs)
    for path in flows:
        whens = sorted(r["params"] for r in runs if r["flow_path"] == str(path))
        assert len(whens) == len(set(whens)) == 4  # each due minute ran once


CRASHING_EDITOR = """
import ctypes, os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QMessageBox
from taskloom.config import Home
from taskloom.editor.window import MainWindow
app = QApplication([])
w = MainWindow(Home(Path(sys.argv[1])))
shown = []
QMessageBox.warning = staticmethod(lambda *a, **k: shown.append(a[1]))
w.safety.install_crash_log(on_error=w.show_error)
w.restore_session()

def broken_slot():
    raise ValueError("a bug in a button")

QTimer.singleShot(0, broken_slot)
QTimer.singleShot(200, lambda: print("still running, told the user:", shown, flush=True))
if sys.argv[2] == "hard":
    QTimer.singleShot(400, lambda: ctypes.string_at(0))  # a hard crash, like a bug inside Qt
QTimer.singleShot(800, app.quit)
app.exec()
"""


@pytest.mark.parametrize("kind", ["error", "hard"])
def test_errors_and_hard_crashes_are_written_to_the_crash_log(tmp_path, kind):
    script = tmp_path / "crashing.py"
    script.write_text(CRASHING_EDITOR)
    proc = subprocess.run([sys.executable, str(script), str(tmp_path / "home"), kind],
                          capture_output=True, text=True, timeout=120)
    log = (tmp_path / "home" / "crash.log").read_text()

    assert "still running, told the user: ['Something went wrong']" in proc.stdout
    assert "ValueError: a bug in a button" in log
    if kind == "hard":
        assert proc.returncode != 0 and "Fatal Python error" in log
    else:
        assert proc.returncode == 0
