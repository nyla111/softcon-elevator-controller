
"""Tests for Issue #25: B1/B2 access."""

from ems.core.app import build
from ems.contracts.types import Permission, Role


class FakeACS:
    """Return a valid employee card or an unknown card."""

    def lookup(self, card):

        if card == "employee-1":
            return Permission(
                credential_id=card,
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        return Permission(
            credential_id=card,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )


def make_system():
    ctx = build(
        parts=["access"],
        services={"acs": FakeACS()},
    )

    return ctx.services["access"]


def test_valid_card_can_access_b1():
    access = make_system()

    permission = access.permission("employee-1")

    assert permission.valid
    assert -1 in permission.allowed_floors


def test_valid_card_can_access_b2():
    access = make_system()

    permission = access.permission("employee-1")

    assert permission.valid
    assert -2 in permission.allowed_floors


def test_invalid_card_cannot_access_basements():
    access = make_system()

    permission = access.permission("unknown-card")

    assert not permission.valid
    assert -1 not in permission.allowed_floors
    assert -2 not in permission.allowed_floors


def test_original_employee_floor_is_preserved():
    access = make_system()

    permission = access.permission("employee-1")

    assert 5 in permission.allowed_floors
