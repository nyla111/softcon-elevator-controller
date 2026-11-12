"""SIM-09 EMS server failover, seeds 1-10, every seed must pass (definition: ems/sim/scenarios.py).

Up-peak at the default rate (35/min, D-01), primary crashes 20 min into the measured window,
8 s takeover. Uses the nearest-car stand-in while Part 2 is a stub.
"""
import pytest

from ems.sim.scenarios import run_scenario


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_failover(seed, tmp_path):
    r = run_scenario("SIM-09", seed, out_dir=tmp_path)
    failed = [c for c in r["criteria"] if not c["passed"]]
    assert r["passed"], failed
