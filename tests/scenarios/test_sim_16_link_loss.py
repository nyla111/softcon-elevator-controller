"""SIM-16: link loss on car B (20 min into the measured window, 5 min) AND dispatch plug-in crash
(40 min in, 5 min), seeds 1-10, every seed must pass (definition: ems/sim/scenarios.py).

The plug-in crash half uses the nearest-car stand-in engine behind the FR-105 guard while Part 2
is a stub; once Part 2 registers its engine the same criteria apply to the real plug-in.
"""
import pytest

from ems.sim.scenarios import run_scenario


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_link_loss_and_plugin_crash(seed, tmp_path):
    r = run_scenario("SIM-16", seed, out_dir=tmp_path)
    failed = [c for c in r["criteria"] if not c["passed"]]
    assert r["passed"], failed
