"""FR-41 tests. Each test checks one visitor-pass rule.

1. Normal request: tenant T01 visits floor 5, from second 10 to 100.
"""

from types import SimpleNamespace

import pytest

from ems.access.visitor_qr_pass import VisitorPass
from ems.contracts.types import Permission, Role


class FakeClock:
    def now(self):
        return 0.0  # Simulated time starts at 0 seconds


class FakeACS:
    def __init__(self):
        self.offline = False
        self.last_request = None

    def issue_visitor_pass(self, tenant_id, floors, valid_from, valid_to, group_size):
        if self.offline:
            raise TimeoutError("ACS offline")

        # Save the request so a test can check its contents.
        self.last_request = {
            "tenant_id": tenant_id,
            "floors": floors,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "group_size": group_size,
        }
        return "QR-001"  # Fake QR credential ID, not a real QR image


class FakeFloorAccess:
    def __init__(self):
        self.from_cache = False

    def permission(self, credential_id):
        is_admin = credential_id == "admin-T01"
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset({5, 7}),
            default_floor=5,
            roles=frozenset({Role.TENANT_ADMIN}) if is_admin else frozenset({Role.EMPLOYEE}),
            valid=True,
            tenant_id="T01",
            from_cache=self.from_cache,
        )


def make_system():
    """Create new fake services for each test."""
    acs = FakeACS()
    floor_access = FakeFloorAccess()

    # SimpleNamespace is a small object with .clock and .services fields.
    ctx = SimpleNamespace(
        clock=FakeClock(),
        services={
            "acs": acs,
            "population": SimpleNamespace(tenants={"T01": [5, 7], "T02": [9]}),
        },
    )
    visitor_pass = VisitorPass(ctx, floor_access)
    return visitor_pass, ctx, acs, floor_access


def create_qr(visitor_pass, **changes):
    """Normal request: tenant T01 visits floor 5, from second 10 to 100."""
    request = {
        "admin_credential_id": "admin-T01",
        "tenant_id": "T01",
        "floors": {5},
        "valid_from": 10,
        "valid_to": 100,
    }
    request.update(changes)
    return visitor_pass.issue_visitor_pass(**request)


def test_valid_admin_can_issue_qr():
    # tenant admin issue a valid QR code
    service, ctx, acs, floor_access = make_system()
    token = create_qr(service)
    assert token == "QR-001"
    assert acs.last_request["floors"] == {5}
    assert acs.last_request["group_size"] == 1


def test_qr_time_window_is_sent_to_acs():
    # time range is accurately passed to ACS
    service, ctx, acs, floor_access = make_system()
    create_qr(service)
    assert acs.last_request["valid_from"] == 10
    assert acs.last_request["valid_to"] == 100


def test_employee_cannot_issue_qr():
    # regular employees cannot issue QR codes
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(PermissionError):
        create_qr(service, admin_credential_id="employee-T01")


def test_admin_cannot_issue_for_another_tenant():
    # admin cannot issue QR codes for another tenant
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(PermissionError):
        create_qr(service, tenant_id="T02", floors={9})


def test_admin_cannot_issue_for_another_tenants_floor():
    # admin cannot issue QR codes for floors belonging to another tenant
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(PermissionError):
        create_qr(service, floors={9})


def test_invalid_time_window_is_rejected():
    # invalid time range is rejected
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(ValueError):
        create_qr(service, valid_from=100, valid_to=10)


def test_empty_floor_list_is_rejected():
    # empty floor list is rejected
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(ValueError):
        create_qr(service, floors=set())


def test_cached_admin_permission_cannot_issue_qr():
    # cached admin permissions cannot be used to issue QR codes
    service, ctx, acs, floor_access = make_system()
    floor_access.from_cache = True
    with pytest.raises(PermissionError):
        create_qr(service)


def test_acs_outage_cannot_issue_qr():
    # new QR codes cannot be issued when ACS is disconnected
    service, ctx, acs, floor_access = make_system()
    acs.offline = True
    with pytest.raises(TimeoutError):
        create_qr(service)


def test_missing_tenant_directory_cannot_issue_qr():
    # missing tenant data is rejected
    service, ctx, acs, floor_access = make_system()
    ctx.services.pop("population")
    with pytest.raises(PermissionError):
        create_qr(service)
