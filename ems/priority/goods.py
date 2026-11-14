"""Goods moves and the service car: FR-81 request + FM approval, FR-82 only the move's
credentials, FR-36 independent-service periods (not in Rush), FR-80 house-rule limits on panels.

Life of a move:  requested -> approved -> active -> completed
                 (or rejected / cancelled / start_refused / cancelled_emergency)

* request (tenant admin card, tenant from Access): window [start, end) in simulated seconds,
  credential ids of the movers, car (default: the first car of the building's service group).
  Refused if the window is empty or in the past, overlaps another approved/active move on the
  same car, or (passenger car only, FR-36) overlaps a Rush block of the schedule.
* approve (FM): publishes GoodsMoveScheduled (Part 3 uses credential_ids to restrict the car's
  panels, FR-82) and books start / end on ctx.clock. Each booking carries an epoch, so a callback
  that fires after a cancellation does nothing.
* start: Rush (Part 2's current mode) and the FR-36 ceiling check (Part 2's
  would_breach_ceiling_without) for passenger cars, then INDEPENDENT_SERVICE from Part 4 and
  set_independent_service(True). Any refusal: start_refused + FM alert, nothing held.
* end / cancel: set_independent_service(False) and release; emergencies (fire, mains loss)
  cancel an active move without any controller command (retried after recovery).

Assumption to confirm (docs/priority/REQUIREMENTS.md): the "not in Rush" rule of FR-36 applies to
passenger cars taken out of group service. Moves on the service car are allowed at any time:
Appendix B SIM-08 has a goods move still active at the 17:00 Rush transition.

House rules (FR-80): the limit VALUES are configuration owned by the FM (Part 6 keeps
`house_rule_limits`); Part 5 only formats and shows them. No value is defined here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from ems.contracts import config
from ems.contracts.events import GoodsMoveScheduled, PanelMessage
from ems.contracts.types import CarId, CarState, CredentialId, Mode, Role

from .service import Outcome, PriorityService

LABELS = {  # known keys of Part 6's house_rule_limits -> (English, Vietnamese, unit)
    "item_kg": ("Max item weight", "Khối lượng tối đa mỗi món", "kg"),
    "item_length_m": ("Max item length", "Chiều dài tối đa", "m"),
    "cart_width_m": ("Max cart width", "Chiều rộng xe đẩy tối đa", "m"),
}


@dataclass
class GoodsMove:
    move_id: str
    tenant_id: Optional[str]
    car_id: CarId
    start: float
    end: float
    credential_ids: tuple[CredentialId, ...]
    status: str = "requested"
    reason: Optional[str] = None
    epoch: int = 0


def house_rules_text(limits: dict) -> tuple[str, str]:
    en, vi = [], []
    for key, value in limits.items():
        name_en, name_vi, unit = LABELS.get(key, (key, key, ""))
        en.append(f"{name_en}: {value}{(' ' + unit) if unit else ''}")
        vi.append(f"{name_vi}: {value}{(' ' + unit) if unit else ''}")
    return "House rules - " + "; ".join(en), "Nội quy - " + "; ".join(vi)


def _rush_blocks(day: datetime) -> list[tuple[datetime, datetime]]:
    schedule = config.WEEKDAY_SCHEDULE if day.weekday() < 5 else config.WEEKEND_SCHEDULE
    out = []
    for i, (hhmm, mode) in enumerate(schedule):
        if mode is not Mode.RUSH:
            continue
        end = schedule[i + 1][0] if i + 1 < len(schedule) else "24:00"
        to = lambda s: day + timedelta(hours=int(s[:2]), minutes=int(s[3:]))  # noqa: E731
        out.append((to(hhmm), to(end)))
    return out


class GoodsMoves:
    def __init__(self, service: PriorityService) -> None:
        self.s = service
        self.ctx = service.ctx
        self.moves: dict[str, GoodsMove] = {}
        self.house_rules: dict = {}

    # ---------------------------------------------------------------- helpers
    def _dt(self, t: float) -> datetime:
        return self.ctx.clock.now_dt() + timedelta(seconds=t - self.s.now())

    def overlaps_rush(self, start: float, end: float) -> bool:
        """FR-36: does [start, end) touch a Rush block of the default schedule?
        (Manual Rush overrides are Part 2's; they are checked again at start time.)"""
        a, b = self._dt(start), self._dt(end)
        day = a.replace(hour=0, minute=0, second=0, microsecond=0)
        while day < b:
            if any(r0 < b and a < r1 for r0, r1 in _rush_blocks(day)):
                return True
            day += timedelta(days=1)
        return False

    def is_passenger_car(self, car_id: CarId) -> bool:
        g = self.s.group_of_car(car_id)
        return g is not None and g not in self.s.building().service_groups

    def default_car(self) -> Optional[CarId]:
        for g in self.s.building().groups:
            if not g.passenger and g.cars:
                return g.cars[0]
        return None

    def _clash(self, move: GoodsMove) -> bool:
        return any(m.move_id != move.move_id and m.car_id == move.car_id
                   and m.status in ("approved", "active")
                   and m.start < move.end and move.start < m.end
                   for m in self.moves.values())

    # ---------------------------------------------------------------- FR-81 request
    def request(self, credential_id: CredentialId, start: float, end: float,
                credential_ids: list[CredentialId], car_id: Optional[CarId] = None) -> Outcome:
        actor, err = self.s.authorize(credential_id, "goods.request", Role.TENANT_ADMIN)
        if err:
            return err
        car = car_id or self.default_car()
        move = GoodsMove(self.s.next_id("gm"), actor.tenant_id, car or "", start, end,
                         tuple(dict.fromkeys(credential_ids)))
        if car is None or self.s.group_of_car(car) is None:
            out = Outcome(False, "no_service_car")
        elif not end > start or start < self.s.now():
            out = Outcome(False, "invalid_window")
        elif not move.credential_ids:
            out = Outcome(False, "no_credentials")
        elif self.is_passenger_car(car) and self.overlaps_rush(start, end):
            out = Outcome(False, "rush")
        elif self._clash(move):
            out = Outcome(False, "overlap")
        else:
            self.moves[move.move_id] = move
            self.s.alert("info", "goods_move_request", ["fm"],
                         f"Goods move {move.move_id} on car {car} requested, waiting for approval",
                         car)
            out = Outcome(True, "requested", move.move_id, car)
        self.s.record("goods.request", actor, out.ok, out.reason, out.ref, car)
        return out

    def approve(self, credential_id: CredentialId, move_id: str) -> Outcome:
        actor, err = self.s.authorize(credential_id, "goods.approve", Role.FM)
        if err:
            return err
        move = self.moves.get(move_id)
        now = self.s.now()
        if move is None:
            out = Outcome(False, "unknown_move")
        elif move.status != "requested":
            out = Outcome(False, move.status, move_id)
        elif move.end <= now:
            move.status = "expired"
            out = Outcome(False, "expired", move_id)
        elif self._clash(move):
            out = Outcome(False, "overlap", move_id)
        else:
            move.status = "approved"
            epoch = move.epoch
            self.ctx.bus.publish(GoodsMoveScheduled(now, move.move_id, move.car_id, move.start,
                                                    move.end, list(move.credential_ids)))
            self.ctx.clock.call_at(max(move.start, now), lambda: self._start(move_id, epoch))
            self.ctx.clock.call_at(move.end, lambda: self._end(move_id, epoch))
            out = Outcome(True, "approved", move_id, move.car_id)
        self.s.record("goods.approve", actor, out.ok, out.reason, move_id)
        return out

    def reject(self, credential_id: CredentialId, move_id: str) -> Outcome:
        actor, err = self.s.authorize(credential_id, "goods.reject", Role.FM)
        if err:
            return err
        move = self.moves.get(move_id)
        if move is None or move.status != "requested":
            out = Outcome(False, "unknown_move" if move is None else move.status, move_id)
        else:
            move.status = "rejected"
            out = Outcome(True, "rejected", move_id)
        self.s.record("goods.reject", actor, out.ok, out.reason, move_id)
        return out

    def cancel(self, credential_id: CredentialId, move_id: str) -> Outcome:
        """FM, or a tenant admin of the tenant that asked for the move."""
        actor = self.s.authorizer.actor(credential_id)
        move = self.moves.get(move_id)
        allowed = actor.has(Role.FM) or (actor.has(Role.TENANT_ADMIN) and move is not None
                                         and actor.tenant_id == move.tenant_id)
        if not allowed:
            self.s.record("goods.cancel", actor, False, "not_authorized", move_id)
            return Outcome(False, "not_authorized")
        if move is None or move.status not in ("requested", "approved", "active"):
            out = Outcome(False, "unknown_move" if move is None else move.status, move_id)
        else:
            self._finish(move, "cancelled")
            out = Outcome(True, "cancelled", move_id, move.car_id)
        self.s.record("goods.cancel", actor, out.ok, out.reason, move_id)
        return out

    # ---------------------------------------------------------------- start / end
    def _refuse_start(self, move: GoodsMove, reason: str) -> None:
        move.status, move.reason = "start_refused", reason
        move.epoch += 1
        self.s.alert("high", "goods_move_refused", ["fm"],
                     f"Goods move {move.move_id} could not start on car {move.car_id}: {reason}",
                     move.car_id)

    def _start(self, move_id: str, epoch: int) -> None:
        move = self.moves[move_id]
        if move.epoch != epoch or move.status != "approved":
            return                                        # cancelled / rescheduled meanwhile
        why = self.s.blocked_reason()
        group = self.s.group_of_car(move.car_id)
        dispatcher = self.ctx.get("dispatcher")
        if why is None and self.is_passenger_car(move.car_id):
            if dispatcher.current_mode(group) is Mode.RUSH:
                why = "rush"
            elif dispatcher.would_breach_ceiling_without(move.car_id):
                why = "ceiling"
        if why is None and not self.s.healthy(self.s.status(move.car_id)):
            why = "car_unavailable"
        if why is None:
            why = self.s.take(move.car_id, CarState.INDEPENDENT_SERVICE, "goods", move_id,
                              ("set_independent_service", True))
        if why is not None:
            self._refuse_start(move, why)
            return
        move.status = "active"
        if self.is_passenger_car(move.car_id):
            dispatcher.reassign_calls_of(move.car_id, "goods_move")      # FR-04 (Part 2)
        if self.house_rules:
            en, vi = house_rules_text(self.house_rules)
            self.ctx.bus.publish(PanelMessage(self.s.now(), f"car:{move.car_id}", en, vi, False))

    def _end(self, move_id: str, epoch: int) -> None:
        move = self.moves[move_id]
        if move.epoch == epoch and move.status == "active":
            self._finish(move, "completed")
        elif move.epoch == epoch and move.status == "approved":
            move.status, move.epoch = "expired", move.epoch + 1

    def _finish(self, move: GoodsMove, status: str) -> None:
        move.epoch += 1                                   # late start/end callbacks: no-ops
        was_active = move.status == "active"
        move.status = status
        if was_active:
            self.s.give_back(move.car_id, ("set_independent_service", False))

    def emergency(self, why: str) -> None:
        for move in self.moves.values():
            if move.status == "active":
                self._finish(move, "cancelled_emergency")
                move.reason = why

    # ---------------------------------------------------------------- FR-82 / FR-80
    def active_move(self, car_id: CarId) -> Optional[GoodsMove]:
        return next((m for m in self.moves.values()
                     if m.car_id == car_id and m.status == "active"), None)

    def service_car_accepts(self, credential_id: Optional[CredentialId], car_id: CarId) -> bool:
        """FR-82 for Part 3: during an active move the car answers only the move's credentials.
        Outside a move Part 5 adds no restriction (Part 3's normal rules apply)."""
        move = self.active_move(car_id)
        return move is None or credential_id in move.credential_ids

    def show_house_rules(self, credential_id: CredentialId, limits: dict,
                         targets: Optional[list[str]] = None) -> Outcome:
        """FR-80 (FM): show the configured item limits on panels / car displays. Default
        targets: the displays of every service-group car."""
        actor, err = self.s.authorize(credential_id, "house_rules.show", Role.FM)
        if err:
            return err
        if not limits:
            self.s.record("house_rules.show", actor, False, "no_limits")
            return Outcome(False, "no_limits")
        self.house_rules = dict(limits)
        targets = targets or [f"car:{c}" for g in self.s.building().groups
                              if not g.passenger for c in g.cars]
        en, vi = house_rules_text(limits)
        for target in targets:
            self.ctx.bus.publish(PanelMessage(self.s.now(), target, en, vi, False))
        self.s.record("house_rules.show", actor, True, "ok")
        return Outcome(True, "shown", ref=",".join(targets))

    def snapshot(self) -> dict:
        return {m.move_id: {"car_id": m.car_id, "start": m.start, "end": m.end,
                            "status": m.status, "reason": m.reason,
                            "credentials": len(m.credential_ids)}
                for m in self.moves.values()}
