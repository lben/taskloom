"""A real SSH + SFTP server on localhost for tests.

Commands run in /bin/sh with HOME set to a temporary "server home", so `~` on the
"server" is that folder; relative SFTP paths resolve against it too.
"""

import asyncio
import os
import socket
import threading

import asyncssh

USER, PASSWORD = "tester", "s3cret"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Auth(asyncssh.SSHServer):
    def begin_auth(self, username):
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        return username == USER and password == PASSWORD


class SSHTestServer:
    def __init__(self, home, port=None, host_key=None):
        self.home = str(home)
        self.port = port or free_port()
        self.host_key = host_key or asyncssh.generate_private_key("ssh-ed25519")
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)

    async def _process(self, process):
        proc = await asyncio.create_subprocess_shell(
            process.command, cwd=self.home, env={**os.environ, "HOME": self.home},
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        process.stdout.write(out.decode(errors="replace"))
        process.stderr.write(err.decode(errors="replace"))
        process.exit(proc.returncode)

    def _sftp(self, chan):
        home = self.home.encode()

        class HomeSFTP(asyncssh.SFTPServer):
            def map_path(self, path):
                return path if path.startswith(b"/") else os.path.join(home, path)

        return HomeSFTP(chan)

    def start(self):
        self._thread.start()
        async def listen():
            return await asyncssh.listen(
                "127.0.0.1", self.port, server_factory=_Auth, server_host_keys=[self.host_key],
                process_factory=self._process, sftp_factory=self._sftp, encoding="utf-8")

        self._acceptor = asyncio.run_coroutine_threadsafe(listen(), self._loop).result(10)
        return self

    def stop(self):
        async def close():
            self._acceptor.close()  # stops listening right away
            try:  # finishing open connections may take a moment; don't wait for stragglers
                await asyncio.wait_for(self._acceptor.wait_closed(), 2)
            except asyncio.TimeoutError:
                pass
        asyncio.run_coroutine_threadsafe(close(), self._loop).result(10)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(5)

    def connection(self) -> str:
        """connections.yaml entry for this server (the password is stored as a secret)."""
        return (f"appserver:\n  kind: ssh\n  host: 127.0.0.1\n  port: {self.port}\n"
                f"  username: {USER}\n  password: 'secret:appserver_password'\n")
