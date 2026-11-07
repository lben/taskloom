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
  Hover over a block to see what it does. Each block shows its icon left of its name, on its
  group's colour (blocks of your own: a puzzle piece).
- **Documentation in the editor:** rest the mouse on any row of the Properties tab for a card
  with what it is for, examples of what it takes, and **Learn more ›**, which opens that row's
  entry in **Help → Documentation** (F1): a column at the right of the window, next to the
  canvas and Properties, with this guide and a reference of every block and setting (your own
  blocks too), and Back, Forward and Contents. Drag its edge to resize it, or float it.
- **Theme:** Tools → Settings → **Theme** is **System** (the default: light or dark as your
  operating system is, also when it switches while the editor is open), **Light** or **Dark**
  (colours like those of JetBrains' dark theme). It applies at once to the whole editor and is
  kept in `editor.ini`, not in `settings.yaml`.
- **Zoom** with the mouse wheel or the control at the top right of the canvas: it shows the
  zoom, and **+** and **−** zoom in and out by the step between them (5% by default, change
  it with its arrows), from 20% to 300%. Or click the zoom, type one (`150` or `150%`) and
  press Enter.
- **Copy and paste** (Ctrl+C, Ctrl+V or the Edit menu) the selected blocks and texts, in the
  same flow or into another one, at any zoom: the copies keep their layout and land where you
  are working. With the mouse pointer on the canvas, Ctrl+V puts their top-left corner at the
  pointer; right-click an empty spot → **Paste** puts it at that spot; otherwise (Edit →
  Paste clicked, or the pointer on a panel) they go in the middle of the visible canvas.
  Pasted again at the same place, they go 40 px right and down of the copies already there,
  so none is hidden under another; likewise while a pasted text would be under a block, or a
  pasted block wholly over a text (texts are drawn behind blocks); if that takes them out
  of view, the canvas scrolls to show them. The copies get new names and are selected, to
  move them; Undo takes the whole paste back.
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
- **A run is checked for the blocks it takes part in.** A problem in a block that does not
  take part in it (an off block, or a block only another start block's line reaches), e.g.
  an If without its Condition or an input left unconnected, does not stop the run: its Log
  shows a warning naming the block and the problem, and saying it does not take part in this
  run. A run that block takes part in is refused, as are all runs for a problem with the
  flow's connections, parameters or settings, or a loop through a block that takes part.
  **Validate** (F7), `taskloom validate` and `taskloom check` list every problem: those of
  an off block as warnings that say it is off.
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
  name when you first save it) and calendar. Selecting a block or a text shows its
  Properties; an edit on Parameters or Flow stays on that tab, and what is selected stays
  selected.
- Some settings are only for some choices of another setting: Properties shows them only
  then, and shows or hides them at once when you change that choice. A value left in a
  setting that is not shown stays in the flow file, but runs do not use it. Help >
  Documentation says which choices each such setting is for.
- **Ask User** asks you a question when you run the flow from the editor. Its **Kind** is
  what it takes: `text`; `number` (the question box takes only a number: for anything else
  it says to type one and keeps asking; the answer is a whole number such as 7, or 2.5, so
  an If after it can compare it, e.g. `value > 10`);
  `password`; `yes_no` (true or false); or `choice` (one of its **Options**, which only
  this kind has). Only a password answer is hidden (`***` in the Log, Inspect, Data preview
  and the run history); any other answer shows as it is. Runs nobody can answer (scheduled
  ones, `taskloom run`) use its **Default**, which every kind but password has; for kind
  number it must be a number (or have `{name}` placeholders, read as the block runs), or
  Validate and runs refuse the flow. A password never comes
  from a Default: its **Stored secret** setting names a password saved in Taskloom's secret
  store (`taskloom secret set NAME`), and runs nobody can answer use that one. With no
  name there, or a name never saved, such a run fails and says what is missing.
- In Ask User's question box, the **Default** of a text or number question is shown in
  grey in the empty box, with a note under it: OK (or Enter) with the box left empty
  answers the Default, and the Log says so; what you type replaces it. A choice question
  opens on its Default; for yes_no, Enter presses the Default's button (Yes without one); a
  password box shows no Default. The box waits above the editor without blocking it, so
  **Stop** can be clicked while it is open. When the run stops waiting for the answer (you
  press Stop, the block's Timeout runs out, or the run ends), the box closes by itself and the
  Log says why; what was typed into it is not used, and a retry asks again in a new box.
- **Python and SQL code** (Python Code, Polars Transform, DuckDB SQL, Run Query and blocks
  of your own) is written in a code box: keywords, strings, comments and numbers in their
  own colours (only on the screen), each line's number on the left (as in error messages,
  e.g. `(line 3)`), and long lines unwrapped, with scroll bars, unless you tick **Wrap
  lines** under the box (for every code box; remembered in `editor.ini`). Enter starts the
  new line as deep as the one before (one level deeper after a Python line ending with `:`),
  Tab inserts spaces to the next multiple of 4, Tab and Shift+Tab indent and unindent the
  selected lines by 4 spaces, and Backspace after indentation goes back a level.
- **A new block where you write code** (added in the editor) starts with a few lines of
  comments, in its language's own form, that show with small examples how to use it: DuckDB
  SQL (`--`: each connected table by its name in Inputs, at first `input`; `:name`
  parameters; its `result`), Run Query (`--`: `:name` from the flow parameters or its
  `params` input; its `result`), Polars Transform (`#`: `df`, `pl`, `params["name"]`; its
  `table`) and HTML Template (`<!-- -->`: `{name}`, `{name:format}`; its `html`). No need to
  delete them: what you write below or between them runs as if they were not there (SQL is
  sent with its comments), and code that is only comments counts as empty, so Properties,
  Validate and runs say it is required (e.g. `load: SQL is required`); an HTML Template of
  only comments shows every input in turn, as an empty one does. A new If block's condition
  starts with `# No need to delete this. Examples: value > 10, value is True, value ==
  'Option A'`: lines starting with `#` in a condition are comments, and one of only comments
  is `Condition is required`. A Python Code block of only comments still runs (its output
  is `{}`), and Inspect's message starts empty. Blocks already in a flow file, and pasted ones, keep
  what they have.
- The large **+** just right of the last tab (as tall as the tabs) opens a new flow (as File → New flow).
- Each open flow's tab shows its last result (a coloured dot; a red ✕ while any line's last
  run failed, else a grey ✕ while one was stopped) and, when it is scheduled, a clock; hover
  the tab for how each start block's line last ended (e.g. `manual: ok 2026-10-20 10:28:20`;
  re-running a block counts for the lines it is in) and when it runs next. Bottom left, **N flows running**
  lists the flows running now (also on schedule or from a terminal), with when each one's
  next scheduled run is: choose one to open it. While the scheduler service is off, the tabs
  and this list say so instead of a next run.
- **Run now** (it first says what it will run; you can turn that off) saves, checks and
  runs the flow in a separate process: blocks show their
  status live, the **Log** shows the selected block's messages (when a block finishes, its line
  gives the full path of each table it produced, to copy; what a database driver or library
  prints shows as plain `OUTPUT`), **Data preview** shows
  everything the selected block produced (tables as a grid, other values as text; a table
  inside a dict or list also on its own, by where it is, e.g. `output.combined`), and
  **Run history** lists every run of the flow as its own row, newest first, whoever started
  it (Run now, the scheduler service, a terminal): a run started while the flow is open
  comes in as it goes, also while an older run is shown or one of yours is going. The run you
  select (click its row) stays selected as runs come in and finish (with Ctrl+F open, the
  current match stays on its run and the count follows), and the list stays where
  you scrolled to (at the top, each new run shows at the top); a selected run that Keep runs
  removes leaves nothing selected. The runs
  are those of the flow's file, however its path is spelled (through a link, with `..`, or
  in other case where the file system ignores case). It reopens any past run; its
  **Started by** column says what started each
  run: its start blocks (e.g. `manual`, `nightly`), or `only BLOCK` / `from BLOCK` for a
  re-run. Right-click a run → **Copy executed query** copies the SQL a Run Query or DuckDB SQL
  block sent in it, also one the database refused, with each parameter's value written in
  (`'O''Brien'`, `DATE '2026-01-16'`, a secret as `<hidden: secret NAME>`), to paste into a
  SQL tool and run; several such blocks are listed in the order they ran, and a run in which
  no query ran (or one recorded before runs kept their queries) says so. **Stop** cancels.
- **Ctrl+F** (Cmd+F on macOS) in the Log, Data preview, Run history or a code box opens a find
  bar at its bottom (just under a code box) that counts (`3/12`) and highlights the matches of
  what you type, or of the text selected there, and steps through them with Enter and
  Shift+Enter (or ↓ and ↑) until Esc, with a `.*` toggle for regular expressions (on as the
  editor starts; a pattern that is not valid shows in red and says so) and an `Aa` toggle to
  match case (off), both shared by every find bar while the editor runs.
- **After a run, hover** over any input, output or connection line to see the value that
  went through it (a table over 10 rows: its first 5 rows, `…`, its last 5). Every run's
  values are kept in **Run history** (double-click a run, then hover) for as long as its start
  block's **Keep runs** says (by default `keep_runs_for`: 5 years). Hover over the message under a block to read all of it;
  its bottom right says when that result was made, the time the block finished in its run (e.g.
  `results from 2026-10-31 14:03:22`; none while the block runs). The canvas follows the runs of the flow, including scheduled ones: each block
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
  the values of your variables. A failure Ask User expects (you chose **Don't answer**;
  nobody can be asked, e.g. in a scheduled run, and it has no Default, or for a password no
  stored secret or one never saved; an answer that is not one of its Options; kind choice
  without Options) says so in a short message,
  without a Python traceback. It is a failure like any other: it goes out the block's
  `error` output, and a retry setting on the block asks again. When you run a block again
  (Retry after a fix, Test a block with values you choose), each connection into an input
  shows what came by it in the earlier run (or what you typed), and "(nothing arrived)" only
  when nothing came by it, e.g. the path an If did not take or an input you left empty.
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
  DataFrames, `params`, `print()` to the Log, and what goes out. As it is, that code runs
  and its output is `{}`.
  In your environment every table a block receives, also inside a dict or list, is a pandas
  DataFrame, so a block with a table among its inputs needs pandas and pyarrow there
  (`taskloom env pip install pandas pyarrow`); without them it fails before your code runs
  and says so.
- **What a Python Code block passes on:** unless your code sets `output`, its `output` is a
  dict of every variable your code creates, by name, e.g. `{"sales": ..., "region": ...,
  "north": ...}` (`{}` when it creates none). Not in it: its inputs (e.g. `input`), `params`
  and the helpers (`print`, `log`; in Taskloom's own Python also `pl`, `Table`, `ctx`), the
  modules it imports, the functions and classes it defines, and names starting with `_`
  (e.g. `_scratch`). To send something else, set it yourself, e.g. `output = north` (or
  `output = None`): that is what goes out. Other Outputs you name are produced only when
  your code sets them. A DataFrame (pandas or polars, or a pyarrow Table) comes out as a
  table, also inside a dict or list at any depth, and the run keeps it with the rest, so
  **Run only this block** or **Run from this block on** (with the same inputs) on a later
  block gets the dict again, tables included. Hovering the block's result shows each
  table's size, and Data preview shows each table on its own (e.g. `output.combined`).
  A variable that cannot be passed on (an open file, a generator, a database connection;
  in your environment also a value Taskloom's own Python cannot read, such as a pandas
  Series) is left out, and one line in the Log names each one and why. In your
  environment, an output you set to such a value fails the block, saying why.
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
  from stays as it is. To delete a block of your own, right-click it under **User Created**
  in the blocks tree (or select it and press Delete) → **Delete block…**: once you say yes
  to a question naming its file, any other blocks that file defines (deleted with it) and
  the blocks of open flows that use it (shown as unknown blocks from then on), the file is
  deleted and the block is gone from the editor at once (the User Created group too when
  none is left); built-in blocks cannot be deleted.
- **Your work is safe:** unsaved changes are copied to a recovery folder as you work, and
  the flows you had open come back with them after a crash or a restart. If the editor
  ever closes unexpectedly, the details are in `crash.log` in your Taskloom folder. The
  list of open flows is kept in `editor.ini`, so a new version opens where you left off.
- Connections: use **New…** or **Edit…** next to a block's connection field, or **Tools →
  Connections**. Type the password in the Password field (it is stored in the system's
  secure store, never in a file) and click **Save and test connection**. A database
  connection asks for its driver .jar file(s) first, then the Driver class, URL, User name
  and Password. Choosing the jar(s) with **…**, or typing or pasting their paths (separate
  several with `;`) and then leaving the field or pressing Enter (there it does not save),
  fills in the Driver class from the jar (JDBC 4 drivers name it in
  `META-INF/services/java.sql.Driver`): when the jar names several, you choose one; when it
  names none (an older driver), a note under the Driver class says so and you type it. A
  class you typed is replaced only if you say so. A typed path that does not exist is named
  under the jar field.
- Java gets the jars of every database connection at once (one class path), so a
  connection whose own jars do not hold its driver class can still connect through another
  connection's jars. Tools → Connections warns about it on that connection, naming the
  connection it relies on (removing that one breaks it), and **Save and test connection**
  says the same when it connects. Give each connection its own driver jar.

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

**File → Commit History…** (just under Save & Commit, for a saved flow) opens a window of
its own with the commits of the flow file, newest first: each card shows the message and, at
its bottom right, the author date and time and the short commit id (so the versions Save as
copies keep their dates). Typing in its search field (`.*` regular expressions, on as it
opens; `Aa` match case, off) filters the cards at once, grouped by where they match: first
the commit message, then the outputs of the latest run of that version, then the code of its
blocks (Python, SQL, Polars, HTML templates, If conditions, every code setting), then
anything else in the flow file; each card says where. Up and Down move through the cards,
also from the search field. On the right, a read-only preview draws the flow as it was at the
selected commit, with the results of the latest run made with exactly that version under its
blocks (hover one for its outputs, as on the canvas). Each run keeps the Git blob id of the
flow file's bytes it ran, so a run of changes not committed, or one from before runs kept it,
is no version's run. **Restore this version** puts that version into the flow's tab as one
change (Undo takes it back); nothing is written until you save it, and Save & Commit then
commits it as the newest version, the older ones kept. A Save & Commit adds its commit to the
open window; closing the flow's tab closes it.

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
- `{name}` placeholders work in every setting you type a value into, in every block: text,
  paths, whole numbers, numbers and each item of a list. `{name}` is the flow parameter
  `name`, or the value arriving at the block's own input `name` (not its run after);
  `{name:format}` formats it (`{value:.1f}`, `{day:%Y-%m-%d}`) and `{{` and `}}` are
  literal braces. A number setting reads the filled-in text as its number (spaces around it
  ignored), within its own limits, e.g. `count: "{value}0"`. SQL code takes flow
  parameters only; other code (Python, Polars, If, Check File's Since and Check Table's Latest
  at least, and an HTML Template, which fills in its own `{name}`), yes/no and choice
  settings, and a Splitter's At most at once (Validate checks the lines with it before the
  run) take none. Validate
  (F7, `taskloom check`, `taskloom validate`) refuses a name that is neither a parameter
  nor an input of the block, and one that is both (`{value} could be the parameter value or
  the input value: rename one`). Nothing is filled in silently: the
  block fails before it does anything, naming the setting, the placeholder and why, when
  nothing is connected to that input or nothing arrived there, a table arrived (only a
  single value can be written into a setting: text, a number, a date or time, yes/no), or a
  number setting's text is not a number (`Seconds: {value} gave 'abc', which is not a
  number`).
- **Wait** (`logic.wait`) pauses for its **Seconds**, then passes its `value` input on:
  a number, `{value}` for the number arriving at its value input, or `{name}` for a flow
  parameter, e.g. `seconds: "{pause}"`. The Log says once how long it waits and until when
  (`waiting 90 s, until 14:05:30`). In the editor, the line at the bottom of the block says how
  long it waits (`⏱ waits 90 s`) and, while it waits, counts down the time left
  (`⏱ 00:01:29 left`), as a Schedule block's line counts down to its next run.
- Every block has an `error` output; connect it to handle failures.
- Results stream to Parquet on disk, so millions of rows never sit in memory.

Built-in blocks: `logic.python`, `logic.if`, `logic.wait`, `logic.ask_user`,
`logic.loop`, `logic.splitter`, `logic.merger`, `data.duckdb_sql`, `data.polars`, `data.read_file`,
`data.write_file`, `data.statistics`, `data.detect_anomalies`, `checks.check_file`, `checks.check_table`,
`db.run_query`, `files.copy`, `files.move`, `files.delete`,
`servers.ssh_command`, `servers.start_process`, `servers.stop_process`,
`servers.health_check`, `servers.remote_extract`, `servers.api_request`, `servers.api_session`,
`reports.html_template`, `reports.chart`, `reports.excel`, `email.send`.

### Splitter and Merger: lines that run at the same time

A **Splitter** (`logic.splitter`) starts lines: each connection leaving its `lines` output
starts one, which gets its own copy of the value that arrived at its `value` input. A line
is the block that connection goes to and the blocks after it, up to the **Merger**
(`logic.merger`) that joins the lines; the Merger and what follows it come after the lines,
also a block that uses a line's output too. A block a Splitter's connection goes to starts
its own line. A Splitter inside another Splitter's line is not supported yet: Validate
refuses it.

- **Lines run**: *at the same time* (as the block is added) runs the lines together, each
  line's own blocks one after another, in the flow's order. **At most at once** (a whole
  number, 1 or more; empty: all of them) limits how many run at once, to spare the database
  and the computer: the next line starts as soon as one ends, in line order. *One after
  another* runs line 1 to its end, then line 2, and so on. Line order is top to bottom by
  where each line's first block is on the canvas, then left to right; in this mode the canvas
  numbers the connections leaving the Splitter 1, 2, 3, and the numbers follow when you move
  the blocks.
- The Log says once how the lines run, e.g. `3 lines at the same time, at most 2 at once:
  load_sales, load_costs, load_stock` or `3 lines one after another: 1 load_sales,
  2 load_costs, 3 load_stock`; when no line runs (**Run only this block** on the Splitter), it
  says so: `no line runs: only split runs`. With nothing at its `value` input (connected by
  run after only), its lines get `None` and it says under it `its lines start with nothing:
  no value arrived`. While lines run at the same time the canvas shows each of
  their running blocks as running, each Log line keeps its block's tag, and the run history
  keeps every line, result and status as with one block at a time.
- **Lines connecting across**: a block of one line may use what a block of another line
  makes; it waits until that has arrived. A block that several lines lead to runs in the
  last of them, so one after another it runs once those lines have. The lines of two
  Splitters side by side may feed each other one way, into the first block of a line. In a run
  with two or more lines, each block gets its own copy of what it
  receives, so no line can change another's data.
- The blocks outside the lines run one at a time, in the flow's order, as in a flow without
  a Splitter; a Splitter left at one line runs it so too. While one of them waits for the
  lines (the Merger), a block after it that need not wait runs meanwhile: a line may be
  waiting for it.
- The **Merger** passes on its lines as its **Passes on** says. *All lines together* (as the
  block is added, and in a flow file that does not say) waits until every line connected to
  its `lines` input has delivered, then passes on one value, `{line name: that line's
  output}`, named as Python Code names inputs from several blocks: the block's name, or
  `block.output` when a block connects several of its outputs (also with only one line
  connected). *Each line as it finishes* is below.
- **A failed line** stops no other line: the others run to their end, nothing is cancelled.
  The Merger then fails, saying which (`load_costs failed: …`, in the Log too), and what
  follows it does not run; its error output handles that as any block's. A line that delivered
  nothing without failing (an If sent it nowhere) makes the Merger skipped, saying which. The
  lines that succeeded keep their outputs: once you have fixed the failed block, right-click
  it > **Run from this block on, with the same inputs** (`taskloom run flow.yaml --from
  load_costs --inputs-from 12`) runs it, the rest of its line, the Merger and what follows;
  the other lines do not run again, their kept outputs reach the Merger. **Run only this
  block** on the Merger gets the lines' kept outputs.
- Stop cancels every running line at once; a block's Timeout ends only that block.
- **Validate** (F7, `taskloom validate`, `taskloom check`) refuses, before any run, a layout
  that could never finish, naming the connection: one after another (or at the same time
  with an At most at once lower than the number of lines), a connection from a later line
  into the block an earlier one starts with, which would wait forever for a line that cannot
  start before it ends (`edge load_stock.output -> load_sales.stock: from line 3 of split into
  line 1, …`). In any mode it refuses a Splitter inside another Splitter's line, a block that
  takes from the lines of two Splitters side by side, and the lines of Splitters side by side
  that feed each other both ways. It warns
  about a Splitter with fewer than two lines and a Merger with fewer than two connections
  (except one that closes the runs of a Merger set to *each line as it finishes*: with one
  connection it joins every one of those runs).
- **A run that cannot go on** never waits forever, whatever its layout: once no block is
  running and every block still to run waits for something no block can bring any more (a
  line's turn that never comes, what a block that cannot start makes), the run ends within
  about a second. The Log says which blocks wait for what (`the run cannot go on: load_sales
  waits for load_stock (line 3 of split), load_costs waits for its turn (line 2 of split,
  which runs its lines one after another), …`), each of them fails saying so, the blocks
  after them end as after any failure, and the run fails (`taskloom run` exits 1); inside a
  Loop, its item fails, and so the Loop. A run that is only slow is never ended so: a block
  running however long (also a Wait block, Ask User waiting for an answer, a block waiting to
  try again), and the lines waiting for it.
- **Each line as it finishes**: the Merger does not wait for every line; it passes on the
  lines' outputs one at a time, as each finishes (the value itself, not a dict), and the
  blocks after it run once for that line, as a Loop runs the blocks inside it once per item:
  up to the end of the flow, or up to a Merger set to *all lines together* further on, which
  waits for every one of those runs and passes on `{line name: what that run gave it}`.
  **At the same time** (shown for this mode: a whole number, 1 or more; 1 as the block is
  added) sets how many of those runs go at once; the next starts as soon as one ends, in the
  order the lines finished. Each run gets its own copy of what it receives.
- The Log says as each line arrives (`load_sales arrived: its run starts`), each Log line of a
  block run for a line carries that line (`[report] INFO: [line load_costs] …`), and the run
  history keeps every run's lines. While it runs, the Merger shows `done/N · k running` under
  it; after the run, each block after it shows how its runs ended (`✓ 2 ✕ 1`), and hovering
  it shows the last one's result, saying which line.
- A line that failed, or delivered nothing (`route has no run: it delivered nothing`), has
  no run after the Merger; a run that fails stops no other line's run. Once every line has
  been dealt with, the Merger fails if a line or a run failed, listing them (`load_costs
  failed: …; report failed for line load_stock: …`), through its error output, and what
  follows it does not run. After a fix, right-click the Merger > **Run from this block on,
  with the same inputs** (`taskloom run flow.yaml --from merge --inputs-from 12`) runs again
  only the runs that failed or did not run: the lines do not run again (their kept outputs
  are used), the other runs keep their results. On a block run once per line, Run only this
  block and Run from this block on are not offered yet: the menu says to right-click the
  Merger. **Run only this block** on the Merger runs it with the blocks it runs once per
  line (again only the runs that failed or did not run), not what comes after the Merger
  that closes them. Its setting **Runs again in** says in which order those runs go: *line
  order* (as it is added: top to bottom, where the lines reach the Merger on the canvas) or
  *the order the lines finished* in the run it carries on from. A run from the start always
  goes in the order the lines finish.
- Validate warns about a file path a block run once per line writes or copies to without a
  `{placeholder}` (each line's run would write over the one before), and refuses a block run
  once per line that takes from the end of one of the Merger's lines (connect it to the
  Merger's output instead). It works inside a Loop's box, and in the runs of another Merger
  set to each line as it finishes: the blocks after the inner one then run once per line of
  each.

### Loop: the blocks inside it, once per item

A **Loop** (`logic.loop`) is a box on the canvas around a set of blocks: the blocks inside it
run once for every item of the list arriving at its `items` input, as a small flow of their
own, in the flow's order (a Splitter's lines inside it run as anywhere). Add it from the
Blocks list (Logic) and drag blocks into its box; dragged out, they are no longer inside.
Moving the box moves its blocks; drag its bottom-right corner to resize it (never smaller
than its blocks). Deleting it keeps its blocks on the canvas; copying it copies them too. The
flow file says which blocks are inside with `inside:` and the Loop's name. For example:

```yaml
blocks:
  regions:
    type: logic.python
    config: {inputs: [], code: "output = ['north', 'south', 'east']"}
  each:
    type: logic.loop
    config: {at_the_same_time: 2}
  load:
    type: logic.python
    inside: each
    config: {inputs: [region], code: "output = region.upper()"}
  report:
    type: logic.python
    config: {inputs: [names], outputs: [], code: "print(names)"}
edges:
  - regions.output -> each.items
  - each.item -> load.region
  - load.output -> each.result
  - each.results -> report.names
```

- **items**: a list, or a table (one item per row, as a dict of its columns); anything else
  fails the Loop, saying it needs a list. An empty list runs nothing inside and gives `[]`.
- Inside the box, **item** gives each run its item and **index** its position, 1 to N:
  connect them to the blocks inside like any port. What reaches **result** is what the item
  gives back, and **results** is every item's result in the items' order (not the order they
  finished): above, `['NORTH', 'SOUTH', 'EAST']`. Its `done` fires once every item has run.
- **At the same time** (a whole number, 1 or more; 1 as the Loop is added) is how many items
  run at once: the next item starts as soon as one ends, in item order.
- Blocks outside may connect into blocks inside: the Loop starts once they are done, and each
  item gets its own copy of what they give. A block inside connects out of the Loop only
  through `result`: Validate refuses any other connection from inside to outside, naming it.
  A Loop may be inside another (Validate warns: its blocks run items × items times), and one
  of a Splitter's lines may be a Loop.
- While it runs, the box shows a progress bar and `done/N · k running`; the blocks inside
  show as running while any item runs them, and each of their Log lines carries the item,
  e.g. `[load] INFO: [item 2/3] …`. The Loop writes one Log line as it starts (`3 items, 2 at
  the same time`) and one as it ends. Afterwards each block inside shows how its items ended
  (`✓ 2 ✕ 1`), and hovering it shows the last item's result, saying which item it was. The
  run history keeps every item's Log lines.
- **A failed item**: an item fails when one of its blocks fails and no error output inside the
  Loop handles it. Then no new item starts, the items running finish, and the Loop fails,
  listing the failed items with their errors (`2 of 5 items failed: item 2 (load failed: …),
  item 4 (load failed: …); 1 not run`), through its error output; the blocks after its results
  are skipped, as after any failure. Stop cancels every running item at once.
- **Running again**: once you have fixed the cause, right-click the Loop > **Run from this
  block on, with the same inputs** (`taskloom run flow.yaml --from each --inputs-from 12`): it
  runs again only the items that failed or did not run, the others keeping their results from
  that run, then what follows the Loop (**Run only this block** on the Loop does the same,
  without what follows). Run from here and Run only this block are not offered
  on a block inside a Loop yet: right-click the Loop.
- Validate also warns when a block inside writes or copies to a path with no `{placeholder}`
  (every item would write the same file), and when the Loop's results is connected but its
  result is not.

## Checks

The **Checks** blocks check that what a flow is about to use is as expected, so a surprise
fails the flow clearly instead of flowing on. Each check is ticked to use it, and its **If it
fails** either fails the block (the default) or only warns in the Log. A `results` table has
a row per check (`check`, `outcome`: passed, failed or warned, and `details`), and the
block's error output carries `results` too.

### Check File: is the file there?

**Check File** (`checks.check_file`) checks a file, or the files matching a pattern in the
last part of its **Path** (`C:/inbox/sales_*.csv`, `~/exports/orders-*.parquet`), on this
computer (also network folders) or on the server of an SSH connection (**Path on**), as Copy
Files' paths are. A folder is not a file: it does not count as one found.

- **It is there**, always checked: at least one file is found. **If none is there**: fail the
  block (the default) or warn.
- **Not empty** (ticked in a new block): every file found has more than 0 bytes.
- **Modified since**: every file found was last changed at or after **Since**, an expression
  as an If's condition is, with the flow parameters: `today()` (the default),
  `now() - days(1)`, `today() - bdays(1)`. It is on the flow's clock (its time zone); a date
  is its midnight.
- **Size**: every file found is at least **Minimum** and/or at most **Maximum**, written with
  a unit: B, KB, MB or GB (1 KB = 1024 bytes, as Windows shows sizes), e.g. `1 KB` or
  `200 MB`. A size written another way is refused before the run (written with a
  `{placeholder}`, as the block runs).

With a pattern every check applies to each file found, and a failure names each file that
breaks it, with its size or time. The Log lists each file found with its size and when it was
last changed, then a line per check. Its outputs: `files`, the paths found (a list, as Copy
Files gives), and `results`.

To wait for a file that arrives late, set the block's **Retry** (its Retry and timeout box in
Properties): a failed check is checked again later, e.g. every 10 minutes, 6 times, before the
block fails. A check set to warn never retries.

```yaml
blocks:
  arrived:
    type: checks.check_file
    config:
      path: C:/inbox/sales.csv
      modified: true                # last changed today
      size: true
      minimum: 1 KB
    retry: {policy: fixed, delay: 10m, max_attempts: 6}   # waits up to 50 minutes
  load:
    type: data.read_file
    config: {path: C:/inbox/sales.csv}
edges:
  - arrived.files -> load.after
```

### Check Table: is the table as expected?

**Check Table** (`checks.check_table`) checks a table, e.g. the one a Read File just read, and
passes it on unchanged (its `table` output) only when no check set to fail the block failed.
DuckDB works each check out on the table's Parquet file, so the table is never read into
memory and tables of millions of rows are fine. A new block has **Row count** ticked, at
least 1 row.

- **Row count**: at least **At least** and/or at most **At most** rows.
- **Row count compared with the last successful run**: the rows differ by at most
  **Within %** (30 by default) from the rows this block passed on in the latest earlier run of
  the same flow in which it succeeded, as Run history shows (whatever that run's own status).
  With no such run it passes, saying there is nothing to compare with yet; its details name
  that run (`#12`) and its row count. Inside a Loop it is refused (Properties and Validate say
  so): a Loop's items have no single earlier row count.
- **No missing values**: in **In columns** (none listed: every column) a null is missing, and so
  is an empty text or one of only spaces with **Count empty text as missing** ticked.
  **Allowed**: at most a number of missing values (0 by default) or a percentage of the rows
  (`5%`), per column. Its details name each column over the limit, with its count.
- **Unique**: no value of **Key columns** repeats (several columns: together, e.g. `day, region`);
  a row missing one of them is left out. Its details say how many values repeat, with
  examples and their rows.
- **Columns and types**: each column of **Column types** (rows `Column: type`) is in the table, with
  its type: `text`, `int`, `float` (any number: an int column passes), `bool`, `date` or
  `datetime`, or no type for any. A missing column fails it, naming it; one stored with another
  type fails it, saying the type it has. A text column whose type is to be a number, a date
  or yes/no passes only if every value reads as one (missing values aside): numbers as Python
  and SQL write them (`1234.5`), dates `YYYY-MM-DD`, dates and times `YYYY-MM-DD HH:MM[:SS]`,
  yes/no `true`, `false`, `yes`, `no`, `1` or `0`. Otherwise it lists the values that do not,
  with how many rows have each, e.g. `'N/A' (12 rows, the first row 2), '1,234.5' (3 rows,
  the first row 3)`: at most 5 of them, saying how many more.
- **Values within a range or a list**: rows `Column=min..max` (an end may be left empty:
  `0..`, `..100`) or `Column=a, b, c`. Missing values are not counted here (No missing values
  counts them). A range on a number or date column compares numbers or dates; on a text
  column a range of numbers or dates reads its values as numbers or dates, and a value that
  does not read is not within it.
- **Freshness**: the latest value of **Date column** (a date or datetime column, or a text
  column of `YYYY-MM-DD` dates, read as dates) is at least **Latest at least**, an expression
  as an If's condition is, with the flow parameters: `today() - days(1)` (the default),
  `today() - bdays(1)`, `now() - days(2)`. Its details say the latest value found and the one
  expected.
- **Conditions every row must meet**: SQL conditions as DuckDB writes them, one per line (e.g.
  `amount >= 0` and `end_date >= start_date`), each checked on its own. A row where one is not
  true breaks it: false, or unknown because of a missing value (write
  `amount is null or amount >= 0` to allow missing ones). A condition that is not valid SQL,
  or names a column the table does not have, fails the block, naming it.

Each check's **If it fails**: fail the block (the default) or warn (a WARNING line in the Log;
the block succeeds and the table goes on). Every ticked check runs, also after one failed. A
failing check's details say how many rows break it and show up to 5 example rows: their row
numbers (from 1, in the table's order) and the values of the columns concerned. When checks
set to fail the block fail, the block fails with a message naming each of them with its
details. A check that cannot be checked as it is set up (a column not in the table, a
condition that is not valid SQL, a range end that is not a number or a date) fails the block
also when it is set to warn, as nothing was checked. The Log has a line per check: passed,
failed or warned, with its details. `results` has a row per ticked check: `check`, `outcome`,
`rows` (how many rows break it; empty for Row count, the comparison and Freshness) and
`details`; the error output carries it too.

```yaml
blocks:
  load:
    type: data.read_file
    config: {path: data/orders.csv}
  check:
    type: checks.check_table
    config:
      row_count: true
      at_least: 1
      compared: true                # within 30% of the rows of the last successful run
      missing: true
      missing_columns: [order_id, amount]
      unique: true
      unique_columns: [order_id]
      types: true
      types_columns:
        - {key: amount, value: float}
        - {key: day, value: date}
      values: true
      allowed_values:
        - {key: region, value: "north, south, east, west"}
      values_fails: warn            # only a warning in the Log
      conditions: true
      rules: [amount >= 0]
  totals:
    type: data.duckdb_sql
    config:
      inputs: [orders]
      sql: select region, sum(amount) as total from orders group by region
edges:
  - load.table -> check.table
  - check.table -> totals.orders
```

## Statistics

**Statistics** (`data.statistics`, in the Data group) shows the most useful statistics of a
table, as numbers and as charts. DuckDB works them out on the table's Parquet file, so the
table is never held in memory and millions of rows are fine. Missing values are counted,
never dropped: each statistic is over the values present, with the count of the missing ones
next to it.

- Tick the groups of statistics you want (all ticked in a new block); an unticked group's
  statistics and charts are left out:
  - **Overview**: the table's rows and columns, each column's type and missing values, the
    missing cells and the fully repeated rows (each the same as an earlier row in every column).
  - **Numbers**: mean, std (the sample standard deviation), min, 25%, median, 75%, max, sum,
    zeros and negatives; a histogram of each number column.
  - **Text**: the shortest and longest length and the 5 most common values with their counts;
    a chart of the 10 most common values.
  - **Dates** (dates, and dates with a time): the first and the last; a chart of the rows per
    day (per month or year over a longer span).
  - **Correlations**: Pearson's r between each two number columns, over the rows where both
    have a value; a heatmap of them with 2 or more number columns.
- **Columns**: the columns to describe and chart (none listed: every column but the Group by
  ones). **Group by**: optional columns to work the statistics out per group of: the report
  has a section per group, `stats` a row per group and column (the group columns first) and
  there is a chart of the rows per group. Rows whose group value is missing are a group too. A
  column that is not in the table fails the block, naming it, and so does a Group by column
  named as a column of `stats` (`column`, `type`, `count`, ...): rename it before the block.
  A number column holding NaN or an infinite value (e.g. from a division by 0) has no mean,
  spread or order: it fails the block, naming the column and how many it holds; make them
  missing or drop them before the block, e.g. in a DuckDB SQL block.
- **Custom statistics**: rows of a name = an SQL aggregate over the table, worked out by
  DuckDB (per group with Group by), e.g. `p95 = quantile_cont(amount, 0.95)` or
  `north_share = avg(case when region = 'north' then 1 else 0 end)`. One that is not valid
  SQL, or no aggregate (`amount * 2`), fails the block, naming it. For a statistic SQL does
  not say easily, work it out in a **Python Code** block and connect its output to the
  optional `extra` input: a dict (each key and value shown as a row) or a table, shown in the
  report under its own heading as it is.
- **Exact** (ticked by default): every statistic is exact. Unticked, distinct counts (also the
  overview's fully repeated rows) and percentiles are estimated, faster and with less memory
  on very big tables: the report shows each estimated value with ≈, and `stats` says in its
  `estimated` column which statistics of the row were.

The Log shows the report as Markdown: the overview, a section per ticked group with its numbers
as tables (per group with Group by), the custom statistics and the extra input, then each chart
as a picture. A column left without a chart is named, with why (only numbers, text and dates
get one, and a column with no values gets none). The charts are drawn on a light background,
also in the Dark theme: they are for emails too.

Its outputs: `stats`, a table with a row per column: its name and type, then the statistics as
columns (`count`, `missing`, `missing %`, `distinct`, `mean`, `std`, `min`, `25%`, `median`,
`75%`, `max`, `sum`, `zeros`, `negatives`, `shortest`, `longest`, `most common`, `first`,
`last`; min and max readable for every type, e.g. a duration as `48:00:00`), shown in Data
preview after a run (also after Run only this block); `charts`, the charts' PNG files, which
an HTML Template and Send Email show as pictures; and `custom`, a table of the custom
statistics (one row, or a row per group).

```yaml
blocks:
  load:
    type: data.read_file
    config: {path: data/sales.csv}
  summary:
    type: data.statistics
    config:
      group_by: [region]
      custom:
        - {key: p95, value: "quantile_cont(amount, 0.95)"}
        - {key: big_orders, value: "count_if(amount > 1000)"}
  body:
    type: reports.html_template
    config:
      names: {summary.stats: stats, summary.charts: charts}
      template: "<h2>Sales statistics</h2>{stats}<br>{charts}"
  mail:
    type: email.send
    config: {connection: mailer, to: [team@example.com], subject: Sales statistics}
edges:
  - load.table -> summary.table
  - summary.stats -> body.data
  - summary.charts -> body.data
  - body.html -> mail.body
```

## Detect Anomalies

**Detect Anomalies** (`data.detect_anomalies`, in the Data group) answers "is today's number
unusual?" for the numbers flows already produce: daily row counts, job durations, file sizes,
error counts. It marks the values of a table of numbers over time that are unusual for their
weekday or hour of day, each with a reason, e.g. `Mon 2026-11-02, server-a: rows 120,431; a
usual Monday is 30,000 (26,664.15–33,335.85)`.

What is usual for a point comes from the comparable earlier points of its series: by default
the same weekday over the last 8 weeks. Their median is the expected value, and their typical
spread is 1.4826 × their median absolute deviation from it (or, when that is 0, 1.2533 × their
mean absolute deviation from it): both equal the standard deviation for normal data, but one
past spike does not skew them as it would a mean. A value more than 4, 3 or 2 spreads from the
expected value (**Sensitivity** low, medium or high) is unusual. When the earlier values were
all the same, any other value is unusual, and its reason says they were all that value.

- **Date column**: a date or date and time column, or a text column of `YYYY-MM-DD` or
  `YYYY-MM-DD HH:MM[:SS]` values, read as such.
- **Number columns**: one or more, each checked on its own. A text column whose values all
  read as numbers is read as numbers.
- **Group by** (optional): one series per group, e.g. per server, each judged against its own
  history.
- **Pattern**: weekday (the default) compares a point with the earlier points of its series
  on the same day of the week; hour of day, with those at the same hour of the day (the dates
  need times); none, with all of them. Earlier means at an earlier date (day, for daily
  times): rows of the same date are never each other's history.
- **Compare with the last**: how many of the latest comparable earlier points make what is
  usual (8 by default, at least 4).
- **Sensitivity**: low, medium (the default) or high: 4, 3 or 2 spreads.
- **Only the latest** (ticked by default): only the points at the table's latest date are
  judged; the other rows get their expected value and range, for the chart, but an empty
  anomaly and reason. Unticked, every point with enough history is judged.

Properties and Validate refuse a block without a Date column or Number columns, or with
Compare with the last under 4. Nothing is guessed silently:

- A point with fewer than 4 comparable earlier points is not flagged: its reason says `not
  enough history: 2 earlier Mondays, 4 needed`, and the Log warns once per series, saying how
  many points. A missing number is not judged nor used as history (its reason: `missing
  value`), and the Log says how many there are.
- The Log warns of repeated dates in a series (each such row is still judged on its own; if
  one value per date is meant, add them up first, e.g. in a DuckDB SQL block), of gaps (dates
  missing at the series' usual step, but for the days of the week, or hours of the day, on
  which it never has a row, such as the weekends of a weekdays-only series) and, with Only the
  latest, of a series with no row at the table's latest date (it is not judged). A series'
  dates are taken in whole days (whole hours, minutes or seconds for dates mostly less than a
  day apart) for these and for what is earlier, so two runs on the same day are a repeated
  date, and run times that vary by some seconds or minutes are neither gaps nor behind; the
  gaps of dates a month or more apart, or without a regular step (one shared by at least half
  of the differences between consecutive dates), are not checked, and the Log says so.
- A column that is not in the table, a row without a date, or a text value of the Date column
  or a Number column that does not read fails the block, listing up to 5 such values with how
  many rows have each.

Its outputs: `table`, the input's rows and columns in the same order, plus for each number
column `<column>_expected`, `<column>_low` and `<column>_high`, then `anomaly` (true, false,
or empty when the row was not judged) and `reason`, shown in Data preview after a run (also
after Run only this block); `anomalies`, only the rows whose anomaly is true; `count`, their
number, for an If such as `value > 0`; and `charts`, a chart of each series (up to 12; the Log
names any left without one): its values as a line over the dates, the usual range shaded and
the unusual points marked in red, on a light background (also in the Dark theme: they are for
emails too). The Log shows how many rows were judged and how many are unusual, per series,
the reasons of the unusual points as a table, then each chart. It reads only the columns it
needs, and a series of 100,000 points takes a few seconds.

A Run Query of daily row counts per server, Detect Anomalies by weekday per server, then an
email with the anomalies and their charts only when there are some:

```yaml
blocks:
  counts:
    type: db.run_query
    config:
      connection: warehouse
      sql: select load_date as day, server, count(*) as row_count from loads group by load_date, server
  detect:
    type: data.detect_anomalies
    config: {date_column: day, number_columns: [row_count], group_by: [server], pattern: weekday}
  any:
    type: logic.if
    config: {condition: "value > 0"}
  body:
    type: reports.html_template
    config:
      names: {detect.anomalies: anomalies, detect.charts: charts}
      template: "<h2>Unusual row counts</h2>{anomalies}<br>{charts}"
  mail:
    type: email.send
    config: {connection: mailer, to: [team@example.com], subject: Unusual row counts}
edges:
  - counts.result -> detect.table
  - detect.count -> any.value
  - any.true -> body.after
  - detect.anomalies -> body.data
  - detect.charts -> body.data
  - body.html -> mail.body
```

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
  turn. A comment, `<!-- ... -->`, is a note for you: it is left out of the email, and a
  `{name}` in it shows nothing; Outlook's conditional comments (`<!--[if mso]> ...
  <![endif]-->`) and `<style>` elements are kept as written. A name the Template does not
  know, or a brace left alone, is shown in red and stops
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
off the scheduler service** on its tray icon; see **Turning the service off** below): then
nothing new starts, and it does not start by itself (also not at login), until you turn it on
again. While it is off, a Schedule block says **scheduler service off** instead of its
countdown. On Windows, **Tools → Settings → Start the scheduler service when I log in** (or
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
service was off, the computer asleep, ...). `misfire` (**Misfire** on the Schedule block)
says what runs:

- `run_once` (the default): one run, for the most recent time, on time or missed;
- `skip`: only the on-time run, if there is one; one **Missed runs skipped** notification
  says how many were missed;
- `catch_up`: a run for every missed time, oldest first, one at a time.

A run's parameters are computed for the time it was due (that is what `now()` means).

The rest of this section is about the cases where the scheduler might surprise you.

**More than 100 missed.** When a `catch_up` schedule has missed more than 100 times, the
scheduler service asks you first. A **Catch up missed runs?** window says how many times were
missed: the times already past when the service found them. Choose **Run all of them**, or
**Run only the latest** with a number (100 unless you change it), and click **Proceed**. With
only the latest, the older times are not run: they count as missed, and a **Missed runs
skipped** notification says how many (plus an email, if the flow's notifications include
skipped runs). The window shows even when the editor is closed. Until you answer, that
schedule's missed runs wait, and so do its times that are not missed (the one on time when
the window opened, and those that come while it is open); other flows run as usual. Those
times are never dropped or counted as missed: they run after the missed runs you chose,
oldest first, one at a time. If the service stops before you answer, it asks again when it
is back. On a server, with no screen to ask on (`taskloom scheduler --headless`), the latest
100 missed times run, oldest first, then the times that are not missed. The service's log
says how many were missed and how many of them are not run (the log is what the service
prints: `scheduler.log` in its Taskloom folder when your PC's keeper started it), and so does
an email if the flow's notifications include skipped runs.

**Overlaps.** If a flow's time comes while its previous run is still going, its `overlap`
decides what happens (in the editor: Flow settings, **If a scheduled run starts while the
last one is still going**). `skip`, the default, does not start the new run, and a **Run
skipped** notification tells you. `queue` makes the new run wait; it starts once the run
going has finished. `allow` runs both at the same time. A catch-up is the exception: the
missed runs of a `catch_up` schedule always go one at a time, even with `allow`.

**Runs at once.** **Scheduled runs at once** in Tools → Settings (`max_concurrent_runs` in
`settings.yaml`) limits how many scheduled runs go at the same time; unlimited by default.
When that many are going, the next runs wait until one ends, and they start in the order
they came: a flow whose run has just finished does not jump the line.

**Turning the service off.** If no runs are going, you are only asked to confirm. If some
are, a **Turn off the scheduler service?** window lists each flow with runs going, and you
choose for each one. **Let it finish** (the default): its runs going finish, and its runs
waiting their turn still run. **Stop now**: its runs going stop and are recorded as
cancelled, and its waiting runs are dropped (a run that does not stop within a few seconds
is forced to end, and is recorded as cancelled all the same). Once you click **Proceed**,
nothing new starts, and the service turns off when the last run you let finish is done. A
run whose program the service started early, to begin on time, has not begun yet: it is not
listed, and it does not begin. **Cancel** keeps it on and changes nothing. Flows not in the
list keep their waiting runs: they are saved, and start when you turn the service on again.
The tray icon's **Turn off the scheduler service** shows the same window. From **Proceed**,
the editor shows the service off, also while the runs you let finish go; click the button to
turn it on again before they are done, and it stays on: they go on, and scheduled times run
again.

**Waiting runs are saved.** Runs waiting their turn are saved in the run history: behind
their flow's run going, until fewer runs go than **Scheduled runs at once** allows, in a
catch-up, or for your answer about missed times. However the service stops (the PC restarts,
the service crashes or is ended), they are not lost: when it is back, each starts once, in
the order they came. If only the service stops, the runs it started go on by themselves
(each run is a program of its own), and they count as going once it is back. After a PC
restart, a run that was going has ended, and its program's number may be another program's
by then: the service counts a run as going only if its program began before the run did, so
it never waits for, or stops, another program. A run whose program has gone is recorded as
crashed, and you are told (see below).

**A run that crashes.** If a run's program stops unexpectedly (it crashed, or something
ended it), the run is recorded as crashed: it counts as failed, and its error says "crashed:
the run process stopped unexpectedly". You get a desktop message (for a flow named nightly:
**nightly crashed**), and an email if the flow's notifications include failures; also for a
run that was going when the service started again.

**Clock changes.** Twice a year, daylight saving time (summer time) moves the clock. The
service counts in real time, so: an **Every N minutes or hours** schedule keeps its rhythm
(every 5 minutes stays 5 real minutes apart while the clock jumps); a time the clock skips
(02:30, the night 02:00 jumps to 03:00) runs right after the jump; and a time the clock
shows twice (the night it goes back) runs once, the first time. The jump forward does not
count as time the service was off: no run is called missed because of it.

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

### API Request: a call to a web API

**API Request** (`servers.api_request`) calls a web API as Postman's Send does (with Python's own
HTTP library: nothing else to install). Its settings come in groups in the order of Postman's
tabs, a line above each group in Properties:

- **Method** (GET unless you choose POST, PUT, PATCH, DELETE, HEAD or OPTIONS) and **URL**,
  with its `?query` if it has one.
- **Params**: a table of names and values, each with a tick; the ticked rows are added to the
  URL's query in their order, encoded as a URL needs. Typing a query into the URL fills Params,
  and changing Params changes the URL Properties shows. Add a row by typing `name=value` in the
  line under the table; empty a name to remove its row. The flow file keeps the URL without its
  query and each row as `{key: page, value: "2"}`, with `on: false` for a row not ticked.
- **Auth**: none, **Basic** (a user name and password, sent with the request), a **Bearer
  token**, an **API key** in a header or in the query (its **Key name** is the header's or the
  parameter's), or **Digest** (in answer to the server's challenge). The password, token or key
  is a stored secret: **Stored secret (name)** names it (store it with `taskloom secret set
  orders_token`); its value is never in the flow file, the Log or the run history. A run whose
  secret is not stored fails before it sends anything, saying how to store it. Auth's header
  replaces one of the same name in Headers.
- **Headers**: a table as Params (add a row as `Name: value`). A value `secret:NAME` sends the
  stored secret NAME, e.g. `X-Api-Key: secret:orders_key`: the key itself is never in the flow.
- **Body**: none, **JSON**, **text** or **XML** (written in **Content**; JSON is checked before it
  is sent), a **form (url-encoded)** (its **Form** table), **multipart form-data** (its
  **Parts**: a part ticked **File** sends the file its value names) or a **file** sent as it is.
  Each sets its Content-Type, unless Headers has one. Placeholders work in Content as in every
  setting, so the braces of JSON are written `{{` and `}}`: `{{"region": "{region}"}}`.
- **Response as**: **automatic** (JSON read into dicts and lists when the server says it is
  JSON, text otherwise, as its charset says), **text**, **table** (the answer must be a JSON
  list of records, an object per row; anything else fails it, saying so) or **file** (saved
  as it came to **Save to**; the output is that path).
- **Statuses that count as success**: `200-299` unless you write others, e.g. `200-299, 404`.
  One that is not a status nor a range of them is refused before the run starts.
- **Follow redirects** (ticked unless you untick it): unticked, the redirect is the answer. A
  redirect to another host (or scheme or port) is followed without the Authorization header
  and the headers that carry a stored secret (an API key, a header written `secret:NAME`), as
  curl and Postman do.
- **Certificates** are checked against the system's store; **CA certificate file** adds an
  authority to trust (e.g. your company's), and **Don't verify certificates** checks none, which
  the Log warns about on every run. Requests go through the proxy `HTTPS_PROXY` or `HTTP_PROXY`
  names (but to the hosts `NO_PROXY` lists), or else through the system's proxy settings.
- **Retry and timeout** as for every block, e.g. a Timeout of `30s` for a server that does not
  answer.

`{name}` placeholders work in the URL, Params, Headers and Content. Its outputs are `response`
(as Response as says), `status` (a number), `headers` (a dict; a `Set-Cookie` header shows as
`***` in the Log, the results and Data preview, as a stored secret does, while the blocks after it
get its value) and `done`. Its `body` input, when
connected, is what it sends instead of its Body (which is then none; Properties says so): a dict
or list as JSON, a table as JSON records (an object per row), text as it is.

A status that does not count as success fails the block, and its `error` output carries `error`,
`status`, `headers` (its `Set-Cookie` as `***`, as the error output's values hide every secret)
and `body` (the answer's text) for the blocks that handle it; so do a server
that cannot be reached (naming it, e.g. `could not reach api.example.com:443: …`), a Timeout and
an answer that says it is JSON but is not. The Log says what was sent and what came back, never
a secret: `GET https://api.example.com/v1/orders?status=open`, then `200 OK: 5120 bytes in 84 ms`.

**Run only this block** (right-click it) is Postman's Send: its response shows in Data preview.
**Copy as cURL** (right-click it) copies the request as a curl command (as bash reads it), its
placeholders filled in with the flow's parameters and each stored secret shown as `<hidden:
secret NAME>`: put the secret there to run it in a terminal.

**Import from cURL**, at the top of its Properties, does the reverse: paste a curl command into
its box (coloured as code: options, quoted strings, the URL), as an API's documentation or a
browser's Copy as cURL writes it, for bash (`\` at a line's end, `'…'`, `"…"`, `$'…'`) or for
Windows cmd (`^` at a line's end, `^"`, `\"`), and click **Fill in**. It fills the request:

- the Method from `-X`/`--request` (else POST when it sends data, HEAD with `-I`/`--head`, GET);
  the URL (also `--url`), its query into Params;
- Headers from `-H`/`--header`, a Cookie header from `-b`/`--cookie`, User-Agent and Referer from
  `-A`/`--user-agent` and `-e`/`--referer`;
- the Body from `-d`/`--data`/`--data-raw`/`--data-binary`/`--json`: JSON (shown pretty-printed)
  when it is JSON, else text (with curl's `Content-Type: application/x-www-form-urlencoded` unless
  a header gives one), or a file with `@path`; `--json` also adds its two JSON headers.
  `--data-urlencode` makes a url-encoded form, `-F`/`--form` multipart Parts (`name=@path`: a
  file); with `-G`/`--get` the data goes into Params instead;
- Auth from `-u`/`--user` (Basic) or an `Authorization: Bearer` header (Bearer token);
- Don't verify certificates from `-k`/`--insecure`, Follow redirects from `-L`/`--location`, the
  Timeout from `-m`/`--max-time` (in seconds); `--compressed` needs nothing.

The secrets it recognises never go into the flow: for a `-u` password, a Bearer token and each
header named like a secret (Authorization, or with token, key, secret or password in its name,
e.g. `X-API-Key`), Fill in asks the name of a stored secret, suggesting one, and stores the value
there; the block uses it by that name (a header as `secret:NAME`). Anything else in the command,
such as a key in the URL's query or in the data, is filled in as written. When the block's request is
filled in already, a box says what Fill in replaces and asks: **Approve**, or **Cancel**, which
changes nothing. The request is replaced as a whole, Connection included (what the command does
not give is left empty; Follow redirects, Don't verify certificates and the Timeout change only
with `-L`, `-k` and `-m`), in one step for Undo. Then it says what it filled and lists what it could not use (an
option it does not read, a second URL); text that is not a curl command is refused, saying so.
Braces are written `{{` and `}}`, so they are sent as they are.

**Connection**: none (everything is set in the block), or an API connection (Tools →
Connections, New…, Kind Web API): its **Base URL**, **Auth** with its stored secret, the headers
every request sends and its certificate settings. With one chosen, Properties shows what it gives
(change it in the Connections window); the block's URL is then whole, or relative to the base URL
(with `https://api.example.com/v1`, `/orders/{id}` calls `https://api.example.com/v1/orders/7`), and
the block's own Headers are added to the connection's, the block's winning on the same name
(Properties says which of the connection's it replaces). For example, today's open orders saved as
a file, then a report of them sent:

```yaml
# orders-report.yaml
params:
  day: {type: date, expr: "today()"}
blocks:
  start: {type: trigger.manual}
  orders:
    type: servers.api_request
    config:
      connection: shop
      url: /orders
      params: [{key: status, value: open}, {key: day, value: "{day:%Y-%m-%d}"}]
      response_as: table
  save:
    type: data.write_file
    config: {path: "out/orders_{day:%Y-%m-%d}.csv"}
  report:
    type: servers.api_request
    config:
      method: POST
      url: https://reports.example.com/api/uploads
      auth: Bearer token
      secret: reports_token
      body: JSON
      content: '{{"day": "{day:%Y-%m-%d}", "file": "orders_{day:%Y-%m-%d}.csv"}}'
edges:
  - start.start -> orders.after
  - orders.response -> save.table
  - save.file -> report.after
```

```yaml
# in connections.yaml
shop:
  kind: api
  base_url: https://api.example.com/v1
  auth: Bearer token
  secret: "secret:shop_token"
  headers: [{key: Accept, value: application/json}]
```

### Session: API Request blocks that share cookies

A **Session** (`servers.api_session`) is a box on the canvas, drawn as a Loop's, around API
Request blocks: like a `requests.Session`, the API Request blocks inside it share one set of
cookies during a run. The cookies a response sets (a login's, say) go with the requests after
it inside the Session, by the domain and path rules of cookies: a cookie set for `/account`
goes with the calls to `/account/...` on that host, not with other paths nor other hosts, and
one a response sets for another domain is not taken. API Request blocks outside any Session
keep no cookies from one block to the next. Add it from the Blocks list (Servers) and drag
blocks into its box, as for a Loop; the flow file says which blocks are inside with
`inside:` and the Session's name. Nothing is connected to the Session itself: the blocks
inside are connected to each other and to blocks outside as anywhere, and other blocks may be
inside too (they run as usual). For example, a login, then a call that needs its cookie:

```yaml
blocks:
  start: {type: trigger.manual}
  shop: {type: servers.api_session}
  login:
    type: servers.api_request
    inside: shop
    config: {method: POST, url: "https://api.example.com/login"}
  orders:
    type: servers.api_request
    inside: shop
    config: {url: "https://api.example.com/orders"}
edges:
  - start.start -> login.after
  - login.status -> orders.after
```

- After each request inside it, the Log lists the cookies the session holds, by name and
  domain, never their values: `session shop's cookies: sessionid (api.example.com)`. The run
  history keeps those lines.
- Every run starts its session with no cookies: they are not kept from one run to the next.
  A Session inside a Loop gives each item a session of its own (the items of a Loop inside a
  Session share its cookies). A Splitter's lines inside a Session share its cookies, also when
  they run at the same time.
- **Running again**: Run only this block and Run from this block on, on an API Request inside
  a Session (`taskloom run flow.yaml --only orders --inputs-from 12`), start with the cookies
  the session held just before that block in the run they carry on from, kept with that run's
  results (as its outputs are, in the Taskloom folder's runs, never in a file next to the
  flow): a later call runs again without logging in again, and the Log says the cookies come
  from run #12. If the server has ended the session since, that call fails with what the
  server answered (e.g. 401): run the flow again from the login.
- A Session shares cookies only: the base URL and the authentication belong to an API
  connection.

## Checking flows after an update

Each version can check your flows for what it can fix and what needs you: **Tools → Check
flows…** (the open and scheduled flows; it also runs by itself the first time a new
version starts), or `taskloom check [FLOW…]`. It fixes an older flow format, a block's
settings saved by an older version of that block, settings a block no longer has,
connections to blocks, inputs or outputs that no longer exist (e.g. after renaming an
output) and connections listed twice, and adds a Manual Start (connected to its first
blocks) to a flow without a start block. **Back up and fix** (or `--fix`) first copies each
flow into `backups/` in your Taskloom folder. Anything else it finds is listed for you to
fix; a problem of an off block is listed as a warning, which runs do not stop for.

## Your files

Everything per-user lives in `~/.taskloom` (or `$TASKLOOM_HOME`):

| File | Purpose |
|---|---|
| `connections.yaml` | Connections: databases (a JDBC driver, URL, jar and properties), servers (SSH), mail (SMTP), web APIs |
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
full list of components. Most of the blocks' icons are from Tabler Icons (MIT); its licence text
is next to them, in `taskloom/editor/icons`, and in the packaged editor's `licenses` folder.
