"""FR-41: Allow a Tenant Admin to request a visitor QR pass from ACS"""

from __future__ import annotations

import math

from ..contracts.types import Role


class VisitorPass:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access

    def issue_visitor_pass(
        self, admin_credential_id, tenant_id, floors, valid_from, valid_to
    ):
        """Check authorization, then ask ACS to issue one visitor pass."""

        # Step 1: Validate requested floors and time window.
        if not admin_credential_id or not tenant_id:
            raise ValueError("Admin ID and tenant ID are required")

        if not isinstance(floors, (list, tuple, set, frozenset)) or not floors:
            raise ValueError("Choose at least one floor")
        if any(type(floor) is not int for floor in floors):
            raise ValueError("Floor numbers must be integers")

        if any(
            type(t) not in (int, float) or not math.isfinite(t)
            for t in (valid_from, valid_to)
        ):
            raise ValueError("Times must be finite numbers")
        if valid_from >= valid_to or valid_to <= self.ctx.clock.now():
            raise ValueError("The end time must be later than the start and now")

        # Step 2: Check that this person is a CURRENT tenant admin.
        admin = self.floor_access.permission(admin_credential_id)
        if not admin.valid or admin.from_cache:
            raise PermissionError("A live, valid admin credential is required")
        if Role.TENANT_ADMIN not in admin.roles or admin.tenant_id != tenant_id:
            raise PermissionError("Only this tenant's admin may issue the QR")

        # Step 3: Check that every requested floor belongs to this tenant.
        population = self.ctx.services.get("population")
        tenants = getattr(population, "tenants", None)
        if not isinstance(tenants, dict) or tenant_id not in tenants:
            raise PermissionError("Tenant information is unavailable")
        if not set(floors).issubset(set(tenants[tenant_id])):
            raise PermissionError("One or more floors belong to another tenant")

        # Step 4: ACS creates the credential and manages expiry/revocation.
        return self.ctx.services["acs"].issue_visitor_pass(
            tenant_id=tenant_id,
            floors=set(floors),
            valid_from=float(valid_from),
            valid_to=float(valid_to),
            group_size=1,
        )
