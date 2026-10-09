# Taskloom

Build automations by connecting blocks instead of writing a script each time: run and
save queries, schedule them, retry with backoff, move files to servers, watch and
restart remote apps, build reports and email them.

Runs on Windows and Linux without admin/root rights.

**Status:** milestones 1–5 — the engine, the visual editor, scheduling, server blocks,
reports and email, and packaged builds. See [docs/DESIGN.md](docs/DESIGN.md).

## Install

**Windows (no Python needed):** download `taskloom-<version>-windows-*.zip` from the
[releases](https://github.com/lben/taskloom/releases) (or the latest build's artifacts),
unzip it anywhere you can write (no admin needed) and run `taskloomw.exe` for the
editor. `taskloom.exe` is the command line (`taskloom.exe run flow.yaml`).

**Linux server (no root, no system Python needed):** the runner is
`taskloom-runner-<version>-linux-x86_64.tar.gz`, built on Enterprise Linux 8 so it runs
on RHEL 8 and newer. Install it with the `examples/deploy-runner.yaml` flow from your PC,
or by hand: copy it to the server, `tar -xzf` it in your home folder and run
`~/taskloom/taskloom scheduler --headless`.

Teammates who only need to run a flow get the same zip plus the flow file.

**From source** (Python 3.11+):

```
pip install .
taskloom validate examples/daily-sales-report.yaml
taskloom run examples/daily-sales-report.yaml --param day=2026-10-02
```

**Build the packages yourself** (e.g. through your company's package mirror):

```
pip install . pyinstaller
python packaging/build.py             # editor + runner, for this OS
python packaging/build.py --headless  # runner only, for servers
python packaging/smoke.py dist/taskloom
```

Packaged builds include Python and all libraries; a Python Code block can import any of
them (polars, duckdb, pyarrow, matplotlib, …) but not packages you install separately.

The example reads `examples/data/sales.csv`, totals the previous business day with
DuckDB SQL and writes `examples/out/sales_<day>.csv`.

## The editor

```
taskloom editor examples/daily-sales-report.yaml
```

- Drag blocks from the tree on the left onto the canvas (or double-click them); drag
  from an output port (right side of a block) to an input port (left side) to connect.
- Select a block to edit its properties, retry and timeout; the **Parameters** tab
  previews parameter values on any day; **Flow** sets the name and calendar.
- **Run** (F5) saves, checks and runs the flow in a separate process: blocks show their
  status live, the **Log** shows the selected block's output, **Data preview** shows
  the tables it produced, and **Run history** reopens any past run. **Stop** cancels.
- **Tools → Connections** and **Tools → Settings** edit your connections (passwords go
  to the secret store) and settings such as the Java folder.

## Flows

A flow is a YAML file of blocks and the edges between them:

```yaml
params:
  day: {type: date, expr: "today() - bdays(1)"}       # previous business day
  day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
blocks:
  q:
    type: db.run_query
    config: {connection: warehouse, sql: "select * from sales where day_key = :day_key"}
    retry: {policy: exponential, initial: 30s, factor: 2, max_delay: 10m, max_attempts: 5}
  save:
    type: data.write_file
    config: {path: "out/sales_{day:%Y-%m-%d}.csv"}
edges:
  - q.result -> save.table
```

- `:name` in SQL binds a parameter with its type; `{name:format}` fills text.
- Every block has an `error` output; connect it to handle failures.
- Results stream to Parquet on disk, so millions of rows never sit in memory.

Built-in blocks: `logic.python`, `logic.if`, `logic.wait`, `logic.ask_user`,
`logic.for_each`, `data.duckdb_sql`, `data.polars`, `data.read_file`,
`data.write_file`, `db.run_query`, `files.copy`, `files.move`, `files.delete`,
`servers.ssh_command`, `servers.start_process`, `servers.stop_process`,
`servers.health_check`, `servers.remote_extract`, `reports.html_table`,
`reports.html_template`, `reports.chart`, `reports.excel`, `email.send`.

## Reports and email

`examples/sales-report-email.yaml` builds a chart, an Excel file with a native Excel
chart and an HTML table, and emails them. Emails are built for desktop Outlook:
table layout, CSS moved into `style=""` attributes, charts as inline images with
explicit sizes (never `data:` URIs or SVG), plus a plain-text version.

- **HTML Template** (Jinja2): `{{ table(totals) }}`, `{{ image(chart) }}`, `{{ params.day }}`.
- **Send Email** takes any number of body parts and attachments; `save_copy` also
  writes the email as an `.eml` file you can open in Outlook.

Notifications: set `notify_email`, `smtp_connection` and `notify_on` in Tools →
Settings. Scheduled runs then email you on failure (or success / skipped runs). A flow
can override this:

```yaml
notify: {when: [failure, success], to: [team@example.com]}
```

## Schedules

```yaml
triggers:
  - schedule: {cron: "0 7 * * MON-FRI", misfire: run_once}   # 07:00 on weekdays
  - schedule: {at: "2026-10-08 15:30"}                       # once
overlap: skip        # if the previous run is still going: skip | queue | allow
```

Turn on "Run on schedule" in the editor's Flow tab (or list the flow in
`scheduled.yaml`), then start the scheduler with the **Scheduler** button at the top
right of the editor (it also shows whether it is on), or `taskloom scheduler`. It shows
a tray icon and notifies you about failed or skipped runs. On Windows, **Tools →
Settings → Start the scheduler when I log in** (or `taskloom scheduler --at-login on`)
adds it to your Startup folder; no admin needed.

Runs missed while the computer was off follow `misfire`: `skip`, `run_once` (the
latest missed one) or `catch_up` (each of them), with parameters computed for the
time each run was due.

## Servers

Add an SSH connection (Tools → Connections) and use the Servers and Files blocks.
File locations on a server are written `connection:path`, e.g. `appserver:~/app/`.

An app watchdog is a flow: Health Check (URL and/or PID file) → `unhealthy` →
Start Remote Process → Health Check, scheduled every few minutes.

On a server without root or crontab, run `taskloom scheduler --headless` there and
list the server in the `keeper` setting (Tools → Settings) on your PC: your
scheduler checks it every 5 minutes and starts it again after a server reboot.

## Your files

Everything per-user lives in `~/.taskloom` (or `$TASKLOOM_HOME`):

| File | Purpose |
|---|---|
| `connections.yaml` | Database connections, e.g. a JDBC driver, URL, jar and properties |
| `settings.yaml` | `calendar`, `keep_runs`, `java_home`, `max_concurrent_runs`, `keeper` |
| `calendars/<name>.yaml` | Holidays: `base: US`, `add: [...]`, `remove: [...]`, `weekend: [sat, sun]` |
| `blocks/*.py` | Your own blocks; same `type_id` as a built-in replaces it |
| `history.db` | Every run, block result and log line |
| `editor.ini` | Editor preferences, e.g. remembered choices for overlapping runs |
| `scheduled.yaml` | Flows the scheduler runs |
| `known_hosts` | SSH server keys trusted on first connection |

```yaml
# connections.yaml
warehouse:
  kind: jdbc
  driver: com.example.jdbc.Driver
  url: "jdbc:example://db.example.com/default"
  jars: [C:/drivers/example-jdbc.jar]
  properties: {user: me, password: "secret:warehouse_password", queryTimeout: 600}
```

Store the password with `taskloom secret set warehouse_password` (Windows Credential
Manager / macOS Keychain, or a private `secrets.yaml` on headless Linux). JDBC needs
Java; set `java_home` in `settings.yaml` (or `JAVA_HOME`) if it is not found.

## Writing a block

```python
# ~/.taskloom/blocks/greet.py
from taskloom import Block, fields, ports

class Greet(Block):
    type_id = "my.greet"
    title = "Greet"
    description = "Says hello to whoever you name."  # shown when hovering over the block
    inputs = {"name": ports.Any()}
    outputs = {"text": ports.Any()}
    config = {"greeting": fields.Text(default="Hello")}

    def run(self, ctx, name):
        ctx.log(f"greeting {name}")
        return {"text": f"{self.config.greeting}, {name}!"}
```

## Development

```
uv sync
uv run pytest
```

The JDBC tests start an H2 database server and need Java; they download the H2 jar once.
GitHub Actions (`.github/workflows/build.yml`) runs the tests on Linux and Windows, builds
the Windows zip and the Linux runner, and smoke-tests both; tagging `v*` publishes them.

## License

MIT
