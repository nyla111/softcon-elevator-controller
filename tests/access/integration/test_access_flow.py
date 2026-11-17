
"""Integration Test 1 (FR-40 + FR-45): Access Control + Floor Selection"""

from ems.core.app import build
from ems.contracts.events import CallRequested, CallReplaced
from ems.contracts.types import Permission, Role


class FakeACS:
    """Only ACS is simulated. Access components are real."""

    def lookup(self, credential_id):
        if credential_id == "employee-1":
            return Permission(
                credential_id="employee-1",
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


def test_valid_card_default_floor_and_replacement():
    # STEP 1: Build the real Access system
    ctx = build(
        seed=1,
        parts=["access"],
        services={"acs": FakeACS()},
    )

    access = ctx.services["access"]

    # STEP 2: Employee taps card
    call_id = access.tap_card(
        "employee-1", "panel-L1", 1, "LOW"
    )

    calls = ctx.bus.of_type(CallRequested)

    # The default floor (L5) is called immediately
    assert call_id is not None
    assert len(calls) == 1
    assert calls[0].call.destination == 5

    # STEP 3: Advance simulation by 4 seconds
    ctx.clock.run_until(4.0)

    # Employee chooses L7 instead
    result = access.choose_other_floor(call_id, 7)

    replacements = ctx.bus.of_type(CallReplaced)

    # STEP 4: Verify the replacement
    assert result is True
    assert len(replacements) == 1

    assert replacements[0].old_call_id == call_id
    assert replacements[0].new_call.destination == 7

    # Original waiting timestamp must be preserved
    assert (
        replacements[0].new_call.created_at
        == calls[0].call.created_at
    )

    # No second CallRequested (no ghost stop)
    assert len(ctx.bus.of_type(CallRequested)) == 1
