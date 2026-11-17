"""Assigning and re-assigning calls (SRS 4.1, FR-01..05, FR-04, FR-08, FR-100, FR-101).

Mixin of Dispatcher (service.py): uses its book (calls, waiting, riders) and services.
Every assignment goes through Part 1's FR-105 guard; a refused register_stop backs the car off
for 5 s and the next car is tried. Re-assignment keeps the call's wait clock (CallReassigned).
"""
from __future__ import annotations

from typing import Optional

from ems.contracts import config
from ems.contracts.events import CallAssigned, CallReassigned, OverloadDetected, PanelMessage
from ems.contracts.types import Call, CallId, CallPriority, CarId, CarStatus, GroupId, Mode

from .model import replay
from .records import Rec

REFUSED_BACKOFF_S = 5.0          # a car that refused a stop is not asked again for 5 s
LOBBY_HOLD_MAX_S = 10.0          # Rush up-peak: longest door hold asked at the lobby
LOBBY_HOLD_MIN_S = 6.0           # shorter holds are not worth asking


class AssignMixin:
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
