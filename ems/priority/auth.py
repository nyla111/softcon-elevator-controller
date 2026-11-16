"""Who may call the Part 5 API.

Identity is a credential id (card). Its roles come from the Access service (Part 3, contract
`AccessService.permission`), never from a role string chosen by the caller. A credential that
is unknown, invalid or cannot be looked up (ACS timeout) has no roles.

Tests and scenario setups may install another authorizer with
`PriorityService.use_authorizer(...)`: while Part 3's access service is the contract stub (it
answers EMPLOYEE for every card), the scenarios read the staff cards of Part 1's mock ACS
(standins.AcsStaffAuthorizer).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from ems.contracts.types import CredentialId, Role


@dataclass(frozen=True)
class Actor:
    """The answer for one credential. Never copied into trip records or events."""
    credential_id: CredentialId
    roles: frozenset[Role]
    tenant_id: Optional[str] = None

    def has(self, *roles: Role) -> bool:
        return any(r in self.roles for r in roles)


class Authorizer(Protocol):
    def actor(self, credential_id: CredentialId) -> Actor: ...


class AccessAuthorizer:
    """Production path: ask ctx.services['access'] (looked up at call time)."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def actor(self, credential_id: CredentialId) -> Actor:
        try:
            perm = self.ctx.get("access").permission(credential_id)
        except (TimeoutError, KeyError):
            return Actor(credential_id, frozenset())
        if not perm.valid:
            return Actor(credential_id, frozenset())
        return Actor(credential_id, frozenset(perm.roles), perm.tenant_id)
