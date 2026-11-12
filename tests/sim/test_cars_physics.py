"""Exact timing of the car physics (config.SIM: 1.5 s/floor, 2 s doors, 5 s dwell, 1 s/person)."""
from ems.contracts.events import (CarStatusUpdated, DoorsOpened, OverloadDetected,
                                  PassengerAlighted, PassengerBoarded)
from ems.contracts.types import Direction, DoorState

from .helpers import arrive, assign, make, times


def test_all_cars_start_empty_at_lobby_doors_closed():
    _, sim = make()
    statuses = sim.all_status()
    assert len(statuses) == 9
    for s in statuses:
        assert (s.floor, s.doors, s.direction) == (1, DoorState.CLOSED, Direction.IDLE)
        assert s.passengers == 0 and s.pending_stops == []
    assert [s.car_id for s in sim.all_status("SERVICE")] == ["S"]
    assert len(sim.all_status("LOW")) == 4


def test_one_floor_plus_doors_exact_time():
    ctx, sim = make()
    assert sim.register_stop("A", 2, "c1")
    ctx.clock.run_until(1.4)
    assert 1.0 < sim.status("A").position < 2.0 and sim.status("A").floor == 1
    ctx.clock.run_until(1.5)                       # travel 1.5 s
    assert sim.status("A").floor == 2 and sim.status("A").doors is DoorState.OPENING
    ctx.clock.run_until(3.4)
    assert times(ctx, DoorsOpened, car_id="A") == []
    ctx.clock.run_until(3.5)                       # + 2 s opening
    assert times(ctx, DoorsOpened, car_id="A", floor=2) == [3.5]
    ctx.clock.run_until(8.4)
    assert sim.status("A").doors is DoorState.OPEN
    ctx.clock.run_until(8.5)                       # + 5 s dwell
    assert sim.status("A").doors is DoorState.CLOSING
    ctx.clock.run_until(10.5)                      # + 2 s closing
    s = sim.status("A")
    assert (s.doors, s.direction, s.pending_stops) == (DoorState.CLOSED, Direction.IDLE, [])


def test_board_and_alight_extend_nothing_when_one_passenger():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 3)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(60)
    assert times(ctx, DoorsOpened, car_id="A", floor=1) == [2.0]
    assert times(ctx, PassengerBoarded) == [3.0]           # 1 s after doors open
    # dwell 5 s -> doors close at 9.0, 2 floors = 3 s -> 12.0, open -> 14.0, alight 15.0
    assert times(ctx, DoorsOpened, car_id="A", floor=3) == [14.0]
    assert times(ctx, PassengerAlighted) == [15.0]
    p = sim.passengers["P1"]
    assert (p.wait_s, p.ride_s) == (3.0, 12.0)


def test_accessible_passenger_gets_ten_second_dwell():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 2, accessible=True)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(11.9)                      # doors open at 2.0, dwell 10 -> close at 12.0
    assert sim.status("A").doors is DoorState.OPEN
    ctx.clock.run_until(12.0)
    assert sim.status("A").doors is DoorState.CLOSING


def test_many_passengers_extend_dwell():
    ctx, sim = make()
    for i in range(8):
        arrive(ctx, f"P{i}", 1, 2)
        assign(ctx, sim, f"P{i}", "A")
    ctx.clock.run_until(9.9)                       # 8 boardings end at 10.0 (> 5 s min dwell)
    assert sim.status("A").doors is DoorState.OPEN
    ctx.clock.run_until(10.0)
    assert sim.status("A").doors is DoorState.CLOSING
    assert times(ctx, PassengerBoarded) == [3.0 + i for i in range(8)]


def test_travel_and_doors_never_overlap():
    ctx, sim = make()
    for i, (o, d) in enumerate([(1, 6), (4, 2), (3, 9)]):
        arrive(ctx, f"P{i}", o, d)
        assign(ctx, sim, f"P{i}", "A")
    ctx.clock.run_until(300)
    seen = ctx.bus.of_type(CarStatusUpdated)
    assert len(seen) > 100
    for ev in seen:
        s = ev.status
        if s.doors is not DoorState.CLOSED:
            assert s.position == s.floor, f"doors {s.doors} while moving at t={ev.t}"


def test_collective_order_serves_stops_in_travel_direction():
    ctx, sim = make()
    for floor in (8, 3, 5):                        # registered in "wrong" order
        assert sim.register_stop("A", floor, f"c{floor}")
    ctx.clock.run_until(100)
    opened = [e.floor for e in ctx.bus.of_type(DoorsOpened)]
    assert opened == [3, 5, 8]


def test_car_reverses_when_nothing_ahead():
    ctx, sim = make()
    sim.register_stop("A", 6, "up")
    ctx.clock.run_until(9.5)                       # passed L5, now beyond it
    assert sim.status("A").floor >= 5
    sim.register_stop("A", 2, "down")
    ctx.clock.run_until(200)
    assert [e.floor for e in ctx.bus.of_type(DoorsOpened)] == [6, 2]


def test_status_published_every_second_per_car():
    ctx, _ = make()
    ctx.clock.run_until(10)
    mine = [e.t for e in ctx.bus.of_type(CarStatusUpdated) if e.status.car_id == "A"]
    assert mine == [float(t) for t in range(1, 11)]


def test_capacity_never_exceeded():
    ctx, sim = make()
    for i in range(30):                            # 30 want the same 21-person car
        arrive(ctx, f"P{i:02d}", 1, 5)
        assign(ctx, sim, f"P{i:02d}", "A")
    ctx.clock.run_until(600)
    riders = 0
    for ev in sorted(ctx.bus.log, key=lambda e: e.t):
        if isinstance(ev, PassengerBoarded):
            riders += 1
        elif isinstance(ev, PassengerAlighted):
            riders -= 1
        assert 0 <= riders <= 21
    assert max(e.status.passengers for e in ctx.bus.of_type(CarStatusUpdated)) == 21
    overloads = ctx.bus.of_type(OverloadDetected)
    assert overloads and overloads[0].floor == 1 and overloads[0].load_pct > 100
    assert sim.undelivered() == 0                  # the 9 left behind were picked up later


def test_service_car_capacity_is_26():
    _, sim = make()
    assert sim.cars["S"].capacity == 26 and sim.cars["A"].capacity == 21
