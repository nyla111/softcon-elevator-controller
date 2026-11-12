"""Deterministic synthetic tenant population (the people who hold cards).

Built from the seed, using ctx.rng only: the same seed gives the same tenants, people and flags.
The mock ACS (devices.py) serves exactly this population (ctx.services["population"]).

Layout (SRS 2.4: about 2,500 workers in 20-30 tenants; low-rise L2-L15, high-rise L16-L30):
* 25 tenants take the floors 2..30 in order. 4 of them (chosen with rng) occupy 2 contiguous
  floors, the other 21 occupy 1 floor. A 2-floor tenant never spans L15/L16 (the layout is
  redrawn until none does). Tenant ids are "T01".."T25".
* 2,500 employees are split evenly over the 29 floors (86 per floor; the 6 left over go to
  6 random floors). Card ids look like "card-L18-007": L18 is the employee's HOME floor.
* default floor = home floor; allowed floors = all floors of the tenant + the public floors
  (the lobby).
* Exactly round(share x people) employees are accessible (5%) / VIP (1%), picked with rng.sample.
* Employees may make more than one trip per run (a passenger is drawn with replacement).
* Visitors are not in this population: they come with a QR pass from the mock ACS.
"""
from __future__ import annotations

from dataclasses import dataclass

from ems.contracts import config
from ems.contracts.types import CredentialId, Floor

from .building import BuildingSpec

PUBLIC_FLOORS = frozenset({config.LOBBY})
# Constants of the REFERENCE building (kept for the capacity check and older tests). Runs read
# the layout from ctx.services["building"] / Population.building instead.
_REF = BuildingSpec.reference()
PASSENGER_GROUPS = tuple(_REF.passenger_groups)
TENANT_FLOORS = _REF.tenant_floors
ALL_TENANT_FLOORS = _REF.all_tenant_floors
LOW_TOP, HIGH_BOTTOM = max(TENANT_FLOORS["LOW"]), min(TENANT_FLOORS["HIGH"])   # 15, 16


@dataclass(frozen=True)
class Employee:
    credential_id: CredentialId
    home_floor: Floor
    default_floor: Floor
    tenant_id: str
    allowed_floors: frozenset[Floor]
    accessible: bool
    vip: bool


def _layout_tenants(rng, building: BuildingSpec, n_tenants: int,
                    n_double: int) -> list[list[Floor]]:
    """Floors of each tenant, in floor order. Redraw until no tenant spans two groups.
    More tenants than floors: tenant i takes floor i mod n (floors are shared, no rng)."""
    all_floors = building.all_tenant_floors
    if n_tenants > len(all_floors) and n_double == 0:
        return [[all_floors[i % len(all_floors)]] for i in range(n_tenants)]
    if n_tenants + n_double != len(all_floors):
        raise ValueError("tenants + 2-floor tenants must equal the number of tenant floors")
    group_of = building.group_of_floor
    while True:
        doubles = set(rng.sample(range(n_tenants), n_double))
        floors = iter(all_floors)
        tenants = [[next(floors) for _ in range(2 if i in doubles else 1)]
                   for i in range(n_tenants)]
        if not any(len({group_of[f] for f in t}) > 1 for t in tenants):
            return tenants


class Population:
    def __init__(self, employees: list[Employee], tenants: dict[str, list[Floor]],
                 params: dict, building: BuildingSpec | None = None) -> None:
        self.building = building or _REF
        self.employees = employees
        self.tenants = tenants                       # tenant id -> its floors
        self.params = params                         # recorded in the run manifest
        self.tenant_of_floor = {}                    # first tenant of a (shared) floor
        for t, fl in tenants.items():
            for f in fl:
                self.tenant_of_floor.setdefault(f, t)
        self._by_floor: dict[Floor, list[Employee]] = {}
        self._by_card = {e.credential_id: e for e in employees}
        for e in employees:
            self._by_floor.setdefault(e.home_floor, []).append(e)

    @classmethod
    def build(cls, rng, total_employees: int | None = None, n_tenants: int | None = None,
              n_double: int | None = None, accessible_share: float | None = None,
              vip_share: float | None = None,
              building: BuildingSpec | None = None) -> "Population":
        """Counts default to the building's (reference: 2,500 / 25 / 4 / 5% / 1%)."""
        building = building or _REF
        total_employees = building.total_employees if total_employees is None else total_employees
        n_tenants = building.n_tenants if n_tenants is None else n_tenants
        n_double = building.n_double if n_double is None else n_double
        accessible_share = building.accessible_share if accessible_share is None else accessible_share
        vip_share = building.vip_share if vip_share is None else vip_share
        all_floors = building.all_tenant_floors
        layout = _layout_tenants(rng, building, n_tenants, n_double)
        tenants = {f"T{i + 1:02d}": floors for i, floors in enumerate(layout)}
        on_floor: dict[Floor, list[str]] = {}               # tenants present on each floor
        for t, fl in tenants.items():
            for f in fl:
                on_floor.setdefault(f, []).append(t)

        per_floor, extra = divmod(total_employees, len(all_floors))
        bonus = set(rng.sample(all_floors, extra))
        homes = [(floor, n) for floor in all_floors
                 for n in range(1, per_floor + (floor in bonus) + 1)]
        total = len(homes)
        accessible = set(rng.sample(range(total), round(accessible_share * total)))
        vip = set(rng.sample(range(total), round(vip_share * total)))
        people = []
        for i, (floor, n) in enumerate(homes):
            here = on_floor[floor]
            tenant = here[(n - 1) % len(here)]
            people.append(Employee(f"card-L{floor}-{n:03d}", floor, floor, tenant,
                                   frozenset(tenants[tenant]) | PUBLIC_FLOORS,
                                   i in accessible, i in vip))
        params = {"total_employees": total, "n_tenants": n_tenants, "n_double": n_double,
                  "accessible_share": accessible_share, "vip_share": vip_share,
                  "building": building.name}
        return cls(people, tenants, params, building)

    def on_floor(self, floor: Floor) -> list[Employee]:
        """Employees whose home floor is this floor."""
        return self._by_floor[floor]

    def lookup(self, credential_id: CredentialId) -> Employee | None:
        return self._by_card.get(credential_id)
