"""Part 2 Dispatch & modes.

Scope: Call handling 4.1, modes 4.3, FR-100/101 overload re-assign, FR-105 fallback rule.
Provides ctx.services key(s): dispatcher
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["dispatcher"] = service
    pass
