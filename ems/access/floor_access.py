"""FR-40: validate destination-floor access (includes public lobby from BR-07)

ACS cache (FR-49), lockdown (FR-42), and time-based public floors (FR-44) will be separate features.
"""
from __future__ import annotations

from .acs_fallback import AcsFallback
from ..contracts.events import (
    AccessRefused,
    CallRequested,
    PanelMessage,
    PassengerArrived,
)
from ..contracts.types import Call, CallPriority, CallSource, Permission

from .courier_access import CourierAccess

from .lockdown import Lockdown

from .public_floors import PublicFloors

from .refused_panel import RefusedPanel


class FloorAccess:
    def __init__(self, ctx):
        self.ctx = ctx
        self.acs_fallback = AcsFallback(ctx)
        self.courier_access = CourierAccess(ctx)
        self.lockdown = Lockdown(ctx, self)
        self.public_floor_schedule = PublicFloors(ctx, self)
        self.refused_panel = RefusedPanel(ctx, self)

    def public_floors(self, at_time: float) -> set[int]:
        return self.public_floor_schedule.get_public_floors(
            at_time
        )

    def permission(self, credential_id: str) -> Permission:
        """Ask ACS for credential permissions."""
        try:
            permission = self.acs_fallback.permission(
                credential_id
            )

            return self.courier_access.restrict_permission(
                permission
            )
        except TimeoutError:
            # Basic fallback. Cache will be added later.
            return Permission(
                credential_id=credential_id,
                allowed_floors=frozenset(),
                default_floor=None,
                roles=frozenset(),
                valid=False,
            )

    def on_passenger(self, event: PassengerArrived) -> None:
        """Handle passenger arrival"""

        permission = None

        # Step 1: Get public floors
        allowed = self.public_floors(
            self.ctx.clock.now()
        )

        # Step 2: Check credential
        if event.credential_id is not None:
            permission = self.permission(
                event.credential_id
            )

            if permission.valid:  # nếu nhận diện valid
                allowed.update(
                    permission.allowed_floors
                )
                self.courier_access.show_reception_message(
                    permission, event.panel_id
                )

        # Step 3: Check destination
        if event.destination not in allowed or not self.lockdown.can_visit_floor(event.destination, permission):  # nếu chọn tầng không được phép -> refuse access
            self.refused_panel.refuse(
                event.panel_id,
                event.credential_id,
                "unauthorized"
            )
            return

        # Step 4: Set priority
        if event.accessible:
            priority = CallPriority.ACCESSIBLE
        elif event.vip:
            priority = CallPriority.VIP
        else:
            priority = CallPriority.NORMAL

        # Step 5: Create an elevator call
        call = Call(
            call_id=event.passenger_id,
            origin=event.origin,
            destination=event.destination,
            group=self.courier_access.dispatch_group(
                permission, event.group
            ),
            created_at=event.t,
            source=CallSource.DESTINATION_PANEL,
            priority=priority,
            credential_id=event.credential_id,
            panel_id=event.panel_id,
        )

        # Step 6: Send request to Dispatch
        self.ctx.bus.publish(
            CallRequested(
                t=self.ctx.clock.now(),
                call=call,
            )
        )
