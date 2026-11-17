"""Dispatch pauses: EMS server loss (FR-54), fire (FR-50, FR-55), mains loss (FR-110).

Mixin of Dispatcher (service.py). While paused no command is sent and calls queue up.
"""
from __future__ import annotations

from ems.contracts.events import (EmsServerDown, EmsServerUp, FireAlarm, FireAlarmCleared,
                                  MainsLost)


class PauseMixin:
    def on_fire(self, ev: FireAlarm) -> None:
        self.fire_hold, self.fire_cleared = True, False
        self._requeue_all("fire")

    def on_fire_cleared(self, ev: FireAlarmCleared) -> None:
        self.fire_cleared = True

    def on_mains_lost(self, ev: MainsLost) -> None:
        self.power_hold = True

    def on_power_back(self, ev) -> None:
        self.power_hold = False

    def on_server_down(self, ev: EmsServerDown) -> None:
        self.ems_down = True
        for rec in [self.calls[c] for c in list(self.open)]:
            if rec.state == "pending":   # unassigned: the controllers take them now
                rec.state = "controller"

    def on_server_up(self, ev: EmsServerUp) -> None:
        self.ems_down = False

    def _requeue_all(self, why: str) -> None:
        """Fire recall: controllers dropped every stop. Waiting calls go back to the queue,
        keeping their wait clock; exclusive priority trips are over."""
        for car_id in list(self.waiting):
            for cid in list(self.waiting.get(car_id, ())):
                rec = self.calls[cid]
                self._unlink(rec)
                rec.prev_car, rec.car, rec.state = car_id, None, "pending"
        for rec in self.calls.values():
            if rec.state == "vip":
                rec.prev_car, rec.car, rec.state = rec.car, None, "pending"
        self.exclusive.clear()

    def _may_dispatch(self) -> bool:
        if self.ems_down or self.power_hold:
            return False
        safety = self._svc("car_states").safety_active()
        if self.fire_hold:
            if self.fire_cleared and not safety:
                self.fire_hold = False           # FR-55 recovery granted by Part 4
            else:
                return False
        return not safety
