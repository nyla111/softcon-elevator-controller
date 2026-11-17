"""Part 4 Safety & edge cases.

Scope: Car states + BR-06, 4.6 fire/power, 4.11.1-4.11.6 edge cases, FR-120-125 spam limits.
Provides ctx.services key(s): car_states
"""
from __future__ import annotations


def register(ctx) -> None:
    """Install the authoritative state service; dependencies are resolved lazily."""
    from .service import SafetyService

    service = SafetyService(ctx)
    ctx.services["car_states"] = service
    service.subscribe()
