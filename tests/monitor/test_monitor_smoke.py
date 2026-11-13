import pytest

from ems.contracts.events import (AlertRaised, CallAssigned, CallRequested, CameraEvent,
                                  PanelMessage, PassengerAlighted, PassengerBoarded)
from ems.contracts.types import (Call, CallPriority, CallSource, Role)
from ems.core.app import build
from ems.monitor.service import DAY, MonitorService, _nearest_rank


def test_monitor_registers():
    ctx = build(seed=1, parts=["monitor"])
    assert isinstance(ctx.services["reports"], MonitorService)


def make_call(call_id="c1", credential="CARD-SECRET"):
    return Call(call_id, 1, 10, "LOW", 2.0, CallSource.DESTINATION_PANEL,
                CallPriority.NORMAL, credential)


def complete_trip(ctx, call=None, car="A", boarded=12.0, arrived=32.0):
    call = call or make_call()
    ctx.bus.publish(CallRequested(call.created_at, call))
    ctx.bus.publish(CallAssigned(3.0, call.call_id, car, 9.0))
    ctx.bus.publish(PassengerBoarded(boarded, call.call_id, car))
    ctx.bus.publish(PassengerAlighted(arrived, call.call_id, car, call.destination))


def test_fr21_trip_is_passenger_free_and_published():
    ctx = build(seed=1, parts=["monitor"])
    complete_trip(ctx)
    row = ctx.services["reports"].trips[0]
    assert row["wait_s"] == 10.0 and row["ride_s"] == 20.0
    assert not ({"credential_id", "person_id", "passenger_id"} & row.keys())
    assert "CARD-SECRET" not in repr(row)
    assert len(ctx.bus.of_type(__import__("ems.contracts.events", fromlist=["TripCompleted"]).TripCompleted)) == 1


def test_fr22_nearest_rank_and_kpis():
    ctx = build(seed=1, parts=["monitor"])
    svc = ctx.services["reports"]
    for i, wait in enumerate(range(1, 21), 1):
        call = make_call(f"c{i}", f"secret-{i}")
        complete_trip(ctx, call, boarded=call.created_at + wait,
                      arrived=call.created_at + wait + 5)
    kpi = svc.kpis()
    assert kpi["trips"] == 20 and kpi["average_wait_s"] == 10.5
    assert kpi["p95_wait_s"] == 19 and _nearest_rank([], .95) == 0
    assert "run_time_s" in kpi and "estimated_energy_kwh" in kpi


@pytest.mark.parametrize("kind", ["door_held", "bulky_item", "repeated_overload", "smoking",
                                  "vandalism", "lying_still", "tailgating"])
def test_fr90_all_camera_types_are_accepted(kind):
    ctx = build(seed=1, parts=["monitor"])
    ctx.bus.publish(CameraEvent(5, "e1", "A", kind, 2, "clip://e1"))
    assert ctx.services["reports"].incidents["e1"]["kind"] == kind


def test_ver07_camera_queue_safety_and_no_automatic_penalty():
    ctx = build(seed=1, parts=["monitor"])
    svc = ctx.services["reports"]
    call = make_call()
    ctx.bus.publish(CallRequested(2, call))
    ctx.bus.publish(CallAssigned(3, "c1", "A", 1))
    ctx.bus.publish(PassengerBoarded(4, "c1", "A"))
    ctx.bus.publish(CameraEvent(5, "ordinary", "A", "vandalism", 1, "clip://ordinary"))
    assert svc.incidents["ordinary"]["candidate_credentials"] == ["CARD-SECRET"]
    assert svc.incidents["ordinary"]["status"] == "pending"
    reviewed = svc.review_incident("ordinary", "confirmed", "guard", "visual evidence")
    assert reviewed["responsible_credential"] is None  # ambiguous remains unattributed
    assert not any("fine" in repr(x).lower() or "suspend" in repr(x).lower()
                   for x in ctx.bus.log)
    ctx.bus.publish(CameraEvent(6, "safety", "A", "lying_still", 1, "clip://safety"))
    alerts = [x for x in ctx.bus.of_type(AlertRaised) if x.kind == "camera_safety"]
    assert len(alerts) == 1 and alerts[0].recipients == ["security"] and alerts[0].t == 6


def test_fr84_bulky_item_plays_bilingual_voice_once():
    ctx = build(seed=1, parts=["monitor"])
    event = CameraEvent(5, "bulky", "B", "bulky_item", 1, "clip://bulky")
    ctx.bus.publish(event)
    ctx.bus.publish(event)
    messages = ctx.bus.of_type(PanelMessage)
    assert len(messages) == 1 and messages[0].voice
    assert messages[0].text_en and messages[0].text_vi


def test_ver08_camera_retention_boundary_and_configured_confirmed_limit():
    ctx = build(seed=1, parts=["monitor"])
    svc = ctx.services["reports"]
    ctx.bus.publish(CameraEvent(0, "pending", "A", "door_held", 1, "clip://p"))
    ctx.bus.publish(CameraEvent(0, "confirmed", "A", "vandalism", 1, "clip://c"))
    svc.review_incident("confirmed", "confirmed", "guard", "evidence")
    ctx.clock.run_until(30 * DAY - 1)
    assert {"pending", "confirmed"} <= svc.incidents.keys()
    ctx.clock.run_until(30 * DAY)
    assert "pending" not in svc.incidents and "confirmed" in svc.incidents
    svc.update_configuration("confirmed_clip_days", 31, "fm", Role.FM)
    ctx.clock.run_until(31 * DAY)
    assert "confirmed" not in svc.incidents


def test_fr61_configuration_change_has_old_new_user_and_role():
    ctx = build(seed=1, parts=["monitor"])
    svc = ctx.services["reports"]
    svc.update_configuration("public_floors", [1, 2], "alice", Role.FM)
    row = svc.audit_log[-1]
    assert row["user"] == "alice" and row["role"] == "facility_manager"
    assert row["old"] == [1] and row["new"] == [1, 2]
