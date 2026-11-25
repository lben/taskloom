"""{{name}} in a typed value (docs/promises/typed-values.yaml TV-3, with the braces of docs/promises/braces.yaml):
naming another input of the block that has a typed value is an error, and its message says why in its own words. What
mutation testing of placeholder_problems (docs/promises/braces.mutants.yaml) found no test for: the words of that
message. Through the real `taskloom validate` and `taskloom run` on a temporary Taskloom folder.
"""

import pytest

FLOW = """
    blocks:
      receiver:
        type: logic.python
        config: {inputs: [t, b], outputs: [output], code: "open('ran.txt', 'w').write(t)"}
        inputs:
          t: {type: text, value: "to {{b}}"}
          b: {type: text, value: "B"}
    edges: []
"""
SAYS = ("error: receiver: the value typed for the input t: {{b}} is the value typed for the input b, which no line "
        "brings: name a flow parameter or an input a line comes into")


@pytest.mark.invariant("TV-3")
def test_a_typed_text_naming_an_input_with_a_typed_value_is_refused_saying_why_in_full(env):
    """receiver's typed text t is "to {{b}}", b being another input of receiver with a typed value (no line comes into
    it). `taskloom validate` says, whole: {{b}} is the value typed for the input b, which no line brings: name a flow
    parameter or an input a line comes into; `taskloom run` refuses to start with the same line, and nothing runs."""
    flow = env.write("flow.yaml", FLOW)

    validated, ran = env.taskloom("validate", flow), env.taskloom("run", flow)

    assert validated.returncode == 2 and SAYS in validated.stderr.splitlines(), validated.stderr
    assert ran.returncode == 2 and SAYS in ran.stderr.splitlines(), ran.stderr
    assert not (env.root / "ran.txt").exists()
    assert not (env.home / "history.db").exists() or env.query("SELECT COUNT(*) FROM runs") == [(0,)]
