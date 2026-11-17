"""FR-49: simulated ACS timeout handling and a 24-hour permission cache"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ..contracts.events import AlertRaised
from ..contracts.types import Permission, Role

CACHE_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class _CacheEntry:
    permission: Permission
    saved_at: float


class AcsFallback:
    """Ask ACS; on timeout use fresh cached employee permissions, otherwise public only.

    Caller (FloorAccess) combines these credential permissions with public floors.
    Visitor/courier QR permissions are not cached: the shared Permission type does not
    expose their expiry, so reusing one offline could admit an expired QR pass.
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self._cache: dict[str, _CacheEntry] = {}
        self._outage_alerted = False

    def permission(self, credential_id: str) -> Permission:
        """Return ACS permission (or a safe fallback) at simulated time now."""
        now = self.ctx.clock.now()
        try:
            result = self.ctx.services["acs"].lookup(credential_id)
        except TimeoutError:
            self._alert_security_once()
            entry = self._cache.get(credential_id)
            if entry is not None and now - entry.saved_at < CACHE_TTL_SECONDS:
                return replace(entry.permission, from_cache=True)
            # No valid offline permission. FloorAccess still allows public floors.
            self._cache.pop(credential_id, None)
            return self._no_permission(credential_id)

        # ACS answered: the live answer ALWAYS wins over cached data (NFR-SE6).
        self._outage_alerted = False
        if not result.valid:
            self._cache.pop(credential_id, None)
            return replace(result, from_cache=False)

        fresh = replace(result, from_cache=False)
        if Role.VISITOR in fresh.roles or Role.COURIER in fresh.roles:
            # Their pass-expiry time is absent from the shared Permission contract.
            self._cache.pop(credential_id, None)
        else:
            self._cache[credential_id] = _CacheEntry(fresh, now)
            self.ctx.clock.call_later(
                CACHE_TTL_SECONDS,
                lambda cid=credential_id, saved_at=now: self._expire(cid, saved_at),
            )
        return fresh

    def _expire(self, credential_id: str, saved_at: float) -> None:
        """Remove only the entry this timer was created for, not a newer refresh."""
        entry = self._cache.get(credential_id)
        if entry is not None and entry.saved_at == saved_at:
            if self.ctx.clock.now() - saved_at >= CACHE_TTL_SECONDS:
                self._cache.pop(credential_id, None)

    def _alert_security_once(self) -> None:
        if self._outage_alerted:
            return
        self._outage_alerted = True
        self.ctx.bus.publish(
            AlertRaised(
                t=self.ctx.clock.now(),
                severity="high",
                kind="acs_down",
                recipients=["security"],
                message="ACS unavailable: using fresh cached permissions or public floors only.",
            )
        )

    @staticmethod
    def _no_permission(credential_id: str) -> Permission:
        return Permission(
            credential_id=credential_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
            from_cache=False,
        )
