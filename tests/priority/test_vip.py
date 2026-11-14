"""FR-70 / FR-71 / FR-72 VIP exclusive trips, with protocol doubles (conftest.py)."""
from ems.contracts.events import (CallAssigned, CallCancelled, CallReassigned, CallRequested, ControllerFault, ControllerFaultCleared,
                                  DoorsOpened, EmsServerDown, EmsServerUp, FireAlarm,
                                  PassengerAlighted, PassengerBoarded, PriorityTripEnded,
                                  PriorityTripStarted)
from ems.contracts.types import CallPriority, CarState

from .conftest import EMPLOYEE, FM


def _vip_requests(env, car):
    return [x for x in env.states.log if x[0] == "request" and x[1] == car
            and x[2] is CarState.VIP_EXCLUSIVE]


def test_non_vip_calls_are_not_claimed(env):
    assert env.p.claim_call(env.call(priority=CallPriority.NORMAL)) is None
    assert env.p.claim_call(env.call(priority=CallPriority.ACCESSIBLE)) is None
    assert env.controllers.log == [] and env.states.log == []


def test_vip_trip_start_board_and_delivery(env):
    call = env.call(origin=1, destination=20)
    car = env.p.claim_call(call)
    assert car == "E"                                   # all HIGH cars at L1, empty: E first
    assert env.states.states["E"] is CarState.VIP_EXCLUSIVE
    assert [x[0] for x in env.controllers.sent(car="E")] == ["close_to_new_calls", "express"]
    assert env.controllers.sent("express", "E")[0][2] == (1, False)
    assert env.dispatcher.reassigned == []          # nobody waits for E: nothing to move
    started = env.events(PriorityTripStarted)
    assert [(e.kind, e.car_id, e.origin, e.destination) for e in started] == [("vip", "E", 1, 20)]
    assert env.p.vip_starts == [0.0]

    env.publish(PassengerBoarded(env.now(), call.call_id, "E"))
    assert env.controllers.sent("express", "E")[-1][2] == (20, False)
    env.publish(PassengerAlighted(env.now(), call.call_id, "E", 20))
    ended = env.events(PriorityTripEnded)
    assert [(e.kind, e.car_id, e.cancelled) for e in ended] == [("vip", "E", False)]
    assert env.states.states["E"] is CarState.GROUP_SERVICE
    assert env.controllers.sent("close_to_new_calls", "E")[-1][2] == (False,)
    assert env.p.holds == {} and env.p.trips == {}


def test_duplicate_claim_is_idempotent(env):
    call = env.call()
    assert env.p.claim_call(call) == env.p.claim_call(call) == "E"
    assert len(env.events(PriorityTripStarted)) == 1 and len(env.p.vip_starts) == 1


def test_one_exclusive_car_per_group(env):
    assert env.p.claim_call(env.call(group="HIGH")) == "E"
    second = env.call(group="HIGH")
    assert env.p.claim_call(second) is None
    assert env.p.decisions[-1]["reason"] == "group_exclusive_cap"
    assert env.p.claim_call(env.call(group="LOW", destination=9)) == "A"   # other group is free


def _finish(env, call, car):
    env.publish(PassengerBoarded(env.now(), call.call_id, car))
    env.publish(PassengerAlighted(env.now(), call.call_id, car, call.destination))


def test_six_starts_per_rolling_hour_with_exact_boundary(env):
    first = None
    for i in range(6):
        env.run_to(i * 100.0)
        call = env.call()
        car = env.p.claim_call(call)
        assert car is not None
        first = env.now() if first is None else first
        _finish(env, call, car)
    env.run_to(first + 3599.0)
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "hourly_cap"
    env.run_to(first + 3600.0)                          # the first start leaves the window
    assert env.p.claim_call(env.call()) is not None
    assert env.p.vip_starts_in_last_hour() == 6


def test_refused_state_tries_the_next_car_and_counts_nothing_when_all_refuse(env):
    env.states.refuse.add(("E", CarState.VIP_EXCLUSIVE))
    assert env.p.claim_call(env.call()) == "F"
    env2_refuse = {(c, CarState.VIP_EXCLUSIVE) for c in "EFGH"}
    env.states.refuse |= env2_refuse
    env.p.end_trip(next(iter(env.p.trips.values())), True, "test")
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "refused" and len(env.p.vip_starts) == 1


def test_controller_refusals_roll_back_and_do_not_use_quota(env):
    env.controllers.refuse.add(("close_to_new_calls", "E"))     # state granted, flag refused
    env.controllers.refuse.add(("express", "F"))                # flag ok, express refused
    car = env.p.claim_call(env.call())
    assert car == "G"
    assert env.states.states["E"] is CarState.GROUP_SERVICE      # released again
    assert env.states.states["F"] is CarState.GROUP_SERVICE
    assert env.controllers.sent("close_to_new_calls", "F")[-1][2] == (False,)   # undone
    assert set(env.p.holds) == {"G"} and len(env.p.vip_starts) == 1


def test_ceiling_check_skips_cars_and_refuses_when_every_car_breaches(env):
    env.dispatcher.breach = {"E"}
    assert env.p.claim_call(env.call()) == "F"
    assert ("E" in [c[1] for c in env.p.ceiling_checks])
    env.p.end_trip(next(iter(env.p.trips.values())), True, "test")
    env.dispatcher.breach = {"E", "F", "G", "H"}
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "ceiling"


def test_no_healthy_car(env):
    for c in "EFGH":
        env.controllers.put(c, fault_code="F-1")
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "no_car" and env.states.log == []


def test_boarding_another_car_or_cancellation_ends_the_trip(env):
    a = env.call()
    env.p.claim_call(a)
    env.publish(PassengerBoarded(env.now(), a.call_id, "F"))
    assert env.events(PriorityTripEnded)[-1].cancelled and env.p.holds == {}
    b = env.call()
    car = env.p.claim_call(b)
    env.publish(CallCancelled(env.now(), b.call_id, "user"))
    assert len(env.events(PriorityTripEnded)) == 2 and car not in env.p.holds


def test_not_boarded_after_one_door_cycle_and_late_callback_is_ignored(env):
    a = env.call()
    car = env.p.claim_call(a)
    env.publish(DoorsOpened(env.now(), car, 1))
    env.run_to(10.0)
    env.publish(CallCancelled(env.now(), a.call_id, "user"))      # ends the trip first
    env.run_to(100.0)                                              # the door-cycle callback fires
    assert len(env.events(PriorityTripEnded)) == 1
    b = env.call()
    car = env.p.claim_call(b)
    env.publish(DoorsOpened(env.now(), car, 1))
    env.run_to(env.now() + 21 + 2)                                 # 21 persons x 1 s + 2 s close
    assert env.p.finished[-1].end_reason == "not_boarded_in_door_cycle"
    assert car not in env.p.holds


def test_fault_ends_the_trip_without_commands_and_reopens_after_recovery(env):
    a = env.call()
    car = env.p.claim_call(a)
    n = len(env.controllers.log)
    env.controllers.put(car, fault_code="F-9")
    env.publish(ControllerFault(env.now(), car, "F-9", False, False))
    assert env.controllers.log[n:] == []                           # nothing sent to the faulty car
    assert env.events(PriorityTripEnded)[-1].cancelled
    assert env.states.states[car] is CarState.GROUP_SERVICE and car in env.p.reopen
    env.controllers.put(car, fault_code=None)
    env.publish(ControllerFaultCleared(env.now(), car))
    assert env.controllers.sent("close_to_new_calls", car)[-1][2] == (False,)
    assert env.p.reopen == {}


def test_fire_cancels_trips_without_commands_and_blocks_new_ones(env):
    car = env.p.claim_call(env.call())
    n = len(env.controllers.log)
    env.publish(FireAlarm(env.now()))
    assert env.controllers.log[n:] == [] and env.p.holds == {}
    assert env.p.finished[-1].end_reason == "emergency:fire"
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "emergency" and car in env.p.reopen


def test_ems_down_blocks_new_trips_until_takeover(env):
    env.publish(EmsServerDown(env.now(), "primary"))
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "ems_down"
    env.publish(EmsServerUp(env.now(), "standby", 8.0))
    assert env.p.claim_call(env.call()) == "E"


def test_fr70_quota_and_revoke_need_the_fm(env):
    assert env.p.set_vip_quota(EMPLOYEE, "vip-card-1", 1).reason == "not_authorized"
    assert env.p.set_vip_quota(FM, "vip-card-1", 1).ok
    a = env.call()
    car = env.p.claim_call(a)
    _finish(env, a, car)
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "vip_quota"
    assert env.p.claim_call(env.call(credential="vip-card-2")) is not None
    env.p.end_trip(next(iter(env.p.trips.values())), True, "test")
    assert env.p.revoke_vip(FM, "vip-card-2").ok
    assert env.p.claim_call(env.call(credential="vip-card-2")) is None
    assert env.p.decisions[-1]["reason"] == "vip_revoked"
    assert all("vip-card" not in str(a) for a in env.p.audit)   # no credential in the audit


def _assign(env, call_id, car, origin=1, group="HIGH"):
    call = env.call(origin=origin, group=group, priority=CallPriority.NORMAL, call_id=call_id)
    env.publish(CallRequested(env.now(), call))
    env.publish(CallAssigned(env.now(), call_id, car, 3.0))
    return call


def test_exclusive_vip_needs_waiting_calls_moved_away_first(env):
    """Regression A3: the stub dispatcher moves nothing, so a car with a waiting call must not
    become 'exclusive' (that passenger would board the VIP car)."""
    _assign(env, "w1", "E")
    car = env.p.claim_call(env.call())
    assert car == "F"                     # E is not "empty" (someone waits for it): F first
    assert _vip_requests(env, "E") == [] and env.dispatcher.reassigned == []
    assert [d["reason"] for d in env.p.decisions] == ["ok"]


def test_all_cars_busy_and_nothing_moved_declines_without_using_quota(env):
    for i, c in enumerate("EFGH"):
        _assign(env, f"w{i}", c)
    assert env.p.claim_call(env.call()) is None
    assert env.p.decisions[-1]["reason"] == "calls_not_moved"
    assert env.p.vip_starts == [] and env.p.holds == {}
    assert all(s is CarState.GROUP_SERVICE for s in env.states.states.values())
    assert not env.controllers.sent("express")


def test_busy_car_is_used_when_part2_moves_its_calls(env):
    for i, c in enumerate("EFGH"):
        _assign(env, f"w{i}", c)
    env.dispatcher.reassign_calls_of = lambda car, reason: [
        c for c, k in env.p.assigned.items() if k == car]
    assert env.p.claim_call(env.call()) == "E"


def test_reassigned_waiting_call_frees_the_car(env):
    _assign(env, "w1", "E")
    env.publish(CallReassigned(env.now(), "w1", "E", "G", "test"))
    assert env.p.pending_calls_of("E") == [] and env.p.pending_calls_of("G") == ["w1"]
    assert env.p.claim_call(env.call()) == "E"


def test_simultaneous_vip_calls_same_timestamp(env):
    a = env.p.claim_call(env.call(group="HIGH"))
    b = env.p.claim_call(env.call(group="LOW", destination=9))
    c = env.p.claim_call(env.call(group="HIGH"))
    assert a and b and c is None and len(env.p.vip_starts) == 2
    assert {e.car_id for e in env.events(PriorityTripStarted)} == {a, b}


def test_unboarded_vip_trip_end_re_registers_the_pick_up_stop(env):
    """Regression A1: the call was assigned to the car on Part 5's word; when the exclusive trip
    ends before boarding, the car must still come for it."""
    vip = env.call(origin=20, destination=1)
    car = env.p.claim_call(vip)
    env.publish(CallAssigned(env.now(), vip.call_id, car, 30.0))     # Part 2 assigns it
    env.controllers.put(car, fault_code="F-1")
    env.publish(ControllerFault(env.now(), car, "F-1", False, False))
    assert not env.controllers.sent("register_stop", car, accepted=None)   # faulted: wait
    env.controllers.put(car, fault_code=None)
    env.publish(ControllerFaultCleared(env.now(), car))
    sent = [x[0] for x in env.controllers.sent(car=car)][-2:]
    assert sent == ["close_to_new_calls", "register_stop"]               # reopen, then stop
    assert env.controllers.sent("register_stop", car)[-1][2] == (20,)


def test_cancelled_or_reassigned_vip_call_is_not_re_registered(env):
    a = env.call()
    car = env.p.claim_call(a)
    env.publish(CallAssigned(env.now(), a.call_id, car, 3.0))
    env.publish(CallCancelled(env.now(), a.call_id, "user"))
    b = env.call()
    car = env.p.claim_call(b)
    env.publish(CallAssigned(env.now(), b.call_id, car, 3.0))
    env.publish(CallReassigned(env.now(), b.call_id, car, "H", "fault"))
    assert not env.controllers.sent("register_stop", accepted=None)
