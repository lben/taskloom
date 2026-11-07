"""The C4 view on the proof's cover, generated from the code: who and what Taskloom talks to, and its programs.

    python packaging/c4.py OUT_DIR        # writes context.mmd, containers.mmd and their .png

Nothing here is drawn by hand. The programs are the command line's subcommands that start them. An arrow "starts"
is a place in the code that starts that subcommand. The outside systems and the stores are the libraries and modules
each program's code imports (also inside functions). The table OUTSIDE only names what a library talks to.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "taskloom"
# library -> (node id, what it talks to, how)
OUTSIDE = {
    "jpype": ("databases", "Databases", "JDBC"),
    "paramiko": ("servers", "Your servers", "SSH"),
    "smtplib": ("mail", "Mail server", "SMTP"),
    "urllib.request": ("web", "Web APIs", "HTTPS"),
    "keyring": ("vault", "Credential store", "the system's"),
}
# module -> (node id, store); a program that imports it keeps that state there
STORES = {
    "taskloom.history": ("folder", "Taskloom folder: settings, run history"),
    "taskloom.config": ("folder", "Taskloom folder: settings, run history"),
    "taskloom.flow": ("flowdir", "Flow folder: your flows and their Git history"),
    "dulwich": ("flowdir", "Flow folder: your flows and their Git history"),
}
PROGRAMS = {  # subcommand -> (node id, name, the modules its code starts from)
    "editor": ("editor", "Editor", ["taskloom/editor"]),
    "scheduler": ("service", "Scheduler service", ["taskloom/scheduler.py"]),
    "run": ("run", "Run process (one per run)", ["taskloom/engine.py", "taskloom/registry.py", "taskloom/blocks"]),
}


def _files(where: str) -> list[Path]:
    path = ROOT / where
    return sorted(path.rglob("*.py")) if path.is_dir() else [path]


def _module(file: Path) -> str:
    parts = file.relative_to(ROOT).with_suffix("").parts
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _imports(file: Path) -> set[str]:
    package = file.relative_to(ROOT).with_suffix("").parts[:-1]
    found = set()
    for node in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            base = ".".join(package[:len(package) - node.level + 1]) if node.level else ""
            module = ".".join(p for p in (base, node.module or "") if p)
            found |= {module} | {f"{module}.{a.name}" for a in node.names}
    return found


def closure(starts: list[Path]) -> set[str]:
    """Every module and library the code of `starts` imports, and what those modules of Taskloom import."""
    by_module = {_module(f): f for f in PACKAGE.rglob("*.py")}
    seen, todo = set(), [_module(f) for f in starts]
    while todo:
        module = todo.pop()
        if module in seen:
            continue
        seen.add(module)
        if module in by_module:
            todo += [m for m in _imports(by_module[module]) if m.split(".")[0] != "taskloom" or m in by_module]
    return seen


def subcommands() -> set[str]:
    """The command line's subcommands (`taskloom run`, `taskloom scheduler`, ...)."""
    tree = ast.parse((PACKAGE / "cli.py").read_text(encoding="utf-8"))
    return {n.args[0].value for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") ==
            "add_parser" and n.args and isinstance(n.args[0], ast.Constant)}


def starts() -> set[tuple[str, str]]:
    """(program, subcommand it starts): a function of the program's code that gets Taskloom's own command and names
    the subcommand as the first word of a command list."""
    found = set()
    for sub, (_, _, where) in PROGRAMS.items():
        for file in (f for w in where for f in _files(w)):
            for fn in ast.walk(ast.parse(file.read_text(encoding="utf-8"))):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                calls = {ast.unparse(n.func) for n in ast.walk(fn) if isinstance(n, ast.Call)}
                if not calls & {"runner_command", "windowed_command"}:
                    continue
                found |= {(sub, n.elts[0].value) for n in ast.walk(fn) if isinstance(n, ast.List) and n.elts
                          and isinstance(n.elts[0], ast.Constant) and n.elts[0].value in PROGRAMS}
    return found


def keeps_servers() -> bool:
    """The scheduler service starts the scheduler of a server over SSH (the keeper)."""
    tree = ast.parse((PACKAGE / "scheduler.py").read_text(encoding="utf-8"))
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and "scheduler --headless" in n.value
               for n in ast.walk(tree))


def uses(modules: set[str], name: str) -> bool:
    return any(m == name or m.startswith(name + ".") for m in modules)


def context() -> str:
    used = closure([f for _, _, where in PROGRAMS.values() for w in where for f in _files(w)])
    lines = ["flowchart LR", '  you(["You"]) -->|build and schedule flows| taskloom["Taskloom on your PC"]']
    lines += [f'  taskloom -->|{how}| {node}["{name}"]' for library, (node, name, how) in OUTSIDE.items()
              if uses(used, library)]
    return "\n".join(lines) + "\n"


def containers() -> str:
    """The programs, where each keeps its state, and which starts which. (The outside systems: the context view.)"""
    names = subcommands()
    used = {sub: closure([f for w in where for f in _files(w)]) for sub, (_, _, where) in PROGRAMS.items()
            if sub in names}
    lines = ["flowchart LR", '  subgraph pc["Your PC"]']
    lines += [f'    {PROGRAMS[sub][0]}["{PROGRAMS[sub][1]}"]' for sub in used]
    lines += list(dict.fromkeys(f'    {node}[("{name}")]' for module, (node, name) in STORES.items()
                                if any(uses(mods, module) for mods in used.values())))
    lines.append("  end")
    lines += [f"  {PROGRAMS[program][0]} -->|starts| {PROGRAMS[sub][0]}" for program, sub in sorted(starts())]
    lines += list(dict.fromkeys(f"  {PROGRAMS[sub][0]} -.-> {node}" for sub, mods in used.items()
                                for module, (node, _) in STORES.items() if uses(mods, module)))
    if keeps_servers():
        lines.append('  service -->|"SSH: starts it when it is down"| remote["Scheduler service on each server"]')
    return "\n".join(lines) + "\n"


def main(out: Path) -> int:
    import mmdr

    out.mkdir(parents=True, exist_ok=True)
    for name, source in (("context", context()), ("containers", containers())):
        (out / f"{name}.mmd").write_text(source, encoding="utf-8")
        (out / f"{name}.png").write_bytes(mmdr.render(source).png(background="#ffffff"))
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
