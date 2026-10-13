"""Your own Python environment, for Python Code blocks that need packages Taskloom does not include.

It is an ordinary virtual environment in <Taskloom folder>/python-env, made from a Python
installed on this computer. Install packages into it with `taskloom env pip install NAME`
(your pip settings, such as a company package mirror, apply). A Python Code block with
"use_environment" runs there, in its own process.
"""

from __future__ import annotations

import os
import pickle
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .launch import frozen

# Runs inside the environment's Python, so it may only use the standard library (and
# pandas/polars/pyarrow when they happen to be installed there).
HELPER = r'''
import pickle, sys
result_file = sys.argv[1]
job = pickle.load(sys.stdin.buffer)  # never written to disk: it can hold passwords

def load_table(path):
    try:
        import pandas
        return pandas.read_parquet(path)
    except ImportError:
        pass
    try:
        import polars
        return polars.read_parquet(path)
    except ImportError:
        return path  # no DataFrame library here: you get the Parquet file's path

namespace = {"params": job["params"], "log": lambda *a: print(*a, flush=True)}
for name, value in job["inputs"].items():
    namespace[name] = load_table(value["__table__"]) if isinstance(value, dict) and "__table__" in value else value
exec(compile(job["code"], "<Python Code block>", "exec"), namespace)

result = {}
for name in job["outputs"]:
    if name not in namespace:
        continue
    value = namespace[name]
    library = type(value).__module__.split(".")[0]
    if library in ("pandas", "polars", "pyarrow"):
        path = f"{job['workspace']}/{name}.parquet"
        if library == "pandas":
            value.to_parquet(path, index=False)
        elif library == "polars":
            value.write_parquet(path)
        else:
            import pyarrow.parquet
            pyarrow.parquet.write_table(value, path)
        value = {"__table__": path}
    result[name] = value
with open(result_file, "wb") as f:
    pickle.dump(result, f, protocol=4)
'''


def env_dir(home) -> Path:
    return home.root / "python-env"


def env_python(home) -> Path:
    return env_dir(home) / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def base_python(home) -> list[str]:
    """The Python used to create the environment: the python setting, else one found on this computer."""
    configured = home.settings().get("python")
    if configured:
        return [str(Path(configured).expanduser())]
    if not frozen():
        return [sys.executable]
    if sys.platform == "win32" and shutil.which("py"):
        return ["py", "-3"]
    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and "WindowsApps" not in found:  # the Microsoft Store placeholder is not a Python
            return [found]
    raise FileNotFoundError("no Python found on this computer: install Python 3.8 or newer, then set its "
                            "path under Tools → Settings → Python (or the python setting)")


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def create(home, python: str | None = None) -> str:
    command = [python] if python else base_python(home)
    result = subprocess.run(command + ["-m", "venv", str(env_dir(home))], capture_output=True, text=True,
                            creationflags=_no_window())
    if result.returncode != 0:
        raise RuntimeError(f"could not create the environment: {result.stderr.strip() or result.stdout.strip()}")
    version = subprocess.run([str(env_python(home)), "--version"], capture_output=True, text=True,
                             creationflags=_no_window()).stdout.strip()
    return f"created {env_dir(home)} ({version})"


def pip(home, args: list[str]) -> int:
    """Run pip in the environment, printing its output as it goes."""
    if not env_python(home).exists():
        raise FileNotFoundError("there is no Python environment yet: create it first (taskloom env create)")
    return subprocess.call([str(env_python(home)), "-m", "pip", *args], creationflags=_no_window())


def _portable(value, table_type, secret_type):
    """Inputs as plain Python values the environment can unpickle without Taskloom installed."""
    if isinstance(value, table_type):
        return {"__table__": str(value.path)}
    if isinstance(value, secret_type):
        return str.__str__(value)
    if isinstance(value, dict):
        return {k: _portable(v, table_type, secret_type) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable(v, table_type, secret_type) for v in value]
    if value is None or isinstance(value, (str, int, float, bool, bytes)) or type(value).__module__ == "datetime":
        return value
    return str(value)


def run_code(ctx, code: str, inputs: dict, outputs: list[str]) -> dict:
    """Run a Python Code block's code in the environment; returns its outputs."""
    from .engine import Cancelled, SecretStr
    from .table import Table

    python = env_python(ctx.home)
    if not python.exists():
        raise FileNotFoundError("this block runs in your Python environment, but there is none yet: "
                                "create it under Tools → Python environment")
    work = ctx.new_path("env")
    work.mkdir(exist_ok=True)
    helper, result_file = work / "helper.py", work / "result.pickle"
    helper.write_text(HELPER, encoding="utf-8")
    # Inputs can hold passwords (Ask User, secrets): they go through a pipe, never to a file.
    job = {"code": code, "outputs": outputs, "workspace": str(work),
           "params": _portable(ctx.params, Table, SecretStr),
           "inputs": {k: _portable(v, Table, SecretStr) for k, v in inputs.items()}}
    proc = subprocess.Popen([str(python), "-I", str(helper), str(result_file)], cwd=os.getcwd(),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            creationflags=_no_window())
    try:
        proc.stdin.write(pickle.dumps(job, protocol=4))
        proc.stdin.close()
    except BrokenPipeError:
        pass  # it failed before reading its job; the error is in its output
    lines: list[str] = []

    def read():
        for raw in proc.stdout:
            line = raw.decode(errors="replace").rstrip()
            lines.append(line)
            ctx.log(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    while proc.poll() is None:
        if ctx.cancelled:
            proc.kill()
            proc.wait()
            raise Cancelled()
        ctx._attempt_cancel.wait(0.1)
    reader.join(5)
    if proc.returncode != 0:
        raise RuntimeError("the code failed in your Python environment:\n" + "\n".join(lines[-30:]))
    try:
        result = pickle.loads(result_file.read_bytes())
    finally:
        result_file.unlink(missing_ok=True)  # outputs can hold passwords too
    secrets = _secret_values(inputs) | _secret_values(ctx.params)

    def restore(value):  # a password that comes back out is still a password
        if isinstance(value, str) and value in secrets:
            return SecretStr(value)
        if isinstance(value, dict):
            return {k: restore(v) for k, v in value.items()}
        if isinstance(value, list):
            return [restore(v) for v in value]
        return value

    return {k: Table(Path(v["__table__"])) if isinstance(v, dict) and "__table__" in v else restore(v)
            for k, v in result.items()}


def _secret_values(value) -> set[str]:
    from .engine import SecretStr

    if isinstance(value, SecretStr):
        return {str.__str__(value)}
    if isinstance(value, dict):
        return set().union(*(_secret_values(v) for v in value.values())) if value else set()
    if isinstance(value, (list, tuple)):
        return set().union(*(_secret_values(v) for v in value)) if value else set()
    return set()
