"""Entry point of the packaged programs: `taskloom` (console) and `taskloomw` (no console)."""

import os
import sys

if __name__ == "__main__" and sys.argv[1:2] == ["--relay"]:  # a run's relay (taskloom.launch.RELAY_ARGUMENT)
    from taskloom import relay

    sys.exit(relay.main(*sys.argv[2:4]))

from taskloom.cli import run_cli  # noqa: E402

if __name__ == "__main__":
    windowed = os.path.splitext(os.path.basename(sys.executable))[0].lower() == "taskloomw"
    if sys.stdout is None:  # a windowed program on Windows has no console to print to
        sys.stdout = sys.stderr = open(os.devnull, "w")
    args = sys.argv[1:] or (["editor"] if windowed else ["--help"])
    run_cli(args)
