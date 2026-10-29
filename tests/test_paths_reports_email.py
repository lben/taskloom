"""Reports (HTML Template, Chart, To Excel), Send Email and run notifications, through the
real CLI, real SMTP servers (plain, STARTTLS, SSL, with a login) and the editor."""

import contextlib
import datetime as dt
import email
import io
import ipaddress
import re
import shutil
import ssl
import textwrap
import zipfile
from email import policy

import polars as pl
import yaml

from conftest import REPO

DAY = "params:\n      day: {type: date, value: 2026-10-02}\n    "

# A table most tests start from: a Python Code block's frame becomes a table.
DATA = """
      data:
        type: logic.python
        config:
          inputs: []
          outputs: [table]
          code: |
            table = pl.DataFrame({"region": ["APAC", "EU", "US"], "total": [10.5, 20.0, 15.25], "stores": [3, 4, 2]})
"""


def run(env, text, *args, name="flow.yaml"):
    """Run the flow saved as the editor saves flows: UTF-8, not the system's encoding (cp1252 on Windows)."""
    path = env.root / name
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return env.taskloom("run", path, *args)


def html_of(msg) -> str:
    return msg.get_body(preferencelist=("html",)).get_content().replace("\r\n", "\n")


def text_of(msg) -> str:
    return msg.get_body(preferencelist=("plain",)).get_content().replace("\r\n", "\n")


def inline_images(msg) -> dict:
    """{content id: PNG bytes} of the images shown in the body."""
    return {p["Content-ID"].strip("<>"): p.get_content() for p in msg.walk()
            if p.get_content_type() == "image/png" and p.get_content_disposition() == "inline"}


def errors(env) -> dict:
    """{block: error} of the blocks that failed in the last run."""
    run_id, _ = env.last_run()
    return dict(env.query("SELECT block, error FROM blocks WHERE run_id=? AND status='failed'", run_id))


def png_size(data: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        return im.size


def add_connection(env, name, **conn):
    path = env.home / "connections.yaml"
    conns = yaml.safe_load(path.read_text()) if path.exists() else {}
    conns[name] = conn
    path.write_text(yaml.safe_dump(conns))


def mailer_port(env) -> int:
    return yaml.safe_load((env.home / "connections.yaml").read_text())["mailer"]["port"]


def files_holding(root, secret: str) -> list:
    """Files under root (the Taskloom folder: history, run files, …) that contain this text, except secrets.yaml."""
    return [p for p in root.rglob("*") if p.is_file() and p.name != "secrets.yaml" and secret.encode() in p.read_bytes()]


# --- a mail server that needs a login (and TLS) ------------------------------------------

PASSWORD = "pw-Rk81-mail-secret"


def _certificate(folder):
    """A self-signed certificate for 127.0.0.1: (cert.pem, key.pem)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = folder / "cert.pem", folder / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return cert_path, key_path


@contextlib.contextmanager
def login_mail_server(env, security="none", stored_password=PASSWORD):
    """A local SMTP server that needs the user `reporter` with PASSWORD, registered as the connection `secure`
    (its password in the secret store). With starttls it refuses to log in before STARTTLS; with ssl it
    speaks TLS only. The runner trusts its certificate. Yields (logins, messages)."""
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import AuthResult
    from sshserver import free_port

    logins, messages = [], []

    def authenticate(server, session, envelope, mechanism, data):
        logins.append((data.login.decode(), data.password.decode()))
        ok = (data.login, data.password) == (b"reporter", PASSWORD.encode())
        return AuthResult(success=ok, handled=False)  # the server answers 235 or 535

    class Inbox:
        async def handle_DATA(self, server, session, envelope):
            messages.append(email.message_from_bytes(envelope.content, policy=policy.default))
            return "250 OK"

    tls = {}
    if security != "none":
        cert, key = _certificate(env.root)
        context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        context.load_cert_chain(cert, key)
        env.env["SSL_CERT_FILE"] = str(cert)
        tls = {"tls_context": context, "require_starttls": True} if security == "starttls" else {"ssl_context": context}
    port = free_port()
    controller = Controller(Inbox(), hostname="127.0.0.1", port=port, authenticator=authenticate, auth_required=True,
                            auth_require_tls=security == "starttls", **tls)
    controller.start()
    add_connection(env, "secure", kind="smtp", host="127.0.0.1", port=port, security=security, username="reporter",
                   password="secret:secure_password", sender="reports@example.com")
    env.write("home/secrets.yaml", f"secure_password: {stored_password}\n").chmod(0o600)
    try:
        yield logins, messages
    finally:
        controller.stop()


@contextlib.contextmanager
def picky_mail_server(env, refuse=(), reject_data=None):
    """A local SMTP server registered as the connection `picky`: it refuses the addresses in `refuse`
    (550) and, with reject_data, refuses every message with that reply. Yields the delivered
    [(envelope recipients, message)]."""
    from aiosmtpd.controller import Controller
    from sshserver import free_port

    delivered = []

    class Inbox:
        async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
            if address in refuse:
                return "550 5.1.1 no such mailbox here"
            envelope.rcpt_tos.append(address)
            return "250 OK"

        async def handle_DATA(self, server, session, envelope):
            if reject_data:
                return reject_data
            delivered.append((list(envelope.rcpt_tos), email.message_from_bytes(envelope.content, policy=policy.default)))
            return "250 OK"

    port = free_port()
    controller = Controller(Inbox(), hostname="127.0.0.1", port=port)
    controller.start()
    add_connection(env, "picky", kind="smtp", host="127.0.0.1", port=port, sender="reports@example.com")
    try:
        yield delivered
    finally:
        controller.stop()


# --- HTML Template: tables -------------------------------------------------------------------

def test_a_table_in_a_template_shows_every_value_escaped_in_an_inline_styled_table_under_its_heading(env, smtp):
    result = run(env, """
    params:
      day: {type: date, value: 2026-10-02}
      regions: {type: text, value: "<all regions>"}
    blocks:
      data:
        type: logic.python
        config:
          inputs: []
          outputs: [table]
          code: |
            import datetime as dt
            table = pl.DataFrame({"region": ["EU <north> & co", "Zürich 東京", None],
                                  "stores": [1234567, 2, None], "total": [1234.5, 0.25, None],
                                  "opened": [dt.date(2026, 10, 2), None, None]})
      table:
        type: reports.html_template
        config: {template: "<h3>Totals for {day:%Y-%m-%d} {regions}</h3>{data}"}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Totals}
    edges:
      - data.table -> table.data
      - table.html -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    assert re.search(r"<h3[^>]*>Totals for 2026-10-02 &lt;all regions&gt;</h3>", html)
    assert html.index("Totals for") < html.index(">region<")  # a heading above the table
    for column in ("region", "stores", "total", "opened"):
        assert re.search(rf'<th [^>]*style="[^"]+"[^>]*>{column}</th>', html)  # Outlook: inline styles only
    for value in ("EU &lt;north&gt; &amp; co", "Zürich 東京", "1,234,567", "1,234.50", "0.25", "2026-10-02"):
        assert f">{value}<" in html
    assert "None" not in html and "null" not in html  # a missing value is an empty cell
    text = text_of(msg)
    assert "EU <north> & co" in text and "Zürich 東京" in text


def test_html_template_max_table_rows_shows_the_first_rows_and_says_how_many_there_are(env, smtp):
    result = run(env, """
    blocks:
      small:
        type: logic.python
        config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'n': list(range(1, 31))})"}
      big:
        type: logic.python
        config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'n': list(range(1001, 1601))})"}
      huge:
        type: logic.python
        config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'n': range(100_000, 200_000)})"}
      ten:
        type: reports.html_template
        config: {max_rows: 10}
      default:
        type: reports.html_template
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Long}
    edges:
      - small.table -> ten.data
      - big.table -> default.data
      - ten.html -> mail.body
      - default.html -> mail.body
      - huge.table -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert ">10<" in html and ">11<" not in html and "10 of 30 rows" in html
    assert ">1,500<" in html and ">1,501<" not in html and "500 of 600 rows" in html  # 500 by default
    assert "500 of 100,000 rows" in html  # a table straight into the body is cut too


def test_a_template_with_an_empty_table_still_sends_its_header(env, smtp):
    result = run(env, """
    blocks:
      data:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'x' as region, 1.5 as total where false"}
      table:
        type: reports.html_template
        config: {template: "<h3>No sales today</h3>{data}"}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Empty}
    edges:
      - data.result -> table.data
      - table.html -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    html = html_of(smtp.messages[0])
    assert "No sales today" in html and ">region<" in html and ">total<" in html


# --- Chart -----------------------------------------------------------------------------------

def test_every_chart_kind_goes_into_the_email_as_an_inline_image_of_the_size_set(env, smtp):
    charts = {
        "bar": "{kind: bar, x: region, y: [total, stores], title: 'Sales {day}', width: 300, height: 200}",
        "barh": "{kind: barh, x: region, y: [total], width: 300, height: 200}",
        "line": "{kind: line, x: opened, y: [total], width: 300, height: 200}",
        "area": "{kind: area, x: opened, y: [total, stores], width: 300, height: 200}",
        "scatter": "{kind: scatter, x: stores, y: [total], width: 300, height: 200}",
        "pie": "{kind: pie, x: region, y: [total], width: 300, height: 200}",
        "hist": "{kind: hist, y: [total], width: 300, height: 200}",
        "no_x": "{y: [total], width: 300, height: 200}",  # x is optional
        "default": "{x: region, y: [total]}",  # a bar chart, 640 x 360
    }
    blocks = "".join(f"      {name}:\n        type: reports.chart\n        config: {config}\n" for name, config in charts.items())
    edges = "".join(f"      - data.table -> {name}.table\n      - {name}.image -> mail.body\n" for name in charts)
    result = run(env, f"""
    {DAY}blocks:
      data:
        type: logic.python
        config:
          inputs: []
          outputs: [table]
          code: |
            import datetime as dt
            table = pl.DataFrame({{"region": ["APAC", "EU", "US"], "opened": [dt.date(2026, 10, d) for d in (1, 2, 5)],
                                  "total": [10.5, 20.0, 15.25], "stores": [3, 4, 2]}})
{blocks}      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Charts}}
    edges:
{edges}""")

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    shown = re.findall(r'<img src="cid:([^"]+)" width="(\d+)" height="(\d+)"', html)
    images = inline_images(msg)
    assert sorted(cid for cid, _, _ in shown) == sorted(images)  # every chart shown is in the email
    assert [(int(w), int(h)) for _, w, h in shown] == [(300, 200)] * 8 + [(640, 360)]  # in the order of the connections
    for cid, w, h in shown:  # never a picture smaller than the size it is shown at
        width, height = png_size(images[cid])
        assert width >= int(w) and height >= int(h)
    assert "data:" not in html and "<svg" not in html


def test_chart_max_points_charts_the_first_rows_and_warns(env, smtp):
    result = run(env, """
    blocks:
      data:
        type: logic.python
        config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'n': list(range(50)), 'v': list(range(50))})"}
      chart:
        type: reports.chart
        config: {kind: line, x: n, y: [v], max_points: 10}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Points}
    edges:
      - data.table -> chart.table
      - chart.image -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    run_id, _ = env.last_run()
    warnings = [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block='chart' AND level='WARNING'", run_id)]
    assert len(warnings) == 1 and "10" in warnings[0] and "50" in warnings[0]
    assert len(inline_images(smtp.messages[0])) == 1


def test_chart_settings_that_cannot_work_fail_with_a_clear_message(env):
    result = run(env, f"""
    blocks:
{DATA}      nine:
        type: logic.python
        config: {{inputs: [], outputs: [table], code: "table = pl.DataFrame({{f'c{{i}}': [1, 2] for i in range(9)}})"}}
      nine_rows:
        type: logic.python
        config: {{inputs: [], outputs: [table], code: "table = pl.DataFrame({{'r': list('abcdefghi'), 'v': list(range(9))}})"}}
      unknown_column:
        type: reports.chart
        config: {{x: region, y: [revenue]}}
      pie_of_two:
        type: reports.chart
        config: {{kind: pie, x: region, y: [total, stores]}}
      nine_series:
        type: reports.chart
        config: {{y: [c0, c1, c2, c3, c4, c5, c6, c7, c8]}}
      no_series:
        type: reports.chart
        config: {{x: region, y: []}}
      pie_of_nine:
        type: reports.chart
        config: {{kind: pie, x: r, y: [v]}}
    edges:
      - data.table -> unknown_column.table
      - data.table -> pie_of_two.table
      - nine.table -> nine_series.table
      - data.table -> no_series.table
      - nine_rows.table -> pie_of_nine.table
    """)

    assert result.returncode == 1
    failed = errors(env)
    assert set(failed) == {"unknown_column", "pie_of_two", "nine_series", "no_series", "pie_of_nine"}
    assert "revenue" in failed["unknown_column"] and "total" in failed["unknown_column"]  # names the columns there are
    assert "one y column" in failed["pie_of_two"]
    assert "8" in failed["nine_series"]
    assert "y column" in failed["no_series"]
    assert "8" in failed["pie_of_nine"] and "slices" in failed["pie_of_nine"]


def test_chart_kind_and_y_are_checked_before_the_run(env):
    flow = env.write("flow.yaml", f"""
    blocks:
{DATA}      donut:
        type: reports.chart
        config: {{kind: donut, x: region, y: [total]}}
      no_y:
        type: reports.chart
        config: {{x: region}}
    edges:
      - data.table -> donut.table
      - data.table -> no_y.table
    """)

    result = env.taskloom("validate", flow)

    assert result.returncode == 2
    assert "donut: Kind:" in result.stderr and "pie" in result.stderr
    assert "no_y: Y is required" in result.stderr


def test_a_chart_of_a_table_with_a_missing_value_is_drawn(env, smtp):
    """A region without sales has an empty total in the CSV: every chart of it still draws."""
    env.write("sales.csv", "region,total\nAPAC,10.5\nEU,\nUS,15.25\n")
    kinds = ["bar", "barh", "line", "area", "scatter", "pie"]
    blocks = "".join(f"      {k}:\n        type: reports.chart\n        config: {{kind: {k}, x: region, y: [total]}}\n"
                     for k in kinds)
    edges = "".join(f"      - data.table -> {k}.table\n      - {k}.image -> mail.body\n" for k in kinds)
    result = run(env, f"""
    blocks:
      data:
        type: data.read_file
        config: {{path: sales.csv}}
{blocks}      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Charts}}
    edges:
{edges}""")

    assert errors(env) == {}
    assert result.returncode == 0
    assert len(inline_images(smtp.messages[0])) == len(kinds)


def test_a_pie_chart_of_decimal_values_from_a_database_is_drawn(env, smtp):
    """Money columns from databases are DECIMAL; a bar chart of them draws, so does a pie chart."""
    result = run(env, """
    blocks:
      data:
        type: data.duckdb_sql
        config:
          inputs: []
          sql: "select * from (values ('APAC', 10.50::DECIMAL(12, 2)), ('EU', 20.00::DECIMAL(12, 2))) t(region, total)"
      bar:
        type: reports.chart
        config: {kind: bar, x: region, y: [total]}
      pie:
        type: reports.chart
        config: {kind: pie, x: region, y: [total]}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Shares}
    edges:
      - data.result -> bar.table
      - data.result -> pie.table
      - bar.image -> mail.body
      - pie.image -> mail.body
    """)

    assert errors(env) == {}
    assert result.returncode == 0
    assert len(inline_images(smtp.messages[0])) == 2


def test_a_bar_chart_of_an_empty_table_still_sends(env, smtp):
    result = run(env, """
    blocks:
      data:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'x' as region, 1.5 as total where false"}
      chart:
        type: reports.chart
        config: {kind: bar, x: region, y: [total], title: No sales}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Empty}
    edges:
      - data.result -> chart.table
      - chart.image -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    assert len(inline_images(smtp.messages[0])) == 1


# --- HTML Template ---------------------------------------------------------------------------

def test_html_template_mixes_text_parameters_tables_charts_and_html_from_other_blocks(env, smtp):
    from PIL import Image

    Image.new("RGB", (240, 60), "#2c5d8f").save(env.root / "logo.png")
    result = run(env, f"""
    {DAY}blocks:
{DATA}      note:
        type: logic.python
        config: {{inputs: [], outputs: [text], code: "text = 'Revenue < 5 & rising'"}}
      logo:
        type: logic.python
        config: {{inputs: [], outputs: [path], code: "path = 'logo.png'"}}
      chart:
        type: reports.chart
        config: {{kind: bar, x: region, y: [total]}}
      detail:
        type: reports.html_template
        config: {{template: "<h3>Detail by region</h3>{{data}}"}}
      inner:
        type: reports.html_template
        config: {{template: "<p>Inner part</p>{{chart}}"}}
      body:
        type: reports.html_template
        config:
          template: |
            {{logo}}
            <h2>Ventas del {{day}} — Zürich</h2>
            {{data}}
            {{inner}}
            {{detail}}
            <p>{{note}}</p>
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Template}}
    edges:
      - data.table -> chart.table
      - data.table -> detail.data
      - logo.path -> body.data
      - data.table -> body.data
      - chart.image -> inner.data
      - inner.html -> body.data
      - detail.html -> body.data
      - note.text -> body.data
      - body.html -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    html = html_of(msg)
    order = [html.index(s) for s in ("Ventas del 2026-10-02 — Zürich", ">APAC<", "Inner part", "Detail by region",
                                     "Revenue &lt; 5 &amp; rising")]
    assert order == sorted(order)
    shown = re.findall(r'<img src="cid:([^"]+)"', html)
    images = inline_images(msg)
    assert sorted(shown) == sorted(images) and len(images) == 2  # the logo and the inner part's chart
    assert png_size(images[shown[0]]) == (240, 60)  # the logo comes first


def test_an_html_template_with_its_one_input_renders_a_table(env, smtp):
    """A new HTML Template block has one input for what it shows; a table into it renders under its block's name."""
    from taskloom.blocks.reports import HtmlTemplate

    [name] = HtmlTemplate.inputs
    result = run(env, f"""
    blocks:
{DATA}      body:
        type: reports.html_template
        config: {{template: "<p>Totals</p>{{data}}"}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Default}}
    edges:
      - data.table -> body.{name}
      - body.html -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    assert ">EU<" in html_of(smtp.messages[0])


def test_html_template_mistakes_are_refused_before_the_run_and_send_nothing(env, smtp):
    result = run(env, f"""
    blocks:
{DATA}      typo:
        type: reports.html_template
        config: {{template: "<p>{{totls}}</p>"}}
      broken:
        type: reports.html_template
        config: {{template: "{{data"}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Template}}
    edges:
      - data.table -> typo.data
      - data.table -> broken.data
      - typo.html -> mail.body
      - broken.html -> mail.body
    """)

    assert result.returncode == 2
    assert "error: typo: Template: {totls} is not an input or a flow parameter (inputs: data;" in result.stderr
    assert "error: broken: Template: expected '}' before end of string;" in result.stderr
    assert smtp.messages == []
    assert not (env.home / "history.db").exists() or env.query("SELECT count(*) FROM runs") == [(0,)]


# --- To Excel ---------------------------------------------------------------------------------

def test_to_excel_writes_one_sheet_per_table_with_all_its_data(env, smtp):
    result = run(env, f"""
    {DAY}blocks:
      sales:
        type: logic.python
        config:
          inputs: []
          outputs: [table]
          code: |
            import datetime as dt
            table = pl.DataFrame({{"store": ["Zürich 東京", "A&B <1>", None], "units": [3, None, 1_000_000],
                                  "amount": [10.25, 0.5, None], "day": [dt.date(2026, 10, 2), None, dt.date(2026, 10, 1)],
                                  "open": [True, False, None]}})
      totals:
        type: logic.python
        config: {{inputs: [], outputs: [table], code: "table = pl.DataFrame({{'region': ['EU'], 'total': [10.75]}})"}}
      excel:
        type: reports.excel
        config: {{path: "out/{{day:%Y}}/report_{{day:%Y%m%d}}.xlsx", sheets: [sales, totals]}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Excel}}
    edges:
      - sales.table -> excel.sales
      - totals.table -> excel.totals
      - excel.file -> mail.attachments
    """)

    assert result.returncode == 0, result.stderr
    path = env.root / "out" / "2026" / "report_20261002.xlsx"
    sales = pl.read_excel(path, sheet_name="sales")
    assert sales["store"].to_list() == ["Zürich 東京", "A&B <1>", None]
    assert sales["units"].to_list() == [3, None, 1_000_000]
    assert sales["amount"].to_list() == [10.25, 0.5, None]
    assert sales["day"].to_list() == [dt.date(2026, 10, 2), None, dt.date(2026, 10, 1)]
    assert sales["open"].to_list() == [True, False, None]
    assert pl.read_excel(path, sheet_name="totals").to_dicts() == [{"region": "EU", "total": 10.75}]
    with zipfile.ZipFile(path) as z:
        assert not any(n.startswith("xl/charts/") for n in z.namelist())  # chart: none by default
    [attachment] = list(smtp.messages[0].iter_attachments())
    assert attachment.get_filename() == "report_20261002.xlsx"
    assert attachment.get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert attachment.get_content() == path.read_bytes()


def test_to_excel_adds_each_kind_of_native_excel_chart(env):
    kinds = {"bar": ("barChart", '<c:barDir val="col"/>'), "barh": ("barChart", '<c:barDir val="bar"/>'),
             "line": ("lineChart", ""), "area": ("areaChart", ""), "pie": ("pieChart", ""), "scatter": ("scatterChart", "")}
    blocks = "".join(f"      {k}:\n        type: reports.excel\n"
                     f"        config: {{path: out/{k}.xlsx, chart: {k}, chart_x: region, chart_y: [total]}}\n" for k in kinds)
    edges = "".join(f"      - data.table -> {k}.table\n" for k in kinds)
    result = run(env, f"""
    {DAY}blocks:
{DATA}{blocks}      second:
        type: reports.excel
        config:
          path: out/second.xlsx
          sheets: [first, by_region]
          chart: bar
          chart_sheet: by_region
          chart_x: region
          chart_y: [total, stores]
          chart_title: "Totals {{day}}"
    edges:
{edges}      - data.table -> second.first
      - data.table -> second.by_region
    """)

    assert result.returncode == 0, result.stderr
    for kind, (element, detail) in kinds.items():
        with zipfile.ZipFile(env.root / "out" / f"{kind}.xlsx") as z:
            chart = z.read("xl/charts/chart1.xml").decode()
        assert f"<c:{element}>" in chart and detail in chart, kind
        assert "table!$A$2:$A$4" in chart and "table!$B$2:$B$4" in chart, kind  # categories, values
    with zipfile.ZipFile(env.root / "out" / "second.xlsx") as z:
        chart = z.read("xl/charts/chart1.xml").decode()
    assert chart.count("<c:ser>") == 2 and "by_region!$B$2:$B$4" in chart and "by_region!$C$2:$C$4" in chart
    assert "<a:t>Totals 2026-10-02</a:t>" in chart


def test_to_excel_chart_without_a_chart_sheet_uses_and_sits_on_the_first_sheet(env):
    result = run(env, f"""
    blocks:
{DATA}      returns:
        type: logic.python
        config: {{inputs: [], outputs: [table], code: "table = pl.DataFrame({{'region': ['EU', 'US'], 'total': [1.0, 2.0]}})"}}
      excel:
        type: reports.excel
        config: {{path: out/two.xlsx, sheets: [sales, returns], chart: bar, chart_x: region, chart_y: [total]}}
    edges:
      - data.table -> excel.sales
      - returns.table -> excel.returns
    """)

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(env.root / "out" / "two.xlsx") as z:
        chart = z.read("xl/charts/chart1.xml").decode()
        rels = z.read("xl/_rels/workbook.xml.rels").decode()
        sheet_file = {name: re.search(rf'Id="{rid}"[^>]*Target="worksheets/([^"]+)"', rels).group(1)
                      for name, rid in re.findall(r'<sheet name="([^"]+)" sheetId="\d+" r:id="(rId\d+)"',
                                                  z.read("xl/workbook.xml").decode())}
        with_drawing = {name for name, file in sheet_file.items()
                        if f"xl/worksheets/_rels/{file}.rels" in z.namelist()
                        and 'relationships/drawing"' in z.read(f"xl/worksheets/_rels/{file}.rels").decode()}
    assert list(sheet_file) == ["sales", "returns"]
    assert chart.count("<c:ser>") == 1 and "sales!$A$2:$A$4" in chart and "sales!$B$2:$B$4" in chart
    assert "returns!" not in chart
    assert with_drawing == {"sales"}  # the chart is on the sheet whose data it shows


def test_to_excel_chart_settings_that_cannot_work_fail_with_a_clear_message(env):
    result = run(env, f"""
    blocks:
{DATA}      no_y:
        type: reports.excel
        config: {{path: out/a.xlsx, chart: bar, chart_x: region}}
      unknown_y:
        type: reports.excel
        config: {{path: out/b.xlsx, chart: bar, chart_y: [revenue]}}
      unknown_sheet:
        type: reports.excel
        config: {{path: out/c.xlsx, chart: line, chart_sheet: nope, chart_y: [total]}}
    edges:
      - data.table -> no_y.table
      - data.table -> unknown_y.table
      - data.table -> unknown_sheet.table
    """)

    assert result.returncode == 1
    failed = errors(env)
    assert set(failed) == {"no_y", "unknown_y", "unknown_sheet"}
    assert "Chart y" in failed["no_y"] and "total" in failed["no_y"]
    assert "revenue" in failed["unknown_y"]
    assert "nope" in failed["unknown_sheet"] and "table" in failed["unknown_sheet"]


def test_to_excel_refuses_a_table_too_big_for_excel(env):
    result = run(env, """
    blocks:
      data:
        type: logic.python
        config: {inputs: [], outputs: [table], code: "table = pl.DataFrame({'n': range(1_048_576)})"}
      excel:
        type: reports.excel
        config: {path: out/big.xlsx}
    edges:
      - data.table -> excel.table
    """)

    assert result.returncode == 1
    assert "1,048,576" in errors(env)["excel"] and "1,048,575" in errors(env)["excel"]
    assert not (env.root / "out" / "big.xlsx").exists()


def test_to_excel_of_an_empty_table_writes_its_header(env):
    """A day without sales: the file is still written, with the column names, and its chart does not fail."""
    result = run(env, """
    blocks:
      data:
        type: data.duckdb_sql
        config: {inputs: [], sql: "select 'x' as region, 1.5 as total where false"}
      excel:
        type: reports.excel
        config: {path: out/empty.xlsx, chart: bar, chart_x: region, chart_y: [total]}
    edges:
      - data.result -> excel.table
    """)

    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(env.root / "out" / "empty.xlsx") as z:
        strings = z.read("xl/sharedStrings.xml").decode()
    assert "<t>region</t>" in strings and "<t>total</t>" in strings


# --- Send Email: body, recipients, subject --------------------------------------------------

def test_send_email_puts_the_message_first_then_each_body_part_in_order(env, smtp):
    result = run(env, f"""
    blocks:
{DATA}      intro:
        type: logic.python
        config: {{inputs: [], outputs: [text], code: "text = 'First line\\\\nsecond line\\\\n\\\\nNew paragraph'"}}
      detail:
        type: reports.html_template
        config: {{template: "<h3>Detail by region</h3>{{data}}"}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Report, message: "Hello team,"}}
    edges:
      - data.table -> detail.data
      - intro.text -> mail.body
      - data.table -> mail.body
      - detail.html -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    assert msg["From"] == "taskloom@example.com"  # the connection's sender
    html = html_of(msg)
    order = [html.index(s) for s in ("Hello team,", "First line", ">APAC<", "Detail by region")]
    assert order == sorted(order)
    assert re.search(r"First line<br>second line</p>\s*<p[^>]*>New paragraph</p>", html)
    text = text_of(msg)
    assert text.startswith("Hello team,") and "First line\nsecond line" in text and "New paragraph" in text
    assert "APAC" in text and "Detail by region" in text


def test_send_email_to_cc_subject_template_and_unusual_characters(env, smtp):
    result = run(env, f"""
    {DAY}blocks:
      mail:
        type: email.send
        config:
          connection: mailer
          to: ["Ana Pérez <ana@example.com>", bob@example.com]
          cc: [carol@example.com, dan@example.com]
          subject: "Ventas {{day:%d/%m/%Y}} — Zürich ✓"
          message: "Grüße aus 東京 ✓ {{day:%d.%m.}}"
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    assert msg["To"] == "Ana Pérez <ana@example.com>, bob@example.com"
    assert msg["Cc"] == "carol@example.com, dan@example.com"
    assert msg["Subject"] == "Ventas 02/10/2026 — Zürich ✓"
    assert text_of(msg).strip() == "Grüße aus 東京 ✓ 02.10." and "Grüße aus 東京 ✓ 02.10." in html_of(msg)


def test_every_to_and_cc_address_receives_the_email(env):
    """Not only the To/Cc headers: the mail server is asked to deliver to each to and cc address
    (a display name reduced to its bare address), and to each notify_email address when 'to' is empty."""
    env.write("home/settings.yaml", "notify_email: me@example.com; boss@example.com\n")
    with picky_mail_server(env) as delivered:
        result = run(env, """
        blocks:
          named:
            type: email.send
            config:
              connection: picky
              to: ["Ana Pérez <ana@example.com>", bob@example.com]
              cc: [carol@example.com, dan@example.com]
              subject: Named
              message: Hello
          from_settings:
            type: email.send
            config: {connection: picky, subject: From settings, message: Hello}
        """)

    assert result.returncode == 0, result.stderr
    received = {msg["Subject"]: recipients for recipients, msg in delivered}
    assert received == {"Named": ["ana@example.com", "bob@example.com", "carol@example.com", "dan@example.com"],
                        "From settings": ["me@example.com", "boss@example.com"]}


def test_a_connected_subject_wins_over_the_subject_setting(env, smtp):
    result = run(env, """
    blocks:
      title:
        type: logic.python
        config: {inputs: [], outputs: [subject], code: "subject = f'Computed {6 * 7}'"}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Static}
    edges:
      - title.subject -> mail.subject
    """)

    assert result.returncode == 0, result.stderr
    assert smtp.messages[0]["Subject"] == "Computed 42"


def test_an_email_without_a_subject_fails_clearly_and_sends_nothing(env, smtp):
    result = run(env, """
    blocks:
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], message: Hello}
    """)

    assert result.returncode == 1
    assert "subject" in errors(env)["mail"]
    assert smtp.messages == []


def test_empty_to_sends_to_notify_email_from_the_settings(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com; boss@example.com\n")
    result = run(env, """
    blocks:
      mail:
        type: email.send
        config: {connection: mailer, subject: Hi, message: Hello}
    """)

    assert result.returncode == 0, result.stderr
    assert smtp.messages[0]["To"] == "me@example.com, boss@example.com"


def test_addresses_separated_by_semicolons_all_get_the_email(env):
    """Outlook separates addresses with ';' and so does notify_email in the settings; typed like that into
    the 'to' field (the editor splits it at commas only), no address is silently dropped."""
    with picky_mail_server(env) as delivered:
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: picky, to: ["ana@example.com; bob@example.com"], subject: Hi, message: Hello}
        """)

    sent = [(recipients, msg["To"]) for recipients, msg in delivered]
    expected = (["ana@example.com", "bob@example.com"], "ana@example.com, bob@example.com")
    assert sent == [expected] or (result.returncode != 0 and sent == [])


def test_a_bcc_or_a_misspelt_parameter_is_refused_before_anything_is_sent(env, smtp):
    """Send Email has no bcc: written into the file anyway, it is refused rather than its recipients
    silently left out. A {name} that is no parameter is named before the run, too."""
    result = run(env, f"""
    {DAY}blocks:
      blind:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], bcc: [auditor@example.com], subject: Hi, message: Hello}}
      subject_typo:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: "Sales {{dya:%Y}}", message: Hello}}
      message_typo:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Hi, message: "Totals for {{regon}}"}}
    """)

    assert result.returncode == 2 and smtp.messages == []
    assert "blind: unknown setting(s) ['bcc']" in result.stderr
    assert "subject_typo: Subject: unknown parameter {dya}" in result.stderr
    assert "message_typo: Message: unknown parameter {regon}" in result.stderr


def test_a_subject_from_data_cannot_add_recipients_or_headers(env):
    with picky_mail_server(env) as delivered:
        run(env, """
        blocks:
          title:
            type: logic.python
            config: {inputs: [], outputs: [subject], code: "subject = 'Daily sales\\\\nBcc: intruder@example.com'"}
          mail:
            type: email.send
            config: {connection: picky, to: [team@example.com], message: Hello}
        edges:
          - title.subject -> mail.subject
        """)

    for recipients, msg in delivered:
        assert recipients == ["team@example.com"] and msg["Bcc"] is None


def test_no_recipients_at_all_fails_clearly_and_sends_nothing(env, smtp):
    result = run(env, """
    blocks:
      mail:
        type: email.send
        config: {connection: mailer, subject: Hi, message: Hello}
    """)

    assert result.returncode == 1
    assert "recipients" in errors(env)["mail"] and "notify_email" in errors(env)["mail"]
    assert smtp.messages == []


def test_text_with_tag_like_words_reaches_the_email_whole(env, smtp):
    """An error handler emailing a Python Code block's traceback: it says `in <module>:2` and has one
    line per code line; the email shows it as it is."""
    result = run(env, """
    blocks:
      boom:
        type: logic.python
        config: {inputs: [], code: "rows = None\\ntotal = len(rows)"}
      describe:
        type: logic.python
        config: {inputs: [error], outputs: [text], code: "text = error['traceback']"}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: It failed}
    edges:
      - boom.error -> describe.error
      - describe.text -> mail.body
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    text = text_of(msg)
    assert "in <module>:2" in text
    assert re.search(r"\n.*1 rows = None.*\n", text)  # still one line per line
    assert "&lt;module&gt;" in html_of(msg)


# --- Send Email: attachments and the saved copy -----------------------------------------------

def test_attachments_from_several_blocks_tables_and_lists_all_arrive(env, smtp):
    result = run(env, f"""
    blocks:
{DATA}      files:
        type: logic.python
        config:
          inputs: []
          outputs: [paths]
          code: |
            from pathlib import Path
            Path("notes.txt").write_text("plain notes")
            Path("año informe.csv").write_text("a,b\\n1,2\\n", encoding="utf-8")
            paths = ["notes.txt", "año informe.csv"]
      excel:
        type: reports.excel
        config: {{path: out/report.xlsx}}
      chart:
        type: reports.chart
        config: {{x: region, y: [total]}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Files, message: See attached.}}
    edges:
      - data.table -> excel.table
      - data.table -> chart.table
      - excel.file -> mail.attachments
      - data.table -> mail.attachments
      - files.paths -> mail.attachments
      - chart.image -> mail.attachments
    """)

    assert result.returncode == 0, result.stderr
    [msg] = smtp.messages
    attached = {p.get_filename(): p for p in msg.iter_attachments()}
    names = list(attached)
    assert len(names) == 5 and names[0] == "report.xlsx" and names[2:] == ["notes.txt", "año informe.csv", "chart.png"]
    assert attached["report.xlsx"].get_content() == (env.root / "out" / "report.xlsx").read_bytes()
    table_csv = attached[names[1]]  # the table, sent as CSV
    assert names[1].endswith(".csv") and table_csv.get_content_type() == "text/csv"
    assert pl.read_csv(io.BytesIO(table_csv.get_content().encode())).to_dicts() == [
        {"region": "APAC", "total": 10.5, "stores": 3}, {"region": "EU", "total": 20.0, "stores": 4},
        {"region": "US", "total": 15.25, "stores": 2}]
    assert attached["notes.txt"].get_content() == "plain notes"
    assert attached["año informe.csv"].get_content().replace("\r\n", "\n") == "a,b\n1,2\n"
    assert attached["chart.png"].get_content_type() == "image/png"
    assert inline_images(msg) == {}  # attached, not shown in the body


def test_a_missing_attachment_fails_clearly_and_sends_nothing(env, smtp):
    result = run(env, """
    blocks:
      files:
        type: logic.python
        config: {inputs: [], outputs: [path], code: "path = 'reports/missing.pdf'"}
      mail:
        type: email.send
        config: {connection: mailer, to: [team@example.com], subject: Files}
    edges:
      - files.path -> mail.attachments
    """)

    assert result.returncode == 1
    assert "reports/missing.pdf" in errors(env)["mail"]
    assert smtp.messages == []


def test_an_html_part_connected_to_attachments_fails_and_sends_nothing(env, smtp):
    """Attachments take files and tables (a table goes as CSV); an HTML Template's html is neither."""
    result = run(env, f"""
    blocks:
{DATA}      table:
        type: reports.html_template
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Files}}
    edges:
      - data.table -> table.data
      - table.html -> mail.attachments
    """)

    assert result.returncode == 1 and smtp.messages == []
    assert "mail" in errors(env)


def test_save_copy_writes_the_email_that_was_sent_as_an_eml_file(env, smtp):
    result = run(env, f"""
    {DAY}blocks:
{DATA}      chart:
        type: reports.chart
        config: {{x: region, y: [total]}}
      mail:
        type: email.send
        config: {{connection: mailer, to: [team@example.com], subject: Saved, save_copy: "copies/{{day:%Y%m%d}}/report.eml"}}
    edges:
      - data.table -> chart.table
      - chart.image -> mail.body
      - data.table -> mail.attachments
    """)

    assert result.returncode == 0, result.stderr
    [sent] = smtp.messages
    saved = email.message_from_bytes((env.root / "copies" / "20261002" / "report.eml").read_bytes(), policy=policy.default)
    assert (saved["Message-ID"], saved["Subject"], saved["To"]) == (sent["Message-ID"], "Saved", "team@example.com")
    assert html_of(saved) == html_of(sent) and inline_images(saved) == inline_images(sent)
    assert [p.get_content() for p in saved.iter_attachments()] == [p.get_content() for p in sent.iter_attachments()]


# --- the SMTP connection -----------------------------------------------------------------------

def test_starttls_with_a_login_sends_and_the_password_is_never_written_down(env):
    with login_mail_server(env, "starttls") as (logins, messages):
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: secure, to: [team@example.com], subject: Secure, message: Hello}
        """)
        tested = env.taskloom("connection", "test", "secure")

    assert result.returncode == 0, result.stderr
    assert [m["Subject"] for m in messages] == ["Secure"] and messages[0]["From"] == "reports@example.com"
    assert logins == [("reporter", PASSWORD)] * 2
    assert tested.returncode == 0 and "accepted the login" in tested.stdout
    assert files_holding(env.home, PASSWORD) == []
    assert PASSWORD not in result.stdout + result.stderr + tested.stdout + tested.stderr


def test_ssl_with_a_login_sends(env):
    with login_mail_server(env, "ssl") as (logins, messages):
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: secure, to: [team@example.com], subject: Over SSL, message: Hello}
        """)

    assert result.returncode == 0, result.stderr
    assert [m["Subject"] for m in messages] == ["Over SSL"] and logins == [("reporter", PASSWORD)]


def test_a_wrong_password_fails_clearly_without_showing_it(env):
    wrong = "pw-Wrong-9931-guess"
    with login_mail_server(env, "none", stored_password=wrong) as (logins, messages):
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: secure, to: [team@example.com], subject: Secure, message: Hello}
        """)
        tested = env.taskloom("connection", "test", "secure")

    assert result.returncode == 1 and messages == []
    assert "Authentication" in errors(env)["mail"]
    assert tested.returncode == 1 and "Authentication" in tested.stderr
    assert files_holding(env.home, wrong) == []
    assert wrong not in result.stdout + result.stderr + tested.stdout + tested.stderr


def test_a_recipient_the_mail_server_refuses_is_reported_not_silently_dropped(env):
    with picky_mail_server(env, refuse={"ghost@example.com"}):
        result = run(env, """
        blocks:
          some:
            type: email.send
            config: {connection: picky, to: [team@example.com, ghost@example.com], subject: Partly, message: Hello}
          nobody:
            type: email.send
            config: {connection: picky, to: [ghost@example.com], subject: Nobody, message: Hello}
        """)

    assert result.returncode == 1
    run_id, _ = env.last_run()
    failed = errors(env)
    assert "ghost@example.com" in failed["nobody"]
    said = [m for (m,) in env.query(
        "SELECT message FROM logs WHERE run_id=? AND block='some' AND level IN ('WARNING', 'ERROR')", run_id)]
    assert any("ghost@example.com" in m for m in said + [failed.get("some", "")]), \
        "the mail server refused ghost@example.com, and the block said it sent the email to it"


def test_a_message_the_mail_server_rejects_fails_the_block_with_its_reason(env):
    with picky_mail_server(env, reject_data="552 5.3.4 message too big for this mailbox") as delivered:
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: picky, to: [team@example.com], subject: Big, message: Hello}
        """)

    assert result.returncode == 1 and delivered == []
    assert "too big" in errors(env)["mail"]


def test_an_smtp_connection_made_in_the_connections_window_logs_in_and_sends(env, win, qtbot, monkeypatch):
    import keyring.core
    from keyring.backends import fail
    from PySide6.QtWidgets import QDialogButtonBox, QInputDialog

    from taskloom.editor.dialogs import ConnectionsDialog

    monkeypatch.setattr(keyring.core, "_keyring_backend", fail.Keyring())  # never the real credential store
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("relay", True)))
    with login_mail_server(env, "starttls") as (logins, messages):
        monkeypatch.setenv("SSL_CERT_FILE", env.env["SSL_CERT_FILE"])  # the window's test trusts the server too
        port = yaml.safe_load((env.home / "connections.yaml").read_text())["secure"]["port"]
        dialog = ConnectionsDialog(win.home, win, new_kind="smtp")
        for key, text in (("host", "127.0.0.1"), ("port", str(port)), ("security", "starttls"),
                          ("username", "reporter"), ("sender", "reports@example.com")):
            dialog.edits[key].setText(text)
        dialog.password.setText(PASSWORD)
        dialog.test_button.click()  # Save and test connection
        qtbot.waitUntil(lambda: "✓" in dialog.test_result.text() or "✕" in dialog.test_result.text(), timeout=30_000)
        tested = dialog.test_result.text()
        dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()
        result = run(env, """
        blocks:
          mail:
            type: email.send
            config: {connection: relay, to: [team@example.com], subject: Via the relay, message: Hello}
        """)

    assert "answered and accepted the login" in tested
    text = (env.home / "connections.yaml").read_text()
    assert PASSWORD not in text
    assert yaml.safe_load(text)["relay"] == {"kind": "smtp", "host": "127.0.0.1", "port": port, "security": "starttls",
                                             "username": "reporter", "sender": "reports@example.com",
                                             "password": "secret:relay_password"}
    assert result.returncode == 0, result.stderr
    assert [m["Subject"] for m in messages] == ["Via the relay"] and logins == [("reporter", PASSWORD)] * 2


def test_connection_test_says_the_mail_server_answered(env, smtp):
    result = env.taskloom("connection", "test", "mailer")

    assert result.returncode == 0, result.stderr
    assert f"127.0.0.1:{mailer_port(env)}" in result.stdout and "answered" in result.stdout


def test_an_unreachable_mail_server_fails_the_block_and_the_connection_test(env):
    from sshserver import free_port

    add_connection(env, "down", kind="smtp", host="127.0.0.1", port=free_port(), sender="reports@example.com")
    result = run(env, """
    blocks:
      mail:
        type: email.send
        config: {connection: down, to: [team@example.com], subject: Hi, message: Hello}
    """)
    tested = env.taskloom("connection", "test", "down")

    assert result.returncode == 1 and "refused" in errors(env)["mail"].lower()
    assert tested.returncode == 1 and "refused" in tested.stderr.lower()


def test_a_missing_or_wrong_kind_of_connection_is_reported_before_the_run(env):
    add_connection(env, "appserver", kind="ssh", host="server01.example.com", username="me")
    result = run(env, """
    blocks:
      missing:
        type: email.send
        config: {connection: nomailer, to: [team@example.com], subject: Hi}
      wrong_kind:
        type: email.send
        config: {connection: appserver, to: [team@example.com], subject: Hi}
    """)

    assert result.returncode == 2
    assert "no connection named 'nomailer'" in result.stderr
    assert "connection 'appserver' is ssh, expected smtp" in result.stderr
    assert not (env.home / "history.db").exists() or env.query("SELECT count(*) FROM runs") == [(0,)]


def test_a_connection_without_a_sender_or_host_fails_clearly(env, smtp):
    port = mailer_port(env)
    add_connection(env, "no_sender", kind="smtp", host="127.0.0.1", port=port)
    add_connection(env, "no_host", kind="smtp", port=port, sender="reports@example.com")
    result = run(env, """
    blocks:
      a:
        type: email.send
        config: {connection: no_sender, to: [team@example.com], subject: Hi, message: Hello}
      b:
        type: email.send
        config: {connection: no_host, to: [team@example.com], subject: Hi, message: Hello}
    """)

    assert result.returncode == 1
    failed = errors(env)
    assert "sender" in failed["a"] and "host" in failed["b"]
    assert smtp.messages == []


def test_an_unknown_security_setting_is_refused_instead_of_sending_unencrypted(env, smtp):
    """Security is none, starttls or ssl; 'tls' is none of them, so nothing goes out in plain text."""
    add_connection(env, "typo", kind="smtp", host="127.0.0.1", port=mailer_port(env), security="tls",
                   sender="reports@example.com")
    result = run(env, """
    blocks:
      mail:
        type: email.send
        config: {connection: typo, to: [team@example.com], subject: Hi, message: Hello}
    """)

    assert smtp.messages == [], "it went out unencrypted"
    assert result.returncode != 0
    assert "security" in (result.stderr if result.returncode == 2 else errors(env)["mail"])


# --- notifications -----------------------------------------------------------------------------

OK_FLOW = """
    name: weekly
    blocks:
      fine:
        type: logic.python
        config: {inputs: [], code: "output = 1"}
"""
FAILING_FLOW = """
    name: nightly
    blocks:
      boom:
        type: logic.python
        config: {inputs: [], code: "raise RuntimeError('the warehouse is down')"}
"""


def test_notify_on_success_emails_every_address_in_the_settings(env, smtp):
    env.write("home/settings.yaml", "notify_email: [me@example.com, boss@example.com]\nsmtp_connection: mailer\n"
                                    "notify_on: [success]\n")

    assert run(env, OK_FLOW, "--notify", name="ok.yaml").returncode == 0
    assert run(env, FAILING_FLOW, "--notify", name="failing.yaml").returncode == 1  # failure is not in notify_on

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] weekly succeeded" and msg["To"] == "me@example.com, boss@example.com"
    html = html_of(msg)
    assert "weekly" in html and ">fine<" in html and ">success<" in html


def test_a_flows_notify_section_turns_emails_off_or_sends_them_elsewhere(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")  # on failure
    off = FAILING_FLOW.replace("name: nightly", "name: quiet\n    notify: {when: []}")
    elsewhere = FAILING_FLOW.replace("name: nightly", "name: loud\n    notify: {to: [ops@example.com, oncall@example.com]}")

    assert run(env, off, "--notify", name="off.yaml").returncode == 1
    assert run(env, elsewhere, "--notify", name="elsewhere.yaml").returncode == 1

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] loud failed" and msg["To"] == "ops@example.com, oncall@example.com"


def test_a_notification_reaches_every_address_it_names(env):
    """The mail server is asked to deliver the notification to each notify_email address, or to each
    address of a flow's notify.to."""
    with picky_mail_server(env) as delivered:
        env.write("home/settings.yaml", "notify_email: [me@example.com, boss@example.com]\nsmtp_connection: picky\n")
        elsewhere = FAILING_FLOW.replace("name: nightly", "name: loud\n    notify: {to: [ops@example.com, oncall@example.com]}")
        settings = run(env, FAILING_FLOW, "--notify", name="settings.yaml")
        flow = run(env, elsewhere, "--notify", name="elsewhere.yaml")

    assert settings.returncode == 1 and flow.returncode == 1
    assert "could not send" not in settings.stderr + flow.stderr
    assert [(msg["Subject"], recipients) for recipients, msg in delivered] == [
        ("[Taskloom] nightly failed", ["me@example.com", "boss@example.com"]),
        ("[Taskloom] loud failed", ["ops@example.com", "oncall@example.com"])]


def test_the_failure_email_names_the_block_and_its_error_but_never_a_password(env, smtp):
    secret = "Hunter2-db-secret-77"
    env.write("home/secrets.yaml", f"db_password: {secret}\n").chmod(0o600)
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    result = run(env, """
    name: loader
    params:
      region: {type: text, value: EU}
    blocks:
      load:
        type: logic.python
        config:
          inputs: []
          code: |
            password = ctx.home.get_secret("db_password")
            raise RuntimeError(f"login refused for {password}")
    """, "--notify")

    assert result.returncode == 1
    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] loader failed"
    html = html_of(msg)
    assert ">load<" in html and ">failed<" in html and "RuntimeError: login refused for ***" in html
    assert "region" in html and "EU" in html  # the parameters of the run
    assert secret not in msg.as_string() and secret not in result.stdout + result.stderr
    assert files_holding(env.home, secret) == []


def test_the_notification_shows_parameter_values_as_they_are(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    flow = FAILING_FLOW.replace("name: nightly", "name: nightly\n    params:\n      region: {type: text, value: Zürich}")

    assert run(env, flow, "--notify").returncode == 1

    assert "Zürich" in html_of(smtp.messages[0])


def test_a_notification_problem_does_not_change_the_run_result(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: nomailer\nnotify_on: [failure, success]\n")
    failing = run(env, FAILING_FLOW, "--notify", name="failing.yaml")
    ok = run(env, OK_FLOW, "--notify", name="ok.yaml")

    assert failing.returncode == 1 and ok.returncode == 0
    assert "could not send the notification email" in failing.stderr and "nomailer" in failing.stderr
    assert "could not send the notification email" in ok.stderr

    from sshserver import free_port

    add_connection(env, "down", kind="smtp", host="127.0.0.1", port=free_port(), sender="reports@example.com")
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: down\n")
    down = run(env, FAILING_FLOW, "--notify", name="failing.yaml")
    assert down.returncode == 1 and "could not send the notification email" in down.stderr
    assert smtp.messages == []


def test_without_an_smtp_connection_or_an_address_nothing_is_emailed(env, smtp):
    env.write("home/settings.yaml", "notify_email: me@example.com\n")
    no_connection = run(env, FAILING_FLOW, "--notify")
    env.write("home/settings.yaml", "smtp_connection: mailer\n")
    no_address = run(env, FAILING_FLOW, "--notify")

    for result in (no_connection, no_address):
        assert result.returncode == 1 and "could not send" not in result.stderr
    assert smtp.messages == []


def test_notifications_log_in_with_the_stored_password_and_never_show_it(env):
    with login_mail_server(env, "starttls") as (logins, messages):
        env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: secure\n")
        result = run(env, FAILING_FLOW, "--notify")

    assert result.returncode == 1
    assert [(m["Subject"], m["From"], m["To"]) for m in messages] == [
        ("[Taskloom] nightly failed", "reports@example.com", "me@example.com")]
    assert logins == [("reporter", PASSWORD)]
    assert PASSWORD not in result.stdout + result.stderr and files_holding(env.home, PASSWORD) == []


def test_a_wrong_notify_section_is_reported(env):
    wrong_event = env.write("a.yaml", OK_FLOW.replace("name: weekly", "name: a\n    notify: {when: [always]}"))
    on_key = env.write("b.yaml", OK_FLOW.replace("name: weekly", "name: b\n    notify: {on: [failure]}"))

    a, b = env.taskloom("validate", wrong_event), env.taskloom("validate", on_key)

    assert a.returncode == 2 and "notify.when can contain failure, success and skipped" in a.stderr
    assert b.returncode == 2 and "notify looks like" in b.stderr


def _scheduled(env, monkeypatch, name, text):
    from taskloom.config import Home
    from taskloom.scheduler import Scheduler

    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")  # the runs never use the real store
    home = Home(env.home)
    home.set_scheduled(env.write(name, text), True)
    return Scheduler(home)


def test_a_failed_scheduled_run_is_emailed(env, smtp, monkeypatch):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    scheduler = _scheduled(env, monkeypatch, "nightly.yaml", FAILING_FLOW.replace(
        "blocks:", 'triggers:\n      - schedule: {cron: "* * * * *"}\n    blocks:', 1))
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(t0)
    scheduler.tick(t0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] nightly failed" and msg["To"] == "me@example.com"
    assert "the warehouse is down" in html_of(msg)


def test_a_scheduled_run_that_crashes_is_emailed(env, smtp, monkeypatch):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    scheduler = _scheduled(env, monkeypatch, "nightly.yaml", """
        name: nightly
        triggers:
          - schedule: {cron: "* * * * *"}
        blocks:
          die:
            type: logic.python
            config: {inputs: [], code: "import os; os._exit(3)"}
    """)
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(t0)
    scheduler.tick(t0 + dt.timedelta(minutes=1))
    scheduler.wait_for_runs()

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] nightly crashed" and msg["To"] == "me@example.com"


def test_missed_runs_skipped_are_emailed_once_with_how_many(env, smtp, monkeypatch):
    env.write("home/settings.yaml", "notify_email: me@example.com\nsmtp_connection: mailer\n")
    scheduler = _scheduled(env, monkeypatch, "every-minute.yaml", """
        name: every-minute
        notify: {when: [skipped, failure]}
        triggers:
          - schedule: {cron: "* * * * *", misfire: skip}
        blocks:
          fine:
            type: logic.python
            config: {inputs: [], code: "output = 1"}
    """)
    t0 = dt.datetime(2026, 1, 5, 6, 30)
    scheduler.tick(t0)
    scheduler.tick(t0 + dt.timedelta(minutes=5))  # 06:31 … 06:34 were missed, 06:35 is on time
    scheduler.wait_for_runs()

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] Missed runs skipped"
    assert "4 run(s)" in text_of(msg) and "every-minute" in text_of(msg)


# --- the editor ----------------------------------------------------------------------------------

def test_notifications_set_in_the_settings_window_are_what_unattended_runs_email(env, smtp, win):
    from PySide6.QtWidgets import QDialogButtonBox

    from taskloom.editor.dialogs import SettingsDialog

    add_connection(env, "appserver", kind="ssh", host="server01.example.com")
    dialog = SettingsDialog(win.home, win)
    assert dialog.smtp.findText("mailer") >= 0 and dialog.smtp.findText("appserver") < 0  # SMTP connections only
    dialog.notify_email.setText("me@example.com; boss@example.com")
    dialog.smtp.setCurrentText("mailer")
    dialog.events["failure"].setChecked(False)
    dialog.events["success"].setChecked(True)
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save).click()

    assert run(env, FAILING_FLOW, "--notify", name="failing.yaml").returncode == 1
    assert run(env, OK_FLOW, "--notify", name="ok.yaml").returncode == 0

    [msg] = smtp.messages
    assert msg["Subject"] == "[Taskloom] weekly succeeded" and msg["To"] == "me@example.com, boss@example.com"


def test_a_send_email_block_set_up_in_its_properties_emails_everyone_named(env, smtp, win, qtbot, monkeypatch):
    from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel

    from taskloom.editor.dialogs import RunDialog
    from test_editor import wait_done

    add_connection(env, "appserver", kind="ssh", host="server01.example.com")
    flow = env.write("weekly.yaml", f"""
    {DAY}blocks:
      manual:
        type: trigger.manual
{DATA}      table:
        type: reports.html_template
      mail:
        type: email.send
    edges:
      - manual.start -> data.after
      - data.table -> table.data
      - table.html -> mail.body
    """)
    monkeypatch.setattr(RunDialog, "exec", lambda dialog: True)  # the parameters as they are
    tab = win.open_flow(flow)
    tab.scene.nodes["mail"].setSelected(True)

    def field(label):
        """The properties panel's field with this label."""
        for form in win.properties.findChildren(QFormLayout):
            for i in range(form.rowCount()):
                name = form.itemAt(i, QFormLayout.LabelRole)
                if name is not None and isinstance(name.widget(), QLabel) and name.widget().text() == label:
                    return form.itemAt(i, QFormLayout.FieldRole).widget()

    combo = field("Connection").findChild(QComboBox)
    assert [combo.itemData(i) for i in range(combo.count())] == ["", "mailer"]  # SMTP connections only
    combo.setCurrentIndex(combo.findData("mailer"))
    combo.activated.emit(combo.currentIndex())
    for label, text in (("To", "ana@example.com, bob@example.com"), ("Cc", "carol@example.com"),
                        ("Subject", "Weekly {day:%Y-%m-%d}"), ("Message", "Hello team,")):
        edit = field(label)
        edit.setText(text)
        edit.editingFinished.emit()
    assert win.save(tab)
    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert tab.last["status"] == "success"
    [msg] = smtp.messages
    assert (msg["To"], msg["Cc"], msg["Subject"]) == ("ana@example.com, bob@example.com", "carol@example.com",
                                                      "Weekly 2026-10-02")
    html = html_of(msg)
    assert html.index("Hello team,") < html.index(">APAC<")


def test_run_now_of_the_report_example_emails_the_report(env, smtp, win, qtbot, monkeypatch):
    from taskloom.editor.dialogs import RunDialog
    from test_editor import wait_done

    shutil.copytree(REPO / "examples" / "data", env.root / "data")
    flow = env.root / "sales-report-email.yaml"
    shutil.copy(REPO / "examples" / "sales-report-email.yaml", flow)

    def for_one_day(dialog):
        dialog.edits["day"].setText("2026-10-02")
        return True

    monkeypatch.setattr(RunDialog, "exec", for_one_day)
    tab = win.open_flow(flow)
    win.run_flow(tab)
    wait_done(qtbot, tab)

    assert tab.last["status"] == "success"
    [msg] = smtp.messages
    assert msg["Subject"] == "Daily sales 2026-10-02" and msg["To"] == "you@example.com"
    html = html_of(msg)
    assert "Sales for Friday 02 October 2026" in html and ">APAC<" in html and len(inline_images(msg)) == 1
    [attachment] = list(msg.iter_attachments())
    assert attachment.get_filename() == "sales_2026-10-02.xlsx"
