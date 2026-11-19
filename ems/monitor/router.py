"""HTTP API for Part 6. Identity is simulated with X-User and X-Role headers."""
from __future__ import annotations

from dataclasses import asdict
from datetime import timedelta
from typing import Any, Callable

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

from ems.contracts.types import Mode, Role
from ems.monitor.service import ROLE_PERMISSIONS

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


class ChangeBody(BaseModel):
    action: str
    target: str
    old: Any
    new: Any


class ManualModeBody(BaseModel):
    credential_id: str
    mode: str
    group: str | None = None
    duration_s: float | None = None


class RecoveryBody(BaseModel):
    credential_id: str


class RemovalDecisionBody(BaseModel):
    credential_id: str
    approve: bool = True


def _credential_has(credential_id: str, role: Role) -> bool:
    if _context is None:
        return False
    try:
        permission = _context().get("access").permission(credential_id)
    except Exception:
        return False
    return bool(permission.valid and role in permission.roles)


@router.get("/report")
def report(period: str = "month", mode: str | None = None,
           x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.OWNER, Role.FM, Role.SYSADMIN})
    try:
        return _service().monthly_report() if period == "month" and mode is None \
            else _service().period_report(period, mode)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/permissions")
def permissions(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _, role = _identity(x_user, x_role)
    return {"role": role.value, "permissions": sorted(ROLE_PERMISSIONS.get(role, ())) }


@router.get("/tenant-report")
def tenant_report(x_user: str | None = Header(None), x_role: str | None = Header(None),
                  x_tenant: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.TENANT_ADMIN})
    if not x_tenant:
        raise HTTPException(400, "X-Tenant header is required")
    return _service().tenant_report(x_tenant)


@router.get("/diagnostics")
def diagnostics(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.TECHNICIAN, Role.FM, Role.SYSADMIN})
    return {"fault_history": _service().fault_history}


@router.get("/wear")
def wear(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.OWNER, Role.FM, Role.TECHNICIAN, Role.SYSADMIN})
    ctx = _context()
    if "usage" not in ctx.services:
        raise HTTPException(503, "usage service is not registered")
    return ctx.get("usage").wear_report()


@router.get("/recovery")
def recovery_status(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.FM})
    return _context().get("car_states").recovery_status()


@router.post("/recovery/confirm")
def confirm_recovery(body: RecoveryBody, x_user: str | None = Header(None),
                     x_role: str | None = Header(None)):
    user, role = _allow(_identity(x_user, x_role), {Role.FM})
    if not _credential_has(body.credential_id, Role.FM):
        raise HTTPException(403, "a valid facility-manager credential is required")
    states = _context().get("car_states")
    before = states.recovery_status()
    if not states.confirm_recovery(Role.FM):
        raise HTTPException(409, {"message": "recovery is not eligible",
                                  "blockers": before["blockers"]})
    _service().audit(user, role, "safety.recovery.confirm", "building", before,
                     states.recovery_status())
    return {"confirmed": True, "status": states.recovery_status()}


@router.post("/mode")
def set_manual_mode(body: ManualModeBody, x_user: str | None = Header(None),
                    x_role: str | None = Header(None)):
    user, role = _allow(_identity(x_user, x_role), {Role.FM})
    if not _credential_has(body.credential_id, Role.FM):
        raise HTTPException(403, "a valid facility-manager credential is required")
    try:
        mode = Mode(body.mode)
    except ValueError:
        raise HTTPException(400, "mode must be rush, balanced or eco")
    ctx = _context()
    if body.group is not None and body.group not in ctx.get("building").group_names:
        raise HTTPException(400, "unknown elevator group")
    if body.duration_s is not None and body.duration_s <= 0:
        raise HTTPException(400, "duration_s must be positive")
    dispatcher = ctx.get("dispatcher")
    groups = [body.group] if body.group else list(ctx.get("building").group_names)
    old = {group: dispatcher.current_mode(group).value for group in groups}
    until = (ctx.clock.now_dt() + timedelta(seconds=body.duration_s)
             if body.duration_s is not None else None)
    dispatcher.set_manual_mode(mode, body.group, until)
    new = {group: dispatcher.current_mode(group).value for group in groups}
    _service().audit(user, role, "mode.change", body.group or "all", old, new)
    return {"mode": mode.value, "group": body.group, "until": until, "old": old, "new": new}


@router.get("/maintenance/requests")
def removal_requests(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.FM})
    requests = _context().get("priority").maintenance.requests.values()
    return [asdict(request) for request in requests if request.status == "pending"]


@router.post("/maintenance/requests/{request_id}")
def decide_removal(request_id: str, body: RemovalDecisionBody,
                   x_user: str | None = Header(None), x_role: str | None = Header(None)):
    user, role = _allow(_identity(x_user, x_role), {Role.FM})
    if not _credential_has(body.credential_id, Role.FM):
        raise HTTPException(403, "a valid facility-manager credential is required")
    maintenance = _context().get("priority").maintenance
    request = maintenance.requests.get(request_id)
    old = asdict(request) if request else None
    outcome = (maintenance.approve_removal if body.approve else maintenance.deny_removal)(
        body.credential_id, request_id)
    if not outcome.ok:
        raise HTTPException(409, {"reason": outcome.reason, "request_id": request_id})
    _service().audit(user, role, "car_state.change", request_id, old,
                     asdict(maintenance.requests[request_id]))
    return asdict(outcome)


@router.post("/changes")
def human_change(body: ChangeBody, x_user: str | None = Header(None),
                 x_role: str | None = Header(None)):
    user, role = _allow(_identity(x_user, x_role), {Role.FM, Role.TECHNICIAN})
    try:
        return _service().record_human_change(user, role, body.action, body.target,
                                              body.old, body.new)
    except PermissionError as exc:
        raise HTTPException(403, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.get("/incidents")
def incidents(x_user: str | None = Header(None), x_role: str | None = Header(None)):
    _allow(_identity(x_user, x_role), {Role.SECURITY, Role.SYSADMIN})
    return [row for row in _service().incidents.values() if row["status"] != "safety_alert"]


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
