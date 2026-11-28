"""Credentials on the PC (docs/promises/credentials.yaml), more of what code below print writes (CR-5): the relay of a
run's output (taskloom/relay.py, its protocol in its docstring) answers every flush, also several that reach it in one
read, and writes on what comes after them; and in a real Windows console what code writes below print shows with the
value as *** and other text exactly as written. Helpers and values are tests/test_credentials_relay.py's."""

import json
import os
import subprocess
import sys

import pytest

from taskloom import relay
from taskloom.launch import relay_command
from test_credentials_relay import CARDS, VALUES, command, flow_of, secret_shown
from test_credentials_sinks import TOKEN
from test_credentials_store import plant, write_cards


@pytest.fixture(autouse=True)
def stored(env, credential_store):
    write_cards(env, CARDS)
    plant(credential_store, VALUES)


# --- the relay program: flushes ----------------------------------------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_the_relay_answers_every_flush_also_several_in_one_read_and_writes_on_what_follows():
    """As a run writes when a Ctrl+C handler prints while the run waits for a flush's answer (a second flush at once):
    a line, two flushes, the value told in records (+HEX, then =), then a line holding it, all in the pipe before the
    relay reads it. The relay answers both flushes (one byte each on its error output) and writes both lines on, the
    value as ***."""
    token = os.urandom(16).hex()
    mark = b"\0" + token.encode() + b"\0"
    hexed = TOKEN.encode("utf-8").hex().encode()
    told = [mark + b"+" + hexed[i:i + 2 * relay.PART] + mark for i in range(0, len(hexed), 2 * relay.PART)]
    data = b"a\n" + mark + mark + mark + mark + b"".join(told) + mark + b"=" + mark + f"x {TOKEN} y\n".encode("utf-8")
    read, write = os.pipe()
    try:
        os.write(write, data)  # (a few hundred bytes: the pipe holds them)
        os.close(write)
        done = subprocess.run([*relay_command(), "utf-8", token], stdin=read, capture_output=True, timeout=60)
    finally:
        os.close(read)

    assert (done.returncode, done.stdout, done.stderr) == (0, b"a\nx *** y\n", b"..")


# --- a real Windows console --------------------------------------------------------------------------------------

TEXT = "→ Ünïcødé"  # → Ünïcødé: one letter cp1252 lacks, and letters it holds

CHILD = f'''\
import os
os.write(1, ("out child " + os.environ["FOR_CHILD"] + " " + {ascii(TEXT)} + " .\\n").encode("utf-8"))
'''

CODE = '''\
import os, subprocess, sys
t = secret("UAT.token")
text = {text}
os.write(1, f"out os.write {{t}} {{text}} .\\n".encode("utf-8"))
sys.stdout.write(f"out sys.stdout {{t}} {{text}} .\\n")
sys.stdout.flush()
subprocess.run([sys.executable, {child}], env={{**os.environ, "FOR_CHILD": t}}, check=True)
os.write(2, f"err os.write {{t}} {{text}} .\\n".encode("utf-8"))
'''

# Started in a console of its own: writes the text straight to it (a row "control TEXT ."), runs the run in it (the
# run inherits it), waits for it, then reads that console's screen and writes what it shows, a line a row, to the file
# given.
HELPER = '''\
import ctypes, json, subprocess, sys
from ctypes import wintypes

command, cwd, result_file, text = json.loads(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]


class COORD(ctypes.Structure):
    _fields_ = [("X", wintypes.SHORT), ("Y", wintypes.SHORT)]


class SMALL_RECT(ctypes.Structure):
    _fields_ = [("Left", wintypes.SHORT), ("Top", wintypes.SHORT), ("Right", wintypes.SHORT),
                ("Bottom", wintypes.SHORT)]


class CONSOLE_SCREEN_BUFFER_INFO(ctypes.Structure):
    _fields_ = [("dwSize", COORD), ("dwCursorPosition", COORD), ("wAttributes", wintypes.WORD),
                ("srWindow", SMALL_RECT), ("dwMaximumWindowSize", COORD)]


kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
kernel32.GetStdHandle.restype = wintypes.HANDLE
kernel32.GetConsoleMode.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetConsoleMode.restype = wintypes.BOOL
kernel32.GetConsoleScreenBufferInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(CONSOLE_SCREEN_BUFFER_INFO)]
kernel32.GetConsoleScreenBufferInfo.restype = wintypes.BOOL
kernel32.ReadConsoleOutputCharacterW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, COORD,
                                                 ctypes.POINTER(wintypes.DWORD)]
kernel32.ReadConsoleOutputCharacterW.restype = wintypes.BOOL

result = {"console": False, "exit": None, "screen": "", "error": None}
try:
    screen = kernel32.GetStdHandle(-11 & 0xFFFFFFFF)  # STD_OUTPUT_HANDLE
    result["console"] = bool(kernel32.GetConsoleMode(screen, ctypes.byref(wintypes.DWORD())))
    print("control " + text + " .", flush=True)
    result["exit"] = subprocess.run(command, cwd=cwd, timeout=150).returncode
    info = CONSOLE_SCREEN_BUFFER_INFO()
    if not kernel32.GetConsoleScreenBufferInfo(screen, ctypes.byref(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    rows = []
    for y in range(info.dwCursorPosition.Y + 1):
        cells, read = ctypes.create_unicode_buffer(info.dwSize.X), wintypes.DWORD()
        if not kernel32.ReadConsoleOutputCharacterW(screen, cells, info.dwSize.X, COORD(0, y), ctypes.byref(read)):
            raise ctypes.WinError(ctypes.get_last_error())
        rows.append(cells[:read.value].rstrip())
    result["screen"] = "\\n".join(rows)
except BaseException as error:
    result["error"] = repr(error)
with open(result_file, "w", encoding="utf-8") as f:
    json.dump(result, f)
'''


@pytest.mark.invariant("CR-5")
@pytest.mark.skipif(sys.platform != "win32", reason="a Windows console: Windows only")
def test_in_a_windows_console_what_code_writes_below_print_shows_the_value_as_stars_and_other_text_as_written(
        env, tmp_path):
    """`taskloom run` in a console of its own (a terminal window): the code writes a line holding the value and
    → Ünïcødé with os.write(1, ...) and with sys.stdout.write, starts a program that writes one too, then writes one
    with os.write(2, ...). The console's screen shows each line once, exactly as written (the text as the console
    shows it written straight to it, before the run: a console's font may lack a letter), the value as ***, the
    output's in the order written; no form of the value shows anywhere on it."""
    child = tmp_path / "child.py"
    child.write_text(CHILD, encoding="utf-8")
    flow = flow_of(env, CODE.format(text=ascii(TEXT), child=ascii(str(child))))
    helper, result_file = tmp_path / "console_helper.py", tmp_path / "screen.json"
    helper.write_text(HELPER, encoding="utf-8")
    hidden = subprocess.STARTUPINFO(dwFlags=subprocess.STARTF_USESHOWWINDOW, wShowWindow=subprocess.SW_HIDE)
    process = subprocess.Popen(
        [sys.executable, str(helper), json.dumps(command(flow)), str(env.root), str(result_file), TEXT],
        env=env.env, cwd=env.root, creationflags=subprocess.CREATE_NEW_CONSOLE, startupinfo=hidden)
    try:
        assert process.wait(180) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    seen = json.loads(result_file.read_text(encoding="utf-8"))
    rows = seen["screen"].split("\n")
    control = [row[len("control "):-len(" .")] for row in rows if row.startswith("control ")]

    assert (seen["console"], seen["error"], seen["exit"], len(control)) == (True, None, 0, 1), seen
    text = control[0]
    assert [row for row in rows if row.startswith("out ")] == [
        f"out os.write *** {text} .", f"out sys.stdout *** {text} .", f"out child *** {text} ."], seen["screen"]
    assert [row for row in rows if row.startswith("err ")] == [f"err os.write *** {text} ."], seen["screen"]
    assert secret_shown(seen["screen"].encode("utf-8"), TOKEN) == [], seen["screen"]


# --- a flush: what comes after it ----------------------------------------------------------------------------------

@pytest.mark.invariant("CR-5")
def test_a_long_text_with_no_line_break_flushed_shows_whole_before_the_error_output_line_written_next(env):
    """Output and error output to one terminal (2>&1): the code writes a long text with no line break (4 MB, the value
    in it 4 times) to the output and flushes, then writes a line to the error output and flushes, then ends the line
    on the output. The terminal shows the whole long text, each value as ***, then the error output's line, then the
    output's line break; the value shows nowhere."""
    from test_credentials_relay import lines, run_bytes

    source = '''\
import sys
t = secret("UAT.token")
block = "x" * 1_000_000
sys.stdout.write("long " + "".join(f"{block} {t} " for _ in range(4)) + "end")
sys.stdout.flush()
sys.stderr.write(f"after {t}\\n")
sys.stderr.flush()
sys.stdout.write("\\n")
sys.stdout.flush()
'''
    result = run_bytes(env, flow_of(env, source), stderr=subprocess.STDOUT)
    shown = lines(result.stdout)
    after = shown.find(b"after ")

    assert result.returncode == 0, shown[-2000:]
    wanted = b"long " + b"".join(b"x" * 1_000_000 + b" *** " for _ in range(4)) + b"end" + b"after ***\n\n"
    assert (shown.count(b"after "), wanted in shown) == (1, True), shown[max(0, after - 200):after + 200]
    assert secret_shown(shown, TOKEN) == []
