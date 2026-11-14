"""SIM-11 Accessible calls mixed into up-peak (FR-08, BR-10).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the Part 5 stand-ins for the missing Part 2/3/4 services.
"""
import pytest

from .part5_helpers import BLOCKED_REASON, assert_part5_criteria, result

NOT_PART5 = {
    'EMS requests the >= 10 s door time': 'CCR-02: ControllerAdapter has no door-time command',
    'priority order medical > accessible > VIP > ordinary applied by the dispatcher': 'Part 2 must apply rank()',
    'Rush wait targets hold (average <= 25 s, none over 60 s), every measured call counted': 'nearest-car stand-in misses Rush targets even without Part 5',
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_11_part5_criteria(seed):
    assert_part5_criteria("SIM-11", seed, NOT_PART5)


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=BLOCKED_REASON)
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_11_full_acceptance(seed):
    assert result("SIM-11", seed)["passed"]
