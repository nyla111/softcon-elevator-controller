"""Trip model: predicts when a car picks up and delivers every passenger it is responsible for.

Pure functions, no ctx. The dispatcher keeps a book of who rides / waits for each car (it knows
destinations, the controller only learns them when a passenger boards), and this module replays
the controller's stop logic (simple collective, Appendix B timings) to get the times.

Replayed controller rules (ems/sim/cars.py, Appendix B):
* keep going the same way while there are stops ahead, then reverse; idle car: nearest stop first;
* every stop = open 2 s + dwell + close 2 s; dwell = max(5 s, 10 s if accessible, 1 s per
  passenger boarding or alighting, EMS min-dwell request); 1.5 s per floor;
* alighting first, then boarding, 1 s each.
The prediction is an estimate: it is used to compare cars, never as a promise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ems.contracts.config import SIM
from ems.contracts.types import CarStatus, Direction, DoorState, Floor

S_FLOOR = SIM["s_per_floor"]
OPEN = SIM["door_open_s"]
CLOSE = SIM["door_close_s"]
DWELL = SIM["min_dwell_s"]
DWELL_ACC = SIM["accessible_dwell_s"]
S_PAX = SIM["s_per_passenger"]


@dataclass(frozen=True)
class Pax:
    """One call the car is responsible for. origin=None: already on board."""
    call_id: str
    origin: Optional[Floor]
    dest: Optional[Floor]
    party: int = 1
    accessible: bool = False
    min_dwell: float = 0.0


@dataclass
class Plan:
    """Result of replaying one car."""
    pickup: dict[str, float] = field(default_factory=dict)     # call_id -> seconds from now
    arrive: dict[str, float] = field(default_factory=dict)     # call_id -> seconds from now
    run_s: float = 0.0                                         # moving seconds
    door_cycles: int = 0
    overflow: set[str] = field(default_factory=set)            # calls that would not fit
    end_s: float = 0.0
    end_floor: Optional[Floor] = None                          # where the car is when done


def _start(status: CarStatus) -> tuple[float, Floor, Direction, bool]:
    """(seconds until the car can decide, floor where it decides, direction, doors_busy)."""
    d = status.direction
    if status.doors is DoorState.OPENING:
        return OPEN + DWELL + CLOSE, status.floor, d, True
    if status.doors is DoorState.OPEN:
        return DWELL / 2 + CLOSE, status.floor, d, True
    if status.doors is DoorState.CLOSING:
        return CLOSE / 2, status.floor, d, True
    if d is not Direction.IDLE and abs(status.position - status.floor) > 1e-9:
        nxt = status.floor + (1 if d is Direction.UP else -1)
        if nxt == 0:                                   # there is no floor 0 (B1 -> L1)
            nxt = 1 if d is Direction.UP else -1
        return abs(nxt - status.position) * S_FLOOR, nxt, d, False
    return 0.0, status.floor, d, False


def _step(f: Floor, d: Direction) -> Floor:
    n = f + (1 if d is Direction.UP else -1)
    return n if n != 0 else (1 if d is Direction.UP else -1)


def _distance(a: Floor, b: Floor) -> int:
    """Floors travelled between a and b (no floor 0)."""
    n = abs(a - b)
    return n - 1 if (a < 0 < b or b < 0 < a) else n


def _pick(f: Floor, d: Direction, targets: set[Floor]) -> Direction:
    above = [t for t in targets if t > f]
    below = [t for t in targets if t < f]
    if d is Direction.UP and above:
        return Direction.UP
    if d is Direction.DOWN and below:
        return Direction.DOWN
    if above and below:
        return Direction.UP if _distance(min(above), f) <= _distance(f, max(below)) \
            else Direction.DOWN
    return Direction.UP if above else Direction.DOWN


def replay(status: CarStatus, riders: list[Pax], waiting: list[Pax], capacity: int,
           extra_load: int = 0, new_call: Optional[str] = None) -> Plan:
    """Replay the car until every rider is delivered and every waiting call picked up.

    extra_load: riders the controller counts but the book does not know (never alight here).
    new_call: the call being tried; if it waits at the floor where the doors are busy right
    now it needs a new door cycle (the controller only boards people waiting when doors open).
    """
    plan = Plan()
    t, f, d, doors_busy = _start(status)
    on_board: list[Pax] = list(riders)
    load = extra_load + sum(p.party for p in riders)
    pending = {p.call_id: p for p in waiting}
    # passengers already boarding through the open doors are on board almost at once
    if doors_busy and status.doors in (DoorState.OPEN, DoorState.OPENING):
        for p in list(pending.values()):
            if p.origin == f and p.call_id != new_call:
                plan.pickup[p.call_id] = 0.0 if status.doors is DoorState.OPEN else OPEN
                on_board.append(p)
                load += p.party
                del pending[p.call_id]
    deferred: dict[str, Pax] = {}      # left behind by a full car: back when the car leaves
    for _ in range(4000):                # bound: never loop forever on bad input
        targets = {p.dest for p in on_board if p.dest is not None} | \
                  {p.origin for p in pending.values()}
        if not targets:
            if deferred:                 # empty car comes back for them at once
                pending.update(deferred)
                deferred.clear()
                continue
            break                        # riders with unknown destination: not modelled
        if f in targets:
            t += OPEN
            plan.door_cycles += 1
            leaving = [p for p in on_board if p.dest == f]
            for i, p in enumerate(leaving):
                plan.arrive[p.call_id] = t + (i + 1) * S_PAX
            on_board = [p for p in on_board if p.dest != f]
            load -= sum(p.party for p in leaving)
            boarding = []
            for p in [p for p in pending.values() if p.origin == f]:
                del pending[p.call_id]
                if p.party > capacity:
                    plan.overflow.add(p.call_id)          # can never board this car
                elif load + p.party > capacity:
                    plan.overflow.add(p.call_id)
                    deferred[p.call_id] = p
                else:
                    load += p.party
                    boarding.append(p)
            for i, p in enumerate(boarding):
                plan.pickup[p.call_id] = t + (len(leaving) + i + 1) * S_PAX
                on_board.append(p)
            moves = len(leaving) + sum(p.party for p in boarding)
            acc = any(p.accessible for p in leaving + boarding)
            t += max(DWELL_ACC if acc else DWELL, moves * S_PAX,
                     max((p.min_dwell for p in boarding), default=0.0)) + CLOSE
            continue
        if deferred:                     # leaving the floor: left-behind calls come back
            pending.update(deferred)
            deferred.clear()
        d = _pick(f, d, targets)
        ahead = [x for x in targets if (x > f if d is Direction.UP else x < f)]
        nxt = min(ahead) if d is Direction.UP else max(ahead)   # next stop: no event between
        dist = _distance(f, nxt)
        f = nxt
        t += dist * S_FLOOR
        plan.run_s += dist * S_FLOOR
    plan.end_s = t
    plan.end_floor = f
    return plan
