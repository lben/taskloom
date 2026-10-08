"""Server blocks over SSH: run commands, start/stop apps, health checks, extract archives."""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from .. import ssh
from ..block import Block, fields, ports


@contextmanager
def _client(ctx, name):
    client = ssh.connect(ctx.connection(name), ctx.home)
    try:
        yield client
    finally:
        client.close()


def _run(ctx, client, command) -> tuple[int, str, str]:
    return ssh.run(client, command, lambda: ctx.cancelled)


class SSHCommand(Block):
    type_id = "servers.ssh_command"
    title = "SSH Command"
    category = "Servers"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"stdout": ports.Any(), "exit_code": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh"),
        "command": fields.Text(help="Runs in your login shell on the server"),
        "fail_on_error": fields.Bool(default=True, help="Fail the block when the command exits with a non-zero code"),
    }

    def run(self, ctx, after=None):
        with _client(ctx, self.config.connection) as client:
            code, out, err = _run(ctx, client, self.config.command)
        if out.strip():
            ctx.log(out.rstrip())
        if err.strip():
            ctx.log(err.rstrip(), "WARNING")
        if code != 0 and self.config.fail_on_error:
            raise RuntimeError(f"command exited with code {code}: {err.strip()[-500:]}")
        return {"stdout": out, "exit_code": code}


class StartProcess(Block):
    type_id = "servers.start_process"
    title = "Start Remote Process"
    category = "Servers"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"pid": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh"),
        "command": fields.Text(help="e.g. ~/venv/bin/python app.py --port 8501"),
        "workdir": fields.Text(default="~"),
        "log_file": fields.Text(default="~/.taskloom/apps/app.log"),
        "pid_file": fields.Text(default="~/.taskloom/apps/app.pid"),
        "check_after": fields.Float(default=2.0, help="Seconds to wait before checking it is still running"),
    }

    def run(self, ctx, after=None):
        c = self.config
        with _client(ctx, c.connection) as client:
            code, out, err = _run(ctx, client, ssh.start_detached(c.command, c.workdir, c.log_file, c.pid_file))
            if code != 0:
                raise RuntimeError(f"could not start the process: {err.strip()}")
            pid = int(out.strip().splitlines()[-1])
            ctx.log(f"started process {pid}")
            ctx.wait(c.check_after)
            alive, _, _ = _run(ctx, client, f"kill -0 {pid}")
            if alive != 0:
                _, log, _ = _run(ctx, client, f"tail -n 20 {ssh.remote_path(c.log_file)}")
                raise RuntimeError(f"the process exited right after starting; last log lines:\n{log}")
        return {"pid": pid}


class StopProcess(Block):
    type_id = "servers.stop_process"
    title = "Stop Remote Process"
    category = "Servers"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"stopped": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh"),
        "pid_file": fields.Text(default="~/.taskloom/apps/app.pid"),
        "grace": fields.Float(default=10.0, help="Seconds to wait after asking it to stop before forcing it"),
    }

    def run(self, ctx, after=None):
        c = self.config
        pid_file = ssh.remote_path(c.pid_file)
        with _client(ctx, c.connection) as client:
            code, out, _ = _run(ctx, client, f"cat {pid_file} 2>/dev/null")
            pid = out.strip()
            if code != 0 or not pid.isdigit() or _run(ctx, client, f"kill -0 {pid}")[0] != 0:
                ctx.log("the process was not running")
                return {"stopped": False}
            _run(ctx, client, f"kill {pid}")
            deadline = time.monotonic() + c.grace
            while time.monotonic() < deadline and _run(ctx, client, f"kill -0 {pid}")[0] == 0:
                ctx.wait(0.5)
            if _run(ctx, client, f"kill -0 {pid}")[0] == 0:
                ctx.warn(f"process {pid} did not stop in {c.grace:g} s; forcing it")
                _run(ctx, client, f"kill -9 {pid}")
        ctx.log(f"stopped process {pid}")
        return {"stopped": True}


class HealthCheck(Block):
    type_id = "servers.health_check"
    title = "Health Check"
    category = "Servers"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"healthy": ports.Any(), "unhealthy": ports.Any()}
    config = {
        "url": fields.Text(required=False, help="Checked from this machine"),
        "expect_status": fields.Int(default=200),
        "connection": fields.Connection(kind="ssh", required=False, help="Needed for the PID file check"),
        "pid_file": fields.Text(required=False, help="On the server: the process in it must be running"),
        "port": fields.Int(required=False, help="TCP port on the connection's host that must accept connections"),
        "timeout": fields.Float(default=10.0),
    }

    def run(self, ctx, after=None):
        c = self.config
        problems, checks = [], []
        if c.url:
            checks.append("url")
            try:
                with urllib.request.urlopen(c.url, timeout=c.timeout) as response:
                    status = response.status
            except urllib.error.HTTPError as e:
                status = e.code
            except OSError as e:
                status = None
                problems.append(f"{c.url}: {getattr(e, 'reason', e)}")
            if status is not None and status != c.expect_status:
                problems.append(f"{c.url} returned {status}, expected {c.expect_status}")
        if c.pid_file or c.port:
            if not c.connection:
                raise ValueError("pid_file and port checks need a connection")
            conn = ctx.connection(c.connection)
        if c.pid_file:
            checks.append("pid")
            with _client(ctx, c.connection) as client:
                path = ssh.remote_path(c.pid_file)
                if _run(ctx, client, f"kill -0 \"$(cat {path} 2>/dev/null)\" 2>/dev/null")[0] != 0:
                    problems.append(f"no running process for {c.pid_file}")
        if c.port:
            checks.append("port")
            try:
                socket.create_connection((conn["host"], c.port), timeout=c.timeout).close()
            except OSError as e:
                problems.append(f"port {c.port} on {conn['host']}: {e}")
        if not checks:
            raise ValueError("configure at least one check: url, pid_file or port")
        details = {"checks": checks, "problems": problems}
        if problems:
            ctx.warn("unhealthy: " + "; ".join(problems))
            return {"unhealthy": details}
        ctx.log(f"healthy ({', '.join(checks)})")
        return {"healthy": details}


class RemoteExtract(Block):
    type_id = "servers.remote_extract"
    title = "Remote Extract"
    category = "Servers"
    inputs = {"after": ports.Any(required=False)}
    outputs = {"folder": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh"),
        "archive": fields.Text(help=".tar.gz, .tgz, .tar or .zip on the server"),
        "destination": fields.Text(),
    }

    def run(self, ctx, after=None):
        c = self.config
        archive, dest = ssh.remote_path(c.archive), ssh.remote_path(c.destination)
        tool = f"unzip -o -q {archive} -d {dest}" if c.archive.lower().endswith(".zip") else f"tar -xf {archive} -C {dest}"
        with _client(ctx, c.connection) as client:
            code, _, err = _run(ctx, client, f"mkdir -p {dest} && {tool}")
        if code != 0:
            raise RuntimeError(f"could not extract {c.archive}: {err.strip()}")
        ctx.log(f"extracted {c.archive} to {c.destination}")
        return {"folder": c.destination}
