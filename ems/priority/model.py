"""Plain data shared by the Part 5 modules."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ems.contracts.types import CallId, CarId, CarState, Floor, GroupId

HOUR_S = 3600.0
# docs/CONTRACT.md rule 4: VIP_EXCLUSIVE means any exclusive priority trip (VIP, medical,
# send-to-lobby); the kind is in PriorityTripStarted.kind.
EXCLUSIVE = CarState.VIP_EXCLUSIVE


@dataclass(frozen=True)
class Outcome:
    """Answer of every Part 5 API call. ok=False always carries a reason."""
    ok: bool
    reason: str = "ok"
    ref: Optional[str] = None          # trip / request / move id
    car_id: Optional[CarId] = None


@dataclass
class Hold:
    """A car Part 5 has taken out of group service through car_states."""
    car_id: CarId
    state: CarState
    kind: str                          # vip | medical | send_to_lobby | maintenance | goods
    ref: str
    since: float


@dataclass
class Trip:
    trip_id: str
    kind: str                          # "vip" | "medical" | "send_to_lobby"
    car_id: CarId
    group: GroupId
    origin: Floor
    destination: Floor
    started_at: float
    call_id: Optional[CallId] = None   # VIP only
    phase: str = "to_origin"           # to_origin | at_origin | to_destination | at_destination
    boarded: bool = False              # VIP rider (or patient) is in the car
    end_reason: Optional[str] = None


@dataclass
class VipGrant:
    """FR-70: an FM decision about one VIP credential (quota per day, or revoked)."""
    trips_per_day: Optional[int] = None
    revoked: bool = False
    used: dict = field(default_factory=dict)       # date iso -> exclusive trips started
