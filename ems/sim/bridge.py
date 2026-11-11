"""TEMPORARY until Part 3 merges.

Part 3 (access) will check permissions and turn PassengerArrived into CallRequested.
Until it exists, ctx.services["access"] is the stub (class StubAccess) and this bridge
publishes CallRequested directly, so the rest of the system can be tested.
Once the real access service is registered the bridge does nothing.
"""
from __future__ import annotations

from ems.contracts.events import CallRequested, PassengerArrived
from ems.contracts.types import Call, CallPriority, CallSource


class TemporaryBridge:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        ctx.bus.subscribe(PassengerArrived, self.on_passenger)

    def on_passenger(self, ev: PassengerArrived) -> None:
        # Look the service up now (not in __init__): stubs are filled in after register().
        if type(self.ctx.services.get("access")).__name__ != "StubAccess":
            return
        if ev.accessible:
            priority = CallPriority.ACCESSIBLE
        elif ev.vip:
            priority = CallPriority.VIP
        else:
            priority = CallPriority.NORMAL
        call = Call(call_id=ev.passenger_id, origin=ev.origin, destination=ev.destination,
                    group=ev.group, created_at=ev.t, source=CallSource.DESTINATION_PANEL,
                    priority=priority, credential_id=ev.credential_id, panel_id=ev.panel_id)
        self.ctx.bus.publish(CallRequested(ev.t, call))
