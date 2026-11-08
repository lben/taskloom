"""TLA+ trace validation (packaging/tla_trace.py) catches a code behaviour that is not one of the model's: a trace of
the real scheduler service matches specs/formal/SchedulerService.tla, and the same trace with a refused claim recorded
as begun does not. TLC runs here, so these tests need java and tla2tools.jar 1.7.4."""

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packaging"))
import tla_trace  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("java") is None or not tla_trace.jar().is_file(),
    reason=f"TLA+ trace validation runs TLC: it needs java on the PATH and tla2tools.jar 1.7.4 at {tla_trace.jar()} "
           "(or $TLA2TOOLS_JAR)")


def test_a_refused_claim_recorded_as_begun_does_not_match_the_model(tmp_path):
    """The real trace (the service starts f1's run for its time; you start f1 by hand while its program loads; its
    claim is refused; your run ends) matches the model. The same trace with that claim recorded as begun, as a claim
    that begins shows it, does not: no behaviour of the model matches it at that claim. With the model's claim not
    checking Overlap (ClaimChecksOverlap FALSE) it would match: what catches it is the model's rule, not a slip in the
    edit."""
    real = tla_trace.refused_claim(tmp_path / "code")
    assert [e["kind"] for e in real["events"] if e["kind"] == "Claim"] == ["Claim"]
    assert tla_trace.check(real, tmp_path / "real") == {"matched": True}
    planted, n = tla_trace.planted(real)
    assert tla_trace.check(planted, tmp_path / "planted") == {"matched": False, "unmatched": n}
    cfg = (tla_trace.SPEC / "TraceSchedulerService.cfg").read_text(encoding="utf-8")
    assert "ClaimChecksOverlap = TRUE" in cfg
    (tmp_path / "unchecked").mkdir()
    (tmp_path / "unchecked" / "Unchecked.cfg").write_text(
        cfg.replace("ClaimChecksOverlap = TRUE", "ClaimChecksOverlap = FALSE"), encoding="utf-8")
    assert tla_trace.check(planted, tmp_path / "unchecked", "Unchecked.cfg") == {"matched": True}
