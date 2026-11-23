"""FR-48: A secured in-car floor needs a valid card tap within 10 seconds."""

from ..contracts.config import LOBBY
from ..contracts.types import Role


CARD_WINDOW_SECONDS = 10


class InCarAccess:
    def __init__(self, ctx, floor_access, access_logging=None):
        self.ctx = ctx
        self.floor_access = floor_access
        self.access_logging = access_logging

        # Store recent card taps for each car
        # Example: {"A": {"credential_id": "card-1", "time": 12.0}}
        self.recent_taps = {}

        # Generate unique IDs for registered stops
        self.next_stop_id = 0

    def refuse(self, car_id, credential_id, reason):
        """Use FR-46 for a safe refusal message."""

        self.floor_access.refused_panel.refuse(
            f"car-{car_id}",
            credential_id,
            reason
        )

    def tap_card(self, car_id, credential_id):
        """Remember a valid card tapped inside a car."""

        # STEP 1: Verify credential
        permission = self.floor_access.permission(
            credential_id
        )

        # STEP 2: Reject invalid card
        if not permission.valid:
            self.recent_taps.pop(car_id, None)

            self.refuse(
                car_id,
                credential_id,
                "invalid_credential"
            )
            return False

        # STEP 3: Store card and tap time
        now = self.ctx.clock.now()

        self.recent_taps[car_id] = {
            "credential_id": credential_id,
            "time": now,
        }

        # STEP 4: Log credential use (FR-43)
        if self.access_logging is not None:
            self.access_logging.add_log(
                time=now,
                credential_id=credential_id,
                panel_id=f"car-{car_id}",
                action="CARD_TAP",
            )

        return True

    def press_floor(self, car_id, destination, *, action_id=None):
        """Check authorization and register an in-car stop."""

        # STEP 1: Check floor number
        if type(destination) is not int or destination == 0:
            return False

        now = self.ctx.clock.now()

        permission = None
        credential_id = None

        # STEP 2: Look for a recent card tap in this car
        tap = self.recent_taps.get(car_id)

        if tap is not None:
            age = now - tap["time"]

            if 0 <= age < CARD_WINDOW_SECONDS:
                credential_id = tap["credential_id"]

                # Recheck in case the card was revoked
                permission = self.floor_access.permission(
                    credential_id
                )

                if not permission.valid:
                    permission = None
                    self.recent_taps.pop(car_id, None)

            else:
                # Tap is too old
                self.recent_taps.pop(car_id, None)

        # STEP 3: Check allowed floors
        allowed = set(
            self.floor_access.public_floors(now)
        )

        if permission is not None:
            allowed.update(permission.allowed_floors)

        if destination not in allowed:
            self.refuse(
                car_id,
                credential_id,
                "unauthorized"
            )
            return False

        # STEP 4: Check Lockdown (FR-42)
        if not self.floor_access.lockdown.can_visit_floor(
            destination, permission
        ):
            self.refuse(
                car_id,
                credential_id,
                "lockdown"
            )
            return False

        # STEP 5: Courier must use SERVICE car (FR-85)
        if (
            permission is not None
            and Role.COURIER in permission.roles
            and destination != LOBBY
        ):
            car_status = self.ctx.services[
                "controllers"
            ].status(car_id)

            if car_status.group != "SERVICE":
                self.refuse(
                    car_id,
                    credential_id,
                    "courier_service_only"
                )
                return False

        # STEP 6: Register stop with this car's controller
        # AccessManager installs the runtime policy; standalone FR-48 component
        # adapters retain their original constructor contract for owner unit tests.
        actions = getattr(self.floor_access, "actions", None)
        if actions is not None:
            if self.ctx.get("car_states").safety_active():
                return False  # No EMS request, including an eventual controller refusal.
            replay = actions.replay(action_id, car_id, destination, credential_id)
            if replay is not None:
                return replay
            origin = self.ctx.get("controllers").status(car_id).floor
            if not actions.admit(credential_id, f"car:{car_id}", origin, destination, permission):
                return False
        self.next_stop_id += 1
        stop_id = f"car-{car_id}-{self.next_stop_id}"

        accepted = self.ctx.services[
            "controllers"
        ].register_stop(
            car_id,
            destination,
            stop_id
        )

        if not accepted:
            return False
        if actions is not None:
            actions.registered(car_id, destination, credential_id, stop_id, action_id)

        # STEP 7: Log accepted credential-based stop
        if (
            self.access_logging is not None
            and credential_id is not None
            and permission is not None
        ):
            self.access_logging.add_log(
                time=now,
                credential_id=credential_id,
                panel_id=f"car-{car_id}",
                action="CAR_FLOOR_ACCEPTED",
                destination=destination,
            )

        return True
