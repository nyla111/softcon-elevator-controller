"""FR-126 tests: each test checks one group-QR rule
[scenario] --> [expected output]

1. Tenant T01 owns L5 and L7
2. Tenant T02 owns L9
3. admin-T01 is tenant admin of T01
4. employee-T01	is regular employee of T01
5. Group QR	is used only to reach L5
6. QR validity is from second 10 to before second 100
7. Maximum group size: 20 visitors
"""

from types import SimpleNamespace

import pytest

from ems.access.group_qr_pass import GroupQR
from ems.contracts.events import CallRequested
from ems.contracts.types import Permission, Role


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [event for event in self.events if isinstance(event, event_type)]


class FakeACS:
    def __init__(self, clock):
        self.clock = clock
        self.passes = {}
        self.offline = False
        self.revoked = set()

    def issue_visitor_pass(self, tenant_id, floors, valid_from, valid_to, group_size=1):
        if self.offline:
            raise TimeoutError("ACS unavailable")
        token = f"GROUP-QR-{len(self.passes) + 1}"
        self.passes[token] = {
            "tenant_id": tenant_id,
            "floors": set(floors),
            "valid_from": valid_from,
            "valid_to": valid_to,
            "group_size": group_size,
        }
        return token

    def lookup(self, credential_id):
        if self.offline:
            raise TimeoutError("ACS unavailable")

        info = self.passes.get(credential_id)
        if info is None or credential_id in self.revoked:
            return Permission(credential_id, frozenset(), None, frozenset(), False)

        valid = info["valid_from"] <= self.clock.now() < info["valid_to"]
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(info["floors"]) if valid else frozenset(),
            default_floor=next(iter(info["floors"])),
            roles=frozenset({Role.VISITOR}),
            valid=valid,
            tenant_id=info["tenant_id"],
        )


class FakeFloorAccess:
    def __init__(self, acs):
        self.acs = acs
        self.admin_is_cached = False

    def permission(self, credential_id):
        if credential_id == "admin-T01":
            return Permission(
                credential_id="admin-T01",
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.TENANT_ADMIN}),
                valid=True,
                tenant_id="T01",
                from_cache=self.admin_is_cached,
            )
        if credential_id == "employee-T01":
            return Permission(
                credential_id="employee-T01",
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
                tenant_id="T01",
            )
        return self.acs.lookup(credential_id)


def make_system():
    clock = FakeClock()
    bus = FakeBus()
    acs = FakeACS(clock)
    floor_access = FakeFloorAccess(acs)
    ctx = SimpleNamespace(
        clock=clock,
        bus=bus,
        services={
            "acs": acs,
            "population": SimpleNamespace(tenants={"T01": [5, 7], "T02": [9]}),
        },
    )
    return GroupQR(ctx, floor_access), ctx, acs, floor_access


def issue_qr(service, group_size=10):
    return service.issue_group_qr(
        admin_credential_id="admin-T01",
        tenant_id="T01",
        floor=5,
        valid_from=10,
        valid_to=100,
        group_size=group_size,
    )


def scan_qr(service, token, group_size=5, destination=5):
    return service.request_group_trip(
        qr_token=token,
        panel_id="panel-L1",
        origin=1,
        destination=destination,
        group="LOW",
        group_size=group_size,
    )


def test_tenant_admin_can_issue_group_qr_for_20():
    # [admin0-T01 issue QR for 20 visitors to reach L5] --> [successfully issued]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service, group_size=20)
    assert acs.passes[token]["group_size"] == 20
    assert acs.passes[token]["floors"] == {5}


def test_group_size_above_20_is_rejected():
    # [admin issues QR for 21 visitors] --> [ValueError, exceeds group size limit]
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(ValueError):
        issue_qr(service, group_size=21)


def test_group_size_zero_is_rejected():
    # [admin issues QR for 0 visitors] --> [ValueError, group must have at least 1]
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(ValueError):
        issue_qr(service, group_size=0)


def test_employee_cannot_issue_group_qr():
    # [regular employees issues QR codes for 8 visitors] --> [PermissionError, only admin has authorization]
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(PermissionError):
        service.issue_group_qr("employee-T01", "T01", 5, 10, 100, 8)


def test_admin_cannot_issue_qr_for_another_tenant_floor():
    # [admin-T01 issues QR to L9, belonging to T02] --> [PermissionError, cannot issue QR codes for floors belonging to another tenant]
    service, ctx, acs, floor_access = make_system()
    with pytest.raises(PermissionError):
        service.issue_group_qr("admin-T01", "T01", 9, 10, 100, 8)


def test_group_qr_rejects_cached_admin_permission():
    # [admin issues QR with old cached permission] --> [PermissionError]
    service, ctx, acs, floor_access = make_system()
    floor_access.admin_is_cached = True
    with pytest.raises(PermissionError):
        issue_qr(service)


def test_group_scan_creates_one_call_with_party_size():
    # [QR issued for 10 visitors, at second 20, 8 scan to reach L5] --> [successful]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service, group_size=10)
    ctx.clock.t = 20
    result = scan_qr(service, token, group_size=8)
    calls = ctx.bus.of_type(CallRequested)
    assert result is True
    assert len(calls) == 1
    assert calls[0].call.destination == 5
    assert calls[0].call.party_size == 8


def test_unknown_qr_is_rejected():
    # [unknown group QR] --> [return False, not create CallRequested]
    service, ctx, acs, floor_access = make_system()
    ctx.clock.t = 20
    assert scan_qr(service, "GROUP-QR-UNKNOWN") is False
    assert ctx.bus.of_type(CallRequested) == []


def test_panel_cannot_request_more_than_issued_quota():
    # [QR issued for 5 but panel entered 6] --> [refused]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service, group_size=5)
    ctx.clock.t = 20
    assert scan_qr(service, token, group_size=6) is False
    assert ctx.bus.of_type(CallRequested) == []


def test_same_qr_cannot_exceed_total_quota_on_repeat_scans():
    # [QR issued for 5 visitors, 1st time - 3, 2nd time - 2, 3rd time - 1] --> [2 first times - success, last one failed]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service, group_size=5)
    ctx.clock.t = 20
    assert scan_qr(service, token, group_size=3) is True
    assert scan_qr(service, token, group_size=2) is True
    assert scan_qr(service, token, group_size=1) is False
    assert len(ctx.bus.of_type(CallRequested)) == 2


def test_expired_group_qr_is_rejected():
    # [expired QR at second 100, scan at 100] --> [refused]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service)
    ctx.clock.t = 100
    assert scan_qr(service, token) is False
    assert ctx.bus.of_type(CallRequested) == []


def test_group_qr_cannot_visit_another_floor():
    # [QR issued for L5, visitor chooses L7] --> [refused]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service)
    ctx.clock.t = 20
    assert scan_qr(service, token, destination=7) is False
    assert ctx.bus.of_type(CallRequested) == []


def test_revoked_group_qr_is_rejected():
    # [QR revoked] --> [refused]
    service, ctx, acs, floor_access = make_system()
    token = issue_qr(service)
    ctx.clock.t = 20
    acs.revoked.add(token)
    assert scan_qr(service, token) is False
    assert ctx.bus.of_type(CallRequested) == []


def test_acs_outage_cannot_issue_new_group_qr():
    # [offline acs, tenant admin wants to issue QR] --> [TimeoutError, do not issue]
    service, ctx, acs, floor_access = make_system()
    acs.offline = True
    with pytest.raises(TimeoutError):
        issue_qr(service)
