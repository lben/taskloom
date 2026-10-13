"""Start blocks (Manual Start, Schedule), the `after` input of every block, and Inspect."""

import datetime as dt
import json

from taskloom.config import Home
from taskloom.history import History
from taskloom.scheduler import Scheduler

TWO_STARTS = """
    blocks:
      manual:
        type: trigger.manual
      nightly:
        type: trigger.schedule
        config: {cron: "0 2 * * *"}
      by_hand:
        type: logic.python
        config: {inputs: [], outputs: [], code: "open('by_hand.txt', 'w').write('x')"}
      by_schedule:
        type: logic.python
        config: {inputs: [], outputs: [], code: "open('by_schedule.txt', 'w').write('x')"}
    edges:
      - manual.start -> by_hand.after
      - nightly.start -> by_schedule.after
"""


def test_run_starts_from_manual_start_and_the_scheduler_from_its_schedule_block(env):
    flow = env.write("flow.yaml", TWO_STARTS)

    assert env.taskloom("run", flow).returncode == 0
    assert (env.root / "by_hand.txt").exists() and not (env.root / "by_schedule.txt").exists()

    (env.root / "by_hand.txt").unlink()
    assert env.taskloom("run", flow, "--trigger", "nightly").returncode == 0
    assert (env.root / "by_schedule.txt").exists() and not (env.root / "by_hand.txt").exists()
    run_id, _ = env.last_run()
    assert env.block_status(run_id)["manual"] == "skipped"


def test_run_now_starts_a_flow_that_only_has_a_schedule(env):
    flow = env.write("flow.yaml", """
        blocks:
          nightly:
            type: trigger.schedule
            config: {cron: "0 2 * * *"}
          work:
            type: logic.python
            config: {inputs: [], outputs: [], code: "open('done.txt', 'w').write('x')"}
        edges:
          - nightly.start -> work.after
    """)
    assert env.taskloom("run", flow).returncode == 0
    assert (env.root / "done.txt").exists()


def test_scheduler_runs_the_schedule_block_branch(env):
    flow = env.write("flow.yaml", TWO_STARTS.replace('"0 2 * * *"', '"* * * * *"'))
    home = Home(env.home)
    home.set_scheduled(flow, True)
    scheduler = Scheduler(home)
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(t0)
    scheduler.tick(t0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()

    assert (env.root / "by_schedule.txt").exists() and not (env.root / "by_hand.txt").exists()


def test_a_schedule_block_needs_a_time(env):
    flow = env.write("flow.yaml", """
        blocks:
          when:
            type: trigger.schedule
    """)
    result = env.taskloom("validate", flow)
    assert result.returncode == 2 and "needs either cron" in result.stderr


def test_inspect_shows_what_passes_through(env):
    flow = env.write("flow.yaml", """
        blocks:
          make:
            type: logic.python
            config: {inputs: [], code: "output = {'rows': 3, 'region': 'EU'}"}
          look:
            type: logic.inspect
            config: {message: "{len(value)} fields, region {value['region']}"}
          use:
            type: logic.python
            config: {outputs: [], code: "assert input['rows'] == 3"}
        edges:
          - make.output -> look.value
          - look.value -> use.input
    """)
    assert env.taskloom("run", flow).returncode == 0
    run_id, _ = env.last_run()
    history = History(env.home / "history.db")
    logs = {entry["block"]: entry["message"] for entry in history.logs(run_id) if entry["block"]}
    previews = history.blocks(run_id)["make"]["previews"]
    history.close()
    assert logs["look"] == "2 fields, region EU"
    assert json.loads(previews["output"]) == {"rows": 3, "region": "EU"}


def test_secrets_inside_outputs_never_reach_previews_logs_or_output(env):
    env.write("home/secrets.yaml", "db_pw: hunter2SECRET\n").chmod(0o600)
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Password?", kind: password, secret: db_pw}
          wrap:
            type: logic.python
            config: {code: "output = {'user': 'bob', 'password': input, 'more': [input]}"}
          look:
            type: logic.inspect
        edges:
          - ask.answer -> wrap.input
          - wrap.output -> look.value
    """)
    result = env.taskloom("run", flow, "--json-events")

    assert result.returncode == 0, result.stderr
    assert "hunter2SECRET" not in result.stdout + result.stderr
    stored = env.query("SELECT group_concat(coalesce(previews, '') || coalesce(summary, ''), ' ') FROM blocks")[0][0]
    stored += env.query("SELECT group_concat(message, ' ') FROM logs")[0][0]
    assert "hunter2SECRET" not in stored and '"password": "***"' in stored
