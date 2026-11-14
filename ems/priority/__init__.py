"""Part 5 Priority & service car.

Scope: VIP 4.8 (FR-70/71/72), accessible FR-08, priority order FR-76, medical FR-127/128,
maintenance 4.5 (FR-30/31/32/33), goods moves and the service car (FR-36/80/81/82).
Bulky items (FR-84) are Part 6's (scripts/requirements.csv).
Provides ctx.services key(s): priority

Modules: service (holds, VIP, medical, send-to-lobby, voice feedback, faults/emergencies),
maintenance, goods, policy (pure functions Part 2 may call), auth (credential -> roles via
Access), standins (scenario/test doubles only, never registered here), demo (CLI walkthrough).
API and integration notes: docs/priority/INTEGRATION.md.
"""
from __future__ import annotations


def register(ctx) -> None:
    from . import goods, maintenance, service

    svc = service.PriorityService(ctx)
    svc.maintenance = maintenance.Maintenance(svc)
    svc.goods = goods.GoodsMoves(svc)
    service.subscribe(ctx, svc)
    maintenance.subscribe(ctx, svc.maintenance)
    ctx.services["priority"] = svc
