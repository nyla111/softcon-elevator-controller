
"""SIM-07: ACS outage acceptance checks for seeds 1-10."""

import pytest

from ems.sim.harness import run
from ems.sim.scenarios import SCENARIOS
from ems.access.acceptance import check_sim_07


@pytest.mark.parametrize("seed", range(1, 11))
def test_sim_07_acceptance(seed):
    scenario = SCENARIOS["SIM-07"]

    result = run(
        profile=scenario.profile,
        seed=seed,
        faults=scenario.faults,
        warmup_s=scenario.warmup_s,
        measure_s=scenario.measure_s,
        clear_s=scenario.clear_s,
        setup=scenario.setup,
        start_hhmm=scenario.runs[0].start_hhmm,
        keep_ctx=True,
        out_dir=None,
    )

    ctx = result.ctx

    checks = check_sim_07(result, ctx)

    # All defined criteria must pass for every seed.
    assert len(checks) == 7

    for criterion, passed, detail in checks:
        assert passed, (
            f"Seed {seed} failed: {criterion}\n"
            f"Details: {detail}"
        )
