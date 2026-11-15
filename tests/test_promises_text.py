"""Each value in docs/promises/*.yaml is read whole. In a value written without quotes, YAML takes " #" and the rest
of the line for a comment, so the proof PDF, which prints these files' words, printed "A run opened from Run history
is used as it is, e.g. run." where the file says "e.g. run #5 itself although run #6 carried on from it; no later
re-run is looked for." A value that holds " #" is written in quotes or as a block (>-)."""

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]


def test_no_value_is_cut_short_where_a_number_sign_follows_a_space():
    """After a value without quotes, nothing more is on its line (a comment there may be the rest of its sentence);
    a comment on a line of its own, after a key, or after a value in quotes cuts nothing."""
    files = sorted((REPO / "docs" / "promises").glob("*.yaml"))
    assert files, "no docs/promises/*.yaml"
    cut = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for token in yaml.scan(text):
            if isinstance(token, yaml.ScalarToken) and token.plain:  # a key too: its colon follows it
                rest = lines[token.end_mark.line][token.end_mark.column:].strip()
                if rest.startswith("#"):
                    cut.append(f"{path.name}, line {token.end_mark.line + 1}: {token.value[-40:]!r} is followed by "
                               f"{rest[:60]!r}")
    assert not cut, "read as comments, not as the values' words (write the value in quotes):\n" + "\n".join(cut)
