"""VIP exclusive trips (FR-70, FR-71, FR-72). Mixed into PriorityService.

Part 2 calls claim_call(call) for a VIP call before it chooses a car (PriorityService protocol,
contracts/interfaces.py). A car id back: Part 5 runs an exclusive trip on it and Part 2 publishes
CallAssigned without register_stop. None: Part 2 dispatches the call normally.
"""
from __future__ import annotations

from typing import Optional

from ems.contracts import config
from ems.contracts.events import PassengerBoarded
from ems.contracts.types import Call, CallPriority, CarId, CredentialId, Role

from . import policy
from .model import EXCLUSIVE, HOUR_S, Outcome, Trip, VipGrant


class VipMixin:
    # ---------- FR-70 grants ----------
    def set_vip_quota(self, credential_id: CredentialId, vip_credential: CredentialId,
                      trips_per_day: Optional[int]) -> Outcome:
        """FR-70 (FM): limit the exclusive trips of one VIP credential per day (None = no
        Part 5 quota). The VIP role itself comes from the ACS through Part 3."""
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

    # ---------- FR-71 / FR-72 ----------
    def vip_starts_in_last_hour(self, at: Optional[float] = None) -> int:
        """Exclusive starts in the rolling hour (at - 3600, at]."""
        at = self.now() if at is None else at
        return sum(1 for t in self.vip_starts if at - HOUR_S < t <= at)

    def claim_call(self, call: Call) -> Optional[CarId]:
        """Idempotent per call id: the same call always gets the same answer."""
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
                # this car (FR-04). A call it did not move would board the VIP car.
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

    # ---------- the trip ----------
    def _vip_boarded(self, trip: Trip, ev: PassengerBoarded) -> None:
        if ev.car_id != trip.car_id:
            self.end_trip(trip, True, "boarded_other_car")
            return
        trip.boarded = True
        trip.phase = "to_destination"
        if not self.cmd("express", trip.car_id, trip.destination, False):
            # the rider's own stop is still registered: delivered as an ordinary rider
            self.end_trip(trip, True, "controller_refused")

    def _vip_at_origin(self, trip: Trip) -> None:
        """FR-122-style: if the VIP is not on board after one full door cycle (car full or
        VIP absent) the exclusive hold ends; the pick-up stays on the same car."""
        cycle = (self.building().group(trip.group).rated_persons
                 * config.SIM["s_per_passenger"] + config.SIM["door_close_s"])
        trip_id = trip.trip_id
        self.ctx.clock.call_later(cycle, lambda: self._vip_not_boarded(trip_id))

    def _vip_not_boarded(self, trip_id: str) -> None:
        trip = self.trips.get(trip_id)                 # late callback after the end: ignored
        if trip is not None and not trip.boarded:
            self.end_trip(trip, True, "not_boarded_in_door_cycle")
