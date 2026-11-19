"""Mode policy (4.3 table): where idle cars park, and Eco Standby cars.

Mixin of Dispatcher (service.py). Rush: lobby (up-peak) / spread over the zone (down-peak);
Balanced: lobby + busiest recent floors; Eco: stay where the last trip ended, ECO_ACTIVE_CARS
per group in service, the rest in Standby.
"""
from __future__ import annotations

from collections import Counter
from typing import Optional

from ems.contracts import config
from ems.contracts.types import CarId, CarState, CarStatus, DoorState, GroupId, Mode

from .records import BY

BALANCED_LOBBY_CARS = 1          # Balanced: idle cars parked at the lobby (rest near demand)
ECO_ACTIVE_CARS = 2              # Eco: cars per group kept in service (4.3: "at least 1")


class PolicyMixin:
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
        busy_first = sorted(active, key=lambda s: (
            self._idle(s), self.wear_distance(s.car_id), s.car_id))
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
        cars = sorted((c for c in self.standby if self._group_of(c) == group),
                      key=lambda c: (self.wear_distance(c), c))
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
