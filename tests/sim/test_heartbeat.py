from ems.contracts.events import CarStatusUpdated, DoorsOpened, HeartbeatLost, HeartbeatRestored

from .helpers import make


def test_link_loss_gives_heartbeat_lost_exactly_3_s_after_last_status():
    ctx, sim = make()
    ctx.clock.run_until(10.5)
    sim.set_disconnected("B", True)                      # last status of B was at t=10
    ctx.clock.run_until(12.9)
    assert not ctx.bus.of_type(HeartbeatLost)
    ctx.clock.run_until(13.0)
    lost = ctx.bus.of_type(HeartbeatLost)
    assert [(e.car_id, e.t) for e in lost] == [("B", 13.0)]
    assert sim.status("B").disconnected and not sim.status("A").disconnected
    assert not sim.register_stop("B", 2, "c")
    ctx.clock.run_until(60)
    assert len(ctx.bus.of_type(HeartbeatLost)) == 1       # published once


def test_status_resuming_publishes_heartbeat_restored():
    ctx, sim = make()
    ctx.clock.run_until(10.5)
    sim.set_disconnected("B", True)
    ctx.clock.run_until(20.5)
    sim.set_disconnected("B", False)
    ctx.clock.run_until(21.0)
    assert [(e.car_id, e.t) for e in ctx.bus.of_type(HeartbeatRestored)] == [("B", 21.0)]
    assert not sim.status("B").disconnected
    assert sim.register_stop("B", 2, "c")


def test_car_finishes_its_existing_stops_during_link_loss():
    ctx, sim = make()
    assert sim.register_stop("A", 8, "c")
    ctx.clock.run_until(2)
    sim.set_disconnected("A", True)
    ctx.clock.run_until(60)
    assert [e.floor for e in ctx.bus.of_type(DoorsOpened) if e.car_id == "A"] == [8]
    assert not [e for e in ctx.bus.of_type(CarStatusUpdated)
                if e.status.car_id == "A" and e.t > 2]
