"""FR-85: Courier floor access and SERVICE elevator"""

from __future__ import annotations

from dataclasses import replace

from ..contracts.config import LOADING_DOCK, LOBBY
from ..contracts.events import PanelMessage
from ..contracts.types import Mode, Role


class CourierAccess:
    def __init__(self, ctx):
        self.ctx = ctx

    def is_courier(self, permission):
        """Check if the credential belongs to a courier."""

        return (
            permission is not None
            and permission.valid
            and Role.COURIER in permission.roles
        )

    def restrict_permission(self, permission):
        """Restrict couriers to Lobby and B1."""

        # STEP 1: Keep non-courier permissions unchanged
        if not self.is_courier(permission):
            return permission

        # STEP 2: Restrict accessible floors
        courier_floors = {LOBBY, LOADING_DOCK}

        allowed = (
            set(permission.allowed_floors)
            & courier_floors
        )

        # BR-07: Lobby is always accessible
        allowed.add(LOBBY)

        # STEP 3: Validate default floor
        default_floor = permission.default_floor

        if default_floor not in allowed:
            default_floor = None

        # STEP 4: Return updated permission
        return replace(
            permission,
            allowed_floors=frozenset(allowed),
            default_floor=default_floor,
        )

    def dispatch_group(self, permission, requested_group):
        """Couriers must use SERVICE elevators."""

        if self.is_courier(permission):
            return "SERVICE"

        return requested_group

    def is_rush(self):
        """Check whether the building is in Rush mode."""

        dispatcher = self.ctx.services.get("dispatcher")

        if dispatcher is None:
            return False

        if dispatcher.current_mode("LOW") == Mode.RUSH:
            return True

        if dispatcher.current_mode("HIGH") == Mode.RUSH:
            return True

        return False

    def show_reception_message(self, permission, panel_id):
        """Tell couriers to deliver to reception during Rush."""

        # STEP 1: Check courier role
        if not self.is_courier(permission):
            return

        # STEP 2: Check Rush mode
        if not self.is_rush():
            return

        # STEP 3: Show message on panel
        self.ctx.bus.publish(
            PanelMessage(
                t=self.ctx.clock.now(),
                target=panel_id,
                text_en="During Rush, please deliver to lobby reception.",
                text_vi="Trong giờ cao điểm, vui lòng giao hàng tại lễ tân tầng 1.",
            )
        )
