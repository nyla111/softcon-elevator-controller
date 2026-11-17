"""Part 2 Dispatcher: call handling (SRS 4.1), modes (4.3), FR-100/101, FR-04 re-assignment.

Flow of one call (UC-01, SD-01):
  CallRequested -> (VIP: Part 5 claim_call) -> candidate cars -> EtdEngine via the FR-105 guard
  -> register_stop(min_dwell_s from Part 5) -> CallAssigned.
If no car can take it now the call waits in the pending queue (ordered by Part 5, BR-10) and is
retried every second. The wait clock is Call.created_at and is never reset (4.3).

Book-keeping: the dispatcher knows each call's destination, so it keeps, per car, who rides and
who waits for it. The trip model (model.py) replays that to predict waits (FR-13, predicted_wait).

Dispatch is paused (calls queue up, no command is sent) while:
  * the EMS server is down (EmsServerDown .. EmsServerUp): controllers serve calls themselves,
    so calls that arrive or are still unassigned then are left to them (no double stops);
  * fire (FireAlarm until it is cleared and car_states.safety_active() is False, FR-50/FR-55);
    the controllers dropped every stop, so waiting calls go back to the queue and are
    re-assigned after recovery (FR-04: fire cancels dispatch instead of re-assigning);
  * mains lost (FR-110) until GeneratorReady / MainsRestored; car_states.safety_active() also.
"""
from __future__ import annotations

from collections import deque
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (CallCancelled, CallReplaced, CallRequested, CarStateChanged,
                                  ControllerFault, EmsServerDown, EmsServerUp, FireAlarm,
                                  FireAlarmCleared, GeneratorReady, HeartbeatLost, MainsLost,
                                  MainsRestored, OverloadDetected, PassengerAlighted,
                                  PassengerBoarded, PriorityTripEnded, PriorityTripStarted)
from ems.contracts.types import (Call, CallId, CallPriority, CarId, CarState, CarStatus, GroupId,
                                 Mode)

from .assign import AssignMixin
from .ceiling import CeilingMixin
from .engine import EtdEngine
from .model import Pax, replay
from .modes import ModeManager
from .pauses import PauseMixin
from .policy import PolicyMixin
from .records import BY, OPEN_STATES, Rec  # noqa: F401  (Rec, OPEN_STATES re-exported)

TICK_S = 1.0
RISK_EVERY_S = 2.0
POLICY_EVERY_S = 5.0


class Dispatcher(AssignMixin, PauseMixin, CeilingMixin, PolicyMixin):
    """ctx.services["dispatcher"] (Dispatcher protocol) and the trip book for the engine.

    Modules: assign.py (assigning, FR-04/100/101), pauses.py (server loss, fire, power),
    ceiling.py (FR-13, FR-14), policy.py (parking, Eco Standby), records.py (Rec).
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.building = ctx.services.get("building")
        groups = (self.building.group_names if self.building is not None
                  else list(config.GROUPS))
        self.modes = ModeManager(ctx, groups)
        self.modes.listeners.append(self._on_mode_changed)
        self.engine = EtdEngine(self)
        self.calls: dict[CallId, Rec] = {}
        self.open: set[CallId] = set()                  # pending or assigned (not picked up)
        self.recent: deque[Call] = deque()              # calls of the last 10 min (traffic mix)
        self.waiting: dict[CarId, set[CallId]] = {}     # assigned, not yet boarded
        self.riders: dict[CarId, set[CallId]] = {}
        self.exclusive: set[CarId] = set()              # Part 5 priority trips (VIP, medical...)
        self.standby: set[CarId] = set()                # Eco standby cars we put there
        self.refused_until: dict[CarId, float] = {}
        self.persons_limit: dict[GroupId, int] = {}      # FR-101 lower limit set by the FM
        self.ems_down = False
        self.fire_hold = False
        self.fire_cleared = False
        self.power_hold = False
        self.pickups: deque[tuple[float, GroupId, float]] = deque()   # (t, group, wait) 5 min
        self.over_since: dict[GroupId, Optional[float]] = {}
        self.breaches: list[dict] = []                  # FR-13 log: call, cause, eventual wait
        self.risk_log: list[tuple[float, CallId, float, float]] = []
        self.overloads: list[dict] = []                 # FR-100 evidence
        self.reassign_log: list[tuple[float, CallId, CarId, CarId, str]] = []
        self.parked: dict[CarId, int] = {}              # last park floor we commanded
        self._next_risk = 0.0
        self._next_policy = 0.0
        self._last_breach_alert: dict[GroupId, float] = {}
        self.active = True

    # ================================================================ wiring

    def subscribe(self) -> None:
        on = self._on
        on(CallRequested, self.on_call_requested)
        on(CallReplaced, self.on_call_replaced)
        on(CallCancelled, self.on_call_cancelled)
        on(PassengerBoarded, self.on_boarded)
        on(PassengerAlighted, self.on_alighted)
        on(OverloadDetected, self.on_overload)
        on(ControllerFault, lambda e: self.reassign_calls_of(e.car_id, "fault"))
        on(HeartbeatLost, lambda e: self.reassign_calls_of(e.car_id, "link_lost"))
        on(CarStateChanged, self.on_car_state)
        on(PriorityTripStarted, self.on_priority_started)
        on(PriorityTripEnded, lambda e: self.exclusive.discard(e.car_id))
        on(FireAlarm, self.on_fire)
        on(FireAlarmCleared, self.on_fire_cleared)
        on(MainsLost, self.on_mains_lost)
        on(GeneratorReady, self.on_power_back)
        on(MainsRestored, self.on_power_back)
        on(EmsServerDown, self.on_server_down)
        on(EmsServerUp, self.on_server_up)
        self.ctx.clock.every(TICK_S, lambda: self.tick() if self.active else None)

    def _on(self, event_type, handler) -> None:
        self.ctx.bus.subscribe(event_type, lambda e: handler(e) if self.active else None)

    def detach(self) -> None:
        """Stop reacting to anything (tests of the stand-ins build the full app, then detach)."""
        self.active = False

    def _svc(self, name: str):
        return self.ctx.services[name]

    def _now(self) -> float:
        return self.ctx.clock.now()

    # ================================================================ Dispatcher protocol

    def current_mode(self, group: GroupId) -> Mode:
        return self.modes.mode(group)

    def predicted_wait(self, call_id: CallId) -> float:
        """Total predicted wait in seconds since the call was made (elapsed + still to wait)."""
        rec = self.calls.get(call_id)
        if rec is None:
            return 0.0
        if rec.picked_up_at is not None:
            return rec.picked_up_at - rec.call.created_at
        elapsed = self.elapsed(call_id)
        if rec.state == "assigned" and rec.car is not None:
            status = self._status(rec.car)
            if status is not None:
                plan = replay(status, self.riders_of(rec.car), self.waiting_of(rec.car),
                              self.capacity_of(status), self.extra_load_of(status))
                if call_id in plan.pickup:
                    return elapsed + plan.pickup[call_id]
        best = self._best_eta(rec.call, exclude=())
        return elapsed + (best if best is not None else self._ceiling(rec.call.group))

    def pending_calls(self, group: GroupId) -> list[Call]:
        """Calls of the group not yet picked up (queued or assigned and waiting)."""
        return [self.calls[c].call for c in sorted(self.open)
                if self.calls[c].call.group == group]

    def would_breach_ceiling_without(self, car_id: CarId) -> bool:
        """True if taking this car away pushes a waiting call's elapsed + ETA over the ceiling."""
        status = self._status(car_id)
        if status is None:
            return False
        group = status.group
        ceiling = self._ceiling(group)
        for rec in [self.calls[c] for c in sorted(self.open)]:
            if rec.call.group != group:
                continue
            if rec.state == "assigned" and rec.car != car_id:
                continue
            eta = self._best_eta(rec.call, exclude={car_id})
            if eta is None or self.elapsed(rec.call.call_id) + eta > ceiling:
                return True
        return False

    # ================================================================ extra FM / test API (not in the protocol)

    def set_manual_mode(self, mode: Mode, group: Optional[GroupId] = None, until=None) -> None:
        """FR-12 (by the FM, through Part 6's admin page)."""
        self.modes.set_manual(mode, group, until)

    def set_persons_limit(self, group: GroupId, persons: Optional[int]) -> None:
        """FR-101: a lower persons-per-trip limit set by the FM (e.g. health emergency)."""
        if persons is None:
            self.persons_limit.pop(group, None)
        else:
            self.persons_limit[group] = max(1, int(persons))

    # ================================================================ engine book

    def riders_of(self, car_id: CarId) -> list[Pax]:
        return [self.pax_of(self.calls[c].call, on_board=True)
                for c in sorted(self.riders.get(car_id, ()))]

    def waiting_of(self, car_id: CarId) -> list[Pax]:
        return [self.pax_of(self.calls[c].call)
                for c in sorted(self.waiting.get(car_id, ()),
                                key=lambda c: (self.calls[c].call.created_at, c))]

    def pax_of(self, call: Call, on_board: bool = False) -> Pax:
        acc = call.priority is CallPriority.ACCESSIBLE
        rec = self.calls.get(call.call_id)
        dwell = max(config.SIM["accessible_dwell_s"] if acc else 0.0,
                    rec.min_dwell if rec is not None else 0.0)
        return Pax(call.call_id, None if on_board else call.origin, call.destination,
                   call.party_size, acc, dwell)

    def capacity_of(self, status: CarStatus) -> int:
        rated = self._rated(status.group)
        limit = self.persons_limit.get(status.group)
        return min(rated, limit) if limit else rated

    def extra_load_of(self, status: CarStatus) -> int:
        known = sum(self.calls[c].call.party_size for c in self.riders.get(status.car_id, ()))
        return max(0, status.passengers - known)

    def elapsed(self, call_id: str) -> float:
        rec = self.calls.get(call_id)
        return 0.0 if rec is None else max(0.0, self._now() - rec.call.created_at)

    # ================================================================ events

    def on_call_requested(self, ev: CallRequested) -> None:
        call = ev.call
        rec = Rec(call, self.open)
        self.calls[call.call_id] = rec
        self.recent.append(call)
        if self.ems_down:
            rec.state = "controller"     # the controllers' own group logic serves it
            return
        self._dispatch(rec)

    def on_call_replaced(self, ev: CallReplaced) -> None:
        self._drop(ev.old_call_id, "cancelled", cancel_stop=True)
        self.on_call_requested(CallRequested(ev.t, ev.new_call))

    def on_call_cancelled(self, ev: CallCancelled) -> None:
        self._drop(ev.call_id, "cancelled", cancel_stop=True)

    def on_boarded(self, ev: PassengerBoarded) -> None:
        rec = self.calls.get(ev.call_id)
        if rec is None:
            return
        self._unlink(rec)
        rec.state, rec.car, rec.avoid = "riding", ev.car_id, None
        rec.picked_up_at = ev.t
        self.riders.setdefault(ev.car_id, set()).add(ev.call_id)
        wait = ev.t - rec.call.created_at
        self.pickups.append((ev.t, rec.call.group, wait))
        ceiling = self._ceiling(rec.call.group)
        if wait > ceiling:
            self._log_breach(rec, wait, ceiling)

    def on_alighted(self, ev: PassengerAlighted) -> None:
        rec = self.calls.get(ev.call_id)
        if rec is None:
            return
        self._unlink(rec)
        rec.state = "done"

    def on_car_state(self, ev: CarStateChanged) -> None:
        if ev.new in (CarState.FIRE_RECALL, CarState.EMERGENCY_POWER):
            return                                    # handled as a dispatch pause
        if not ev.new.dispatchable and not (ev.new is CarState.STANDBY and
                                            ev.requested_by == BY):
            self.reassign_calls_of(ev.car_id, f"state_{ev.new.name.lower()}")

    def on_priority_started(self, ev: PriorityTripStarted) -> None:
        self.exclusive.add(ev.car_id)

    # ================================================================ periodic work

    def tick(self) -> None:
        now = self._now()
        self.modes.tick()
        if self._may_dispatch():
            self._retry_queue()
            if now >= self._next_policy:
                self._next_policy = now + POLICY_EVERY_S
                self._apply_policy()
            if now >= self._next_risk:
                self._next_risk = now + RISK_EVERY_S
                self._check_risk()
        self._check_step_up()

    # ================================================================ helpers

    def _recent(self, group: GroupId, window_s: float) -> list[Call]:
        now = self._now()
        while self.recent and self.recent[0].created_at < now - 600.0:
            self.recent.popleft()
        return [c for c in self.recent if c.group == group and c.created_at >= now - window_s]

    def _status(self, car_id: CarId) -> Optional[CarStatus]:
        try:
            return self._svc("controllers").status(car_id)
        except KeyError:
            return None

    def _group_of(self, car_id: CarId) -> Optional[GroupId]:
        s = self._status(car_id)
        return s.group if s else None

    def _floors(self, group: GroupId) -> list[int]:
        if self.building is not None:
            return list(self.building.group(group).floors)
        return list(config.GROUPS[group]["floors"])

    def _rated(self, group: GroupId) -> int:
        if self.building is not None:
            return self.building.group(group).rated_persons
        return config.RATED["S" if group == "SERVICE" else "passenger"]["persons"]

    def _ceiling(self, group: GroupId) -> float:
        return float(config.MODE_TARGETS[self.current_mode(group)][1])
