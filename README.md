# Taskloom

Build automations by connecting blocks instead of writing a script each time: run and
save queries, schedule them, retry with backoff, move files to servers, watch and
restart remote apps, build reports and email them.

Runs on Windows and Linux without admin/root rights.

**Status:** milestones 1–5 — the engine, the visual editor, scheduling, server blocks,
reports and email, and packaged builds. See [docs/DESIGN.md](docs/DESIGN.md).

## Install

**Windows (no Python needed):** download `taskloom-windows-amd64.zip` from the
[releases](https://github.com/lben/taskloom/releases) (or the latest build's artifacts),
unzip it anywhere you can write (no admin needed) and run `taskloomw.exe` for the
editor. `taskloom.exe` is the command line (`taskloom.exe run flow.yaml`).

**Linux server (no root, no system Python needed):** the runner is
`taskloom-runner-linux-x86_64.tar.gz`. It runs on Linux with glibc 2.28 or newer
(for example AlmaLinux or Rocky 8+, Ubuntu 20.04+, Debian 10+). Install it with the `examples/deploy-runner.yaml` flow from your PC,
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
  Hover over a block to see what it does.
- Two kinds of connectors: **circles carry data** (an output into an input), **diamonds set
  the order**. Every block has a **done** diamond output (it fires when the block succeeded)
  and a **run after** diamond input: connect `done` → `run after` to run blocks one after
  another, even when no data passes between them. A block with data inputs also waits for
  its data. You can use both lines between two blocks; each has its own purpose.
- **Start blocks** say how a flow starts. Connect their `start` output to the **run
  after** diamond of the first block:
  - **Manual Start**: the flow runs when you press **Run now** (F5).
  - **Schedule**: the flow runs by itself at the times you set (e.g. `0 7 * * MON-FRI`)
    while the scheduler is on. Saving a flow with a Schedule block schedules it.
  A flow can have both. Without start blocks, Run now runs every block.
- Select a block to edit its properties, retry and timeout; the **Parameters** tab
  previews parameter values on any day; **Flow** sets the name and calendar.
- **Run now** saves, checks and runs the flow in a separate process: blocks show their
  status live, the **Log** shows the selected block's messages, **Data preview** shows
  everything the selected block produced (tables as a grid, other values as text), and
  **Run history** reopens any past run. **Stop** cancels.
- **After a run, hover** over any input, output or connection line to see the value that
  went through it. Every run's values are kept in **Run history** (double-click a run, then
  hover) for `history_days` (365 by default). Hover over the message under a block to read
  all of it. The canvas shows each new run of the flow, including scheduled ones, unless you
  opened an older run from Run history; a flow you open starts clean, unless it ran since
  you last saw it (e.g. on schedule). The Log keeps the messages of the earlier runs above
  it, each run starting with a bold line with its number, date and time.
- **Text on the canvas:** right-click an empty spot → **Add text here** (or Edit → Add
  text) for titles and remarks. Select it to write it in Properties, with a style (title,
  heading, normal, small), bold, italic, underline and any font installed on your computer.
- **Retry one block after a fix:** right-click it → **Run this block again with the same
  inputs**. Only that block runs, with what reached it in the run on the canvas; the other
  blocks keep their results. (`taskloom run FLOW --only BLOCK --inputs-from RUN`.) Inputs
  that held a password are never saved, so such a block needs a full run. After fixing a
  block and retrying it, retry the blocks after it the same way: they get its new output,
  by the same rules as a full run (Taskloom says which block to retry first if needed).
- **Test a block with values you choose:** right-click it → **Run this block with inputs I
  choose…**. Each input is filled in with what reached it in the run on the canvas (empty if
  it did not run); change any of them and press Run. Write values as Python: `42`, `'text'`,
  `[1, 2]`, `{'a': 1}`, `None`; tables as `table('data.csv')`, `table('data.parquet')` or
  `pl.DataFrame({'a': [1, 2]})`. An empty field means no value. Next time the window shows
  what you typed and works it out again, so after editing `data.csv` just press Run.
  (`--input NAME=VALUE` with `--only` from a terminal.)
- **When a block fails**, the Log says which block, what came into each input (from which
  block and with what value) and, for Python Code, the code around the failing line and
  the values of your variables.
- To check data mid-flow, put an **Inspect** block (Debug) between two blocks: it passes
  the data on and shows it in the Log and Data preview. Its **message** is written like a
  Python f-string, e.g. `Healthy: {value['healthy']}`. Example:
  `examples/check-app-health.yaml` (Health Check's `result` output says healthy or not).
- **One error handler for many blocks:** connect the red `error` output of every block you
  want covered to one input of one block (e.g. a Python Code block); it runs once and gets
  the errors, each naming its `block`. Example: `examples/shared-error-handler.yaml`.
- **Python packages:** Tools → Python environment creates your own Python environment and
  installs packages with pip. Tick **Run in my Python environment** on a Python Code
  block to use them (`taskloom env create`, `taskloom env pip install NAME` from a terminal).
- **`print()` in a Python Code block** goes to that block's Log (and run history), like
  `log("...")`.
  The screen shows the environment in use; **Use an existing environment…** picks one you
  already have (the `python_env` setting).
- **Your work is safe:** unsaved changes are copied to a recovery folder as you work, and
  the flows you had open come back with them after a crash or a restart. If the editor
  ever closes unexpectedly, the details are in `crash.log` in your Taskloom folder. The
  list of open flows is kept in `editor.ini`, so a new version opens where you left off.
- Connections: use **New…** or **Edit…** next to a block's connection field, or **Tools →
  Connections**. Type the password in the Password field (it is stored in the system's
  secure store, never in a file) and click **Save and test connection**.

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

Add a **Schedule** block to the flow and save it: saved flows with a Schedule block are
listed in `scheduled.yaml` (removing the block takes the flow off). Then start the scheduler with the **Scheduler** button at the top
right of the editor (it also shows whether it is on), or `taskloom scheduler`. It shows
a tray icon and notifies you about failed or skipped runs. On Windows, **Tools →
Settings → Start the scheduler when I log in** (or `taskloom scheduler --at-login on`)
adds it to your Startup folder; no admin needed.

One scheduler runs every scheduled flow. It rereads changed flow files every 15 seconds,
so a new cron line applies shortly after you save, without restarting it; a changed
schedule counts from that moment (nothing before it counts as missed).

Runs missed while the computer was off follow `misfire`: `skip`, `run_once` (the
latest missed one) or `catch_up` (each of them), with parameters computed for the
time each run was due.

## Servers

Add an SSH connection (Tools → Connections) and use the Servers and Files blocks.
File locations on a server are written `connection:path`, e.g. `appserver:~/app/`.

An app watchdog is a flow: Health Check (URL and/or PID file) → `unhealthy` →
Start Remote Process → Health Check, scheduled every few minutes.

**Start Remote Process** runs your command as you would type it (`cd app && ./run.sh >>
app.log 2>&1` is fine; no need for `nohup` or a final `&`), keeps track of it, and checks
it is still running a few seconds later. For a command that does its job and finishes
(e.g. a script that starts a service), untick `must_keep_running`. **Stop Remote Process**
stops the app and everything it started, forcing whatever has not stopped after `grace`.

On a server without root or crontab, run `taskloom scheduler --headless` there and
list the server in the `keeper` setting (Tools → Settings) on your PC: your
scheduler checks it every 5 minutes and starts it again after a server reboot.

## Checking flows after an update

Each version can check your flows for what it can fix and what needs you: **Tools → Check
flows…** (the open and scheduled flows; it also runs by itself the first time a new
version starts), or `taskloom check [FLOW…]`. It fixes an older flow format, a block's
settings saved by an older version of that block, settings a block no longer has,
connections to blocks, inputs or outputs that no longer exist (e.g. after renaming an
output) and connections listed twice. **Back up and fix** (or `--fix`) first copies each
flow into `backups/` in your Taskloom folder. Anything else it finds is listed for you to
fix.

## Your files

Everything per-user lives in `~/.taskloom` (or `$TASKLOOM_HOME`):

| File | Purpose |
|---|---|
| `connections.yaml` | Database connections, e.g. a JDBC driver, URL, jar and properties |
| `settings.yaml` | `calendar`, `keep_runs`, `history_days`, `java_home`, `max_concurrent_runs`, `keeper`, notifications |
| `calendars/<name>.yaml` | Holidays: `base: US`, `add: [...]`, `remove: [...]`, `weekend: [sat, sun]` |
| `blocks/*.py` | Your own blocks; same `type_id` as a built-in replaces it |
| `history.db` | Every run, block result and log line |
| `editor.ini` | Editor preferences and the flows you had open |
| `scheduled.yaml` | Flows the scheduler runs |
| `known_hosts` | SSH server keys trusted on first connection |
| `python-env/` | Your Python environment for Python Code blocks (unless `python_env` points elsewhere) |
| `recovery/` | Autosaved copies of unsaved flows |
| `backups/` | Flows as they were before Check flows fixed them |
| `crash.log` | What happened if the editor ever stopped unexpectedly |

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
