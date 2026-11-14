"""Integration Test 4: FR-44 Public Floors + FR-42 Lockdown."""

from datetime import datetime

from ems.core.app import build
from ems.contracts.events import (
    AccessRefused,
    CallRequested,
    PanelMessage,
    PassengerArrived,
)
from ems.contracts.types import Permission, Role


# ==========================================
# STEP 1: Fake ACS
# ==========================================

class FakeACS:
    """Only the ACS is fake. All Part 3 components are real."""

    def lookup(self, credential_id):

        # Facility Manager can configure public floors
        if credential_id == "fm-1":
            return Permission(
                credential_id="fm-1",
                allowed_floors=frozenset({1, 5}),
                default_floor=5,
                roles=frozenset({Role.FM}),
                valid=True,
            )

        # Security can lock/unlock and access L5
        if credential_id == "security-1":
            return Permission(
                credential_id="security-1",
                allowed_floors=frozenset({1, 5}),
                default_floor=5,
                roles=frozenset({Role.SECURITY}),
                valid=True,
            )

        # Any other card is invalid
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


# ==========================================
# STEP 2: Helper functions
# ==========================================

def make_system():
    """Create a system at 10:00, during public hours."""

    # STEP 1: Build the real Access system
    ctx = build(
        parts=["access"],
        start=datetime(2026, 11, 16, 10, 0),
        services={"acs": FakeACS()},
    )

    access = ctx.services["access"]

    # STEP 2: FM makes L5 public from 09:00 to 17:00
    access.set_public_hours(
        "fm-1",
        {5},
        "09:00",
        "17:00",
    )

    return ctx, access


def request_floor(
    ctx, passenger_id, floor,
    credential_id=None, origin=1
):
    """Simulate a passenger selecting a floor."""

    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id=passenger_id,
            panel_id="panel-L1",
            origin=origin,
            destination=floor,
            group="LOW",
            credential_id=credential_id,
            accessible=False,
            vip=False,
        )
    )


# ==========================================
# TEST 1: Public floor before Lockdown
# ==========================================

def test_01_public_floor_allows_guest_without_card():
    # L5 is public from 09:00 to 17:00.
    # At 10:00, a guest without a card can visit L5.

    ctx, access = make_system()

    # STEP 1: Guest selects L5
    request_floor(ctx, "guest-1", 5)

    # STEP 2: Check that a call was created
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 5

    # STEP 3: No access refusal
    assert ctx.bus.of_type(AccessRefused) == []


# ==========================================
# TEST 2: Lockdown overrides public access
# ==========================================

def test_02_lockdown_blocks_guest_and_creates_one_log():
    # L5 is public, but Security locks the floor.
    # A guest without a card must be refused.

    ctx, access = make_system()

    # STEP 1: Security locks L5
    access.lock_floors("security-1", {5})

    # STEP 2: Guest tries to visit L5
    request_floor(ctx, "guest-1", 5)

    # STEP 3: No elevator call should be created
    assert ctx.bus.of_type(CallRequested) == []

    # STEP 4: One access refusal
    refusals = ctx.bus.of_type(AccessRefused)
    assert len(refusals) == 1

    # STEP 5: Panel shows a safe message
    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert "Not recognised" in messages[0].text_en
    assert "lockdown" not in messages[0].text_en.lower()

    # L5 must not be offered as an available public floor
    assert "5" not in messages[0].text_en

    # STEP 6: FR-43 records exactly one refusal
    logs = access.get_access_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "REFUSED"


# ==========================================
# TEST 3: Security can visit locked floor
# ==========================================

def test_03_security_can_visit_locked_floor():
    # Security has valid access to L5.
    # Lockdown must not block Security.

    ctx, access = make_system()

    # STEP 1: Activate Lockdown
    access.lock_floors("security-1", {5})

    # STEP 2: Security requests L5 using a valid card
    request_floor(
        ctx,
        "security-passenger",
        5,
        "security-1",
    )

    # STEP 3: Check successful request
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 5

    # STEP 4: No refusal
    assert ctx.bus.of_type(AccessRefused) == []


# ==========================================
# TEST 4: Unlock restores public access
# ==========================================

def test_04_unlock_restores_public_access():
    # Security locks and then unlocks L5.
    # Public access should return automatically
    # without changing the original schedule.

    ctx, access = make_system()

    # STEP 1: Lock L5
    access.lock_floors("security-1", {5})

    # STEP 2: First guest is refused
    request_floor(ctx, "guest-1", 5)

    assert len(ctx.bus.of_type(AccessRefused)) == 1
    assert ctx.bus.of_type(CallRequested) == []

    # STEP 3: Security unlocks L5
    access.unlock_floors("security-1", {5})

    # STEP 4: Second guest requests L5
    request_floor(ctx, "guest-2", 5)

    # STEP 5: Public access works again
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.call_id == "guest-2"
    assert calls[0].call.destination == 5


# ==========================================
# TEST 5: Lobby remains accessible
# ==========================================

def test_05_lobby_is_still_accessible():
    # BR-07: Lockdown must never prevent
    # a passenger from returning to the Lobby.

    ctx, access = make_system()

    # STEP 1: Lock L5
    access.lock_floors("security-1", {5})

    # STEP 2: A passenger at L5 wants to return to L1
    request_floor(
        ctx,
        "guest-1",
        floor=1,
        origin=5,
    )

    # STEP 3: Lobby call must be accepted
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 1

    assert ctx.bus.of_type(AccessRefused) == []
