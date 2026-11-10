"""Part 3 Access & panels.

Scope: Floor access 4.2, panel screens, FR-85 courier, FR-126 group QR, BR-07 exit always open.
Provides ctx.services key(s): access
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["access"] = service
    pass
