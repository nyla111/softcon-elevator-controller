"""Part 4 Safety & edge cases.

Scope: Car states + BR-06, 4.6 fire/power, 4.11.1-4.11.6 edge cases, FR-120-125 spam limits.
Provides ctx.services key(s): car_states
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["car_states"] = service
    pass
