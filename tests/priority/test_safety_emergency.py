"""Part 5 during and after fire / power emergencies, with Part 4's REAL Safety service and Part 1's
simulator (issue #18). Evidence is taken from bus events, car_states holds and the controller
command log, not from Part 5 flags alone."""
from types import SimpleNamespace

import pytest

from ems import priority
from ems.contracts import config
from ems.contracts import events as E
from ems.contracts.types import Call, CallPriority, CallSource, Role
from ems.contracts.types import CarState as S
from ems.core.app import build
from ems.priority.standins import RecordingCarStates

SECURITY, FM, ADMIN = "staff-security-1", "staff-fm-1", "staff-admin-T01"


def make():
    ctx = build(parts=["sim", "safety"])
    ctx.services["access"] = SimpleNamespace(permission=ctx.services["acs"].lookup)
    priority.register(ctx)
    return ctx, ctx.services["priority"], ctx.services["car_states"], ctx.services["controllers"]


def vip_call(call_id="vip", origin=1, destination=20):
    return Call(call_id, origin, destination, "HIGH", 0, CallSource.DESTINATION_PANEL,
                CallPriority.VIP)


def alarm(ctx, kind):
    if kind == "fire":
        ctx.services["devices"].fire.alarm()
    else:
        ctx.services["devices"].power.lose_mains(generator_failed=True)


def clear(ctx, kind):
    if kind == "fire":
        ctx.services["devices"].fire.clear()
        assert ctx.services["controllers"].reset_fire()
    else:
        ctx.services["devices"].power.restore_mains()
        ctx.clock.run_until(ctx.clock.now() + config.MAINS_STABLE_S)


def confirm(safety):
    safety.report_safety_clear(True)
    for car in safety.table.cars():
        safety.report_controller_normal(car, True)
    assert safety.confirm_recovery(Role.FM)


def sent_after(sim, mark):
    return [(c[1], c[2]) for c in sim.command_log[mark:]]


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_cancelled_vip_is_never_returned_or_restarted_and_new_vips_work_after_recovery(kind):
    ctx, p, safety, sim = make()
    call = vip_call()
    car = p.claim_call(call)
    assert car is not None and (S.VIP_EXCLUSIVE, "priority:vip") in safety.table.holds[car]
    mark = len(sim.command_log)
    alarm(ctx, kind)
    ended = ctx.bus.of_type(E.PriorityTripEnded)
    assert [(e.kind, e.car_id, e.cancelled) for e in ended] == [("vip", car, True)]
    assert p.claim_call(call) is None                       # #18 PR16-03: no cached car
    assert sim.command_log[mark:] == []
    clear(ctx, kind)
    assert p.claim_call(call) is None and p.claim_call(vip_call("during-recovery")) is None
    assert sim.command_log[mark:] == []                     # still held by Safety
    confirm(safety)
    # recovery: only the owed re-open, the cancelled trip is not started again
    assert sent_after(sim, mark) == [(car, "close_to_new_calls")]
    assert p.claim_call(call) is None
    assert len(ctx.bus.of_type(E.PriorityTripStarted)) == 1 and ended == \
        ctx.bus.of_type(E.PriorityTripEnded)
    fresh = p.claim_call(vip_call("after-recovery"))
    assert fresh is not None and (S.VIP_EXCLUSIVE, "priority:vip") in safety.table.holds[fresh]
    assert len(ctx.bus.of_type(E.PriorityTripStarted)) == 2


def test_a_claim_made_while_safety_reassigns_calls_gets_no_car():
    """Part 4 asks Part 2 to re-assign calls inside its FireAlarm handler, before Part 5 has
    seen the alarm: a re-dispatch of the VIP call there must not get the VIP car back."""
    ctx, p, safety, _sim = make()
    call = vip_call()
    car = p.claim_call(call)
    answers = []
    stub = ctx.services["dispatcher"]

    class Redispatching:
        def reassign_calls_of(self, car_id, reason):
            if car_id == car:
                answers.append(p.claim_call(call))
            return stub.reassign_calls_of(car_id, reason)

        def __getattr__(self, name):
            return getattr(stub, name)

    ctx.services["dispatcher"] = Redispatching()
    alarm(ctx, "fire")
    assert answers == [None] and p.trips == {}


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_late_door_cycle_callback_after_an_emergency_does_nothing(kind):
    ctx, p, safety, sim = make()
    car = p.claim_call(vip_call(origin=1))
    ctx.clock.run_until(6)                       # doors open at the origin, nobody boards
    trip = p.trip_of_car(car)
    assert trip is not None and trip.phase == "at_origin"
    alarm(ctx, kind)
    mark, ended = len(sim.command_log), list(ctx.bus.of_type(E.PriorityTripEnded))
    ctx.clock.run_until(60)                                  # past the VIP door-cycle callback
    assert sim.command_log[mark:] == [] and ctx.bus.of_type(E.PriorityTripEnded) == ended
    assert len(ended) == 1 and safety.state(car) <= S.EMERGENCY_POWER


@pytest.mark.parametrize("kind", ["fire", "power"])
def test_repeated_emergency_signals_end_and_release_once(kind):
    ctx, p, safety, sim = make()
    states = ctx.services["car_states"] = RecordingCarStates(ctx, safety)
    car = p.claim_call(vip_call())
    request = p.goods.request(ADMIN, 0, 600, ["test-mover"], "S")
    assert request.ok and p.goods.approve(FM, request.ref).ok
    ctx.clock.run_until(1)
    assert p.goods.moves[request.ref].status == "active"
    mark = len(sim.command_log)
    alarm(ctx, kind)
    ctx.bus.publish(E.FireAlarm(1) if kind == "fire" else E.MainsLost(1))
    alarm(ctx, kind)
    assert sim.command_log[mark:] == []
    assert len(ctx.bus.of_type(E.PriorityTripEnded)) == 1
    assert [(e.car_id, e.reason) for e in ctx.bus.of_type(E.GoodsMoveEnded)] == [
        ("S", "emergency:" + kind)]
    releases = [x for x in states.log if x[2] == "release"]
    assert sorted((x[1], x[4]) for x in releases) == [
        (car, "priority:vip"), ("S", "priority:goods")]
    assert p.holds == {}


def test_overlapping_fire_and_power_loss_keep_priority_closed_until_both_recover():
    ctx, p, safety, sim = make()
    car = p.claim_call(vip_call())
    mark = len(sim.command_log)
    alarm(ctx, "fire")
    alarm(ctx, "power")
    clear(ctx, "fire")                                       # fire over, mains still lost
    assert p.emergency() and p.claim_call(vip_call("v2")) is None
    assert not p.request_medical(SECURITY, 1, 20).ok
    assert not safety.confirm_recovery(Role.FM)
    p.retry_reopen()
    assert sim.command_log[mark:] == []
    ctx.services["devices"].power.restore_mains()           # mains back, not yet stable
    ctx.clock.run_until(ctx.clock.now() + config.MAINS_STABLE_S - 1)
    safety.report_safety_clear(True)
    for c in safety.table.cars():
        safety.report_controller_normal(c, True)
    assert not safety.confirm_recovery(Role.FM) and p.emergency()
    assert sim.command_log[mark:] == []
    ctx.clock.run_until(ctx.clock.now() + 1)
    assert safety.confirm_recovery(Role.FM)
    assert not p.emergency() and sent_after(sim, mark) == [(car, "close_to_new_calls")]
    assert p.claim_call(vip_call("v3")) is not None


def test_fire_recovery_without_a_firefighter_car_reopens_priority_after_fm():
    """design_limit building: no service group, so no FirefighterService ends the fire."""
    from ems.sim.building import BuildingSpec
    ctx = build(parts=["sim", "safety"], services={"building": BuildingSpec.design_limit()})
    ctx.services["access"] = SimpleNamespace(permission=ctx.services["acs"].lookup)
    priority.register(ctx)
    p, safety = ctx.services["priority"], ctx.services["car_states"]
    sim = ctx.services["controllers"]
    alarm(ctx, "fire")
    assert not ctx.bus.of_type(E.FirefighterService)
    ctx.services["devices"].fire.clear()
    assert p.emergency()                                     # alarm cleared, Safety still holds
    assert sim.reset_fire()
    assert p.emergency()
    confirm(safety)
    assert not p.emergency()
    group = ctx.services["building"].groups[0]
    call = Call("vip-dl", group.floors[0], group.floors[-1], group.name, 0,
                CallSource.DESTINATION_PANEL, CallPriority.VIP)
    assert p.claim_call(call) is not None


def test_preempting_an_isolated_vip_car_is_refused_and_leaves_its_holds():
    ctx, p, safety, _sim = make()
    car = p.claim_call(vip_call(origin=20, destination=25))
    ctx.clock.run_until(3)
    assert safety.request(car, S.MAINTENANCE, "test:isolated")
    out = p.send_to_lobby(SECURITY, car)
    assert not out.ok and out.reason == "car_isolated"
    assert safety.table.holds[car] == {(S.VIP_EXCLUSIVE, "priority:vip"),
                                       (S.MAINTENANCE, "test:isolated")}
    medical = p.request_medical(SECURITY, 20, 25)
    assert medical.ok and medical.car_id != car
    assert (S.VIP_EXCLUSIVE, "priority:medical") in safety.table.holds[medical.car_id]


@pytest.mark.parametrize("replacement", ["medical", "send_to_lobby"])
def test_preemption_hands_the_hold_over_without_a_gap(replacement):
    ctx, p, safety, _sim = make()
    car = p.claim_call(vip_call(origin=20, destination=25))
    ctx.clock.run_until(3)
    changes = len(ctx.bus.of_type(E.CarStateChanged))
    out = (p.request_medical(SECURITY, 20, 25) if replacement == "medical"
           else p.send_to_lobby(SECURITY, car))
    assert out.ok and out.car_id == car
    assert safety.table.holds[car] == {(S.VIP_EXCLUSIVE, f"priority:{replacement}")}
    assert len(ctx.bus.of_type(E.CarStateChanged)) == changes     # never left VIP_EXCLUSIVE
    assert p.finish_trip(SECURITY, out.ref).ok
    assert safety.state(car) is S.GROUP_SERVICE and safety.table.holds[car] == set()
    kinds = [(e.kind, e.cancelled) for e in ctx.bus.of_type(E.PriorityTripEnded)]
    assert kinds[0] == ("vip", True) and len(kinds) == 2
