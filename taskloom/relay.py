"""The relay of a run's output (see cli._hide_below_print): a process of its own, so what a run writes just before it
dies (a crash, os._exit) is still written on. It reads what the run's file descriptor 1 or 2 carries on its stdin and
writes it on to its stdout, whole lines at a time (the rest at a flush, or when the run's end closes the pipe), with
every secret the run has told it of as ***. main(ENCODING, TOKEN) is all it runs (launch.relay_command): it imports
nothing of Taskloom.

The run tells it, in the stream itself, between two MARKs (TOKEN, a random text no output holds, between NULs):
- nothing: a flush. All before it is written on, then one byte is written to stderr, which the run waits for.
- +HEX: a part of a secret (UTF-8, as hexadecimal), in parts short enough to reach it whole (a pipe's PIPE_BUF).
- =: the parts so far are a secret: from here on it is written as ***."""

import os
import signal

TOKEN = 16  # random bytes of the token: 32 hexadecimal digits, so a MARK (them between NULs) is 34 bytes
# bytes of a secret in one record (MARK, +, the secret's hexadecimal, MARK): at most 512, macOS' PIPE_BUF
PART = (512 - 2 * (2 * TOKEN + 2) - 1) // 2


def main(encoding: str, token: str) -> int:
    signal.signal(signal.SIGINT, signal.SIG_IGN)  # (Ctrl+C in a terminal reaches it too: the run says what it does)
    mark_bytes = b"\0" + token.encode() + b"\0"
    out = open(1, "wb", buffering=0, closefd=False)  # (a Windows console's takes UTF-8)
    secrets, part, held = [], b"", b""

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
                put(before)
                os.write(2, b".")
                continue
            if body.startswith(b"+"):
                part += bytes.fromhex(body[1:].decode())
            else:
                secrets.append(part.decode("utf-8", "surrogatepass"))
                secrets.sort(key=len, reverse=True)  # (a longer one holding a shorter one first)
                part = b""
            held = before + held  # (a line written around it is one line)
        told = held.find(mark_bytes)
        end = held.rfind(b"\n", 0, told if told >= 0 else len(held)) + 1
        put(held[:end])
        held = held[end:]
    told = held.find(mark_bytes)
    put(held if told < 0 else held[:told])  # (never a part of a secret the run died telling)
    return 0

