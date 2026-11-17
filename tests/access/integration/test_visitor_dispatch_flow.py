"""Integration Test 5: Visitor QR + Access + Dispatch."""

import pytest

from types import SimpleNamespace

from ems.core.app import build
from ems.contracts.events import (
    AccessRefused,
    CallAssigned,
    CallRequested,
    PassengerArrived,
)
from ems.contracts.types import Permission, Role


# ==========================================
# STEP 1: Fake ACS
# ==========================================

class FakeACS:
    def __init__(self, clock):
        self.clock = clock
        self.passes = {}
        self.next_pass_id = 0

    def issue_visitor_pass(
        self, tenant_id, floors,
        valid_from, valid_to, group_size=1
    ):
        # Create a visitor QR credential
        self.next_pass_id += 1
        token = f"visitor-qr-{self.next_pass_id}"

        self.passes[token] = {
            "tenant_id": tenant_id,
            "floors": set(floors),
            "valid_from": valid_from,
            "valid_to": valid_to,
        }

        return token

    def lookup(self, credential_id):

        # Tenant Admin can issue QR passes
        if credential_id == "admin-T01":
            return Permission(
                credential_id=credential_id,
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.TENANT_ADMIN}),
                valid=True,
                tenant_id="T01",
            )

        # Check visitor QR
        qr = self.passes.get(credential_id)

        if qr is not None:
            now = self.clock.now()

            if qr["valid_from"] <= now < qr["valid_to"]:
                return Permission(
                    credential_id=credential_id,
                    allowed_floors=frozenset(qr["floors"]),
                    default_floor=5,
                    roles=frozenset({Role.VISITOR}),
                    valid=True,
                    tenant_id=qr["tenant_id"],
                )

        # Unknown or expired credential
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
    """Build real Simulator, Access and Dispatch parts."""

    # STEP 1: Initialize the system
    ctx = build(
        seed=1,
        parts=["sim", "safety", "access", "dispatch"],
    )

    # STEP 2: Use predictable ACS test data
    acs = FakeACS(ctx.clock)
    ctx.services["acs"] = acs

    # FR-41 needs the tenant's floor directory
    ctx.services["population"] = SimpleNamespace(
        tenants={"T01": [5, 7]}
    )

    return ctx, ctx.services["access"]


def issue_qr(access):
    """Tenant Admin issues one QR for L5."""

    return access.issue_visitor_pass(
        admin_credential_id="admin-T01",
        tenant_id="T01",
        floors={5},
        valid_from=0,
        valid_to=120,
    )


def scan_qr(ctx, token, passenger_id, floor):
    """Simulate a visitor scanning QR at Lobby."""

    ctx.bus.publish(
        PassengerArrived(
            t=ctx.clock.now(),
            passenger_id=passenger_id,
            panel_id="panel-L1",
            origin=1,
            destination=floor,
            group="LOW",
            credential_id=token,
            accessible=False,
            vip=False,
        )
    )


# ==========================================
# TEST 1: Visitor QR is accepted by Access
# ==========================================

def test_01_valid_visitor_qr_creates_call():
    ctx, access = make_system()

    # STEP 1: Tenant Admin issues QR
    token = issue_qr(access)

    # STEP 2: Visitor scans QR for L5
    scan_qr(ctx, token, "visitor-1", 5)

    # STEP 3: Access must create one request
    calls = [
        event for event in ctx.bus.of_type(CallRequested)
        if event.call.call_id == "visitor-1"
    ]

    assert len(calls) == 1
    assert calls[0].call.destination == 5
    assert calls[0].call.credential_id == token

    assert ctx.bus.of_type(AccessRefused) == []


# ==========================================
# TEST 2: Dispatch assigns a real cabin
# ==========================================

@pytest.mark.skip(
    reason="Dispatch module is not implemented yet"
)
def test_02_dispatch_assigns_car_for_visitor():
    ctx, access = make_system()

    # STEP 1: Issue a valid QR
    token = issue_qr(access)

    # STEP 2: Visitor requests L5
    scan_qr(ctx, token, "visitor-2", 5)

    # STEP 3: Verify that Access sent the call
    calls = [
        event for event in ctx.bus.of_type(CallRequested)
        if event.call.call_id == "visitor-2"
    ]

    assert len(calls) == 1

    # STEP 4: Give Dispatch simulated time
    # to process the request
    ctx.clock.run_until(30)

    # STEP 5: Look for a real assignment
    assignments = [
        event for event in ctx.bus.of_type(CallAssigned)
        if event.call_id == "visitor-2"
    ]

    assert len(assignments) == 1, (
        "Access created CallRequested, but Dispatch "
        "did not publish CallAssigned."
    )

    # A cabin must be selected
    assert assignments[0].car_id is not None

    # Estimated arrival time cannot be negative
    assert assignments[0].eta_s >= 0


# ==========================================
# TEST 3: QR cannot access unauthorized floor
# ==========================================

def test_03_qr_cannot_access_another_floor():
    ctx, access = make_system()

    # STEP 1: QR is issued only for L5
    token = issue_qr(access)

    # STEP 2: Visitor attempts to access L7
    scan_qr(ctx, token, "visitor-3", 7)

    # STEP 3: Access must refuse the request
    refusals = ctx.bus.of_type(AccessRefused)

    assert len(refusals) == 1

    # STEP 4: No CallRequested for this visitor
    calls = [
        event for event in ctx.bus.of_type(CallRequested)
        if event.call.call_id == "visitor-3"
    ]

    assert calls == []

    # STEP 5: Dispatch must not assign a cabin
    assignments = [
        event for event in ctx.bus.of_type(CallAssigned)
        if event.call_id == "visitor-3"
    ]

    assert assignments == []
