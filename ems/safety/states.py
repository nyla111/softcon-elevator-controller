"""BR-06 state arbitration. Holds belong to (state, owner), not to a single slot."""
from __future__ import annotations

from ems.contracts.events import CarStateChanged
from ems.contracts.types import CarId, CarState, GroupId


class StateTable:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        building = ctx.services.get("building")
        # Safety-only builds can discover the stub adapter after build() fills it in.
        self.groups = ({g.name: tuple(g.cars) for g in building.groups}
                       if building is not None else None)
        self.holds: dict[CarId, set[tuple[CarState, str]]] = {}

    def cars(self, group: GroupId | None = None) -> list[CarId]:
        if self.groups is not None:
            if group is not None:
                return list(self.groups[group])
            return [car for cars in self.groups.values() for car in cars]
        return [s.car_id for s in self.ctx.get("controllers").all_status(group)]

    def check_car(self, car_id: CarId) -> None:
        if car_id not in self.cars():
            raise KeyError(car_id)

    def state(self, car_id: CarId) -> CarState:
        self.check_car(car_id)
        return min((s for s, _ in self.holds.get(car_id, ())),
                   default=CarState.GROUP_SERVICE)

    def set(self, car_id: CarId, state: CarState, owner: str, active: bool) -> bool:
        old = self.state(car_id)
        holds = self.holds.setdefault(car_id, set())
        key = (state, owner)
        changed = (key not in holds) if active else (key in holds)
        if active:
            holds.add(key)
        else:
            holds.discard(key)
        new = self.state(car_id)
        if new != old:
            self.ctx.bus.publish(CarStateChanged(self.ctx.clock.now(), car_id, old, new, owner))
        return changed

    def isolated(self, car_id: CarId) -> bool:
        return any(s in {CarState.FAULTED, CarState.MAINTENANCE,
                         CarState.INDEPENDENT_SERVICE}
                   for s, _ in self.holds.get(car_id, ()))
