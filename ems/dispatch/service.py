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

from collections import Counter, deque
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (AlertRaised, CallAssigned, CallCancelled, CallReassigned,
                                  CallReplaced, CallRequested, CarStateChanged, ControllerFault,
                                  EmsServerDown, EmsServerUp, FireAlarm, FireAlarmCleared,
                                  GeneratorReady, HeartbeatLost, MainsLost, MainsRestored,
                                  OverloadDetected, PanelMessage, PassengerAlighted,
                                  PassengerBoarded, PriorityTripEnded, PriorityTripStarted,
                                  WaitCeilingRisk)
from ems.contracts.types import (Call, CallId, CallPriority, CarId, CarState, CarStatus,
                                 DoorState, Direction, GroupId, Mode)

from .engine import EtdEngine
from .model import Pax, replay
from .modes import ModeManager

TICK_S = 1.0
RISK_EVERY_S = 2.0
POLICY_EVERY_S = 5.0
REFUSED_BACKOFF_S = 5.0          # a car that refused a stop is not asked again for 5 s
LOBBY_HOLD_MAX_S = 10.0          # Rush up-peak: longest door hold asked at the lobby
LOBBY_HOLD_MIN_S = 6.0           # shorter holds are not worth asking
BALANCED_LOBBY_CARS = 1          # Balanced: idle cars parked at the lobby (rest near demand)
ECO_ACTIVE_CARS = 2              # Eco: cars per group kept in service (4.3: "at least 1")
BY = "dispatch"                  # requested_by for car_states


OPEN_STATES = ("pending", "assigned")


class Rec:
    """One call as the dispatcher sees it. Changing `state` keeps the open-call index current
    (pending or assigned calls), so periodic work never scans the whole call history."""

    def __init__(self, call: Call, index: set) -> None:
        self.call = call
        self._index = index
        self._state = ""
        self.car: Optional[CarId] = None
        self.prev_car: Optional[CarId] = None   # car before fire / server loss (CallReassigned)
        self.avoid: Optional[CarId] = None      # must leave this car (FR-04) when another can
        self.eta = 0.0
        self.risk_sent = False
        self.picked_up_at: Optional[float] = None
        self.min_dwell = 0.0                    # door-open time asked with the stop
        self.state = "pending"  # pending | assigned | riding | done | cancelled | controller | vip

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value
        if value in OPEN_STATES:
            self._index.add(self.call.call_id)
        else:
            self._index.discard(self.call.call_id)


class Dispatcher:
    """ctx.services["dispatcher"] (Dispatcher protocol) and the trip book for the engine."""

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

    def reassign_calls_of(self, car_id: CarId, reason: str) -> list[CallId]:
        """FR-04: move every call still waiting for this car to another car. Returns the calls
        moved now; the others keep trying every second (they never board this car meanwhile
        only if the controller drops the stop; Part 5 checks the returned list)."""
        moved: list[CallId] = []
        for cid in sorted(self.waiting.get(car_id, set()),
                          key=lambda c: (self.calls[c].call.created_at, c)):
            rec = self.calls[cid]
            rec.avoid = car_id
            if self._dispatch(rec, exclude={car_id}, reason=reason):
                moved.append(cid)
        return moved

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

    # ---------- extra FM / test API (not in the protocol) ----------
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

    # ---------- FR-100 overload ----------
    def on_overload(self, ev: OverloadDetected) -> None:
        """Step-out message naming the next car now; re-assign the calls left at that floor
        once the boarding in progress is over (the controller boards first come first)."""
        here = [self.calls[c] for c in self.waiting.get(ev.car_id, set())
                if self.calls[c].call.origin == ev.floor]
        nxt = None
        if here and self._may_dispatch():
            first = min(here, key=lambda r: (r.call.created_at, r.call.call_id))
            cars = self._candidates(first.call, exclude={ev.car_id})
            if cars:
                guard = self._svc("dispatch_guard")
                nxt, _ = guard.choose_car(self.engine, first.call, cars,
                                          self.current_mode(first.call.group))
        en = "Car is overloaded, please step out." + (f" Next car: {nxt}" if nxt else "")
        vi = "Thang quá tải, vui lòng bước ra." + (f" Thang tiếp theo: {nxt}" if nxt else "")
        targets = [f"car:{ev.car_id}"] + sorted({r.call.panel_id for r in here if r.call.panel_id})
        for target in targets:
            self.ctx.bus.publish(PanelMessage(ev.t, target, en, vi, True))
        self.overloads.append({"t": ev.t, "car_id": ev.car_id, "floor": ev.floor,
                               "next_car": nxt, "waiting": len(here)})
        riders_out = sum(1 for c in self.riders.get(ev.car_id, ())
                         if self.calls[c].call.destination == ev.floor)
        delay = (riders_out + len(here)) * config.SIM["s_per_passenger"] + 0.5
        car, floor = ev.car_id, ev.floor
        self.ctx.clock.call_later(delay, lambda: self._reassign_left_behind(car, floor))

    def _reassign_left_behind(self, car_id: CarId, floor: int) -> None:
        for cid in sorted(self.waiting.get(car_id, set())):
            rec = self.calls[cid]
            if rec.call.origin == floor:
                rec.avoid = car_id
                self._dispatch(rec, exclude={car_id}, reason="overload")

    # ---------- dispatch pauses ----------
    def on_fire(self, ev: FireAlarm) -> None:
        self.fire_hold, self.fire_cleared = True, False
        self._requeue_all("fire")

    def on_fire_cleared(self, ev: FireAlarmCleared) -> None:
        self.fire_cleared = True

    def on_mains_lost(self, ev: MainsLost) -> None:
        self.power_hold = True

    def on_power_back(self, ev) -> None:
        self.power_hold = False

    def on_server_down(self, ev: EmsServerDown) -> None:
        self.ems_down = True
        for rec in [self.calls[c] for c in list(self.open)]:
            if rec.state == "pending":   # unassigned: the controllers take them now
                rec.state = "controller"

    def on_server_up(self, ev: EmsServerUp) -> None:
        self.ems_down = False

    def _requeue_all(self, why: str) -> None:
        """Fire recall: controllers dropped every stop. Waiting calls go back to the queue,
        keeping their wait clock; exclusive priority trips are over."""
        for car_id in list(self.waiting):
            for cid in list(self.waiting.get(car_id, ())):
                rec = self.calls[cid]
                self._unlink(rec)
                rec.prev_car, rec.car, rec.state = car_id, None, "pending"
        for rec in self.calls.values():
            if rec.state == "vip":
                rec.prev_car, rec.car, rec.state = rec.car, None, "pending"
        self.exclusive.clear()

    def _may_dispatch(self) -> bool:
        if self.ems_down or self.power_hold:
            return False
        safety = self._svc("car_states").safety_active()
        if self.fire_hold:
            if self.fire_cleared and not safety:
                self.fire_hold = False           # FR-55 recovery granted by Part 4
            else:
                return False
        return not safety

    # ================================================================ assigning
    def _dispatch(self, rec: Rec, exclude=(), reason: str = "") -> bool:
        """Assign (or re-assign) one call. True if a car registered the stop."""
        if not self._may_dispatch():
            return False
        call = rec.call
        old = rec.car if rec.state == "assigned" else None
        if call.priority is CallPriority.VIP and old is None and rec.state == "pending" \
                and rec.prev_car is None:
            car = self._svc("priority").claim_call(call)
            if car is not None:                       # Part 5 runs an exclusive trip
                rec.state, rec.car = "vip", car
                self.exclusive.add(car)
                self.ctx.bus.publish(CallAssigned(self._now(), call.call_id, car, 0.0))
                return True
        cars = self._candidates(call, exclude)
        guard = self._svc("dispatch_guard")
        controllers = self._svc("controllers")
        mode = self.current_mode(call.group)
        dwell = self._svc("priority").door_dwell_s(call)
        while cars:
            car_id, used_fallback = guard.choose_car(self.engine, call, cars, mode)
            if car_id is None:
                break
            if car_id == old:                         # staying is best: nothing to do
                return False
            eta0 = self.engine.last.get(car_id, (0.0, None))[1] if not used_fallback else None
            hold = self._lobby_hold(call, car_id, eta0, mode)
            ask = max(dwell, hold) if hold else dwell
            if controllers.register_stop(car_id, call.origin, call.call_id, min_dwell_s=ask):
                rec.min_dwell = hold
                eta = eta0
                if eta is None:
                    st = next(s for s in cars if s.car_id == car_id)
                    eta = abs(st.position - call.origin) * config.SIM["s_per_floor"]
                self._attach(rec, car_id, eta)
                frm = old or rec.prev_car
                if frm is not None and frm != car_id:
                    self.reassign_log.append((self._now(), call.call_id, frm, car_id,
                                              reason or "requeue"))
                    self.ctx.bus.publish(CallReassigned(self._now(), call.call_id, frm, car_id,
                                                        reason or "requeue"))
                else:
                    self.ctx.bus.publish(CallAssigned(self._now(), call.call_id, car_id, eta))
                rec.prev_car = None
                return True
            self.refused_until[car_id] = self._now() + REFUSED_BACKOFF_S
            cars = [c for c in cars if c.car_id != car_id]
        return False

    def _lobby_hold(self, call: Call, car_id: CarId, eta: Optional[float], mode: Mode) -> float:
        """Rush up-peak: keep a car at the lobby until the next car is about to arrive, so
        there is always a car to board (the wait clock stops at boarding). 0 = no hold."""
        lobby = self.building.lobby if self.building is not None else config.LOBBY
        if mode is not Mode.RUSH or call.origin != lobby or eta is None:
            return 0.0
        if not self._up_peak(call.group):
            return 0.0
        mine = self._status(car_id)
        if mine is None:
            return 0.0
        load = (self.extra_load_of(mine) + len(self.riders.get(car_id, ()))
                + sum(self.calls[c].call.party_size for c in self.waiting.get(car_id, ())
                      if self.calls[c].call.origin == lobby))
        if load + call.party_size >= self.capacity_of(mine) - 1:
            return 0.0                                     # full: let it go
        nxt = None
        for s in self._candidates(call, exclude={car_id}):
            if s.passengers == 0 and not self.waiting.get(s.car_id) and \
                    abs(s.position - lobby) < 0.5 and not s.pending_stops:
                return 0.0                                 # another car already waits here
            plan = replay(s, self.riders_of(s.car_id), self.waiting_of(s.car_id),
                          self.capacity_of(s), self.extra_load_of(s))
            end_floor = plan.end_floor if plan.end_floor is not None else s.floor
            back = plan.end_s + abs(end_floor - lobby) * config.SIM["s_per_floor"]
            nxt = back if nxt is None else min(nxt, back)
        if nxt is None:
            return 0.0
        hold = min(LOBBY_HOLD_MAX_S, nxt - eta)
        return hold if hold >= LOBBY_HOLD_MIN_S else 0.0

    def _up_peak(self, group: GroupId) -> bool:
        lobby = self.building.lobby if self.building is not None else config.LOBBY
        recent = self._recent(group, 300.0)
        if len(recent) < 5:
            return self.ctx.clock.now_dt().hour < 10
        return sum(1 for c in recent if c.origin == lobby) >= 0.6 * len(recent)

    def _attach(self, rec: Rec, car_id: CarId, eta: float) -> None:
        self._unlink(rec)
        rec.state, rec.car, rec.eta, rec.avoid = "assigned", car_id, eta, None
        self.waiting.setdefault(car_id, set()).add(rec.call.call_id)

    def _unlink(self, rec: Rec) -> None:
        cid = rec.call.call_id
        for book in (self.waiting, self.riders):
            for s in book.values():
                s.discard(cid)

    def _drop(self, call_id: CallId, state: str, cancel_stop: bool) -> None:
        rec = self.calls.get(call_id)
        if rec is None:
            return
        if cancel_stop and rec.state == "assigned" and rec.car and self._may_dispatch():
            self._svc("controllers").cancel_stop(rec.car, call_id)
        self._unlink(rec)
        rec.state = state

    def _candidates(self, call: Call, exclude=()) -> list[CarStatus]:
        """FR-02/04/05/08: cars of the call's group that may take a new hall call now."""
        group = call.group
        try:
            allowed = set(self._svc("car_states").dispatchable(group))
        except KeyError:
            return []
        now = self._now()
        limit = config.ASSIGN_LOAD_LIMIT_PCT
        prio = self._svc("priority")
        out = []
        for s in self._svc("controllers").all_status(group):
            if (s.car_id in exclude or s.car_id not in allowed or s.car_id in self.exclusive
                    or s.car_id in self.standby or s.fault_code is not None or s.disconnected
                    or s.load_pct >= limit or self.refused_until.get(s.car_id, -1.0) > now):
                continue
            if call.priority is CallPriority.ACCESSIBLE and not prio.car_ok_for(call, s):
                continue
            out.append(s)
        return out

    def _best_eta(self, call: Call, exclude) -> Optional[float]:
        mode = self.current_mode(call.group)
        etas = [r[1] for s in self._candidates(call, exclude)
                if (r := self.engine.evaluate(call, s, mode)) is not None]
        return min(etas) if etas else None

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

    def _retry_queue(self) -> None:
        recs = [r for r in (self.calls[c] for c in sorted(self.open))
                if r.state == "pending" or (r.state == "assigned" and r.avoid is not None)]
        if not recs:
            return
        order = self._svc("priority").order([r.call for r in recs])
        for call in order:
            rec = self.calls[call.call_id]
            if rec.state == "pending":
                self._dispatch(rec, reason="requeue")
            elif rec.state == "assigned" and rec.avoid is not None:
                self._dispatch(rec, exclude={rec.avoid}, reason="reassign")

    # ---------- FR-13 wait ceiling ----------
    def _check_risk(self) -> None:
        for car_id, ids in list(self.waiting.items()):
            if not ids:
                continue
            status = self._status(car_id)
            if status is None:
                continue
            plan = replay(status, self.riders_of(car_id), self.waiting_of(car_id),
                          self.capacity_of(status), self.extra_load_of(status))
            for cid in list(ids):
                rec = self.calls[cid]
                eta = plan.pickup.get(cid)
                if eta is not None:
                    rec.eta = eta
                self._risk(rec, self.elapsed(cid) + (eta if eta is not None else 0.0))
        for rec in [self.calls[c] for c in sorted(self.open)]:
            if rec.state == "pending":
                self._risk(rec, self.elapsed(rec.call.call_id))

    def _risk(self, rec: Rec, predicted: float) -> None:
        ceiling = self._ceiling(rec.call.group)
        if rec.risk_sent or predicted < config.CEILING_WARN_FRACTION * ceiling:
            return
        rec.risk_sent = True
        self.risk_log.append((self._now(), rec.call.call_id, predicted, ceiling))
        self.ctx.bus.publish(WaitCeilingRisk(self._now(), rec.call.call_id, predicted, ceiling))
        if self._wake_standby(rec.call.group, "wait_ceiling"):   # FR-13: Standby capacity
            self._dispatch(rec, reason="wait_ceiling")

    def _log_breach(self, rec: Rec, wait: float, ceiling: float) -> None:
        group = rec.call.group
        cause = ("degraded" if (self.fire_hold or self.power_hold or self.ems_down)
                 else "capacity")
        self.breaches.append({"t": self._now(), "call_id": rec.call.call_id, "group": group,
                              "mode": self.current_mode(group).value, "wait_s": round(wait, 1),
                              "ceiling_s": ceiling, "cause": cause})
        last = self._last_breach_alert.get(group, -1e9)
        if self._now() - last >= 300.0:                  # one alert per group per 5 min
            self._last_breach_alert[group] = self._now()
            self.ctx.bus.publish(AlertRaised(
                self._now(), "high", "wait_ceiling", ["fm"],
                f"Group {group}: a call waited {wait:.0f} s, over the {ceiling} s ceiling "
                f"({cause}); see the breach log"))

    # ---------- FR-14 Eco step-up ----------
    def _check_step_up(self) -> None:
        now = self._now()
        while self.pickups and self.pickups[0][0] < now - config.ECO_STEP_UP["window_s"]:
            self.pickups.popleft()
        open_by_group = Counter(self.calls[c].call.group for c in self.open)
        for group in self.modes.groups:
            if self.current_mode(group) is not Mode.ECO:
                self.over_since[group] = None
                continue
            waiting = open_by_group.get(group, 0)
            if waiting > config.ECO_STEP_UP["pending_calls"]:
                since = self.over_since.get(group)
                if since is None:
                    self.over_since[group] = since = now
            else:
                self.over_since[group] = since = None
            waits = [w for _, g, w in self.pickups if g == group]
            avg = sum(waits) / len(waits) if waits else 0.0
            cause = None
            if since is not None and now - since >= config.ECO_STEP_UP["for_s"]:
                cause = f"{waiting} pending calls for {now - since:.0f} s"
            elif waits and avg > config.ECO_STEP_UP["avg_wait_s"]:
                cause = f"average wait {avg:.0f} s over the last 5 min"
            if cause:
                self.modes.step_up(group)
                self.ctx.bus.publish(AlertRaised(
                    now, "info", "eco_step_up", ["fm"],
                    f"Group {group}: Eco stepped up to Balanced ({cause})"))

    # ================================================================ mode policy
    def _on_mode_changed(self, group: GroupId, old: Mode, new: Mode, cause: str) -> None:
        self._next_policy = self._now()          # apply parking / standby at the next tick
        if new is not Mode.ECO:
            self._wake_all_standby(group)

    def _apply_policy(self) -> None:
        for group in self.modes.groups:
            if self.building is not None and group not in self.building.passenger_groups:
                continue
            mode = self.current_mode(group)
            statuses = [s for s in self._svc("controllers").all_status(group)
                        if s.fault_code is None and not s.disconnected
                        and s.car_id not in self.exclusive]
            if mode is Mode.ECO:
                self._eco_standby(group, statuses)
            self._park(group, mode, [s for s in statuses if s.car_id not in self.standby])

    def _idle(self, s: CarStatus) -> bool:
        return (not self.waiting.get(s.car_id) and not self.riders.get(s.car_id)
                and not s.pending_stops and s.doors is DoorState.CLOSED and s.passengers == 0)

    def _eco_standby(self, group: GroupId, statuses: list[CarStatus]) -> None:
        """Eco: at least one car per group in service, the rest in Standby (4.3 table)."""
        active = [s for s in statuses if s.car_id not in self.standby]
        if not active:
            return
        busy_first = sorted(active, key=lambda s: (self._idle(s), s.car_id))
        keep = {s.car_id for s in busy_first[:ECO_ACTIVE_CARS]}
        car_states = self._svc("car_states")
        controllers = self._svc("controllers")
        for s in active:
            if s.car_id in keep or not self._idle(s):
                continue
            if not car_states.request(s.car_id, CarState.STANDBY, BY):
                continue
            if controllers.set_standby(s.car_id, True):
                self.standby.add(s.car_id)
            else:
                car_states.release(s.car_id, CarState.STANDBY, BY)

    def _wake_standby(self, group: GroupId, why: str) -> bool:
        cars = sorted(c for c in self.standby if self._group_of(c) == group)
        for car_id in cars:
            if self._wake(car_id):
                return True
        return False

    def _wake_all_standby(self, group: GroupId) -> None:
        for car_id in sorted(c for c in self.standby if self._group_of(c) == group):
            self._wake(car_id)

    def _wake(self, car_id: CarId) -> bool:
        if not self._svc("controllers").set_standby(car_id, False):
            return False
        self._svc("car_states").release(car_id, CarState.STANDBY, BY)
        self.standby.discard(car_id)
        return True

    def _park(self, group: GroupId, mode: Mode, statuses: list[CarStatus]) -> None:
        """Where idle cars wait (4.3 table): Rush -> lobby / busy floors, Balanced -> near recent
        demand, Eco -> stay where the last trip ended (no empty runs)."""
        if not statuses:
            return
        floors = self._park_floors(group, mode, len(statuses))
        controllers = self._svc("controllers")
        free = sorted(statuses, key=lambda s: s.car_id)
        if floors is None:                         # Eco: park where the car is now
            for s in free:
                if self._idle(s) and self.parked.get(s.car_id) != s.floor:
                    if controllers.park(s.car_id, s.floor):
                        self.parked[s.car_id] = s.floor
            return
        targets = list(floors)
        plan: dict[CarId, int] = {}
        for s in sorted(free, key=lambda s: min(abs(s.position - f) for f in targets)):
            if not targets:
                break
            f = min(targets, key=lambda f: (abs(s.position - f), f))
            targets.remove(f)
            plan[s.car_id] = f
        for car_id, f in sorted(plan.items()):
            if self.parked.get(car_id) != f and controllers.park(car_id, f):
                self.parked[car_id] = f

    def _park_floors(self, group: GroupId, mode: Mode, n: int) -> Optional[list[int]]:
        lobby = self.building.lobby if self.building is not None else config.LOBBY
        zone = [f for f in self._floors(group) if f > lobby]
        if mode is Mode.ECO or not zone:
            return None
        recent = self._recent(group, 600.0)
        up = sum(1 for c in recent if c.origin == lobby)
        down = sum(1 for c in recent if c.destination == lobby)
        spread = lambda k: [zone[min(len(zone) - 1, int((i + 0.5) * len(zone) / k))]
                            for i in range(k)]
        if mode is Mode.RUSH:
            if not recent:
                hour = self.ctx.clock.now_dt().hour
                up, down = (1, 0) if hour < 10 else ((1, 1) if hour < 15 else (0, 1))
            total = max(1, up + down)
            n_lobby = max(1, round(n * up / total)) if up else 0
            return [lobby] * n_lobby + spread(n - n_lobby) if n - n_lobby else [lobby] * n
        # Balanced: some cars at the lobby, the others at the busiest recent origin floors
        k = min(n, BALANCED_LOBBY_CARS)
        busiest = [f for f, _ in Counter(c.origin for c in recent if c.origin != lobby)
                   .most_common(n - k)]
        rest = busiest + [f for f in spread(n - k) if f not in busiest]
        return [lobby] * k + rest[:n - k]

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
