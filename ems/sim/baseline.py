"""Trivial nearest-car chooser, only for testing the simulator before Part 2 exists.

It listens to CallRequested, picks a car and publishes CallAssigned (normally Part 2's job).
Its ranking (rank_cars) is also used by the controllers' built-in group logic (failover.py).
Never register it together with the real dispatcher: calls would be assigned twice.
Install it from a test or the harness:  run(profile, seed, setup=install_nearest_car)
"""
from __future__ import annotations

from ems.contracts.config import SIM
from ems.contracts.events import CallAssigned, CallRequested
from ems.contracts.types import Call

STOP_PENALTY_FLOORS = 6      # each pending stop counts like 6 extra floors of distance


def rank_cars(statuses, origin):
    """Cars sorted best-first: closest to the origin floor, with a penalty per pending stop."""
    return sorted(statuses, key=lambda s: (abs(s.position - origin)
                                           + STOP_PENALTY_FLOORS * len(s.pending_stops),
                                           s.car_id))


class NearestCar:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        ctx.bus.subscribe(CallRequested, self.on_call)

    def on_call(self, ev: CallRequested) -> None:
        call: Call = ev.call
        controllers = self.ctx.services["controllers"]
        cars = [s for s in controllers.all_status(call.group)
                if s.fault_code is None and not s.disconnected]
        for car in rank_cars(cars, call.origin):
            if controllers.register_stop(car.car_id, call.origin, call.call_id):
                eta = abs(car.position - call.origin) * SIM["s_per_floor"]
                self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car.car_id, eta))
                return


def install_nearest_car(ctx) -> NearestCar:
    return NearestCar(ctx)
