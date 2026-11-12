"""SIM-16 (link-loss half): one car loses its link 20 min into the measured window for 5 min,
up-peak at 35 persons/min, seeds 1-10. HeartbeatLost must come within 3 s, every call is delivered.

Uses the nearest-car stand-in for the Part 2 dispatcher (it skips the car once register_stop is
refused). The plug-in-crash half of SIM-16 belongs to Part 2.
"""
import pytest

from ems.contracts.events import HeartbeatLost, HeartbeatRestored
from ems.sim.baseline import install_nearest_car
from ems.sim.faults import link_loss, measured
from ems.sim.harness import run
from ems.sim.traffic import with_rate

RATE_PER_MIN = 35
AT, DURATION = measured(20), 300.0


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_link_loss(seed, tmp_path):
    r = run(with_rate("up_peak", RATE_PER_MIN), seed, [link_loss("B", AT, DURATION)],
            setup=install_nearest_car, out_dir=tmp_path, keep_ctx=True)
    (lost,) = r.ctx.bus.of_type(HeartbeatLost)
    assert lost.car_id == "B" and 0 < lost.t - AT <= 3.0
    (back,) = r.ctx.bus.of_type(HeartbeatRestored)
    assert AT + DURATION <= back.t <= AT + DURATION + 1.0
    assert r.metrics["calls_not_cleared"] == 0 and r.passed


@pytest.mark.skip(reason="plug-in crash half of SIM-16 belongs to Part 2 (dispatch)")
def test_dispatch_plugin_crash():
    pass
