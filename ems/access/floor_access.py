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

from dataclasses import replace

# Issue #25: B1 and B2 are accessible to all valid cards.
BASEMENT_FLOORS = frozenset({-1, -2})


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
        """Get ACS permissions and grant B1/B2 access to valid cards."""

        try:
            # Step 1: Get permission from ACS or its cache.
            permission = self.acs_fallback.permission(
                credential_id
            )

            # Step 2: Apply existing courier restrictions.
            permission = self.courier_access.restrict_permission(
                permission
            )

            # Step 3: Give every valid card access to B1 and B2.
            if permission.valid:
                permission = replace(
                    permission,
                    allowed_floors=frozenset(
                        set(permission.allowed_floors)
                        | BASEMENT_FLOORS
                    ),
                )

            return permission

        except TimeoutError:
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

            if permission.valid:  # Verified credential
                allowed.update(
                    permission.allowed_floors
                )
                self.courier_access.show_reception_message(
                    permission, event.panel_id
                )

        # Step 3: Check destination
        if event.destination not in allowed or not self.lockdown.can_visit_floor(event.destination, permission):  # Reject an unauthorized or locked floor
            self.refused_panel.refuse(
                event.panel_id,
                event.credential_id,
                "unauthorized"
            )
            return

        # Step 4: Set priority
        if not self.actions.admit(event.credential_id, event.panel_id, event.origin,
                                  event.destination, permission, assistance=event.accessible):
            return
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
