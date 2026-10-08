# Taskloom — Design

Status: **draft for sign-off**

Taskloom is a desktop tool for building automations by dragging blocks onto a
canvas and connecting them, instead of writing a script each time. Flows run
from the editor, from a headless runner, or on a schedule — on a Windows
workstation or on a Linux server, without admin/root rights.

---

## 1. Goals and non-goals

**Goals**

- Visual flow editor (drag, drop, connect, configure) in the spirit of CloverETL / Godot.
- Common work automations out of the box: run and save queries, schedule them,
  retry with fixed/exponential backoff, move files to servers, watch and restart
  remote apps, build reports (tables + charts) and email them.
- Extensible: a new block is one Python file; blocks can be added or replaced
  without rebuilding the app.
- Large data safe: query results of millions of rows never have to fit in memory.
- Runs without admin (Windows) or root (Linux), and comes back on its own after reboots.
- Shippable to non-developers as a zip or exe: full editor, or runner + one flow.

**Non-goals (for now)**

- Multi-user server / web UI, distributed execution, real-time streaming.
- Record-by-record dataflow (CloverETL-style). Blocks pass whole values/tables.

---

## 2. Core concepts

| Concept | Meaning |
|---|---|
| **Flow** | A graph of blocks saved as a YAML file. |
| **Block** | A unit of work (Run Query, Send Email, …) with typed inputs, outputs and config. |
| **Port** | A named, typed input or output on a block. |
| **Edge** | Connects an output port to an input port; data travels along it. |
| **Trigger** | How a flow starts: manual or schedule. Part of the flow file. |
| **Connection** | A named, reusable endpoint (JDBC database, SMTP, SSH host). Flows refer to it by name only. |
| **Secret** | Password or token, stored outside flows and connections. |
| **Run** | One execution of a flow: status, per-block logs, outputs, timings. |
| **Table** | Handle to tabular data stored as Parquet on disk (see §4). |

---

## 3. Execution model

- A flow is a directed acyclic graph. The engine runs blocks in topological order
  (sequentially in v1; parallel branches can be added later without format changes).
- **Many flows run at the same time.** Each run is its own process, so the editor,
  the scheduler and the CLI can run any number of flows in parallel. An optional
  setting caps the total concurrent runs (**unlimited by default**).
- **Overlap** — a flow started while a previous run of itself is still going:
  - **manual run from the editor**: a dialog says a previous run is still going and
    offers **Skip**, **Queue** or **Run alongside**, with "remember for this flow";
  - **scheduled or headless run** (nobody to click): uses the flow's
    **overlap policy** `skip | queue | allow` (default `skip`) and logs it; with
    `skip`, a notification tells you a run was skipped.
- A block runs when all of its **connected** inputs have values.
- Every block has an implicit **`error`** output. On failure:
  - if `error` is connected, the error info (message, traceback, block id) flows
    down that edge and the run continues;
  - otherwise the run is marked failed, downstream blocks are skipped.
- Blocks downstream of a branch that was not taken (If block, unused `error` port)
  are **skipped**, not failed.
- **Retry policy** is a setting on every block:
  `none | fixed(delay) | exponential(initial, factor, max_delay, jitter)` plus
  `max_attempts` and optional per-attempt `timeout`. Retries are logged per attempt.
  A timeout is cooperative: blocks that wait or loop check `ctx.cancelled`; a block
  that does not is abandoned after a short grace period and the attempt fails.
- **Cancellation**: a run can be stopped from the editor or CLI; blocks receive a
  cancel signal through their context and long waits (retry sleeps) are interruptible.
- **Isolation**: the editor never runs a flow in its own process. It launches the
  runner as a subprocess and streams events back, so a crash or out-of-memory in a
  flow cannot take down the editor.
- **Loops**: `For Each` runs a referenced sub-flow once per item (e.g. per server),
  keeping every graph acyclic.

### Run context

Each block receives a `ctx` with: logger (per block), run workspace directory,
connection/secret lookup, flow parameters, cancel token, and `ctx.ask(...)` for
interactive prompts (§9).

---

## 4. Data handling (large-data safe)

- Small values (numbers, text, lists, dicts, file paths) pass in memory.
- Tabular data passes as a **`Table`**: Parquet file(s) in the run workspace plus
  schema and row count.
- **Query results stream to Parquet** in chunks (`fetchmany`), so row count is
  limited by disk, not RAM.
- Transform blocks read Tables **lazily** with **Polars** (`scan_parquet`) or
  **DuckDB** (`read_parquet`) — both work on larger-than-memory data.
- **pandas** is used only where a library requires it (some charts), via an
  explicit `table.to_pandas(limit=...)` after the data has been reduced.
- The editor's data preview reads only the first N rows.
- Run workspaces are cleaned up by a retention setting (e.g. keep last N runs / X days).

---

## 5. Block plugin API

```python
from taskloom import Block, ports, fields

class RunQuery(Block):
    type_id = "db.run_query"          # stable id stored in flow files
    version = 1
    title = "Run Query"
    category = "Database"

    inputs = {"params": ports.Any(required=False)}
    outputs = {"result": ports.Table()}
    config = {
        "connection": fields.Connection(kind="jdbc"),
        "sql": fields.Code(language="sql"),
        "chunk_size": fields.Int(default=50_000),
    }

    def run(self, ctx, params=None):
        ...
        return {"result": table}
```

- The **properties panel is generated from `config`** — no UI code per block.
- Field types: Text, Int, Float, Bool, Choice, Code(language), Path, Connection(kind),
  Secret, List, KeyValue, Cron.
- **Discovery** (all loaded into the palette at startup):
  1. built-in blocks in `taskloom.blocks`;
  2. installed packages exposing the `taskloom.blocks` entry point;
  3. `.py` files in the user blocks folder (works inside the packaged exe).
- A user block with the same `type_id` as a built-in **overrides** it — this is how
  you replace a built-in privately (e.g. your own email sender).
- **Versioning**: when a block's config changes shape, bump `version` and provide
  `migrate(old_config, old_version)`; old flow files keep loading.
- **Python Code block**: inline editor; receives `inputs` and `ctx`, returns a dict of outputs.

---

## 6. Flow file format

```yaml
taskloom: 1
name: daily-sales-report
params:
  region: {type: text, value: EU}
  day: {type: date, expr: "today() - days(1)"}
  day_key: {type: int, expr: "int(format(day, '%Y%m%d'))"}
triggers:
  - schedule: {cron: "0 7 * * MON-FRI", misfire: run_once}
notify: {on: [failure]}
blocks:
  q:
    type: db.run_query
    config: {connection: warehouse, sql: "select ... where region = :region and day_key = :day_key"}
    retry: {policy: exponential, initial: 30s, factor: 2, max_delay: 10m, max_attempts: 5}
    ui: {x: 120, y: 80}
  xl:
    type: report.to_excel
    config: {path: "reports/sales_{day:%Y-%m-%d}.xlsx", chart: {kind: bar, x: day, y: total}}
  mail:
    type: email.send
    config: {connection: smtp, to: ["{settings.notify_email}"], subject: "Daily sales"}
edges:
  - q.result -> xl.table
  - xl.file -> mail.attachments
```

Plain text, diff-friendly, safe to commit and share (no secrets). Relative paths
in a flow are relative to the flow file's folder.

### Parameters

Parameters are defined once per flow (a **Parameters** tab in the editor, not
blocks on the canvas) and can be used by any block.

- **Types**: `text`, `int`, `float`, `bool`, `date`, `datetime`, `list`.
- **Value** is either a literal (`value:`) or an expression (`expr:`) computed at
  the start of every run. Expressions can use earlier parameters and a small, safe
  function set (no imports, no file or network access):
  - dates: `today()`, `now()`, `days(n)`, `weeks(n)`, `months(n)`,
    `start_of_month(d)`, `end_of_month(d)`, `start_of_week(d)`;
  - business days: `bdays(n)` (so `today() - bdays(1)` on a Monday is the previous
    Friday), `is_business_day(d)`, `next_business_day(d)`, `previous_business_day(d)`,
    `business_days_between(a, b)`;
  - conversion: `format(d, '%Y%m%d')`, `parse_date(s, fmt)`, `int()`, `float()`, `str()`.
- **Type and format are separate.** A parameter keeps its real type (`day` is a date);
  the format is chosen where it is used:
  - SQL **bind parameters** `:name` pass the typed value to the driver
    (`:day` as a date, `:day_key` as the integer `20260101`) — safe from injection;
  - **text templates** `{name}` / `{name:format}` in paths, subjects, commands, e.g.
    `{day:%Y-%m-%d}` → `2026-01-01`, `{day:%Y%m%d}` → `20260101`. Inside SQL these are
    raw text substitution, for things binds can't do (table or partition names).
- **Business-day calendars** decide which days count:
  - a calendar has weekend days (default Saturday and Sunday) and a holiday list;
  - holidays can come from the `holidays` package (a country or region, e.g. `US`,
    `US-NY`, or a market calendar), from your own file `calendars/<name>.yaml`
    (extra closures, or removing a public holiday you work), or both combined;
  - each flow picks a calendar (default from settings); `bdays(n, cal="name")` uses
    another one for a single expression;
  - the Parameters tab previews values "as of" any date, so you can check how
    Mondays and holidays resolve before the flow runs;
  - a calendar with no holiday data for the year in question is a warning in
    `taskloom validate`, not a silent assumption.
- Evaluated **once per run**, in the flow's time zone (default: the machine's), so
  every block and every retry sees the same values even if the run crosses midnight.
- **Overrides**: a manual run from the editor shows the computed values and lets you
  change them (e.g. re-run for a past date); the CLI accepts `--param day=2026-01-01`;
  scheduled runs use the computed values. The values used are saved with the run.
- **Values that come from data** (e.g. "latest loaded date" from a query) are not
  parameters: they flow through ports like any other data, e.g. into a query's
  `params` input. An **Expression** block computes new values from block outputs
  with the same function set.

---

## 7. Connections, secrets and settings

**Connections** (`connections.yaml`, per user, no secrets):

- `jdbc`: driver class, JDBC URL, jar path(s) and driver properties (key/value:
  user, password, timeouts, …); any value written `secret:NAME` is read from the
  secret store. Talks to the driver through JPype (the bridge `jaydebeapi` uses) so
  column types are read once per query; requires a Java runtime, found through
  the `java_home` setting or `JAVA_HOME`.
- `smtp`: host, port, TLS mode, optional login, default sender.
- `ssh`: host, port, username, password (secret reference) or key file. A server's
  host key is trusted the first time and saved in `known_hosts` in the Taskloom
  folder; a changed key is refused (like `ssh` with accept-new).
- later: `smb` (network shares from Linux, pure Python, no mount).

**Secrets**:

- Windows / macOS: OS credential store via `keyring` (Windows Credential Manager —
  per user, no admin).
- Linux headless: a per-user secrets file, `chmod 600`, optionally encrypted.
- Flows and connections only hold references like `secret:warehouse_password`.

**Settings** (`settings.yaml`, per user):

- notification email address(es) and the SMTP connection to use;
- default notification policy (failure / success / retries exhausted / app restarted),
  overridable per flow;
- desktop notifications on/off;
- paths: user blocks folder, run workspace, retention.

Deployed server runners carry a copy of the relevant settings so server-side
flows can email you directly.

---

## 8. Scheduling and keeping things alive

**Windows workstation**

- `taskloom scheduler` is a long-running process with a tray icon that runs all
  scheduled flows.
- Registered in the user's **Startup folder** (no admin) → it comes back on
  login after a reboot.
- Per-trigger **misfire policy** for runs missed while off: `skip | run_once | catch_up`.
  A run counts as missed when it starts more than 2 minutes late. Every scheduled
  run computes its parameters as of the time it was due (`taskloom run --as-of`), so
  a caught-up Monday 07:00 run reports on Monday even when it runs on Tuesday.
- Schedules live in the flow file (`triggers:`, edited in the editor's Flow tab) and
  use the machine's local time; `scheduled.yaml` in the Taskloom folder lists the
  flows the scheduler runs (the editor's "Run on schedule" switch).
- Single-instance lock so it never runs twice; `scheduler.heartbeat` shows it is alive.

**Linux server** (works even without root, crontab or systemd user lingering)

- A server-side `taskloom scheduler` started detached (`setsid nohup …`), writing a PID file.
- The Windows scheduler acts as its **keeper**: on startup and every 5 minutes it
  connects over SSH, checks the server scheduler (PID alive + heartbeat file fresh),
  restarts it if needed, and notifies you. Servers to keep are listed in the
  `keeper` setting: SSH connection, the Taskloom command on the server, its folder.
- Result: after a server reboot, server flows come back within minutes of your
  workstation being up.

**App watchdogs** (e.g. a web app started with `nohup`) are just flows:
Health Check (URL status + PID) → If dead → Start Remote Process → Health Check → Email.
They can run on the server scheduler itself, so they keep working while the
workstation is off (between the reboot and the next keeper check, they're down).

---

## 9. Interactive blocks

`Ask User` (text / password / yes-no / choice) only prompts in **manual runs from
the editor**. In scheduled or headless runs it uses a stored secret or a configured
default; if neither exists, it fails immediately instead of waiting forever.

---

## 10. Editor (desktop)

PySide6 (Qt) application:

- **Flows panel** (left, below the block palette; the divider between them can be
  dragged to resize both): every flow you have open or scheduled,
  each with its status:
  - spinner — running;
  - green dot — last run succeeded;
  - red ✕ — last run failed;
  - clock — scheduled to run again (tooltip shows the next run time), shown next to
    the last-run status.
  Right-click: run now, open, open last run, enable/disable schedule. Runs started
  by the scheduler appear here too (shared run history).
- **Tabs**: several flows open at once, each in its own tab with its own canvas,
  live status and logs; switching tabs never interrupts a run.
- **Left**: block palette as a collapsible tree (category folders → blocks, each with
  an icon), with a search box that filters it; drag a block onto the canvas.
  User blocks appear in their own folder.
- **Center**: canvas — drag, connect, pan/zoom, multi-select, copy/paste, undo/redo.
- **Right**: properties of the selected block (generated from its config).
- **Bottom**: run log; selecting a block filters to **that block's live log**,
  attempts, timings and errors.
- Live status on the canvas: idle / running / success / failed / skipped / retrying.
- Click an edge or output to **preview data** (schema, row count, first rows).
- **Run history** (SQLite): open any past run and inspect every block as it was.
- Connection manager and settings dialogs.
- Desktop notifications through the Qt tray icon (standard Windows notifications,
  no admin); clicking one opens that run.

---

## 11. Reports and email (Outlook-safe)

- HTML built from Jinja2 templates, then **CSS inlined** automatically.
- Table-based layout; no flexbox/grid, no external CSS, no SVG, no background images.
- Charts rendered with matplotlib to PNG and embedded as **inline CID attachments**
  (desktop Outlook blocks `data:` URIs).
- Excel attachments via `xlsxwriter`, with **native Excel charts**.
- Also: CSV / Parquet attachments; large attachments can be zipped.
- Email sending uses `smtplib` from the standard library.

---

## 12. Block catalog

| Category | Blocks | Milestone |
|---|---|---|
| Logic | Python Code, Expression, If, Wait, For Each (sub-flow), Ask User | M1 / M3 |
| Data | DuckDB SQL, Polars Transform, Read File, Write File (CSV/Excel/Parquet) | M1 |
| Database | Run Query (JDBC), Execute Statement (JDBC) | M1 |
| Reports | Table → HTML, HTML Template, Chart, To Excel | M4 |
| Email | Send Email | M4 |
| Servers | SSH Command, Remote Extract, Start Remote Process, Stop Remote Process, Health Check (URL/PID/port) | M3 |
| Files | Copy/Move/Delete Files between local folders, network shares and servers (SFTP); `server:path` locations make uploads and downloads plain copies | M3 |

Schedules are flow settings (Flow tab) rather than blocks on the canvas.

---

## 13. Repository layout

```
taskloom/
  core/        flow model, loader, engine, retry, table, registry, settings, secrets
  blocks/      built-in blocks
  cli.py       taskloom run | validate | scheduler | editor
  scheduler/   scheduler + keeper
  editor/      PySide6 application
examples/      example flows (incl. "deploy runner to server")
tests/
docs/
```

CLI:

```
taskloom run flow.yaml [--param key=value] [--json-events]
taskloom validate flow.yaml
taskloom scheduler [--flows DIR]
taskloom editor
```

---

## 14. Packaging and distribution

- Python 3.12, PyInstaller **one-folder** builds (fewer antivirus false positives
  than one-file), each also shipped zipped.
- Artifacts:
  - `taskloom` for Windows (editor + runner + scheduler);
  - `taskloom-runner` for Linux, built on an Enterprise Linux 8–compatible base (glibc 2.28),
    bundling its own Python — no system Python needed.
- Windows builds are produced on Windows (GitHub Actions `windows-latest`), Linux
  builds in an Alma/Rocky 8 container.
- Installing from a private package index is a user-level pip config; nothing
  index-specific lives in the repo.
- Sharing with teammates: either the full app, or the runner + a flow file
  (double-click or scheduled).
- **"Deploy runner to server"** ships as an example flow built with Taskloom's own
  blocks: ask password → check existing install/version → confirm overwrite →
  upload → extract → start scheduler → health check.

---

## 15. Milestones

Each milestone ends with tests passing and a demo on the real path.

**M1 — Engine and runner (headless)**
Flow model + YAML loader/validator, block registry and plugin discovery, engine
(topological run, error ports, skips, retry policies, timeouts, cancellation),
typed parameters with expressions and overrides,
Table on Parquet, per-block structured logs, run history in SQLite, CLI
`run`/`validate`. Blocks: Python Code, If, Wait, DuckDB SQL, Polars Transform,
Read/Write File, JDBC Run Query.
*Accept*: example flows run end to end; a date parameter computed as "yesterday" or
"previous business day" (weekends and calendar holidays skipped) is bound
to SQL as a date and as a `YYYYMMDD` integer, and can be overridden from the CLI; a flaky block succeeds after retries with
correct backoff timings; a multi-million-row result streams to Parquet without
loading in memory; a user block in the user folder is discovered and can override
a built-in.

**M2 — Editor**
Flows panel with status icons, tabs for several open flows, canvas, palette,
properties panel, Parameters tab, save/load, run in subprocess with live status
and per-block logs, data preview, run history browser, connections and settings
dialogs, Ask User.
*Accept*: build, save, reopen and run a flow entirely from the UI; kill a running
flow from the UI; editor survives a flow that crashes.

**M3 — Servers, files and scheduling**
SSH/SFTP blocks, Start Remote Process, Health Check, file blocks, For Each,
scheduler with misfire policies, Startup-folder registration, tray notifications,
server scheduler and keeper.
*Accept*: a watchdog flow detects a killed process and restarts it; the keeper
restarts a killed server scheduler; a missed schedule follows its misfire policy.

**M4 — Reports and email**
Table → HTML, templates, charts, Excel with native charts, Send Email, notification
policies.
*Accept*: a report email with an inline chart and an Excel attachment renders
correctly in desktop Outlook.

**M5 — Packaging**
PyInstaller builds (Windows, Enterprise Linux 8–compatible), zips, the deploy-to-server
example flow, user documentation.
*Accept*: fresh Windows machine (no Python) runs the editor; fresh Linux user
account runs the runner; deploy flow installs and re-run asks before overwriting.

---

## 16. Risks

| Risk | Mitigation |
|---|---|
| JPype compatibility with newer Python/Java | Tested in M1 against a real H2 database server over TCP. |
| JDBC fetch speed on very large results | Chunked streaming; a faster connection type can be added later without changing flows. |
| Antivirus / proxy flags the exe | One-folder build; fall back to zip with launcher scripts. |
| PyInstaller + JVM bridge packaging | Verify in M1 with a throwaway build before the editor exists. |
| Workstation off → server flows not restarted after reboot | Documented limitation; watchdogs on the server cover app restarts once the scheduler is up. |
