"""SIM-01 up-peak: 75 pax/min, 90% lobby -> tenant floors, seeds 1-10, every seed must pass.

Uses the trivial nearest-car chooser (ems.sim.baseline) in place of the Part 2 dispatcher.
It cannot meet the RUSH targets, so the test is expected to fail until Part 2 merges.
When Part 2 is in: remove the `setup=` argument and the xfail mark.
"""
import pytest

from ems.contracts.config import MODE_TARGETS
from ems.contracts.types import Mode
from ems.sim.baseline import install_nearest_car
from ems.sim.harness import run

AVG_TARGET_S, CEILING_S = MODE_TARGETS[Mode.RUSH]


@pytest.mark.xfail(reason="waiting for Part 2 dispatcher (nearest-car stand-in is too weak)",
                   strict=False)
@pytest.mark.parametrize("seed", range(1, 11))
def test_up_peak(seed, tmp_path):
    r = run("up_peak", seed, setup=install_nearest_car, out_dir=tmp_path)
    assert r.metrics["calls_not_cleared"] == 0
    assert r.metrics["avg_wait_s"] <= AVG_TARGET_S
    assert r.metrics["max_wait_s"] <= CEILING_S
