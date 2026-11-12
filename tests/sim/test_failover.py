"""EMS server failover: refused commands, built-in group logic, takeover, bounded data loss."""
from ems.contracts.events import EmsServerDown, EmsServerUp

from .helpers import arrive, assign, make


def crash(ctx, at, takeover_s=8.0):
    ctx.clock.call_at(at, lambda: ctx.services["failover"].crash(takeover_s))


def test_down_and_up_events_and_gap():
    ctx, sim = make()
    crash(ctx, 100)
    ctx.clock.run_until(200)
    down, up = ctx.bus.of_type(EmsServerDown), ctx.bus.of_type(EmsServerUp)
    assert [(e.t, e.node) for e in down] == [(100.0, "primary")]
    assert [(e.t, e.node, e.gap_s) for e in up] == [(108.0, "standby", 8.0)]
    assert up[0].gap_s <= 10


def test_commands_are_refused_while_the_ems_server_is_down():
    ctx, sim = make()
    crash(ctx, 100)
    ctx.clock.run_until(99)
    assert sim.register_stop("A", 3, "before")
    ctx.clock.run_until(104)
    assert not sim.register_stop("A", 4, "during") and not sim.park("B", 2)
    ctx.clock.run_until(108)
    assert sim.register_stop("A", 5, "after")
    during = [e for e in sim.command_log if 100 <= e[0] < 108]
    assert during and not any(e[4] for e in during)


def test_controllers_serve_passengers_themselves_after_3_s():
    ctx, sim = make()
    crash(ctx, 100)
    ctx.clock.call_at(101, lambda: arrive(ctx, "P1", 1, 7))      # arrives before they notice
    ctx.clock.call_at(105, lambda: arrive(ctx, "P2", 1, 9))      # arrives in fallback mode
    ctx.clock.run_until(103.0)
    assert sim.fallback_active and sim.passengers["P1"].car_id is not None
    assert sim.passengers["P1"].car_id in "ABCD"
    ctx.clock.run_until(400)
    assert sim.fallback_periods == [[103.0, 108.0]]
    assert [a[1] for a in sim.fallback_assignments] == ["P1", "P2"]
    assert not sim.fallback_active
    for pid in ("P1", "P2"):
        assert sim.passengers[pid].alighted_at is not None
    assert not [e for e in sim.command_log if e[4] and 100 <= e[0] < 108]


def test_passengers_assigned_before_the_crash_keep_their_car():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 6)
    assign(ctx, sim, "P1", "C")
    crash(ctx, 1)
    ctx.clock.run_until(100)
    assert sim.passengers["P1"].car_id == "C" and sim.passengers["P1"].alighted_at is not None
    assert sim.fallback_assignments == []


def test_takeover_faster_than_detection_still_serves_everyone():
    ctx, sim = make()
    crash(ctx, 10, takeover_s=2.0)
    ctx.clock.call_at(11, lambda: arrive(ctx, "P1", 1, 4))
    ctx.clock.run_until(100)
    assert sim.passengers["P1"].alighted_at is not None
    assert ctx.bus.of_type(EmsServerUp)[0].gap_s == 2.0


def test_at_most_30_s_of_records_are_lost():
    ctx, _ = make()
    db = ctx.services["db"]
    for k in range(1, 20):                                       # one record every 5 s, t=5..95
        ctx.clock.call_at(5.0 * k, lambda k=k: db.write({"t": 5.0 * k}))
    crash(ctx, 100)
    ctx.clock.run_until(200)
    lost = ctx.services["failover"].lost_records
    assert lost and all(r["t"] > 100 - 30 for r in lost)
    kept = [r["t"] for r in db.records()]
    assert kept == sorted(kept) and set(kept) | {r["t"] for r in lost} == {5.0 * k for k in range(1, 20)}


def test_second_crash_while_down_is_ignored():
    ctx, _ = make()
    crash(ctx, 10)
    crash(ctx, 12)
    ctx.clock.run_until(100)
    assert len(ctx.bus.of_type(EmsServerDown)) == 1
