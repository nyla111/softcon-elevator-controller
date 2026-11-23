"""Medical priority (FR-127) and send-to-lobby (FR-128). Mixed into PriorityService.

Both are security actions. Medical waits for existing riders' proven destination delivery
before reassigning an occupied car. The medical car then goes express and holds its doors
at the target. Security ends the trip with finish_trip().
"""
from __future__ import annotations

from ems.contracts.types import CarId, CredentialId, DoorState, Floor, GroupId, Role, floor_label

from . import policy
from .model import EXCLUSIVE, Outcome, PendingMedical, Trip


class MedicalMixin:
    def group_for(self, *floors: Floor) -> list[GroupId]:
        return [g.name for g in self.building().groups if all(f in g.floors for f in floors)]

    # ---------- FR-127 ----------
    def request_medical(self, credential_id: CredentialId, origin: Floor,
                        destination: Floor) -> Outcome:
        """Send the fastest healthy car to `origin` above any VIP trip; then
        medical_depart() and finish_trip()."""
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

    def _start_medical(self, origin: Floor, destination: Floor, *,
                       reserved_car=None, request_id=None) -> Outcome:
        why = self.blocked_reason()
        if why:
            return Outcome(False, why)
        groups = self.group_for(origin, destination)
        if origin == destination or not groups:
            return Outcome(False, "invalid_floors")
        statuses = [s for g in groups
                    for s in self.candidate_statuses(g, (origin, destination), True)]
        waiting_here = {s.car_id for s in statuses
                        if any(self._calls.get(c) is not None and self._calls[c].origin == origin
                               for c in self.pending_calls_of(s.car_id))}
        for s in policy.medical_candidates(statuses, origin, waiting_here):
            if reserved_car is not None and s.car_id != reserved_car:
                continue
            if any(p.car_id == s.car_id for p in self.pending_medical.values()):
                continue
            group = self.group_of_car(s.car_id)
            trip_id = request_id or self.next_id("med")
            if s.passengers > 0:
                riders = dict(self.riders.get(s.car_id, {}))
                if len(riders) < s.passengers:
                    continue  # Cannot invent destination delivery for unknown occupants.
                self.pending_medical[trip_id] = PendingMedical(
                    trip_id, s.car_id, origin, destination, riders,
                )
                self.alert("info", "medical_waiting_delivery", ["security"],
                           f"Medical request waits for existing riders in car {s.car_id} "
                           "to reach their destinations", s.car_id, origin)
                return Outcome(True, "awaiting_rider_delivery", trip_id, s.car_id)
            vip = self.trip_of_car(s.car_id)
            there = s.position == origin and s.doors is DoorState.OPEN
            if vip is not None:                        # the VIP car itself: hand the hold over
                if self.retake(s.car_id, "medical", trip_id,
                               ("express", origin, there)) is not None:
                    continue
                self.end_trip(vip, True, "medical_preempt", keep_hold=True)
            else:
                if self.take(s.car_id, EXCLUSIVE, "medical", trip_id) is not None:
                    continue
                if not self.cmd("express", s.car_id, origin, there):
                    self.give_back(s.car_id)
                    continue
            # Above VIP, and still one exclusive car per group (FR-72): VIP trips of every group
            # that serves this medical trip yield.
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

    def _medical_rider_alighted(self, event) -> None:
        for ref, pending in list(self.pending_medical.items()):
            if pending.car_id != event.car_id or event.call_id not in pending.remaining:
                continue
            destination = pending.remaining.pop(event.call_id)
            if event.floor != destination:
                self.pending_medical.pop(ref)
                self.alert("critical", "medical_delivery_unconfirmed", ["security"],
                           "Existing rider did not reach the recorded destination; medical "
                           "reassignment cancelled", event.car_id)
            elif not pending.remaining and self.status(event.car_id).passengers == 0:
                self.pending_medical.pop(ref)
                outcome = self._start_medical(pending.origin, pending.destination,
                                              reserved_car=pending.car_id, request_id=ref)
                if not outcome.ok:
                    self.alert("critical", "medical_no_car", ["security"],
                               f"Pending medical car could not start ({outcome.reason})",
                               pending.car_id, pending.origin)

    def _cancel_pending_medical(self, reason, car_id=None) -> None:
        for ref, pending in list(self.pending_medical.items()):
            if car_id is None or pending.car_id == car_id:
                self.pending_medical.pop(ref)
                self.alert("critical", "medical_request_cancelled", ["security"],
                           f"Pending medical request cancelled ({reason})", pending.car_id)

    def medical_depart(self, credential_id: CredentialId, trip_id: str) -> Outcome:
        """Security confirms the patient is in the car: express to the destination."""
        actor, err = self.authorize(credential_id, "medical.depart", Role.SECURITY)
        if err:
            return err
        trip = self.trips.get(trip_id)
        if trip_id in self.pending_medical:
            out = Outcome(False, "awaiting_rider_delivery")
        elif trip is None or trip.kind != "medical":
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

    # ---------- FR-128 ----------
    def send_to_lobby(self, credential_id: CredentialId, car_id: CarId) -> Outcome:
        """Security, after reviewing e.g. a camera alert: the car goes to the lobby and keeps
        its doors open. Not started by camera events themselves."""
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
            why = self.retake(car_id, "send_to_lobby", trip_id, ("express", lobby, there))
            if why is not None:
                return Outcome(False, why)
            self.end_trip(vip, True, "send_to_lobby", keep_hold=True)
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

    # ---------- both ----------
    def finish_trip(self, credential_id: CredentialId, trip_id: str) -> Outcome:
        """Security ends a medical or send-to-lobby trip (done, or cancelled before the
        destination). The doors close normally and the car returns to service."""
        actor, err = self.authorize(credential_id, "trip.finish", Role.SECURITY)
        if err:
            return err
        pending = self.pending_medical.pop(trip_id, None)
        if pending is not None:
            out = Outcome(True, "cancelled", trip_id, pending.car_id)
            self.record("trip.finish", actor, out.ok, out.reason, trip_id)
            return out
        trip = self.trips.get(trip_id)
        if trip is None or trip.kind == "vip":
            out = Outcome(False, "unknown_trip")
        else:
            done = trip.phase == "at_destination"
            self.end_trip(trip, not done, "completed" if done else "cancelled_by_security")
            out = Outcome(True, "ok" if done else "cancelled", trip_id, trip.car_id)
        self.record("trip.finish", actor, out.ok, out.reason, trip_id)
        return out

    def _hold_doors(self, trip: Trip, floor: Floor) -> None:
        """Keep the doors open at the floor the car has just opened at. Sent on arrival, not
        with the first express: a hold sent while the car is in a door cycle at another floor
        would keep the doors open there (simulated controller behaviour)."""
        if not self.cmd("express", trip.car_id, floor, True):
            self.alert("high", f"{trip.kind}_doors_not_held", ["security"],
                       f"Car {trip.car_id} could not hold its doors open at {floor_label(floor)}",
                       trip.car_id, floor)

    def _patient_lost(self, trip: Trip) -> None:
        """The medical car was lost before pick-up: send another car at once."""
        again = self._start_medical(trip.origin, trip.destination)
        note = f"re-sent car {again.car_id}" if again.ok else f"no car ({again.reason})"
        self.alert("critical", "medical_car_lost", ["security"],
                   f"Medical car {trip.car_id} lost before pick-up: {note}", trip.car_id,
                   trip.origin)
