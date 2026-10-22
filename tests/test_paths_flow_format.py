"""Flow files and checking them, through their real paths: `taskloom validate`, `taskloom run` and `taskloom check`
on real flow files (and the editor's Check flows). Every mistake a flow file can have and what Taskloom says about
it, the kinds of block settings, parameters in settings, the examples folder and the README's flow snippets, and
what Check fixes (after a backup of the file as it was)."""

import json
import re
import shutil
import textwrap

import polars as pl
import pytest
import yaml

from conftest import REPO


def write(env, text: str, name: str = "flow.yaml"):
    """A file under the test folder (a flow, or home/...), written as Taskloom reads it: UTF-8."""
    path = env.root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def runs_recorded(env) -> int:
    return env.query("SELECT count(*) FROM runs")[0][0] if (env.home / "history.db").exists() else 0


def errors(result) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.startswith("error: ")]


def refused(result) -> bool:
    return result.returncode == 2 and "Traceback" not in result.stderr and bool(errors(result))


# --- the shape of a flow file ------------------------------------------------------------------------------------

WAIT = "{type: logic.wait, config: {seconds: 0}"
WRONG_SHAPES = {
    "blocks as a list": ("blocks:\n  - load\n  - save\n", "blocks"),
    "params as a list": ("params:\n  - day\nblocks: {}\n", "params"),
    "triggers as one schedule": ("triggers:\n  schedule: {cron: '0 7 * * *'}\nblocks: {}\n", "triggers"),
    "a block's config as a list": ("blocks:\n  a: {type: logic.wait, config: [seconds, 1]}\n", "config"),
    "retry as a word": (f"blocks:\n  a: {WAIT}, retry: fixed}}\n", "retry"),
    "max_attempts as a word": (f"blocks:\n  a: {WAIT}, retry: {{policy: fixed, max_attempts: three}}}}\n",
                               "max_attempts"),
    "a block version as a word": (f"blocks:\n  a: {WAIT}, version: two}}\n", "version"),
}


@pytest.mark.parametrize("text, named", list(WRONG_SHAPES.values()), ids=list(WRONG_SHAPES))
def test_a_section_or_setting_of_the_wrong_shape_is_refused_naming_it_not_a_crash(env, text, named):
    flow = write(env, text)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert refused(result), result.stderr
        assert any(named in line for line in errors(result)), result.stderr  # says which part is wrong
    assert runs_recorded(env) == 0


DUPLICATES = {
    "a block listed twice": ("""
        blocks:
          start: {type: trigger.manual}
          load: {type: logic.python, config: {inputs: [], outputs: [], code: "open('first.txt', 'w').write('1')"}}
          load: {type: logic.python, config: {inputs: [], outputs: [], code: "open('second.txt', 'w').write('2')"}}
        edges:
          - start.start -> load.after
    """, "load"),
    "the edges written twice": ("""
        blocks:
          start: {type: trigger.manual}
          a: {type: logic.python, config: {inputs: [], outputs: [], code: "open('a.txt', 'w').write('a')"}}
          b: {type: logic.python, config: {inputs: [], outputs: [], code: "open('b.txt', 'w').write('b')"}}
        edges:
          - start.start -> a.after
        edges:
          - a.done -> b.after
    """, "edges"),
}


@pytest.mark.parametrize("text, named", list(DUPLICATES.values()), ids=list(DUPLICATES))
def test_a_name_written_twice_is_refused_instead_of_one_of_them_being_dropped(env, text, named):
    # A hand-edited flow: a block copied without renaming it, or a second edges: section added at the end.
    flow = write(env, text)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert refused(result), (command, result.stdout, result.stderr)
        assert any(named in line for line in errors(result)), result.stderr
    assert runs_recorded(env) == 0


CHECK_DUPLICATES = {  # each with a setting the block no longer has, so --fix writes the file again
    "the edges written twice": ("""
        blocks:
          start: {type: trigger.manual}
          a: {type: logic.wait, config: {seconds: 0}}
          b: {type: logic.wait, config: {seconds: 0, old_setting: 1}}
        edges:
          - start.start -> a.after
        edges:
          - a.done -> b.after
    """, "edges", "start.start -> a.after"),
    "a block listed twice": ("""
        blocks:
          start: {type: trigger.manual}
          load: {type: logic.python, config: {inputs: [], outputs: [], code: "rows = 3"}}
          load: {type: logic.wait, config: {seconds: 0, old_setting: 1}}
        edges:
          - start.start -> load.after
    """, "load", "rows = 3"),
}


@pytest.mark.parametrize("text, named, first", list(CHECK_DUPLICATES.values()), ids=list(CHECK_DUPLICATES))
def test_check_reports_a_name_written_twice_and_fixing_does_not_drop_the_first_one(env, text, named, first):
    flow = write(env, text)

    checked = env.taskloom("check", flow)
    fixed = env.taskloom("check", flow, "--fix")

    assert "Traceback" not in checked.stdout + checked.stderr + fixed.stdout + fixed.stderr, fixed.stderr
    assert checked.returncode == 2 and any(line.startswith("  needs you: ") and named in line
                                           for line in checked.stdout.splitlines()), checked.stdout
    assert fixed.returncode == 2, fixed.stdout
    assert first in flow.read_text(encoding="utf-8"), flow.read_text(encoding="utf-8")  # not only in the backup


def test_the_format_version_written_as_text_is_not_called_unsupported_or_newer(env):
    flow = write(env, 'taskloom: "1"\nblocks: {}\n')

    validated, checked = env.taskloom("validate", flow), env.taskloom("check", flow)

    assert "Traceback" not in validated.stderr + checked.stderr
    # Format 1 is the one this version reads: it may be accepted, or refused for being text, but not this.
    assert "unsupported flow format taskloom: 1 (this version reads 1)" not in validated.stderr, validated.stderr
    assert "newer Taskloom" not in checked.stdout, checked.stdout


@pytest.mark.parametrize("name", ["yes", "off"])
def test_a_block_named_yes_or_off_runs_or_is_refused_never_a_crash(env, name):
    # Unquoted, YAML reads these names as true / false.
    flow = write(env, f"""
        blocks:
          {name}:
            type: logic.python
            config: {{inputs: [], outputs: [], code: "open('ran.txt', 'w').write('ran')"}}
    """)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert "Traceback" not in validated.stderr + ran.stderr, ran.stderr
    assert (validated.returncode, ran.returncode) in ((0, 0), (2, 2)), (validated.stderr, ran.stderr)
    if ran.returncode == 0:
        assert (env.root / "ran.txt").read_text(encoding="utf-8") == "ran"


def test_if_branches_named_yes_and_no_run_or_are_refused_saying_why(env):
    flow = write(env, """
        blocks:
          start: {type: trigger.manual}
          check: {type: logic.if, config: {condition: "True"}}
          yes: {type: logic.python, config: {outputs: [], code: "open('yes.txt', 'w').write('y')"}}
          no: {type: logic.python, config: {outputs: [], code: "open('no.txt', 'w').write('n')"}}
        edges:
          - start.start -> check.after
          - check.true -> yes.input
          - check.false -> no.input
    """)

    result = env.taskloom("run", flow)

    assert "Traceback" not in result.stderr
    if result.returncode == 0:
        assert (env.root / "yes.txt").exists() and not (env.root / "no.txt").exists()
    else:  # the blocks are right there: "unknown block" sends you looking for one that is not
        assert refused(result) and "unknown block" not in result.stderr, result.stderr


@pytest.mark.parametrize("name", ["1st", "naïve", "two words"])
def test_a_block_name_taskloom_cannot_use_is_refused_naming_it(env, name):
    flow = write(env, f"blocks:\n  {name}: {WAIT}}}\n")

    result = env.taskloom("validate", flow)

    assert refused(result) and f"error: blocks.{name}: not a valid block id" in result.stderr, result.stderr


CONNECTION_MISTAKES = {
    "into a start block": ("blocks:\n  s: {type: trigger.manual}\n  t: {type: trigger.manual}\n"
                           "edges:\n  - s.start -> t.after\n", "'t' has no input 'after'"),
    "from an output a block does not have": (f"blocks:\n  a: {WAIT}}}\n  b: {WAIT}}}\n"
                                             "edges:\n  - a.start -> b.value\n", "'a' has no output 'start'"),
    "into a block's error output": (f"blocks:\n  a: {WAIT}}}\n  b: {WAIT}}}\nedges:\n  - a.value -> b.error\n",
                                    "'b' has no input 'error'"),
    "written as a mapping": (f"blocks:\n  a: {WAIT}}}\n  b: {WAIT}}}\nedges:\n  - {{a.value: b.value}}\n",
                             "expected 'block.port -> block.port'"),
    "a block after itself": (f"blocks:\n  a: {WAIT}}}\nedges:\n  - a.done -> a.after\n", "cycle through: a"),
}


@pytest.mark.parametrize("text, problem", list(CONNECTION_MISTAKES.values()), ids=list(CONNECTION_MISTAKES))
def test_a_connection_line_that_cannot_work_is_refused_saying_what_is_wrong(env, text, problem):
    flow = write(env, text)

    result = env.taskloom("validate", flow)

    assert refused(result) and problem in result.stderr, result.stderr


def test_a_cycle_is_reported_through_the_blocks_in_it_not_the_blocks_after_it(env):
    flow = write(env, f"""
        blocks:
          start: {{type: trigger.manual}}
          fetch: {WAIT}}}
          parse: {WAIT}}}
          report: {WAIT}}}
        edges:
          - start.start -> fetch.after
          - fetch.done -> parse.after
          - parse.done -> fetch.after
          - parse.value -> report.value
    """)

    result = env.taskloom("validate", flow)

    assert refused(result)
    [line] = [line for line in errors(result) if "cycle" in line]
    assert "fetch" in line and "parse" in line
    assert "report" not in line, line  # report comes after the cycle; it is not part of it


SCHEDULE_MISTAKES = {  # the timing, and what the error says (any one of them)
    "an hour that does not exist": ('{cron: "0 25 * * *"}', ["hour"]),
    "a cron line of four fields": ('{cron: "0 7 * *"}', ["5 fields"]),
    "a date written day first": ('{at: "08/10/2026 15:30"}', ["08/10/2026 15:30", "2026-10-08 15:30"]),
    "both cron and at": ('{cron: "0 7 * * *", at: "2026-10-08 15:30"}', ["cron"]),
    "a misfire that does not exist": ('{cron: "0 7 * * *", misfire: always}', ["misfire"]),
}


@pytest.mark.parametrize("place", ["triggers", "a Schedule block"])
@pytest.mark.parametrize("timing, says", list(SCHEDULE_MISTAKES.values()), ids=list(SCHEDULE_MISTAKES))
def test_a_schedule_that_cannot_be_read_is_refused_naming_where_it_is_and_what_is_wrong(env, place, timing, says):
    if place == "triggers":
        named, flow = "triggers[0]", write(env, f"""
            triggers:
              - schedule: {timing}
            blocks:
              start: {{type: trigger.manual}}
              a: {WAIT}}}
            edges:
              - start.start -> a.after
        """)
    else:
        named, flow = "blocks.nightly", write(env, f"""
            blocks:
              nightly: {{type: trigger.schedule, config: {timing}}}
              a: {WAIT}}}
            edges:
              - nightly.start -> a.after
        """)

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert refused(result), result.stderr
        assert any(line.startswith(f"error: {named}: ") and any(s in line for s in says) for line in errors(result)), \
            result.stderr
    assert runs_recorded(env) == 0


# --- block settings of every kind --------------------------------------------------------------------------------

KINDS_BLOCK = """
    import json
    from taskloom import Block, fields

    class Kinds(Block):
        type_id = "test.kinds"
        title = "Kinds"
        description = "Writes the settings it got to <its name>.json."
        config = {
            "label": fields.Text(),
            "text": fields.Text(default=""),
            "count": fields.Int(default=1, minimum=1),
            "ratio": fields.Float(default=0.5),
            "flag": fields.Bool(default=False),
            "mode": fields.Choice(["fast", "slow"], default="fast"),
            "items": fields.List(default=[]),
            "names": fields.Names(default=[]),
            "outs": fields.Names(default=[], outputs=True),
            "code": fields.Code("python", default=""),
            "query": fields.Code("sql", default="select 1"),
            "where": fields.Path(default="out.txt"),
            "server": fields.Connection(kind="ssh", required=False),
        }

        def run(self, ctx):
            with open(f"{ctx.block_id}.json", "w", encoding="utf-8") as f:
                json.dump(vars(self.config), f)
"""
CONNECTIONS = """
    appserver: {kind: ssh, host: 127.0.0.1, user: me}
    mailer: {kind: smtp, host: 127.0.0.1, port: 25, sender: taskloom@example.com}
"""


def kinds_flow(env, settings: str):
    write(env, KINDS_BLOCK, "home/blocks/kinds.py")
    write(env, CONNECTIONS, "home/connections.yaml")
    return write(env, f"""
        params:
          day: {{type: date, value: 2026-10-02}}
        blocks:
          k: {{type: test.kinds, config: {{{settings}}}}}
    """)


WRONG_KINDS = {
    "whole number: a fraction": ("label: x, count: 2.5", "count", "expected an integer, got 2.5"),
    "whole number: true": ("label: x, count: true", "count", "expected an integer, got True"),
    "whole number: a word": ("label: x, count: many", "count", "'many'"),
    "whole number: below its minimum": ("label: x, count: 0", "count", "expected 1 or more, got 0"),
    "number: true": ("label: x, ratio: true", "ratio", "expected a number, got True"),
    "number: a list": ("label: x, ratio: [1]", "ratio", "expected a number"),
    "number: a word": ("label: x, ratio: soon", "ratio", "'soon'"),
    "true or false: quoted": ("label: x, flag: 'yes'", "flag", "expected true or false, got 'yes'"),
    "true or false: a number": ("label: x, flag: 1", "flag", "expected true or false, got 1"),
    "choice: not one of them": ("label: x, mode: medium", "mode", "expected one of ['fast', 'slow'], got 'medium'"),
    "list: one value": ("label: x, items: one", "items", "expected a list, got 'one'"),
    "names: not a name": ("label: x, names: [my-input]", "names", "expected a list of names"),
    "names: one name, not a list": ("label: x, names: a", "names", "expected a list of names"),
    "names: twice the same": ("label: x, names: [a, a]", "names", "duplicate names"),
    "names: after": ("label: x, names: [after]", "names", "every block already has these"),
    "output names: done": ("label: x, outs: [done]", "outs", "every block already has these"),
    "output names: error": ("label: x, outs: [error]", "outs", "every block already has these"),
    "connection: not in connections.yaml": ("label: x, server: nowhere", "server",
                                            "no connection named 'nowhere' in connections.yaml"),
    "connection: of another kind": ("label: x, server: mailer", "server", "connection 'mailer' is smtp, expected ssh"),
    "text: an unknown parameter": ("label: '{missing}'", "label", "unknown parameter {missing}"),
    "text: a lone brace": ("label: 'a { b'", "label", "write {{ and }} for literal braces"),
    "path: an unknown parameter": ("label: x, where: 'out/{day:%Y}/{nope}.txt'", "where", "unknown parameter {nope}"),
    "sql: an unknown bind parameter": ("label: x, query: 'select :nope'", "query", "unknown bind parameter :nope"),
    "sql: an unknown parameter": ("label: x, query: 'select {nope}'", "query", "unknown parameter {nope}"),
    "required: left out": ("text: x", "label", "config.label is required"),
}


@pytest.mark.parametrize("settings, name, problem", list(WRONG_KINDS.values()), ids=list(WRONG_KINDS))
def test_a_setting_of_the_wrong_kind_is_refused_naming_the_block_the_setting_and_the_problem(env, settings, name,
                                                                                            problem):
    flow = kinds_flow(env, settings)

    result = env.taskloom("validate", flow)

    assert refused(result), result.stderr
    assert any("blocks.k" in line and f"config.{name}" in line and problem in line for line in errors(result)), \
        result.stderr


def test_each_kind_of_setting_reaches_the_block_as_written_and_one_left_out_gets_its_default(env):
    flow = kinds_flow(env, "label: 'Día {day:%d}', text: '{{literal}}', count: 3, ratio: 2, flag: true, mode: slow, "
                           "items: [1, a], names: [x_1], outs: [result], code: pass, query: 'select :day', "
                           "where: 'out/{day:%Y}/r.txt', server: appserver")
    flow.write_text(flow.read_text(encoding="utf-8") + "  plain: {type: test.kinds, config: {label: only}}\n",
                    encoding="utf-8")

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert json.loads((env.root / "k.json").read_text(encoding="utf-8")) == {
        "label": "Día 02", "text": "{literal}", "count": 3, "ratio": 2.0, "flag": True, "mode": "slow",
        "items": ["1", "a"], "names": ["x_1"], "outs": ["result"], "code": "pass", "query": "select :day",
        "where": "out/2026/r.txt", "server": "appserver"}
    assert json.loads((env.root / "plain.json").read_text(encoding="utf-8")) == {
        "label": "only", "text": "", "count": 1, "ratio": 0.5, "flag": False, "mode": "fast", "items": [],
        "names": [], "outs": [], "code": "", "query": "select 1", "where": "out.txt", "server": None}


# --- parameters in settings ---------------------------------------------------------------------------------------

TEMPLATED = """
    params:
      day: {type: date, value: 2026-10-02}
      region: {type: text, value: São Paulo}
      n: {type: int, value: 7}
    blocks:
      start: {type: trigger.manual}
      make: {type: logic.python, config: {inputs: [], code: "output = pl.DataFrame({'a': [1]})"}}
      save: {type: data.write_file, config: {path: "PATH"}}
    edges:
      - start.start -> make.after
      - make.output -> save.table
"""


def test_parameters_fill_a_setting_with_their_format_and_double_braces_stay_braces(env):
    flow = write(env, TEMPLATED.replace("PATH", "out/{region} {day:%Y-%m-%d}_{{v}}_{n:03d}.csv"))

    assert env.taskloom("validate", flow).returncode == 0
    first = env.taskloom("run", flow)
    later = env.taskloom("run", flow, "--param", "day=2026-10-05", "--param", "n=12")

    assert first.returncode == 0 and later.returncode == 0, first.stderr + later.stderr
    written = sorted(p.name for p in (env.root / "out").iterdir())
    assert written == ["São Paulo 2026-10-02_{v}_007.csv", "São Paulo 2026-10-05_{v}_012.csv"]
    assert pl.read_csv(env.root / "out" / written[0]).to_dicts() == [{"a": 1}]


def test_empty_braces_in_a_setting_are_refused_before_anything_runs(env):
    # {0} is refused as an unknown parameter; {} is no parameter either.
    flow = write(env, TEMPLATED.replace("PATH", "out/{}.csv"))

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert refused(validated) and "config.path" in validated.stderr, validated.stdout + validated.stderr
    assert refused(ran) and runs_recorded(env) == 0, ran.stderr  # refused before a run starts, like {0}


def test_every_duration_the_error_message_suggests_works_as_a_timeout_and_a_retry_delay(env):
    flow = write(env, "blocks:\n  a: " + WAIT + ", timeout: soon}\n")
    message = env.taskloom("validate", flow).stderr
    suggested = re.search(r"\(examples: ([^)]*)\)", message).group(1).split(", ")
    assert suggested  # e.g. 30, 500ms, 30s, 10m, 2h
    blocks = {f"b{i}": {"type": "logic.wait", "config": {"seconds": 0}, "timeout": text,
                        "retry": {"policy": "fixed", "delay": text, "max_attempts": 2}}
              for i, text in enumerate(suggested)}
    flow = write(env, json.dumps({"blocks": blocks}))  # JSON is YAML

    assert env.taskloom("validate", flow).returncode == 0
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    assert set(env.block_status(run_id).values()) == {"success"}


# --- what users read: the README's flow snippets and the examples folder ------------------------------------------

def test_the_flow_schedule_notify_and_connection_snippets_of_the_readme_are_a_valid_flow(env):
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    snippets = re.findall(r"```yaml\n(.*?)```", readme, re.S)
    [flow_text] = [s for s in snippets if s.startswith("params:")]
    [notify] = [s for s in snippets if s.startswith("notify:")]
    [schedules] = [s for s in snippets if s.startswith("triggers:")]
    [connections] = [s for s in snippets if s.startswith("# connections.yaml")]
    write(env, connections, "home/connections.yaml")
    assert "overlap: skip" in schedules

    for overlap in ("skip", "queue", "allow"):  # "skip | queue | allow"
        flow = write(env, flow_text + notify + schedules.replace("overlap: skip", f"overlap: {overlap}"))
        result = env.taskloom("validate", flow)
        assert result.returncode == 0 and "OK (2 blocks)" in result.stdout, result.stderr
    flow = write(env, flow_text + notify + schedules.replace("overlap: skip", "overlap: sometimes"))
    result = env.taskloom("validate", flow)
    assert refused(result) and "overlap must be one of skip, queue, allow" in result.stderr, result.stderr


EXAMPLES = sorted((REPO / "examples").glob("*.yaml"))


@pytest.mark.parametrize("example", EXAMPLES, ids=[p.name for p in EXAMPLES])
def test_each_example_is_valid_with_the_connections_its_comments_say_it_needs_and_has_nothing_to_fix(env, example):
    # e.g. '# Needs an SSH connection named "appserver"'
    needs = re.findall(r'an (SSH|SMTP|JDBC) connection named "(\w+)"', example.read_text(encoding="utf-8"))

    without = env.taskloom("validate", example)
    if needs:
        assert refused(without)
        for _, name in needs:
            assert f"no connection named '{name}' in connections.yaml" in without.stderr, without.stderr
    write(env, "".join(f"{name}: {{kind: {kind.lower()}, host: 127.0.0.1}}\n" for kind, name in needs),
          "home/connections.yaml")

    validated, checked = env.taskloom("validate", example), env.taskloom("check", example)

    assert validated.returncode == 0 and validated.stderr == "", validated.stderr
    assert checked.returncode == 0 and checked.stdout.strip() == f"{example}: OK", checked.stdout


def copy_daily_report(env):
    shutil.copytree(REPO / "examples" / "data", env.root / "data")
    flow = env.root / "daily-sales-report.yaml"
    shutil.copy(REPO / "examples" / "daily-sales-report.yaml", flow)
    return flow


@pytest.mark.parametrize("as_of, day", [("2026-10-05 08:00", "2026-10-02"), ("2026-10-07 08:00", "2026-10-06")],
                         ids=["on a Monday: the Friday", "midweek: the day before"])
def test_the_daily_sales_report_example_reports_the_previous_business_day(env, as_of, day):
    flow = copy_daily_report(env)

    result = env.taskloom("run", flow, "--as-of", as_of)

    assert result.returncode == 0, result.stderr
    sales = pl.read_csv(env.root / "data" / "sales.csv").filter((pl.col("day") == day) & (pl.col("region") == "EU"))
    out = pl.read_csv(env.root / "out" / f"sales_{day}.csv")
    assert out["day_key"].to_list() == [int(day.replace("-", ""))] and out["region"].to_list() == ["EU"]
    assert out["stores"].to_list() == [sales.height] and out["total"].to_list() == [round(sales["amount"].sum(), 2)]


def test_the_daily_sales_report_example_totals_the_region_its_parameter_names(env):
    flow = copy_daily_report(env)
    assert "Totals per region" in yaml.safe_load(flow.read_text(encoding="utf-8"))["description"]

    result = env.taskloom("run", flow, "--param", "day=2026-10-02", "--param", "region=US")

    assert result.returncode == 0, result.stderr
    sales = pl.read_csv(env.root / "data" / "sales.csv").filter((pl.col("day") == "2026-10-02")
                                                                & (pl.col("region") == "US"))
    assert sales.height > 0
    out = pl.read_csv(env.root / "out" / "sales_2026-10-02.csv")
    assert out.to_dicts() == [{"day_key": 20261002, "region": "US", "stores": sales.height,
                               "total": round(sales["amount"].sum(), 2)}]


# --- Check: what it fixes and what needs you ----------------------------------------------------------------------

def test_check_removes_connections_to_blocks_and_inputs_that_are_gone_and_says_what_still_needs_you(env):
    flow = write(env, """
        blocks:
          start: {type: trigger.manual}
          load: {type: logic.python, config: {inputs: [], outputs: [rows], code: "rows = 3"}}
          use: {type: logic.python, config: {inputs: [value], outputs: [], code: "open('use.txt', 'w').write(str(value))"}}
          save: {type: data.write_file, config: {path: out.csv}}
        edges:
          - start.start -> load.after
          - load.rows -> use.value
          - removed.output -> use.value
          - load.rows -> use.amount
          - load.table -> save.table
    """)
    original = flow.read_bytes()

    checked = env.taskloom("check", flow)
    assert checked.returncode == 2 and flow.read_bytes() == original
    assert checked.stdout.splitlines()[1:] == [
        "  can fix: remove the connection removed.output -> use.value: there is no block 'removed'",
        "  can fix: remove the connection load.rows -> use.amount: use has no input 'amount'",
        "  can fix: remove the connection load.table -> save.table: load has no output 'table'",
        "  needs you: blocks.save: required input 'table' is not connected",  # what is left once it is fixed
    ], checked.stdout

    fixed = env.taskloom("check", flow, "--fix")

    assert fixed.returncode == 2  # something still needs you
    [backup] = (env.home / "backups").iterdir()
    assert backup.read_bytes() == original and f"fixed (backup: {backup})" in fixed.stdout
    assert yaml.safe_load(flow.read_text(encoding="utf-8"))["edges"] == ["start.start -> load.after",
                                                                         "load.rows -> use.value"]
    again = env.taskloom("check", flow)
    assert "can fix" not in again.stdout and "needs you: blocks.save: required input 'table'" in again.stdout


NO_START = """
    # Written by hand, before flows had start blocks.
    name: Informe → ventas
    params:
      day: {type: date, value: 2026-10-02}
      tag: {type: text, expr: "format(day, '%d.%m')"}
    blocks:
      start:
        type: logic.python
        config:
          inputs: []
          code: |
            output = "café ✓"
      shout:
        type: logic.python
        config: {code: "output = input.upper()"}
      broken:
        type: logic.python
        config: {inputs: [], outputs: [], code: "1 / 0"}
      handler:
        type: logic.python
        config:
          inputs: [problem]
          outputs: []
          code: "open('handled.txt', 'w', encoding='utf-8').write(problem['error'])"
      write:
        type: logic.python
        config:
          inputs: [text]
          outputs: []
          code: "open(f'out_{params[\\"tag\\"]}.txt', 'w', encoding='utf-8').write(text)"
    edges:
      - start.output -> shout.input
      - shout.output -> write.text
      - broken.error -> handler.problem
"""


def outcome(env, folder) -> tuple[dict, dict]:
    """What the latest run left: the text files it wrote (removed after reading) and how each block ended."""
    files = {}
    for path in sorted(folder.glob("*.txt")):
        files[path.name] = path.read_text(encoding="utf-8")
        path.unlink()
    run_id, status = env.last_run()
    assert status == "success"
    return files, env.block_status(run_id)


def test_a_flow_fixed_by_check_runs_as_before_and_its_backup_is_the_file_as_it_was(env):
    flow = write(env, NO_START)
    original = flow.read_bytes()
    assert env.taskloom("run", flow).returncode == 0
    files_before, blocks_before = outcome(env, env.root)
    assert files_before == {"handled.txt": "ZeroDivisionError: division by zero (line 1)", "out_02.10.txt": "CAFÉ ✓"}

    fixed = env.taskloom("check", flow, "--fix")

    assert fixed.returncode == 0, fixed.stdout
    assert "can fix: add a Manual Start connected to its first blocks (start, broken)" in fixed.stdout
    [backup] = (env.home / "backups").iterdir()
    assert backup.read_bytes() == original  # comments and all
    data = yaml.safe_load(flow.read_text(encoding="utf-8"))
    added = [b for b in data["blocks"] if b not in yaml.safe_load(original.decode("utf-8"))["blocks"]]
    assert [data["blocks"][b]["type"] for b in added] == ["trigger.manual"]  # your block named start is kept
    assert data["blocks"]["start"]["config"]["code"] == 'output = "café ✓"\n' and data["name"] == "Informe → ventas"
    assert env.taskloom("run", flow).returncode == 0
    files_after, blocks_after = outcome(env, env.root)
    assert files_after == files_before
    assert blocks_after == {**blocks_before, added[0]: "success"}
    assert env.taskloom("check", flow).stdout.strip().endswith(": OK")


def test_a_flow_saved_by_windows_notepad_is_read_run_and_fixed_keeping_its_text(env):
    flow = env.root / "notepad.yaml"
    text = "name: Informe de café\r\nblocks:\r\n  a: {type: logic.python, config: {inputs: [], outputs: [], " \
           "code: \"open('a.txt', 'w', encoding='utf-8').write('añadido')\"}}\r\n"
    flow.write_bytes("﻿".encode("utf-8") + text.encode("utf-8"))  # UTF-8 with a BOM, Windows line ends
    original = flow.read_bytes()

    validated = env.taskloom("validate", flow)
    assert validated.returncode == 0 and "Informe de café: OK" in validated.stdout, validated.stderr
    fixed = env.taskloom("check", flow, "--fix")

    assert fixed.returncode == 0, fixed.stdout
    [backup] = (env.home / "backups").iterdir()
    assert backup.read_bytes() == original
    assert yaml.safe_load(flow.read_text(encoding="utf-8"))["name"] == "Informe de café"
    assert env.taskloom("run", flow).returncode == 0
    assert (env.root / "a.txt").read_text(encoding="utf-8") == "añadido"


def test_a_flow_file_saved_as_utf16_is_refused_naming_the_file(env):
    # Windows PowerShell writes UTF-16 (e.g. `Get-Content a.yaml > flow.yaml`); settings.yaml then says which file.
    flow = env.root / "flow.yaml"
    flow.write_text("name: café\nblocks: {}\n", encoding="utf-16")

    for command in ("validate", "run"):
        result = env.taskloom(command, flow)
        assert refused(result), result.stderr
        assert flow.name in result.stderr, result.stderr


def test_check_leaves_a_flow_made_by_a_newer_taskloom_as_it_is_and_says_to_update(env):
    flow = write(env, f"""
        taskloom: 2
        blocks:
          a: {WAIT}}}
        edges:
          - gone.value -> a.value
    """)
    original = flow.read_bytes()

    result = env.taskloom("check", flow, "--fix")

    assert result.returncode == 2
    assert "needs you: it was made by a newer Taskloom (flow format 2; this version knows 1): update Taskloom" \
        in result.stdout and "can fix" not in result.stdout, result.stdout
    assert flow.read_bytes() == original and not (env.home / "backups").exists()


GREET_V2 = """
    from taskloom import Block, fields, ports

    class Greet(Block):
        type_id = "test.greet"
        title = "Greet"
        description = "Says hello."
        version = 2
        outputs = {"text": ports.Any()}
        config = {"message": fields.Text()}

        @classmethod
        def migrate(cls, config, old_version):
            if old_version < 1:
                raise ValueError("settings from before version 1 cannot be read")
            return {"message": config.pop("greeting")}

        def run(self, ctx):
            return {"text": self.config.message}
"""


def test_check_keeps_the_settings_and_connections_of_blocks_it_cannot_judge(env):
    write(env, GREET_V2, "home/blocks/greet.py")
    flow = write(env, """
        blocks:
          start: {type: trigger.manual}
          old: {type: test.greet, version: 0, config: {greeting: hi, color: red}}
          newer: {type: logic.wait, version: 7, config: {seconds: 0, future_setting: 1}}
          unknown: {type: my.missing, config: {anything: 1}}
          use: {type: logic.python, config: {inputs: [a], outputs: [], code: pass}}
        edges:
          - start.start -> use.after
          - old.text -> use.a
          - old.later -> use.a
          - newer.future_output -> use.a
          - unknown.out -> use.a
          - gone.output -> use.a
    """)
    before = yaml.safe_load(flow.read_text(encoding="utf-8"))

    result = env.taskloom("check", flow, "--fix")

    assert result.returncode == 2
    [can_fix] = [line for line in result.stdout.splitlines() if line.startswith("  can fix: ")]
    assert can_fix == "  can fix: remove the connection gone.output -> use.a: there is no block 'gone'", result.stdout
    needs_you = [line for line in result.stdout.splitlines() if line.startswith("  needs you: ")]
    for block, problem in [("old", "settings from before version 1 cannot be read"),
                           ("newer", "saved with version 7 of logic.wait, but this install has version 1"),
                           ("unknown", "unknown block type 'my.missing'")]:
        assert any(block in line and problem in line for line in needs_you), (block, result.stdout)
    after = yaml.safe_load(flow.read_text(encoding="utf-8"))
    assert after["blocks"] == before["blocks"]  # their settings and versions as they were
    assert after["edges"] == [e for e in before["edges"] if not e.startswith("gone.")]


def test_check_keeps_the_connections_of_a_block_whose_settings_are_wrong_and_says_what_is_wrong(env):
    # Its inputs and outputs cannot be worked out until you fix its settings: its connections still exist.
    flow = write(env, """
        blocks:
          start: {type: trigger.manual}
          load: {type: logic.python, config: {inputs: [], outputs: [rows], code: "rows = 3"}}
          p: {type: logic.python, config: {inputs: [value, value], outputs: [total, total], code: "total = value"}}
          use: {type: logic.python, config: {inputs: [a], outputs: [], code: pass}}
        edges:
          - start.start -> load.after
          - load.rows -> p.value
          - p.total -> use.a
          - gone.output -> use.a
    """)
    before = yaml.safe_load(flow.read_text(encoding="utf-8"))

    result = env.taskloom("check", flow, "--fix")

    assert result.returncode == 2, result.stdout
    [can_fix] = [line for line in result.stdout.splitlines() if line.startswith("  can fix: ")]
    assert can_fix == "  can fix: remove the connection gone.output -> use.a: there is no block 'gone'", result.stdout
    assert any(line.startswith("  needs you: blocks.p: ") and "duplicate names" in line
               for line in result.stdout.splitlines()), result.stdout
    after = yaml.safe_load(flow.read_text(encoding="utf-8"))
    assert after["blocks"] == before["blocks"]
    assert after["edges"] == ["start.start -> load.after", "load.rows -> p.value", "p.total -> use.a"]


def test_check_says_why_a_block_type_is_unknown_when_its_block_file_did_not_load(env):
    write(env, "from taskloom import Block\n\nclass Greet(Block:\n", "home/blocks/greet.py")
    flow = write(env, """
        blocks:
          start: {type: trigger.manual}
          g: {type: my.greet, config: {greeting: Hola}}
        edges:
          - start.start -> g.after
    """)
    validated = env.taskloom("validate", flow)
    assert "could not load user block file" in validated.stderr and "greet.py" in validated.stderr  # as validate says

    result = env.taskloom("check", flow)

    assert result.returncode == 2 and "unknown block type 'my.greet'" in result.stdout
    assert "greet.py" in result.stdout + result.stderr, result.stdout + result.stderr


UNREADABLE = {
    "not YAML": b"blocks: [unclosed\n",
    "a list": b"- a\n- b\n",
    "empty": b"",
    "UTF-16": "blocks: {}\n".encode("utf-16"),
    "missing": None,
}


@pytest.mark.parametrize("content", list(UNREADABLE.values()), ids=list(UNREADABLE))
def test_check_reports_a_flow_it_cannot_read_and_goes_on_with_the_others(env, content):
    broken = env.root / "broken.yaml"
    if content is not None:
        broken.write_bytes(content)
    fine = write(env, f"blocks:\n  start: {{type: trigger.manual}}\n  a: {WAIT}}}\nedges:\n  - start.start -> a.after\n",
                 "fine.yaml")

    result = env.taskloom("check", broken, fine, "--fix")

    assert result.returncode == 2 and "Traceback" not in result.stdout + result.stderr, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == f"{broken}:" and lines[1].startswith("  needs you: "), result.stdout
    assert f"{fine}: OK" in result.stdout
    assert not (env.home / "backups").exists()
    assert (broken.read_bytes() == content) if content is not None else not broken.exists()


# --- the editor's Check flows -------------------------------------------------------------------------------------

def test_a_flow_fixed_by_check_flows_in_the_editor_runs_from_the_editor(win, qtbot, tmp_path, monkeypatch):
    from taskloom import __version__
    from taskloom.editor import dialogs
    from test_editor import wait_done

    win.settings.setValue("checked_version", __version__)  # not the check of a first start: this one is asked for
    flow = tmp_path / "leftover.yaml"
    flow.write_text(textwrap.dedent("""
        blocks:
          start: {type: trigger.manual}
          python:
            type: logic.python
            config: {inputs: [], outputs: [result], code: "result = 21", old_setting: 3}
          after:
            type: logic.python
            config: {inputs: [value], outputs: [], code: "open('result.txt', 'w').write(str(value * 2))"}
        edges:
          - start.start -> python.after
          - python.output -> after.value
          - python.result -> after.value
    """), encoding="utf-8")
    original = flow.read_bytes()
    tab = win.open_flow(flow)
    shown = []
    monkeypatch.setattr(dialogs.CheckFlowsDialog, "exec", lambda self: shown.append(self.details.toPlainText()) or True)

    win.check_flows()  # Tools -> Check flows..., then Back up and fix

    [details] = shown
    assert "can fix: remove the connection python.output -> after.value" in details
    assert "can fix: remove the setting 'old_setting' of python" in details
    [backup] = (win.home.root / "backups").iterdir()
    assert backup.read_bytes() == original
    [(kind, (title, text))] = win.message_boxes
    assert title == "Check flows" and "leftover.yaml (backup:" in text
    win.message_boxes.clear()
    assert tab.doc.edges() == [("start", "start", "python", "after"), ("python", "result", "after", "value")]
    assert not tab.doc.dirty

    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert tab.last["status"] == "success", win.message_boxes
    assert (tmp_path / "result.txt").read_text() == "42"
    assert yaml.safe_load(flow.read_text(encoding="utf-8"))["blocks"]["python"]["config"] == {
        "inputs": [], "outputs": ["result"], "code": "result = 21"}
