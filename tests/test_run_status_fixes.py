"""Three things seen in the 0.32.0 proof screenshots, through the real editor (offscreen) and the real `taskloom`
command with its run history (history.db). Each test names the point of the change it protects:

1. A flow's tab shows its run's real status: after Stop, cancelled (its own icon and colour, as the canvas and Run
   history show it), not the green success dot; failed as failed, success as success.
2. A Splitter connected only by run after (nothing at its value input) says plainly what its lines got (that they
   started with nothing), not "lines: None" as if None were a value.
3. Run only this block on a Splitter, where no line runs, says so plainly in the Log, not "0 lines …: " with nothing
   after the colon."""

import json
import os
import re

import pytest
import yaml

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from taskloom.editor.panels import status_icon  # noqa: E402
from tests.test_editor import wait_done  # noqa: E402
from tests.test_lines import start_from, tooltip_lines, wait_runs  # noqa: E402

MANUAL = {"type": "trigger.manual", "ui": {"x": -400, "y": 0}}
NIGHTLY = {"type": "trigger.schedule", "config": {"cron": "0 2 * * *"}, "ui": {"x": -400, "y": 400}}
WAITING = {"type": "logic.wait", "config": {"seconds": 120}}  # busy until stopped
SAME_TIME, ONE_AFTER_ANOTHER = "at the same time", "one after another"


def flow_file(path, blocks: dict, edges: list):
    path.write_text(yaml.safe_dump({"blocks": blocks, "edges": edges}, sort_keys=False), encoding="utf-8")
    return path


def python(code: str, inputs=("input",), outputs=("output",), x=400, y=0) -> dict:
    return {"type": "logic.python", "config": {"inputs": list(inputs), "outputs": list(outputs), "code": code},
            "ui": {"x": x, "y": y}}


def gated(gate: str) -> dict:
    """A block that waits until the file `gate` exists (next to the flow): busy until then, or until stopped."""
    return python(f"import os, time\nwhile not os.path.exists({gate!r}):\n    time.sleep(0.05)\n",
                  inputs=(), outputs=(), y=400)


def tab_shows(win, tab) -> str:
    """Which status the flow's tab icon shows (with or without the scheduled clock beside it)."""
    size = win.tabs.iconSize()
    shown = win.tabs.tabIcon(win.tabs.indexOf(tab)).pixmap(size).toImage()
    for status in ("success", "failed", "cancelled", "running", None):
        if any(shown == status_icon(status, clock=clock).pixmap(size).toImage() for clock in (False, True)):
            return str(status)
    return "an icon of no status"


# --- 1: the flow's tab shows the run's real status ------------------------------------------------------------------

def test_after_stop_the_flows_tab_shows_the_run_as_cancelled_not_as_success(win, qtbot, tmp_path):
    """Point 1, the user's case: a Manual Start flow is run, then stopped (Stop). The canvas and the run history say
    cancelled; the flow's tab must show cancelled too, not the green success dot."""
    path = flow_file(tmp_path / "stopped.yaml", {"manual": MANUAL, "work": WAITING}, ["manual.start -> work.after"])
    tab = win.open_flow(path)

    win.run_flow(tab)
    qtbot.waitUntil(lambda: tab.scene.nodes["work"].status == "running", timeout=60_000)
    win.stop_action.trigger()  # Stop (Shift+F5)
    wait_done(qtbot, tab, timeout=30_000)

    assert tab.last["status"] == "cancelled" and tab.scene.nodes["work"].status == "cancelled"
    assert tab_shows(win, tab) == "cancelled"  # (tab_shows tells it from success and failed: its own icon)


def test_a_lines_stopped_run_stays_on_the_tab_until_that_line_runs_again_and_succeeds(win, qtbot, tmp_path):
    """Point 1 with two start blocks (the tab shows each line's latest result, as a failed one already does): the
    nightly line stopped shows as cancelled, also after the manual line then succeeds (the tooltip says nightly:
    cancelled); once the nightly line runs again and succeeds, the tab shows success."""
    path = flow_file(tmp_path / "two_lines.yaml", {
        "manual": MANUAL, "quick": python("pass", inputs=(), outputs=()),
        "nightly": NIGHTLY, "slow": gated("go.txt"),
    }, ["manual.start -> quick.after", "nightly.start -> slow.after"])
    tab = win.open_flow(path)

    start_from(win, tab, "nightly")  # Run the flow now, as if its time had come
    qtbot.waitUntil(lambda: tab.scene.nodes["slow"].status == "running", timeout=60_000)
    win.stop_action.trigger()
    wait_runs(qtbot, tab, 1)
    assert tab.runs[-1].status == "cancelled"
    assert tab_shows(win, tab) == "cancelled"

    start_from(win, tab, "manual")  # the manual line succeeds: the flow's latest run
    wait_runs(qtbot, tab, 2)
    assert tab.runs[-1].status == "success"
    assert any(line.startswith("nightly: cancelled") for line in tooltip_lines(win, tab)), tooltip_lines(win, tab)
    assert tab_shows(win, tab) == "cancelled"

    (tmp_path / "go.txt").write_text("", encoding="utf-8")
    start_from(win, tab, "nightly")
    wait_runs(qtbot, tab, 3)
    assert tab.runs[-1].status == "success"
    assert tab_shows(win, tab) == "success"


# --- 2: a Splitter with nothing at its value input --------------------------------------------------------------------

def split_flow(folder, fed: bool):
    """manual -> (load -> split.value, or only split.after) -> lines a and b (each passes on repr of what it got) ->
    merge -> report (report.json)."""
    blocks = {"manual": MANUAL, "split": {"type": "logic.splitter", "ui": {"x": 0, "y": 0}},
              "a": python("output = repr(input)", y=-200), "b": python("output = repr(input)", y=200),
              "merge": {"type": "logic.merger", "ui": {"x": 800, "y": 0}},
              "report": python("import json\nwith open('report.json', 'w', encoding='utf-8') as f:\n"
                               "    json.dump(input, f)", outputs=(), x=1200)}
    edges = ["split.lines -> a.input", "split.lines -> b.input", "a.output -> merge.lines", "b.output -> merge.lines",
             "merge.value -> report.input"]
    if fed:
        blocks["load"] = python("output = 'today'", inputs=(), x=-200)
        edges += ["manual.start -> load.after", "load.output -> split.value"]
    else:
        edges += ["manual.start -> split.after"]
    return flow_file(folder / "split.yaml", blocks, edges)


def splitter_result(env, run_id: int) -> dict:
    [(status, summary)] = env.query("SELECT status, summary FROM blocks WHERE run_id=? AND block='split'", run_id)
    return {"status": status, "summary": summary or ""}


def test_a_splitter_with_nothing_at_its_value_says_under_it_that_its_lines_started_with_nothing(win, qtbot, tmp_path):
    """Point 2, the user's case on the canvas: a Splitter connected only by run after succeeds and its lines run
    (each got nothing, as before); under it, it says plainly that its lines started with nothing, not "lines: None"."""
    tab = win.open_flow(split_flow(tmp_path, fed=False))

    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert tab.last["status"] == "success"
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == {"a": "None", "b": "None"}
    node = tab.scene.nodes["split"]
    assert node.status == "success" and node.status_text.startswith("✓"), node.status_text
    assert "None" not in node.status_text and "nothing" in node.status_text.lower(), node.status_text


def test_the_run_history_says_the_same_of_a_splitter_with_nothing_at_its_value(env):
    """Point 2 where the result is read again (Results of this block, the Log's finished line, the emails all show the
    block's summary from the run history): no "None" shown as if it were a value; it says its lines got nothing."""
    result = env.taskloom("run", split_flow(env.root, fed=False))
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]

    split = splitter_result(env, env.last_run()[0])
    assert split["status"] == "success"
    assert "None" not in split["summary"] and "nothing" in split["summary"].lower(), split


def test_a_splitter_given_a_value_still_shows_that_value(env):
    """Protects point 2's limit (passes today): a Splitter that got a value still shows it, as before."""
    result = env.taskloom("run", split_flow(env.root, fed=True))
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]

    split = splitter_result(env, env.last_run()[0])
    assert split["status"] == "success" and "today" in split["summary"], split
    assert json.loads((env.root / "report.json").read_text(encoding="utf-8")) == {"a": "'today'", "b": "'today'"}


# --- 3: Run only this block on a Splitter -----------------------------------------------------------------------------

@pytest.mark.parametrize("lines_run", [SAME_TIME, ONE_AFTER_ANOTHER])
def test_run_only_the_splitter_says_plainly_in_the_log_that_no_line_runs(env, lines_run):
    """Point 3: Run only this block on a Splitter (`taskloom run flow --only split --inputs-from N`, as the editor's
    menu runs it) runs the Splitter alone; its Log says plainly that no line runs, not "0 lines <mode>: "
    with nothing after the colon. In either mode."""
    path = split_flow(env.root, fed=True)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["blocks"]["split"]["config"] = {"lines_run": lines_run}
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    first = env.taskloom("run", path)
    assert first.returncode == 0, first.stdout[-3000:] + first.stderr[-3000:]
    full_run = env.last_run()[0]

    only = env.taskloom("run", path, "--only", "split", "--inputs-from", full_run)

    assert only.returncode == 0, only.stdout[-3000:] + only.stderr[-3000:]
    only_run = env.last_run()[0]
    assert only_run != full_run and env.block_status(only_run) == {"split": "success"}
    said = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='split'", only_run)]
    assert not any(m.rstrip().endswith(":") or m.startswith("0 lines") for m in said), said
    assert any(re.search(r"\bno lines?\b|\bnone of (its|the) lines\b", m, re.IGNORECASE) for m in said), said
