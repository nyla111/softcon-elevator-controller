"""Integration Test 2: FR-40 + FR-43 + FR-46."""

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
    """Simulate credential verification."""

    def lookup(self, credential_id):

        # Security card can read access logs
        if credential_id == "security-1":
            return Permission(
                credential_id="security-1",
                allowed_floors=frozenset({1, 5, 18}),
                default_floor=1,
                roles=frozenset({Role.SECURITY}),
                valid=True,
            )

        # Employee can access L5, but not L18
        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        # Any other credential is invalid
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


# ==========================================
# STEP 2: Build the real Access system
# ==========================================

def make_system():
    """Create a fresh system for each test."""

    ctx = build(
        parts=["access"],
        services={"acs": FakeACS()},
    )

    access = ctx.services["access"]

    return ctx, access


# ==========================================
# STEP 3: Helper to check refusal results
# ==========================================

def check_refusal(ctx, access, expected_card):
    """Check events, panel message and security log."""

    # Check 1: Exactly one refusal
    refusals = ctx.bus.of_type(AccessRefused)

    assert len(refusals) == 1
    assert refusals[0].credential_id == expected_card

    # Check 2: One message appears on the correct panel
    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert messages[0].target == "panel-L1"

    # Check 3: Message contains only safe information
    text = messages[0].text_en

    assert "Not recognised, please see reception" in text
    assert "Public floors: 1" in text

    # Do not reveal secured floor or private refusal reason
    assert "18" not in text
    assert "unauthorized" not in text.lower()

    # Check 4: Refused access must not create an elevator call
    calls = ctx.bus.of_type(CallRequested)

    assert calls == []

    # Check 5: Security sees exactly one REFUSED log
    logs = access.get_access_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "REFUSED"
    assert logs[0]["credential_id"] == expected_card


# ==========================================
# TEST 1: Invalid card at destination panel
# ==========================================

def test_01_invalid_card_through_passenger_event():

    ctx, access = make_system()

    # STEP 1: Passenger uses an invalid card
    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id="p-1",
            panel_id="panel-L1",
            origin=1,
            destination=18,
            group="HIGH",
            credential_id="unknown-card",
            accessible=False,
            vip=False,
        )
    )

    # STEP 2: Check entire refusal flow
    check_refusal(ctx, access, "unknown-card")


# ==========================================
# TEST 2: Valid card, unauthorized floor
# ==========================================

def test_02_valid_card_but_unauthorized_destination():

    ctx, access = make_system()

    # Employee has access to L5 only
    # But tries to travel to L18
    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id="p-2",
            panel_id="panel-L1",
            origin=1,
            destination=18,
            group="HIGH",
            credential_id="employee-1",
            accessible=False,
            vip=False,
        )
    )

    # The request must still be refused
    check_refusal(ctx, access, "employee-1")


# ==========================================
# TEST 3: Invalid card through FR-45
# ==========================================

def test_03_invalid_card_through_tap_card():

    ctx, access = make_system()

    # STEP 1: Tap an invalid card
    call_id = access.tap_card(
        "unknown-card",
        "panel-L1",
        1,
        "LOW",
    )

    # STEP 2: No call should be created
    assert call_id is None

    # STEP 3: Check panel and security log
    check_refusal(ctx, access, "unknown-card")
