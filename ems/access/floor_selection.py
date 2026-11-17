"""FR-45: automatically call the default floor and allow a 5-second replacement"""
from __future__ import annotations

from ..contracts.config import OTHER_FLOOR_WINDOW_S
from ..contracts.events import CallReplaced, CallRequested
from ..contracts.types import Call, CallSource
# Access refusal event
from ems.contracts.events import AccessRefused


class FloorSelection:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access

        # Store calls that can still be replaced
        self.pending_default_calls = {}

        # Generate unique IDs for panel calls
        self.next_panel_call_id = 0

    def tap_card(self, credential_id, panel_id, origin, group):
        """FR-45: Automatically call the default floor."""

        permission = self.floor_access.permission(credential_id)

        if not permission.valid:
            self.floor_access.refused_panel.refuse(
                panel_id, credential_id, "invalid_credential"
            )
            return None

        destination = permission.default_floor

        if destination is None:
            self.floor_access.refused_panel.refuse(
                panel_id, credential_id, "no_default_floor"
            )
            return None

        allowed = (
                set(permission.allowed_floors)
                | self.floor_access.public_floors(self.ctx.clock.now())
        )

        if destination not in allowed:
            self.floor_access.refused_panel.refuse(
                panel_id, credential_id, "unauthorized"
            )
            return None

        self.floor_access.courier_access.show_reception_message(
            permission, panel_id
        )

        if not self.floor_access.lockdown.can_visit_floor(
                destination, permission
        ):
            self.floor_access.refused_panel.refuse(
                panel_id, credential_id, "lockdown"
            )
            return None

        self.next_panel_call_id += 1
        call_id = f"panel-{self.next_panel_call_id}"

        call = Call(
            call_id=call_id,
            origin=origin,
            destination=destination,
            group=self.floor_access.courier_access.dispatch_group(
                permission, group
            ),
            created_at=self.ctx.clock.now(),
            source=CallSource.DESTINATION_PANEL,
            credential_id=credential_id,
            panel_id=panel_id,
        )

        self.pending_default_calls[call_id] = call

        self.ctx.bus.publish(
            CallRequested(self.ctx.clock.now(), call)
        )

        return call_id

    def choose_other_floor(self, call_id, new_floor):
        """FR-45: Replace the default call within 5 seconds."""

        old_call = self.pending_default_calls.get(call_id)

        if old_call is None:
            return False

        now = self.ctx.clock.now()

        # Expired after 5 simulated seconds
        if now - old_call.created_at >= OTHER_FLOOR_WINDOW_S:
            self.pending_default_calls.pop(call_id, None)
            return False

        permission = self.floor_access.permission(old_call.credential_id)

        allowed = self.floor_access.public_floors(now)

        if permission.valid:
            allowed.update(permission.allowed_floors)

        if new_floor not in allowed:
            self.floor_access.refused_panel.refuse(
                old_call.panel_id,
                old_call.credential_id,
                "unauthorized"
            )
            return False

        if not self.floor_access.lockdown.can_visit_floor(
                new_floor, permission
        ):
            self.floor_access.refused_panel.refuse(
                old_call.panel_id,
                old_call.credential_id,
                "lockdown"
            )
            return False

        # Create a replacement, preserving the wait start time
        new_call = Call(
            call_id=f"{call_id}-replacement",
            origin=old_call.origin,
            destination=new_floor,
            group=self.floor_access.courier_access.dispatch_group(
                permission, old_call.group
            ),
            created_at=old_call.created_at,
            source=old_call.source,
            priority=old_call.priority,
            credential_id=old_call.credential_id,
            panel_id=old_call.panel_id,
        )

        self.ctx.bus.publish(
            CallReplaced(
                t=now,
                old_call_id=call_id,
                new_call=new_call,
            )
        )

        self.pending_default_calls.pop(call_id, None)

        return True
