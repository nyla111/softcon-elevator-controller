"""Part 5 Priority & service car: ctx.services["priority"].

One service object owns every car Part 5 has taken out of group service (a "hold"):

    kind            car state asked from Part 4      controller commands sent by Part 5
    vip             VIP_EXCLUSIVE                    close_to_new_calls, express
    medical         VIP_EXCLUSIVE (see note)         close_to_new_calls, express, then
    send_to_lobby   VIP_EXCLUSIVE (see note)         express(same floor, hold doors) on arrival
    maintenance     MAINTENANCE                      close_to_new_calls   (maintenance.py)
    goods           INDEPENDENT_SERVICE              set_independent_service (goods.py)

Note: the contract has no medical / priority-express car state; VIP_EXCLUSIVE is the only
"exclusive trip" state, so medical and send-to-lobby trips hold it too (Part 5 records the real
kind; contract change request CCR-03 asks for a dedicated state).

Rules followed (docs/CONTRACT.md):
* car state only through car_states.request / release; a refusal (False) is final for that car;
* every controller command may return False: the hold is rolled back, nothing is counted;
* no command is sent to a car that is faulted / disconnected, during fire or power loss, or while
  the EMS server is down. A "re-open" command that could not be sent is retried when the car or
  the building recovers (ControllerFaultCleared, HeartbeatRestored, FirefighterService off,
  GeneratorReady, MainsRestored, EmsServerUp);
* events published: PriorityTripStarted, PriorityTripEnded, GoodsMoveScheduled (Part 5 events),
  AlertRaised and PanelMessage (any module). Never CallAssigned / CallReassigned /
  CarStateChanged;
* credential ids are kept only in memory for authorisation and quotas, never published (except
  GoodsMoveScheduled.credential_ids, which the contract defines for Part 3).

VIP calls (FR-70/71/72) are not taken from the bus: Part 2 owns CallRequested handling and may
run before Part 5. Part 2 asks `claim_call(call)` before it assigns a VIP call; a car id back
means "assign this call to that car, Part 5 already sent it" (docs/priority/INTEGRATION.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (AlertRaised, CallAssigned, CallCancelled, CallReassigned,
                                  CallRequested, ControllerFault, ControllerFaultCleared,
                                  DoorsOpened, EmsServerDown, EmsServerUp, FireAlarm,
                                  FirefighterService, GeneratorReady, HeartbeatLost,
                                  HeartbeatRestored, MainsLost, MainsRestored, PanelMessage,
                                  PassengerAlighted, PassengerBoarded, PriorityTripEnded,
                                  PriorityTripStarted)
from ems.contracts.types import (Call, CallId, CallPriority, CarId, CarState, CarStatus,
                                 CredentialId, DoorState, Floor, GroupId, Role,
                                 floor_label)

from . import policy
from .auth import AccessAuthorizer, Actor, Authorizer

HOUR_S = 3600.0
EXCLUSIVE = CarState.VIP_EXCLUSIVE
EXCLUSIVE_KINDS = ("vip", "medical", "send_to_lobby")


@dataclass(frozen=True)
class Outcome:
    """Answer of every Part 5 API call. ok=False always carries a reason."""
    ok: bool
    reason: str = "ok"
    ref: Optional[str] = None          # trip / request / move id
    car_id: Optional[CarId] = None


@dataclass
class Hold:
    car_id: CarId
    state: CarState
    kind: str
    ref: str
    since: float


@dataclass
class Trip:
    trip_id: str
    kind: str                          # "vip" | "medical" | "send_to_lobby"
    car_id: CarId
    group: GroupId
    origin: Floor
    destination: Floor
    started_at: float
    call_id: Optional[CallId] = None   # VIP only
    phase: str = "to_origin"           # to_origin | at_origin | to_destination | at_destination
    boarded: bool = False              # VIP rider is in the car
    end_reason: Optional[str] = None


@dataclass
class VipGrant:
    """FR-70: an FM decision about one VIP credential (quota per day, or revoked)."""
    trips_per_day: Optional[int] = None
    revoked: bool = False
    used: dict = field(default_factory=dict)       # date iso -> exclusive trips started


class PriorityService:
    def __init__(self, ctx, authorizer: Optional[Authorizer] = None) -> None:
        self.ctx = ctx
        self.authorizer: Authorizer = authorizer or AccessAuthorizer(ctx)
        self.holds: dict[CarId, Hold] = {}
        self.trips: dict[str, Trip] = {}               # active trips
        self.finished: list[Trip] = []
        self.vip_starts: list[float] = []              # successful exclusive VIP starts (FR-72)
        self.vip_grants: dict[CredentialId, VipGrant] = {}
        self.claims: dict[CallId, Optional[CarId]] = {}
        self.decisions: list[dict] = []                # VIP decisions (no credential ids)
        self.ceiling_checks: list[tuple] = []          # (t, car_id, would_breach)
        self.commands: list[tuple] = []                # (t, car_id, command, args, accepted)
        self.audit: list[dict] = []                    # API calls (roles only, no credential)
        self.reopen: dict[CarId, list[tuple]] = {}     # commands to retry after recovery
        self.announced: set[tuple] = set()             # (call_id, car_id) voice feedback sent
        self.after_release: dict[CarId, list[tuple]] = {}   # commands owed once a hold ends
        self._accessible: dict[CallId, Call] = {}
        self._calls: dict[CallId, Call] = {}           # calls seen on the bus, not yet boarded
        self.assigned: dict[CallId, CarId] = {}        # call -> car, assigned and not boarded
        self.riders: dict[CarId, dict[CallId, Floor]] = {}  # car -> riders and destinations
        self._seq = 0
        self.fire = False
        self.power_lost = False
        self.ems_down = False
        # sub-services (set by register())
        self.maintenance = None
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
        for g in self.building().groups:
            if car_id in g.cars:
                return g.name
        return None

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
        if self.ems_down:
            return "ems_down"
        return None

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

    # ---------- holds: the only place that asks Part 4 for states ----------
    def take(self, car_id: CarId, state: CarState, kind: str, ref: str,
             command: Optional[tuple] = ("close_to_new_calls", True)) -> Optional[str]:
        """Ask Part 4 for `state`, then send `command`. Returns None on success, else the
        reason; on failure nothing is left behind (state released, no hold recorded)."""
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
        state is never forced away) and undo our controller flag, now or after recovery."""
        hold = self.holds.pop(car_id, None)
        if hold is None:
            return
        self.ctx.get("car_states").release(car_id, hold.state, f"priority:{hold.kind}")
        todo = ([undo] if undo is not None else []) + self.after_release.pop(car_id, [])
        sent = self.commandable(car_id)
        left = [u for u in todo if not (sent and self.cmd(u[0], car_id, *u[1:]))]
        if left:
            self.reopen.setdefault(car_id, []).extend(left)

    def retry_reopen(self, *_ev) -> None:
        for car_id in list(self.reopen):
            if car_id in self.holds or not self.commandable(car_id):
                continue
            pending = self.reopen.pop(car_id)
            left = [u for u in pending if not self.cmd(u[0], car_id, *u[1:])]
            if left:
                self.reopen[car_id] = left

    def exclusive_in_group(self, group: GroupId) -> list[Trip]:
        return [t for t in self.trips.values() if t.group == group]

    def candidate_statuses(self, group: GroupId, floors: tuple[Floor, ...],
                           allow_vip_held: bool = False) -> list[CarStatus]:
        """Healthy cars of the group that serve `floors`, in group service and not held by
        Part 5 (optionally: also cars held for a VIP trip, which medical may take)."""
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

    # ================================================================ FR-76 / FR-08 policy
    rank = staticmethod(policy.rank)
    order = staticmethod(policy.order)
    door_dwell_s = staticmethod(policy.door_dwell_s)

    def pending_calls_of(self, car_id: CarId) -> list[CallId]:
        """Calls assigned to the car (CallAssigned / CallReassigned) and not yet on board."""
        return [c for c, car in self.assigned.items() if car == car_id]

    def committed_places(self, car_id: CarId, floor: Floor) -> int:
        """Places taken when the car opens at `floor` (conservative, from bus events): riders
        who do not leave at that floor, plus every call already assigned to the car (at any
        floor: it may be picked up on the way) that does not end at that floor."""
        riders = self.riders.get(car_id, {})
        staying = sum(1 for dest in riders.values() if dest != floor)
        pending = sum(self._calls[c].party_size for c in self.pending_calls_of(car_id)
                      if c in self._calls and self._calls[c].destination != floor)
        return staying + pending

    def car_ok_for(self, call: Call, status: CarStatus) -> bool:
        """FR-08 free capacity, for Part 2's car filter: room for the whole party when the car
        opens at the call's floor (committed_places). Riders are tracked from PassengerBoarded /
        PassengerAlighted; if the controller reports more riders than Part 5 saw board (e.g.
        Part 5 registered late), the controller's count is used."""
        g = self.group_of_car(status.car_id)
        rated = self.building().group(g).rated_persons if g else 0
        seen = len(self.riders.get(status.car_id, {}))
        extra = max(0, status.passengers - seen)
        free = rated - extra - self.committed_places(status.car_id, call.origin)
        return free >= call.party_size

    # ================================================================ FR-70/71/72 VIP
    def set_vip_quota(self, credential_id: CredentialId, vip_credential: CredentialId,
                      trips_per_day: Optional[int]) -> Outcome:
        """FR-70 (FM): limit the exclusive VIP trips of one VIP credential per day
        (None = no Part 5 quota). The VIP role itself is granted in the ACS (Part 1/3)."""
        actor, err = self.authorize(credential_id, "vip.quota", Role.FM)
        if err:
            return err
        if trips_per_day is not None and trips_per_day < 0:
            self.record("vip.quota", actor, False, "invalid_quota")
            return Outcome(False, "invalid_quota")
        grant = self.vip_grants.setdefault(vip_credential, VipGrant())
        grant.trips_per_day, grant.revoked = trips_per_day, False
        self.record("vip.quota", actor, True, "ok")
        return Outcome(True)

    def revoke_vip(self, credential_id: CredentialId, vip_credential: CredentialId) -> Outcome:
        """FR-70 (FM): no more exclusive trips for this credential (served as ordinary)."""
        actor, err = self.authorize(credential_id, "vip.revoke", Role.FM)
        if err:
            return err
        self.vip_grants.setdefault(vip_credential, VipGrant()).revoked = True
        self.record("vip.revoke", actor, True, "ok")
        return Outcome(True)

    def vip_starts_in_last_hour(self, at: Optional[float] = None) -> int:
        """Exclusive starts in the rolling hour (at - 3600, at]."""
        at = self.now() if at is None else at
        return sum(1 for t in self.vip_starts if at - HOUR_S < t <= at)

    def claim_call(self, call: Call) -> Optional[CarId]:
        """Part 2 asks before assigning a call. Returns the car Part 5 sent for an exclusive VIP
        trip, or None (= Part 2 serves the call normally, still with its VIP rank). Idempotent:
        the same call id always gets the same answer."""
        if call.priority is not CallPriority.VIP:
            return None
        if call.call_id in self.claims:
            return self.claims[call.call_id]
        outcome = self._start_vip(call)
        car = outcome.car_id if outcome.ok else None
        self.claims[call.call_id] = car
        self.decisions.append({"t": self.now(), "call_id": call.call_id, "group": call.group,
                               "ok": outcome.ok, "reason": outcome.reason, "car_id": car})
        return car

    def _start_vip(self, call: Call) -> Outcome:
        why = self.blocked_reason()
        if why:
            return Outcome(False, why)
        if call.destination is None:
            return Outcome(False, "no_destination")
        grant = self.vip_grants.get(call.credential_id) if call.credential_id else None
        day = self.ctx.clock.now_dt().date().isoformat()
        if grant and grant.revoked:
            return Outcome(False, "vip_revoked")
        if grant and grant.trips_per_day is not None and \
                grant.used.get(day, 0) >= grant.trips_per_day:
            return Outcome(False, "vip_quota")
        if self.exclusive_in_group(call.group):
            return Outcome(False, "group_exclusive_cap")              # FR-72: 1 per group
        if self.vip_starts_in_last_hour() >= config.VIP_MAX_PER_HOUR:
            return Outcome(False, "hourly_cap")                       # FR-72: 6 per hour
        statuses = self.candidate_statuses(call.group, (call.origin, call.destination))
        if not statuses:
            return Outcome(False, "no_car")
        dispatcher = self.ctx.get("dispatcher")
        reasons = []
        busy = {s.car_id for s in statuses if self.pending_calls_of(s.car_id)}
        for s in policy.vip_candidates(statuses, call.origin, busy):
            breach = bool(dispatcher.would_breach_ceiling_without(s.car_id))
            self.ceiling_checks.append((self.now(), s.car_id, breach))
            if breach:                                                # FR-72 ceiling check
                reasons.append("ceiling")
                continue
            trip_id = self.next_id("vip")
            why = self.take(s.car_id, EXCLUSIVE, "vip", trip_id)
            if why is None and s.car_id in busy:
                # Exclusive means nobody else boards: Part 2 must move every call waiting for
                # this car (FR-04). Calls it did not move would board the VIP car, so no.
                moved = set(dispatcher.reassign_calls_of(s.car_id, "priority_vip") or [])
                if set(self.pending_calls_of(s.car_id)) - moved:
                    self.give_back(s.car_id)
                    why = "calls_not_moved"
            if why is None and not self.cmd("express", s.car_id, call.origin, False):
                self.give_back(s.car_id)
                why = "controller_refused"
            if why is not None:
                reasons.append(why)
                continue
            trip = Trip(trip_id, "vip", s.car_id, call.group, call.origin, call.destination,
                        self.now(), call_id=call.call_id)
            self.vip_starts.append(self.now())                       # counted only now
            if grant is not None:
                grant.used[day] = grant.used.get(day, 0) + 1
            self._trip_started(trip)
            return Outcome(True, "ok", trip_id, s.car_id)
        if reasons and len(set(reasons)) == 1 and reasons[0] in ("ceiling", "calls_not_moved"):
            return Outcome(False, reasons[0])
        return Outcome(False, "refused")

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
            # target, not a registered stop: register it once the car is back in group service.
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

    # ---------- VIP events ----------
    def on_boarded(self, ev: PassengerBoarded) -> None:
        self.assigned.pop(ev.call_id, None)
        call = self._calls.pop(ev.call_id, None)
        if call is not None and call.destination is not None:
            self.riders.setdefault(ev.car_id, {})[ev.call_id] = call.destination
        trip = self.trip_of_call(ev.call_id)
        if trip is None:
            return
        if ev.car_id != trip.car_id:
            self.end_trip(trip, True, "boarded_other_car")
            return
        trip.boarded = True
        trip.phase = "to_destination"
        if not self.cmd("express", trip.car_id, trip.destination, False):
            # the rider's own stop is still registered: delivered as an ordinary rider
            self.end_trip(trip, True, "controller_refused")

    def on_alighted(self, ev: PassengerAlighted) -> None:
        self._accessible.pop(ev.call_id, None)
        self.riders.get(ev.car_id, {}).pop(ev.call_id, None)
        trip = self.trip_of_call(ev.call_id)
        if trip is not None:
            self.end_trip(trip, ev.floor != trip.destination, "delivered"
                          if ev.floor == trip.destination else "left_early")

    def on_call_cancelled(self, ev: CallCancelled) -> None:
        self._accessible.pop(ev.call_id, None)
        self.assigned.pop(ev.call_id, None)
        self._calls.pop(ev.call_id, None)
        trip = self.trip_of_call(ev.call_id)
        if trip is not None and not trip.boarded:
            self.end_trip(trip, True, f"call_cancelled:{ev.reason}")

    def on_reassigned(self, ev: CallReassigned) -> None:
        self.assigned[ev.call_id] = ev.to_car
        trip = self.trip_of_call(ev.call_id)
        if trip is not None and ev.to_car != trip.car_id and not trip.boarded:
            self.end_trip(trip, True, "reassigned")
        call = self._accessible.get(ev.call_id)
        if call is not None:
            self._announce(call, ev.to_car)

    def on_doors_opened(self, ev: DoorsOpened) -> None:
        trip = self.trip_of_car(ev.car_id)
        if trip is None:
            return
        if trip.phase == "to_origin" and ev.floor == trip.origin:
            trip.phase = "at_origin"
            if trip.kind != "vip":
                self._hold_doors(trip, ev.floor)
            elif not trip.boarded:
                # FR-122-style: not on board after one full door cycle (car full, absent) -> the
                # exclusive hold ends; the call itself stays with Part 2
                cycle = (self.building().group(trip.group).rated_persons
                         * config.SIM["s_per_passenger"] + config.SIM["door_close_s"])
                trip_id = trip.trip_id
                self.ctx.clock.call_later(cycle, lambda: self._vip_not_boarded(trip_id))
        elif trip.phase == "to_destination" and ev.floor == trip.destination:
            trip.phase = "at_destination"
            if trip.kind != "vip":
                self._hold_doors(trip, ev.floor)

    def _hold_doors(self, trip: Trip, floor: Floor) -> None:
        """Keep the doors open at the floor the car has just opened at. Sent on arrival, not
        with the trip's first express: a hold sent while the car is still in a door cycle at
        another floor would keep the doors open THERE (simulated controller behaviour)."""
        if not self.cmd("express", trip.car_id, floor, True):
            self.alert("high", f"{trip.kind}_doors_not_held", ["security"],
                       f"Car {trip.car_id} could not hold its doors open at {floor_label(floor)}",
                       trip.car_id, floor)

    def _vip_not_boarded(self, trip_id: str) -> None:
        trip = self.trips.get(trip_id)                 # late callback after the end: ignored
        if trip is not None and not trip.boarded:
            self.end_trip(trip, True, "not_boarded_in_door_cycle")

    # ================================================================ FR-127 medical
    def group_for(self, *floors: Floor) -> list[GroupId]:
        return [g.name for g in self.building().groups if all(f in g.floors for f in floors)]

    def request_medical(self, credential_id: CredentialId, origin: Floor,
                        destination: Floor) -> Outcome:
        """FR-127 (security): send the fastest healthy car express to `origin`, doors held
        open, above any VIP trip. Then medical_depart() and finish_trip()."""
        actor, err = self.authorize(credential_id, "medical.request", Role.SECURITY)
        if err:
            return err
        out = self._start_medical(origin, destination)
        self.record("medical.request", actor, out.ok, out.reason, out.ref, out.car_id)
        if not out.ok:
            self.alert("critical", "medical_no_car", ["security"],
                       f"No car for the medical trip {floor_label(origin)} -> "
                       f"{floor_label(destination)} ({out.reason})", floor=origin)
        return out

    def _start_medical(self, origin: Floor, destination: Floor) -> Outcome:
        why = self.blocked_reason()
        if why:
            return Outcome(False, why)
        if origin == destination:
            return Outcome(False, "invalid_floors")
        groups = self.group_for(origin, destination)
        if not groups:
            return Outcome(False, "invalid_floors")
        statuses = [s for g in groups
                    for s in self.candidate_statuses(g, (origin, destination), True)]
        waiting_here = {s.car_id for s in statuses
                        if any(self._calls.get(c) is not None and self._calls[c].origin == origin
                               for c in self.pending_calls_of(s.car_id))}
        for s in policy.medical_candidates(statuses, origin, waiting_here):
            group = self.group_of_car(s.car_id)
            trip_id = self.next_id("med")
            vip = self.trip_of_car(s.car_id)
            there = s.position == origin and s.doors is DoorState.OPEN
            if vip is not None:                        # the VIP car itself: convert the hold
                if not self.cmd("express", s.car_id, origin, there):
                    continue
                self.end_trip(vip, True, "medical_preempt", keep_hold=True)
                self.holds[s.car_id] = Hold(s.car_id, EXCLUSIVE, "medical", trip_id, self.now())
            else:
                if self.take(s.car_id, EXCLUSIVE, "medical", trip_id) is not None:
                    continue
                if not self.cmd("express", s.car_id, origin, there):
                    self.give_back(s.car_id)
                    continue
            # FR-127 above VIP + FR-72 one exclusive car per group: VIP trips of every group that
            # serves this medical trip (and of the chosen car's group) yield.
            for g in set(groups) | {group}:
                for other in self.exclusive_in_group(g):
                    if other.kind == "vip":
                        self.end_trip(other, True, "medical_preempt")
            trip = Trip(trip_id, "medical", s.car_id, group, origin, destination, self.now())
            if there:
                trip.phase = "at_origin"               # already there, doors now held open
            self._trip_started(trip)
            return Outcome(True, "ok", trip_id, s.car_id)
        return Outcome(False, "no_eligible_car")

    def medical_depart(self, credential_id: CredentialId, trip_id: str) -> Outcome:
        """Security confirms the patient is in the car: express to the destination."""
        actor, err = self.authorize(credential_id, "medical.depart", Role.SECURITY)
        if err:
            return err
        trip = self.trips.get(trip_id)
        if trip is None or trip.kind != "medical":
            out = Outcome(False, "unknown_trip")
        elif trip.phase != "at_origin":
            out = Outcome(False, "not_at_origin")
        elif not self.commandable(trip.car_id) or not self.cmd(
                "express", trip.car_id, trip.destination, False):
            out = Outcome(False, "controller_refused")
        else:
            trip.boarded, trip.phase = True, "to_destination"
            out = Outcome(True, "ok", trip_id, trip.car_id)
        self.record("medical.depart", actor, out.ok, out.reason, trip_id)
        return out

    # ================================================================ FR-128 send to lobby
    def send_to_lobby(self, credential_id: CredentialId, car_id: CarId) -> Outcome:
        """FR-128 (security, after reviewing e.g. a camera 'lying_still' alert): the car goes
        express to the lobby and keeps its doors open there. Riders stay on board with their
        own destinations; finish_trip() returns the car to service."""
        actor, err = self.authorize(credential_id, "send_to_lobby", Role.SECURITY)
        if err:
            return err
        out = self._start_send_to_lobby(car_id)
        self.record("send_to_lobby", actor, out.ok, out.reason, out.ref, car_id)
        return out

    def _start_send_to_lobby(self, car_id: CarId) -> Outcome:
        why = self.blocked_reason()
        group = self.group_of_car(car_id)
        if why:
            return Outcome(False, why)
        if group is None:
            return Outcome(False, "unknown_car")
        lobby = self.building().lobby
        status = self.status(car_id)
        if not self.healthy(status):
            return Outcome(False, "car_unavailable")
        there = status.position == lobby and status.doors is DoorState.OPEN
        trip_id = self.next_id("stl")
        vip = self.trip_of_car(car_id)
        hold = self.holds.get(car_id)
        if vip is not None and vip.kind == "vip":
            if not self.cmd("express", car_id, lobby, there):
                return Outcome(False, "controller_refused")
            self.end_trip(vip, True, "send_to_lobby", keep_hold=True)
            self.holds[car_id] = Hold(car_id, EXCLUSIVE, "send_to_lobby", trip_id, self.now())
        elif hold is not None:
            return Outcome(False, f"car_held:{hold.kind}")
        else:
            why = self.take(car_id, EXCLUSIVE, "send_to_lobby", trip_id)
            if why is not None:
                return Outcome(False, why)
            if not self.cmd("express", car_id, lobby, there):
                self.give_back(car_id)
                return Outcome(False, "controller_refused")
        trip = Trip(trip_id, "send_to_lobby", car_id, group, status.floor, lobby, self.now(),
                    phase="at_destination" if there else "to_destination", boarded=True)
        self._trip_started(trip)
        return Outcome(True, "ok", trip_id, car_id)

    def finish_trip(self, credential_id: CredentialId, trip_id: str) -> Outcome:
        """Security ends a medical or send-to-lobby trip (done, or cancelled before the
        destination). The doors close normally and the car returns to service."""
        actor, err = self.authorize(credential_id, "trip.finish", Role.SECURITY)
        if err:
            return err
        trip = self.trips.get(trip_id)
        if trip is None or trip.kind == "vip":
            out = Outcome(False, "unknown_trip")
        else:
            done = trip.phase == "at_destination"
            self.end_trip(trip, not done, "completed" if done else "cancelled_by_security")
            out = Outcome(True, "ok" if done else "cancelled", trip_id, trip.car_id)
        self.record("trip.finish", actor, out.ok, out.reason, trip_id)
        return out

    # ================================================================ FR-08 voice feedback
    def on_call_requested(self, ev: CallRequested) -> None:
        self._calls[ev.call.call_id] = ev.call
        if ev.call.priority is CallPriority.ACCESSIBLE:
            self._accessible[ev.call.call_id] = ev.call

    def on_assigned(self, ev: CallAssigned) -> None:
        self.assigned[ev.call_id] = ev.car_id
        call = self._accessible.get(ev.call_id)
        if call is not None:
            self._announce(call, ev.car_id)

    def _announce(self, call: Call, car_id: CarId) -> None:
        key = (call.call_id, car_id)
        if key in self.announced:
            return                                     # duplicate CallAssigned: one message
        self.announced.add(key)
        dwell = int(policy.door_dwell_s(call))
        target = call.panel_id or f"floor:{floor_label(call.origin)}"
        self.ctx.bus.publish(PanelMessage(
            self.now(), target,
            f"Car {car_id} is coming for you. Its doors will stay open at least {dwell} seconds.",
            f"Thang {car_id} đang đến đón bạn. Cửa sẽ mở ít nhất {dwell} giây.", True))

    # ================================================================ faults and emergencies
    def on_car_lost(self, ev) -> None:
        """ControllerFault / HeartbeatLost: the car cannot run a priority trip any more."""
        trip = self.trip_of_car(ev.car_id)
        if trip is None:
            return
        self.end_trip(trip, True, "car_fault", commands=False)
        if trip.kind == "medical" and not trip.boarded:
            again = self._start_medical(trip.origin, trip.destination)
            note = (f"re-sent car {again.car_id}" if again.ok else f"no car ({again.reason})")
            self.alert("critical", "medical_car_lost", ["security"],
                       f"Medical car {trip.car_id} lost before pick-up: {note}",
                       trip.car_id, trip.origin)
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
        """Fire recall / power loss (Part 4 and the controllers take over): every priority trip
        and goods move ends now, states are released, NO controller command is sent."""
        for trip in list(self.trips.values()):
            self.end_trip(trip, True, f"emergency:{why}", commands=False)
        if self.goods is not None:
            self.goods.emergency(why)

    def on_ems_down(self, ev: EmsServerDown) -> None:
        self.ems_down = True

    def on_ems_up(self, ev: EmsServerUp) -> None:
        self.ems_down = False
        self.retry_reopen()

    # ================================================================ read-only view
    def snapshot(self) -> dict:
        return {
            "holds": {c: {"state": h.state.name, "kind": h.kind, "ref": h.ref, "since": h.since}
                      for c, h in self.holds.items()},
            "trips": [{"trip_id": t.trip_id, "kind": t.kind, "car_id": t.car_id,
                       "origin": t.origin, "destination": t.destination, "phase": t.phase}
                      for t in self.trips.values()],
            "vip_starts_last_hour": self.vip_starts_in_last_hour(),
            "emergency": self.emergency(), "ems_down": self.ems_down,
            "maintenance": self.maintenance.snapshot() if self.maintenance else None,
            "goods_moves": self.goods.snapshot() if self.goods else None,
        }


def subscribe(ctx, service: PriorityService) -> None:
    bus = ctx.bus
    bus.subscribe(CallRequested, service.on_call_requested)
    bus.subscribe(CallAssigned, service.on_assigned)
    bus.subscribe(CallReassigned, service.on_reassigned)
    bus.subscribe(CallCancelled, service.on_call_cancelled)
    bus.subscribe(PassengerBoarded, service.on_boarded)
    bus.subscribe(PassengerAlighted, service.on_alighted)
    bus.subscribe(DoorsOpened, service.on_doors_opened)
    bus.subscribe(ControllerFault, service.on_car_lost)
    bus.subscribe(HeartbeatLost, service.on_car_lost)
    bus.subscribe(ControllerFaultCleared, service.retry_reopen)
    bus.subscribe(HeartbeatRestored, service.retry_reopen)
    bus.subscribe(GeneratorReady, service.retry_reopen)
    bus.subscribe(FireAlarm, service.on_fire)
    bus.subscribe(FirefighterService, service.on_firefighter)
    bus.subscribe(MainsLost, service.on_mains_lost)
    bus.subscribe(MainsRestored, service.on_mains_restored)
    bus.subscribe(EmsServerDown, service.on_ems_down)
    bus.subscribe(EmsServerUp, service.on_ems_up)
