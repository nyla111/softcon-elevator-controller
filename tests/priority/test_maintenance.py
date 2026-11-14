"""FR-30 fault alerts, FR-31 counters, FR-32 maintenance in/out, FR-33 FM approval."""
from ems.contracts import config
from ems.contracts.events import (AlertRaised, CarStatusUpdated, ControllerFault,
                                  ControllerFaultCleared, DoorsOpened)
from ems.contracts.types import CarState, Mode

from .conftest import EMPLOYEE, FM, TECH


def _alerts(env, kind):
    return [a for a in env.events(AlertRaised) if a.kind == kind]


def test_one_fault_alert_per_episode_to_contractor_and_fm(env):
    env.publish(ControllerFault(env.now(), "A", "F-7", True, False))
    env.publish(ControllerFault(env.now(), "A", "F-7", True, False))     # repeated report
    alerts = _alerts(env, "fault")
    assert len(alerts) == 1 and alerts[0].recipients == ["contractor", "fm"]
    assert "between floors" in alerts[0].message and alerts[0].car_id == "A"
    env.publish(ControllerFaultCleared(env.now(), "A"))
    assert len(_alerts(env, "fault_cleared")) == 1
    env.publish(ControllerFault(env.now(), "A", "F-8", False, True))
    assert len(_alerts(env, "fault")) == 2


def _status(env, car, floor, position=None):
    st = env.controllers.status(car)
    st.floor, st.position = floor, float(floor if position is None else position)
    env.publish(CarStatusUpdated(env.now(), st))


def test_usage_counters_come_from_telemetry_not_ticks(env):
    for _ in range(5):
        _status(env, "A", 1)                                  # standing still: nothing counted
    assert env.p.maintenance.usage("A")["total"] == {"door_cycles": 0, "floors_travelled": 0,
                                                     "starts": 0}
    _status(env, "A", 1, 1.6)                                  # starts moving
    _status(env, "A", 2, 2.3)
    _status(env, "A", 3)
    _status(env, "A", 3)                                       # stopped
    _status(env, "A", 3, 2.4)                                  # second start, going down
    _status(env, "A", -1, -1.0)                                # L3 -> B1 = 3 floors (no L0)
    env.publish(DoorsOpened(env.now(), "A", -1))
    assert env.p.maintenance.usage("A")["total"] == {"door_cycles": 1, "floors_travelled": 5,
                                                     "starts": 2}


def test_service_reminder_once_at_the_fm_threshold_and_reset_after_service(env):
    m = env.p.maintenance
    assert m.set_service_threshold(EMPLOYEE, "door_cycles", 2).reason == "not_authorized"
    assert m.set_service_threshold(FM, "bogus", 2).reason == "invalid_threshold"
    assert m.set_service_threshold(FM, "door_cycles", 2).ok
    for _ in range(4):
        env.publish(DoorsOpened(env.now(), "B", 1))
    assert len(_alerts(env, "service_due")) == 1
    assert m.request_maintenance(TECH, "B").ok and m.exit_maintenance(TECH, "B").ok
    assert m.usage("B")["since_service"]["door_cycles"] == 0
    assert m.usage("B")["total"]["door_cycles"] == 4
    for _ in range(2):
        env.publish(DoorsOpened(env.now(), "B", 1))
    assert len(_alerts(env, "service_due")) == 2


def test_no_reminder_without_a_threshold(env):
    for _ in range(50):
        env.publish(DoorsOpened(env.now(), "B", 1))
    assert _alerts(env, "service_due") == []


def test_maintenance_in_and_out_by_a_technician(env):
    m = env.p.maintenance
    assert m.request_maintenance(EMPLOYEE, "C").reason == "not_authorized"
    out = m.request_maintenance(TECH, "C")
    assert out.ok and env.states.states["C"] is CarState.MAINTENANCE
    assert env.controllers.sent("close_to_new_calls", "C")[-1][2] == (True,)
    assert ("C", "maintenance") in env.dispatcher.reassigned
    assert m.request_maintenance(TECH, "C").reason == "car_held:maintenance"
    assert m.exit_maintenance(EMPLOYEE, "C").reason == "not_authorized"
    assert m.exit_maintenance(TECH, "D").reason == "not_in_maintenance"
    assert m.exit_maintenance(TECH, "C").ok
    assert env.states.states["C"] is CarState.GROUP_SERVICE
    assert env.controllers.sent("close_to_new_calls", "C")[-1][2] == (False,)


def test_faulted_car_enters_maintenance_without_controller_commands(env):
    env.controllers.put("D", fault_code="F-1")
    assert env.p.maintenance.request_maintenance(TECH, "D").ok
    assert env.controllers.sent(car="D", accepted=None) == []
    env.states.states["D"] = CarState.MAINTENANCE
    assert env.p.maintenance.exit_maintenance(TECH, "D").ok
    assert "D" in env.p.reopen                       # re-open is sent once the car recovers


def test_refusals_leave_nothing_held(env):
    m = env.p.maintenance
    env.states.refuse.add(("A", CarState.MAINTENANCE))
    assert m.request_maintenance(TECH, "A").reason == "state_refused"
    env.controllers.refuse.add(("close_to_new_calls", "B"))
    assert m.request_maintenance(TECH, "B").reason == "controller_refused"
    assert env.states.states["B"] is CarState.GROUP_SERVICE and env.p.holds == {}


def _second_car_in_rush(env):
    env.dispatcher.mode = Mode.RUSH
    env.controllers.put("A", fault_code="F-1")       # first car out (a real fault)
    return env.p.maintenance.request_maintenance(TECH, "B")


def test_rush_second_removal_needs_fm_approval(env):
    out = _second_car_in_rush(env)
    assert not out.ok and out.reason == "approval_required" and out.ref
    alert = _alerts(env, "removal_approval")[-1]
    assert alert.recipients == ["fm"] and alert.car_id == "B"
    assert env.states.states["B"] is CarState.GROUP_SERVICE
    again = env.p.maintenance.request_maintenance(TECH, "B")        # duplicate request
    assert again.reason == "approval_required" and again.ref == out.ref
    assert len(_alerts(env, "removal_approval")) == 1
    assert env.p.maintenance.approve_removal(EMPLOYEE, out.ref).reason == "not_authorized"
    env.run_to(599.0)
    ok = env.p.maintenance.approve_removal(FM, out.ref)
    assert ok.ok and env.states.states["B"] is CarState.MAINTENANCE
    assert env.p.maintenance.approve_removal(FM, out.ref).reason == "entered"


def test_no_approval_needed_outside_rush_or_for_the_first_car(env):
    env.dispatcher.mode = Mode.BALANCED
    env.controllers.put("A", fault_code="F-1")
    assert env.p.maintenance.request_maintenance(TECH, "B").ok
    env.dispatcher.mode = Mode.RUSH
    assert env.p.maintenance.request_maintenance(TECH, "E").ok        # first car out in HIGH


def test_request_expires_after_600_s_and_late_approval_is_refused(env):
    out = _second_car_in_rush(env)
    env.run_to(config.SECOND_CAR_APPROVAL_TIMEOUT_S - 0.1)
    assert env.p.maintenance.requests[out.ref].status == "pending"
    env.run_to(config.SECOND_CAR_APPROVAL_TIMEOUT_S)
    assert env.p.maintenance.requests[out.ref].status == "expired"
    assert _alerts(env, "removal_expired")[-1].t == config.SECOND_CAR_APPROVAL_TIMEOUT_S
    late = env.p.maintenance.approve_removal(FM, out.ref)
    assert not late.ok and late.reason == "expired"
    assert not [x for x in env.states.log if x[1] == "B"]


def test_deny_and_approved_but_refused(env):
    out = _second_car_in_rush(env)
    assert env.p.maintenance.deny_removal(FM, out.ref).reason == "denied"
    assert env.p.maintenance.approve_removal(FM, out.ref).reason == "denied"
    env.states.refuse.add(("C", CarState.MAINTENANCE))
    ask = env.p.maintenance.request_maintenance(TECH, "C")
    res = env.p.maintenance.approve_removal(FM, ask.ref)
    assert not res.ok and res.reason == "state_refused" and env.p.holds == {}
