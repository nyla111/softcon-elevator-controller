"""SIM-18 Medical priority starts during an occupied VIP trip (FR-127, BR-10).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the Part 5 stand-ins for the missing Part 2/3/4 services.
"""
import pytest

from .part5_helpers import BLOCKED_REASON, assert_part5_criteria, result

NOT_PART5 = {
    
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_18_part5_criteria(seed):
    assert_part5_criteria("SIM-18", seed, NOT_PART5)


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=BLOCKED_REASON)
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_18_full_acceptance(seed):
    assert result("SIM-18", seed)["passed"]
