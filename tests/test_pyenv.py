"""Python Code blocks running in your own Python environment, with packages installed by pip."""

import sys
import zipfile




def _tiny_wheel(folder):
    """A minimal installable package, so the test needs no internet."""
    wheel = folder / "tinypkg-1.0-py3-none-any.whl"
    files = {
        "tinypkg/__init__.py": "def greet(name):\n    return f'hello {name} from tinypkg'\n",
        "tinypkg-1.0.dist-info/METADATA": "Metadata-Version: 2.1\nName: tinypkg\nVersion: 1.0\n",
        "tinypkg-1.0.dist-info/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    with zipfile.ZipFile(wheel, "w") as z:
        for name, text in files.items():
            z.writestr(name, text)
        z.writestr("tinypkg-1.0.dist-info/RECORD", "".join(f"{n},,\n" for n in files) + "tinypkg-1.0.dist-info/RECORD,,\n")
    return wheel


def test_python_block_uses_packages_installed_in_your_environment(env):
    created = env.taskloom("env", "create", "--python", sys.executable, timeout=180)
    assert created.returncode == 0, created.stderr
    installed = env.taskloom("env", "pip", "install", "--no-index", str(_tiny_wheel(env.root)), timeout=180)
    assert installed.returncode == 0, installed.stdout + installed.stderr
    listed = env.taskloom("env", "pip", "list", timeout=180)  # pip's output reaches whoever ran it
    assert listed.returncode == 0 and "tinypkg" in listed.stdout, listed.stdout + listed.stderr

    flow = env.write("flow.yaml", """
        blocks:
          sales:
            type: data.duckdb_sql
            config: {inputs: [], sql: "select 'EU' as region, 10 as total"}
          greet:
            type: logic.python
            config:
              inputs: [table, who]
              outputs: [message, where]
              use_environment: true
              code: |
                import sys, tinypkg
                log("running in", sys.prefix)
                message = tinypkg.greet(who) + f" ({len(params)} params)"
                where = table  # this environment has no DataFrame library: tables arrive as a file path
          who:
            type: logic.python
            config: {inputs: [], code: "output = 'Ana'"}
          save:
            type: logic.python
            config: {inputs: [message, where], outputs: [], code: "open('out.txt', 'w').write(message + '|' + where)"}
        edges:
          - sales.result -> greet.table
          - who.output -> greet.who
          - greet.message -> save.message
          - greet.where -> save.where
    """)
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    message, where = (env.root / "out.txt").read_text().split("|")
    assert message == "hello Ana from tinypkg (0 params)" and where.endswith("result.parquet")
    run_id, _ = env.last_run()
    logs = " ".join(m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='greet'", run_id))
    assert "python-env" in logs


def test_a_python_block_needing_the_environment_says_how_to_create_it(env):
    flow = env.write("flow.yaml", """
        blocks:
          code:
            type: logic.python
            config: {inputs: [], code: "output = 1", use_environment: true}
    """)
    assert env.taskloom("run", flow).returncode == 1
    run_id, _ = env.last_run()
    assert "Tools → Python environment" in env.query("SELECT error FROM blocks WHERE run_id=?", run_id)[0][0]


def test_secrets_never_reach_logs_or_files_through_inspect_or_the_environment(env):
    import sys as _sys

    env.write("home/secrets.yaml", "db_pw: hunter2SECRET\n").chmod(0o600)
    assert env.taskloom("env", "create", "--python", _sys.executable, timeout=180).returncode == 0
    flow = env.write("flow.yaml", """
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "Password?", kind: password, secret: db_pw}
          message:
            type: logic.inspect
            config: {message: "password is {value}"}
          outside:
            type: logic.python
            config: {inputs: [pw], outputs: [n, same, built], use_environment: true,
                     code: "n = len(pw); same = pw; built = f'user:{pw}'"}
          inside:
            type: logic.python
            config: {inputs: [pw], outputs: [built, long_url, long_list], code: "built = 'user:' + pw; long_url = 'https://example.com/api?' + 'a' * 163 + '&token=' + pw; long_list = ['x' * 3990 + pw]"}
          show_same:
            type: logic.inspect
          show_built:
            type: logic.inspect
          show_inside:
            type: logic.inspect
          check:
            type: logic.python
            config: {inputs: [n], outputs: [], code: "assert n == len('hunter2SECRET')"}
        edges:
          - ask.answer -> message.value
          - ask.answer -> outside.pw
          - outside.n -> check.n
          - ask.answer -> inside.pw
          - outside.same -> show_same.value
          - outside.built -> show_built.value
          - inside.built -> show_inside.value
    """)
    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    assert "hunte" not in result.stdout + result.stderr  # not even part of it (e.g. text cut to length)
    logs = " ".join(m for (m,) in env.query("SELECT message FROM logs"))
    assert "hunter2SECRET" not in logs and "password is ***" in logs and "user:***" in logs
    stored = " ".join(str(v) for row in env.query("SELECT summary, error, previews FROM blocks") for v in row)
    assert "hunte" not in stored
    for path in (env.home / "runs").rglob("*"):
        if path.is_file():
            assert b"hunter2SECRET" not in path.read_bytes(), path
