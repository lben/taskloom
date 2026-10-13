"""Trying a connection: does it connect and log in? Used by `taskloom connection test NAME`."""

from __future__ import annotations

import smtplib
import ssl

from . import jdbc, ssh


def test_connection(home, name: str) -> str:
    """A short success message; raises with the reason when it does not work."""
    conns = home.connections()
    if name not in conns:
        raise KeyError(f"no connection named '{name}'")
    conn = home.resolve_secrets(conns[name])
    kind = conn.get("kind")
    if kind == "ssh":
        client = ssh.connect(conn, home)
        try:
            code, out, err = ssh.run(client, "echo ok")
        finally:
            client.close()
        if code != 0 or out.strip() != "ok":
            raise RuntimeError(f"logged in, but running a command failed: {err.strip()}")
        return f"Connected to {conn['host']} as {conn['username']}"
    if kind == "smtp":
        security = conn.get("security", "none")
        port = int(conn.get("port") or {"ssl": 465, "starttls": 587}.get(security, 25))
        server = (smtplib.SMTP_SSL(conn["host"], port, timeout=30, context=ssl.create_default_context())
                  if security == "ssl" else smtplib.SMTP(conn["host"], port, timeout=30))
        with server:
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if conn.get("username"):
                server.login(conn["username"], conn.get("password") or "")
            server.noop()
        return f"The mail server {conn['host']}:{port} answered" + (" and accepted the login" if conn.get("username") else "")
    if kind == "jdbc":
        connection = jdbc.connect(conn, jdbc.all_jars(conns), home.settings()["java_home"])
        try:
            meta = connection.getMetaData()
            return f"Connected to {meta.getDatabaseProductName()} {meta.getDatabaseProductVersion()}"
        finally:
            connection.close()
    raise ValueError(f"unknown connection kind '{kind}'")
