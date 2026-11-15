"""FR-127 medical express, FR-128 send to lobby, FR-08 voice feedback (protocol doubles)."""
from ems.contracts.events import (AlertRaised, CallAssigned, CallReassigned, CallRequested,
                                  ControllerFault, DoorsOpened, PanelMessage, PassengerBoarded,
                                  PriorityTripEnded, PriorityTripStarted)
from ems.contracts.types import CallPriority, CarState, DoorState

from .conftest import EMPLOYEE, FM, SECURITY


def test_only_security_may_request_medical(env):
    assert env.p.request_medical(EMPLOYEE, 20, 1).reason == "not_authorized"
    assert env.p.request_medical(FM, 20, 1).reason == "not_authorized"
    env.access.down = True                                  # ACS timeout: no roles
    assert env.p.request_medical(SECURITY, 20, 1).reason == "not_authorized"
    assert env.controllers.log == [] and env.states.log == []


def test_medical_trip_full_cycle(env):
    env.controllers.put("E", floor=1, passengers=4)          # riders: least preferred
    env.controllers.put("F", floor=3)                        # 17 floors away
    env.controllers.put("G", floor=12)                       # 8 floors away
    env.controllers.put("H", floor=30)                       # 10 floors away
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.ok and out.car_id == "G"                      # empty, fastest to L20
    assert env.states.states["G"] is CarState.VIP_EXCLUSIVE
    assert env.controllers.sent("express", "G")[-1][2] == (20, False)   # go first ...
    assert env.events(PriorityTripStarted)[-1].kind == "medical"
    assert env.p.medical_depart(SECURITY, out.ref).reason == "not_at_origin"
    env.publish(DoorsOpened(env.now(), "G", 20))
    assert env.controllers.sent("express", "G")[-1][2] == (20, True)    # ... hold on arrival
    assert env.p.medical_depart(SECURITY, out.ref).ok
    assert env.controllers.sent("express", "G")[-1][2] == (1, False)
    env.publish(DoorsOpened(env.now(), "G", 1))
    assert env.controllers.sent("express", "G")[-1][2] == (1, True)
    env.controllers.put("G", floor=1)
    fin = env.p.finish_trip(SECURITY, out.ref)
    assert fin.ok and fin.reason == "ok"
    assert env.events(PriorityTripEnded)[-1].cancelled is False
    assert env.controllers.sent("express", "G")[-1][2] == (1, False)    # doors close normally
    assert env.controllers.sent("close_to_new_calls", "G")[-1][2] == (False,)
    assert env.states.states["G"] is CarState.GROUP_SERVICE
    assert not any(x[0] == "cancel_stop" for x in env.controllers.log)  # riders keep their stops


def test_car_already_open_at_origin_counts_as_arrived(env):
    env.controllers.put("E", floor=20, doors=DoorState.OPEN)
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.car_id == "E"
    assert env.controllers.sent("express", "E")[-1][2] == (20, True)    # held where it is
    assert env.p.medical_depart(SECURITY, out.ref).ok


def test_medical_preempts_vip_in_the_same_group(env):
    vip = env.call(group="HIGH")
    vip_car = env.p.claim_call(vip)                          # E
    env.publish(PassengerBoarded(env.now(), vip.call_id, vip_car))
    env.controllers.put(vip_car, passengers=1)               # the VIP is on board
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.ok and out.car_id != vip_car
    ended = env.events(PriorityTripEnded)
    assert [(e.kind, e.car_id, e.cancelled) for e in ended] == [("vip", vip_car, True)]
    assert env.p.finished[-1].end_reason == "medical_preempt"
    assert env.states.states[vip_car] is CarState.GROUP_SERVICE
    assert len(env.p.exclusive_in_group("HIGH")) == 1        # FR-72 still one per group
    # the VIP rider's own stop is untouched: no cancel_stop and no new express to that car
    assert not env.controllers.sent("cancel_stop", vip_car)


def test_medical_may_take_the_vip_car_itself(env):
    for c in "FGH":
        env.controllers.put(c, fault_code="F-1")
    vip = env.call(group="HIGH")
    env.p.claim_call(vip)
    n_requests = len(env.states.log)
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.ok and out.car_id == "E"
    assert env.p.holds["E"].kind == "medical"
    assert env.states.log[n_requests:] == []                 # hold converted, no new request
    assert env.states.states["E"] is CarState.VIP_EXCLUSIVE


def test_no_eligible_car_alerts_security(env):
    for c in "EFGH":
        env.controllers.put(c, fault_code="F-1")
    env.controllers.put("S", fault_code="F-1")               # service car also serves L20/L1
    out = env.p.request_medical(SECURITY, 20, 1)
    assert not out.ok and out.reason == "no_eligible_car"
    alert = env.events(AlertRaised)[-1]
    assert alert.kind == "medical_no_car" and alert.recipients == ["security"]
    assert alert.severity == "critical"


def test_medical_car_lost_before_pick_up_is_replaced(env):
    out = env.p.request_medical(SECURITY, 20, 1)
    env.controllers.put(out.car_id, fault_code="F-2")
    env.publish(ControllerFault(env.now(), out.car_id, "F-2", False, False))
    again = [t for t in env.p.trips.values() if t.kind == "medical"]
    assert len(again) == 1 and again[0].car_id != out.car_id
    lost = [a for a in env.events(AlertRaised) if a.kind == "medical_car_lost"]
    assert len(lost) == 1 and lost[0].recipients == ["security"]


def test_finish_before_destination_is_a_cancel(env):
    out = env.p.request_medical(SECURITY, 20, 1)
    fin = env.p.finish_trip(SECURITY, out.ref)
    assert fin.ok and fin.reason == "cancelled"
    assert env.events(PriorityTripEnded)[-1].cancelled
    assert env.p.finish_trip(SECURITY, out.ref).reason == "unknown_trip"    # idempotent


def test_send_to_lobby_holds_doors_and_keeps_riders(env):
    env.controllers.put("B", floor=9, passengers=3)
    assert env.p.send_to_lobby(EMPLOYEE, "B").reason == "not_authorized"
    out = env.p.send_to_lobby(SECURITY, "B")
    assert out.ok
    assert env.controllers.sent("express", "B")[-1][2] == (1, False)
    started = env.events(PriorityTripStarted)[-1]
    assert (started.kind, started.origin, started.destination) == ("send_to_lobby", 9, 1)
    assert not env.controllers.sent("cancel_stop")
    env.publish(DoorsOpened(env.now(), "B", 1))
    assert env.controllers.sent("express", "B")[-1][2] == (1, True)     # doors held at L1
    assert env.p.finish_trip(SECURITY, out.ref).reason == "ok"
    assert env.states.states["B"] is CarState.GROUP_SERVICE


def test_send_to_lobby_refuses_a_faulted_or_held_car(env):
    env.controllers.put("B", fault_code="F-3")
    assert env.p.send_to_lobby(SECURITY, "B").reason == "car_unavailable"
    assert env.p.send_to_lobby(SECURITY, "Z").reason == "unknown_car"
    env.states.refuse.add(("C", CarState.VIP_EXCLUSIVE))
    assert env.p.send_to_lobby(SECURITY, "C").reason == "state_refused"
    assert env.controllers.sent(car="C", accepted=None) == []


def test_accessible_voice_feedback_once_per_call_and_car(env):
    call = env.call(origin=1, destination=7, group="LOW", priority=CallPriority.ACCESSIBLE)
    env.publish(CallRequested(env.now(), call))
    env.publish(CallAssigned(env.now(), call.call_id, "A", 3.0))
    env.publish(CallAssigned(env.now(), call.call_id, "A", 3.0))         # duplicate event
    env.publish(CallReassigned(env.now(), call.call_id, "A", "B", "fault"))
    msgs = env.events(PanelMessage)
    assert len(msgs) == 2 and all(m.voice and m.target == "panel-L1" for m in msgs)
    assert "Car A" in msgs[0].text_en and "10 seconds" in msgs[0].text_en
    assert "Thang B" in msgs[1].text_vi
    normal = env.call(priority=CallPriority.NORMAL)
    env.publish(CallRequested(env.now(), normal))
    env.publish(CallAssigned(env.now(), normal.call_id, "E", 3.0))
    assert len(env.events(PanelMessage)) == 2


def test_medical_preempts_vip_of_the_served_group_even_on_the_service_car(env):
    for c in "ABCDEFGH":
        if c != "C":
            env.controllers.put(c, fault_code="F-1")
    vip = env.call(group="LOW", destination=9)
    assert env.p.claim_call(vip) == "C"
    env.controllers.put("C", passengers=1)
    out = env.p.request_medical(SECURITY, 9, 1)               # L9 -> L1: LOW and SERVICE serve it
    assert out.ok and out.car_id == "S"
    assert env.p.finished[-1].end_reason == "medical_preempt"


def test_medical_over_unboarded_vip_keeps_the_vip_call(env):
    """Regression A1 (conversion): the VIP car becomes the medical car before the VIP boards;
    after the medical trip the car registers the VIP pick-up again."""
    for c in "FGH":
        env.controllers.put(c, fault_code="F-1")
    env.controllers.put("S", fault_code="F-1")
    vip = env.call(origin=20, destination=1)
    car = env.p.claim_call(vip)
    env.publish(CallAssigned(env.now(), vip.call_id, car, 30.0))
    out = env.p.request_medical(SECURITY, 25, 1)
    assert out.car_id == car and not env.controllers.sent("register_stop", accepted=None)
    env.publish(DoorsOpened(env.now(), car, 25))
    env.p.medical_depart(SECURITY, out.ref)
    env.publish(DoorsOpened(env.now(), car, 1))
    env.p.finish_trip(SECURITY, out.ref)
    assert [x[0] for x in env.controllers.sent(car=car)][-2:] == ["close_to_new_calls",
                                                                  "register_stop"]


def test_medical_prefers_a_car_nobody_waits_for_at_the_medical_floor(env):
    from ems.contracts.events import CallAssigned as CA, CallRequested as CR
    waiting = env.call(origin=20, group="HIGH", priority=CallPriority.NORMAL, call_id="w")
    env.publish(CR(env.now(), waiting))
    env.publish(CA(env.now(), "w", "E", 3.0))
    out = env.p.request_medical(SECURITY, 20, 1)
    assert out.car_id == "F"                             # E would take the waiting person
