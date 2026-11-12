"""SIM-09 EMS server failover: up-peak at 35 persons/min, primary crashes 20 min into the measured
window, seeds 1-10. Every seed must: fail over within 10 s, keep serving passengers during the
gap, lose at most 30 s of records and clear every call.

The rate is a parameter (RATE_PER_MIN); the up_peak profile default (75/min) is not changed.
Uses the nearest-car stand-in for the Part 2 dispatcher; remove `setup` when Part 2 is in.
"""
import pytest

from ems.contracts.events import EmsServerDown, EmsServerUp, PassengerAlighted
from ems.sim.baseline import install_nearest_car
from ems.sim.faults import ems_server_crash, measured
from ems.sim.harness import run
from ems.sim.traffic import with_rate

RATE_PER_MIN = 35
TAKEOVER_S = 8.0
CRASH_AT = measured(20)


def setup(ctx):
    install_nearest_car(ctx)
    db = ctx.services["db"]            # stand-in for Part 6: one record per delivered passenger
    ctx.bus.subscribe(PassengerAlighted, lambda e: db.write({"t": e.t, "call_id": e.call_id}))


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_failover(seed, tmp_path):
    r = run(with_rate("up_peak", RATE_PER_MIN), seed, [ems_server_crash(CRASH_AT, TAKEOVER_S)],
            setup=setup, out_dir=tmp_path, keep_ctx=True)
    ctx, bus = r.ctx, r.ctx.bus
    (down,), (up,) = bus.of_type(EmsServerDown), bus.of_type(EmsServerUp)
    assert down.t == CRASH_AT and up.gap_s <= 10 and up.t - down.t == up.gap_s

    sim = ctx.services["controllers"]
    gap = lambda e: down.t <= e.t <= up.t                      # noqa: E731
    delivered = [e for e in bus.of_type(PassengerAlighted) if gap(e)]
    assert delivered                                           # service continued in the gap
    arrived_in_gap = [p for p in sim.passengers.values() if down.t <= p.arrived_at <= up.t]
    assert arrived_in_gap and all(p.car_id for p in arrived_in_gap)   # nobody left unassigned
    assert sim.fallback_assignments                            # built-in logic did assign cars
    assert not [c for c in sim.command_log if down.t <= c[0] < up.t and c[4]]

    lost = ctx.services["failover"].lost_records
    assert all(rec["t"] > down.t - 30 for rec in lost)         # <= 30 s of records lost
    assert r.metrics["calls_not_cleared"] == 0 and r.passed
