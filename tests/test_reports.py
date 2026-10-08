"""Reports and email through the real CLI and a real SMTP server."""

import re
import shutil
import zipfile

from conftest import REPO

REPORT_FLOW = """
    params:
      day: {type: date, value: 2026-10-02}
    blocks:
      sales:
        type: data.read_file
        config: {path: data/sales.csv}
      totals:
        type: data.duckdb_sql
        config:
          inputs: [sales]
          sql: "select region, round(sum(amount), 2) as total from sales where day = :day group by region order by region"
      chart:
        type: reports.chart
        config: {kind: bar, x: region, y: [total], title: "Sales by region"}
      excel:
        type: reports.excel
        config: {path: "out/sales_{day:%Y%m%d}.xlsx", sheets: [totals], chart: bar, chart_x: region, chart_y: [total]}
      body:
        type: reports.html_template
        config:
          inputs: [totals, chart]
          template: |
            <style>h2 { color: #2c5d8f; font-size: 18px; }</style>
            <h2>Sales for {{ params.day }}</h2>
            {{ image(chart) }}
            {{ table(totals) }}
      mail:
        type: email.send
        config:
          connection: mailer
          to: [team@example.com]
          subject: "Daily sales {day:%Y-%m-%d}"
          save_copy: out/report.eml
    edges:
      - sales.table -> totals.sales
      - totals.result -> chart.table
      - totals.result -> excel.totals
      - totals.result -> body.totals
      - chart.image -> body.chart
      - body.html -> mail.body
      - excel.file -> mail.attachments
"""


def test_report_email_with_inline_chart_and_excel_attachment(env, smtp):
    shutil.copytree(REPO / "examples" / "data", env.root / "data")
    flow = env.write("report.yaml", REPORT_FLOW)

    result = env.taskloom("run", flow)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    assert msg["Subject"] == "Daily sales 2026-10-02" and msg["To"] == "team@example.com"
    html_part = msg.get_body(preferencelist=("html",))
    html = html_part.get_content()
    # Outlook-safe: no style blocks or data: images, the chart is an inline (cid:) attachment with a width.
    assert "<style" not in html and "data:" not in html and "<svg" not in html and "flex" not in html
    assert re.search(r'<h2 style="[^"]*color: ?#2c5d8f', html)
    [cid] = re.findall(r'<img src="cid:([^"]+)" width="\d+"', html)
    related = [p for p in msg.walk() if p.get_content_type() == "image/png"]
    assert [(p["Content-ID"], p.get_content_disposition()) for p in related] == [(f"<{cid}>", "inline")]
    assert re.search(r'<img src="cid:[^"]+" width="\d+" height="\d+"', html)
    assert ">APAC<" in html and ">1,075.69<" in html
    assert msg.get_body(preferencelist=("plain",)).get_content().strip().startswith("Sales for 2026-10-02")
    [attachment] = [p for p in msg.iter_attachments() if p.get_filename() == "sales_20261002.xlsx"]
    xlsx = env.root / "attached.xlsx"
    xlsx.write_bytes(attachment.get_content())
    with zipfile.ZipFile(xlsx) as z:
        assert "<c:barChart>" in z.read("xl/charts/chart1.xml").decode()
    assert (env.root / "out" / "report.eml").exists()


FAILING = """
    blocks:
      boom:
        type: logic.python
        config: {inputs: [], code: "raise RuntimeError('the warehouse is down')"}
"""


def test_notify_emails_failures_of_unattended_runs(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    failing = env.write("nightly.yaml", FAILING.replace("blocks:", "name: nightly\n    blocks:", 1))
    ok = env.write("ok.yaml", """
        blocks:
          fine:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)

    assert env.taskloom("run", failing).returncode == 1  # a manual run: no email
    assert smtp.messages == []
    assert env.taskloom("run", failing, "--notify").returncode == 1
    assert env.taskloom("run", ok, "--notify").returncode == 0  # success is not in notify_on

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] nightly failed" and msg["To"] == "me@example.com"
    assert "RuntimeError: the warehouse is down" in msg.get_body(preferencelist=("html",)).get_content()


def test_flow_notify_settings_override_the_defaults(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    flow = env.write("flow.yaml", """
        name: weekly
        notify: {when: [success], to: [boss@example.com]}
        blocks:
          fine:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)

    assert env.taskloom("run", flow, "--notify").returncode == 0

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] weekly succeeded" and msg["To"] == "boss@example.com"


def test_text_with_angle_brackets_stays_text(env, smtp):
    flow = env.write("flow.yaml", """
        blocks:
          mail:
            type: email.send
            config: {connection: mailer, to: [me@example.com], subject: Check, message: "Revenue < 5 and > 3 today"}
    """)
    assert env.taskloom("run", flow).returncode == 0
    [msg] = smtp.messages
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Revenue < 5 and > 3 today"
    assert "Revenue &lt; 5 and &gt; 3 today" in msg.get_body(preferencelist=("html",)).get_content()


def test_scheduler_emails_skipped_runs_when_asked(env, smtp):
    import datetime as dt

    from taskloom.config import Home
    from taskloom.scheduler import Scheduler

    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    flow = env.write("flow.yaml", """
        name: slow
        notify: {when: [skipped]}
        triggers:
          - schedule: {cron: "* * * * *"}
        blocks:
          pause:
            type: logic.wait
            config: {seconds: 3}
    """)
    home = Home(env.home)
    home.set_scheduled(flow, True)
    scheduler = Scheduler(home)
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(t0)
    scheduler.tick(t0 + dt.timedelta(minutes=1))
    scheduler.tick(t0 + dt.timedelta(minutes=2))  # still running: skipped
    scheduler.wait_for_runs()

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] Run skipped" and msg["To"] == "me@example.com"
