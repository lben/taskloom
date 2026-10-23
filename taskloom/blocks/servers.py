"""Server blocks over SSH: run commands, start/stop apps, health checks, extract archives."""

from __future__ import annotations

import http.client
import socket
import time
import urllib.error
import urllib.request
from contextlib import contextmanager

from .. import ssh
from ..block import Block, fields, ports


@contextmanager
def _client(ctx, name):
    session = ssh.Session(ctx.connection(name), ctx.home)
    try:
        yield session
    finally:
        session.close()


def _run(ctx, session, command) -> tuple[int, str, str]:
    return session.run(command, ctx.check_cancelled)


class SSHCommand(Block):
    type_id = "servers.ssh_command"
    title = "SSH Command"
    description = 'Runs a command on a server, like typing it in a terminal there, and gives you what it printed.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
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
    description = 'Starts a program on a server so it keeps running after Taskloom disconnects, and checks it did not crash right away.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"pid": ports.Any()}
    config = {
        "connection": fields.Connection(kind="ssh"),
        "command": fields.Text(help="e.g. ~/venv/bin/python app.py --port 8501"),
        "workdir": fields.Text(default="~"),
        "log_file": fields.Text(default="~/.taskloom/apps/app.log"),
        "pid_file": fields.Text(default="~/.taskloom/apps/app.pid"),
        "check_after": fields.Float(default=2.0, help="Seconds to wait before checking it is still running"),
        "must_keep_running": fields.Bool(default=True, help="Untick for a command that does its job and finishes "
                                                            "(e.g. a script that starts a service): exit code 0 is then fine"),
    }

    def run(self, ctx, after=None):
        c = self.config
        command = c.command.rstrip()
        if command.endswith("&") and not command.endswith("&&"):
            command = command[:-1].rstrip()
            ctx.log("removed the & at the end: this block already runs the command in the background, "
                    "and it needs to keep track of it")
        exit_file = ssh.remote_path(ssh.exit_file(c.pid_file))
        # Start it, wait, and check it, all in one command: some servers allow one command per connection.
        script = (f"{ssh.start_detached(command, c.workdir, c.log_file, c.pid_file)} && sleep {c.check_after:g} && "
                  f"if kill -0 \"$(cat {ssh.remote_path(c.pid_file)})\" 2>/dev/null; then echo TASKLOOM_RUNNING; "
                  f"else echo \"TASKLOOM_EXITED $(cat {exit_file} 2>/dev/null)\"; tail -n 20 {ssh.remote_path(c.log_file)}; fi")
        with _client(ctx, c.connection) as session:
            code, out, err = _run(ctx, session, script)
        lines = out.strip().splitlines()
        if code != 0 or len(lines) < 2 or not lines[0].strip().isdigit():
            raise RuntimeError(f"could not start the process: {(err or out).strip()[-1000:]}")
        pid = int(lines[0])
        if lines[1].strip() == "TASKLOOM_RUNNING":
            ctx.log(f"started process {pid}; still running after {c.check_after:g} s")
            return {"pid": pid}
        exit_code = lines[1].split()[1] if len(lines[1].split()) > 1 else "unknown"
        log = "\n".join(lines[2:]).strip()
        if exit_code == "0" and not c.must_keep_running:
            ctx.log(f"the command finished with exit code 0{': ' + log if log else ''}")
            return {"pid": pid}
        if exit_code == "0":
            problem = ("the command finished right away (exit code 0), so nothing is left running. If it starts "
                       "the program in the background itself, or is a script that starts a service, untick "
                       "must_keep_running; otherwise check that it runs in the foreground.")
        else:
            problem = f"the command exited with code {exit_code} right after starting."
        if ">" in command:
            problem += " Your command writes its output to its own file: the reason is probably there."
        raise RuntimeError(problem + (f"\nLast lines of {c.log_file}:\n{log}" if log else ""))


class StopProcess(Block):
    type_id = "servers.stop_process"
    title = "Stop Remote Process"
    description = 'Stops a program on a server that was started with Start Remote Process.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
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
            # Everything the command started, found before signalling: the shell running it can end
            # first while the app is still shutting down (or ignores the request).
            members = _run(ctx, client, ssh.processes(pid))[1].split()
            _run(ctx, client, f"kill -TERM {' '.join(members)} 2>/dev/null; true")
            deadline = time.monotonic() + c.grace
            running = members
            while running and time.monotonic() < deadline:
                ctx.wait(0.5)
                running = _run(ctx, client, ssh.alive(running))[1].split()
            if running:
                ctx.warn(f"{len(running)} process(es) did not stop in {c.grace:g} s; forcing them")
                _run(ctx, client, f"kill -KILL {' '.join(running)} 2>/dev/null; true")
        ctx.log(f"stopped process {pid}")
        return {"stopped": True}


class HealthCheck(Block):
    type_id = "servers.health_check"
    title = "Health Check"
    description = ("Checks that an app is alive: its web address answers, its process is running, or its port is "
                   "open. Continues down 'healthy' or 'unhealthy'; 'result' always says which, for checking or emails.")
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
    outputs = {"healthy": ports.Any(), "unhealthy": ports.Any(), "result": ports.Any()}
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
            except http.client.InvalidURL:
                raise  # a mistake in the url, not an unhealthy app
            except http.client.HTTPException as e:  # it answered, but not with HTTP (e.g. another program's port)
                status = None
                problems.append(f"{c.url} did not answer like a web server ({type(e).__name__}: {str(e).strip()})")
            if status is not None and status != c.expect_status:
                problems.append(f"{c.url} returned {status}, expected {c.expect_status}")
        if c.pid_file or c.port:
            if not c.connection:
                raise ValueError("the PID file and Port checks need a Connection")
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
            raise ValueError("set at least one check: URL, PID file or Port")
        details = {"healthy": not problems, "checks": checks, "problems": problems}
        if problems:
            ctx.warn("unhealthy: " + "; ".join(problems))
            return {"unhealthy": details, "result": details}
        ctx.log(f"healthy ({', '.join(checks)})")
        return {"healthy": details, "result": details}


class RemoteExtract(Block):
    type_id = "servers.remote_extract"
    title = "Remote Extract"
    description = 'Unpacks a .zip or .tar.gz file on a server into a folder.'
    category = "Servers"
    inputs = {"after": ports.Any(required=False, any_of=True)}  # just sets the order
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
