
"""SIM-07: ACS outage acceptance checks for Part 3."""

from ..contracts.events import AlertRaised
from ..contracts.types import Permission, Role
from ..sim.faults import measured
from .acs_fallback import _CacheEntry, CACHE_TTL_SECONDS


OUTAGE_START = measured(20)
OUTAGE_DURATION = 15 * 60
OUTAGE_END = OUTAGE_START + OUTAGE_DURATION


def setup_sim_07(ctx):
    """Prepare cache entries and schedule outage probes."""

    access = ctx.services["access"]
    acs = ctx.services["acs"]
    fallback = access.floor_access.acs_fallback

    now = ctx.clock.now()

    # Create a sample employee permission.
    sample = Permission(
        credential_id="sim07-fresh",
        allowed_floors=frozenset({5}),
        default_floor=5,
        roles=frozenset({Role.EMPLOYEE}),
        valid=True,
    )

    # Fresh and expired cache entries are test fixtures.
    fallback._cache["sim07-fresh"] = _CacheEntry(
        sample, now
    )

    fallback._cache["sim07-stale"] = _CacheEntry(
        sample, now - CACHE_TTL_SECONDS
    )

    evidence = {}
    ctx.services["sim07_evidence"] = evidence

    def during_outage():
        """Check what happens when ACS is offline."""

        fresh = access.permission("sim07-fresh")
        stale = access.permission("sim07-stale")
        missing = access.permission("sim07-missing")

        evidence["acs_was_down"] = acs.down

        evidence["fresh_cache_works"] = (
            fresh.valid
            and fresh.from_cache
            and 5 in fresh.allowed_floors
        )

        evidence["stale_cache_restricted"] = (
            not stale.valid
            and not stale.allowed_floors
        )

        evidence["missing_cache_restricted"] = (
            not missing.valid
            and not missing.allowed_floors
        )

    def after_recovery():
        """Check that live ACS takes control after recovery."""

        evidence["acs_recovered"] = not acs.down

        # This synthetic credential is unknown to live ACS.
        # A live invalid answer must override its old cache.
        permission = access.permission("sim07-fresh")

        evidence["live_acs_used"] = (
            not permission.valid
            and not permission.from_cache
        )

    # Run probes one second after each fault transition.
    ctx.clock.call_at(OUTAGE_START + 1, during_outage)
    ctx.clock.call_at(OUTAGE_END + 1, after_recovery)


def check_sim_07(result, ctx):
    """Return the individual SIM-07 acceptance criteria."""

    evidence = ctx.services.get("sim07_evidence", {})

    alerts = [
        event
        for event in ctx.bus.of_type(AlertRaised)
        if event.kind == "acs_down"
        and "security" in event.recipients
        and OUTAGE_START <= event.t < OUTAGE_END
    ]

    # Report waiting times separately for outage arrivals.
    passengers = ctx.services["controllers"].passengers.values()

    outage_passengers = [
        passenger
        for passenger in passengers
        if OUTAGE_START <= passenger.arrived_at < OUTAGE_END
    ]

    waits = [
        passenger.wait_s
        for passenger in outage_passengers
        if passenger.wait_s is not None
    ]

    wait_report = {
        "arrivals_during_outage": len(outage_passengers),
        "observed_waits": len(waits),
        "unobserved_waits": len(outage_passengers) - len(waits),
        "avg_wait_s": sum(waits) / len(waits) if waits else None,
        "max_wait_s": max(waits) if waits else None,
    }

    return [
        (
            "ACS went offline",
            evidence.get("acs_was_down", False),
            evidence,
        ),
        (
            "Fresh cache accepted",
            evidence.get("fresh_cache_works", False),
            evidence,
        ),
        (
            "Expired cache restricted",
            evidence.get("stale_cache_restricted", False),
            evidence,
        ),
        (
            "Missing cache restricted",
            evidence.get("missing_cache_restricted", False),
            evidence,
        ),
        (
            "Security received ACS outage alert",
            len(alerts) >= 1,
            {"alerts": len(alerts)},
        ),
        (
            "ACS recovered and live lookup resumed",
            evidence.get("acs_recovered", False)
            and evidence.get("live_acs_used", False),
            evidence,
        ),
        (
            "Outage waiting times reported separately",
            len(outage_passengers) > 0 and len(waits) > 0,
            wait_report,
        ),
    ]
