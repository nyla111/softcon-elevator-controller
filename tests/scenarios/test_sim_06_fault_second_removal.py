"""SIM-06 Car fault at up-peak, then a second removal requested (FR-04, FR-33).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the Part 5 stand-ins for the missing Part 2/3/4 services.
"""
import pytest

from .part5_helpers import BLOCKED_REASON, assert_part5_criteria, result

NOT_PART5 = {
    'waiting calls re-assigned away from the faulted car (FR-04, Part 2)': 'Part 2 FR-04 (stub dispatcher cannot re-assign)',
    'degraded waits logged for the affected calls': 'Part 6 marks degraded only on CallReassigned (Part 2)',
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_06_part5_criteria(seed):
    assert_part5_criteria("SIM-06", seed, NOT_PART5)


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=BLOCKED_REASON)
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_06_full_acceptance(seed):
    assert result("SIM-06", seed)["passed"]
