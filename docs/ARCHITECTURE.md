# Architecture charter

The rules every change to Taskloom keeps. Approved on 2026-11-07.

- **[CI]** means a check fails the build: `tests/test_charter.py`.
- **[Review]** means a question the independent reviewer must answer yes to.
- A change that needs a different rule first gets a short decision record and the maintainer's approval.

## Layers

**C1. Code may use its own layer and the layers below it, never the ones above. [CI]**

| Layer | Modules |
| --- | --- |
| 1. Entry points | the command line, the editor, the scheduler service, the connection check |
| 2. Blocks | `taskloom/blocks/` |
| 3. Core | reading, checking and upgrading flows; running them; the block registry; the Python environment |
| 4. Outside world | settings files, run history, databases, servers, mail |
| 5. Foundations | tables, expressions, parameters, calendars, schedules, reports, how Taskloom starts its own programs |

Foundations never call a database, a server, a mail server or the web.

**C2. Only the editor and the scheduler service's tray icon use the window toolkit (Qt). [CI]**
- Everything a run on a server needs works without it.
- The Linux runner is built without Qt, and its smoke test runs a flow.

## Where state lives

**C3. Flows: their files and the Git history in their folder. [Review]**

**C4. Settings, connections and the list of scheduled flows: files in the Taskloom folder. [Review]**

**C5. Run history, results and schedule state: one database in the Taskloom folder, opened only by the history code. [CI]**

**C6. Secrets: the system's credential store; on a server without one, a file only you can read. [CI]**
- Never in flows, history, logs or emails.

**C7. Nothing you'd miss after a restart lives only in memory. [Review]**
- The one exception is unsaved edits in the editor, where saving is your explicit choice.

## Outside calls and errors

**C8. Every call outside Taskloom has a time limit: databases, servers, mail, the web, other programs. [CI where the code shows it, Review otherwise]**
- A database's time limits come from its connection's properties.
- A run's own programs are bounded by their block's Timeout.

**C9. A failure becomes the block's failure, with a plain message naming what failed. [Review]**

**C10. Your mistakes and outside failures never stop the editor or the scheduler service. [Review]**
- They tell you, and carry on.

## Consistency

**C11. Time: clock schedules and everything on screen use your PC's local time. [Review, plus clock tests]**
- Intervals, waits and time limits use real elapsed time, so clock changes don't stretch or shrink them.

**C12. Plain words on screen [Review]. Every block has a description [CI].**

**C13. No backwards-compatibility code until there are flows in production. [Review]**

## Design health

**C14. No worse than the last release. [CI, plus Review of kept functions]**
- A function with more than 25 decision points, or longer than 80 lines, is a warning sign:
  - If splitting it makes the code clearer, split it.
  - If splitting would make it worse, keep it, with a one-line reason the reviewer accepts.
- A decision point is each `if`, loop, error handler, `and`, `or`.
- Kept functions are expected to be few, if any.
- The functions over a limit in the last release are listed in `docs/design_health.yaml`.
- A function newly over a limit must be split or listed there with its reason.
- `packaging/design_health.py` counts them.
