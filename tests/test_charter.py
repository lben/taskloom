"""The architecture charter's [CI] rules (docs/ARCHITECTURE.md): each test fails the build when a change breaks one."""

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "taskloom"
sys.path.insert(0, str(ROOT / "packaging"))
import design_health  # noqa: E402

# C1: the layers, 1 (entry points) to 5 (foundations). A module not listed here fails test_every_module_has_a_layer:
# a new module is placed in a layer of the charter first.
LAYERS = {
    1: ["cli", "editor", "scheduler", "check"],
    2: ["blocks"],
    3: ["flow", "engine", "registry", "pyenv", "upgrade"],
    4: ["config", "history", "jdbc", "ssh", "mail"],
    5: ["__init__", "block", "table", "expr", "params", "calendars", "schedule", "report", "launch"],
}
LAYER_OF = {name: layer for layer, names in LAYERS.items() for name in names}
# Calls to a database, a server, a mail server or the web, which foundations never make.
OUTSIDE = ("smtplib", "urllib.request", "http.client", "socket", "paramiko", "jpype", "sqlite3", "duckdb", "ftplib",
           "requests")


def modules():
    """(top-level name in taskloom, file, parsed source) of every Python file of the package."""
    for file in sorted(PACKAGE.rglob("*.py")):
        rel = file.relative_to(PACKAGE).parts
        yield (rel[0] if len(rel) > 1 else Path(rel[0]).stem), file, ast.parse(file.read_text(encoding="utf-8"))


def imports(tree, file):
    """(line, imported module) of every import of the file, also those inside functions."""
    package = file.relative_to(ROOT).with_suffix("").parts[:-1]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
            module = ".".join(p for p in (base, node.module or "") if p)
            yield node.lineno, module
            if not node.module:  # from . import mail, ssh
                yield from ((node.lineno, f"{module}.{a.name}") for a in node.names)


def taskloom_part(module: str) -> str | None:
    parts = module.split(".")
    if parts[0] != "taskloom":
        return None
    return parts[1] if len(parts) > 1 else "__init__"


@pytest.mark.charter("C1")
def test_every_module_has_a_layer():
    assert [str(f.relative_to(ROOT)) for name, f, _ in modules() if name not in LAYER_OF] == []


@pytest.mark.charter("C1")
def test_code_uses_its_own_layer_and_the_layers_below_never_those_above():
    """C1."""
    upward = []
    for name, file, tree in modules():
        for line, module in imports(tree, file):
            target = taskloom_part(module)
            if target in LAYER_OF and LAYER_OF[target] < LAYER_OF[name]:
                upward.append(f"{file.relative_to(ROOT)}:{line} ({name}, layer {LAYER_OF[name]}) uses {module} "
                              f"(layer {LAYER_OF[target]})")
    assert upward == []


@pytest.mark.charter("C1")
def test_foundations_never_call_a_database_a_server_a_mail_server_or_the_web():
    """C1."""
    calls = [f"{file.relative_to(ROOT)}:{line} imports {module}" for name, file, tree in modules()
             if LAYER_OF.get(name) == 5 for line, module in imports(tree, file)
             if module.split(".")[0] in {o.split(".")[0] for o in OUTSIDE} and module.startswith(OUTSIDE)]
    assert calls == []


@pytest.mark.charter("C2")
def test_only_the_editor_and_the_tray_icon_use_qt():
    """C2: outside the editor, Qt is imported only inside the scheduler service's tray functions, so the command line,
    a run and the headless service never load it (the Linux runner is built without it)."""
    tray = {"_tray_available", "_serve_tray"}

    def qt_imports(node, file, inside=None):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                yield from qt_imports(child, file, inside or child.name)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                if any(m.startswith(("PySide6", "shiboken6")) for _, m in imports(ast.Module([child], []), file)):
                    yield child.lineno, inside
            else:
                yield from qt_imports(child, file, inside)

    found = [f"{file.relative_to(ROOT)}:{line}" for name, file, tree in modules() if name != "editor"
             for line, inside in qt_imports(tree, file) if not (name == "scheduler" and inside in tray)]
    assert found == []


@pytest.mark.charter("C5")
def test_only_the_history_code_opens_the_taskloom_folders_database():
    """C5."""
    opens = []
    for name, file, tree in modules():
        if file == PACKAGE / "history.py":
            continue
        nodes = list(ast.walk(tree))
        modules_as = {a.asname or a.name for n in nodes if isinstance(n, ast.Import) for a in n.names if a.name == "sqlite3"}
        connect_as = {a.asname or a.name for n in nodes if isinstance(n, ast.ImportFrom) and n.module == "sqlite3"
                      for a in n.names if a.name in ("connect", "*")}
        opens += [f"{file.relative_to(ROOT)}:{n.lineno}" for n in nodes if isinstance(n, ast.Call) and (
            (isinstance(n.func, ast.Attribute) and n.func.attr == "connect" and ast.unparse(n.func.value) in modules_as)
            or (isinstance(n.func, ast.Name) and (n.func.id in connect_as or "*" in connect_as and n.func.id == "connect")))]
    assert opens == []


@pytest.mark.charter("C8")
def test_calls_outside_taskloom_have_a_time_limit():
    """C8, where the code shows it: mail servers, the web, network connections and other programs run to their end."""
    limited = ("SMTP", "SMTP_SSL", "urlopen", "HTTPConnection", "HTTPSConnection", "create_connection")
    to_end = ("run", "call", "check_call", "check_output")
    missing = []
    for name, file, tree in modules():
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            func, base = node.func.attr, ast.unparse(node.func.value)
            if func in limited or (base == "subprocess" and func in to_end):
                if "timeout" not in {k.arg for k in node.keywords}:
                    missing.append(f"{file.relative_to(ROOT)}:{node.lineno} {ast.unparse(node)[:80]}")
    assert missing == []


@pytest.mark.charter("C12")
def test_every_built_in_block_has_a_description():
    """C12."""
    from taskloom.registry import discover

    registry = discover(ROOT / "no-user-blocks")
    assert [type_id for type_id, cls in registry.blocks.items() if not (cls.description or "").strip()] == []


@pytest.mark.charter("C14")
def test_design_health_is_no_worse_than_the_last_release():
    """C14: a function newly over a limit is split, or kept with a reason in docs/design_health.yaml."""
    report = design_health.report()
    assert report["unexplained"] == [], "split these, or keep them with a one-line reason the reviewer accepts"


@pytest.mark.charter("C6")
def test_a_secret_never_reaches_the_failure_email_the_history_or_the_logs(env, smtp):
    """C6: a block that fails with a secret in its error: the email about it, the output, the run history and every
    file of the Taskloom folder show *** instead."""
    env.write("home/secrets.yaml", "api_token: tok-Q7v-never-shown\n").chmod(0o600)
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    flow = env.write("flow.yaml", """
        name: nightly
        blocks:
          ask:
            type: logic.ask_user
            config: {prompt: "API token?", kind: password, secret: api_token}
          use:
            type: logic.python
            config:
              inputs: [token]
              outputs: []
              code: |
                raise ValueError("the server refused " + token)
        edges:
          - ask.answer -> use.token
    """)
    result = env.taskloom("run", flow, "--notify")
    _, status = env.last_run()

    assert status == "failed", result.stdout + result.stderr
    [message] = smtp.messages
    body = message.get_body(("html", "plain")).get_content()
    assert "the server refused ***" in body, body
    seen = [body, result.stdout, result.stderr] + [
        f.read_bytes().decode("utf-8", "replace") for f in env.home.rglob("*") if f.is_file() and f.name != "secrets.yaml"]
    assert not any("tok-Q7v" in text for text in seen)
