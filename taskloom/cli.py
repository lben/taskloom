"""Command line: taskloom run | validate | secret."""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import queue
import signal
import sys
import threading
from pathlib import Path

from .config import Home
from .engine import Cancelled, run_flow
from .flow import FlowError, load_flow, validate
from .registry import discover

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_CANCELLED = 0, 1, 2, 130


def _print_event(event: dict):
    kind, ts = event["event"], event["ts"][11:19]
    block = f"[{event['block']}] " if event.get("block") else ""
    if kind == "log":
        line = f"{ts} {block}{event['level']}: {event['message']}"
    elif kind == "block_started":
        line = f"{ts} {block}started (attempt {event['attempt']})"
    elif kind == "block_finished":
        detail = event.get("summary") or event.get("error") or ""
        line = f"{ts} {block}{event['status']}" + (f" · {detail}" if detail else "")
    elif kind == "run_started":
        params = " ".join(f"{k}={v}" for k, v in event["params"].items())
        line = f"{ts} run #{event['run_id']} of {event['flow']} started" + (f" · {params}" if params else "")
    else:
        line = f"{ts} run #{event['run_id']} {event['status']}"
    print(line, file=sys.stderr, flush=True)


def _print_json(event: dict):
    print(json.dumps(event, default=str), flush=True)


class StdinControl:
    """Commands from the editor, one JSON object per line on stdin.

    {"cancel": true} cancels the run; {"ask_id": ..., "value": ...} answers a question.
    If stdin closes (the editor went away) the run is cancelled.
    """

    def __init__(self, cancel: threading.Event):
        self.cancel = cancel
        self._answers: dict[str, queue.Queue] = {}
        self._lock = threading.Lock()
        threading.Thread(target=self._read, name="stdin-control", daemon=True).start()

    def _queue(self, ask_id) -> queue.Queue:
        with self._lock:
            return self._answers.setdefault(ask_id, queue.Queue())

    def _read(self):
        for line in sys.stdin:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("cancel"):
                self.cancel.set()
            elif "ask_id" in message:
                self._queue(message["ask_id"]).put(message.get("value"))
        self.cancel.set()

    def wait(self, ask_id: str, ctx):
        answers = self._queue(ask_id)
        while True:
            try:
                return answers.get(timeout=0.1)
            except queue.Empty:
                if ctx.cancelled:
                    raise Cancelled()


def _parse_overrides(items) -> dict:
    overrides = {}
    for item in items or []:
        name, sep, value = item.partition("=")
        if not sep:
            raise ValueError(f"--param expects name=value, got {item!r}")
        overrides[name.strip()] = value
    return overrides


def cmd_run(args, home: Home) -> int:
    flow = load_flow(Path(args.flow).resolve())
    # Relative paths in a flow are relative to the flow file, wherever it is run from.
    os.chdir(flow.path.parent)
    registry = discover(home.blocks_dir)
    sinks = [_print_json] if args.json_events else [_print_event]
    cancel = threading.Event()

    def on_signal(signum, frame):
        if cancel.is_set():  # second Ctrl+C: stop immediately
            raise KeyboardInterrupt
        print("cancelling… (press Ctrl+C again to force)", file=sys.stderr, flush=True)
        cancel.set()

    signal.signal(signal.SIGINT, on_signal)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, on_signal)
    answers = StdinControl(cancel) if args.interactive else None
    as_of = dt.datetime.fromisoformat(args.as_of) if args.as_of else None
    result = run_flow(flow, registry, home, _parse_overrides(args.param), sinks=sinks, cancel=cancel,
                      answers=answers, as_of=as_of)
    if args.notify and result.status in ("success", "failed"):
        _notify(home, flow, result)
    return {"success": EXIT_OK, "failed": EXIT_FAILED, "cancelled": EXIT_CANCELLED}[result.status]


def _notify(home: Home, flow, result):
    from . import mail
    from .history import History

    history = History(home.history_db)
    try:
        event = "success" if result.status == "success" else "failure"
        subject = f"[Taskloom] {flow.name} {'succeeded' if event == 'success' else 'failed'}"
        if mail.send_notice(home, flow.notify, event, subject, mail.run_summary(history, result.run_id)):
            print(f"notified about run #{result.run_id}", file=sys.stderr)
    except Exception as e:  # a notification problem must not change the run's result
        print(f"warning: could not send the notification email: {e}", file=sys.stderr)
    finally:
        history.close()


def cmd_validate(args, home: Home) -> int:
    flow = load_flow(args.flow)
    registry = discover(home.blocks_dir)
    errors, warnings = validate(flow, registry, home)
    for message in registry.warnings + warnings:
        print(f"warning: {message}", file=sys.stderr)
    for message in errors:
        print(f"error: {message}", file=sys.stderr)
    if errors:
        return EXIT_INVALID
    print(f"{flow.name}: OK ({len(flow.blocks)} blocks)")
    return EXIT_OK


def cmd_secret_set(args, home: Home) -> int:
    value = getpass.getpass(f"Value for secret '{args.name}': ")
    where = home.set_secret(args.name, value)
    print(f"saved secret '{args.name}' in {where}")
    return EXIT_OK


def cmd_scheduler(args, home: Home) -> int:
    from . import scheduler

    if args.at_login:
        path = scheduler.set_start_at_login(home, args.at_login == "on")
        print(f"start at login {'enabled' if args.at_login == 'on' else 'disabled'}: {path}")
        return EXIT_OK
    try:
        return scheduler.serve(home, args.headless)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_FAILED


def cmd_editor(args, home: Home) -> int:
    from .editor.window import main as editor_main  # Qt is only needed for the editor

    return editor_main(args.flows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="taskloom", description="Run and check Taskloom flows.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("run", help="run a flow")
    p.add_argument("flow")
    p.add_argument("--param", action="append", metavar="NAME=VALUE", help="override a parameter (repeatable)")
    p.add_argument("--json-events", action="store_true", help="print events as JSON lines on stdout")
    p.add_argument("--as-of", metavar="DATETIME", help="compute parameters as if it were this local time (YYYY-MM-DD HH:MM)")
    p.add_argument("--notify", action="store_true",
                   help="email the result as the notify settings say (the scheduler does this)")
    p.add_argument("--interactive", action="store_true",
                   help="read cancel requests and answers to questions as JSON lines on stdin (used by the editor)")
    p.set_defaults(func=cmd_run)
    p = sub.add_parser("validate", help="check a flow without running it")
    p.add_argument("flow")
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser("scheduler", help="run scheduled flows (keeps running)")
    p.add_argument("--headless", action="store_true", help="no tray icon; for servers")
    p.add_argument("--at-login", choices=["on", "off"], help="start the scheduler when you log in (Windows)")
    p.set_defaults(func=cmd_scheduler)
    p = sub.add_parser("editor", help="open the visual editor")
    p.add_argument("flows", nargs="*", help="flow files to open")
    p.set_defaults(func=cmd_editor)
    p = sub.add_parser("secret", help="manage secrets")
    secret_sub = p.add_subparsers(dest="secret_command", required=True)
    s = secret_sub.add_parser("set", help="store a secret (prompts for the value)")
    s.add_argument("name")
    s.set_defaults(func=cmd_secret_set)

    args = parser.parse_args(argv)
    try:
        return args.func(args, Home.default())
    except FlowError as e:
        for message in e.errors:
            print(f"error: {message}", file=sys.stderr)
        return EXIT_INVALID
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return EXIT_INVALID


if __name__ == "__main__":
    sys.exit(main())
