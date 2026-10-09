"""Entry point of the packaged programs: `taskloom` (console) and `taskloomw` (no console)."""

import os
import sys

from taskloom.cli import run_cli

if __name__ == "__main__":
    windowed = os.path.splitext(os.path.basename(sys.executable))[0].lower() == "taskloomw"
    if sys.stdout is None:  # a windowed program on Windows has no console to print to
        sys.stdout = sys.stderr = open(os.devnull, "w")
    args = sys.argv[1:] or (["editor"] if windowed else ["--help"])
    run_cli(args)
