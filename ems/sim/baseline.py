"""Trivial nearest-car chooser, only for testing the simulator before Part 2 exists.

It listens to CallRequested, picks a car and publishes CallAssigned (normally Part 2's job).
The choice goes through the FR-105 guard (dispatch_guard.py) exactly like Part 2's plug-in will:
NearestEngine is the stand-in plug-in, so a "plug-in crash" makes the guard use its fallback.
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


class NearestEngine:
    """Test-only stand-in for Part 2's DispatchEngine plug-in."""

    def choose_car(self, call, cars, mode):
        usable = [c for c in cars if c.group == call.group]
        return rank_cars(usable, call.origin)[0].car_id if usable else None


class NearestCar:
    def __init__(self, ctx, engine=None) -> None:
        self.ctx = ctx
        self.engine = engine or NearestEngine()
        ctx.bus.subscribe(CallRequested, self.on_call)

    def on_call(self, ev: CallRequested) -> None:
        call: Call = ev.call
        controllers = self.ctx.services["controllers"]
        guard = self.ctx.services["dispatch_guard"]
        mode = self.ctx.services["dispatcher"].current_mode(call.group)
        cars = [s for s in controllers.all_status(call.group)
                if s.fault_code is None and not s.disconnected]
        while cars:                            # a car that refuses the stop is tried no more
            car_id, _ = guard.choose_car(self.engine, call, cars, mode)
            if car_id is None:
                return
            if controllers.register_stop(car_id, call.origin, call.call_id):
                status = next(s for s in cars if s.car_id == car_id)
                eta = abs(status.position - call.origin) * SIM["s_per_floor"]
                self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car_id, eta))
                return
            cars = [s for s in cars if s.car_id != car_id]


def install_nearest_car(ctx) -> NearestCar:
    return NearestCar(ctx)


def install_dispatch_standin(ctx):
    """Install the nearest-car stand-in only while Part 2 is still the stub dispatcher."""
    if type(ctx.services["dispatcher"]).__name__ == "StubDispatcher":
        return NearestCar(ctx)
    return None
