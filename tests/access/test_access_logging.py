"""FR-43: Unit tests for Access Logging."""

from types import SimpleNamespace

import pytest

from ems.access.access_logging import AccessLogging
from ems.contracts.events import (
    AccessRefused,
    CallRequested,
    CallReplaced,
)
from ems.contracts.types import (
    Call,
    CallSource,
    Permission,
    Role,
)


# ==============================================
# STEP 1: Fake dependencies
# ==============================================

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


class FakeBus:
    def __init__(self):
        self.listeners = {}

    def subscribe(self, event_type, handler):
        if event_type not in self.listeners:
            self.listeners[event_type] = []

        self.listeners[event_type].append(handler)

    def publish(self, event):
        handlers = self.listeners.get(
            type(event), []
        )

        for handler in handlers:
            handler(event)


class FakeACS:
    def __init__(self):
        self.offline = False
        self.security_cached = False

    def lookup(self, credential_id):

        # Simulate ACS outage
        if self.offline:
            raise TimeoutError("ACS unavailable")

        # Security card
        if credential_id == "security-1":
            return Permission(
                credential_id="security-1",
                allowed_floors=frozenset({1, 5}),
                default_floor=5,
                roles=frozenset({Role.SECURITY}),
                valid=True,
                from_cache=self.security_cached,
            )

        # Employee card
        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        # Unknown card
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


# ==============================================
# STEP 2: Helper functions
# ==============================================

def make_system(retention_days=90):
    """Create a fresh system for each test."""

    clock = FakeClock()
    bus = FakeBus()
    acs = FakeACS()

    ctx = SimpleNamespace(
        clock=clock,
        bus=bus,
        services={"acs": acs},
    )

    logging = AccessLogging(ctx, retention_days)

    return ctx, logging, acs


def make_call(
    ctx, credential_id="employee-1", destination=5
):
    """Create one elevator call."""

    return Call(
        call_id="call-1",
        origin=1,
        destination=destination,
        group="LOW",
        created_at=ctx.clock.now(),
        source=CallSource.DESTINATION_PANEL,
        credential_id=credential_id,
        panel_id="panel-1",
    )


def accept_call(
    ctx, credential_id="employee-1", destination=5
):
    """Simulate a successful access request."""

    call = make_call(
        ctx, credential_id, destination
    )

    ctx.bus.publish(
        CallRequested(
            t=ctx.clock.now(),
            call=call,
        )
    )


def refuse_call(ctx, credential_id="unknown-1"):
    """Simulate a refused access request."""

    ctx.bus.publish(
        AccessRefused(
            t=ctx.clock.now(),
            panel_id="panel-1",
            credential_id=credential_id,
            reason="unauthorized",
        )
    )


# ==============================================
# STEP 3: Test recording
# ==============================================

def test_01_accepted_card_request_is_logged():
    # Employee uses card to access L5.
    # The event must be recorded as ALLOWED.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    logs = logging.get_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "ALLOWED"
    assert logs[0]["credential_id"] == "employee-1"
    assert logs[0]["destination"] == 5


def test_02_refused_card_request_is_logged():
    # Unknown card tries to access a floor.

    ctx, logging, acs = make_system()

    refuse_call(ctx)

    logs = logging.get_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "REFUSED"
    assert logs[0]["reason"] == "unauthorized"


def test_03_refused_request_without_card_is_logged():
    # A passenger without a card is refused.

    ctx, logging, acs = make_system()

    refuse_call(ctx, credential_id=None)

    logs = logging.get_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["credential_id"] is None


def test_04_public_floor_call_without_card_is_not_a_card_use():
    # Public floor trips need no credential.
    # They do not create a credential-use log.

    ctx, logging, acs = make_system()

    accept_call(
        ctx, credential_id=None, destination=1
    )

    assert logging.get_logs("security-1") == []


def test_05_other_floor_replacement_is_logged():
    # Employee changes destination to L7.
    # The replacement must be recorded.

    ctx, logging, acs = make_system()

    new_call = make_call(
        ctx, destination=7
    )

    ctx.bus.publish(
        CallReplaced(
            t=ctx.clock.now(),
            old_call_id="call-1",
            new_call=new_call,
        )
    )

    logs = logging.get_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "REPLACED"
    assert logs[0]["destination"] == 7


def test_06_multiple_attempts_are_saved_in_order():
    # First request succeeds.
    # Second request is refused after 10 seconds.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    ctx.clock.t = 10

    refuse_call(ctx)

    logs = logging.get_logs("security-1")

    assert len(logs) == 2
    assert logs[0]["action"] == "ALLOWED"
    assert logs[1]["action"] == "REFUSED"
    assert logs[0]["time"] == 0
    assert logs[1]["time"] == 10


# ==============================================
# STEP 4: Test who can view logs
# ==============================================

def test_07_security_can_view_logs():
    # Security is authorized to read access logs.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    logs = logging.get_logs("security-1")

    assert len(logs) == 1


def test_08_employee_cannot_view_logs():
    # Regular employees cannot view private logs.

    ctx, logging, acs = make_system()

    with pytest.raises(PermissionError):
        logging.get_logs("employee-1")


def test_09_unknown_card_cannot_view_logs():
    # Invalid card cannot access security logs.

    ctx, logging, acs = make_system()

    with pytest.raises(PermissionError):
        logging.get_logs("unknown-1")


def test_10_cached_security_role_cannot_view_logs():
    # Old cached rights are not enough
    # to view sensitive security records.

    ctx, logging, acs = make_system()

    acs.security_cached = True

    with pytest.raises(PermissionError):
        logging.get_logs("security-1")


def test_11_acs_outage_prevents_reading_private_logs():
    # Security cannot be verified if ACS is down.

    ctx, logging, acs = make_system()

    acs.offline = True

    with pytest.raises(PermissionError):
        logging.get_logs("security-1")


# ==============================================
# STEP 5: Test retention period
# ==============================================

def test_12_log_younger_than_90_days_is_kept():
    # A log younger than 90 days must still exist.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    ctx.clock.t = 90 * 86400 - 1

    assert len(logging.get_logs("security-1")) == 1


def test_13_log_is_deleted_at_90_days():
    # A log expires after 90 days.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    ctx.clock.t = 90 * 86400

    assert logging.get_logs("security-1") == []


def test_14_retention_period_can_be_configured():
    # Admin can configure a different retention
    # period through initial configuration.
    # Here we test a 7-day setting.

    ctx, logging, acs = make_system(
        retention_days=7
    )

    accept_call(ctx)

    ctx.clock.t = 7 * 86400

    assert logging.get_logs("security-1") == []


def test_15_recent_log_survives_when_old_log_is_deleted():
    # First log is 90 days old.
    # Second log is only 40 days old.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    ctx.clock.t = 50 * 86400

    refuse_call(ctx)

    ctx.clock.t = 90 * 86400

    logs = logging.get_logs("security-1")

    assert len(logs) == 1
    assert logs[0]["action"] == "REFUSED"


def test_16_viewer_cannot_edit_saved_logs():
    # Editing the returned list must not
    # change the original stored records.

    ctx, logging, acs = make_system()

    accept_call(ctx)

    logs = logging.get_logs("security-1")
    logs[0]["credential_id"] = "modified"

    original_logs = logging.get_logs("security-1")

    assert original_logs[0]["credential_id"] == "employee-1"
