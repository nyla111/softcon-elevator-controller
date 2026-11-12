"""EMS-side heartbeat monitor (FR-104).

Remembers the time of the last CarStatusUpdated of every car and checks once per second.
When a car has been silent for config.HEARTBEAT_TIMEOUT_S (3 s) it publishes HeartbeatLost
once and marks the car disconnected in the simulator (status().disconnected = True, commands
refused). When statuses come back it publishes HeartbeatRestored and clears the mark.
Part 4 decides what the lost heartbeat means for the car state; this module only publishes.

Timing: statuses arrive at whole seconds and the check runs right after them, so a car whose
last status was at t is reported lost at exactly t + 3.
"""
from __future__ import annotations

from ems.contracts import config
from ems.contracts.events import CarStatusUpdated, HeartbeatLost, HeartbeatRestored


class HeartbeatMonitor:
    def __init__(self, ctx, simulator) -> None:
        self.ctx = ctx
        self.sim = simulator
        self.last_seen = {car_id: 0.0 for car_id in simulator.cars}
        self.lost: set[str] = set()
        ctx.bus.subscribe(CarStatusUpdated, self.on_status)
        ctx.clock.every(1.0, self.check)

    def on_status(self, ev: CarStatusUpdated) -> None:
        car_id = ev.status.car_id
        self.last_seen[car_id] = ev.t
        if car_id in self.lost:
            self.lost.discard(car_id)
            self.sim.set_heartbeat_lost(car_id, False)
            self.ctx.bus.publish(HeartbeatRestored(ev.t, car_id))

    def check(self) -> None:
        now = self.ctx.clock.now()
        for car_id, seen in self.last_seen.items():
            if car_id not in self.lost and now - seen >= config.HEARTBEAT_TIMEOUT_S:
                self.lost.add(car_id)
                self.sim.set_heartbeat_lost(car_id, True)
                self.ctx.bus.publish(HeartbeatLost(now, car_id))
