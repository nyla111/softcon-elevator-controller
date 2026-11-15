"""FR-44: Each test checks one public-floor scheduling situation."""

import pytest

from ems.core.app import build
from ems.contracts.config import LOBBY
from ems.contracts.events import AccessRefused, CallReplaced, CallRequested, PassengerArrived
from ems.contracts.types import Permission, Role


class FakeACS:
    """Small stand-in for the ACS used only by these tests."""

    def __init__(self):
        self.offline = False

    def lookup(self, credential_id):
        if self.offline:
            raise TimeoutError("ACS unavailable")

        if credential_id == "fm-1":
            return Permission(
                credential_id="fm-1",
                allowed_floors=frozenset({1, 5, 7}),
                default_floor=5,
                roles=frozenset({Role.FM}),
                valid=True,
            )

        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        if credential_id == "security-1":
            return Permission(
                credential_id="security-1",
                allowed_floors=frozenset({1, 5, 7}),
                default_floor=5,
                roles=frozenset({Role.SECURITY}),
                valid=True,
            )

        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


def make_system():
    """Build a fresh Part 3 system for each test."""
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    return ctx, ctx.services["access"]


def send_passenger(ctx, destination, credential_id=None):
    """Simulate choosing a floor at a Lobby destination panel."""
    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id="passenger-test",
            panel_id="panel-1",
            origin=LOBBY,
            destination=destination,
            group="LOW",
            credential_id=credential_id,
            accessible=False,
            vip=False,
        )
    )


# ============== Public floors change by time ==============

def test_01_lobby_is_public_without_any_schedule():
    ctx, access = make_system()
    assert access.public_floors(ctx.clock.now()) == {LOBBY}


def test_02_fm_can_open_floor_at_start_time():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    ctx.clock.run_until(9 * 3600)
    assert access.public_floors(ctx.clock.now()) == {LOBBY, 5}


def test_03_floor_is_private_before_opening_time():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    ctx.clock.run_until(8 * 3600 + 59 * 60)
    assert access.public_floors(ctx.clock.now()) == {LOBBY}


def test_04_floor_is_private_at_exact_closing_time():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    ctx.clock.run_until(17 * 3600)
    assert access.public_floors(ctx.clock.now()) == {LOBBY}


def test_05_guest_without_card_can_go_to_open_public_floor():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    ctx.clock.run_until(10 * 3600)
    send_passenger(ctx, 5, credential_id=None)
    calls = ctx.bus.of_type(CallRequested)
    assert len(calls) == 1
    assert calls[0].call.destination == 5


def test_06_guest_without_card_cannot_go_after_closing():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    ctx.clock.run_until(18 * 3600)
    send_passenger(ctx, 5, credential_id=None)
    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


# ============== Only FM can change the schedule ==============

def test_07_employee_cannot_set_public_hours():
    ctx, access = make_system()
    with pytest.raises(PermissionError):
        access.set_public_hours("employee-1", {5}, "09:00", "17:00")


def test_08_invalid_card_cannot_set_public_hours():
    ctx, access = make_system()
    with pytest.raises(PermissionError):
        access.set_public_hours("unknown", {5}, "09:00", "17:00")


def test_09_cached_fm_card_cannot_change_schedule():
    ctx, access = make_system()
    access.permission("fm-1")  # Fill FR-49 permission cache.
    ctx.services["acs"].offline = True
    with pytest.raises(PermissionError):
        access.set_public_hours("fm-1", {5}, "09:00", "17:00")


def test_10_invalid_time_is_rejected():
    ctx, access = make_system()
    with pytest.raises(ValueError):
        access.set_public_hours("fm-1", {5}, "25:00", "17:00")


def test_11_zero_floor_is_rejected():
    ctx, access = make_system()
    with pytest.raises(ValueError):
        access.set_public_hours("fm-1", {0}, "09:00", "17:00")


# ============== Schedule management ==============

def test_12_window_can_cross_midnight():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {7}, "22:00", "02:00")
    ctx.clock.run_until(23 * 3600)
    assert 7 in access.public_floors(ctx.clock.now())
    ctx.clock.run_until(25 * 3600)
    assert 7 in access.public_floors(ctx.clock.now())
    ctx.clock.run_until(27 * 3600)
    assert 7 not in access.public_floors(ctx.clock.now())


def test_13_overlapping_windows_combine_floors():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    access.set_public_hours("fm-1", {7}, "10:00", "12:00")
    ctx.clock.run_until(11 * 3600)
    assert access.public_floors(ctx.clock.now()) == {LOBBY, 5, 7}


def test_14_same_time_window_replaces_old_floors():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    access.set_public_hours("fm-1", {7}, "09:00", "17:00")
    ctx.clock.run_until(11 * 3600)
    assert access.public_floors(ctx.clock.now()) == {LOBBY, 7}


def test_15_fm_can_remove_public_hours():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    assert access.remove_public_hours("fm-1", "09:00", "17:00") is True
    ctx.clock.run_until(11 * 3600)
    assert access.public_floors(ctx.clock.now()) == {LOBBY}


# ============== Integration with FR-42 and FR-45 ==============

def test_16_lockdown_blocks_a_public_floor():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")
    access.lock_floors("security-1", {5})
    ctx.clock.run_until(10 * 3600)
    send_passenger(ctx, 5, credential_id=None)
    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_17_other_floor_can_choose_currently_public_floor():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {7}, "09:00", "17:00")
    ctx.clock.run_until(10 * 3600)
    call_id = access.tap_card("employee-1", "panel-1", 1, "LOW")
    assert access.choose_other_floor(call_id, 7) is True
    assert len(ctx.bus.of_type(CallReplaced)) == 1


def test_18_other_floor_rechecks_schedule_after_closing():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {7}, "09:00", "17:00")
    ctx.clock.run_until(17 * 3600 - 1)  # 16:59:59
    call_id = access.tap_card("employee-1", "panel-1", 1, "LOW")
    ctx.clock.run_until(17 * 3600 + 1)  # 17:00:01 (2 sec later)
    assert access.choose_other_floor(call_id, 7) is False
    assert ctx.bus.of_type(CallReplaced) == []


def test_19_schedule_works_when_simulator_starts_at_1630():
    # The simulation does not always begin at midnight.
    from datetime import datetime

    ctx = build(
        parts=["access"],
        start=datetime(2026, 11, 16, 16, 30),
        services={"acs": FakeACS()},
    )
    access = ctx.services["access"]
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")

    assert 5 in access.public_floors(ctx.clock.now())
    ctx.clock.run_until(30 * 60)  # 30 minutes later = 17:00
    assert 5 not in access.public_floors(ctx.clock.now())


def test_20_public_floors_can_query_a_future_simulated_time():
    ctx, access = make_system()
    access.set_public_hours("fm-1", {5}, "09:00", "17:00")

    # Without moving the clock, ask what is public at 10:00.
    assert access.public_floors(10 * 3600) == {LOBBY, 5}
    assert access.public_floors(ctx.clock.now()) == {LOBBY}
