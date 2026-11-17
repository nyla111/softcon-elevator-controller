"""Integration Test 6: Real Simulator + Real Access."""

from datetime import datetime

from ems.core.app import build
from ems.sim.traffic import TrafficGenerator, PROFILES

from ems.contracts.events import (
    PassengerArrived,
    CallRequested,
    AccessRefused,
    PanelMessage,
)


# ==========================================
# STEP 1: Create system
# ==========================================

def make_system():
    """Start Simulator and Access without Dispatch."""

    ctx = build(
        seed=1,
        parts=["sim", "access"],
        start=datetime(2026, 11, 16, 7, 0),
    )

    return ctx


# ==========================================
# TEST 1: Real traffic -> Access requests
# ==========================================

def test_01_simulator_passengers_reach_access():

    # STEP 1: Initialize Simulator and Access
    ctx = make_system()

    # STEP 2: Generate morning traffic for 60 seconds
    traffic = TrafficGenerator.fixed(
        ctx,
        PROFILES["up_peak"],
        start=0,
        end=60,
    )

    traffic.start()

    # STEP 3: Run the simulation
    ctx.clock.run_until(60)

    # STEP 4: Check passenger arrival events
    passengers = ctx.bus.of_type(PassengerArrived)

    assert len(passengers) > 0

    # STEP 5: Check elevator requests
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) > 0

    # STEP 6: Every request must belong to an arrival
    passenger_ids = {
        event.passenger_id
        for event in passengers
    }

    for event in calls:
        assert event.call.call_id in passenger_ids

    # STEP 7: No passenger should create duplicate calls
    call_ids = [
        event.call.call_id
        for event in calls
    ]

    assert len(call_ids) == len(set(call_ids))


# ==========================================
# TEST 2: Invalid card -> Access refusal
# ==========================================

def test_02_invalid_card_is_refused_by_real_access():

    # STEP 1: Initialize system
    ctx = make_system()

    # STEP 2: Passenger presents an unknown card
    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id="invalid-passenger",
            panel_id="panel-L1",
            origin=1,
            destination=5,
            group="LOW",
            credential_id="unknown-card-123",
            accessible=False,
            vip=False,
        )
    )

    # STEP 3: No elevator request should be created
    calls = ctx.bus.of_type(CallRequested)

    assert calls == []

    # STEP 4: Access must refuse the request
    refusals = ctx.bus.of_type(AccessRefused)

    assert len(refusals) == 1

    # STEP 5: Panel must show a safe message
    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert "Not recognised" in messages[0].text_en
