"""Medical priority (FR-127) and send-to-lobby (FR-128). Mixed into PriorityService.

Both are security actions. The car goes express to the target floor and keeps its doors open
there (the hold is sent on arrival). Riders stay on board with their own destinations. Security
ends the trip with finish_trip().
"""
from __future__ import annotations

from ems.contracts.types import CarId, CredentialId, DoorState, Floor, GroupId, Role, floor_label

from . import policy
from .model import EXCLUSIVE, Hold, Outcome, Trip


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

    def _start_medical(self, origin: Floor, destination: Floor) -> Outcome:
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

    # ---------- both ----------
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
