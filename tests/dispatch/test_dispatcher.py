"""Part 2 unit and small integration tests (trip model, engine, modes, dispatcher)."""
from datetime import datetime

from ems.contracts.events import (AlertRaised, CallAssigned, CallReassigned, CallRequested,
                                  ControllerFault, EmsServerDown, EmsServerUp, FireAlarm,
                                  ModeChanged, OverloadDetected, PanelMessage)
from ems.contracts.types import (Call, CallSource, CarStatus, Direction, DoorState, Mode)
from ems.core.app import build
from ems.dispatch.model import Pax, replay
from ems.dispatch.service import Dispatcher


def status(floor=1, position=None, direction=Direction.IDLE, doors=DoorState.CLOSED, car="A"):
    return CarStatus(car, "LOW", floor, float(floor if position is None else position),
                     direction, doors, 0.0, 0)


def call(cid, origin, dest, t=0.0, group="LOW"):
    return Call(cid, origin, dest, group, t, CallSource.DESTINATION_PANEL)


# ---------------------------------------------------------------- trip model
def test_replay_idle_car_timing_matches_appendix_b():
    # idle at L1, passenger at L5 -> L10: 4 floors, open, board, dwell 5, close, 5 floors, open
    plan = replay(status(1), [], [Pax("c1", 5, 10)], capacity=21)
    assert plan.pickup["c1"] == 4 * 1.5 + 2.0 + 1.0
    assert plan.arrive["c1"] == 4 * 1.5 + 2 + 5 + 2 + 5 * 1.5 + 2 + 1.0
    assert plan.run_s == 9 * 1.5 and plan.door_cycles == 2


def test_replay_keeps_direction_like_the_controller():
    # moving up from L3 with a rider to L9 and a call at L2: L2 is served after L9
    plan = replay(status(3, 3.5, Direction.UP), [Pax("r", None, 9)], [Pax("w", 2, 1)], 21)
    assert plan.arrive["r"] < plan.pickup["w"]


def test_replay_respects_capacity_fr101():
    waiting = [Pax(f"p{i}", 1, 5) for i in range(22)]
    plan = replay(status(1), [], waiting, capacity=21)
    assert len(plan.overflow) == 1 and "p21" in plan.overflow
    assert "p21" in plan.pickup                     # picked up on the next trip


# ---------------------------------------------------------------- dispatcher
def test_registers_real_dispatcher_and_assigns():
    ctx = build(seed=1)
    d = ctx.services["dispatcher"]
    assert isinstance(d, Dispatcher)
    ctx.bus.publish(CallRequested(0.0, call("c1", 1, 8)))
    (ev,) = ctx.bus.of_type(CallAssigned)
    assert ev.call_id == "c1" and ev.car_id in "ABCD"
    sim = ctx.services["controllers"]
    assert any(c[2] == "register_stop" and c[3][1] == "c1" and c[4] for c in sim.command_log)
    # through the FR-105 guard, not around it
    assert ctx.services["dispatch_guard"].calls == 1


def test_prefers_the_car_already_there():
    ctx = build(seed=1)
    sim = ctx.services["controllers"]
    sim.cars["C"].floor = 9
    ctx.bus.publish(CallRequested(0.0, call("c1", 9, 1)))
    assert ctx.bus.of_type(CallAssigned)[0].car_id == "C"


def test_fault_reassigns_waiting_calls_and_keeps_clock_fr04():
    ctx = build(seed=1)
    ctx.bus.publish(CallRequested(0.0, call("c1", 12, 1)))
    car = ctx.bus.of_type(CallAssigned)[0].car_id
    ctx.services["controllers"].inject_fault(car)
    ctx.bus.publish(ControllerFault(0.5, car, "F-01", False, False))
    (ev,) = ctx.bus.of_type(CallReassigned)      # Part 4 or Part 2 moves it, only once
    assert ev.from_car == car and ev.to_car != car and ev.reason.endswith("fault")
    rec = ctx.services["dispatcher"].calls["c1"]
    assert rec.call.created_at == 0.0 and rec.car == ev.to_car


def test_overload_step_out_message_names_next_car_fr100():
    ctx = build(seed=1)
    ctx.bus.publish(CallRequested(0.0, call("c1", 1, 8)))
    car = ctx.bus.of_type(CallAssigned)[0].car_id
    ctx.bus.publish(OverloadDetected(1.0, car, 1, 110.0))
    msgs = [m for m in ctx.bus.of_type(PanelMessage) if m.target == f"car:{car}"]
    assert msgs and msgs[0].t == 1.0 and "Next car:" in msgs[0].text_en
    assert car not in msgs[0].text_en.split("Next car:")[1]


def test_no_commands_while_server_down_or_fire():
    ctx = build(seed=1)
    d = ctx.services["dispatcher"]
    ctx.bus.publish(EmsServerDown(0.0, "primary"))
    ctx.bus.publish(CallRequested(0.0, call("c1", 5, 1)))
    assert d.calls["c1"].state == "controller" and not ctx.bus.of_type(CallAssigned)
    ctx.bus.publish(EmsServerUp(8.0, "standby", 8.0))
    ctx.bus.publish(FireAlarm(9.0))
    ctx.bus.publish(CallRequested(9.0, call("c2", 5, 1)))
    assert d.calls["c2"].state == "pending" and not ctx.bus.of_type(CallAssigned)


# ---------------------------------------------------------------- modes
def test_schedule_and_manual_switch_fr11_fr12():
    ctx = build(seed=1, start=datetime(2026, 11, 16, 6, 59))      # Monday
    d = ctx.services["dispatcher"]
    assert d.current_mode("LOW") is Mode.ECO
    ctx.clock.run_until(61)
    assert d.current_mode("LOW") is Mode.RUSH
    assert any(e.cause == "schedule" and e.new is Mode.RUSH for e in ctx.bus.of_type(ModeChanged))
    d.set_manual_mode(Mode.BALANCED, "LOW")
    assert d.current_mode("LOW") is Mode.BALANCED and d.current_mode("HIGH") is Mode.RUSH
    # manual lasts until the next scheduled change (09:30)
    assert d.modes.overrides["LOW"].until == datetime(2026, 11, 16, 9, 30)


def test_weekend_and_holiday_are_eco():
    ctx = build(seed=1, start=datetime(2026, 11, 21, 8, 0))       # Saturday
    assert ctx.services["dispatcher"].current_mode("LOW") is Mode.ECO
    ctx = build(seed=1, start=datetime(2026, 11, 16, 8, 0))
    d = ctx.services["dispatcher"]
    d.modes.set_holidays({datetime(2026, 11, 16).date()})
    assert d.current_mode("LOW") is Mode.ECO


def test_eco_steps_up_on_queue_fr14():
    ctx = build(seed=1, start=datetime(2026, 11, 16, 22, 0))
    d = ctx.services["dispatcher"]
    ctx.bus.publish(EmsServerDown(0.0, "primary"))     # nobody gets served: the queue grows
    d.ems_down = False
    for i in range(12):
        ctx.bus.publish(CallRequested(0.0, call(f"c{i}", 1, 10 + i % 5)))
    d.fire_hold = True                                 # keep them pending
    for rec in d.calls.values():
        rec.state = "pending"
    ctx.clock.run_until(40)
    ups = [e for e in ctx.bus.of_type(ModeChanged) if e.cause == "eco_step_up"]
    assert ups and ups[0].group == "LOW" and ups[0].new is Mode.BALANCED
    assert any(a.kind == "eco_step_up" and "fm" in a.recipients
               for a in ctx.bus.of_type(AlertRaised))
    # stays in Balanced until the next scheduled change (07:00 Rush next morning)
    assert d.modes.overrides["LOW"].until == datetime(2026, 11, 17, 7, 0)
