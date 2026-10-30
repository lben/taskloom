# Taskloom

Build automations by connecting blocks instead of writing a script each time: run and
save queries, schedule them, retry with backoff, move files to servers, watch and
restart remote apps, build reports and email them.

Runs on Windows and Linux without admin/root rights.

**Status:** milestones 1–5 — the engine, the visual editor, scheduling, server blocks,
reports and email, and packaged builds. See [docs/DESIGN.md](https://github.com/lben/taskloom/blob/main/docs/DESIGN.md).

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
- **Documentation in the editor:** rest the mouse on any row of the Properties tab for a card
  with what it is for, examples of what it takes, and **Learn more ›**, which opens that row's
  entry in **Help → Documentation** (F1): a column at the right of the window, next to the
  canvas and Properties, with this guide and a reference of every block and setting (your own
  blocks too), and Back, Forward and Contents. Drag its edge to resize it, or float it.
- **Zoom** with the mouse wheel or the control at the top right of the canvas: it shows the
  zoom, and **+** and **−** zoom in and out by the step between them (5% by default, change
  it with its arrows), from 20% to 300%. Or click the zoom, type one (`150` or `150%`) and
  press Enter.
- **Copy and paste** (Ctrl+C, Ctrl+V or the Edit menu) the selected blocks and texts, in the
  same flow or into another one: the copies get new names and are selected, to move them;
  Undo takes the whole paste back.
- Two kinds of connectors: **circles carry data** (an output into an input), **diamonds set
  the order**. Every block but a start block has a **run after** diamond input: connect any
  output of another block (or a start block's `start`) to it and the block waits for that
  output without using its value, to run blocks one after another even when no data passes
  between them. Only If, Python Code and reusable blocks also have a **done** diamond output,
  which fires when the block succeeded, even if it produced none of its other outputs. A
  block with data inputs also waits for its data. You can use both lines between two blocks;
  each has its own purpose.
- **Start blocks** say how a flow starts. Connect their `start` output to the **run
  after** diamond of the first block:
  - **Manual Start**: the flow runs when you press **Run now** (F5), or right-click the
    block → **Run the flow from here** (with several, only what follows that one runs).
    In a flow with several Manual Starts, Run now asks which one to start, or **All of
    them**, offering first the one you chose last time in that flow.
  - **Schedule**: the flow runs by itself at the times you set (the scheduler service
    runs it, see Schedules); under the block, a countdown to its next run. Set the times in Properties →
    **When**: repeating (every few minutes or hours from when the scheduler service picks it
    up, or on the clock: every hour, day, weekday, chosen days, month, or a cron line) or one
    time (a date and time), with the next runs shown. Right-click it →
    **Run the flow now, as if its time had come** to try it. Saving a flow
    with a Schedule block schedules it. The scheduler service runs the saved file: while a
    scheduled flow has unsaved changes, its tab and Schedule blocks say so.
  A flow starts from its start blocks: a new flow comes with a Manual Start, and Run now
  offers to add one to a flow without (connected to its first blocks). A flow can have
  both kinds. A run includes the blocks after the start block that started it, the blocks
  whose data those need, and the error handlers of all of them; **a block connected to no
  start block does not run**, which is how to switch one off: it says **off · not connected
  to a start block** under it. A skipped block says why.
- **Lines:** the blocks a start block's runs take part in are its line. A flow can have
  several lines, each from its own start block, and they run independently: a run of one
  line reports only its own blocks, and never clears, overwrites, skips or stops another
  line's blocks, whether it succeeds or fails. One start block can start several lines
  (connected to several first blocks): one run runs them all, and a failure in one does not
  stop the others. A block both lines connect into runs in either line's runs. On the
  canvas each block shows its result from the latest run it took part in, so a block shared
  by two lines shows the most recent one.
- Select a block to edit its properties, retry and timeout; the **Parameters** tab
  previews parameter values on any day; **Flow** sets the name (a new flow takes its file's
  name when you first save it) and calendar.
- **Python and SQL code** (Python Code, Polars Transform, DuckDB SQL, Run Query and blocks
  of your own) is written in a code box: keywords, strings, comments and numbers in their
  own colours (only on the screen), each line's number on the left (as in error messages,
  e.g. `(line 3)`), and long lines unwrapped, with scroll bars, unless you tick **Wrap
  lines** under the box (for every code box; remembered in `editor.ini`). Enter starts the
  new line as deep as the one before (one level deeper after a Python line ending with `:`),
  Tab inserts spaces to the next multiple of 4, Tab and Shift+Tab indent and unindent the
  selected lines by 4 spaces, and Backspace after indentation goes back a level.
- The **+** just right of the last tab opens a new flow (as File → New flow).
- Each open flow's tab shows its last result (a coloured dot; a red ✕ while any line's last
  run failed) and, when it is scheduled, a clock; hover the tab for how each start block's
  line last ended (e.g. `manual: ok 2026-10-20 10:28:20`; re-running a block counts for the
  lines it is in) and when it runs next. Bottom left, **N flows running**
  lists the flows running now (also on schedule or from a terminal), with when each one's
  next scheduled run is: choose one to open it. While the scheduler service is off, the tabs
  and this list say so instead of a next run.
- **Run now** (it first says what it will run; you can turn that off) saves, checks and
  runs the flow in a separate process: blocks show their
  status live, the **Log** shows the selected block's messages (when a block finishes, its line
  gives the full path of each table it produced, to copy; what a database driver or library
  prints shows as plain `OUTPUT`), **Data preview** shows
  everything the selected block produced (tables as a grid, other values as text), and
  **Run history** reopens any past run; its **Started by** column says what started each
  run: its start blocks (e.g. `manual`, `nightly`), or `only BLOCK` / `from BLOCK` for a
  re-run. **Stop** cancels.
- **After a run, hover** over any input, output or connection line to see the value that
  went through it (a table over 10 rows: its first 5 rows, `…`, its last 5). Every run's
  values are kept in **Run history** (double-click a run, then hover) for as long as its start
  block's **Keep runs** says (by default `keep_runs_for`: 5 years). Hover over the message under a block to read all of it. The canvas follows the runs of the flow, including scheduled ones: each block
  shows its result from the latest run it took part in, and a run that starts clears only
  its own blocks. Opening an older run from Run history shows only that run (the blocks not
  in it show nothing) until you start a run or open the newest one. A flow you open starts
  clean, unless it ran since you last saw it (e.g. on schedule). The Log keeps the messages
  of the earlier runs above it, each run starting with a bold line with its number, date
  and time.
- **A block's earlier results:** right-click it → **Results of this block…**: one row per
  run it took part in, newest first, with the run, when it started, what started it, how
  the block ended and its result or error. Select a row to see the values the block's
  outputs held in that run; **Show this run** (or double-click the row) puts that run on the
  canvas.
- **Text on the canvas:** drag **Text** (under **Notes** in the blocks tree) onto the
  canvas, or right-click an empty spot → **Add text here**, for titles and remarks.
  Double-click it to write in it. When it is selected, drag the square past its corner to
  make the text bigger or smaller, and the square on its right to change where it wraps.
  Properties has its style (title, heading, normal, small), bold, italic, underline and
  any font installed on your computer. Text does not run.
- **Retry after a fix:** right-click the block →
  - **Run only this block, with the same inputs**: only that block runs, with what reached
    it in the run its result on the canvas comes from (the menu names that run; a block
    showing no result, e.g. one added since: the latest run among the results of the blocks
    connected into it); the other blocks keep their results
    (`taskloom run FLOW --only BLOCK --inputs-from RUN`);
  - **Run from this block on, with the same inputs** (or **with inputs I choose…**): that
    block and every block after it
    (error handlers too) run; the blocks before it are not run again, what they held in
    that run is used (`--from BLOCK --inputs-from RUN`).
  Inputs that held a password are never saved, so such a block needs a full run. Blocks get
  what a full run would give them now (Taskloom says which block to retry first if needed).
- **Test a block with values you choose:** right-click it → **Run only this block, with
  inputs I choose…**. Each input is filled in with what reached it in the run its result comes
  from (a block showing no result: the latest run among the results of the blocks connected
  into it; empty if it did not run); change any of them and press Run. Write values as Python: `42`, `'text'`,
  `[1, 2]`, `{'a': 1}`, `None`; tables as `table('data.csv')`, `table('data.parquet')` or
  `pl.DataFrame({'a': [1, 2]})`. An empty field means no value. Next time the window shows
  what you typed and works it out again, so after editing `data.csv` just press Run.
  (`--input NAME=VALUE` with `--only` from a terminal.)
- **When a block fails**, the Log says which block, what came into each input (from which
  block and with what value) and, for Python Code, the code around the failing line and
  the values of your variables.
- To check data mid-flow, put an **Inspect** block (Debug) between two blocks: it passes
  the data on and shows it in the Log and Data preview. A table's columns line up as in
  its hover tooltip. Its **message** is written like a Python f-string, e.g.
  `Healthy: {value['healthy']}`. Example: `examples/check-app-health.yaml` (Health Check's
  `result` output says healthy or not).
  - A value that is a picture's path (.png, .jpg, .jpeg, .gif, .bmp, .svg or .webp, e.g. a
    Chart's `image`; a relative path is in the flow's folder) shows in the Log as its full
    path with the picture under it, scaled down to fit 640 × 640 (in a terminal, just the
    path). If the file has gone since, the Log says so.
  - Tick **Show as Markdown** to draw the message (or, with no message, a text value) as
    Markdown: `#` headings, `**bold**`, `*italic*`, lists, `| tables |`, `` `code` `` and
    code blocks. Lines next to each other join into one paragraph, so leave a blank line
    between paragraphs. Mermaid diagrams (```` ```mermaid ```` code blocks) are drawn as
    pictures, offline, on the Log's background (drawn again at once when the editor turns
    light or dark) and scaled down to fit 640 × 640 (in a terminal, the Markdown as
    written); one that cannot be drawn shows as written, with a note saying why. Links are
    shown but open nothing.
  - Past and scheduled runs reopened from Run history show the same pictures and Markdown.
- **Several blocks connected into one input of a Python Code block:** that input is a dict
  keyed by each block's name (as on the canvas), with its output as the value, in the order
  of the connections, e.g. `input["load"]`; with one block connected it is that block's
  output. A block that connects two of its outputs there is keyed `block.output` (e.g.
  `check.result`, `check.details`). Only the blocks that delivered are in it (after an If, the
  path that ran). Other blocks that take several connections into one input (e.g. Inspect)
  get a list, in the order of the connections; HTML Template's `data` names each one (see
  Reports and email).
- **One error handler for many blocks:** connect the red `error` output of every block you
  want covered to one input of one block (e.g. a Python Code block); it runs once and gets
  the errors of the blocks that failed, each naming its `block` (a Python Code block gets
  them as a dict keyed by those blocks' names). Example: `examples/shared-error-handler.yaml`.
  A failure is handled only when a block its error goes to runs: if every one of them is
  skipped (e.g. it also needs the failed block's data, or has one input per covered block and
  not all of them failed), the run fails, as if the error output were not connected.
- **Python packages:** Tools → Python environment creates your own Python environment and
  installs packages with pip. Tick **Run in my Python environment** on a Python Code
  block to use them (`taskloom env create`, `taskloom env pip install NAME` from a terminal).
  A Python Code block you add in the editor starts with it ticked (the flow file says
  `use_environment: true`; a block whose file leaves it out runs in Taskloom's own Python,
  where your code also has `pl`, `Table` and `ctx`) and with a few lines of comments as its
  code that show what your code receives and sets: each input as a variable named as in
  Inputs, several connected blocks as a dict keyed by their names, tables as pandas
  DataFrames, `params`, `print()` to the Log, and `output`. As it is, that code runs and
  sets no output.
  In your environment every table a block receives, also inside a dict or list, is a pandas
  DataFrame, so a block with a table among its inputs needs pandas and pyarrow there
  (`taskloom env pip install pandas pyarrow`); without them it fails before your code runs
  and says so. A pandas or polars DataFrame (or pyarrow Table) your code sets comes out as
  a table.
- **`print()` in a Python Code block** goes to that block's Log (and run history), like
  `log("...")`. `sys.exit()` ends the code there and the block succeeds; given a value other
  than 0 (e.g. `sys.exit("no data")`) the block fails.
  The screen shows the environment in use; **Use an existing environment…** picks one you
  already have (the `python_env` setting).
- **A Python Code block as a block of your own:** right-click it → **Create Reusable
  block…**, give it a name (its title in the blocks tree), say what it does and what its
  inputs expect, and press OK. It is saved as a Python file in your blocks folder (e.g.
  *Tidy names* → `blocks/tidy_names.py`, block type `user.tidy_names`) and listed at once
  under **User Created**, its hover text saying what it does and what its inputs expect.
  Added to any flow, it runs that block's code on what is connected to its inputs, with the
  same inputs, outputs and **Run in my Python environment** choice, in the editor, with
  `taskloom run` and on schedule. It has no Code field (its Properties say what it does and
  what its inputs expect): to change its code, edit its file or make a new one. A name
  another block already has (as its title or block type) is refused. The block it was made
  from stays as it is.
- **Your work is safe:** unsaved changes are copied to a recovery folder as you work, and
  the flows you had open come back with them after a crash or a restart. If the editor
  ever closes unexpectedly, the details are in `crash.log` in your Taskloom folder. The
  list of open flows is kept in `editor.ini`, so a new version opens where you left off.
- Connections: use **New…** or **Edit…** next to a block's connection field, or **Tools →
  Connections**. Type the password in the Password field (it is stored in the system's
  secure store, never in a file) and click **Save and test connection**. For a database,
  choosing the driver .jar file(s) with **…** fills in the Driver class from the jar
  (JDBC 4 drivers name it in `META-INF/services/java.sql.Driver`): when the jar names several,
  you choose one; when it names none (an older driver), a note says so and you type it. A
  class you typed is replaced only if you say so.

## Versions with Git: Save & Commit

**File → Save & Commit…** keeps versions of a flow in Git. Its window says where the flow is
committed, and asks for a message and the name and email to commit with; its **Save &
Commit** button saves the flow, then commits that file, and only it, to the Git repository
of the flow's folder (found in that folder or a folder above it, as Git finds it). With none,
it creates one in the flow's folder, so normal Git tools see it. Nothing else is staged or
committed: files you staged stay staged, and data, outputs, other flows and your Taskloom
folder stay as they are. A flow that is the same as in its last commit makes no commit.

- Saved does not mean committed: File → Save (and the saves of Run, Validate and closing)
  never commit. The next Save & Commit commits whatever differs from the last commit.
- It refuses, before saving anything, when the repository has no branch checked out
  (detached HEAD), is in the middle of a merge, cherry-pick, revert or rebase, cannot be
  read (e.g. a split index), or ignores the flow (not committed yet), and when it would have
  to create a repository in your home folder or at a drive root. File → Save still saves.
  When the commit fails after the save (e.g. another program is using the repository), it
  says so: the flow is saved; Save & Commit again once that is fixed.
- The name and email are the ones you typed last (kept in `editor.ini`), else `user.name`
  and `user.email` of your Git config.
- **Save as…** gives the copy the original's history up to its last commit. In the same
  repository, one commit *Copy a.yaml to b.yaml (Save as)*: `git log --follow -- b.yaml`
  lists it, then a.yaml's versions (and never a.yaml's later commits). In another folder's
  repository (created there if there is none) each version is committed again, oldest
  first, with its message, author and date (you are the committer). Saving as over a
  committed file first commits its removal. Changes not committed yet are only in the
  copy's file (it shows as modified). A flow never committed, or in no repository, gets
  nothing in Git. When the history cannot be copied (e.g. no name and email known yet: give
  them once in Save & Commit), the copy is saved and Taskloom says why.

Taskloom carries its own Git (dulwich): no Git program is needed. Its commits run no hooks
and are never signed, no `.gitattributes`, filter, LFS or line-end conversion is applied (the
committed bytes are the file's), and it never writes any Git config. Flow files are written
with LF line ends on every OS.

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

- `:name` in SQL binds a parameter with its type (a `list` as its items, for
  `where region in (:regions)`); `{name:format}` fills text.
- Every block has an `error` output; connect it to handle failures.
- Results stream to Parquet on disk, so millions of rows never sit in memory.

Built-in blocks: `logic.python`, `logic.if`, `logic.wait`, `logic.ask_user`,
`logic.for_each`, `data.duckdb_sql`, `data.polars`, `data.read_file`,
`data.write_file`, `db.run_query`, `files.copy`, `files.move`, `files.delete`,
`servers.ssh_command`, `servers.start_process`, `servers.stop_process`,
`servers.health_check`, `servers.remote_extract`, `reports.html_template`,
`reports.chart`, `reports.excel`, `email.send`.

## Reports and email

`examples/sales-report-email.yaml` builds a chart, an Excel file with a native Excel
chart and an email body with the chart and the table, and emails them. Emails are built
for desktop Outlook: table layout, CSS moved into `style=""` attributes, charts as inline images with
explicit sizes (never `data:` URIs or SVG), plus a plain-text version (one line per
list item). Headings, paragraphs, lists and table cells get their font and spacing inline;
a style you write yourself wins.

- **Chart**: `x_label` and `y_label` name its axes, e.g. column `store_id` shown as `Store ID`
  (empty: the column names; a pie chart has no axes).
- **HTML Template** composes the email body. Connect any number of outputs to its `data`
  input: Properties lists each one under Inputs with a name, at first its block's name (a
  name already taken, e.g. by another input or a flow parameter, gets `block_output`, e.g.
  `check_details`); change it there. The Template is HTML in which `{name}` shows an input
  or a flow parameter and `{name:format}` formats a number or date, e.g. `{total:,.2f}` or
  `{day:%d %B %Y}`; write `{{` and `}}` for literal braces (also in a `<style>` block), e.g.
  `Hi {input1}, please see below the chart<br>{chart}<br>and below the table<br>{my_table}<br>thank you`.
  Each value is shown by its type: a table as an Outlook-safe table (at most **Max table
  rows**, 500 by default), a chart's picture inline, HTML from another template as it is, a
  list item by item, nothing for an empty value, and anything else as its text, escaped,
  where you put it. Enter makes no new line in the email: write `<br>`, and put a table or
  a chart between `<br>`s rather than inside a `<p>`. An empty Template shows every input in
  turn. A name the Template does not know, or a brace left alone, is shown in red and stops
  Validate and runs; every connected block must deliver (after an If, the path not taken
  skips the template, and the email).
- **Formatting buttons** above the Template box (and every html code box): B, I, U, Heading,
  • List, 1. List, Link… and New line write Outlook-safe tags around the selection (`<b>`,
  `<i>`, `<u>`, `<h2>`, one `<li>` per selected line in a `<ul>` or `<ol>`, `<a href>`, `<br>`);
  each click is one Ctrl+Z. Enter alone makes no new line in the email: use New line.
- **Send Email** takes any number of body parts and attachments; `save_copy` also
  writes the email as an `.eml` file you can open in Outlook. Text is shown as written
  (HTML comes from HTML Template).

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
  - schedule: {every: 5 minutes}                             # every 5 minutes, from when it is switched on
  - schedule: {at: "2026-10-08 15:30"}                       # once
overlap: skip        # if the previous run is still going: skip | queue | allow
```

Add a **Schedule** block to the flow, connect it to the first block, set **When** in
Properties and save: saved flows with a Schedule block are listed in `scheduled.yaml`
(removing the block takes the flow off).

A schedule repeats in one of two ways:

- **Every N minutes or hours** (`every: 5 minutes`, `every: 2 hours`; in When, **Every few
  minutes or hours**) counts from when it is switched on: the moment the scheduler service
  first sees it as it is now (you save the flow with the service on, change its timing, tick
  Active again or schedule the flow again, or the service is turned on for the first time
  with it). Its first run is then, and the next ones every N after it, to the second:
  switched on at 10:00:14, every minute runs at 10:01:14, 10:02:14, ... Under the block, the
  countdown goes to its next run (its tooltip lists the next times, with seconds); until the
  service has seen it, it says it starts when the service picks it up. Turning the service
  off and on again does not move it: the times while it was off were missed (see `misfire`).
- **On the clock**: every hour at :MM, every day, weekday, chosen days or month at a time,
  and a cron line (`*/5 * * * *` runs at :00, :05, :10, ...), plus one time (`at`).

The **scheduler service** runs scheduled flows at their times. It is a program of its own
that keeps running in the background (its icon is in the tray), also while the editor is
closed, and notifies you about failed or skipped runs. It starts by itself when you save a
scheduled flow and when the editor opens with flows scheduled. The **Scheduler service**
button at the top right shows whether it is on or off; click it to turn it off (or **Turn
off the scheduler service** on its tray icon): then no scheduled flow runs, and it does not
start by itself (also not at login), until you turn it on again. While it is off, a Schedule
block says **scheduler service off** instead of its countdown. On Windows, **Tools →
Settings → Start the scheduler service when I log in** (or
`taskloom scheduler --at-login on`) adds it to your Startup folder; no admin needed. From
a terminal or on a server: `taskloom scheduler`.

The scheduler service keeps running when you open a newer Taskloom: the editor then offers to
restart it with the newer version (a scheduler from an older version may not read flows
the newer one saved), and starting at login switches to the newest version you opened.

One scheduler service runs every scheduled flow. Each run begins at its time: the service
starts the run's process a few seconds early, to load the flow and wait, so the Log says the
run started at its time (from a terminal: `taskloom run FLOW --start-at 2026-10-08T15:30:00`).
A run whose schedule goes off before then (the service turned off, Active unticked, the flow
taken off) does not begin. The service checks every second and then rereads changed flow files,
so a schedule applies within a second of saving it, without restarting the service; a new or
changed schedule, or one turned back on (Active ticked again, the flow scheduled again), counts
from that moment (nothing before it counts as missed).

At each check, of the times that came since the previous check only the most recent can be
on time, and only if it is at most 2 minutes old; the others were missed (the scheduler
service was off, the computer asleep, ...). `misfire` says what runs:

- `run_once` (the default): one run, for the most recent time, on time or missed;
- `skip`: only the on-time run, if there is one; one **Missed runs skipped** notification
  says how many were missed;
- `catch_up`: one run for each time, oldest first, one after another (at most 100).

A run's parameters are computed for the time it was due (that is what `now()` means).

## Servers

Add an SSH connection (Tools → Connections) and use the Servers and Files blocks.

**Copy Files**, **Move Files** and **Delete Files** work on this computer (its folders and
network folders, e.g. `\\fileserver\share`) and on servers. For each path, Properties shows
where it is, **This computer** or one of your SSH connections (**Source on**, **Destination
on**, **Path on**), with the path in its own field:

- **Source** (and Delete's **Path**): a file (`C:/data/sales.csv`, `~/outbox/sales.csv`), a
  folder with everything in it (`C:/data/reports`), or a pattern in the last part
  (`~/logs/*.log`). Delete Files deletes a link, never what it points to.
- **Destination**: a folder, ending with `/` (`~/incoming/`): what is copied goes into it, a
  folder with its name; or, for one file, its new name. Missing folders are made.
- **Overwrite** (on unless you untick it) replaces a file already there; off, nothing is
  copied if any file is already there, and the block fails. Move Files copies, then deletes
  the source.

```yaml
fetch:
  type: files.copy
  config: {source_server: appserver, source: "~/outbox/*.csv", destination: "inbox/"}
clean:
  type: files.delete
  config: {server: appserver, path: "~/outbox/*.csv"}
```

A path written the old way, `connection:path` (e.g. `appserver:~/app/`), is refused, saying
what to change: choose the connection where the path is, and write the path alone.

An app watchdog is a flow: Health Check (URL and/or PID file) → `unhealthy` →
Start Remote Process → Health Check, scheduled every few minutes.

**Start Remote Process** runs your command as you would type it (`cd app && ./run.sh >>
app.log 2>&1` is fine; no need for `nohup` or a final `&`), keeps track of it, and checks
it is still running a few seconds later. For a command that does its job and finishes
(e.g. a script that starts a service), untick `must_keep_running`. **Stop Remote Process**
stops the app and everything it started, forcing whatever has not stopped after `grace`.

On a server without root or crontab, run `taskloom scheduler --headless` there and
list the server in the `keeper` setting (Tools → Settings) on your PC: your
scheduler checks it every 5 minutes and starts it again after a server reboot, or replaces
it once it has hung (stopped checking): it stops the process that holds that Taskloom
folder's `scheduler.lock`, found through `/proc` or `lsof` on the server, and no other. If
it cannot, it tells you why.

## Checking flows after an update

Each version can check your flows for what it can fix and what needs you: **Tools → Check
flows…** (the open and scheduled flows; it also runs by itself the first time a new
version starts), or `taskloom check [FLOW…]`. It fixes an older flow format, a block's
settings saved by an older version of that block, settings a block no longer has,
connections to blocks, inputs or outputs that no longer exist (e.g. after renaming an
output) and connections listed twice, and adds a Manual Start (connected to its first
blocks) to a flow without a start block. **Back up and fix** (or `--fix`) first copies each
flow into `backups/` in your Taskloom folder. Anything else it finds is listed for you to
fix.

## Your files

Everything per-user lives in `~/.taskloom` (or `$TASKLOOM_HOME`):

| File | Purpose |
|---|---|
| `connections.yaml` | Database connections, e.g. a JDBC driver, URL, jar and properties |
| `settings.yaml` | `calendar`, `keep_runs_for`, `java_home`, `max_concurrent_runs`, `keeper`, notifications |
| `calendars/<name>.yaml` | Holidays: `base: US`, `add: [...]`, `remove: [...]`, `weekend: [sat, sun]` |
| `blocks/*.py` | Your own blocks, listed under **User Created** in the blocks tree; same `type_id` as a built-in replaces it |
| `history.db` | Every run, block result and log line |
| `editor.ini` | Editor preferences, the flows you had open, and the name and email you commit with |
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

Runs are kept for `keep_runs_for` in `settings.yaml`: a number and `days`, `months` or
`years`, e.g. `18 months` or `90 days` (5 years by default), unless their start blocks say
otherwise (below). As a run ends, every run to let go is deleted: its history, block results
and logs, and its folder in `runs/` with its files. A run still going is never deleted, and a
re-run keeps the tables it reused from an older run. Tools → Settings shows it, with how much
space the runs take now.

Each start block (Manual Start, Schedule) says how its runs are kept, in Properties →
**Keep runs**: **As in Settings** (the default: for `keep_runs_for`), **All of them**, **The
last N** (e.g. the last 100 runs of an every-minute schedule), or **For a time** (a number of
days, months or years, longer or shorter than `keep_runs_for`); and with **Always keep failed
runs** ticked, a failed run is kept whatever the choice. In the flow file they are the block's
`keep: all`, `keep: last 100` or `keep: 18 months` (none: as in Settings) and
`keep_failed: true`. A run counts for the start blocks that started it (its Started by), and
a re-run of a block (`only X`, `from X`) for the start blocks whose lines hold that block; it
is deleted only when every one of them lets it go. Runs of a flow without start blocks, and a
re-run of a block on no line, are kept for `keep_runs_for`; it never deletes a run that a
start block keeps. Each flow file's start blocks count apart, so two flows' start blocks of the
same name never delete each other's runs. Each run records the Keep runs of its flow's start
blocks, and the flow's newest run decides for all their runs: a change applies to earlier runs
too, from when the flow's next run ends (lowering The last N then deletes the older ones). The
last N counts the finished runs: a run still going is neither deleted nor counted.

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

Give each setting `help=` (what it is for) and `examples=` (1 to 3 values, each as you would
type it in Properties): its card and its entry in Help → Documentation show them, e.g.
`fields.Text(default="Hello", help="The word before the name", examples=["Hello", "Good morning"])`.

A block whose `run()` may succeed without returning all its outputs (as If gives `true` or
`false`) sets `has_done = True`: it then also has a **done** output, which fires whenever it
succeeds, for another block to run after it. A block without outputs has **done** anyway.
Without it, an output that `run()` does not return skips a block waiting for it in its run
after ("nothing reached its run-after").

## Development

```
uv sync
uv run pytest
```

The JDBC tests start an H2 database server and need Java; they download the H2 jar once.
GitHub Actions (`.github/workflows/build.yml`) runs the tests on Linux and Windows, builds
the Windows zip and the Linux runner, and smoke-tests both; tagging `v*` publishes them.

The documentation website, <https://lben.github.io/taskloom/>, has the pages of Help > Documentation
with the built-in blocks; `uv run python packaging/docs_site.py site` builds it into `site`. After
the release of each `v*` tag, the workflow's `pages` job publishes it on GitHub Pages. Nothing waits
for that job, so it never holds up a release. It needs a one-time switch: in the repository's
Settings > Pages, set Source to GitHub Actions (until then the job fails on its own). If GitHub
then refuses to deploy from a tag, also add the tag rule `v*` once under Settings > Environments >
github-pages > Deployment branches and tags.

## License

MIT. The packaged editor includes dulwich (Apache-2.0 or GPL-2.0-or-later), urllib3 (MIT), and
mmdr and novasvg (MIT), which draw Mermaid diagrams; their licence texts are in its `licenses`
folder. mmdr is built from Rust components, some under the MPL-2.0 or the EPL-2.0 and included
unmodified: `licenses/mmdr` has those licence texts, where each one's source is, and mmdr's
full list of components.
