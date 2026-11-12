"""A simulated passenger. The simulator owns this state (not the dispatcher)."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ems.contracts.types import CarId, Floor, GroupId


class PassengerState(Enum):
    WAITING = "waiting"      # at the origin floor, not yet in a car
    RIDING = "riding"
    DONE = "done"            # has left the car at the destination
    CANCELLED = "cancelled"


@dataclass
class Passenger:
    passenger_id: str        # also used as the call_id
    origin: Floor
    destination: Floor
    group: GroupId
    accessible: bool
    vip: bool
    arrived_at: float
    car_id: Optional[CarId] = None       # set by CallAssigned / CallReassigned
    state: PassengerState = PassengerState.WAITING
    boarded_at: Optional[float] = None
    alighted_at: Optional[float] = None

    @property
    def wait_s(self) -> Optional[float]:
        return None if self.boarded_at is None else self.boarded_at - self.arrived_at

    @property
    def ride_s(self) -> Optional[float]:
        if self.alighted_at is None or self.boarded_at is None:
            return None
        return self.alighted_at - self.boarded_at
