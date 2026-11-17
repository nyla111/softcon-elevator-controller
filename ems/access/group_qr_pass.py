"""FR-126: Issue a group QR pass and request a trip for up to 20 visitors"""
from __future__ import annotations

import math

from ..contracts.events import CallRequested
from ..contracts.types import Call, CallSource, Role


class GroupQR:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access

        # Remember group passes issued during this simulation run.
        self.passes = {}
        self.next_call_id = 0

    def issue_group_qr(
        self, admin_credential_id, tenant_id,
        floor, valid_from, valid_to, group_size
    ):
        """Allow one tenant admin to issue a QR for one floor and 1-20 visitors."""

        # STEP 1: Check the number of visitors, floor, and time window.
        if type(group_size) is not int or not 1 <= group_size <= 20:
            raise ValueError("Group size must be between 1 and 20")

        if type(floor) is not int or floor == 0:
            raise ValueError("Choose one valid floor")

        if any(
            type(value) not in (int, float) or not math.isfinite(value)
            for value in (valid_from, valid_to)
        ):
            raise ValueError("Start and end times must be numbers")

        if valid_from >= valid_to or valid_to <= self.ctx.clock.now():
            raise ValueError("Invalid QR time window")

        # STEP 2: Check the CURRENT Tenant Admin permission.
        admin = self.floor_access.permission(admin_credential_id)
        if not admin.valid or admin.from_cache:
            raise PermissionError("A current, valid admin credential is required")

        if Role.TENANT_ADMIN not in admin.roles or admin.tenant_id != tenant_id:
            raise PermissionError("Only this tenant's admin may issue a group QR")

        # STEP 3: Make sure the destination belongs to that tenant.
        population = self.ctx.services.get("population")
        tenants = getattr(population, "tenants", None)
        if not isinstance(tenants, dict) or tenant_id not in tenants:
            raise PermissionError("Tenant floor information is unavailable")

        if floor not in tenants[tenant_id]:
            raise PermissionError("Destination does not belong to this tenant")

        # STEP 4: Ask ACS to issue the actual credential.
        token = self.ctx.services["acs"].issue_visitor_pass(
            tenant_id=tenant_id,
            floors={floor},
            valid_from=float(valid_from),
            valid_to=float(valid_to),
            group_size=group_size,
        )

        # Keep the size limit for panel scans in this demo session.
        self.passes[token] = {
            "floor": floor,
            "valid_from": valid_from,
            "valid_to": valid_to,
            "remaining": group_size,
        }
        return token

    def request_group_trip(
        self, qr_token, panel_id, origin,
        destination, group, group_size
    ):
        """Panel asks how many people are traveling; create one grouped call."""

        # STEP 1: Check that the pass exists and the size makes sense.
        pass_info = self.passes.get(qr_token)
        if pass_info is None:
            return False

        if type(group_size) is not int or not 1 <= group_size <= 20:
            return False

        if group_size > pass_info["remaining"]:
            return False

        # STEP 2: Check the time window and the one permitted floor.
        now = self.ctx.clock.now()
        if not pass_info["valid_from"] <= now < pass_info["valid_to"]:
            return False

        if destination != pass_info["floor"]:
            return False

        # STEP 3: Ask ACS again: a revoked or expired QR must not be used.
        permission = self.floor_access.permission(qr_token)
        if not permission.valid or permission.from_cache:
            return False

        if Role.VISITOR not in permission.roles:
            return False

        if destination not in permission.allowed_floors:
            return False

        # STEP 4: Send one Call with party_size to Dispatch.
        self.next_call_id += 1
        call = Call(
            call_id=f"group-qr-{self.next_call_id}",
            origin=origin,
            destination=destination,
            group=group,
            created_at=now,
            source=CallSource.DESTINATION_PANEL,
            credential_id=qr_token,
            party_size=group_size,
            panel_id=panel_id,
        )
        self.ctx.bus.publish(CallRequested(t=now, call=call))
        pass_info["remaining"] -= group_size
        return True
