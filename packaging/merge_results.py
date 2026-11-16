"""One OS's test results, from the results files of the CI machines that shared its test files, in one file.

    python packaging/merge_results.py --expect N OUT.json IN.json [IN.json ...]

Each IN.json is a machine's file (tests/conftest.py writes it with TASKLOOM_LEDGER=<file>). OUT.json has their
tests joined and their numbers summed, as one machine running them all would have written it: packaging/ledger.py and
packaging/release_evidence.py read one file per OS. It fails, saying why, when there are not N files (a machine that
crashed or ran out of time left none: its tests would silently drop out of the proof), when a test is in two (the
machines' files overlap, and one outcome would hide the other), or when they are of different commits or OSes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--expect", type=int, required=True, metavar="N")
    p.add_argument("out", type=Path)
    p.add_argument("inputs", type=Path, nargs="*")
    a = p.parse_args(argv)
    missing = [str(f) for f in a.inputs if not f.is_file()]
    if len(a.inputs) != a.expect or missing:
        sys.exit(f"merge_results.py: {a.expect} results files expected, one per machine; got {len(a.inputs)}"
                 + (f", and these are not there: {', '.join(missing)}" if missing else ""))
    results = [json.loads(f.read_text(encoding="utf-8")) for f in a.inputs]
    for field in ("commit", "platform"):
        if len({r[field] for r in results}) > 1:
            sys.exit(f"merge_results.py: the files are of different {field}s: "
                     + ", ".join(f"{f} {r[field]}" for f, r in zip(a.inputs, results)))
    tests = [t for r in results for t in r["tests"]]
    twice = sorted(i for i, n in Counter(t["id"] for t in tests).items() if n > 1)
    if twice:
        sys.exit(f"merge_results.py: in more than one machine's file: {', '.join(twice)}")
    merged = {**results[0], "totals": {o: sum(r["totals"][o] for r in results) for o in results[0]["totals"]},
              "tests": tests}
    a.out.write_text(json.dumps(merged, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
