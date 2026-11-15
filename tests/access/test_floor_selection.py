"""Unit tests for FR-45: Default Floor and Other Floor (5-second window)
[scenario] --> [expected result]

1. default floor: L5
2. allowed floors: L5, L7
3. time for floor change: 5 seconds

"""
from ems.contracts.events import CallReplaced, CallRequested
from ems.contracts.types import Permission, Role
from ems.core.app import build


class FakeACS:
    def __init__(self):
        self.revoked = False

    def lookup(self, credential_id):
        valid = credential_id == "card-123" and not self.revoked
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset({5, 7}) if valid else frozenset(),
            default_floor=5 if valid else None,
            roles=frozenset({Role.EMPLOYEE}) if valid else frozenset(),
            valid=valid,
        )


def setup():
    acs = FakeACS()
    ctx = build(parts=["access"], services={"acs": acs})
    return ctx, ctx.services["access"], acs


def test_fr45_tap_card_calls_default_floor():
    # [tap card] --> [auto call L5]
    ctx, access, _ = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    calls = ctx.bus.of_type(CallRequested)
    assert call_id is not None
    assert len(calls) == 1
    assert calls[0].call.destination == 5


def test_fr45_other_floor_at_4_9_seconds_replaces_call():
    # [choose L7 at 4.9s] --> [successfully replaced]
    ctx, access, _ = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    original = ctx.bus.of_type(CallRequested)[0].call
    ctx.clock.run_until(4.9)
    assert access.choose_other_floor(call_id, 7) is True
    replacements = ctx.bus.of_type(CallReplaced)
    assert len(replacements) == 1
    assert replacements[0].old_call_id == call_id
    assert replacements[0].new_call.destination == 7
    assert replacements[0].new_call.created_at == original.created_at
    assert len(ctx.bus.of_type(CallRequested)) == 1


def test_fr45_other_floor_at_5_seconds_rejected():
    # [choose L7 at 5.0s] --> [rejected to replace]
    ctx, access, _ = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    ctx.clock.run_until(5.0)
    assert access.choose_other_floor(call_id, 7) is False
    assert not ctx.bus.of_type(CallReplaced)


def test_fr45_unallowed_floor_rejected():
    # [choose L9] --> [reject]
    ctx, access, _ = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    assert access.choose_other_floor(call_id, 9) is False
    assert not ctx.bus.of_type(CallReplaced)


def test_fr45_no_duplicate_replacement():
    # [change floor twice] --> [accepted once]
    ctx, access, _ = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    assert access.choose_other_floor(call_id, 7) is True
    assert access.choose_other_floor(call_id, 5) is False
    assert len(ctx.bus.of_type(CallReplaced)) == 1


def test_fr45_revoked_card_cannot_choose_secured_floor():
    # [card revoked after tap] --> [cannot replace to floors that need card]
    ctx, access, acs = setup()
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    acs.revoked = True
    assert access.choose_other_floor(call_id, 7) is False
    assert not ctx.bus.of_type(CallReplaced)
