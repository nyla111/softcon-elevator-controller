"""Building layout of one run: groups -> cars and floors served (ctx.services["building"]).

The simulator, population, traffic, nearest-car fallback and web server read the layout from here,
never from config.GROUPS (SIM-10 uses another building). `BuildingSpec.reference()` is exactly
the reference building of config.GROUPS / config.RATED, so every existing run is unchanged.

Design-limit building (SIM-10, DECISIONS D-03; the SRS limits: 64 cars, 8 groups, 128 stops,
200 tenants). Choices made here:
* 8 passenger groups "G1".."G8" of 8 cars each: cars "G1-1" .. "G8-8" (64 cars), rated 21 persons.
* 128 stops = B2, B1, L1 .. L126. L2..L126 (125 tenant floors) are split into 8 zones: the first
  five zones have 16 floors, the last three 15. Every group serves L1 plus its zone; G1 also
  serves B2 and B1 (parking). No group is a service group.
* 200 tenants on the 125 tenant floors (several tenants share a floor, tenant i sits on floor
  i mod 125); no two-floor tenants.
* Population scaled to 20,000 employees (160 per floor, 5% accessible, 1% VIP as in the reference
  building). This is the "about 20,000 people" scale of a 126-floor tower.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property

from ems.contracts import config
from ems.contracts.types import CarId, CarState, Floor, GroupId


@dataclass(frozen=True)
class GroupSpec:
    name: GroupId
    cars: tuple[CarId, ...]
    floors: tuple[Floor, ...]
    passenger: bool = True          # False: service group (fire service car, goods)
    rated_persons: int = 21


@dataclass(frozen=True)
class BuildingSpec:
    name: str
    groups: tuple[GroupSpec, ...]
    lobby: Floor = config.LOBBY
    n_tenants: int = 25
    n_double: int = 4               # tenants on two contiguous floors (reference layout only)
    total_employees: int = 2500
    accessible_share: float = 0.05
    vip_share: float = 0.01

    # ---------- derived ----------
    def group(self, name: GroupId) -> GroupSpec:
        return next(g for g in self.groups if g.name == name)

    @property
    def group_names(self) -> list[GroupId]:
        return [g.name for g in self.groups]

    @property
    def passenger_groups(self) -> list[GroupId]:
        return [g.name for g in self.groups if g.passenger]

    @property
    def service_groups(self) -> frozenset[GroupId]:
        return frozenset(g.name for g in self.groups if not g.passenger)

    @cached_property
    def tenant_floors(self) -> dict[GroupId, list[Floor]]:
        """Floors people work on, per passenger group (above the lobby)."""
        return {g.name: [f for f in g.floors if f > self.lobby]
                for g in self.groups if g.passenger}

    @cached_property
    def all_tenant_floors(self) -> list[Floor]:
        return [f for floors in self.tenant_floors.values() for f in floors]

    @cached_property
    def group_of_floor(self) -> dict[Floor, GroupId]:
        return {f: g for g, floors in self.tenant_floors.items() for f in floors}

    @property
    def n_cars(self) -> int:
        return sum(len(g.cars) for g in self.groups)

    @property
    def n_stops(self) -> int:
        return len({f for g in self.groups for f in g.floors})

    def as_dict(self) -> dict:
        return {"name": self.name, "groups": {g.name: {"cars": list(g.cars), "floors":
                [min(g.floors), max(g.floors)], "n_floors": len(g.floors)} for g in self.groups},
                "n_cars": self.n_cars, "n_stops": self.n_stops, "n_tenants": self.n_tenants,
                "total_employees": self.total_employees}

    # ---------- the two buildings ----------
    @classmethod
    def reference(cls) -> "BuildingSpec":
        groups = tuple(
            GroupSpec(name, tuple(info["cars"]), tuple(info["floors"]), name != "SERVICE",
                      config.RATED["S" if name == "SERVICE" else "passenger"]["persons"])
            for name, info in config.GROUPS.items())
        return cls("reference", groups)

    @classmethod
    def design_limit(cls) -> "BuildingSpec":
        tenant = list(range(2, 127))                                   # L2..L126
        sizes = [16] * 5 + [15] * 3
        groups, start = [], 0
        for i, size in enumerate(sizes, start=1):
            floors = [1] + tenant[start:start + size]
            start += size
            if i == 1:
                floors = [-2, -1] + floors
            groups.append(GroupSpec(f"G{i}", tuple(f"G{i}-{n}" for n in range(1, 9)),
                                    tuple(floors)))
        return cls("design_limit", tuple(groups), n_tenants=200, n_double=0,
                   total_employees=20000)


class SpecCarStates:
    """Part 1 stand-in for ctx.services["car_states"] when the building is not the reference
    one (the contracts stub only knows config.GROUPS). Same API as StubCarStates. Part 4's
    real service registers over it."""

    def __init__(self, building: BuildingSpec) -> None:
        self.building = building

    def state(self, car_id: CarId) -> CarState: return CarState.GROUP_SERVICE
    def request(self, car_id, new, by) -> bool: return True
    def release(self, car_id, from_state, by) -> bool: return True
    def dispatchable(self, group: GroupId) -> list[CarId]: return list(self.building.group(group).cars)
    def safety_active(self) -> bool: return False


BUILDINGS = {"reference": BuildingSpec.reference, "design_limit": BuildingSpec.design_limit}
