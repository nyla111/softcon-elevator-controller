"""Maintenance (SRS 4.5): FR-30 fault alerts, FR-31 usage counters and service reminders,
FR-32 technician maintenance in / out, FR-33 second car out in Rush needs FM approval.

* FR-30: one AlertRaised("high", "fault", ["contractor", "fm"]) per fault episode of a car
  (a repeated ControllerFault while the car is still faulted is not alerted again), and one
  info "fault_cleared" when it clears. Part 5 is the only module that raises kind "fault";
  entrapment alerts (FR-112) are Part 4's.
* FR-31: counters come from telemetry, not from status ticks:
  door_cycles (one per DoorsOpened), floors_travelled (floor changes between consecutive
  CarStatusUpdated), starts (status changes from standing to moving). Reminder thresholds are
  NOT in the repo's sources, so there is no default: the FM / technician sets them
  (set_service_threshold). A reminder (info "service_due") is sent once per threshold until the
  technician closes a maintenance visit (exit_maintenance resets the "since service" counters).
* FR-32: technician card (role from Access) puts a car into MAINTENANCE through car_states and
  closes it to new calls; exit releases the state. Part 4 decides what the car becomes (a car
  that is still faulted stays FAULTED: no recovery is forced by Part 5).
* FR-33: in Rush (Part 2's current_mode), taking a car out while another car of the same group
  is already out needs FM approval. The request waits up to SECOND_CAR_APPROVAL_TIMEOUT_S
  (600 s, simulated clock), then expires; a late approval is refused.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (CarStatusUpdated, ControllerFault, ControllerFaultCleared,
                                  DoorsOpened)
from ems.contracts.types import CarId, CarState, CredentialId, Mode, Role

from .service import Outcome, PriorityService

COUNTERS = ("door_cycles", "floors_travelled", "starts")
OUT_STATES = frozenset({CarState.FAULTED, CarState.MAINTENANCE, CarState.INDEPENDENT_SERVICE,
                        CarState.FIRE_RECALL, CarState.EMERGENCY_POWER})


def _floor_index(f: int) -> int:
    return f if f < 0 else f - 1        # there is no floor 0


@dataclass
class RemovalRequest:
    request_id: str
    car_id: CarId
    group: str
    requested_at: float
    expires_at: float
    status: str = "pending"             # pending | approved | denied | expired | entered
    result: Optional[str] = None


class Maintenance:
    def __init__(self, service: PriorityService) -> None:
        self.s = service
        self.ctx = service.ctx
        self.requests: dict[str, RemovalRequest] = {}
        self.faulted: set[CarId] = set()
        self.totals: dict[CarId, dict[str, int]] = {}
        self.since_service: dict[CarId, dict[str, int]] = {}
        self.thresholds: dict[str, int] = {}
        self.reminded: dict[CarId, set[str]] = {}
        self._last: dict[CarId, tuple] = {}               # car -> (floor, position)
        self._moving: dict[CarId, bool] = {}

    # ---------------------------------------------------------------- FR-30
    def on_fault(self, ev: ControllerFault) -> None:
        if ev.car_id in self.faulted:
            return
        self.faulted.add(ev.car_id)
        details = [ev.code] + (["between floors"] if ev.between_floors else []) + (
            ["doors stuck"] if ev.doors_stuck else [])
        floor = self.ctx.get("controllers").status(ev.car_id).floor
        self.s.alert("high", "fault", ["contractor", "fm"],
                     f"Car {ev.car_id} fault: {', '.join(details)}", ev.car_id, floor)

    def on_fault_cleared(self, ev: ControllerFaultCleared) -> None:
        if ev.car_id in self.faulted:
            self.faulted.discard(ev.car_id)
            self.s.alert("info", "fault_cleared", ["contractor", "fm"],
                         f"Car {ev.car_id} fault cleared", ev.car_id)

    # ---------------------------------------------------------------- FR-31
    def _count(self, car_id: CarId, counter: str, n: int) -> None:
        if n <= 0:
            return
        for table in (self.totals, self.since_service):
            row = table.setdefault(car_id, dict.fromkeys(COUNTERS, 0))
            row[counter] += n
        limit = self.thresholds.get(counter)
        done = self.reminded.setdefault(car_id, set())
        if limit is not None and counter not in done and \
                self.since_service[car_id][counter] >= limit:
            done.add(counter)
            self.s.alert("info", "service_due", ["contractor", "fm"],
                         f"Car {car_id}: {counter} since last service reached {limit}", car_id)

    def on_doors_opened(self, ev: DoorsOpened) -> None:
        self._count(ev.car_id, "door_cycles", 1)

    def on_status(self, ev: CarStatusUpdated) -> None:
        st = ev.status
        last = self._last.get(st.car_id)
        self._last[st.car_id] = (st.floor, st.position)
        if last is None:
            return
        self._count(st.car_id, "floors_travelled",
                    abs(_floor_index(st.floor) - _floor_index(last[0])))
        moving = st.position != last[1]
        if moving and not self._moving.get(st.car_id, False):
            self._count(st.car_id, "starts", 1)
        self._moving[st.car_id] = moving

    def set_service_threshold(self, credential_id: CredentialId, counter: str,
                              value: Optional[int]) -> Outcome:
        actor, err = self.s.authorize(credential_id, "usage.threshold", Role.FM,
                                      Role.TECHNICIAN)
        if err:
            return err
        if counter not in COUNTERS or (value is not None and value <= 0):
            self.s.record("usage.threshold", actor, False, "invalid_threshold")
            return Outcome(False, "invalid_threshold")
        if value is None:
            self.thresholds.pop(counter, None)
        else:
            self.thresholds[counter] = value
        self.s.record("usage.threshold", actor, True, "ok")
        return Outcome(True)

    def usage(self, car_id: CarId) -> dict:
        zero = dict.fromkeys(COUNTERS, 0)
        return {"total": dict(self.totals.get(car_id, zero)),
                "since_service": dict(self.since_service.get(car_id, zero))}

    # ---------------------------------------------------------------- FR-32 / FR-33
    def _out(self, car_id: CarId) -> bool:
        """Is this car already out of passenger service (fault, link, maintenance, ...)?"""
        if not self.s.healthy(self.s.status(car_id)):
            return True
        hold = self.s.holds.get(car_id)
        if hold is not None and hold.kind in ("maintenance", "goods"):
            return True
        return self.ctx.get("car_states").state(car_id) in OUT_STATES

    def needs_approval(self, car_id: CarId) -> bool:
        group = self.s.group_of_car(car_id)
        if group is None or self._out(car_id):
            return False
        if self.ctx.get("dispatcher").current_mode(group) is not Mode.RUSH:
            return False
        others = [c for c in self.s.building().group(group).cars if c != car_id]
        return any(self._out(c) for c in others)

    def request_maintenance(self, credential_id: CredentialId, car_id: CarId) -> Outcome:
        """FR-32 entry (technician). ok=True: the car is in MAINTENANCE now. ok=False with
        reason "approval_required" and ref=request id: FR-33, waiting for the FM."""
        actor, err = self.s.authorize(credential_id, "maintenance.enter", Role.TECHNICIAN)
        if err:
            return err
        if self.s.group_of_car(car_id) is None:
            out = Outcome(False, "unknown_car")
        elif car_id in self.s.holds:
            out = Outcome(False, f"car_held:{self.s.holds[car_id].kind}")
        elif any(r.car_id == car_id and r.status == "pending" for r in self.requests.values()):
            pending = next(r for r in self.requests.values()
                           if r.car_id == car_id and r.status == "pending")
            out = Outcome(False, "approval_required", pending.request_id, car_id)
        elif self.needs_approval(car_id):
            out = self._ask_fm(car_id)
        else:
            out = self._enter(car_id, self.s.next_id("mnt"))
        self.s.record("maintenance.enter", actor, out.ok, out.reason, out.ref, car_id)
        return out

    def _ask_fm(self, car_id: CarId) -> Outcome:
        now = self.s.now()
        rid = self.s.next_id("rm")
        timeout = config.SECOND_CAR_APPROVAL_TIMEOUT_S
        req = RemovalRequest(rid, car_id, self.s.group_of_car(car_id), now, now + timeout)
        self.requests[rid] = req
        self.ctx.clock.call_later(timeout, lambda: self._expire(rid))
        self.s.alert("high", "removal_approval", ["fm"],
                     f"Technician asks to take car {car_id} out during Rush while another car "
                     f"of group {req.group} is out. Approve within {timeout / 60:.0f} min "
                     f"(request {rid}).", car_id)
        return Outcome(False, "approval_required", rid, car_id)

    def _expire(self, rid: str) -> None:
        req = self.requests[rid]
        if req.status != "pending":
            return                                       # decided before the timeout
        req.status = "expired"
        self.s.alert("info", "removal_expired", ["contractor", "fm"],
                     f"Request {rid} to take car {req.car_id} out expired without FM approval",
                     req.car_id)

    def approve_removal(self, credential_id: CredentialId, rid: str) -> Outcome:
        return self._decide(credential_id, rid, True)

    def deny_removal(self, credential_id: CredentialId, rid: str) -> Outcome:
        return self._decide(credential_id, rid, False)

    def _decide(self, credential_id: CredentialId, rid: str, approve: bool) -> Outcome:
        action = "removal.approve" if approve else "removal.deny"
        actor, err = self.s.authorize(credential_id, action, Role.FM)
        if err:
            return err
        req = self.requests.get(rid)
        if req is None:
            out = Outcome(False, "unknown_request")
        elif req.status == "pending" and self.s.now() >= req.expires_at:
            req.status = "expired"                       # same instant as the timeout
            out = Outcome(False, "expired", rid)
        elif req.status != "pending":
            out = Outcome(False, req.status, rid)
        elif not approve:
            req.status = "denied"
            out = Outcome(True, "denied", rid, req.car_id)
        else:
            req.status = "approved"
            out = self._enter(req.car_id, rid)
            req.result = out.reason
            if out.ok:
                req.status = "entered"
        self.s.record(action, actor, out.ok, out.reason, rid, req.car_id if req else None)
        return out

    def _enter(self, car_id: CarId, ref: str) -> Outcome:
        why = self.s.blocked_reason()
        if why:
            return Outcome(False, why, ref, car_id)
        # a faulted / disconnected car cannot take commands: the state alone keeps it out
        command = ("close_to_new_calls", True) if self.s.healthy(self.s.status(car_id)) else None
        why = self.s.take(car_id, CarState.MAINTENANCE, "maintenance", ref, command)
        if why is not None:
            return Outcome(False, why, ref, car_id)
        self.ctx.get("dispatcher").reassign_calls_of(car_id, "maintenance")   # FR-04 (Part 2)
        return Outcome(True, "entered", ref, car_id)

    def exit_maintenance(self, credential_id: CredentialId, car_id: CarId) -> Outcome:
        """FR-32 exit (technician): release MAINTENANCE, reopen the car, close the service
        visit (FR-31 'since service' counters and reminders start again)."""
        actor, err = self.s.authorize(credential_id, "maintenance.exit", Role.TECHNICIAN)
        if err:
            return err
        hold = self.s.holds.get(car_id)
        if hold is None or hold.kind != "maintenance":
            out = Outcome(False, "not_in_maintenance", car_id=car_id)
        else:
            self.s.give_back(car_id, ("close_to_new_calls", False))
            self.since_service[car_id] = dict.fromkeys(COUNTERS, 0)
            self.reminded.pop(car_id, None)
            out = Outcome(True, "exited", hold.ref, car_id)
        self.s.record("maintenance.exit", actor, out.ok, out.reason, out.ref, car_id)
        return out

    def snapshot(self) -> dict:
        return {"requests": {r.request_id: {"car_id": r.car_id, "status": r.status,
                                            "expires_at": r.expires_at}
                             for r in self.requests.values()},
                "in_maintenance": sorted(c for c, h in self.s.holds.items()
                                         if h.kind == "maintenance"),
                "thresholds": dict(self.thresholds)}


def subscribe(ctx, m: Maintenance) -> None:
    ctx.bus.subscribe(ControllerFault, m.on_fault)
    ctx.bus.subscribe(ControllerFaultCleared, m.on_fault_cleared)
    ctx.bus.subscribe(DoorsOpened, m.on_doors_opened)
    ctx.bus.subscribe(CarStatusUpdated, m.on_status)
