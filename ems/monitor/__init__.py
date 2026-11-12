"""Part 6: dashboard, reporting, camera review and administration."""
from __future__ import annotations

from ems.contracts.events import Event

from .service import MonitorService


def register(ctx) -> None:
    service = MonitorService(ctx)
    ctx.bus.subscribe(Event, service.on_event)
    ctx.services["reports"] = service
