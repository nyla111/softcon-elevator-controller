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

PUBLIC_FLOORS = frozenset({config.LOBBY})
PASSENGER_GROUPS = ("LOW", "HIGH")
# Tenant floors of each passenger group (everything except the lobby).
TENANT_FLOORS = {g: [f for f in config.GROUPS[g]["floors"] if f != config.LOBBY]
                 for g in PASSENGER_GROUPS}
ALL_TENANT_FLOORS = [f for g in PASSENGER_GROUPS for f in TENANT_FLOORS[g]]
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


def _layout_tenants(rng, n_tenants: int, n_double: int) -> list[list[Floor]]:
    """Floors of each tenant, in floor order. Redraw until no tenant spans L15/L16."""
    if n_tenants + n_double != len(ALL_TENANT_FLOORS):
        raise ValueError("tenants + 2-floor tenants must equal the number of tenant floors")
    while True:
        doubles = set(rng.sample(range(n_tenants), n_double))
        floors = iter(ALL_TENANT_FLOORS)
        tenants = [[next(floors) for _ in range(2 if i in doubles else 1)]
                   for i in range(n_tenants)]
        if not any(LOW_TOP in t and HIGH_BOTTOM in t for t in tenants):
            return tenants


class Population:
    def __init__(self, employees: list[Employee], tenants: dict[str, list[Floor]],
                 params: dict) -> None:
        self.employees = employees
        self.tenants = tenants                       # tenant id -> its floors
        self.params = params                         # recorded in the run manifest
        self.tenant_of_floor = {f: t for t, fl in tenants.items() for f in fl}
        self._by_floor: dict[Floor, list[Employee]] = {}
        self._by_card = {e.credential_id: e for e in employees}
        for e in employees:
            self._by_floor.setdefault(e.home_floor, []).append(e)

    @classmethod
    def build(cls, rng, total_employees: int = 2500, n_tenants: int = 25, n_double: int = 4,
              accessible_share: float = 0.05, vip_share: float = 0.01) -> "Population":
        layout = _layout_tenants(rng, n_tenants, n_double)
        tenants = {f"T{i + 1:02d}": floors for i, floors in enumerate(layout)}
        tenant_of = {f: t for t, fl in tenants.items() for f in fl}

        per_floor, extra = divmod(total_employees, len(ALL_TENANT_FLOORS))
        bonus = set(rng.sample(ALL_TENANT_FLOORS, extra))
        homes = [(floor, n) for floor in ALL_TENANT_FLOORS
                 for n in range(1, per_floor + (floor in bonus) + 1)]
        total = len(homes)
        accessible = set(rng.sample(range(total), round(accessible_share * total)))
        vip = set(rng.sample(range(total), round(vip_share * total)))
        people = [Employee(f"card-L{floor}-{n:03d}", floor, floor, tenant_of[floor],
                           frozenset(tenants[tenant_of[floor]]) | PUBLIC_FLOORS,
                           i in accessible, i in vip)
                  for i, (floor, n) in enumerate(homes)]
        params = {"total_employees": total, "n_tenants": n_tenants, "n_double": n_double,
                  "accessible_share": accessible_share, "vip_share": vip_share}
        return cls(people, tenants, params)

    def on_floor(self, floor: Floor) -> list[Employee]:
        """Employees whose home floor is this floor."""
        return self._by_floor[floor]

    def lookup(self, credential_id: CredentialId) -> Employee | None:
        return self._by_card.get(credential_id)
