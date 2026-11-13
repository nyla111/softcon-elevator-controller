"""HTTP API for Part 6. Identity is simulated with X-User and X-Role headers."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ems.contracts.types import Role

router = APIRouter(prefix="/api/monitor", tags=["monitor"])
_context: Callable[[], Any] | None = None


def bind_context(provider: Callable[[], Any]) -> None:
    global _context
    _context = provider


def _service():
    if _context is None:
        raise HTTPException(503, "monitor context is not bound")
    return _context().get("reports")


def _identity(user: str | None, role_name: str | None):
    if not user or not role_name:
        raise HTTPException(401, "X-User and X-Role headers are required")
    try:
        return user, Role(role_name)
    except ValueError:
        raise HTTPException(401, "unknown role")


def _allow(identity, roles: set[Role]):
    if identity[1] not in roles:
        raise HTTPException(403, "role is not allowed")
    return identity


class ReviewBody(BaseModel):
    decision: str
    evidence: str
    responsible_credential: str | None = None


class ConfigBody(BaseModel):
    value: Any


@router.get("/report")
def report(period: str = "month", mode: str | None = None,
           x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.OWNER, Role.FM, Role.SYSADMIN})
    try:
        return _service().monthly_report() if period == "month" and mode is None \
            else _service().period_report(period, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
@router.get("/incidents")
def incidents(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.SECURITY, Role.SYSADMIN})
    return list(_service().incidents.values())


@router.post("/incidents/{event_id}/review")
def review(event_id: str, body: ReviewBody, x_user: str | None = Header(None),
           x_role: str | None = Header(None)):
    user, _ = _allow(_identity(x_user, x_role), {Role.SECURITY})
    try:
        return _service().review_incident(event_id, body.decision, user, body.evidence,
                                          body.responsible_credential)
    except KeyError:
        raise HTTPException(404, "incident not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/audit")
def audit(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.SYSADMIN, Role.OWNER})
    return _service().audit_log


@router.get("/configuration")
def configuration(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.OWNER, Role.FM, Role.SYSADMIN})
    return _service().configuration


@router.put("/configuration/{key}")
def configure(key: str, body: ConfigBody, x_user: str | None = Header(None),
              x_role: str | None = Header(None)):
    user, role = _allow(_identity(x_user, x_role), {Role.FM, Role.SYSADMIN})
    try:
        return _service().update_configuration(key, body.value, user, role)
    except ValueError as exc:
        raise HTTPException(400, str(exc))

__all__ = ["bind_context", "router"]
