"""Part 5 Priority & service car: ctx.services["priority"] (PriorityService protocol).

The service owns every car Part 5 takes out of group service (a "hold"):

    kind            car state asked from Part 4   controller commands sent by Part 5
    vip             VIP_EXCLUSIVE                 close_to_new_calls, express          (vip.py)
    medical         VIP_EXCLUSIVE                 close_to_new_calls, express; doors
    send_to_lobby   VIP_EXCLUSIVE                 held on arrival                      (medical.py)
    maintenance     MAINTENANCE                   close_to_new_calls                   (maintenance.py)
    goods           INDEPENDENT_SERVICE           set_independent_service              (goods.py)

Rules (docs/CONTRACT.md): car state only through car_states.request / release, a refusal is
final for that car; every controller command may return False and is rolled back; no command
to a faulted / disconnected car, during fire or power loss, or while the EMS server is down.
Commands owed when a hold ends ("re-open", a VIP pick-up stop) are sent when the car can take
them, and a pick-up stop only while its call is still assigned to that car.
Events published: PriorityTripStarted / Ended, GoodsMoveScheduled / GoodsMoveEnded, AlertRaised,
PanelMessage. Credential ids stay in memory (authorisation, quotas), never in records or alerts.
"""
from __future__ import annotations

from typing import Optional

from ems.contracts.events import (AlertRaised, CallAssigned, CallCancelled, CallReassigned,
                                  CallRequested, CarStateChanged, ControllerFault,
                                  ControllerFaultCleared, DoorsOpened, EmsServerDown, EmsServerUp,
                                  FireAlarm, FirefighterService, GeneratorReady, HeartbeatLost,
                                  HeartbeatRestored, MainsLost, MainsRestored, PanelMessage,
                                  PassengerAlighted, PassengerBoarded, PriorityTripEnded,
                                  PriorityTripStarted)
from ems.contracts.types import (Call, CallId, CallPriority, CarId, CarState, CarStatus,
                                 CredentialId, Floor, GroupId, Role, floor_label)

from . import policy
from .auth import AccessAuthorizer, Actor, Authorizer
from .medical import MedicalMixin
from .model import Hold, Outcome, Trip, VipGrant
from .vip import VipMixin

__all__ = ["PriorityService", "Outcome", "subscribe"]


class PriorityService(VipMixin, MedicalMixin):
    def __init__(self, ctx, authorizer: Optional[Authorizer] = None) -> None:
        self.ctx = ctx
        self.authorizer: Authorizer = authorizer or AccessAuthorizer(ctx)
        self.holds: dict[CarId, Hold] = {}
        self.trips: dict[str, Trip] = {}               # active priority trips
        self.finished: list[Trip] = []
        self.vip_starts: list[float] = []              # successful exclusive VIP starts (FR-72)
        self.vip_grants: dict[CredentialId, VipGrant] = {}
        self.claims: dict[CallId, Optional[CarId]] = {}
        self.decisions: list[dict] = []                # VIP decisions (no credential ids)
        self.ceiling_checks: list[tuple] = []          # (t, car_id, would_breach)
        self.commands: list[tuple] = []                # (t, car_id, command, args, accepted)
        self.audit: list[dict] = []                    # API calls (roles only, no credential)
        self.reopen: dict[CarId, list[tuple]] = {}     # owed commands, retried after recovery
        self.after_release: dict[CarId, list[tuple]] = {}   # owed once the car's hold ends
        self.announced: set[tuple] = set()             # (call_id, car_id) voice feedback sent
        self._accessible: dict[CallId, Call] = {}
        self._calls: dict[CallId, Call] = {}           # calls seen on the bus, not yet boarded
        self.assigned: dict[CallId, CarId] = {}        # call -> car, assigned and not boarded
        self.riders: dict[CarId, dict[CallId, Floor]] = {}  # car -> riders and destinations
        self._seq = 0
        self.fire = False
        self.power_lost = False
        self.ems_down = False
        self.maintenance = None                        # set by register()
        self.goods = None

    # ================================================================ helpers
    def now(self) -> float:
        return self.ctx.clock.now()

    def next_id(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq:04d}"

    def use_authorizer(self, authorizer: Authorizer) -> None:
        """Replace how credentials are resolved (tests / scenario stand-ins only)."""
        self.authorizer = authorizer

    def authorize(self, credential_id: CredentialId, action: str,
                  *roles: Role) -> tuple[Optional[Actor], Optional[Outcome]]:
        actor = self.authorizer.actor(credential_id)
        if not actor.has(*roles):
            self.record(action, actor, False, "not_authorized")
            return None, Outcome(False, "not_authorized")
        return actor, None

    def record(self, action: str, actor: Optional[Actor], ok: bool, reason: str,
               ref: Optional[str] = None, car_id: Optional[CarId] = None) -> None:
        roles = sorted(r.value for r in actor.roles) if actor else []
        self.audit.append({"t": self.now(), "action": action, "roles": roles, "ok": ok,
                           "reason": reason, "ref": ref, "car_id": car_id})

    def building(self):
        return self.ctx.get("building")

    def group_of_car(self, car_id: CarId) -> Optional[GroupId]:
        return next((g.name for g in self.building().groups if car_id in g.cars), None)

    def status(self, car_id: CarId) -> CarStatus:
        return self.ctx.get("controllers").status(car_id)

    def healthy(self, status: CarStatus) -> bool:
        return status.fault_code is None and not status.disconnected

    def emergency(self) -> bool:
        return self.fire or self.power_lost or self.ctx.get("car_states").safety_active()

    def blocked_reason(self) -> Optional[str]:
        """Why no new priority trip / car removal may start now."""
        if self.emergency():
            return "emergency"
        return "ems_down" if self.ems_down else None

    def cmd(self, name: str, car_id: CarId, *args) -> bool:
        """Send one controller command, audit it, never raise."""
        try:
            ok = bool(getattr(self.ctx.get("controllers"), name)(car_id, *args))
        except (KeyError, ValueError):
            ok = False
        self.commands.append((self.now(), car_id, name, args, ok))
        return ok

    def commandable(self, car_id: CarId) -> bool:
        return (not self.emergency() and not self.ems_down
                and self.healthy(self.status(car_id)))

    def alert(self, severity: str, kind: str, recipients: list[str], message: str,
              car_id: Optional[CarId] = None, floor: Optional[Floor] = None) -> None:
        self.ctx.bus.publish(AlertRaised(self.now(), severity, kind, recipients, message,
                                         car_id, floor))

    # ================================================================ holds
    def take(self, car_id: CarId, state: CarState, kind: str, ref: str,
             command: Optional[tuple] = ("close_to_new_calls", True)) -> Optional[str]:
        """Ask Part 4 for `state`, then send `command`. None on success, else the reason; on
        failure nothing is left behind (state released, no hold recorded)."""
        if car_id in self.holds:
            return "car_held"
        states = self.ctx.get("car_states")
        if not states.request(car_id, state, f"priority:{kind}"):
            return "state_refused"
        if command is not None and not self.cmd(command[0], car_id, *command[1:]):
            states.release(car_id, state, f"priority:{kind}")
            return "controller_refused"
        self.holds[car_id] = Hold(car_id, state, kind, ref, self.now())
        return None

    def give_back(self, car_id: CarId,
                  undo: Optional[tuple] = ("close_to_new_calls", False)) -> None:
        """Leave our hold: release the state (Part 4 decides what the car becomes; a Safety
        state is never forced away), then send the owed commands now or after recovery."""
        hold = self.holds.pop(car_id, None)
        if hold is None:
            return
        self.ctx.get("car_states").release(car_id, hold.state, f"priority:{hold.kind}")
        owed = ([undo] if undo is not None else []) + self.after_release.pop(car_id, [])
        if owed:
            self.reopen.setdefault(car_id, []).extend(owed)
        self.retry_reopen()

    def _still_owed(self, car_id: CarId, command: tuple) -> bool:
        """A pick-up stop is owed only while its call is still assigned to this car (Part 2
        may have re-assigned it, or it was picked up / cancelled meanwhile)."""
        return command[0] != "register_stop" or self.assigned.get(command[2]) == car_id

    def _can_send(self, car_id: CarId, command: tuple) -> bool:
        if not self.commandable(car_id):
            return False
        if command[0] == "register_stop":       # never onto a car Safety (or anyone) holds
            return self.ctx.get("car_states").state(car_id) is CarState.GROUP_SERVICE
        return True

    def retry_reopen(self, *_ev) -> None:
        for car_id in list(self.reopen):
            if car_id in self.holds:
                continue
            left = []
            for command in self.reopen.pop(car_id):
                if not self._still_owed(car_id, command):
                    continue
                if not (self._can_send(car_id, command) and self.cmd(command[0], car_id,
                                                                      *command[1:])):
                    left.append(command)
            if left:
                self.reopen[car_id] = left

    def exclusive_in_group(self, group: GroupId) -> list[Trip]:
        return [t for t in self.trips.values() if t.group == group]

    def candidate_statuses(self, group: GroupId, floors: tuple[Floor, ...],
                           allow_vip_held: bool = False) -> list[CarStatus]:
        """Healthy cars of the group that serve `floors`, in group service and not held by
        Part 5 (optionally also cars held for a VIP trip, which medical may take)."""
        spec = self.building().group(group)
        if not all(f in spec.floors for f in floors):
            return []
        states = self.ctx.get("car_states")
        dispatchable = set(states.dispatchable(group))
        out = []
        for s in self.ctx.get("controllers").all_status(group):
            if s.car_id not in spec.cars or not self.healthy(s):
                continue
            hold = self.holds.get(s.car_id)
            if hold is not None:
                if allow_vip_held and hold.kind == "vip":
                    out.append(s)
                continue
            if states.state(s.car_id) is CarState.GROUP_SERVICE and s.car_id in dispatchable:
                out.append(s)
        return out

    # ================================================================ FR-76 / FR-08 for Part 2
    rank = staticmethod(policy.rank)
    order = staticmethod(policy.order)
    door_dwell_s = staticmethod(policy.door_dwell_s)

    def pending_calls_of(self, car_id: CarId) -> list[CallId]:
        """Calls assigned to the car (CallAssigned / CallReassigned) and not yet on board."""
        return [c for c, car in self.assigned.items() if car == car_id]

    def committed_places(self, car_id: CarId, floor: Floor) -> int:
        """Places taken when the car opens at `floor` (conservative): riders who do not leave
        there, plus every call assigned to the car (it may be picked up on the way) that does
        not end there."""
        staying = sum(1 for dest in self.riders.get(car_id, {}).values() if dest != floor)
        pending = sum(self._calls[c].party_size for c in self.pending_calls_of(car_id)
                      if c in self._calls and self._calls[c].destination != floor)
        return staying + pending

    def car_ok_for(self, call: Call, status: CarStatus) -> bool:
        """FR-08: room for the whole party when the car opens at the call's floor. If the
        controller counts more riders than Part 5 saw board, its count is used."""
        g = self.group_of_car(status.car_id)
        rated = self.building().group(g).rated_persons if g else 0
        extra = max(0, status.passengers - len(self.riders.get(status.car_id, {})))
        return rated - extra - self.committed_places(status.car_id, call.origin) \
            >= call.party_size

    # ================================================================ trips
    def _trip_started(self, trip: Trip) -> None:
        self.trips[trip.trip_id] = trip
        if trip.kind != "vip":          # VIP: asked (and checked) while choosing the car
            self.ctx.get("dispatcher").reassign_calls_of(trip.car_id, f"priority_{trip.kind}")
        self.ctx.bus.publish(PriorityTripStarted(self.now(), trip.kind, trip.car_id,
                                                 trip.origin, trip.destination))

    def end_trip(self, trip: Trip, cancelled: bool, reason: str, keep_hold: bool = False,
                 commands: bool = True) -> None:
        if self.trips.pop(trip.trip_id, None) is None:
            return                                                    # already ended
        trip.end_reason = reason
        self.finished.append(trip)
        self.ctx.bus.publish(PriorityTripEnded(self.now(), trip.kind, trip.car_id, cancelled))
        if trip.kind == "vip" and not trip.boarded and \
                self.assigned.get(trip.call_id) == trip.car_id:
            # The call was assigned to this car on Part 5's word and its pick-up was an express
            # target, not a registered stop: owe that stop once the car is back in service.
            self.after_release.setdefault(trip.car_id, []).append(
                ("register_stop", trip.origin, trip.call_id))
        hold = self.holds.get(trip.car_id)
        if hold is None and trip.car_id in self.after_release:
            self.reopen.setdefault(trip.car_id, []).extend(self.after_release.pop(trip.car_id))
            self.retry_reopen()
        if keep_hold or hold is None or hold.ref != trip.trip_id:
            return
        if trip.kind != "vip" and commands and self.commandable(trip.car_id):
            # doors were held open: one normal door cycle closes them
            self.cmd("express", trip.car_id, self.status(trip.car_id).floor, False)
        self.give_back(trip.car_id, ("close_to_new_calls", False))

    def trip_of_call(self, call_id: CallId) -> Optional[Trip]:
        return next((t for t in self.trips.values() if t.call_id == call_id), None)

    def trip_of_car(self, car_id: CarId) -> Optional[Trip]:
        return next((t for t in self.trips.values() if t.car_id == car_id), None)

    # ================================================================ bus: calls and riders
    def on_call_requested(self, ev: CallRequested) -> None:
        self._calls[ev.call.call_id] = ev.call
        if ev.call.priority is CallPriority.ACCESSIBLE:
            self._accessible[ev.call.call_id] = ev.call

    def on_assigned(self, ev: CallAssigned) -> None:
        self.assigned[ev.call_id] = ev.car_id
        call = self._accessible.get(ev.call_id)
        if call is not None:
            self._announce(call, ev.car_id)

    def on_reassigned(self, ev: CallReassigned) -> None:
        self.assigned[ev.call_id] = ev.to_car
        trip = self.trip_of_call(ev.call_id)
        if trip is not None and ev.to_car != trip.car_id and not trip.boarded:
            self.end_trip(trip, True, "reassigned")
        call = self._accessible.get(ev.call_id)
        if call is not None:
            self._announce(call, ev.to_car)

    def on_call_cancelled(self, ev: CallCancelled) -> None:
        self._accessible.pop(ev.call_id, None)
        self.assigned.pop(ev.call_id, None)
        self._calls.pop(ev.call_id, None)
        trip = self.trip_of_call(ev.call_id)
        if trip is not None and not trip.boarded:
            self.end_trip(trip, True, f"call_cancelled:{ev.reason}")

    def on_boarded(self, ev: PassengerBoarded) -> None:
        self.assigned.pop(ev.call_id, None)
        call = self._calls.pop(ev.call_id, None)
        if call is not None and call.destination is not None:
            self.riders.setdefault(ev.car_id, {})[ev.call_id] = call.destination
        trip = self.trip_of_call(ev.call_id)
        if trip is not None:
            self._vip_boarded(trip, ev)

    def on_alighted(self, ev: PassengerAlighted) -> None:
        self._accessible.pop(ev.call_id, None)
        self.riders.get(ev.car_id, {}).pop(ev.call_id, None)
        trip = self.trip_of_call(ev.call_id)
        if trip is not None:
            done = ev.floor == trip.destination
            self.end_trip(trip, not done, "delivered" if done else "left_early")

    def on_doors_opened(self, ev: DoorsOpened) -> None:
        trip = self.trip_of_car(ev.car_id)
        if trip is None:
            return
        if trip.phase == "to_origin" and ev.floor == trip.origin:
            trip.phase = "at_origin"
            if trip.kind != "vip":
                self._hold_doors(trip, ev.floor)
            elif not trip.boarded:
                self._vip_at_origin(trip)
        elif trip.phase == "to_destination" and ev.floor == trip.destination:
            trip.phase = "at_destination"
            if trip.kind != "vip":
                self._hold_doors(trip, ev.floor)

    def _announce(self, call: Call, car_id: CarId) -> None:
        """FR-08 voice feedback: one message per (call, car), duplicates ignored."""
        key = (call.call_id, car_id)
        if key in self.announced:
            return
        self.announced.add(key)
        dwell = int(policy.door_dwell_s(call))
        target = call.panel_id or f"floor:{floor_label(call.origin)}"
        self.ctx.bus.publish(PanelMessage(
            self.now(), target,
            f"Car {car_id} is coming for you. Its doors will stay open at least {dwell} seconds.",
            f"Thang {car_id} đang đến đón bạn. Cửa sẽ mở ít nhất {dwell} giây.", True))

    # ================================================================ faults, emergencies
    def on_car_lost(self, ev) -> None:
        """ControllerFault / HeartbeatLost: the car cannot run a priority trip any more."""
        trip = self.trip_of_car(ev.car_id)
        if trip is None:
            return
        self.end_trip(trip, True, "car_fault", commands=False)
        if trip.kind == "medical" and not trip.boarded:
            self._patient_lost(trip)
        elif trip.kind != "vip":
            self.alert("critical", f"{trip.kind}_car_lost", ["security"],
                       f"Car {trip.car_id} on a {trip.kind} trip was lost; check the occupants",
                       trip.car_id)

    def on_fire(self, ev: FireAlarm) -> None:
        self.fire = True
        self._emergency_stop("fire")

    def on_firefighter(self, ev: FirefighterService) -> None:
        if not ev.active:                             # published when the fire reset is done
            self.fire = False
            self.retry_reopen()

    def on_mains_lost(self, ev: MainsLost) -> None:
        self.power_lost = True
        self._emergency_stop("power")

    def on_mains_restored(self, ev: MainsRestored) -> None:
        self.power_lost = False
        self.retry_reopen()

    def _emergency_stop(self, why: str) -> None:
        """Fire recall / power loss: every priority trip and goods move ends now, states are
        released, NO controller command is sent (owed commands wait for recovery)."""
        for trip in list(self.trips.values()):
            self.end_trip(trip, True, f"emergency:{why}", commands=False)
        if self.goods is not None:
            self.goods.emergency(why)

    def on_ems_down(self, ev: EmsServerDown) -> None:
        self.ems_down = True

    def on_ems_up(self, ev: EmsServerUp) -> None:
        self.ems_down = False
        self.retry_reopen()


def subscribe(ctx, service: PriorityService) -> None:
    bus = ctx.bus
    for event, handler in [
            (CallRequested, service.on_call_requested), (CallAssigned, service.on_assigned),
            (CallReassigned, service.on_reassigned), (CallCancelled, service.on_call_cancelled),
            (PassengerBoarded, service.on_boarded), (PassengerAlighted, service.on_alighted),
            (DoorsOpened, service.on_doors_opened), (ControllerFault, service.on_car_lost),
            (HeartbeatLost, service.on_car_lost), (FireAlarm, service.on_fire),
            (FirefighterService, service.on_firefighter), (MainsLost, service.on_mains_lost),
            (MainsRestored, service.on_mains_restored), (EmsServerDown, service.on_ems_down),
            (EmsServerUp, service.on_ems_up)]:
        bus.subscribe(event, handler)
    for event in (ControllerFaultCleared, HeartbeatRestored, GeneratorReady, CarStateChanged):
        bus.subscribe(event, service.retry_reopen)
