"""The replaceable dispatch plug-in (FR-06): EtdEngine, "estimated time to destination".

For a new call, every candidate car is replayed twice with the trip model (model.py): once with
the passengers it already serves, once with the new call added. The cost of a car is

    w_wait * new call's wait + w_ride * new call's ride
  + sum over the car's other passengers of (w_wait * extra wait + w_ride * extra arrival delay)
  + w_run * extra moving seconds and door cycles                    (energy, mode dependent)
  + w_busy * extra seconds the car stays busy (it comes back later for the passengers who have
    not arrived yet: this makes a car serve few floors per trip, like up-peak sectoring)
  + a penalty for every passenger pushed towards the mode's wait ceiling (FR-13, BR-01), and a
    much larger one for every second a passenger is pushed past the ceiling itself

and the cheapest car wins (ties: lower car id). A car where the new call would not fit on the
trip (FR-101) is skipped. None = no car can take the call now (it waits in the pending queue).

The engine only reads; it never sends commands. The dispatcher calls it through Part 1's guard
(FR-105), which falls back to nearest-car if this raises or takes more than 1 s.
"""
from __future__ import annotations

from typing import Optional, Protocol

from ems.contracts.config import MODE_TARGETS
from ems.contracts.types import Call, CallPriority, CarId, CarStatus, Mode

from .model import Pax, Plan, replay

# (w_wait, w_ride, w_run, w_busy): Rush = waiting time first, Eco = run time first (SRS 4.3)
WEIGHTS = {
    Mode.RUSH: (1.0, 0.25, 0.0, 1.0),
    Mode.BALANCED: (1.0, 0.5, 0.2, 0.5),
    Mode.ECO: (1.0, 0.3, 3.0, 0.0),
}
DOOR_CYCLE_RUN_S = 4.0         # a door cycle counted as run time (2 s open + 2 s close)
CEILING_PENALTY = 10.0         # cost per second a passenger's total wait goes past the soft line
SOFT_CEILING = 0.75            # soft line = 75% of the mode's wait ceiling
# cost per second a passenger is pushed past the ceiling itself; Balanced only (in Rush it
# lengthens trips at peak and the average gets worse, measured on SIM-01/02)
HARD_PENALTY = {Mode.RUSH: 0.0, Mode.BALANCED: 100.0, Mode.ECO: 0.0}
QUADRATIC = 1.0                # waits cost w + Q * w^2 / ceiling: long waits weigh more (BR-01)
WEAR_COST_SLACK = 15.0         # permit a small ETD trade-off for the least-used car
ECO_WEAR_COST_SLACK = 5.0      # Eco is more sensitive to active-car location
WEAR_PICKUP_SLACK_S = 5.0      # never add more than five predicted pickup seconds for wear


class Book(Protocol):
    """What the engine needs to know about each car (the dispatcher implements it)."""
    def riders_of(self, car_id: CarId) -> list[Pax]: ...
    def waiting_of(self, car_id: CarId) -> list[Pax]: ...
    def capacity_of(self, status: CarStatus) -> int: ...
    def extra_load_of(self, status: CarStatus) -> int: ...
    def elapsed(self, call_id: str) -> float: ...
    def pax_of(self, call: Call) -> Pax: ...
    def wear_distance(self, car_id: CarId) -> int: ...


class EtdEngine:
    name = "etd"

    def __init__(self, book: Book) -> None:
        self.book = book
        self.last: dict[CarId, tuple[float, float]] = {}   # car -> (cost, predicted pickup s)

    def choose_car(self, call: Call, cars: list[CarStatus], mode: Mode) -> Optional[CarId]:
        self.last = {}
        choices: list[tuple[float, float, int, CarId]] = []
        for status in sorted(cars, key=lambda s: s.car_id):
            if status.group != call.group:
                continue
            res = self.evaluate(call, status, mode)
            if res is None:
                continue
            cost, eta = res
            self.last[status.car_id] = (cost, eta)
            choices.append((cost, eta, self.book.wear_distance(status.car_id), status.car_id))
        if not choices:
            return None
        best = min(choices, key=lambda item: (item[0], item[1], item[3]))
        best_cost, best_eta = best[:2]
        slack = ECO_WEAR_COST_SLACK if mode is Mode.ECO else WEAR_COST_SLACK
        close = [item for item in choices
                 if item[0] <= best_cost + slack
                 and item[1] <= best_eta + WEAR_PICKUP_SLACK_S]
        # Preserve the mode cost and pickup bound; select the least-worn feasible car.
        return min(close, key=lambda item: (item[2], item[0], item[3]))[3]

    # ------------------------------------------------------------------ cost
    def evaluate(self, call: Call, status: CarStatus, mode: Mode) -> Optional[tuple[float, float]]:
        """(cost, predicted pickup seconds) of giving `call` to this car, None if it cannot."""
        book = self.book
        riders = book.riders_of(status.car_id)
        waiting = [p for p in book.waiting_of(status.car_id) if p.call_id != call.call_id]
        cap = book.capacity_of(status)
        extra = book.extra_load_of(status)
        new = book.pax_of(call)
        base = replay(status, riders, waiting, cap, extra)
        with_new = replay(status, riders, waiting + [new], cap, extra, new_call=call.call_id)
        if call.call_id in with_new.overflow or call.call_id not in with_new.pickup:
            return None                                     # FR-101: no room on this trip
        if with_new.overflow - base.overflow:
            return None                                     # it would push someone off
        w_wait, w_ride, w_run, w_busy = WEIGHTS[mode]
        if call.priority is CallPriority.ACCESSIBLE:        # BR-10: served before ordinary calls
            w_wait *= 2.0
        ceiling = MODE_TARGETS[mode][1]
        wait = with_new.pickup[call.call_id]
        ride = with_new.arrive.get(call.call_id, wait) - wait
        cost = w_wait * self._wcost(wait, ceiling) + w_ride * ride
        cost += self._others(base, with_new, w_wait, w_ride, ceiling)
        cost += w_run * ((with_new.run_s - base.run_s)
                         + DOOR_CYCLE_RUN_S * (with_new.door_cycles - base.door_cycles))
        cost += w_busy * max(0.0, with_new.end_s - base.end_s)
        cost += self._ceiling(base, with_new, call, mode)
        return cost, wait

    @staticmethod
    def _wcost(total_wait: float, ceiling: float) -> float:
        return total_wait + QUADRATIC * total_wait * total_wait / ceiling

    def _others(self, base: Plan, new: Plan, w_wait: float, w_ride: float,
                ceiling: float) -> float:
        extra = 0.0
        for cid, t in base.pickup.items():
            if cid in new.pickup and new.pickup[cid] > t:
                el = self.book.elapsed(cid)
                extra += w_wait * (self._wcost(el + new.pickup[cid], ceiling)
                                   - self._wcost(el + t, ceiling))
        for cid, t in base.arrive.items():
            if cid in new.arrive:
                extra += w_ride * max(0.0, new.arrive[cid] - t)
        return extra

    def _ceiling(self, base: Plan, new: Plan, call: Call, mode: Mode) -> float:
        warn = SOFT_CEILING * MODE_TARGETS[mode][1]
        hard = MODE_TARGETS[mode][1]
        def over(plan: Plan, line: float) -> float:
            return sum(max(0.0, self.book.elapsed(cid) + t - line)
                       for cid, t in plan.pickup.items())
        return (CEILING_PENALTY * max(0.0, over(new, warn) - over(base, warn))
                + HARD_PENALTY[mode] * max(0.0, over(new, hard) - over(base, hard)))
