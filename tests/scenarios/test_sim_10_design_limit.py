"""SIM-10 design-limit latency (Part 2). The 60-minute run takes many minutes: EMS_SLOW=1 only."""
import pytest

from .part2_helpers import SLOW, run


@pytest.mark.skipif(not SLOW, reason="long run: set EMS_SLOW=1")
def test_sim_10():
    r = run("SIM-10", 1)
    assert r["passed"], [c for c in r["criteria"] if not c["passed"]]
