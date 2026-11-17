"""FR-85: Tests for Courier Access."""

from ems.core.app import build
from ems.contracts.config import LOBBY, LOADING_DOCK
from ems.contracts.events import (
    AccessRefused,
    CallRequested,
    CallReplaced,
    PanelMessage,
    PassengerArrived,
)
from ems.contracts.types import Mode, Permission, Role


class FakeACS:
    def lookup(self, credential_id):

        # Courier card
        if credential_id == "courier-1":
            return Permission(
                credential_id="courier-1",
                allowed_floors=frozenset({
                    LOBBY, LOADING_DOCK, 18
                }),
                default_floor=LOADING_DOCK,
                roles=frozenset({Role.COURIER}),
                valid=True,
            )

        # Employee card
        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({18}),
                default_floor=18,
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


class FakeDispatcher:
    def __init__(self):
        self.mode = Mode.BALANCED

    def current_mode(self, group):
        return self.mode


def make_system():
    """Create a fresh system for each test."""

    dispatcher = FakeDispatcher()

    ctx = build(
        parts=["access"],
        services={
            "acs": FakeACS(),
            "dispatcher": dispatcher,
        },
    )

    access = ctx.services["access"]

    return ctx, access, dispatcher


def send_passenger(
    ctx, card_id, origin, destination, group="LOW"
):
    """Simulate a passenger selecting a destination."""

    event = PassengerArrived(
        t=ctx.clock.now(),
        passenger_id="test-passenger",
        panel_id="panel-1",
        origin=origin,
        destination=destination,
        group=group,
        credential_id=card_id,
        accessible=False,
        vip=False,
    )

    ctx.bus.publish(event)


def test_courier_can_only_access_lobby_and_loading_dock():
    # ACS allows L1, B1 and L18.
    # CourierAccess must remove L18.

    ctx, access, dispatcher = make_system()

    permission = access.permission("courier-1")

    assert permission.allowed_floors == frozenset({
        LOBBY, LOADING_DOCK
    })


def test_courier_cannot_go_to_office_floor():
    # Courier tries to go to L18.
    # System must refuse access.

    ctx, access, dispatcher = make_system()

    send_passenger(ctx, "courier-1", LOBBY, 18)

    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_courier_from_lobby_to_loading_dock_uses_service():
    # Courier goes from Lobby to B1.
    # Elevator group must be SERVICE.

    ctx, access, dispatcher = make_system()

    send_passenger(
        ctx, "courier-1", LOBBY, LOADING_DOCK
    )

    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == LOADING_DOCK
    assert calls[0].call.group == "SERVICE"


def test_courier_from_loading_dock_to_lobby_uses_service():
    # Courier returns from B1 to Lobby.
    # Elevator group must still be SERVICE.

    ctx, access, dispatcher = make_system()

    send_passenger(
        ctx, "courier-1", LOADING_DOCK, LOBBY
    )

    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == LOBBY
    assert calls[0].call.group == "SERVICE"


def test_courier_sees_reception_message_during_rush():
    # During Rush, courier should see
    # a message to deliver at reception.

    ctx, access, dispatcher = make_system()

    dispatcher.mode = Mode.RUSH

    send_passenger(
        ctx, "courier-1", LOADING_DOCK, LOBBY
    )

    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert "reception" in messages[0].text_en.lower()


def test_courier_sees_no_rush_message_in_balanced_mode():
    # During Balanced mode,
    # no special Rush message is shown.

    ctx, access, dispatcher = make_system()

    dispatcher.mode = Mode.BALANCED

    send_passenger(
        ctx, "courier-1", LOADING_DOCK, LOBBY
    )

    assert ctx.bus.of_type(PanelMessage) == []


def test_employee_can_still_go_to_office_floor():
    # Regular employees should not be restricted
    # by courier rules.

    ctx, access, dispatcher = make_system()

    send_passenger(
        ctx, "employee-1", LOBBY, 18, group="HIGH"
    )

    calls = ctx.bus.of_type(CallRequested)

    assert len(calls) == 1
    assert calls[0].call.destination == 18
    assert calls[0].call.group == "HIGH"


def test_revoked_card_can_still_go_down_to_lobby():
    # BR-07: Invalid cards must still be
    # allowed to return to the Lobby.

    ctx, access, dispatcher = make_system()

    send_passenger(
        ctx, "revoked-courier", 18, LOBBY,
        group="HIGH"
    )

    assert len(ctx.bus.of_type(CallRequested)) == 1
    assert ctx.bus.of_type(AccessRefused) == []


def test_revoked_card_cannot_enter_loading_dock():
    # An invalid card cannot enter B1.

    ctx, access, dispatcher = make_system()

    send_passenger(
        ctx, "revoked-courier", LOBBY, LOADING_DOCK
    )

    assert ctx.bus.of_type(CallRequested) == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_courier_card_tap_uses_service_group():
    # Courier taps a valid card.
    # Default destination is B1.
    # Elevator group must be SERVICE.

    ctx, access, dispatcher = make_system()

    call_id = access.tap_card(
        "courier-1", "panel-1", LOBBY, "LOW"
    )

    calls = ctx.bus.of_type(CallRequested)

    assert call_id is not None
    assert len(calls) == 1
    assert calls[0].call.destination == LOADING_DOCK
    assert calls[0].call.group == "SERVICE"


def test_courier_cannot_choose_office_floor():
    # Courier taps card and chooses Other Floor.
    # L18 is not allowed.

    ctx, access, dispatcher = make_system()

    call_id = access.tap_card(
        "courier-1", "panel-1", LOBBY, "LOW"
    )

    result = access.choose_other_floor(call_id, 18)

    assert result is False
    assert ctx.bus.of_type(CallReplaced) == []


def test_courier_other_floor_still_uses_service_group():
    # Courier changes the destination to Lobby.
    # The replacement must use SERVICE.

    ctx, access, dispatcher = make_system()

    call_id = access.tap_card(
        "courier-1", "panel-1", 2, "LOW"
    )

    result = access.choose_other_floor(
        call_id, LOBBY
    )

    replacements = ctx.bus.of_type(CallReplaced)

    assert result is True
    assert len(replacements) == 1
    assert replacements[0].new_call.destination == LOBBY
    assert replacements[0].new_call.group == "SERVICE"


def test_courier_card_tap_during_rush_shows_message():
    # Courier taps a card during Rush.
    # Panel should display the reception message.

    ctx, access, dispatcher = make_system()

    dispatcher.mode = Mode.RUSH

    access.tap_card(
        "courier-1", "panel-1", LOBBY, "LOW"
    )

    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert "reception" in messages[0].text_en.lower()
