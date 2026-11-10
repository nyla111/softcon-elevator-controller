"""Part 6 Dashboard, camera & admin.

Scope: Dashboard + reports 4.4, camera review queue 4.10, roles/audit 4.7, retention section 6.
Provides ctx.services key(s): reports
"""
from __future__ import annotations


def register(ctx) -> None:
    """Subscribe to events and put your service(s) into ctx.services.
    Until you do, stubs from ems/contracts/stubs.py are used."""
    # Example:
    #   from ems.contracts.events import CallRequested
    #   service = MyService(ctx)
    #   ctx.bus.subscribe(CallRequested, service.on_call_requested)
    #   ctx.services["reports"] = service
    pass
