"""Running flows from the editor: each run is a separate `taskloom run` process.

A crash, hang or out-of-memory in a flow cannot take the editor down. The process
reports events as JSON lines on stdout and takes commands (cancel, answers) on stdin.
"""

from __future__ import annotations

import datetime as dt
import json

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QTimer, Signal

from ..config import Home
from ..history import History
from ..launch import ARGS_VARIABLE, runner_command

KILL_AFTER_MS = 6000  # after asking a run to stop, kill it if it is still going


class RunProcess(QObject):
    event = Signal(dict)
    # Text printed outside of events, with its level: ERROR for Taskloom's own errors, OUTPUT for what other
    # programs print (e.g. a database driver's or a library's messages).
    output = Signal(str, str)
    finished = Signal(str)  # final status: success | failed | cancelled

    def __init__(self, flow_path: str, overrides: dict, home: Home, parent=None, extra_args=()):
        super().__init__(parent)
        self.flow_path, self.overrides, self.home = flow_path, overrides, home
        self.extra_args = list(extra_args)
        self.run_id: int | None = None
        self.status: str | None = None
        self._buffer = b""
        self.proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("TASKLOOM_HOME", str(home.root))
        env.insert("PYTHONUNBUFFERED", "1")
        self.proc.setProcessEnvironment(env)
        self.proc.readyReadStandardOutput.connect(self._read_stdout)
        self.proc.readyReadStandardError.connect(self._read_stderr)
        self.proc.finished.connect(self._on_finished)

    def start(self):
        program, *args = runner_command()
        run_args = ["run", self.flow_path, "--json-events", "--interactive"]
        for name, value in self.overrides.items():
            run_args += ["--param", f"{name}={value}"]
        # In the environment, not as arguments: on macOS Qt decomposes a program's arguments (é becomes e and
        # an accent), so an accented flow path or value would not reach the run as written.
        env = self.proc.processEnvironment()
        env.insert(ARGS_VARIABLE, json.dumps(run_args + self.extra_args))
        self.proc.setProcessEnvironment(env)
        self.proc.start(program, args)

    @property
    def running(self) -> bool:
        return self.proc.state() != QProcess.NotRunning

    def _send(self, message: dict):
        if self.running:
            self.proc.write((json.dumps(message) + "\n").encode())

    def stop(self):
        """Ask the run to cancel; kill it if it does not stop in time."""
        self._send({"cancel": True})
        QTimer.singleShot(KILL_AFTER_MS, self._kill_if_running)

    def _kill_if_running(self):
        if self.running:
            self.proc.kill()

    def answer(self, ask_id: str, value):
        self._send({"ask_id": ask_id, "value": value})

    def _read_stdout(self):
        self._buffer += bytes(self.proc.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for line in lines:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                self.output.emit("OUTPUT", line.decode(errors="replace"))
                continue
            if event.get("event") == "run_started":
                self.run_id = event["run_id"]
            elif event.get("event") == "run_finished":
                self.status = event["status"]
            self.event.emit(event)

    def _read_stderr(self):
        # With --json-events, the runner prints only its errors here ("error: ..." and the "warning: ..." before
        # them when it refuses a run); anything else is what the blocks' libraries print.
        for line in bytes(self.proc.readAllStandardError()).decode(errors="replace").splitlines():
            self.output.emit("ERROR" if line.startswith(("error: ", "warning: ")) else "OUTPUT", line)

    def _on_finished(self, exit_code, exit_status):
        self._read_stdout()
        if self.status is None:
            if self.run_id is not None:
                # The process died before finishing (killed or crashed): record that.
                history = History(self.home.history_db)
                history.mark_crashed(self.run_id, dt.datetime.now().isoformat(timespec="milliseconds"))
                history.close()
                self.output.emit("ERROR", f"the run process stopped unexpectedly (exit code {exit_code})")
            self.status = "failed"
        self.finished.emit(self.status)
