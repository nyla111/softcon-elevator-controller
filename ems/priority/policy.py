"""Pure policy functions of Part 5 (no state, no events). Part 2 may call them directly.

* FR-76 / BR-10: medical > accessible > VIP > ordinary   -> rank(), order()
* FR-08: accessible calls need free capacity and >= 10 s doors -> accessible_car_ok(), door_dwell_s()
* FR-71: VIP car = an empty car that can be there within 30 s, else the fastest -> vip_candidates()

ETA model (an estimate, the controllers do not report one): straight travel at
config.SIM["s_per_floor"], plus the door close time if the doors are not shut, plus the door
open time at the target floor. Express trips do not stop on the way, so pending stops add
nothing. This is the same physics the simulator uses (Appendix B).
"""
from __future__ import annotations

from typing import Iterable

from ems.contracts import config
from ems.contracts.types import Call, CallPriority, CarStatus, DoorState, Floor


def rank(call: Call) -> int:
    """Lower = served first (BR-10). CallPriority already encodes the order."""
    return int(call.priority)


def order(calls: Iterable[Call]) -> list[Call]:
    """Calls in service order: priority first, then the oldest wait clock (created_at)."""
    return sorted(calls, key=lambda c: (rank(c), c.created_at, c.call_id))


def door_dwell_s(call: Call) -> float:
    """Minimum door time for this call (FR-08: 10 s for accessible passengers)."""
    if call.priority is CallPriority.ACCESSIBLE:
        return config.SIM["accessible_dwell_s"]
    return config.SIM["min_dwell_s"]


def free_places(status: CarStatus, rated_persons: int) -> int:
    return rated_persons - status.passengers


def accessible_car_ok(call: Call, status: CarStatus, rated_persons: int) -> bool:
    """FR-08 'free capacity': the car has room for the whole party right now.
    Non-accessible calls are not restricted by this rule."""
    if call.priority is not CallPriority.ACCESSIBLE:
        return True
    return free_places(status, rated_persons) >= call.party_size


def eta_s(status: CarStatus, floor: Floor) -> float:
    s = config.SIM
    eta = abs(status.position - floor) * s["s_per_floor"] + s["door_open_s"]
    if status.doors is not DoorState.CLOSED:
        eta += s["door_close_s"]
    return eta


def vip_candidates(statuses: Iterable[CarStatus], origin: Floor) -> list[CarStatus]:
    """FR-71 preference order. First: empty cars with ETA <= VIP_EMPTY_CAR_ETA_S (fastest
    first); then every other car, fastest first. The caller tries them in this order and
    skips the ones that fail the FR-72 ceiling check or are refused."""
    cars = sorted(statuses, key=lambda s: (eta_s(s, origin), s.car_id))
    empty_fast = [s for s in cars
                  if s.passengers == 0 and eta_s(s, origin) <= config.VIP_EMPTY_CAR_ETA_S]
    return empty_fast + [s for s in cars if s not in empty_fast]


def medical_candidates(statuses: Iterable[CarStatus], origin: Floor) -> list[CarStatus]:
    """FR-127: fastest healthy car, cars without riders first (riders are carried along and
    delivered afterwards, so an empty car is preferred when one exists)."""
    return sorted(statuses, key=lambda s: (s.passengers > 0, eta_s(s, origin), s.car_id))
