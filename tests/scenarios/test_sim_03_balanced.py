"""SIM-03 office hours, Balanced (Part 2)."""
import pytest

from .part2_helpers import run


@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_03(seed):
    r = run("SIM-03", seed)
    assert r["passed"], [c for c in r["criteria"] if not c["passed"]]
