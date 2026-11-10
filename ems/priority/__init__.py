"""Part 5 Priority & service car.

Scope: VIP 4.8, accessible FR-08, medical FR-127/128, maintenance 4.5, goods moves FR-36/81/82, bulky items 4.9.
Provides ctx.services key(s): priority
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["priority"] = service
    pass
