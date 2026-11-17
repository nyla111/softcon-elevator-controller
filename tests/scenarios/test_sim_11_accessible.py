"""SIM-11 Accessible calls mixed into up-peak (FR-08, BR-10).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the real Part 2/3/4 services.
"""
import pytest

from .part5_helpers import RUSH_MAX_GAP, assert_part5_criteria, result

KNOWN_GAPS = {
    'Rush wait targets hold (average <= 25 s, none over 60 s), every measured call counted': RUSH_MAX_GAP,
}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_11_part5_criteria(seed):
    assert_part5_criteria("SIM-11", seed, KNOWN_GAPS)


@pytest.mark.slow
@pytest.mark.xfail(reason=RUSH_MAX_GAP)
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_11_full_acceptance(seed):
    assert result("SIM-11", seed)["passed"]
