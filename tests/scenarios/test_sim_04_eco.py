"""SIM-04 Eco quiet pair + surge (Part 2). About 2 min per seed: seed 1 by default,
seeds 1-10 with EMS_SLOW=1."""
import pytest

from .part2_helpers import SLOW, run


@pytest.mark.parametrize("seed", range(1, 11) if SLOW else [1])
def test_sim_04(seed):
    r = run("SIM-04", seed)
    assert r["passed"], [c for c in r["criteria"] if not c["passed"]]
