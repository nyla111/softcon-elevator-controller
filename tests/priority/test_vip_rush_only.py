"""Issue #65: VIP exclusive trips only in Rush (FR-71); outside Rush a VIP call keeps its VIP
priority and is served through normal dispatch, ahead of ordinary calls (FR-73, BR-10).

Rush exit follows FR-15 / BR-02 (a mode change never cancels a trip; new rules apply to new
decisions): an exclusive trip already started runs to its normal end, a VIP call decided after
the change is served as a normal call. The first part uses the protocol doubles (conftest.py),
the second the real simulator and the real Part 2 dispatcher."""
from datetime import datetime

import pytest

from ems.contracts.events import (
    CallAssigned,
    ModeChanged,
    PassengerAlighted,
    PassengerArrived,
    PassengerBoarded,
    PriorityTripEnded,
    PriorityTripStarted,
)
from ems.contracts.types import CallPriority, CarState, Mode
from ems.core.app import build

from .conftest import SECURITY

NON_RUSH = [Mode.BALANCED, Mode.ECO]


def _finish(env, call, car):
    env.publish(PassengerBoarded(env.now(), call.call_id, car))
    env.publish(PassengerAlighted(env.now(), call.call_id, car, call.destination))


def _nothing_taken(env):
    return (env.states.log == [] and env.controllers.log == [] and env.p.holds == {}
            and env.p.trips == {} and not env.events(PriorityTripStarted))


# ------------------------------------------------------------------ protocol doubles
def test_vip_exclusive_trip_starts_in_rush(env):
    env.rush()
    call = env.call()
    assert env.p.claim_call(call) == "E"
    assert env.p.decisions[-1]["reason"] == "ok"
    assert env.states.states["E"] is CarState.VIP_EXCLUSIVE
    assert [e.kind for e in env.events(PriorityTripStarted)] == ["vip"]
    assert env.p.vip_starts == [0.0]


@pytest.mark.parametrize("mode", NON_RUSH, ids=lambda m: m.value)
def test_outside_rush_no_exclusive_trip_and_the_call_goes_to_normal_dispatch(env, mode):
    env.dispatcher.mode = mode
    call = env.call(group="HIGH")
    assert env.p.claim_call(call) is None                 # Part 2 dispatches it normally
    assert env.p.decisions[-1] == {"t": 0.0, "call_id": call.call_id, "group": "HIGH",
                                   "ok": False, "reason": "not_rush", "car_id": None}
    assert _nothing_taken(env)
    assert env.p.vip_starts == [] and env.p.vip_starts_in_last_hour() == 0
    assert call.priority is CallPriority.VIP              # the call keeps its VIP rank


@pytest.mark.parametrize("mode", NON_RUSH, ids=lambda m: m.value)
def test_outside_rush_vip_still_ranks_above_ordinary_calls(env, mode):
    """FR-73: top queue priority among ordinary calls (behind medical and accessible, BR-10)."""
    env.dispatcher.mode = mode
    ordinary = env.call(priority=CallPriority.NORMAL)
    env.run_to(30.0)
    vip = env.call()
    accessible = env.call(priority=CallPriority.ACCESSIBLE)
    assert env.p.claim_call(vip) is None
    assert [c.call_id for c in env.p.order([ordinary, vip, accessible])] == \
        [accessible.call_id, vip.call_id, ordinary.call_id]


def test_refusals_outside_rush_do_not_use_the_hourly_quota(env):
    env.dispatcher.mode = Mode.BALANCED
    for i in range(10):
        env.run_to(i * 60.0)
        assert env.p.claim_call(env.call()) is None
    assert [d["reason"] for d in env.p.decisions] == ["not_rush"] * 10
    assert env.p.vip_starts == []
    env.rush()                                            # same rolling hour, now Rush
    for _ in range(6):
        call = env.call()
        car = env.p.claim_call(call)
        assert car is not None
        _finish(env, call, car)
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "hourly_cap"
    assert env.p.vip_starts_in_last_hour() == 6


@pytest.mark.parametrize("mode", NON_RUSH, ids=lambda m: m.value)
def test_a_call_refused_outside_rush_is_not_promoted_when_rush_starts(env, mode):
    """Part 2 asks again for a queued call (requeue); the decision is one per call."""
    env.dispatcher.mode = mode
    queued = env.call()
    assert env.p.claim_call(queued) is None
    env.rush()
    env.run_to(5.0)
    assert env.p.claim_call(queued) is None
    assert _nothing_taken(env) and env.p.vip_starts == []
    assert [d["call_id"] for d in env.p.decisions] == [queued.call_id]
    fresh = env.call()                                    # a new call is decided in Rush
    assert env.p.claim_call(fresh) == "E"


def test_entering_rush_still_applies_every_admission_check(env):
    env.dispatcher.mode = Mode.ECO
    env.rush()
    env.dispatcher.breach = {"E", "F", "G", "H"}
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "ceiling"     # FR-72 wait-ceiling check
    env.dispatcher.breach = set()
    assert env.p.claim_call(env.call()) == "E"
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "group_exclusive_cap"
    assert len(env.p.vip_starts) == 1


@pytest.mark.parametrize("mode", [Mode.RUSH, Mode.BALANCED, Mode.ECO], ids=lambda m: m.value)
def test_safety_keeps_its_own_refusal_in_every_mode(env, mode):
    env.dispatcher.mode = mode
    env.states.safety = True                              # fire recall / emergency power
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "emergency"
    env.states.safety = False
    env.p.ems_down = True
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "ems_down"
    assert _nothing_taken(env)


def test_leaving_rush_keeps_a_started_trip_until_the_vip_arrives(env):
    """FR-15 / BR-02: the boarded VIP keeps the exclusive car to their floor; no refund."""
    env.rush()
    call = env.call(destination=20)
    car = env.p.claim_call(call)
    env.publish(PassengerBoarded(env.now(), call.call_id, car))
    env.dispatcher.mode = Mode.BALANCED                   # Rush ends while the VIP travels
    env.run_to(10.0)
    assert env.p.claim_call(call) == car                  # Part 2 still sees the trip
    assert env.states.states[car] is CarState.VIP_EXCLUSIVE
    assert env.controllers.sent("express", car)[-1][2] == (20, False)
    late = env.call()
    assert env.p.claim_call(late) is None
    assert env.p.decisions[-1]["reason"] == "not_rush"    # new decision, new rules
    env.publish(PassengerAlighted(env.now(), call.call_id, car, 20))
    ended = env.events(PriorityTripEnded)
    assert [(e.kind, e.car_id, e.cancelled) for e in ended] == [("vip", car, False)]
    assert env.p.finished[-1].end_reason == "delivered"
    assert env.p.vip_starts == [0.0]                      # counted once, never refunded
    assert env.states.states[car] is CarState.GROUP_SERVICE and env.p.holds == {}


def test_leaving_rush_before_boarding_keeps_the_pick_up(env):
    env.rush()
    call = env.call(origin=20, destination=1)
    car = env.p.claim_call(call)
    env.publish(CallAssigned(env.now(), call.call_id, car, 30.0))
    env.dispatcher.mode = Mode.ECO
    env.run_to(20.0)
    assert env.p.trip_of_call(call.call_id) is not None
    env.publish(PassengerBoarded(env.now(), call.call_id, car))
    assert env.controllers.sent("express", car)[-1][2] == (1, False)
    env.publish(PassengerAlighted(env.now(), call.call_id, car, 1))
    assert env.p.finished[-1].end_reason == "delivered" and len(env.p.vip_starts) == 1


@pytest.mark.parametrize("mode", NON_RUSH, ids=lambda m: m.value)
def test_medical_is_not_mode_gated_and_preempts_a_vip_trip_after_rush(env, mode):
    env.rush()
    vip = env.call(group="HIGH", destination=23)
    vip_car = env.p.claim_call(vip)
    env.publish(PassengerBoarded(env.now(), vip.call_id, vip_car))
    env.controllers.put(vip_car, passengers=1)
    env.dispatcher.mode = mode
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.ok and out.car_id != vip_car
    assert env.p.finished[-1].end_reason == "medical_preempt"
    assert not env.controllers.sent("cancel_stop", vip_car)      # the rider keeps L23
    assert env.p.finish_trip(SECURITY, out.ref).ok
    second = env.p.request_medical(SECURITY, 25, 1)              # outside Rush, no VIP
    assert second.ok


# ------------------------------------------------------------------ real simulator + Part 2
RUSH_AT, BALANCED_AT, ECO_AT = (datetime(2026, 11, 16, 7, 0), datetime(2026, 11, 16, 10, 0),
                                datetime(2026, 11, 16, 0, 0))


def _sim(start):
    ctx = build(seed=1, start=start)
    return ctx, ctx.services["controllers"], ctx.services["priority"], ctx.services["dispatcher"]


def _vip_arrives(ctx, pid, origin=20, destination=1):
    ctx.bus.publish(PassengerArrived(ctx.clock.now(), pid, f"panel-L{origin}", origin,
                                     destination, "HIGH", "card-L20-001", False, True))


@pytest.mark.parametrize("start,mode", [(BALANCED_AT, Mode.BALANCED), (ECO_AT, Mode.ECO)],
                         ids=["balanced", "eco"])
def test_outside_rush_the_vip_rides_a_normally_dispatched_car(start, mode):
    ctx, sim, p, dispatcher = _sim(start)
    assert dispatcher.current_mode("HIGH") is mode
    ctx.clock.run_until(5)
    _vip_arrives(ctx, "VIP1")
    assert [d["reason"] for d in p.decisions] == ["not_rush"]
    call_id = p.decisions[-1]["call_id"]
    assigned = [e for e in ctx.bus.of_type(CallAssigned) if e.call_id == call_id]
    assert len(assigned) == 1
    car = assigned[0].car_id
    assert any(c[1] == car and c[2] == "register_stop" for c in sim.command_log)
    ctx.clock.run_until(600)
    assert not ctx.bus.of_type(PriorityTripStarted) and p.vip_starts == []
    vip = sim.passengers["VIP1"]
    assert vip.alighted_at is not None and vip.destination == 1


def test_in_rush_the_same_vip_gets_an_exclusive_car():
    ctx, sim, p, dispatcher = _sim(RUSH_AT)
    assert dispatcher.current_mode("HIGH") is Mode.RUSH
    ctx.clock.run_until(5)
    _vip_arrives(ctx, "VIP1")
    assert p.decisions[-1]["reason"] == "ok"
    assert [e.kind for e in ctx.bus.of_type(PriorityTripStarted)] == ["vip"]
    ctx.clock.run_until(600)
    assert sim.passengers["VIP1"].alighted_at is not None
    assert p.finished[-1].end_reason == "delivered"


def test_a_queued_vip_is_not_promoted_when_the_fm_switches_to_rush():
    ctx, sim, p, dispatcher = _sim(ECO_AT)
    for c in ("E", "F", "G", "H"):
        sim.inject_fault(c, "F-X")
    ctx.clock.run_until(5)
    _vip_arrives(ctx, "VIP1")
    call_id = p.decisions[-1]["call_id"]
    assert p.decisions[-1]["reason"] == "not_rush"
    assert not [e for e in ctx.bus.of_type(CallAssigned) if e.call_id == call_id]   # queued
    dispatcher.set_manual_mode(Mode.RUSH)                 # FR-12
    assert dispatcher.current_mode("HIGH") is Mode.RUSH
    for c in ("E", "F", "G", "H"):
        sim.clear_fault(c)
    ctx.clock.run_until(600)
    assert [e for e in ctx.bus.of_type(CallAssigned) if e.call_id == call_id]
    assert [d["call_id"] for d in p.decisions] == [call_id]          # never decided again
    assert not ctx.bus.of_type(PriorityTripStarted) and p.vip_starts == []
    assert sim.passengers["VIP1"].alighted_at is not None


def test_the_scheduled_end_of_rush_lets_the_started_trip_finish():
    """09:30 Rush -> Balanced (config.WEEKDAY_SCHEDULE) while an exclusive trip runs."""
    ctx, sim, p, _dispatcher = _sim(datetime(2026, 11, 16, 9, 29, 30))
    ctx.clock.run_until(5)
    _vip_arrives(ctx, "VIP1", origin=1, destination=20)   # the card's own floor
    assert p.decisions[-1]["reason"] == "ok"
    car = p.decisions[-1]["car_id"]
    ctx.clock.run_until(45)
    changes = [e for e in ctx.bus.of_type(ModeChanged) if e.group == "HIGH"]
    assert [(e.old, e.new) for e in changes] == [(Mode.RUSH, Mode.BALANCED)]
    assert p.trips and next(iter(p.trips.values())).car_id == car   # not cancelled at 09:30
    _vip_arrives(ctx, "VIP2")
    assert p.decisions[-1]["reason"] == "not_rush"
    ctx.clock.run_until(900)
    assert sim.passengers["VIP1"].alighted_at is not None
    assert sim.passengers["VIP2"].alighted_at is not None
    vip_ends = [t for t in p.finished if t.kind == "vip"]
    assert [(t.car_id, t.end_reason) for t in vip_ends] == [(car, "delivered")]
    assert len(p.vip_starts) == 1 and p.holds == {}
