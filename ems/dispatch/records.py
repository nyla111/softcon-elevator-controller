"""Per-call record of the dispatcher (shared by its mixins)."""
from __future__ import annotations

from typing import Optional

from ems.contracts.types import Call, CarId

BY = "dispatch"                  # requested_by for car_states


OPEN_STATES = ("pending", "assigned")


class Rec:
    """One call as the dispatcher sees it. Changing `state` keeps the open-call index current
    (pending or assigned calls), so periodic work never scans the whole call history."""

    def __init__(self, call: Call, index: set) -> None:
        self.call = call
        self._index = index
        self._state = ""
        self.car: Optional[CarId] = None
        self.prev_car: Optional[CarId] = None   # car before fire / server loss (CallReassigned)
        self.avoid: Optional[CarId] = None      # must leave this car (FR-04) when another can
        self.eta = 0.0
        self.risk_sent = False
        self.picked_up_at: Optional[float] = None
        self.min_dwell = 0.0                    # door-open time asked with the stop
        self.state = "pending"  # pending | assigned | riding | done | cancelled | controller | vip

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        self._state = value
        if value in OPEN_STATES:
            self._index.add(self.call.call_id)
        else:
            self._index.discard(self.call.call_id)
