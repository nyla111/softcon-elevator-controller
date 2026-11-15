"""

1. FR-40 - floor_access --> có được vào tầng này không?
2. FR-45 - floor_selection --> # quẹt thẻ tự gọi tầng nào và có thể đổi tầng trong 5 giây không?
3. FR-49 - acs_fallback --> xử lý sao khi hệ thống xác thực thẻ mất kết nối?
4. FR-41 - visitor_qr_pass --> cấp QR code cho khách
5. FR-85 - courier_access --> người giao hàng được phép dùng tầng nào?
6. FR-42 - lockdown --> khoá những tầng nhất định, và check xem ai vẫn có quyền đi những tầng đó
7. FR-44 - public_floors --> những tầng luôn available
8. FR-43 - access_logging --> giúp Security xem lịch sử sử dụng thẻ và những lần truy cập bị từ chối
9. FR-46 - refused_panel --> xử lý những gì hành khách nhìn thấy khi hệ thống từ chối quyền truy cập
10. FR-48 - in_car_access --> ử lý tình huống hành khách đã ở trong cabin thang máy và muốn bấm một tầng yêu cầu quyền truy cập
"""
from __future__ import annotations

from .floor_access import FloorAccess
from .floor_selection import FloorSelection

from .visitor_qr_pass import VisitorPass
from .group_qr_pass import GroupQR

from .access_logging import AccessLogging

from .in_car_access import InCarAccess


class AccessManager:
    def __init__(self, ctx):
        self.ctx = ctx
        self.floor_access = FloorAccess(ctx)
        self.floor_selection = FloorSelection(ctx, self.floor_access)
        self.visitor_pass = VisitorPass(ctx, self.floor_access)
        self.group_qr = GroupQR(ctx, self.floor_access)
        self.access_logging = AccessLogging(ctx)
        self.in_car_access = InCarAccess(
            ctx,
            self.floor_access,
            self.access_logging
        )

    # API required by the shared AccessService protocol
    def permission(self, credential_id):
        return self.floor_access.permission(credential_id)

    def public_floors(self, at_time: float) -> set[int]:
        return self.floor_access.public_floors(at_time)

    # FR-40: Traffic from Part 1 -> Part 3 -> Part 2
    def on_passenger(self, event):
        return self.floor_access.on_passenger(event)

    # FR-45: APIs for the future destination-panel screen
    def tap_card(self, credential_id, panel_id, origin, group):
        return self.floor_selection.tap_card(credential_id, panel_id, origin, group)

    def choose_other_floor(self, call_id, new_floor):
        return self.floor_selection.choose_other_floor(call_id, new_floor)

    def issue_visitor_pass(
            self, admin_credential_id, tenant_id,
            floors, valid_from, valid_to
    ):
        return self.visitor_pass.issue_visitor_pass(
            admin_credential_id,
            tenant_id,
            floors,
            valid_from,
            valid_to,
        )

    def issue_group_qr(self, admin_credential_id, tenant_id,
                       floor, valid_from, valid_to, group_size):
        return self.group_qr.issue_group_qr(
            admin_credential_id, tenant_id, floor,
            valid_from, valid_to, group_size
        )

    def request_group_trip(self, qr_token, panel_id, origin,
                           destination, group, group_size):
        return self.group_qr.request_group_trip(
            qr_token, panel_id, origin,
            destination, group, group_size
        )

    def lock_floors(self, security_card, floors):
        return self.floor_access.lockdown.lock_floors(
            security_card, floors
        )

    def unlock_floors(self, security_card, floors):
        return self.floor_access.lockdown.unlock_floors(
            security_card, floors
        )

    def set_public_hours(
            self, fm_card, floors, start_time, end_time
    ):
        return self.floor_access.public_floor_schedule.set_public_hours(
            fm_card, floors, start_time, end_time
        )

    def remove_public_hours(
            self, fm_card, start_time, end_time
    ):
        return self.floor_access.public_floor_schedule.remove_public_hours(
            fm_card, start_time, end_time
        )

    def get_access_logs(self, security_card):
        return self.access_logging.get_logs(security_card)

    # FR-48: Tap a card inside a car
    def tap_car_card(self, car_id, credential_id):
        return self.in_car_access.tap_card(
            car_id, credential_id
        )

    # FR-48: Press a floor button inside a car
    def press_car_floor(self, car_id, destination):
        return self.in_car_access.press_floor(
            car_id, destination
        )
