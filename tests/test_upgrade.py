"""Checking flows for what a version can fix, and fixing them after a backup."""

import yaml

FLOW = """
taskloom: 1
blocks:
  python:
    type: logic.python
    config: {inputs: [], outputs: [result], code: "result = 1", old_setting: 3}
  after:
    type: logic.python
    config: {code: "pass"}
  check:
    type: logic.if
    config: {condition: "True"}
edges:
  - python.output -> after.after
  - python.result -> after.after
  - python.result -> check.value
  - python.result -> check.value
"""


def test_check_finds_what_it_can_fix_and_fixes_it_after_a_backup(env):
    flow = env.write("flow.yaml", FLOW)

    checked = env.taskloom("check", flow)
    assert checked.returncode == 2
    assert "can fix: remove the connection python.output -> after.after: python has no output 'output'" in checked.stdout
    assert "can fix: remove the connection python.result -> check.value: it is listed twice" in checked.stdout
    assert "can fix: remove the setting 'old_setting' of python" in checked.stdout

    fixed = env.taskloom("check", flow, "--fix")
    assert fixed.returncode == 0, fixed.stdout + fixed.stderr
    (backup,) = (env.home / "backups").iterdir()
    assert yaml.safe_load(backup.read_text()) == yaml.safe_load(FLOW)  # the flow as it was
    data = yaml.safe_load(flow.read_text())
    assert data["edges"] == ["python.result -> after.after", "python.result -> check.value"]
    assert "old_setting" not in data["blocks"]["python"]["config"]
    assert env.taskloom("check", flow).stdout.strip().endswith("OK")
    assert env.taskloom("run", flow).returncode == 0


def test_what_needs_you_is_reported_and_left_alone(env):
    flow = env.write("flow.yaml", """
        blocks:
          q:
            type: db.run_query
            config: {connection: nowhere, sql: "select 1"}
    """)
    before = flow.read_text()

    result = env.taskloom("check", flow, "--fix")

    assert result.returncode == 2 and "needs you:" in result.stdout and "nowhere" in result.stdout
    assert flow.read_text() == before and not (env.home / "backups").exists()


def test_an_older_flow_format_is_updated_step_by_step(env, monkeypatch):
    from taskloom import flow as flow_module
    from taskloom import upgrade
    from taskloom.config import Home
    from taskloom.registry import discover

    monkeypatch.setattr(flow_module, "FORMAT_VERSION", 3)
    monkeypatch.setattr(upgrade, "FORMAT_VERSION", 3)
    monkeypatch.setattr(upgrade, "MIGRATIONS", {
        1: lambda d: {**d, "description": "step 1"},
        2: lambda d: {**d, "description": d["description"] + ", step 2"},
    })
    path = env.write("old.yaml", "taskloom: 1\nblocks: {}\n")
    home = Home(env.home)

    report = upgrade.check(path, discover(home.blocks_dir), home)

    assert report.fixes == ["update the flow format from 1 to 2", "update the flow format from 2 to 3"]
    assert report.fixed["taskloom"] == 3 and report.fixed["description"] == "step 1, step 2"


GREET_V2 = """
    from taskloom import Block, fields, ports

    class Greet(Block):
        type_id = "test.greet"
        title = "Greet"
        version = 2
        outputs = {"text": ports.Any()}
        config = {"message": fields.Text()}

        @classmethod
        def migrate(cls, config, old_version):
            return {"message": config.pop("greeting")} if old_version == 1 else config

        def run(self, ctx):
            return {"text": self.config.message}
"""


def test_a_block_saved_by_an_older_version_of_it_is_updated_not_stripped(env):
    env.write("home/blocks/greet.py", GREET_V2)
    flow = env.write("flow.yaml", """
        blocks:
          g:
            type: test.greet
            version: 1
            config: {greeting: hello}
    """)
    assert env.taskloom("run", flow).returncode == 0  # it runs as it is

    checked = env.taskloom("check", flow, "--fix")

    assert "can fix: update the settings of g from version 1 to 2" in checked.stdout, checked.stdout
    assert "remove the setting" not in checked.stdout
    data = yaml.safe_load(flow.read_text())
    assert data["blocks"]["g"] == {"type": "test.greet", "version": 2, "config": {"message": "hello"}}
    assert env.taskloom("run", flow).returncode == 0


def test_a_flow_format_without_an_update_step_is_reported_not_a_crash(env):
    odd = env.write("odd.yaml", "taskloom: 0\nblocks: {}\n")
    fine = env.write("fine.yaml", "blocks: {}\n")

    result = env.taskloom("check", odd, fine)

    assert result.returncode == 2 and "needs you:" in result.stdout and "format 0" in result.stdout
    assert f"{fine}: OK" in result.stdout  # the next flows are still checked


def test_a_repeated_connection_into_an_input_that_takes_several_is_left_alone(env):
    flow = env.write("flow.yaml", """
        blocks:
          p:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
          use:
            type: logic.python
            config: {inputs: [items], outputs: [], code: "open('got.txt', 'w').write(str(items))"}
        edges:
          - p.output -> use.items
          - p.output -> use.items
    """)
    assert env.taskloom("run", flow).returncode == 0
    assert (env.root / "got.txt").read_text() == "[1, 1]"  # the block gets both

    result = env.taskloom("check", flow)

    assert result.returncode == 0 and result.stdout.strip().endswith("OK"), result.stdout


def test_a_repeated_run_after_connection_is_left_alone(env):
    flow = env.write("flow.yaml", """
        blocks:
          a:
            type: logic.python
            config: {code: "pass"}
          b:
            type: logic.python
            config: {code: "pass"}
        edges:
          - a.done -> b.after
          - a.done -> b.after
    """)
    assert env.taskloom("validate", flow).returncode == 0

    assert env.taskloom("check", flow).returncode == 0  # valid, so nothing to fix
