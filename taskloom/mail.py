"""Sending email over SMTP, and run notifications.

An SMTP connection (`kind: smtp`) has host, port, security (none | starttls | ssl),
optional username/password, and sender (the From address).
"""

from __future__ import annotations

import html
import mimetypes
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

import polars as pl

from .report import Html, email_document, html_table

def _addresses(value) -> list[str]:
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[;,]", str(value))
    return [a.strip() for a in items if str(a).strip()]


def build_message(sender: str, to, subject: str, body: Html, attachments=(), cc=()) -> EmailMessage:
    """An HTML email with a plain-text fallback, inline images (cid:) and attachments."""
    if not _addresses(to):
        raise ValueError("no recipients (set 'to', or notify_email in settings)")
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(_addresses(to)), subject
    if _addresses(cc):
        msg["Cc"] = ", ".join(_addresses(cc))
    msg["Date"], msg["Message-ID"] = formatdate(localtime=True), make_msgid()
    document = email_document(body.html)
    visible = re.sub(r"(?is)<(style|script|head)\b.*?</\1>", "", body.html)
    text = re.sub(r"<[^>]+>", " ", re.sub(r"(?i)<br\s*/?>|</(p|tr|h\d|div)>", "\n", visible))
    msg.set_content(html.unescape(re.sub(r"[ \t]+", " ", text)).strip() + "\n")
    msg.add_alternative(document, subtype="html")
    html_part = msg.get_payload()[1]
    for cid, path in body.images.items():
        html_part.add_related(Path(path).read_bytes(), "image", "png", cid=f"<{cid}>",
                              disposition="inline", filename=Path(path).name)
    for path in attachments:
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return msg


def send(conn: dict, msg: EmailMessage, timeout: float = 60):
    for key in ("host", "sender"):
        if not conn.get(key):
            raise ValueError(f"SMTP connection needs '{key}'")
    security = conn.get("security", "none")
    port = int(conn.get("port") or {"ssl": 465, "starttls": 587}.get(security, 25))
    if security == "ssl":
        server = smtplib.SMTP_SSL(conn["host"], port, timeout=timeout, context=ssl.create_default_context())
    else:
        server = smtplib.SMTP(conn["host"], port, timeout=timeout)
    with server:
        if security == "starttls":
            server.starttls(context=ssl.create_default_context())
        if conn.get("username"):
            server.login(conn["username"], conn.get("password") or "")
        server.send_message(msg)


# --- notifications ---------------------------------------------------------------------

def notify_settings(home, flow_notify: dict | None) -> tuple[list[str], list[str], str | None]:
    """(events, recipients, smtp connection name) for a flow: its notify: section over the settings."""
    settings = home.settings()
    flow_notify = flow_notify or {}
    events = flow_notify.get("when", settings["notify_on"])
    to = _addresses(flow_notify.get("to") or settings["notify_email"])
    return list(events), to, settings["smtp_connection"]


def send_notice(home, flow_notify, event: str, subject: str, body: Html) -> bool:
    """Email the people to notify about this event, if the flow/settings ask for it."""
    events, to, conn_name = notify_settings(home, flow_notify)
    if event not in events or not to or not conn_name:
        return False
    conn = home.resolve_secrets(home.connections()[conn_name])
    send(conn, build_message(conn["sender"], to, subject, body))
    return True


def run_summary(history, run_id: int) -> Html:
    run, blocks, logs = history.run(run_id), history.blocks(run_id), history.logs(run_id)
    color = {"success": "#1f8a4c", "failed": "#c0392b"}.get(run["status"], "#5f6d7c")
    rows = pl.DataFrame(
        {"block": list(blocks), "status": [b["status"] for b in blocks.values()],
         "attempts": [b["attempts"] for b in blocks.values()],
         "result": [(b["error"] or b["summary"] or "")[:300] for b in blocks.values()]},
        schema={"block": pl.String, "status": pl.String, "attempts": pl.Int64, "result": pl.String})
    errors = [entry for entry in logs if entry["level"] == "ERROR"][-3:]
    error_html = "".join(
        f'<pre style="font-family:Consolas,monospace;font-size:12px;background:#f3f6f9;padding:8px;'
        f'white-space:pre-wrap;">{html.escape(e["message"][:3000])}</pre>' for e in errors)
    return Html(
        f'<p style="font-size:16px;margin:0 0 4px;"><b>{html.escape(run["flow"])}</b> '
        f'<span style="color:{color};font-weight:bold;">{run["status"]}</span></p>'
        f'<p style="color:#5f6d7c;margin:0 0 12px;">Run #{run_id} · started {run["started"][:19].replace("T", " ")}'
        f' · parameters {html.escape(run["params"])}</p>{html_table(rows)}{error_html}')
