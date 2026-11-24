"""FR-42: Security operators can lock or unlock selected floors"""

from ..contracts.config import LOBBY
from ..contracts.types import CarState, Role


class Lockdown:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access
        self.locked_floors = set()

    def is_security(self, permission):
        """Only a valid, freshly checked security card has security privileges."""
        return (
            permission is not None
            and permission.valid
            and not permission.from_cache
            and Role.SECURITY in permission.roles
        )

    def check_floors(self, floors):
        """Check the floor list before changing any lockdown settings."""
        # STEP 1: Require a list/set of floors
        if not isinstance(floors, (list, tuple, set, frozenset)):
            raise ValueError("Floors must be a list or set")

        selected_floors = set(floors)
        if not selected_floors:
            raise ValueError("Choose at least one floor")

        # STEP 2: Never lock the Lobby (BR-07)
        for floor in selected_floors:
            if type(floor) is not int or floor == 0 or floor == LOBBY:
                raise ValueError("Invalid floor or Lobby cannot be locked")

        return selected_floors

    def lock_floors(self, security_card, floors):
        """Lock selected floors, but only when requested by Security."""
        # STEP 1: Verify the operator's card
        permission = self.floor_access.permission(security_card)
        if not self.is_security(permission):
            raise PermissionError("Only Security can lock floors")

        # STEP 2: Check the requested floors
        selected_floors = self.check_floors(floors)

        # STEP 3: Activate lockdown
        self.locked_floors.update(selected_floors)

    def unlock_floors(self, security_card, floors):
        """Restore access to selected floors."""
        # STEP 1: Verify the operator's card
        permission = self.floor_access.permission(security_card)
        if not self.is_security(permission):
            raise PermissionError("Only Security can unlock floors")

        # STEP 2: Check the requested floors
        selected_floors = self.check_floors(floors)

        # STEP 3: Remove the lockdown
        self.locked_floors.difference_update(selected_floors)

    def can_visit_floor(self, floor, permission):
        """Check lockdown only; normal ACS permissions are checked elsewhere."""
        # FR-50: preserve configured locks, suspend only their effective enforcement.
        # Shared supervisory states retain FIRE_RECALL until valid FR-55 recovery,
        # so clear/reset or a stale FireAlarm event cannot reopen/restore this gate.
        building = self.ctx.services.get("building")
        if building is not None and any(
                self.ctx.get("car_states").state(car) is CarState.FIRE_RECALL
                for group in building.groups for car in group.cars):
            return True
        # STEP 1: Always allow travel to the Lobby
        if floor == LOBBY:
            return True

        # STEP 2: A floor that is not locked stays available
        if floor not in self.locked_floors:
            return True

        # STEP 3: Only Security can enter a locked floor
        return self.is_security(permission)
