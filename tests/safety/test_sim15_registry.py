import pytest

from ems.sim.scenarios import SCENARIOS, run_setup


@pytest.mark.parametrize("seed", range(1, 11))
def test_full_brake_acceptance_on_real_services(seed):
    scenario = SCENARIOS["SIM-15"]
    result = run_setup(scenario.runs[0], seed)
    criteria = scenario.check(result, result.ctx)
    assert criteria and all(passed for _, passed, _ in criteria), criteria
    assert result.metrics["calls_not_cleared"] == 0
