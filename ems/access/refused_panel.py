
"""FR-46: Show a safe message when a credential is refused."""

from ..contracts.config import LOBBY
from ..contracts.events import AccessRefused, PanelMessage


class RefusedPanel:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access

    def get_available_public_floors(self):
        """Return only public floors that are not locked down."""

        # STEP 1: Get the public floors at the current time
        floors = set(
            self.floor_access.public_floors(
                self.ctx.clock.now()
            )
        )

        # BR-07: Lobby is always available
        floors.add(LOBBY)

        # STEP 2: Exclude floors blocked by Security lockdown
        allowed = []

        for floor in sorted(floors):
            if self.floor_access.lockdown.can_visit_floor(
                floor, None
            ):
                allowed.append(floor)

        return allowed

    def refuse(
        self, panel_id, credential_id, reason="unauthorized"
    ):
        """Record the refusal and show a safe public-floor message."""

        # STEP 1: Record the private reason for Security (FR-43)
        self.ctx.bus.publish(
            AccessRefused(
                t=self.ctx.clock.now(),
                panel_id=panel_id,
                credential_id=credential_id,
                reason=reason,
            )
        )

        # STEP 2: List only public floors
        public_floors = self.get_available_public_floors()

        floor_text = ", ".join(
            str(floor) for floor in public_floors
        )

        # STEP 3: Show the same generic message
        # for every refusal reason
        self.ctx.bus.publish(
            PanelMessage(
                t=self.ctx.clock.now(),
                target=panel_id,
                text_en=(
                    "Not recognised, please see reception. "
                    f"Public floors: {floor_text}"
                ),
                text_vi=(
                    "Không nhận diện được, vui lòng liên hệ lễ tân. "
                    f"Tầng công cộng: {floor_text}"
                ),
            )
        )
