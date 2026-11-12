"""Car simulator: implements ControllerAdapter (ctx.services["controllers"]).

Each car is a small state machine that moves through these phases, one at a time
(travel and door phases never overlap):

    IDLE -> MOVING (1.5 s per floor) -> OPENING (2 s) -> DWELL -> CLOSING (2 s) -> IDLE

* DoorsOpened is published when the doors are fully open, and the dwell timer
  starts at that moment.
* Dwell = max(5 s, or 10 s if an accessible passenger boards/alights,
  1 s x number of passengers boarding + alighting).
* Passengers move one at a time, 1 s each: alighters first, then boarders.
* Stop order is simple collective: keep going the same way while there are
  stops ahead, then reverse.

Assumptions (agreed with the tech lead):
* DoorsOpened is published when the doors are FULLY open, not when they start to open.
* Overload: if more passengers wait for this car than fit, the extra ones stay at the floor
  and OverloadDetected is published (load_pct > 100). Their stop is put back when the car
  leaves the floor (otherwise it would re-open here forever); the car comes back for them.
* Fault: a faulted car freezes where it is; when cleared it restarts the interrupted phase
  from its beginning (e.g. a full 1.5 s step, a full door cycle).
* CallReassigned: the simulator drops the origin stop on the old car directly, because
  cancel_stop() is refused for a faulted car.
* Every callback a car schedules carries the car's "epoch". A fault bumps the
  epoch, so everything the car had scheduled is silently dropped (frozen car).
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from ems.contracts import config
from ems.contracts.events import (CallAssigned, CallCancelled, CallReassigned, CarStatusUpdated,
                                  ControllerFault, ControllerFaultCleared, DoorsOpened,
                                  OverloadDetected, PassengerAlighted, PassengerArrived,
                                  PassengerBoarded)
from ems.contracts.types import (CallId, CarId, CarStatus, Direction, DoorState, Floor,
                                 GroupId)

from .passengers import Passenger, PassengerState


class Phase(Enum):
    IDLE = "idle"
    MOVING = "moving"
    OPENING = "opening"
    DWELL = "dwell"
    CLOSING = "closing"


def next_floor(floor: Floor, direction: Direction) -> Floor:
    """The floor one step up/down. There is no floor 0: L1 sits right above B1."""
    index = floor if floor < 0 else floor - 1          # B1=-1, L1=0, L2=1, ...
    index += 1 if direction is Direction.UP else -1
    return index if index < 0 else index + 1


class SimCar:
    def __init__(self, sim: "Simulator", car_id: CarId, group: GroupId,
                 floors: list[Floor], capacity: int) -> None:
        self.sim = sim
        self.ctx = sim.ctx
        self.car_id = car_id
        self.group = group
        self.floors = floors
        self.capacity = capacity               # rated persons
        self.p = config.SIM                    # physics numbers

        self.floor: Floor = config.LOBBY       # last floor the car was at
        self.direction = Direction.IDLE
        self.phase = Phase.IDLE
        self.doors = DoorState.CLOSED
        self.stops: list[tuple[Floor, str]] = []      # (floor, stop key); key = call id
        self.deferred: list[tuple[Floor, str]] = []   # left behind (car full), see _doors_open
        self.riders: list[Passenger] = []

        self.move_started = 0.0
        self.move_to = self.floor
        self.frozen_fraction = 0.0             # how far along a step when a fault hit
        self.epoch = 0
        self.run_time_s = 0.0                  # seconds spent travelling (for the metrics)

        self.fault_code: Optional[str] = None
        self.disconnected = False
        self.standby = False
        self.independent = False
        self.closed_to_new_calls = False
        self.park_floor: Optional[Floor] = None
        self.express_floor: Optional[Floor] = None
        self.hold_doors = False                # express(..., hold_doors_open=True)
        self.held = False                      # dwell is over but doors stay open

    # ---------- helpers ----------
    def _now(self) -> float:
        return self.ctx.clock.now()

    def _later(self, delay: float, fn: Callable[[], None]) -> None:
        epoch = self.epoch

        def run() -> None:
            if epoch == self.epoch:            # skip if a fault happened in between
                fn()
        self.ctx.clock.call_later(delay, run)

    def stop_floors(self) -> list[Floor]:
        floors = {f for f, _ in self.stops}
        if self.express_floor is not None:
            floors.add(self.express_floor)
        return sorted(floors)

    def targets(self) -> list[Floor]:
        """Floors the car is heading for: only the express floor while an express is active."""
        return [self.express_floor] if self.express_floor is not None else self.stop_floors()

    def accepts_calls(self) -> bool:
        return (self.reachable() and not self.standby and not self.independent
                and not self.closed_to_new_calls)

    def reachable(self) -> bool:
        return self.fault_code is None and not self.disconnected

    # ---------- telemetry ----------
    def status(self) -> CarStatus:
        position = float(self.floor)
        if self.phase is Phase.MOVING:
            if self.fault_code is None:
                fraction = min(1.0, (self._now() - self.move_started) / self.p["s_per_floor"])
            else:
                fraction = self.frozen_fraction
            position = self.floor + (self.move_to - self.floor) * fraction
        return CarStatus(
            car_id=self.car_id, group=self.group, floor=self.floor, position=position,
            direction=self.direction, doors=self.doors,
            load_pct=100.0 * len(self.riders) / self.capacity, passengers=len(self.riders),
            fault_code=self.fault_code, disconnected=self.disconnected,
            pending_stops=self.stop_floors())

    # ---------- stops ----------
    def add_stop(self, floor: Floor, key: str) -> None:
        if (floor, key) not in self.stops:
            self.stops.append((floor, key))
        self.wake()

    def drop_stop(self, key: str) -> bool:
        before = len(self.stops) + len(self.deferred)
        self.stops = [s for s in self.stops if s[1] != key]
        self.deferred = [s for s in self.deferred if s[1] != key]
        return len(self.stops) + len(self.deferred) < before

    def release_hold(self) -> None:
        self.hold_doors = False
        if self.held:
            self.held = False
            self._start_closing()

    def wake(self) -> None:
        """Something changed: if the car is sitting idle, decide what to do."""
        if self.phase is Phase.IDLE and self.fault_code is None:
            self._decide()

    # ---------- the state machine ----------
    def _decide(self) -> None:
        """Called whenever the car is stationary with closed doors."""
        wanted = self.targets()
        if self.floor in wanted:
            self._open_doors()
            return
        if wanted:
            target = wanted
        elif self.park_floor is not None and self.park_floor != self.floor:
            target = [self.park_floor]         # drive to the parking floor, doors stay shut
        else:
            self.direction = Direction.IDLE
            self.phase = Phase.IDLE
            return
        self.direction = self._pick_direction(target)
        self._start_move()

    def _pick_direction(self, target: list[Floor]) -> Direction:
        above = [f for f in target if f > self.floor]
        below = [f for f in target if f < self.floor]
        if self.direction is Direction.UP and above:
            return Direction.UP
        if self.direction is Direction.DOWN and below:
            return Direction.DOWN
        if above and below:                    # was idle: go to the nearest stop first
            return Direction.UP if min(above) - self.floor <= self.floor - max(below) \
                else Direction.DOWN
        return Direction.UP if above else Direction.DOWN

    def _start_move(self) -> None:
        if self.deferred:                      # leaving the floor: left-behind calls come back
            self.stops += self.deferred
            self.deferred = []
        self.phase = Phase.MOVING
        self.move_to = next_floor(self.floor, self.direction)
        self.move_started = self._now()
        self._later(self.p["s_per_floor"], self._arrive)

    def _arrive(self) -> None:
        self.floor = self.move_to
        self.run_time_s += self.p["s_per_floor"]
        self.phase = Phase.IDLE
        self._decide()                         # open here, or keep going

    def _open_doors(self) -> None:
        self.phase = Phase.OPENING
        self.doors = DoorState.OPENING
        self._later(self.p["door_open_s"], self._doors_open)

    def _doors_open(self) -> None:
        self.phase = Phase.DWELL
        self.doors = DoorState.OPEN
        self.stops = [s for s in self.stops if s[0] != self.floor]
        if self.express_floor == self.floor:
            self.express_floor = None
        self.ctx.bus.publish(DoorsOpened(self._now(), self.car_id, self.floor))

        leaving = [p for p in self.riders if p.destination == self.floor]
        waiting = self.sim.waiting_at(self.car_id, self.floor)
        free = self.capacity - (len(self.riders) - len(leaving))
        boarding, left_behind = waiting[:free], waiting[free:]
        if left_behind:
            # Car is full: they stay at the floor. Their stop comes back when the car
            # leaves (deferred), otherwise it would re-open here forever.
            load = 100.0 * (len(self.riders) - len(leaving) + len(waiting)) / self.capacity
            self.ctx.bus.publish(OverloadDetected(self._now(), self.car_id, self.floor, load))
            self.deferred += [(self.floor, p.passenger_id) for p in left_behind]

        moves = [(self._alight, p) for p in leaving] + [(self._board, p) for p in boarding]
        for i, (action, passenger) in enumerate(moves):
            self._later((i + 1) * self.p["s_per_passenger"], lambda a=action, q=passenger: a(q))
        accessible = any(p.accessible for _, p in moves)
        base = self.p["accessible_dwell_s"] if accessible else self.p["min_dwell_s"]
        self._later(max(base, len(moves) * self.p["s_per_passenger"]), self._end_dwell)

    def _board(self, p: Passenger) -> None:
        if p.state is not PassengerState.WAITING or p.car_id != self.car_id:
            return                             # cancelled or reassigned meanwhile
        if len(self.riders) >= self.capacity:  # safety net: never exceed rated persons
            return
        self.sim.waiting.pop(p.passenger_id, None)
        p.state = PassengerState.RIDING
        p.boarded_at = self._now()
        self.riders.append(p)
        self.stops.append((p.destination, p.passenger_id + ":dest"))   # simulator registers it
        self.ctx.bus.publish(PassengerBoarded(self._now(), p.passenger_id, self.car_id))

    def _alight(self, p: Passenger) -> None:
        self.riders.remove(p)
        p.state = PassengerState.DONE
        p.alighted_at = self._now()
        self.ctx.bus.publish(PassengerAlighted(self._now(), p.passenger_id, self.car_id,
                                               self.floor))

    def _end_dwell(self) -> None:
        if self.hold_doors:
            self.held = True                   # stays open until park() / express()
            return
        self._start_closing()

    def _start_closing(self) -> None:
        self.phase = Phase.CLOSING
        self.doors = DoorState.CLOSING
        self._later(self.p["door_close_s"], self._closed)

    def _closed(self) -> None:
        self.doors = DoorState.CLOSED
        self.phase = Phase.IDLE
        self._decide()

    # ---------- faults ----------
    def fault(self, code: str) -> None:
        if self.fault_code is not None:
            return
        if self.phase is Phase.MOVING:
            self.frozen_fraction = min(
                1.0, (self._now() - self.move_started) / self.p["s_per_floor"])
        self.fault_code = code
        self.epoch += 1                        # freeze: drop everything scheduled
        self.ctx.bus.publish(ControllerFault(
            self._now(), self.car_id, code, between_floors=self.phase is Phase.MOVING,
            doors_stuck=self.doors is not DoorState.CLOSED))

    def clear_fault(self) -> None:
        if self.fault_code is None:
            return
        self.fault_code = None
        self.ctx.bus.publish(ControllerFaultCleared(self._now(), self.car_id))
        # Restart the interrupted phase from its beginning.
        if self.phase is Phase.MOVING:
            self._start_move()
        elif self.phase is Phase.OPENING:
            self._open_doors()
        elif self.phase is Phase.DWELL:
            self._doors_open()
        elif self.phase is Phase.CLOSING:
            self._start_closing()
        else:
            self._decide()


class Simulator:
    """All cars + passengers. Implements ControllerAdapter.

    Refusal rules (every command returns False when refused):
      register_stop : car faulted, disconnected, standby, independent service,
                      closed to new calls, or floor not served by this car
      park, express : car faulted, disconnected, independent service, floor not served
      set_*, close_to_new_calls : car faulted or disconnected
      cancel_stop   : car faulted or disconnected, or no such stop
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.cars: dict[CarId, SimCar] = {}
        self.passengers: dict[str, Passenger] = {}
        self.waiting: dict[str, Passenger] = {}        # arrival order
        for group, info in config.GROUPS.items():
            rated = config.RATED["S" if group == "SERVICE" else "passenger"]["persons"]
            for car_id in info["cars"]:
                self.cars[car_id] = SimCar(self, car_id, group, info["floors"], rated)
        ctx.bus.subscribe(PassengerArrived, self.on_arrived)
        ctx.bus.subscribe(CallAssigned, self.on_assigned)
        ctx.bus.subscribe(CallReassigned, self.on_reassigned)
        ctx.bus.subscribe(CallCancelled, self.on_cancelled)
        ctx.clock.every(config.SIM["status_interval_s"], self._publish_status)

    # ---------- passengers ----------
    def on_arrived(self, ev: PassengerArrived) -> None:
        p = Passenger(ev.passenger_id, ev.origin, ev.destination, ev.group,
                      ev.accessible, ev.vip, ev.t)
        self.passengers[p.passenger_id] = p
        self.waiting[p.passenger_id] = p

    def on_assigned(self, ev: CallAssigned) -> None:
        p = self.waiting.get(ev.call_id)
        if p is not None:
            p.car_id = ev.car_id

    def on_reassigned(self, ev: CallReassigned) -> None:
        p = self.waiting.get(ev.call_id)
        if p is None:
            return
        old = self.cars.get(ev.from_car)
        if old is not None:
            old.drop_stop(ev.call_id)          # the old car no longer needs to come
        p.car_id = ev.to_car

    def on_cancelled(self, ev: CallCancelled) -> None:
        p = self.waiting.pop(ev.call_id, None)
        if p is None:
            return
        p.state = PassengerState.CANCELLED
        car = self.cars.get(p.car_id) if p.car_id else None
        if car is not None:
            car.drop_stop(ev.call_id)

    def waiting_at(self, car_id: CarId, floor: Floor) -> list[Passenger]:
        return [p for p in self.waiting.values() if p.car_id == car_id and p.origin == floor]

    def undelivered(self) -> int:
        """Passengers who have arrived but not yet left a car."""
        return sum(1 for p in self.passengers.values()
                   if p.state in (PassengerState.WAITING, PassengerState.RIDING))

    # ---------- telemetry ----------
    def _publish_status(self) -> None:
        for car in self.cars.values():
            if not car.disconnected:
                self.ctx.bus.publish(CarStatusUpdated(self.ctx.clock.now(), car.status()))

    # ---------- fault injection (used by the harness and tests) ----------
    def inject_fault(self, car_id: CarId, code: str = "F-01") -> None:
        self.cars[car_id].fault(code)

    def clear_fault(self, car_id: CarId) -> None:
        self.cars[car_id].clear_fault()

    def set_disconnected(self, car_id: CarId, on: bool) -> None:
        self.cars[car_id].disconnected = on

    # ---------- ControllerAdapter ----------
    def status(self, car_id: CarId) -> CarStatus:
        return self.cars[car_id].status()

    def all_status(self, group: Optional[GroupId] = None) -> list[CarStatus]:
        return [c.status() for c in self.cars.values() if group is None or c.group == group]

    def register_stop(self, car_id: CarId, floor: Floor, call_id: CallId) -> bool:
        car = self.cars.get(car_id)
        if car is None or not car.accepts_calls() or floor not in car.floors:
            return False
        car.add_stop(floor, call_id)
        return True

    def cancel_stop(self, car_id: CarId, call_id: CallId) -> bool:
        car = self.cars.get(car_id)
        return car is not None and car.reachable() and car.drop_stop(call_id)

    def park(self, car_id: CarId, floor: Floor) -> bool:
        car = self.cars.get(car_id)
        if car is None or not car.reachable() or car.independent or floor not in car.floors:
            return False
        car.park_floor = floor
        car.release_hold()
        car.wake()
        return True

    def set_standby(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "standby", on)

    def set_independent_service(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "independent", on)

    def close_to_new_calls(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "closed_to_new_calls", on)

    def express(self, car_id: CarId, floor: Floor, hold_doors_open: bool = False) -> bool:
        car = self.cars.get(car_id)
        if car is None or not car.reachable() or car.independent or floor not in car.floors:
            return False
        car.release_hold()
        car.express_floor = floor
        car.hold_doors = hold_doors_open
        car.wake()
        return True

    def _set_flag(self, car_id: CarId, flag: str, on: bool) -> bool:
        car = self.cars.get(car_id)
        if car is None or not car.reachable():
            return False
        setattr(car, flag, on)
        return True
