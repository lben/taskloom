# Taskloom

Taskloom lets you build automations for your daily work by connecting blocks on a canvas, instead of writing a new
script each time. Run a query, check what came back, turn it into a chart, an Excel file and an email, and have it
all happen at 07:00 every weekday. Afterwards, every run is there to look at: what each block got, what it made and
why it failed.

It runs on Windows and Linux without admin or root rights.

## Who it is for

- People who do the same work by hand every day or week: pull a query, check that a file came, send a report,
  restart an app that stopped.
- People with a folder of scripts and no good place to run them: no crontab, no admin rights, no Python on the server.
- You do not need to be a developer. Most blocks are forms to fill in; SQL and Python are there when you want them.

## What you can build

- **A morning report:** at 07:00 on weekdays, query the database for the previous business day, check that the table
  is not empty, draw a chart, write an Excel file and email both in a body that looks right in Outlook.
- **An app watchdog:** every 5 minutes, check that an app answers; if it does not, start it again on its server.
- **A file pickup:** check that today's file is in a folder or on a server and is not empty, load it, then move it
  to an archive folder.
- **An API pull:** paste a curl command from an API's documentation and Taskloom fills in the request; the answer
  can come back as a table.
- **An early warning:** Detect Anomalies tells you when today's row count is unusual for a Monday, and why.

A flow is a short YAML file, so it can be reviewed, compared and kept in Git. This one saves the previous business
day's orders every weekday at 07:00, and tries the query again if the database is busy:

```yaml
taskloom: 1
name: daily-orders
params:
  day: {type: date, expr: "today() - bdays(1)"}   # the previous business day
blocks:
  weekdays:
    type: trigger.schedule
    config: {cron: "0 7 * * MON-FRI"}             # 07:00, Monday to Friday
  orders:
    type: db.run_query
    config: {connection: warehouse, sql: "select * from orders where day = :day"}
    retry: {policy: exponential, initial: 30s, factor: 2, max_delay: 10m, max_attempts: 5}
  save:
    type: data.write_file
    config: {path: "out/orders_{{day:%Y-%m-%d}}.csv"}
edges:
  - weekdays.start -> orders.after
  - orders.result -> save.table
```

`{{name}}` in a setting puts in the flow parameter, or the value arriving at the block, of that name;
`{{day:%Y-%m-%d}}` also formats it. In SQL, `:name` passes a parameter to the database with its type.

## What it can do

**Blocks**, by their group in the editor's Blocks list:

| Group | Blocks |
|---|---|
| Start | Manual Start, Schedule |
| Database | Run Query, on any database with a JDBC driver |
| Data | Read File and Write File (CSV, Excel, Parquet), DuckDB SQL, Polars Transform, Statistics, Detect Anomalies |
| Checks | Check File (there, not empty, recent, size), Check Table (row count, missing values, types, ranges, ...) |
| Logic | If, Python Code, Wait, Ask User, Loop (blocks once per item), Splitter and Merger (lines side by side) |
| Files | Copy, Move and Delete Files, on this computer, on network folders or on servers over SSH |
| Servers | SSH Command, Start and Stop Remote Process, Health Check, Remote Extract, API Request, Session (cookies) |
| Reports | Chart, HTML Template, To Excel (with a native Excel chart) |
| Email | Send Email (made for desktop Outlook, with a plain-text version and attachments) |
| Debug | Inspect: shows the data passing through, also as Markdown, pictures and Mermaid diagrams |

Need something else? A block of your own is one Python file, or right-click a Python Code block → **Create Reusable
block…**. Python Code can use packages you install with pip into your own environment (Tools → Python environment).

**The editor**

- Drag blocks onto the canvas, connect an output to an input, fill in Properties. Rest the mouse on any setting for
  what it is for, with examples.
- **Run now** (F5) and watch each block run. Hover over any connection to see the value that went through it. Data
  preview shows whole tables; the Log shows each block's messages, and Ctrl+F searches both.
- **Fix and carry on:** right-click a block to run only it, or it and what follows, with the inputs of the last run.
  A 20-minute query does not run again because a later block had a typo. Or run a block with inputs you type.
- **Validate** (F7) finds mistakes before a run: a setting left empty, an input not connected, a `{{name}}` that
  names nothing.
- Unsaved work comes back after a crash. Light, dark or your system's theme. After an update, Tools → **Check
  flows…** brings older flows up to date, after a backup.

**Errors and retries.** Every block has an `error` output to handle its failure, a retry policy (fixed waits or
growing ones) and a timeout. One handler can cover many blocks. A failure says which block failed and what came into
each of its inputs.

**Schedules.** A Schedule block runs the flow on the clock (every weekday at 07:00, or a cron line), every few
minutes or hours, or once. Business days follow your holiday calendar. The scheduler service runs in the background,
also with the editor closed, and can start when you log in to Windows, without admin rights. On a Linux server,
`taskloom scheduler --headless` needs no root or crontab, and Taskloom on your PC can start it again after a reboot
or if it hangs. You choose what happens to missed runs and to runs that overlap, and a failed run can email you.

**Run history.** Every run is kept, five years by default or as each start block says (the last 100, failed runs
always, ...): its log, each block's result and every value that went along a connection. Reopen any past run on the
canvas. Copy the exact SQL a run sent, with its values written in, to run it in a SQL tool.

**Versions in Git.** File → **Save & Commit** keeps each version of a flow in Git; no Git program is needed.
**Commit History** searches the messages, code and outputs of every version, shows a version with the results it
gave, and restores it.

**Secrets and big data.** Passwords go to the system's secure store (Windows Credential Manager, macOS Keychain, or a
private file on a Linux server), never into a flow file or a log. Query results stream to Parquet files on disk, so
tables of millions of rows never have to fit in memory.

**A proof with every release.** Each feature and fix is shown working in the real app, step by step with
screenshots and the checks behind it, in a PDF attached to each release. See
[how a proof is written](tests/proofs/README.md).

## Get it and start it

**Windows (no Python needed).** Download `taskloom-windows-amd64.zip` from the
[releases](https://github.com/lben/taskloom/releases), unzip it in any folder you can write to and run
`taskloomw.exe` for the editor. `taskloom.exe` is the command line, e.g. `taskloom.exe run flow.yaml`. Someone who
only runs your flow needs the same zip and the flow file.

**Linux server (no root, no system Python).** Download `taskloom-runner-linux-x86_64.tar.gz`; it needs glibc 2.28 or
newer (for example Rocky or AlmaLinux 8+, Ubuntu 20.04+, Debian 10+). Install it from your PC with the example flow
[deploy-runner.yaml](examples/deploy-runner.yaml), or by hand: unpack it with `tar -xzf` in your home folder and
start the scheduler with `~/taskloom/taskloom scheduler --headless`.

**From source** (Python 3.11 or newer):

```
pip install .
taskloom editor examples/daily-sales-report.yaml
taskloom run examples/daily-sales-report.yaml --param day=2026-10-02
```

The example reads `examples/data/sales.csv`, totals one day's sales with DuckDB SQL and writes
`examples/out/sales_<day>.csv`.

## Documentation

- **In the app:** Help → Documentation (F1) has the guide and a reference of every block and setting.
- **On the web:** the same guide and reference at <https://lben.github.io/taskloom/>.
- **Examples:** flows ready to run in [examples/](examples/).
- **The whole guide as one page:** [docs/guide.md](docs/guide.md).
- **How Taskloom is built:** [docs/DESIGN.md](docs/DESIGN.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

MIT: see [LICENSE](LICENSE). The packaged app includes other open-source components; their licences are in its
`licenses` folder.
