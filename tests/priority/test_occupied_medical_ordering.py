from datetime import datetime

import pytest

from ems.contracts import events as E
from ems.core.app import build
from ems.sim.scripted import ScriptedTraffic, loads


def occupied(seed=1):
    ctx = build(seed=seed, start=datetime(2026, 11, 16, 7))
    for car in "BCDEFGHS":
        ctx.get("controllers").inject_fault(car, "scenario:unavailable")
    ScriptedTraffic(ctx, loads("t,origin,destination,card,vip,passenger_id\n"
                              "0,1,15,card-L15-001,true,existing-rider\n")).start()
    ctx.clock.run_until(10)
    assert ctx.get("controllers").status("A").passengers == 1
    return ctx


@pytest.mark.parametrize("seed", range(1, 11))
def test_existing_rider_alights_before_medical_assignment_or_new_express(seed):
    ctx = occupied(seed)
    priority, sim = ctx.get("priority"), ctx.get("controllers")
    out = priority.request_medical("staff-security-1", 8, 1)
    assert out.ok and out.car_id == "A"
    assert out.reason == "awaiting_rider_delivery"
    assert not [c for c in sim.command_log if c[2] == "express" and c[3][0] == 8]
    assert priority.medical_depart("staff-security-1", out.ref).reason == "awaiting_rider_delivery"
    ctx.clock.run_until(150)
    alight = next(e for e in ctx.bus.of_type(E.PassengerAlighted) if e.call_id == "existing-rider")
    start = next(e for e in ctx.bus.of_type(E.PriorityTripStarted) if e.kind == "medical")
    assert alight.floor == 15 and ctx.bus.log.index(alight) < ctx.bus.log.index(start)
    express = [c for c in sim.command_log if c[2] == "express" and c[3][0] == 8]
    assert express and all(c[0] >= alight.t for c in express)
    assert not [e for e in ctx.bus.of_type(E.CallReassigned) if e.call_id == "existing-rider"]
    assert priority.trips[out.ref].phase == "at_origin"
    assert priority.medical_depart("staff-security-1", out.ref).ok
    ctx.clock.run_until(200)
    assert priority.finish_trip("staff-security-1", out.ref).ok


def test_pending_request_can_be_cancelled_without_diverting_rider():
    ctx = occupied()
    p = ctx.get("priority")
    out = p.request_medical("staff-security-1", 8, 1)
    assert p.finish_trip("staff-security-1", out.ref).reason == "cancelled"
    ctx.clock.run_until(100)
    assert not [e for e in ctx.bus.of_type(E.PriorityTripStarted) if e.kind == "medical"]
    assert ctx.get("controllers").passengers["existing-rider"].alighted_at is not None


def test_legitimate_medical_request_notifies_security_within_ten_seconds():
    ctx = build()
    outcome = ctx.get("priority").request_medical("staff-security-1", 8, 1)
    assert outcome.ok
    received = [n for n in ctx.get("notifier").inbox if "medical_request" in n[2]]
    assert len(received) == 1 and received[0][0] <= 10 and received[0][1] == ["security"]


@pytest.mark.parametrize("emergency", ["fire", "power"])
def test_emergency_invalidates_pending_start_without_late_commands(emergency):
    ctx = occupied()
    p = ctx.get("priority")
    assert p.request_medical("staff-security-1", 8, 1).ok
    if emergency == "fire":
        ctx.get("devices").fire.alarm()
    else:
        ctx.get("devices").power.lose_mains(generator_failed=True)
    ctx.clock.run_until(100)
    assert not [e for e in ctx.bus.of_type(E.PriorityTripStarted) if e.kind == "medical"]
    assert not [c for c in ctx.get("controllers").command_log if c[0] >= 10]
