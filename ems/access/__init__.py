
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

    service = AccessManager(ctx)  # tạo bộ phận quản lý quyền truy cập

    ctx.bus.subscribe(  # khi Simulator thông báo có hành khách mới, tự động gọi on_passenger
        PassengerArrived,
        service.on_passenger
    )

    ctx.services["access"] = service  # đăng ký AccessManager để các phần khác sử dụng
