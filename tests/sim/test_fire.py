"""Fire: recall to L1, doors open, no EMS command accepted until cleared AND reset."""
from ems.contracts.events import DoorsOpened, FirefighterService, PassengerAlighted
from ems.contracts.types import DoorState

from .helpers import arrive, assign, make


def accepted_after(sim, t):
    return [e for e in sim.command_log if e[0] >= t and e[4]]


def test_cars_recall_to_lobby_and_open_doors():
    ctx, sim = make()
    sim.register_stop("C", 10, "x")
    ctx.clock.run_until(5)                              # mid-travel
    ctx.services["devices"].fire.alarm()
    ctx.clock.run_until(60)
    for s in sim.all_status():
        if s.group != "SERVICE":
            assert s.floor == 1 and s.doors is DoorState.OPEN and s.pending_stops == []
    assert any(e.car_id == "C" and e.floor == 1 for e in ctx.bus.of_type(DoorsOpened))


def test_no_accepted_command_after_fire_alarm_until_reset():
    ctx, sim = make()
    fire = ctx.services["devices"].fire
    ctx.clock.run_until(10)
    fire.alarm()
    t_alarm = ctx.clock.now()
    for car in ("A", "E", "S"):
        assert not sim.register_stop(car, 3, "c")
        assert not sim.park(car, 1)
        assert not sim.express(car, 3)
        assert not sim.set_standby(car, True)
    ctx.clock.run_until(100)
    assert not sim.reset_fire()                         # alarm still active
    fire.clear()
    assert not sim.register_stop("A", 3, "c")           # cleared but not reset
    assert accepted_after(sim, t_alarm) == []
    assert sim.reset_fire()
    assert not sim.reset_fire()                         # nothing left to reset
    assert sim.register_stop("A", 3, "c") and sim.register_stop("S", 3, "c")
    ctx.clock.run_until(200)
    assert sim.status("A").floor == 3 and sim.status("A").doors is DoorState.CLOSED


def test_service_car_reports_firefighter_service():
    ctx, sim = make()
    fire = ctx.services["devices"].fire
    fire.alarm()
    fire.clear()
    assert [(e.car_id, e.active) for e in ctx.bus.of_type(FirefighterService)] == [("S", True)]
    sim.reset_fire()
    assert [(e.car_id, e.active) for e in ctx.bus.of_type(FirefighterService)] == [
        ("S", True), ("S", False)]


def test_io_signal_lost_acts_like_a_panel_alarm():
    ctx, sim = make()
    ctx.services["devices"].fire.alarm("io_signal_lost")
    assert not sim.register_stop("B", 4, "c")


def test_riders_leave_at_the_lobby_and_waiting_passengers_do_not_board():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 12)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(14)                             # boarded at 3, now travelling
    assert sim.status("A").passengers == 1
    arrive(ctx, "P2", 1, 5)
    assign(ctx, sim, "P2", "B")                         # B is opening at L1 ...
    ctx.services["devices"].fire.alarm()                # ... when the alarm sounds
    ctx.clock.run_until(100)
    p1 = sim.passengers["P1"]
    assert p1.evacuated and p1.alighted_at is not None
    assert [e.floor for e in ctx.bus.of_type(PassengerAlighted)] == [1]
    assert sim.passengers["P2"].boarded_at is None
    assert sim.status("A").passengers == 0


def test_recall_while_doors_open_closes_then_returns():
    ctx, sim = make()
    sim.register_stop("A", 4, "c")
    ctx.clock.run_until(8)                              # A is at L4 with doors open
    assert sim.status("A").doors is DoorState.OPEN
    ctx.services["devices"].fire.alarm()
    ctx.clock.run_until(80)
    assert sim.status("A").floor == 1 and sim.status("A").doors is DoorState.OPEN
