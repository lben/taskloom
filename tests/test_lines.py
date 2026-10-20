"""Lines: each start block runs its own line. A run of one line never clears or overwrites another line's
results; each block shows its latest result; a block's earlier results can be looked up, with when and what
started them. Through the real `taskloom` command, its history and the editor."""

import html
import json
import os
import re
import subprocess
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (QAbstractButton, QApplication, QLabel, QPlainTextEdit, QTableWidget,  # noqa: E402
                               QTextEdit)

from conftest import events  # noqa: E402
from taskloom.editor import dialogs  # noqa: E402
from taskloom.editor.panels import status_icon  # noqa: E402
from taskloom.history import History  # noqa: E402
from tests.test_editor import write_flow  # noqa: E402

# What the user had: a Manual Start line (a health check, two inspections, a report) and, in the same flow,
# a Schedule line (every minute); plus a block connected to no start block.
USER_FLOW = """
    blocks:
      manual:
        type: trigger.manual
      health:
        type: logic.python
        config: {inputs: [], code: "output = {'healthy': True}"}
      inspect_a:
        type: logic.inspect
      inspect_b:
        type: logic.inspect
      report:
        type: logic.python
        config: {inputs: [a, b], code: "output = a['healthy'] and b['healthy']"}
      schedule:
        type: trigger.schedule
        config: {cron: "* * * * *"}
      job:
        type: logic.python
        config: {inputs: [], code: "output = 7"}
      spare:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
    edges:
      - manual.start -> health.after
      - health.output -> inspect_a.value
      - health.output -> inspect_b.value
      - inspect_a.value -> report.a
      - inspect_b.value -> report.b
      - schedule.start -> job.after
"""
MANUAL_LINE = {"manual", "health", "inspect_a", "inspect_b", "report"}
SCHEDULE_LINE = {"schedule", "job"}

# Two lines that meet: `shared` runs after either of them.
TWO_LINES = """
    blocks:
      manual:
        type: trigger.manual
      load:
        type: logic.python
        config: {inputs: [], code: "output = 21"}
      compute:
        type: logic.python
        config: {inputs: [input], outputs: [], code: "open('result.txt', 'w').write(str(input * 2))"}
      nightly:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
      job:
        type: logic.python
        config: {inputs: [], code: "output = 'nightly'"}
      shared:
        type: logic.python
        config: {inputs: [], code: "output = 'either'"}
    edges:
      - manual.start -> load.after
      - load.output -> compute.input
      - nightly.start -> job.after
      - compute.done -> shared.after
      - job.done -> shared.after
"""

OFF = "off · not connected to a start block"
RESULTS = "Results of this block…"
RESULT_COLUMNS = ["Run", "Started", "Started by", "Status", "Result"]
WHEN = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d"


# --- helpers ---------------------------------------------------------------------------

def history_of(home) -> History:
    return History(home / "history.db")


def run_elsewhere(win, flow, *args) -> int:
    """A run started outside the editor (the scheduler runs `taskloom run flow --trigger <block>`); its id."""
    env = {**os.environ, "TASKLOOM_HOME": str(win.home.root)}
    result = subprocess.run([sys.executable, "-m", "taskloom.cli", "run", str(flow), *map(str, args)], env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode in (0, 1), result.stderr  # it ran (and succeeded or failed)
    history = History(win.home.history_db)
    try:
        return history.runs(str(flow.resolve()), limit=1)[0]["id"]
    finally:
        history.close()


def wait_runs(qtbot, tab, n) -> int:
    """Wait until the tab's n-th run (from the editor) has finished; its id."""
    qtbot.waitUntil(lambda: len(tab.runs) == n and not tab.running and tab.runs[-1].status is not None,
                    timeout=60_000)
    return tab.runs[-1].run_id


def start_from(win, tab, start_block):
    """Right-click a start block: "Run the flow from here" / "Run the flow now, as if its time had come"."""
    (action,) = win._block_menu_for(tab, start_block).actions()
    action.trigger()


def menu_action(win, tab, block, starts_with):
    return next(a for a in win._block_menu_for(tab, block).actions() if a.text().startswith(starts_with))


def shown(tab, blocks=None) -> dict:
    """Each block's result on the canvas: (its status, the run that result comes from)."""
    return {b: (tab.scene.nodes[b].status, (tab.block_info.get(b) or {}).get("run_id"))
            for b in (blocks or tab.doc.blocks)}


def under(tab, block) -> str:
    """The line under a block on the canvas when it is not just its type: its result, or the off marker."""
    node = tab.scene.nodes[block]
    return node.status_text if node.status else ""


def tooltip_lines(win, tab) -> list[str]:
    return win.tabs.tabToolTip(win.tabs.indexOf(tab)).splitlines()


def tab_icon_is(win, tab, status) -> bool:
    """The flow's tab shows the icon of `status` (with or without the scheduled clock beside it)."""
    size = win.tabs.iconSize()
    shown_icon = win.tabs.tabIcon(win.tabs.indexOf(tab)).pixmap(size).toImage()
    return any(shown_icon == status_icon(status, clock=clock).pixmap(size).toImage() for clock in (False, True))


def in_block_results(win, tab, block, monkeypatch, act):
    """Open "Results of this block…" from the block's menu and do act(dialog) in it, as you would."""
    actions = {a.text(): a for a in win._block_menu_for(tab, block).actions()}
    assert RESULTS in actions, list(actions)
    done = []

    def exec_(dialog):
        dialog.show()
        QApplication.processEvents()
        done.append(act(dialog))
        return dialog.result()

    monkeypatch.setattr(dialogs.BlockResultsDialog, "exec", exec_)
    actions[RESULTS].trigger()
    if not done:  # it opened without waiting for you to close it
        (dialog,) = [w for w in QApplication.topLevelWidgets()
                     if isinstance(w, dialogs.BlockResultsDialog) and w.isVisible()]
        QApplication.processEvents()
        done.append(act(dialog))
        dialog.close()
    return done[0]


def results_table(dialog) -> QTableWidget:
    for table in dialog.findChildren(QTableWidget):
        headers = [table.horizontalHeaderItem(c) for c in range(table.columnCount())]
        if [h.text() if h else "" for h in headers] == RESULT_COLUMNS:
            return table
    raise AssertionError(f"no table with the columns {RESULT_COLUMNS}")


def click_row(table, row, double=False):
    at = table.visualItemRect(table.item(row, 0)).center()
    QTest.mouseClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    if double:  # (QTest's double-click is only the second click)
        QTest.mouseDClick(table.viewport(), Qt.LeftButton, Qt.NoModifier, at)
    QApplication.processEvents()


def text_besides(dialog, table) -> str:
    """What the dialog shows besides its table of results (e.g. the selected run's values)."""
    parts = [html.unescape(re.sub(r"<[^>]*>", "", w.text())) for w in dialog.findChildren(QLabel)
             if w.isVisibleTo(dialog)]
    parts += [w.toPlainText() for w in [*dialog.findChildren(QTextEdit), *dialog.findChildren(QPlainTextEdit)]
              if w.isVisibleTo(dialog)]
    parts += [t.item(r, c).text() for t in dialog.findChildren(QTableWidget) if t is not table and t.isVisibleTo(dialog)
              for r in range(t.rowCount()) for c in range(t.columnCount()) if t.item(r, c)]
    return "\n".join(parts)


# --- the runner and its history -------------------------------------------------------

def test_a_run_reports_only_the_blocks_of_its_line(env):
    flow = env.write("flow.yaml", USER_FLOW)

    for args, line, started_by in (((), MANUAL_LINE, "manual"),
                                   (("--trigger", "schedule"), SCHEDULE_LINE, "schedule")):
        result = env.taskloom("run", flow, "--json-events", *args)

        assert result.returncode == 0, result.stderr
        evs = events(result.stdout)
        started, run_id = evs[0], evs[0]["run_id"]
        assert {e["block"] for e in evs if e["event"] in ("block_started", "block_finished")} == line
        assert set(env.block_status(run_id)) == line
        for block in {"manual", "health", "inspect_a", "inspect_b", "report", "schedule", "job", "spare"} - line:
            assert not (env.home / "runs" / str(run_id) / block).exists(), block
        assert "which started this run" not in result.stdout + result.stderr
        assert started["event"] == "run_started"
        assert started["blocks"] == sorted(line) and started["started_by"] == started_by
    assert not env.query("SELECT block FROM blocks WHERE summary LIKE '%which started this run%'")


def test_what_started_each_run_is_in_the_history(env):
    flow = env.write("flow.yaml", """
        blocks:
          morning:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          evening:
            type: trigger.manual
          load:
            type: logic.python
            config: {inputs: [], code: "output = 20"}
          work:
            type: logic.python
            config: {inputs: [input], code: "output = input + 1"}
          late:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - morning.start -> load.after
          - evening.start -> load.after
          - load.output -> work.input
          - nightly.start -> late.after
    """)
    only_schedule = env.write("only_schedule.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - nightly.start -> work.after
    """)
    no_start = env.write("no_start.yaml", """
        blocks:
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
    """)
    ids = {}
    for name, args in (("both manual", (flow,)), ("nightly", (flow, "--trigger", "nightly")),
                       ("only", (flow, "--only", "work", "--inputs-from", 1)),
                       ("from", (flow, "--from", "work", "--inputs-from", 1)),
                       ("only schedule", (only_schedule,)), ("no start", (no_start,))):
        result = env.taskloom("run", *args)
        assert result.returncode == 0, (name, result.stderr)
        ids[name] = env.last_run()[0]

    history = history_of(env.home)
    try:
        assert {r["id"]: r["started_by"] for r in history.runs(str(flow.resolve()))} == {
            ids["both manual"]: "morning, evening",  # in the flow's block order
            ids["nightly"]: "nightly", ids["only"]: "only work", ids["from"]: "from work"}
        assert history.run(ids["only schedule"])["started_by"] == "nightly"
        assert history.run(ids["no start"])["started_by"] == ""
    finally:
        history.close()
    assert env.query("SELECT started_by FROM runs WHERE id=?", ids["nightly"]) == [("nightly",)]


def test_one_start_block_runs_all_its_lines_and_a_failure_stops_only_its_own(env):
    flow = env.write("flow.yaml", """
        blocks:
          manual:
            type: trigger.manual
          extract:
            type: logic.python
            config: {inputs: [], code: "raise RuntimeError('source is down')"}
          load:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "open('load.txt', 'w').write('x')"}
          check:
            type: logic.python
            config: {inputs: [], code: "output = 'fine'"}
          report:
            type: logic.python
            config: {inputs: [input], outputs: [], code: "open('report.txt', 'w').write(input)"}
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          cleanup:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> extract.after
          - extract.output -> load.input
          - manual.start -> check.after
          - check.output -> report.input
          - nightly.start -> cleanup.after
    """)

    assert env.taskloom("run", flow).returncode == 1  # a failure nothing handles: the run failed
    first, status = env.last_run()

    assert status == "failed" and (env.root / "report.txt").read_text() == "fine"
    assert not (env.root / "load.txt").exists()
    assert env.block_status(first) == {"manual": "success", "extract": "failed", "load": "skipped",
                                       "check": "success", "report": "success"}  # not the nightly line

    assert env.taskloom("run", flow, "--trigger", "nightly").returncode == 0
    second, _ = env.last_run()
    history = history_of(env.home)
    try:
        latest = history.latest_block_results(str(flow.resolve()))
    finally:
        history.close()
    assert {b: (r["run_id"], r["status"]) for b, r in latest.items()} == {
        "manual": (first, "success"), "extract": (first, "failed"), "load": (first, "skipped"),
        "check": (first, "success"), "report": (first, "success"),
        "nightly": (second, "success"), "cleanup": (second, "success")}


def test_a_block_shared_by_two_lines_runs_in_either(env):
    flow = env.write("flow.yaml", """
        blocks:
          manual:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          a:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          b:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          shared:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('shared.txt', 'a').write('x')"}
        edges:
          - manual.start -> a.after
          - nightly.start -> b.after
          - a.done -> shared.after
          - b.done -> shared.after
    """)

    assert env.taskloom("run", flow, "--trigger", "nightly").returncode == 0
    nightly, _ = env.last_run()
    assert env.block_status(nightly) == {"nightly": "success", "b": "success", "shared": "success"}
    assert env.taskloom("run", flow).returncode == 0
    manual, _ = env.last_run()
    assert env.block_status(manual) == {"manual": "success", "a": "success", "shared": "success"}
    history = history_of(env.home)
    try:
        assert history.latest_block_results(str(flow.resolve()))["shared"]["run_id"] == manual  # the most recent
    finally:
        history.close()

    # Again from b on, with what the nightly run held: a, which did not take part, does not hold it back.
    result = env.taskloom("run", flow, "--from", "b", "--inputs-from", nightly)

    assert result.returncode == 0, result.stderr
    again, _ = env.last_run()
    assert env.block_status(again) == {"b": "success", "shared": "success"}
    assert (env.root / "shared.txt").read_text() == "xxx"


def test_reruns_report_only_the_blocks_they_run_and_carry_the_earlier_run_over(env):
    flow = env.write("flow.yaml", USER_FLOW)
    assert env.taskloom("run", flow).returncode == 0
    first, _ = env.last_run()

    for args, blocks, started_by in ((("--only", "report"), ["report"], "only report"),
                                     (("--from", "inspect_a"), ["inspect_a", "report"], "from inspect_a")):
        result = env.taskloom("run", flow, "--json-events", *args, "--inputs-from", first)

        assert result.returncode == 0, result.stderr
        evs = events(result.stdout)
        run_id = evs[0]["run_id"]
        assert evs[0]["blocks"] == blocks and evs[0]["started_by"] == started_by
        assert {e["block"] for e in evs if e["event"] in ("block_started", "block_finished")} == set(blocks)
        assert set(env.block_status(run_id)) == set(blocks)
        # As before: the new run holds what the run it starts from held (so retrying another block from it works).
        assert (env.home / "runs" / str(run_id) / "health" / "outputs.pickle").exists()


def test_each_blocks_latest_and_earlier_results(env):
    text = """
        params:
          n: {type: int, value: 1}
        blocks:
          manual:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          make:
            type: logic.python
            config: {inputs: [], code: "output = {'n': params['n']}"}
          job:
            type: logic.python
            config: {inputs: [], code: "output = params['n'] * 10"}
          shared:
            type: logic.python
            config: {inputs: [], code: "output = params['n']"}
        edges:
          - manual.start -> make.after
          - nightly.start -> job.after
          - make.done -> shared.after
          - job.done -> shared.after
    """
    flow = env.write("flow.yaml", text)
    runs = {}
    for name, args in (("manual", ("--param", "n=1")), ("nightly", ("--trigger", "nightly", "--param", "n=2"))):
        assert env.taskloom("run", flow, *args).returncode == 0
        runs[name] = env.last_run()[0]
    assert env.taskloom("run", flow, "--only", "make", "--inputs-from", runs["manual"], "--param", "n=3").returncode == 0
    runs["only"] = env.last_run()[0]
    assert env.taskloom("run", env.write("other.yaml", text), "--param", "n=4").returncode == 0  # another flow file
    path = str(flow.resolve())

    history = history_of(env.home)
    try:
        latest = history.latest_block_results(path)
        assert {b: r["run_id"] for b, r in latest.items()} == {
            "manual": runs["manual"], "make": runs["only"], "nightly": runs["nightly"], "job": runs["nightly"],
            "shared": runs["nightly"]}
        make = latest["make"]
        assert make["started"] == history.run(runs["only"])["started"] and make["started_by"] == "only make"
        result = {k: make[k] for k in ("status", "attempts", "error", "summary", "tables", "previews")}
        assert result == history.blocks(runs["only"])["make"]  # its result in that run
        assert make["status"] == "success" and json.loads(make["previews"]["output"]) == {"n": 3}
        assert latest["shared"]["started_by"] == "nightly" and latest["manual"]["started_by"] == "manual"

        assert {b: r["run_id"] for b, r in history.latest_block_results(path, after_run_id=runs["nightly"]).items()} \
            == {"make": runs["only"]}

        shared = history.block_results(path, "shared")  # newest run first, one per run it took part in
        assert [(r["run_id"], r["started_by"], r["status"]) for r in shared] == [
            (runs["nightly"], "nightly", "success"), (runs["manual"], "manual", "success")]
        assert [r["finished"] for r in shared] == [
            env.query("SELECT finished FROM blocks WHERE run_id=? AND block='shared'", r["run_id"])[0][0]
            for r in shared]
        assert [r["started"] for r in shared] == [history.run(r["run_id"])["started"] for r in shared]
        assert json.loads(shared[0]["previews"]["output"]) == 2
        assert [r["run_id"] for r in history.block_results(path, "make")] == [runs["only"], runs["manual"]]
        assert [r["run_id"] for r in history.block_results(path, "make", limit=1)] == [runs["only"]]
    finally:
        history.close()


def test_a_run_still_going_counts_with_its_finished_blocks(env):
    flow = env.write("flow.yaml", """
        blocks:
          manual:
            type: trigger.manual
          quick:
            type: logic.python
            config: {inputs: [], code: "output = 'first'"}
          slow:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import os, time
                while not os.path.exists('go.txt'):
                    time.sleep(0.05)
        edges:
          - manual.start -> quick.after
          - quick.done -> slow.after
    """)
    proc = env.popen("run", flow, "--json-events")
    try:
        run_id = None
        for line in proc.stdout:  # until slow is running (quick has finished)
            event = json.loads(line)
            run_id = event["run_id"]
            if event["event"] == "block_started" and event["block"] == "slow":
                break
        history = history_of(env.home)
        try:
            latest = history.latest_block_results(str(flow.resolve()))
        finally:
            history.close()
        assert latest["quick"]["run_id"] == run_id and latest["quick"]["status"] == "success"
        assert env.query("SELECT status FROM runs WHERE id=?", run_id) == [("running",)]
        (env.root / "go.txt").write_text("")
        assert proc.wait(timeout=60) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


# --- the editor -----------------------------------------------------------------------

def test_a_scheduled_run_leaves_the_other_lines_results_on_the_canvas(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "lines.yaml", USER_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)  # Run now: the manual line
    manual = wait_runs(qtbot, tab, 1)

    scheduled = run_elsewhere(win, flow, "--trigger", "schedule")  # as the scheduler does, every minute
    win._refresh_statuses()

    assert shown(tab, MANUAL_LINE | SCHEDULE_LINE) == {**{b: ("success", manual) for b in MANUAL_LINE},
                                                       **{b: ("success", scheduled) for b in SCHEDULE_LINE}}
    assert not any("which started this run" in node.status_text for node in tab.scene.nodes.values())

    win.run_flow(tab)  # and Run now again: only the manual line is cleared and updated
    assert shown(tab, SCHEDULE_LINE) == {b: ("success", scheduled) for b in SCHEDULE_LINE}
    again = wait_runs(qtbot, tab, 2)

    assert shown(tab, MANUAL_LINE | SCHEDULE_LINE) == {**{b: ("success", again) for b in MANUAL_LINE},
                                                       **{b: ("success", scheduled) for b in SCHEDULE_LINE}}
    assert not any("which started this run" in node.status_text for node in tab.scene.nodes.values())


def test_opening_a_flow_shows_each_lines_runs_you_have_not_seen(win, tmp_path):
    flow = write_flow(tmp_path / "unseen.yaml", TWO_LINES)
    win.close_tab(win.open_flow(flow))  # you saw it: no runs yet
    manual = run_elsewhere(win, flow)
    nightly = run_elsewhere(win, flow, "--trigger", "nightly")  # e.g. the scheduler, while it was closed

    tab = win.open_flow(flow)

    assert shown(tab) == {"manual": ("success", manual), "load": ("success", manual),
                          "compute": ("success", manual), "nightly": ("success", nightly),
                          "job": ("success", nightly), "shared": ("success", nightly)}  # its most recent
    win.close_tab(tab)

    later = run_elsewhere(win, flow, "--trigger", "nightly")
    tab = win.open_flow(flow)  # only that run is new to you: the runs you saw do not come back

    assert shown(tab) == {"manual": (None, None), "load": (None, None), "compute": (None, None),
                          "nightly": ("success", later), "job": ("success", later), "shared": ("success", later)}


def test_showing_an_older_run_shows_only_it_and_following_again_shows_each_blocks_latest(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "two.yaml", TWO_LINES)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    manual = wait_runs(qtbot, tab, 1)
    start_from(win, tab, "nightly")
    nightly = wait_runs(qtbot, tab, 2)
    latest = {"manual": ("success", manual), "load": ("success", manual), "compute": ("success", manual),
              "nightly": ("success", nightly), "job": ("success", nightly), "shared": ("success", nightly)}
    assert shown(tab) == latest

    win.show_history_run(manual)  # an older run, on purpose (double-click in Run history)

    assert shown(tab) == {"manual": ("success", manual), "load": ("success", manual),
                          "compute": ("success", manual), "shared": ("success", manual),
                          "nightly": (None, None), "job": (None, None)}

    win.show_history_run(nightly)  # the newest: following the latest runs again
    assert shown(tab) == latest

    win.show_history_run(manual)  # the older run again, on purpose
    assert shown(tab, ["nightly", "job"]) == {"nightly": (None, None), "job": (None, None)}
    win.run_flow(tab)  # starting a run: following the latest runs again
    again = wait_runs(qtbot, tab, 3)
    assert shown(tab) == {"manual": ("success", again), "load": ("success", again), "compute": ("success", again),
                          "shared": ("success", again), "nightly": ("success", nightly), "job": ("success", nightly)}


def test_showing_the_newest_run_of_a_flow_you_saw_shows_it(win, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "seen.yaml", TWO_LINES)
    run_elsewhere(win, flow)
    newest = run_elsewhere(win, flow, "--trigger", "nightly")
    nightly_line = {b: ("success", newest) for b in ("nightly", "job", "shared")}
    win.close_tab(win.open_flow(flow))  # you saw its runs
    tab = win.open_flow(flow)
    assert all(result == (None, None) for result in shown(tab).values())  # it opens clean

    table = win.history_panel.table
    (row,) = [r for r in range(table.rowCount()) if table.item(r, 0).text() == str(newest)]
    click_row(table, row, double=True)  # the newest run, on purpose (double-click in Run history)

    assert shown(tab, ["nightly", "job", "shared"]) == nightly_line

    win.close_tab(tab)
    tab = win.open_flow(flow)
    assert all(result == (None, None) for result in shown(tab).values())

    def show_newest(dialog):
        click_row(results_table(dialog), 0)
        (show,) = [b for b in dialog.findChildren(QAbstractButton) if b.text().replace("&", "") == "Show this run"]
        show.click()

    in_block_results(win, tab, "shared", monkeypatch, show_newest)

    assert shown(tab, ["nightly", "job", "shared"]) == nightly_line


def test_run_history_says_what_started_each_run(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "started.yaml", TWO_LINES)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    start_from(win, tab, "nightly")
    wait_runs(qtbot, tab, 2)

    table = win.history_panel.table
    labels = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    assert "Started by" in labels, labels
    column = labels.index("Started by")
    assert labels[column - 1] == "Started"
    assert [table.item(row, column).text() for row in range(table.rowCount())] == ["nightly", "manual"]


RERUN_FLOW = """
    blocks:
      manual:
        type: trigger.manual
      load:
        type: logic.python
        config: {inputs: [], code: "output = 21"}
      compute:
        type: logic.python
        config: {inputs: [input], outputs: [], code: "open('result.txt', 'w').write(str(input * 2))"}
      nightly:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
      job:
        type: logic.python
        config: {inputs: [], code: "output = 'nightly'"}
      weekly:
        type: trigger.schedule
        config: {cron: "0 3 * * 1"}
      digest:
        type: logic.python
        config: {inputs: [], outputs: [], code: "pass"}
    edges:
      - manual.start -> load.after
      - load.output -> compute.input
      - nightly.start -> job.after
      - weekly.start -> digest.after
"""


def test_rerunning_a_block_uses_the_run_its_result_comes_from(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "rerun.yaml", RERUN_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    manual = wait_runs(qtbot, tab, 1)
    start_from(win, tab, "nightly")  # the latest run of the flow, but compute took no part in it
    nightly = wait_runs(qtbot, tab, 2)
    (tmp_path / "result.txt").unlink()

    for block, run_id in (("compute", manual), ("job", nightly)):
        same = [a for a in win._block_menu_for(tab, block).actions() if "with the same inputs" in a.text()]
        assert [a.text().split(" (")[0] for a in same] == ["Run only this block, with the same inputs",
                                                            "Run from this block on, with the same inputs"]
        assert all(f"(run #{run_id} · " in a.text() and a.isEnabled() for a in same), [a.text() for a in same]
    same = [a for a in win._block_menu_for(tab, "digest").actions() if "with the same inputs" in a.text()]
    assert len(same) == 2 and not any(a.isEnabled() for a in same)  # its line never ran: no result to rerun

    menu_action(win, tab, "compute", "Run only this block, with the same inputs").trigger()
    rerun = wait_runs(qtbot, tab, 3)

    assert tab.runs[-1].status == "success" and (tmp_path / "result.txt").read_text() == "42"
    assert shown(tab, ["load", "compute", "job"]) == {"load": ("success", manual), "compute": ("success", rerun),
                                                      "job": ("success", nightly)}


def test_choosing_inputs_starts_from_the_run_the_block_shows(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "choose.yaml", RERUN_FLOW)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)
    start_from(win, tab, "nightly")
    wait_runs(qtbot, tab, 2)
    (tmp_path / "result.txt").unlink()
    filled = {}

    def answer(run):
        def exec_(dialog):
            filled.clear()
            filled.update({name: field.text() for name, field in dialog.fields.items()})
            return run
        return exec_

    # A block added since: it shows no result; it is filled in from the run its input (load) shows.
    fresh = tab.doc.add_block("logic.python", 600, 300, {"inputs": ["input"], "outputs": [], "code": "pass"})
    tab.doc.connect("load", "output", fresh, "input")
    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer(False))
    win.run_block_with_inputs(tab, fresh)
    assert filled == {"input": "21"}

    monkeypatch.setattr(dialogs.ChooseInputsDialog, "exec", answer(True))  # Run, as filled in
    win.run_block_with_inputs(tab, "compute")
    wait_runs(qtbot, tab, 3)

    assert filled == {"input": "21"}
    assert tab.runs[-1].status == "success" and (tmp_path / "result.txt").read_text() == "42"


def test_a_blocks_earlier_results_with_when_and_what_started_them(win, qtbot, tmp_path, monkeypatch):
    flow = write_flow(tmp_path / "results.yaml", """
        params:
          n: {type: int, value: 1}
        blocks:
          manual:
            type: trigger.manual
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          weekly:
            type: trigger.schedule
            config: {cron: "0 3 * * 1"}
          make:
            type: logic.python
            config:
              inputs: []
              code: |
                if params['n'] == 3:
                    raise ValueError('three is not allowed')
                output = {'run': params['n']}
          digest:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> make.after
          - nightly.start -> make.after
          - weekly.start -> digest.after
    """)
    tab = win.open_flow(flow)
    first = run_elsewhere(win, flow, "--param", "n=1")
    second = run_elsewhere(win, flow, "--trigger", "nightly", "--param", "n=2")
    failed = run_elsewhere(win, flow, "--trigger", "nightly", "--param", "n=3")
    run_elsewhere(win, flow, "--trigger", "weekly", "--param", "n=4")  # make takes no part
    win._refresh_statuses()
    assert tab.scene.nodes["digest"].status == "success"  # from the weekly run
    history = History(win.home.history_db)
    started = {r: history.run(r)["started"].replace("T", " ")[:19] for r in (first, second, failed)}
    made = {r: history.blocks(r)["make"] for r in (first, second, failed)}
    history.close()

    def look(dialog):
        table = results_table(dialog)
        rows = [[table.item(r, c).text() for c in range(len(RESULT_COLUMNS))] for r in range(table.rowCount())]
        assert rows == [[str(failed), started[failed], "nightly", "failed", made[failed]["error"]],
                        [str(second), started[second], "nightly", "success", made[second]["summary"]],
                        [str(first), started[first], "manual", "success", made[first]["summary"]]]
        assert all(re.fullmatch(WHEN, row[1]) for row in rows)
        click_row(table, 2)  # the first run: its values, below the table
        assert '"run": 1' in text_besides(dialog, table)
        click_row(table, 1)
        assert '"run": 2' in text_besides(dialog, table) and '"run": 1' not in text_besides(dialog, table)
        click_row(table, 2)
        show = [b for b in dialog.findChildren(QAbstractButton) if b.text().replace("&", "") == "Show this run"]
        assert len(show) == 1, [b.text() for b in dialog.findChildren(QAbstractButton)]
        show[0].click()

    in_block_results(win, tab, "make", monkeypatch, look)

    assert shown(tab) == {"manual": ("success", first), "make": ("success", first), "nightly": (None, None),
                          "weekly": (None, None), "digest": (None, None)}  # only that run
    assert '"run": 1' in tab.scene.nodes["make"].ports[("output", True)].toolTip()

    in_block_results(win, tab, "make", monkeypatch, lambda dialog: click_row(results_table(dialog), 1, double=True))

    assert shown(tab) == {"manual": (None, None), "make": ("success", second), "nightly": ("success", second),
                          "weekly": (None, None), "digest": (None, None)}


def test_each_lines_latest_result_is_on_the_flows_tab(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "status.yaml", """
        blocks:
          manual:
            type: trigger.manual
          good:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          bad:
            type: logic.python
            config: {inputs: [], outputs: [], code: "import os; assert os.path.exists('fixed.txt'), 'not fixed'"}
        edges:
          - manual.start -> good.after
          - nightly.start -> bad.after
    """)
    tab = win.open_flow(flow)
    assert tab_icon_is(win, tab, None)  # nothing ran yet: the hollow dot
    start_from(win, tab, "nightly")
    wait_runs(qtbot, tab, 1)  # the nightly line fails
    win.run_flow(tab)
    wait_runs(qtbot, tab, 2)  # the manual line succeeds: the latest run of the flow

    lines = tooltip_lines(win, tab)
    assert any(re.match(rf"manual: ok {WHEN}", line) for line in lines), lines
    assert any(re.match(rf"nightly: failed {WHEN}", line) for line in lines), lines
    assert tab_icon_is(win, tab, "failed")  # a line's latest result failed
    assert tab.scene.nodes["bad"].status == "failed" and tab.scene.nodes["good"].status == "success"

    (tmp_path / "fixed.txt").write_text("")
    menu_action(win, tab, "bad", "Run only this block, with the same inputs").trigger()
    wait_runs(qtbot, tab, 3)  # a rerun of a block counts for its line

    lines = tooltip_lines(win, tab)
    assert any(re.match(rf"manual: ok {WHEN}", line) for line in lines), lines
    assert any(re.match(rf"nightly: ok {WHEN}", line) for line in lines), lines
    assert tab_icon_is(win, tab, "success")


def test_blocks_done_while_running_counts_the_blocks_of_the_run(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "going.yaml", """
        blocks:
          manual:
            type: trigger.manual
          quick:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          slow:
            type: logic.python
            config:
              inputs: []
              outputs: []
              code: |
                import os, time
                while not os.path.exists('go.txt'):
                    time.sleep(0.05)
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          job:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> quick.after
          - quick.done -> slow.after
          - nightly.start -> job.after
    """)
    tab = win.open_flow(flow)
    start_from(win, tab, "nightly")
    wait_runs(qtbot, tab, 1)  # the other line has its results on the canvas
    win.run_flow(tab)
    try:
        qtbot.waitUntil(lambda: tab.scene.nodes["slow"].status == "running", timeout=60_000)

        tip = win.tabs.tabToolTip(win.tabs.indexOf(tab))
        assert "2 of 3 blocks done" in tip, tip  # manual and quick, of manual, quick and slow
    finally:
        (tmp_path / "go.txt").write_text("")  # slow finishes: no run is left going
        wait_runs(qtbot, tab, 2)


def test_a_block_connected_to_no_start_block_is_marked_off(win, qtbot, tmp_path):
    flow = write_flow(tmp_path / "off.yaml", """
        blocks:
          manual:
            type: trigger.manual
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
          spare:
            type: logic.python
            config: {inputs: [], outputs: [], code: "pass"}
        edges:
          - manual.start -> work.after
    """)
    tab = win.open_flow(flow)
    assert under(tab, "spare") == OFF and under(tab, "work") == ""

    win.run_flow(tab)
    wait_runs(qtbot, tab, 1)

    assert under(tab, "spare") == OFF and "spare" not in tab.block_info  # not a run result
    assert tab.scene.nodes["work"].status == "success"
    tab.doc.connect("manual", "start", "spare", "after")  # now in the manual line
    assert "off" not in under(tab, "spare")
    tab.doc.dirty = False
