
"""Part 3 Access & panels.

Scope: Floor access 4.2, panel screens,
FR-85 courier, FR-126 group QR,
BR-07 exit always open.

Provides ctx.services key(s): access
"""

from __future__ import annotations

from ..contracts.events import PassengerArrived
from .service import AccessManager


def register(ctx) -> None:
    """Subscribe to events and register access service."""

    service = AccessManager(ctx)  # Create the real access manager

    ctx.bus.subscribe(  # Handle passenger arrivals from the simulator
        PassengerArrived,
        service.on_passenger
    )

    ctx.services["access"] = service  # Expose the real service to the other parts
