"""The relay of a run's output (see cli._hide_below_print): a process of its own, so what a run writes just before it
dies (a crash, os._exit) is still written on. It reads what the run's file descriptor 1 or 2 carries on its stdin and
writes it on to its stdout, whole lines at a time (the rest at a flush, or when the run's end closes the pipe), with
every secret the run has told it of as ***; lines that may begin a secret of several lines wait for the rest (ready),
at the latest until the run's end. main(ENCODING, TOKEN) is all it runs (launch.relay_command): it imports
nothing of Taskloom.

The run tells it, in the stream itself, between two MARKs (TOKEN, a random text no output holds, between NULs):
- nothing: a flush. All before it is written on (but what ready holds), then one byte is written to stderr, which
  the run waits for.
- +HEX: a part of a secret (UTF-8, as hexadecimal), in parts short enough to reach it whole (a pipe's PIPE_BUF).
- =: the parts so far are a secret: from here on it is written as ***."""

import os
import signal

TOKEN = 16  # random bytes of the token: 32 hexadecimal digits, so a MARK (them between NULs) is 34 bytes
# bytes of a secret in one record (MARK, +, the secret's hexadecimal, MARK): at most 512, macOS' PIPE_BUF
PART = (512 - 2 * (2 * TOKEN + 2) - 1) // 2


def ready(data, several, flush: bool = False) -> int:
    """How much of `data` (text, or bytes) can be written on now, its secrets as ***: up to its last line break (all
    of it at a flush), but never into a secret of several lines (one of `several`, of data's type), nor from the line
    on which one begins whose first lines (to a line break at least) `data` ends with: that waits for what comes."""
    nl = "\n" if isinstance(data, str) else b"\n"
    end = len(data) if flush else data.rfind(nl) + 1
    while True:
        starts = []
        for secret in several:  # (where it begins, reaching past end)
            first = secret[:secret.find(nl) + 1]  # (its first line, with its line break)
            at = data.find(first, max(0, end - len(secret) + 1), end)
            while at >= 0 and not secret.startswith(data[at:at + len(secret)]):
                at = data.find(first, at + 1, end)
            if at >= 0:
                starts.append(at)
        if not starts:
            return end
        end = data.rfind(nl, 0, min(starts)) + 1


def main(encoding: str, token: str) -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # (Ctrl+C in a terminal reaches it too: the run says what it does)
    mark_bytes = b"\0" + token.encode() + b"\0"
    out = open(1, "wb", buffering=0, closefd=False)  # (a Windows console's takes UTF-8)
    secrets, several, part, held = [], [], b"", b""  # (several: those of several lines, as bytes: ready)

    def put(data: bytes):
        nonlocal out
        for coding in dict.fromkeys((encoding, "utf-8")):
            text = data.decode(coding, "surrogateescape")
            for secret in secrets:
                text = text.replace(secret, "***")
            data = text.encode(coding, "surrogateescape")
        try:
            while out is not None and data:
                data = data[out.write(data):]
        except OSError:  # nothing reads it any more: what comes from then on goes nowhere
            out = None

    while chunk := os.read(0, 65536):
        held += chunk
        while (at := held.find(mark_bytes)) >= 0:
            end = held.find(mark_bytes, at + len(mark_bytes))
            if end < 0:  # (the rest of it is still to come)
                break
            body, before, held = held[at + len(mark_bytes):end], held[:at], held[end + len(mark_bytes):]
            if not body:
                end = ready(before, several, flush=True)
                put(before[:end])
                os.write(2, b".")
                held = before[end:] + held
                continue
            if body.startswith(b"+"):
                part += bytes.fromhex(body[1:].decode())
            else:
                secret = part.decode("utf-8", "surrogatepass")
                forms = dict.fromkeys((secret, secret.replace("\n", "\r\n")))  # (also as Python on Windows writes it)
                secrets += forms
                secrets.sort(key=len, reverse=True)  # (a longer one holding a shorter one first)
                # (as bytes in each coding put reads, a character one has not as Python writes it)
                several += [form.encode(coding, "backslashreplace") for form in forms if "\n" in form
                            for coding in {encoding, "utf-8"}]
                part = b""
            held = before + held  # (a line written around it is one line)
        told = held.find(mark_bytes)
        end = ready(held if told < 0 else held[:told], several)
        put(held[:end])
        held = held[end:]
    told = held.find(mark_bytes)
    put(held if told < 0 else held[:told])  # (never a part of a secret the run died telling)
    return 0

