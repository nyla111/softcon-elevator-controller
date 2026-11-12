"""Deterministic synthetic tenant population (the people who hold cards).

Built from the seed, using ctx.rng only: the same seed gives the same people, ids and flags.
Session B's mock ACS serves exactly this population (ctx.services["population"]).

* Every tenant floor L2..L15 and L16..L30 has `employees_per_floor` employees.
* Card ids look like "card-L18-007". An employee's default floor is their own floor.
* allowed_floors = their own tenant floor + the public floors (the lobby).
* Exactly round(share x people) employees are accessible / VIP (picked with rng.sample).
* Visitors do not exist yet: a visitor would have credential_id=None.
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


@dataclass(frozen=True)
class Employee:
    credential_id: CredentialId
    tenant_floor: Floor
    default_floor: Floor
    allowed_floors: frozenset[Floor]
    accessible: bool
    vip: bool


class Population:
    def __init__(self, employees: list[Employee], params: dict) -> None:
        self.employees = employees
        self.params = params                       # recorded in the run manifest
        self._by_floor: dict[Floor, list[Employee]] = {}
        self._by_card = {e.credential_id: e for e in employees}
        for e in employees:
            self._by_floor.setdefault(e.tenant_floor, []).append(e)

    @classmethod
    def build(cls, rng, employees_per_floor: int = 30, accessible_share: float = 0.05,
              vip_share: float = 0.01) -> "Population":
        employees: list[Employee] = []
        for floor in ALL_TENANT_FLOORS:
            for n in range(1, employees_per_floor + 1):
                employees.append((floor, f"card-L{floor}-{n:03d}"))
        total = len(employees)
        accessible = set(rng.sample(range(total), round(accessible_share * total)))
        vip = set(rng.sample(range(total), round(vip_share * total)))
        people = [Employee(card, floor, floor, frozenset({floor}) | PUBLIC_FLOORS,
                           i in accessible, i in vip)
                  for i, (floor, card) in enumerate(employees)]
        params = {"employees_per_floor": employees_per_floor,
                  "accessible_share": accessible_share, "vip_share": vip_share,
                  "total": total}
        return cls(people, params)

    def on_floor(self, floor: Floor) -> list[Employee]:
        return self._by_floor[floor]

    def lookup(self, credential_id: CredentialId) -> Employee | None:
        return self._by_card.get(credential_id)
