"""Credentials on the PC (docs/promises/credentials.yaml, CR-5): what Python Code's print and the hiding of what
`taskloom` prints leave as it was while they look for values to show as ***. Python Code's print into a file the code
opened writes what Python's print writes there; what it prints to the Log shows there exactly as printed (in the
terminal of `taskloom run`, the run history's Log and the editor's Log pane), also when a thread the code started
prints after the code has run; and a value of several lines inside an error line that `taskloom run` prints once the
run is over (outside it: nothing below print hides it then) shows as ***. Through the real `taskloom` program (python
-m taskloom.cli, as a terminal starts it) and the editor (offscreen), against the tests' own credential store
(tests/credential_store, never the real one). Made-up values only.

What the tests take as given (beside tests/test_credentials_review.py's):
- print("a", "b", sep=",", end="!\\n", file=f) into a file f the code opened writes there what Python's print
  writes: its sep between the values and its end after them.
- A line printed to the Log is one Log line holding the printed text as written: its spaces and tabs at the end
  kept, a line break at its start an empty first line; a line break at its end makes no empty line after it.
- A printed line that may begin a value of several lines the code read (its first line) waits for the next, at the
  latest until the code has run; then it shows as written.
- A Log line printed by `taskloom run` reads `HH:MM:SS [block] LEVEL: message`; in the editor's Log pane
  `HH:MM:SS [block] LEVEL message`.
- `taskloom run --only BLOCK --input NAME=TEXT` reads TEXT as a Python expression; one that cannot be read refuses the
  run with an error line that repeats TEXT.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from test_credentials_editor import flow_file, python, run_in_editor  # noqa: E402
from test_credentials_refs import write_flow  # noqa: E402
from test_credentials_review import KEY, KEY_LINES  # noqa: E402
from test_credentials_sinks import code, terminal  # noqa: E402
from test_credentials_store import plant, refused_cleanly, run, taskloom, told, write_cards  # noqa: E402


def log(env, run_id: int, block: str) -> list[str]:
    """The block's Log in the run history, a message each, in order."""
    return [m for (m,) in env.query("SELECT message FROM logs WHERE run_id=? AND block=? ORDER BY rowid", run_id,
                                    block)]


def statuses(env, run_id: int) -> dict:
    return dict(env.query("SELECT block, status FROM blocks WHERE run_id=?", run_id))


# --- print into a file the code opened -----------------------------------------------------------------------------

INTO_A_FILE = '''\
with open("printed.txt", "w", encoding="utf-8") as f:
    print("a", "b", sep=",", end="!\\n", file=f)
    print("rows", 42, sep="=", end="", file=f)
    print(" and more", file=f)
print("written")
'''


@pytest.mark.invariant("CR-5")
def test_print_into_a_file_the_code_opened_writes_its_sep_and_end_as_pythons_print_does(env):
    """print with sep and end into a file the code opened: the file holds what Python's print writes, the Log none of
    it."""
    result = run(env, write_flow(env, {"talk": code(INTO_A_FILE)}))
    run_id, status = env.last_run()

    assert status == "success", told(env, result)
    assert (env.root / "printed.txt").read_text(encoding="utf-8") == "a,b!\nrows=42 and more\n"
    assert log(env, run_id, "talk") == ["written"], told(env, result)


# --- what print shows in the Log ---------------------------------------------------------------------------------

PRINTS = '''\
print("TAX")
print("region\\tamount\\t")
print("EU    ")
print("\\nTotals")
print("done\\n")
print("end")
'''
# What the Log holds of each print: the printed text as written, a line break at its end making no empty line.
LOGGED = ["TAX", "region\tamount\t", "EU    ", "\nTotals", "done", "end"]


def printed_lines(text: str, prefix: str) -> list[str]:
    """The lines of `text` from the Log line of "TAX" to that of "end", each without `prefix` and what is before it
    (the time): the printed text as the Log shows it, a line at a time."""
    lines = text.splitlines()
    first = next(i for i, line in enumerate(lines) if line.endswith(prefix + "TAX"))
    last = next(i for i, line in enumerate(lines) if line.endswith(prefix + "end"))
    return [line.split(prefix, 1)[1] if prefix in line else line for line in lines[first:last + 1]]


@pytest.mark.invariant("CR-5")
def test_what_print_shows_in_the_log_and_the_terminal_is_the_printed_text_exactly(env):
    """A text ending in an X, a tab or spaces, one starting or ending with a line break: `taskloom run` prints each
    as written, a line break at its start an empty first line and one at its end no empty line after it, and the run
    history's Log holds each as printed."""
    result = run(env, write_flow(env, {"say": code(PRINTS)}))
    run_id, status = env.last_run()

    assert status == "success", told(env, result)
    assert log(env, run_id, "say") == LOGGED
    assert printed_lines(result.stderr, "[say] INFO: ") == [
        "TAX", "region\tamount\t", "EU    ", "", "Totals", "done", "end"], result.stderr


@pytest.mark.invariant("CR-5")
def test_the_log_pane_shows_what_print_printed_exactly(win, qtbot, tmp_path):
    """The same prints in a run from the editor: its Log pane shows each as written, the tab and spaces at the end
    kept and a line break at the start an empty first line."""
    tab = win.open_flow(flow_file(tmp_path / "flows" / "prints.yaml", {"say": python(PRINTS)}))

    run_in_editor(win, qtbot, tab)

    text = win.log.text.toPlainText()
    assert printed_lines(text, "[say] INFO ") == ["TAX", "region\tamount\t", "EU    ", "", "Totals", "done", "end"], \
        text[-3000:]


# --- a thread that prints after the code has run --------------------------------------------------------------------

LATE = '''\
import os, threading, time

def later():  # once the next block has begun (it makes go.flag), print, then say so (done.flag)
    deadline = time.monotonic() + 20
    while not os.path.exists("go.flag") and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        print("late")
    finally:
        open("done.flag", "w").close()

threading.Thread(target=later, daemon=True).start()
print("started")
print(secret("PEM.key").splitlines()[0])  # (the key's first line, which may begin it: it waits for the code's end)
'''
NEXT = '''\
import os, time
open("go.flag", "w").close()
deadline = time.monotonic() + 20
while not os.path.exists("done.flag") and time.monotonic() < deadline:
    time.sleep(0.01)
if not os.path.exists("done.flag"):
    raise RuntimeError("the thread of the block before did not print in 20 s")
'''


@pytest.mark.invariant("CR-5")
def test_a_thread_that_prints_after_the_code_has_run_shows_its_line_in_the_log(env, credential_store):
    """The code starts a thread, prints the first line of a key of several lines (which waits until the code ends, as
    it may begin the key) and ends; the thread prints while the next block runs (a handshake of two files): each line
    shows in the Log as printed, in order, and nothing fails."""
    write_cards(env, {"PEM": {"rows": {"key": None}}})
    plant(credential_store, {"PEM.key": KEY})

    result = run(env, write_flow(env, {"talk": code(LATE), "next": code(NEXT)}, ["talk.done -> next.after"]))
    run_id, status = env.last_run()

    assert status == "success", told(env, result)
    assert statuses(env, run_id) == {"talk": "success", "next": "success"}, told(env, result)
    assert log(env, run_id, "talk") == ["started", KEY_LINES[0], "late"], told(env, result)
    assert [line for line in result.stderr.splitlines() if line.endswith("[talk] INFO: late")], result.stderr
    assert "Traceback" not in terminal(result), terminal(result)


# --- a value of several lines in an error line printed outside a run -----------------------------------------------

CHECKS = 'print("the same key" if secret("PEM.key") == certificate else "another key")'


@pytest.mark.invariant("CR-5")
def test_a_key_typed_as_a_blocks_input_shows_as_stars_in_the_error_line_refusing_the_run(env, credential_store):
    """The key (a value of four lines) pasted, unquoted, as the input of a block run on its own that reads it: the
    run is refused before it begins (nothing below print hides what is printed then) with an error line that
    repeats what was typed, the key as *** and no line of it shown."""
    write_cards(env, {"PEM": {"rows": {"key": None}}})
    plant(credential_store, {"PEM.key": KEY})
    path = write_flow(env, {"check": code(CHECKS, inputs=["certificate"])})

    result = taskloom(env, "run", path, "--only", "check", "--input", f"certificate={KEY}")

    refused_cleanly(env, result, "the value for certificate")
    assert [line for line in KEY_LINES if line in terminal(result)] == [], terminal(result)
    assert [line for line in result.stderr.splitlines() if "the value for certificate (***)" in line], result.stderr
