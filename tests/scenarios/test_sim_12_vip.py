"""SIM-12 Ten VIP calls during up-peak in one group (FR-71, FR-72, FR-76).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the Part 5 stand-ins for the missing Part 2/3/4 services.
"""
import pytest

from .part5_helpers import BLOCKED_REASON, assert_part5_criteria, result

NOT_PART5 = {
    'ordinary calls of group LOW within the 60 s ceiling': 'nearest-car stand-in + stub ceiling answers',
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_12_part5_criteria(seed):
    assert_part5_criteria("SIM-12", seed, NOT_PART5)


@pytest.mark.slow
@pytest.mark.xfail(strict=True, reason=BLOCKED_REASON)
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_12_full_acceptance(seed):
    assert result("SIM-12", seed)["passed"]
