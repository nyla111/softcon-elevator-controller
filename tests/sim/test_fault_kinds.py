"""Fault flavours, command audit and phantom overload."""
from ems.contracts.events import ControllerFault, ControllerFaultCleared, OverloadDetected, \
    PassengerAlighted, PassengerBoarded
from ems.contracts.types import DoorState

from .helpers import arrive, assign, make, times


def test_between_floors_fault_keeps_riders_inside_and_resumes():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 5)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(5)                               # boarded at 3, doors still open
    sim.inject_fault("A", "F-09", between_floors=True)
    s = sim.status("A")
    assert 1.0 < s.position < 2.0 and s.doors is DoorState.CLOSED and s.passengers == 1
    assert ctx.bus.of_type(ControllerFault)[0].between_floors is True
    ctx.clock.run_until(100)
    assert sim.status("A").position == s.position and sim.status("A").passengers == 1
    sim.clear_fault("A")
    ctx.clock.run_until(200)
    assert sim.passengers["P1"].alighted_at is not None
    assert len(ctx.bus.of_type(ControllerFaultCleared)) == 1


def test_doors_stuck_fault_keeps_doors_closed():
    ctx, sim = make()
    arrive(ctx, "P1", 1, 5)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(5)                               # doors open, rider aboard
    sim.inject_fault("A", "F-11", doors_stuck=True)
    assert ctx.bus.of_type(ControllerFault)[0].doors_stuck is True
    ctx.clock.run_until(60)
    assert sim.status("A").doors is DoorState.CLOSED and sim.status("A").passengers == 1
    sim.clear_fault("A")
    ctx.clock.run_until(200)
    assert sim.passengers["P1"].alighted_at is not None


def test_command_log_records_accepted_and_refused_commands():
    ctx, sim = make()
    ctx.clock.run_until(3)
    assert sim.register_stop("A", 4, "c1")
    sim.inject_fault("B")
    assert not sim.register_stop("B", 4, "c2")
    assert not sim.park("B", 1)
    assert sim.set_standby("C", True)
    assert sim.command_log == [
        (3.0, "A", "register_stop", (4, "c1"), True),
        (3.0, "B", "register_stop", (4, "c2"), False),
        (3.0, "B", "park", (1,), False),
        (3.0, "C", "set_standby", (True,), True),
    ]


def test_no_command_is_accepted_by_a_faulted_car():
    ctx, sim = make()
    sim.inject_fault("A")
    for _ in range(3):
        sim.register_stop("A", 2, "c")
        sim.cancel_stop("A", "c")
        sim.express("A", 3)
    assert [e for e in sim.command_log if e[1] == "A" and e[4]] == []


def test_overload_burst_blocks_boarding_for_one_door_cycle():
    ctx, sim = make()
    sim.overload_burst("A")
    arrive(ctx, "P1", 1, 5)
    assign(ctx, sim, "P1", "A")
    ctx.clock.run_until(100)
    overloads = ctx.bus.of_type(OverloadDetected)
    assert len(overloads) == 1 and overloads[0].load_pct > 100 and overloads[0].floor == 1
    assert times(ctx, PassengerBoarded) == [12.0]        # second door cycle: open 9-11, board 12
    assert times(ctx, PassengerAlighted)                 # and the trip still completes
