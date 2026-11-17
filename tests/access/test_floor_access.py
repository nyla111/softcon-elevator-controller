"""Unit tests for FR-40 and lobby public access (BR-07)
[scenario] --> [expected result]

1. card-123: valid, allowed floors: L5, L7
2. other cards: invalid
"""
from ems.contracts.events import AccessRefused, CallRequested, PassengerArrived
from ems.contracts.types import Permission, Role
from ems.core.app import build


class FakeACS:
    def lookup(self, credential_id):
        valid = credential_id == "card-123"
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset({5, 7}) if valid else frozenset(),
            default_floor=5 if valid else None,
            roles=frozenset({Role.EMPLOYEE}) if valid else frozenset(),
            valid=valid,
        )


def passenger(ctx, credential_id, destination):
    ctx.bus.publish(PassengerArrived(
        t=ctx.clock.now(), passenger_id="P001",
        panel_id="panel-L1", origin=1, destination=destination,
        group="LOW", credential_id=credential_id,
        accessible=False, vip=False,
    ))


def test_fr40_authorized_floor_creates_one_call():
    # [valid card chooses L5] --> [create exactly 1 CallRequested]
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    passenger(ctx, "card-123", 5)
    calls = ctx.bus.of_type(CallRequested)
    assert len(calls) == 1
    assert calls[0].call.destination == 5
    assert not ctx.bus.of_type(AccessRefused)


def test_fr40_invalid_credential_cannot_visit_secured_floor():
    # [invalid card chooses L5] --> [not call car, create AccessRefused]
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    passenger(ctx, "invalid-card", 5)
    assert not ctx.bus.of_type(CallRequested)
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_fr40_valid_card_still_cannot_visit_unallowed_floor():
    # [valid card chooses L9] --> [Refuse due to authorization]
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    passenger(ctx, "card-123", 9)
    assert not ctx.bus.of_type(CallRequested)
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_br07_lobby_public_even_for_invalid_credential():
    # [invalid card chooses from L5 to L1] --> [still accept request to lobby]
    ctx = build(parts=["access"], services={"acs": FakeACS()})
    ctx.bus.publish(PassengerArrived(
        t=ctx.clock.now(), passenger_id="P002",
        panel_id="panel-L5", origin=5, destination=1,
        group="LOW", credential_id="invalid-card",
        accessible=False, vip=False,
    ))
    assert len(ctx.bus.of_type(CallRequested)) == 1
    assert not ctx.bus.of_type(AccessRefused)
