"""Part 1 Simulator & integration (Tech lead).

Scope: Simulated cars/doors/load, traffic generator, mock ACS, camera, fire panel, power room, notifier test inbox, heartbeat (FR-104), DB buffering (FR-107), failover (NFR-R2).
Provides ctx.services key(s): controllers, acs, notifier
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["controllers"] = service
    pass
