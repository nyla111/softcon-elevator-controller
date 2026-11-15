"""FR-49 unit tests: simulated ACS outage, 24h cache, security alert, revocation.
[scenario] --> [expected result]

"""

from ems.contracts.events import AccessRefused, AlertRaised, CallRequested, PassengerArrived
from ems.contracts.types import Permission, Role
from ems.core.app import build
from ems.access.acs_fallback import CACHE_TTL_SECONDS


class FakeACS:
    def __init__(self):
        self.down = False
        self.revoked = False

    def lookup(self, credential_id):
        if self.down:
            raise TimeoutError("ACS did not answer within 1 second")
        if credential_id == "card-123" and not self.revoked:
            return Permission(
                credential_id=credential_id,
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )
        if credential_id == "QR-visitor":
            return Permission(
                credential_id=credential_id,
                allowed_floors=frozenset({5}),
                default_floor=5,
                roles=frozenset({Role.VISITOR}),
                valid=True,
            )
        return Permission(credential_id, frozenset(), None, frozenset(), False)


def setup():
    acs = FakeACS()
    ctx = build(parts=["access"], services={"acs": acs})
    return ctx, ctx.services["access"], acs


def test_online_lookup_returns_live_permission_not_cache():
    # [acs still works] --> [use latest access]
    ctx, access, _ = setup()
    permission = access.permission("card-123")
    assert permission.valid is True
    assert permission.allowed_floors == frozenset({5, 7})
    assert permission.from_cache is False
    assert ctx.bus.of_type(AlertRaised) == []


def test_timeout_uses_fresh_cache_and_alerts_security_once():
    # [acs loses connection, new cache] --> [use cache]
    ctx, access, acs = setup()
    access.permission("card-123")  # Prime the cache while ACS is online.
    acs.down = True
    ctx.clock.run_until(3600)
    permission = access.permission("card-123")
    assert permission.valid is True
    assert permission.from_cache is True
    assert 7 in permission.allowed_floors
    access.permission("card-123")
    alerts = [a for a in ctx.bus.of_type(AlertRaised) if a.kind == "acs_down"]
    assert len(alerts) == 1
    assert alerts[0].recipients == ["security"]


def test_timeout_without_cache_returns_public_only():
    # [acs loses connection, no cache] --> [not grant access to floors that require card]
    ctx, access, acs = setup()
    acs.down = True
    permission = access.permission("unknown")
    assert not permission.valid
    assert not permission.allowed_floors
    assert access.public_floors(ctx.clock.now()) == {1}


def test_cache_valid_at_23h_59m_but_not_at_24h():
    # [cache within 23h59m] --> [can still use]
    ctx, access, acs = setup()
    access.permission("card-123")
    acs.down = True
    ctx.clock.run_until(CACHE_TTL_SECONDS - 60)
    assert access.permission("card-123").from_cache is True
    ctx.clock.run_until(CACHE_TTL_SECONDS)
    stale = access.permission("card-123")
    assert stale.valid is False
    assert stale.allowed_floors == frozenset()


def test_received_revocation_clears_cache_even_during_later_outage():
    # [cache exactly 24h] --> [expire]
    ctx, access, acs = setup()
    access.permission("card-123")
    acs.revoked = True
    live = access.permission("card-123")
    assert live.valid is False
    acs.down = True
    assert access.permission("card-123").valid is False


def test_recovery_allows_a_new_outage_alert():
    # [acs returns revoked card] --> [remove old cache]
    ctx, access, acs = setup()
    acs.down = True
    access.permission("card-123")
    acs.down = False
    access.permission("card-123")
    acs.down = True
    access.permission("card-123")
    assert len([a for a in ctx.bus.of_type(AlertRaised) if a.kind == "acs_down"]) == 2


def test_visitor_qr_not_cached_without_expiry_metadata():
    # [acs loses connection many times] --> [not send multiple alerts at the same time]
    _, access, acs = setup()
    assert access.permission("QR-visitor").valid
    acs.down = True
    assert not access.permission("QR-visitor").valid


def test_lobby_still_open_after_timeout_with_no_cache():
    # [cache expires, go to lobby] --> [still allowed]
    ctx, _, acs = setup()
    acs.down = True
    ctx.bus.publish(PassengerArrived(
        t=ctx.clock.now(), passenger_id="p1", panel_id="panel-L5",
        origin=5, destination=1, group="LOW", credential_id="unknown",
        accessible=False, vip=False,
    ))
    calls = ctx.bus.of_type(CallRequested)
    assert len(calls) == 1
    assert calls[0].call.destination == 1
    assert ctx.bus.of_type(AccessRefused) == []


def test_existing_floor_selection_uses_fallback():
    ctx, access, acs = setup()
    access.permission("card-123")
    acs.down = True
    call_id = access.tap_card("card-123", "panel-L1", 1, "LOW")
    assert call_id is not None
    assert ctx.bus.of_type(CallRequested)[0].call.destination == 5


def test_integration_authorizes_with_fresh_cache_but_refuses_when_expired():
    ctx, access, acs = setup()
    access.permission("card-123")
    acs.down = True
    ctx.clock.run_until(60)
    ctx.bus.publish(PassengerArrived(
        t=ctx.clock.now(), passenger_id="p-fresh", panel_id="panel-L1",
        origin=1, destination=5, group="LOW", credential_id="card-123",
        accessible=False, vip=False,
    ))
    assert len(ctx.bus.of_type(CallRequested)) == 1

    ctx.clock.run_until(CACHE_TTL_SECONDS)
    ctx.bus.publish(PassengerArrived(
        t=ctx.clock.now(), passenger_id="p-expired", panel_id="panel-L1",
        origin=1, destination=5, group="LOW", credential_id="card-123",
        accessible=False, vip=False,
    ))
    assert len(ctx.bus.of_type(CallRequested)) == 1  # no second call
    assert len(ctx.bus.of_type(AccessRefused)) == 1
