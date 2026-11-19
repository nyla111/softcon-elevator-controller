"""SIM-06 Car fault at up-peak, then a second removal requested (FR-04, FR-33).

Seeds 1-10, every seed on its own (definition: ems/priority/acceptance.py).

Runs on Part 1's simulator with the real Part 2/3/4 services.
"""
import pytest

from .part5_helpers import assert_part5_criteria, result

KNOWN_GAPS: dict[str, str] = {}


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_06_part5_criteria(seed):
    assert_part5_criteria("SIM-06", seed, KNOWN_GAPS)


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_06_full_acceptance(seed):
    assert result("SIM-06", seed)["passed"]
