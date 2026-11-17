
"""Integration Test 3: FR-49 (ACS Fallback) + FR-40 (Floor Access)."""

from ems.core.app import build

from ems.contracts.events import (
    AccessRefused,
    AlertRaised,
    CallRequested,
    PanelMessage,
    PassengerArrived,
)

from ems.contracts.types import Permission, Role


ONE_DAY = 24 * 60 * 60


# ==========================================
# STEP 1: Fake ACS
# ==========================================

class FakeACS:
    """Simulate ACS online, offline and card revocation."""

    def __init__(self):
        self.offline = False
        self.revoked = False

    def lookup(self, credential_id):

        # Simulate ACS connection failure
        if self.offline:
            raise TimeoutError("ACS did not respond")

        # Employee has access to L5 and L7
        if credential_id == "employee-1" and not self.revoked:
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        # Invalid or revoked card
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
    """Create real Access components with a fake ACS."""

    acs = FakeACS()

    ctx = build(
        parts=["access"],
        services={"acs": acs},
    )

    access = ctx.services["access"]

    return ctx, access, acs


def request_floor(
    ctx, destination, passenger_id="p1", origin=1
):
    """Simulate an employee requesting an elevator."""

    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id=passenger_id,
            panel_id="panel-L1",
            origin=origin,
            destination=destination,
            group="LOW",
            credential_id="employee-1",
            accessible=False,
            vip=False,
        )
    )


def acs_alerts(ctx):
    """Return only alerts about ACS connection failure."""

    alerts = ctx.bus.of_type(AlertRaised)

    return [
        alert for alert in alerts
        if alert.kind == "acs_down"
    ]


# ==========================================
# TEST 1: Use valid cache
# ==========================================

def test_01_recent_cache_allows_secured_floor():
    # ACS is online at first.
    # Employee permission is cached.
    # One hour later ACS fails.
    # Employee should still access L5.

    ctx, access, acs = make_system()

    # STEP 1: Verify card while ACS is online
    assert access.permission("employee-1").valid is True

    # STEP 2: Simulate an ACS outage after 1 hour
    acs.offline = True
    ctx.clock.run_until(3600)

    # STEP 3: Employee requests L5
    request_floor(ctx, destination=5)

    # STEP 4: Check results
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 5

    assert ctx.bus.of_type(AccessRefused) == []
    assert len(acs_alerts(ctx)) == 1


# ==========================================
# TEST 2: No cache, secured floor refused
# ==========================================

def test_02_no_cache_refuses_secured_floor():
    # ACS goes offline before any successful lookup.
    # The system has no saved permission.

    ctx, access, acs = make_system()

    # STEP 1: Disconnect ACS
    acs.offline = True

    # STEP 2: Employee requests L5
    request_floor(ctx, destination=5)

    # STEP 3: No elevator call should be created
    assert ctx.bus.of_type(CallRequested) == []

    # STEP 4: Access is refused
    refusals = ctx.bus.of_type(AccessRefused)
    assert len(refusals) == 1

    # STEP 5: Panel shows a safe message
    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert "Not recognised" in messages[0].text_en

    # STEP 6: Security receives an alert
    assert len(acs_alerts(ctx)) == 1


# ==========================================
# TEST 3: Lobby is always accessible
# ==========================================

def test_03_no_cache_still_allows_lobby():
    # ACS is offline and there is no cache.
    # BR-07: Passenger must still reach Lobby.

    ctx, access, acs = make_system()

    # STEP 1: Disconnect ACS
    acs.offline = True

    # STEP 2: Passenger travels from L5 to L1
    request_floor(
        ctx,
        destination=1,
        origin=5,
    )

    # STEP 3: Lobby call should be accepted
    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 1

    assert ctx.bus.of_type(AccessRefused) == []


# ==========================================
# TEST 4: Cache expires after 24 hours
# ==========================================

def test_04_cache_expires_at_24_hours():
    # Permission was saved at t=0.
    # At t=24 hours, that cache is expired.

    ctx, access, acs = make_system()

    # STEP 1: Save valid permission
    access.permission("employee-1")

    # STEP 2: Disconnect ACS
    acs.offline = True

    # STEP 3: Advance simulation by 24 hours
    ctx.clock.run_until(ONE_DAY)

    # STEP 4: Request secured floor
    request_floor(ctx, destination=5)

    # STEP 5: Refuse the request
    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


# ==========================================
# TEST 5: Revoked card cannot use old cache
# ==========================================

def test_05_revocation_prevents_later_offline_access():
    # Employee initially has valid access.
    # ACS then revokes the card.
    # The system must not reuse the old cache.

    ctx, access, acs = make_system()

    # STEP 1: Save valid permission
    access.permission("employee-1")

    # STEP 2: Revoke card while ACS is online
    acs.revoked = True

    # The system receives the revocation
    assert access.permission("employee-1").valid is False

    # STEP 3: ACS goes offline
    acs.offline = True

    # STEP 4: Employee tries L5
    request_floor(ctx, destination=5)

    # STEP 5: Access must be refused
    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


# ==========================================
# TEST 6: Security alerts should not duplicate
# ==========================================

def test_06_acs_alert_is_not_duplicated():
    # Multiple requests during one ACS outage
    # should generate only one outage alert.

    ctx, access, acs = make_system()

    # STEP 1: Disconnect ACS
    acs.offline = True

    # STEP 2: Two passengers request secured floors
    request_floor(
        ctx,
        destination=5,
        passenger_id="p1"
    )

    request_floor(
        ctx,
        destination=7,
        passenger_id="p2"
    )

    # STEP 3: Check that only one alert was sent
    assert len(acs_alerts(ctx)) == 1

    # STEP 4: ACS recovers
    acs.offline = False

    assert access.permission("employee-1").valid is True

    # STEP 5: ACS fails again
    acs.offline = True

    request_floor(
        ctx,
        destination=5,
        passenger_id="p3"
    )

    # A new outage may generate a new alert
    assert len(acs_alerts(ctx)) == 2
