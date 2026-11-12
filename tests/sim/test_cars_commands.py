"""ControllerAdapter commands must return False when the car cannot accept them."""
from ems.contracts.events import ControllerFault, ControllerFaultCleared, DoorsOpened
from ems.contracts.types import DoorState

from .helpers import arrive, assign, make


def test_register_stop_refused_for_unknown_car_or_unserved_floor():
    _, sim = make()
    assert not sim.register_stop("Z", 2, "c")
    assert not sim.register_stop("A", 20, "c")     # LOW group does not serve L20
    assert not sim.register_stop("E", 5, "c")      # HIGH group serves L1 and L16..L30
    assert sim.register_stop("E", 20, "c")


def test_faulted_car_refuses_all_commands():
    ctx, sim = make()
    sim.inject_fault("A", "F-07")
    assert sim.status("A").fault_code == "F-07"
    assert not sim.register_stop("A", 2, "c")
    assert not sim.cancel_stop("A", "c")
    assert not sim.park("A", 1)
    assert not sim.set_standby("A", True)
    assert not sim.set_independent_service("A", True)
    assert not sim.close_to_new_calls("A", True)
    assert not sim.express("A", 3)
    assert len(ctx.bus.of_type(ControllerFault)) == 1
    sim.clear_fault("A")
    assert len(ctx.bus.of_type(ControllerFaultCleared)) == 1
    assert sim.register_stop("A", 2, "c")


def test_disconnected_car_refuses_commands_and_sends_no_status():
    ctx, sim = make()
    sim.set_disconnected("B", True)
    assert not sim.register_stop("B", 2, "c")
    ctx.clock.run_until(3)
    assert not [e for e in ctx.bus.log if getattr(getattr(e, "status", None), "car_id", "") == "B"]
    assert sim.status("B").disconnected


def test_non_dispatchable_car_refuses_new_stops():
    _, sim = make()
    assert sim.set_standby("A", True)
    assert not sim.register_stop("A", 2, "c")
    assert sim.set_standby("A", False)
    assert sim.close_to_new_calls("A", True)
    assert not sim.register_stop("A", 2, "c")
    assert sim.close_to_new_calls("A", False)
    assert sim.set_independent_service("A", True)
    assert not sim.register_stop("A", 2, "c")
    assert not sim.park("A", 1)


def test_cancel_stop_removes_pending_stop():
    ctx, sim = make()
    sim.register_stop("A", 9, "c")
    assert sim.status("A").pending_stops == [9]
    assert sim.cancel_stop("A", "c")
    assert not sim.cancel_stop("A", "c")           # already gone
    ctx.clock.run_until(60)
    assert not ctx.bus.of_type(DoorsOpened)


def test_park_moves_without_opening_doors():
    ctx, sim = make()
    assert sim.park("A", 4)
    ctx.clock.run_until(30)
    s = sim.status("A")
    assert s.floor == 4 and s.doors is DoorState.CLOSED
    assert not ctx.bus.of_type(DoorsOpened)


def test_express_skips_other_stops():
    ctx, sim = make()
    sim.register_stop("A", 3, "c")
    assert sim.express("A", 8)
    ctx.clock.run_until(100)
    assert [e.floor for e in ctx.bus.of_type(DoorsOpened)] == [8, 3]


def test_express_hold_keeps_doors_open_until_next_command():
    ctx, sim = make()
    assert sim.express("A", 2, hold_doors_open=True)
    ctx.clock.run_until(100)
    assert sim.status("A").doors is DoorState.OPEN
    assert sim.park("A", 1)                        # releases the hold
    ctx.clock.run_until(200)
    assert sim.status("A").floor == 1


def test_fault_freezes_car_and_resumes():
    ctx, sim = make()
    sim.register_stop("A", 5, "c")
    ctx.clock.run_until(2.0)                       # mid-travel
    sim.inject_fault("A")
    frozen = sim.status("A").position
    ctx.clock.run_until(50)
    assert sim.status("A").position == frozen and not ctx.bus.of_type(DoorsOpened)
    sim.clear_fault("A")
    ctx.clock.run_until(120)
    assert [e.floor for e in ctx.bus.of_type(DoorsOpened)] == [5]


def test_reassigned_passenger_boards_new_car_only():
    from ems.contracts.events import CallReassigned, PassengerBoarded
    ctx, sim = make()
    arrive(ctx, "P1", 1, 4)
    assign(ctx, sim, "P1", "A")
    # Reassign before A gets the doors open (A opens at t=2.0).
    ctx.bus.publish(CallReassigned(0.0, "P1", "A", "B", "test"))
    assert sim.register_stop("B", 1, "P1")
    ctx.clock.run_until(60)
    assert [e.car_id for e in ctx.bus.of_type(PassengerBoarded)] == ["B"]
