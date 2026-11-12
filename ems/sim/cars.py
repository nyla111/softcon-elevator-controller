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
* Fire (FireAlarm): passenger cars drop their stops, finish the floor they are travelling
  to, go to L1, open the doors and keep them open; riders leave at L1 (marked evacuated).
  Car S publishes FirefighterService(active=True) and keeps its physical state. All cars refuse
  every EMS command until FireAlarmCleared AND reset_fire() were both done.
  Waiting passengers keep their car assignment: the dispatcher must re-assign them.
* Mains loss: every car freezes; 20-40 s later (rng) its rescue device moves it to the nearest
  floor, opens the doors, riders leave (evacuated) and RescueReport("rescued_at_floor") is
  published. A car in failed_rescue publishes nothing, one in stuck_rescue publishes "stuck";
  both stay frozen. Commands are refused until power is back (GeneratorReady or MainsRestored);
  then cars close their doors and resume their stops.
* between_floors fault: the car stops half-way between two floors, riders stay inside.
  doors_stuck fault: doors are forced shut and stay shut. Both clear with clear_fault().
* Link loss (set_disconnected): the car keeps serving the stops it already has, sends no
  status and refuses commands. status().disconnected only turns True once the EMS-side
  heartbeat monitor (heartbeat.py) notices, 3 s after the last status.
* EMS server down (failover.py): commands are refused; after 3 s the controllers serve new
  passengers themselves (built-in group logic, nearest car) until the standby takes over.
* COMMAND AUDIT: every ControllerAdapter command is appended to Simulator.command_log as
  (t, car_id, command, args, accepted).
* Phantom overload (overload_burst): at the car's next door opening it reports an overload and
  nobody boards during that one door cycle.
* Every callback a car schedules carries the car's "epoch". A fault bumps the
  epoch, so everything the car had scheduled is silently dropped (frozen car).
"""
from __future__ import annotations

from enum import Enum
from typing import Callable, Optional

from ems.contracts import config
from ems.contracts.events import (CallAssigned, CallCancelled, CallReassigned, CarStatusUpdated,
                                  ControllerFault, ControllerFaultCleared, DoorsOpened,
                                  FireAlarm, FireAlarmCleared, FirefighterService,
                                  GeneratorReady, MainsLost, MainsRestored, OverloadDetected,
                                  PassengerAlighted, PassengerArrived, PassengerBoarded,
                                  RescueReport)
from ems.contracts.types import (CallId, CarId, CarStatus, Direction, DoorState, Floor,
                                 GroupId)

from .baseline import rank_cars
from .passengers import Passenger, PassengerState

PHANTOM_LOAD_PCT = 110.0           # what an overload_burst makes the car report
RESCUE_DELAY_S = (20.0, 40.0)      # rescue device: uniform draw per car


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
        self.link_lost = False                 # physical link to the EMS is down
        self.heartbeat_lost = False            # set by the EMS-side heartbeat monitor
        self.fire = False                      # fire recall active
        self.firefighter = False               # car S under firefighter service
        self.power_down = False                # mains lost and generator not ready
        self.rescue_pending = False            # rescue device has not finished yet
        self.phantom = False                   # overload_burst armed
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
        """Floors the car is heading for. Fire recall: only L1. Express: only the express floor."""
        if self.fire:
            return [config.LOBBY]
        return [self.express_floor] if self.express_floor is not None else self.stop_floors()

    def accepts_calls(self) -> bool:
        return (self.reachable() and not self.standby and not self.independent
                and not self.closed_to_new_calls)

    def reachable(self) -> bool:
        """Can the EMS command this car?"""
        return not (self.fault_code is not None or self.link_lost or self.heartbeat_lost
                    or self.fire or self.firefighter or self.power_down)

    def operational(self) -> bool:
        """Can the car itself still run (used by the built-in group logic)?"""
        return not (self.fault_code is not None or self.fire or self.firefighter
                    or self.power_down)

    def blocked(self) -> bool:
        """Frozen by a fault, a power loss or a rescue in progress: nothing is scheduled."""
        return self.fault_code is not None or self.power_down or self.rescue_pending

    # ---------- telemetry ----------
    def status(self) -> CarStatus:
        position = float(self.floor)
        if self.phase is Phase.MOVING:
            if self.blocked():
                fraction = self.frozen_fraction
            else:
                fraction = min(1.0, (self._now() - self.move_started) / self.p["s_per_floor"])
            position = self.floor + (self.move_to - self.floor) * fraction
        return CarStatus(
            car_id=self.car_id, group=self.group, floor=self.floor, position=position,
            direction=self.direction, doors=self.doors,
            load_pct=100.0 * len(self.riders) / self.capacity, passengers=len(self.riders),
            fault_code=self.fault_code, disconnected=self.heartbeat_lost,
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
        if self.phase is Phase.IDLE and not self.blocked():
            self._decide()

    # ---------- the state machine ----------
    def _decide(self) -> None:
        """Called whenever the car is stationary with closed doors."""
        if self.deferred and not self.stops and not self.fire:
            self.stops, self.deferred = self.deferred, []   # empty car: come back for them now
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

        leaving = [p for p in self.riders if p.destination == self.floor or self.fire]
        waiting = [] if self.fire else self.sim.waiting_at(self.car_id, self.floor)
        free = self.capacity - (len(self.riders) - len(leaving))
        overload_reported = False
        if self.phantom:                       # overload_burst: one door cycle with "no room"
            self.phantom = False
            free = 0
            overload_reported = True
            self.ctx.bus.publish(OverloadDetected(self._now(), self.car_id, self.floor,
                                                  PHANTOM_LOAD_PCT))
        boarding, left_behind = waiting[:free], waiting[free:]
        if left_behind:
            # Car is full: they stay at the floor. Their stop comes back when the car
            # leaves (deferred), otherwise it would re-open here forever.
            if not overload_reported:
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
        p.evacuated = p.destination != self.floor      # fire recall / rescue: left early
        self.ctx.bus.publish(PassengerAlighted(self._now(), p.passenger_id, self.car_id,
                                               self.floor))

    def _end_dwell(self) -> None:
        if self.hold_doors or self.fire:
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
    def fault(self, code: str, between_floors: bool = False, doors_stuck: bool = False) -> None:
        if self.fault_code is not None:
            return
        moving = self.phase is Phase.MOVING
        if moving and not self.blocked():
            self.frozen_fraction = min(
                1.0, (self._now() - self.move_started) / self.p["s_per_floor"])
        self.fault_code = code
        self.epoch += 1                        # freeze: drop everything scheduled
        if doors_stuck and self.doors is not DoorState.CLOSED:
            self.doors = DoorState.CLOSED      # simplification: jammed shut
            self.phase = Phase.IDLE
        if between_floors and not moving:      # stop half-way to the next floor
            if self.phase is not Phase.IDLE or self.doors is not DoorState.CLOSED:
                self.doors = DoorState.CLOSED
            direction = self.direction if self.direction is not Direction.IDLE else Direction.UP
            if (direction is Direction.UP and self.floor == max(self.floors)) or \
                    (direction is Direction.DOWN and self.floor == min(self.floors)):
                direction = Direction.DOWN if direction is Direction.UP else Direction.UP
            self.direction = direction
            self.move_to = next_floor(self.floor, direction)
            self.frozen_fraction = 0.5
            self.phase = Phase.MOVING
        self.ctx.bus.publish(ControllerFault(
            self._now(), self.car_id, code, between_floors=moving or between_floors,
            doors_stuck=doors_stuck))

    def clear_fault(self) -> None:
        if self.fault_code is None:
            return
        self.fault_code = None
        self.ctx.bus.publish(ControllerFaultCleared(self._now(), self.car_id))
        if not self.power_down:
            self._resume()

    def _resume(self) -> None:
        """Restart the interrupted phase from its beginning."""
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

    # ---------- fire ----------
    def recall(self) -> None:
        """Fire recall: forget all stops, go to L1, open the doors and keep them open."""
        self.fire = True
        self.stops, self.deferred = [], []
        self.express_floor = self.park_floor = None
        self.hold_doors = False
        self.epoch += 1
        if self.blocked():
            return                             # a frozen car joins the recall once it can move
        if self.phase is Phase.MOVING:         # finish the step, then head for L1
            remaining = self.p["s_per_floor"] - (self._now() - self.move_started)
            self._later(max(0.0, remaining), self._arrive)
        elif self.doors is not DoorState.CLOSED:
            self.phase = Phase.CLOSING
            self.doors = DoorState.CLOSING
            self._later(self.p["door_close_s"], self._closed)
        else:
            self.phase = Phase.IDLE
            self._decide()

    def end_fire(self) -> None:
        """reset_fire(): the car is back under normal control."""
        self.fire = False
        if self.blocked():
            return
        if self.held and not self.hold_doors:
            self.held = False
            self._start_closing()
        else:
            self.wake()

    # ---------- power loss ----------
    def power_lost(self, rescue: str) -> None:
        """Mains lost. rescue = "ok" | "failed" (silent) | "stuck" (reports stuck).
        The rescue device runs on its own battery: it finishes even if the generator is ready
        before it does."""
        if self.power_down:
            return
        if self.phase is Phase.MOVING and not self.blocked():
            self.frozen_fraction = min(
                1.0, (self._now() - self.move_started) / self.p["s_per_floor"])
        self.power_down = True
        self.rescue_pending = True
        self.epoch += 1                        # freeze
        delay = self.ctx.rng.uniform(*RESCUE_DELAY_S)
        self.ctx.clock.call_later(delay, lambda: self._rescue(rescue))

    def _rescue(self, mode: str) -> None:
        self.rescue_pending = False
        if mode == "failed":
            pass                               # silent: the car stays frozen until power is back
        elif mode == "stuck":
            self.ctx.bus.publish(RescueReport(self._now(), self.car_id, "stuck"))
        else:
            if self.phase is Phase.MOVING and self.frozen_fraction >= 0.5:
                self.floor = self.move_to      # nearest floor
            self.phase = Phase.DWELL
            self.doors = DoorState.OPEN
            self.held = True                   # doors stay open until power is back
            self.ctx.bus.publish(DoorsOpened(self._now(), self.car_id, self.floor))
            for p in list(self.riders):
                self._alight(p)
            self.ctx.bus.publish(RescueReport(self._now(), self.car_id, "rescued_at_floor"))
        if not self.power_down:
            self._after_power()

    def power_return(self) -> None:
        if not self.power_down:
            return
        self.power_down = False
        if not self.rescue_pending:            # otherwise _rescue() finishes the job
            self._after_power()

    def _after_power(self) -> None:
        if self.fault_code is not None:
            return                             # still faulted
        if self.held:
            if not (self.hold_doors or self.fire):
                self.held = False
                self._start_closing()
        else:
            self._resume()


class Simulator:
    """All cars + passengers. Implements ControllerAdapter.

    Refusal rules (every command returns False when refused and is written to command_log):
      always        : EMS server down (failover), unknown car
      register_stop : car faulted, link lost / heartbeat lost, fire, firefighter service, no
                      power, standby, independent service, closed to new calls, or floor
                      not served by this car
      park, express : as above except standby / closed to new calls
      set_*, close_to_new_calls : car not reachable (fault, link, fire, no power)
      cancel_stop   : car not reachable, or no such stop
    """

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.cars: dict[CarId, SimCar] = {}
        self.passengers: dict[str, Passenger] = {}
        self.waiting: dict[str, Passenger] = {}        # arrival order
        self.command_log: list[tuple] = []             # (t, car_id, command, args, accepted)
        self.ems_down = False                          # set by failover.py
        self.fallback_active = False                   # controllers serve passengers themselves
        self.fallback_periods: list[list] = []         # [start, end or None]
        self.fallback_assignments: list[tuple] = []    # (t, passenger_id, car_id)
        self.fire_alarm_active = False
        self.fire_needs_reset = False
        self.mains_lost = False
        self.generator_ready = False
        self.rescue_failed: set[CarId] = set()         # cars whose rescue device is silent
        self.rescue_stuck: set[CarId] = set()          # cars that report "stuck"
        for group, info in config.GROUPS.items():
            rated = config.RATED["S" if group == "SERVICE" else "passenger"]["persons"]
            for car_id in info["cars"]:
                self.cars[car_id] = SimCar(self, car_id, group, info["floors"], rated)
        ctx.bus.subscribe(PassengerArrived, self.on_arrived)
        ctx.bus.subscribe(CallAssigned, self.on_assigned)
        ctx.bus.subscribe(CallReassigned, self.on_reassigned)
        ctx.bus.subscribe(CallCancelled, self.on_cancelled)
        ctx.bus.subscribe(FireAlarm, self.on_fire_alarm)
        ctx.bus.subscribe(FireAlarmCleared, self.on_fire_cleared)
        ctx.bus.subscribe(MainsLost, self.on_mains_lost)
        ctx.bus.subscribe(GeneratorReady, self.on_generator_ready)
        ctx.bus.subscribe(MainsRestored, self.on_mains_restored)
        ctx.clock.every(config.SIM["status_interval_s"], self._publish_status)

    # ---------- passengers ----------
    def on_arrived(self, ev: PassengerArrived) -> None:
        p = Passenger(ev.passenger_id, ev.origin, ev.destination, ev.group,
                      ev.accessible, ev.vip, ev.t)
        self.passengers[p.passenger_id] = p
        self.waiting[p.passenger_id] = p
        if self.fallback_active:
            self._fallback_assign(p)

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

    # ---------- built-in group logic (EMS server down) ----------
    def start_fallback(self) -> None:
        self.fallback_active = True
        self.fallback_periods.append([self.ctx.clock.now(), None])
        self._fallback_assign_waiting()

    def stop_fallback(self) -> None:
        self._fallback_assign_waiting()        # nobody who arrived in the gap is forgotten
        self.fallback_active = False
        if self.fallback_periods and self.fallback_periods[-1][1] is None:
            self.fallback_periods[-1][1] = self.ctx.clock.now()

    def _fallback_assign_waiting(self) -> None:
        for p in list(self.waiting.values()):
            if p.car_id is None:
                self._fallback_assign(p)

    def _fallback_assign(self, p: Passenger) -> None:
        """Nearest-car rule. The controller registers the stop itself (no EMS command)."""
        candidates = [c.status() for c in self.cars.values()
                      if c.group == p.group and c.operational()]
        if not candidates:
            return
        best = rank_cars(candidates, p.origin)[0]
        p.car_id = best.car_id
        self.cars[best.car_id].add_stop(p.origin, p.passenger_id)
        self.fallback_assignments.append((self.ctx.clock.now(), p.passenger_id, best.car_id))

    # ---------- building signals: how the controllers react ----------
    def on_fire_alarm(self, ev: FireAlarm) -> None:
        if self.fire_alarm_active:
            return
        self.fire_alarm_active = True
        self.fire_needs_reset = True
        for car in self.cars.values():
            if car.group == "SERVICE":
                car.firefighter = True
                self.ctx.bus.publish(FirefighterService(ev.t, car.car_id, True))
            else:
                car.recall()

    def on_fire_cleared(self, ev: FireAlarmCleared) -> None:
        self.fire_alarm_active = False

    def reset_fire(self) -> bool:
        """Manual reset at the controllers. Only works once the alarm has been cleared."""
        if self.fire_alarm_active or not self.fire_needs_reset:
            return False
        self.fire_needs_reset = False
        for car in self.cars.values():
            if car.group == "SERVICE":
                car.firefighter = False
                self.ctx.bus.publish(FirefighterService(self.ctx.clock.now(), car.car_id, False))
            else:
                car.end_fire()
        return True

    @property
    def power_ok(self) -> bool:
        return not self.mains_lost or self.generator_ready

    def on_mains_lost(self, ev: MainsLost) -> None:
        if self.mains_lost:
            return
        self.mains_lost = True
        self.generator_ready = False
        for car in self.cars.values():
            mode = ("failed" if car.car_id in self.rescue_failed
                    else "stuck" if car.car_id in self.rescue_stuck else "ok")
            car.power_lost(mode)

    def on_generator_ready(self, ev: GeneratorReady) -> None:
        was_ok = self.power_ok
        self.generator_ready = True
        self._power_changed(was_ok)

    def on_mains_restored(self, ev: MainsRestored) -> None:
        was_ok = self.power_ok
        self.mains_lost = False
        self.generator_ready = False
        self._power_changed(was_ok)

    def _power_changed(self, was_ok: bool) -> None:
        if self.power_ok and not was_ok:
            for car in self.cars.values():
                car.power_return()

    # ---------- telemetry ----------
    def _publish_status(self) -> None:
        for car in self.cars.values():
            if not car.link_lost:
                self.ctx.bus.publish(CarStatusUpdated(self.ctx.clock.now(), car.status()))

    # ---------- fault injection (used by faults.py, the harness and tests) ----------
    def inject_fault(self, car_id: CarId, code: str = "F-01", between_floors: bool = False,
                     doors_stuck: bool = False) -> None:
        self.cars[car_id].fault(code, between_floors, doors_stuck)

    def clear_fault(self, car_id: CarId) -> None:
        self.cars[car_id].clear_fault()

    def set_disconnected(self, car_id: CarId, on: bool) -> None:
        """Link loss: no status, commands refused, existing stops still served."""
        self.cars[car_id].link_lost = on

    def set_heartbeat_lost(self, car_id: CarId, on: bool) -> None:
        """Called by the EMS-side heartbeat monitor."""
        self.cars[car_id].heartbeat_lost = on

    def overload_burst(self, car_id: CarId) -> None:
        self.cars[car_id].phantom = True

    # ---------- ControllerAdapter ----------
    def _audit(self, car_id: CarId, command: str, args: tuple, accepted: bool) -> bool:
        self.command_log.append((self.ctx.clock.now(), car_id, command, args, accepted))
        return accepted

    def status(self, car_id: CarId) -> CarStatus:
        return self.cars[car_id].status()

    def all_status(self, group: Optional[GroupId] = None) -> list[CarStatus]:
        return [c.status() for c in self.cars.values() if group is None or c.group == group]

    def register_stop(self, car_id: CarId, floor: Floor, call_id: CallId) -> bool:
        car = self.cars.get(car_id)
        ok = (not self.ems_down and car is not None and car.accepts_calls()
              and floor in car.floors)
        if ok:
            car.add_stop(floor, call_id)
        return self._audit(car_id, "register_stop", (floor, call_id), ok)

    def cancel_stop(self, car_id: CarId, call_id: CallId) -> bool:
        car = self.cars.get(car_id)
        ok = (not self.ems_down and car is not None and car.reachable()
              and car.drop_stop(call_id))
        return self._audit(car_id, "cancel_stop", (call_id,), ok)

    def park(self, car_id: CarId, floor: Floor) -> bool:
        car = self.cars.get(car_id)
        ok = (not self.ems_down and car is not None and car.reachable()
              and not car.independent and floor in car.floors)
        if ok:
            car.park_floor = floor
            car.release_hold()
            car.wake()
        return self._audit(car_id, "park", (floor,), ok)

    def set_standby(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "set_standby", "standby", on)

    def set_independent_service(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "set_independent_service", "independent", on)

    def close_to_new_calls(self, car_id: CarId, on: bool) -> bool:
        return self._set_flag(car_id, "close_to_new_calls", "closed_to_new_calls", on)

    def express(self, car_id: CarId, floor: Floor, hold_doors_open: bool = False) -> bool:
        car = self.cars.get(car_id)
        ok = (not self.ems_down and car is not None and car.reachable()
              and not car.independent and floor in car.floors)
        if ok:
            car.release_hold()
            car.express_floor = floor
            car.hold_doors = hold_doors_open
            car.wake()
        return self._audit(car_id, "express", (floor, hold_doors_open), ok)

    def _set_flag(self, car_id: CarId, command: str, flag: str, on: bool) -> bool:
        car = self.cars.get(car_id)
        ok = not self.ems_down and car is not None and car.reachable()
        if ok:
            setattr(car, flag, on)
        return self._audit(car_id, command, (on,), ok)
