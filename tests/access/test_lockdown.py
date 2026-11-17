"""FR-42:"""

import pytest

from ems.core.app import build
from ems.contracts.config import LOBBY
from ems.contracts.events import (
    AccessRefused,
    CallReplaced,
    CallRequested,
    PassengerArrived,
)
from ems.contracts.types import Permission, Role


class FakeACS:

    def __init__(self):
        self.offline = False

    def lookup(self, credential_id):
        if self.offline:
            raise TimeoutError("ACS unavailable")

        # Security operator, allowed to visit L5 and L7
        if credential_id == "security-1":
            return Permission(
                credential_id="security-1",
                allowed_floors=frozenset({1, 5, 7}),
                default_floor=5,
                roles=frozenset({Role.SECURITY}),
                valid=True,
            )

        # Ordinary employee
        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({1, 5, 7}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        # Visitor only has permission for L5
        if credential_id == "visitor-1":
            return Permission(
                credential_id="visitor-1",
                allowed_floors=frozenset({1, 5}),
                default_floor=5,
                roles=frozenset({Role.VISITOR}),
                valid=True,
            )

        # Unknown or revoked card
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


def make_system():
    """Create a fresh elevator system for each test."""
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    access = ctx.services["access"]
    return ctx, access


def send_passenger(ctx, credential_id, destination):
    """Simulate a passenger choosing a destination from Lobby."""
    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id="passenger-test",
            panel_id="panel-L1",
            origin=LOBBY,
            destination=destination,
            group="LOW",
            credential_id=credential_id,
            accessible=False,
            vip=False,
        )
    )


# ======================= Lock and unlock floors =======================

def test_01_security_can_lock_floor():
    ctx, access = make_system()

    access.lock_floors("security-1", {5})

    assert access.floor_access.lockdown.locked_floors == {5}


def test_02_employee_cannot_lock_floor():
    ctx, access = make_system()

    with pytest.raises(PermissionError):
        access.lock_floors("employee-1", {5})

    assert access.floor_access.lockdown.locked_floors == set()


def test_03_invalid_card_cannot_unlock_floor():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    with pytest.raises(PermissionError):
        access.unlock_floors("unknown", {5})

    assert access.floor_access.lockdown.locked_floors == {5}


def test_04_lobby_cannot_be_locked():
    ctx, access = make_system()

    with pytest.raises(ValueError):
        access.lock_floors("security-1", {LOBBY})

    assert access.floor_access.lockdown.locked_floors == set()


def test_05_security_can_unlock_floor():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    access.unlock_floors("security-1", {5})

    assert access.floor_access.lockdown.locked_floors == set()


def test_06_unlock_only_one_of_two_locked_floors():
    ctx, access = make_system()
    access.lock_floors("security-1", {5, 7})

    access.unlock_floors("security-1", {5})

    assert access.floor_access.lockdown.locked_floors == {7}


def test_07_cached_security_cannot_lock_floor():
    ctx, access = make_system()

    # The live ACS verifies the security card first.
    access.permission("security-1")

    # Then ACS goes offline, so permission can only come from cache.
    ctx.services["acs"].offline = True

    with pytest.raises(PermissionError):
        access.lock_floors("security-1", {5})

    assert access.floor_access.lockdown.locked_floors == set()


# ======================= Passenger destination check =======================

def test_08_employee_cannot_visit_locked_floor():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    send_passenger(ctx, "employee-1", 5)

    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_09_security_can_visit_locked_floor():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    send_passenger(ctx, "security-1", 5)

    assert len(ctx.bus.of_type(CallRequested)) == 1
    assert ctx.bus.of_type(AccessRefused) == []


def test_10_other_floor_remains_accessible():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    send_passenger(ctx, "employee-1", 7)

    assert len(ctx.bus.of_type(CallRequested)) == 1


def test_11_lobby_remains_accessible_without_valid_card():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    send_passenger(ctx, "unknown", LOBBY)

    assert len(ctx.bus.of_type(CallRequested)) == 1


# ======================= FR-45 card tap and Other Floor =======================

def test_12_employee_tap_cannot_call_locked_default_floor():
    ctx, access = make_system()
    access.lock_floors("security-1", {5})

    call_id = access.tap_card("employee-1", "panel-L1", 1, "LOW")

    assert call_id is None
    assert ctx.bus.of_type(CallRequested) == []


def test_13_employee_cannot_replace_call_with_locked_floor():
    ctx, access = make_system()

    # L5 is allowed when employee taps the card
    call_id = access.tap_card("employee-1", "panel-L1", 1, "LOW")
    assert call_id is not None

    # Security locks L7 before the employee chooses Other Floor
    access.lock_floors("security-1", {7})

    changed = access.choose_other_floor(call_id, 7)

    assert changed is False
    assert ctx.bus.of_type(CallReplaced) == []


def test_14_security_can_choose_locked_other_floor():
    ctx, access = make_system()
    call_id = access.tap_card("security-1", "panel-L1", 1, "LOW")
    access.lock_floors("security-1", {7})

    changed = access.choose_other_floor(call_id, 7)

    assert changed is True
    assert len(ctx.bus.of_type(CallReplaced)) == 1
