"""Part 6 domain service with strict separation of trip and security data."""
from __future__ import annotations

import math
from dataclasses import asdict
from datetime import timedelta
from typing import Any

from ems.contracts import config
from ems.contracts.events import (
    AlertRaised, CallAssigned, CallCancelled, CallReassigned, CallRequested, CameraEvent,
    CarStateChanged, CarStatusUpdated, ControllerFault, Event, ModeChanged, PanelMessage, PassengerAlighted,
    PassengerBoarded, PriorityTripEnded, PriorityTripStarted, TripCompleted,
)
from ems.contracts.types import CallPriority, Mode, Role

ENERGY_KWH_PER_RUN_HOUR = 12.0
DAY = 86400.0
RETENTION = {"security": 90 * DAY, "camera_unconfirmed": 30 * DAY,
             "camera_confirmed": 365 * DAY, "trip": 3 * 365 * DAY,
             "audit": 3 * 365 * DAY, "fault": 3 * 365 * DAY}
CAMERA_KINDS = frozenset({"door_held", "bulky_item", "repeated_overload", "smoking",
                          "vandalism", "lying_still", "tailgating"})
SAFETY_CAMERA_KINDS = frozenset({"lying_still", "smoking"})


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[max(0, math.ceil(percentile * len(ordered)) - 1)]


class MonitorService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.calls: dict[str, dict[str, Any]] = {}
        self.trips: list[dict[str, Any]] = []
        self.incidents: dict[str, dict[str, Any]] = {}
        self.security_log: list[dict[str, Any]] = []
        self.audit_log: list[dict[str, Any]] = []
        self.fault_history: list[dict[str, Any]] = []
        self.mode_history: list[dict[str, Any]] = []
        self.run_slices: list[dict[str, Any]] = []
        self._last_status_t: dict[str, float] = {}
        self._bulky_voice_events: set[str] = set()
        building = ctx.services.get("building")
        self.configuration: dict[str, Any] = {
            "building": building.as_dict() if building else {}, "public_floors": [config.LOBBY],
            "panels": [], "tenants": {},
            "house_rule_limits": {"item_kg": 25, "item_length_m": 1.5,
                                  "cart_width_m": 0.6},
            "confirmed_clip_days": 365,
        }
        ctx.clock.every(DAY, self.purge_expired)

    def _dt_at(self, t: float):
        return self.ctx.clock.now_dt() + timedelta(seconds=t - self.ctx.clock.now())

    def on_event(self, event: Event) -> None:
        if isinstance(event, CallRequested):
            call = event.call
            try:
                mode = self.ctx.get("dispatcher").current_mode(call.group)
            except Exception:
                mode = Mode.BALANCED
            self.calls[call.call_id] = {"call": call, "assigned_car": None,
                                        "picked_up_at": None, "mode": mode,
                                        "degraded": False}
        elif isinstance(event, CallAssigned) and event.call_id in self.calls:
            self.calls[event.call_id]["assigned_car"] = event.car_id
        elif isinstance(event, CallReassigned) and event.call_id in self.calls:
            self.calls[event.call_id]["assigned_car"] = event.to_car
            self.calls[event.call_id]["degraded"] = True
        elif isinstance(event, PassengerBoarded) and event.call_id in self.calls:
            self.calls[event.call_id]["picked_up_at"] = event.t
            self.calls[event.call_id]["assigned_car"] = event.car_id
        elif isinstance(event, PassengerAlighted):
            self._complete_trip(event)
        elif isinstance(event, CallCancelled):
            self.calls.pop(event.call_id, None)
        elif isinstance(event, ModeChanged):
            self.mode_history.append({"t": event.t, "group": event.group,
                                      "old": event.old.value, "new": event.new.value,
                                      "cause": event.cause})
        elif isinstance(event, CameraEvent):
            self._camera_event(event)
        elif isinstance(event, CarStatusUpdated):
            previous = self._last_status_t.get(event.status.car_id)
            self._last_status_t[event.status.car_id] = event.t
            if previous is not None and event.status.direction.value != "idle":
                try:
                    mode = self.ctx.get("dispatcher").current_mode(event.status.group).value
                except Exception:
                    mode = Mode.BALANCED.value
                self.run_slices.append({"t": event.t, "car_id": event.status.car_id,
                                        "seconds": event.t - previous, "mode": mode})
        elif isinstance(event, ControllerFault):
            self.fault_history.append({"t": event.t, "car_id": event.car_id,
                                       "code": event.code,
                                       "between_floors": event.between_floors})
        elif isinstance(event, (CarStateChanged, PriorityTripStarted, PriorityTripEnded)):
            self.security_log.append({"t": event.t, "kind": type(event).__name__,
                                      "detail": asdict(event)})

    def _complete_trip(self, event: PassengerAlighted) -> None:
        state = self.calls.pop(event.call_id, None)
        if not state or state["picked_up_at"] is None:
            return
        call = state["call"]
        trip = TripCompleted(event.t, call.call_id, event.car_id, call.group, call.origin,
                             event.floor, call.priority, state["mode"], call.created_at,
                             state["picked_up_at"], event.t, state["degraded"])
        # An allow-list ensures credential/person identifiers never enter trip/report data.
        row = {"record_type": "trip", "t": trip.t, "call_id": trip.call_id,
               "car_id": trip.car_id, "group": trip.group, "origin": trip.origin,
               "destination": trip.destination, "priority": trip.priority.name,
               "mode": trip.mode.value, "created_at": trip.created_at,
               "picked_up_at": trip.picked_up_at, "arrived_at": trip.arrived_at,
               "wait_s": trip.picked_up_at - trip.created_at,
               "ride_s": trip.arrived_at - trip.picked_up_at,
               "degraded": trip.degraded}
        self.trips.append(row)
        self.ctx.get("db").write(row)
        self.ctx.bus.publish(trip)

    def _candidate_credentials(self, event: CameraEvent) -> list[str]:
        candidates = []
        for state in self.calls.values():
            call = state["call"]
            if (state["assigned_car"] == event.car_id and state["picked_up_at"] is not None
                    and call.credential_id):
                candidates.append(call.credential_id)
        return sorted(set(candidates))

    def _camera_event(self, event: CameraEvent) -> None:
        if event.kind not in CAMERA_KINDS:
            return
        safety = event.kind in SAFETY_CAMERA_KINDS
        row = {"event_id": event.event_id, "t": event.t, "kind": event.kind,
               "car_id": event.car_id, "people_count": event.people_count,
               "clip_ref": event.clip_ref,
               "candidate_credentials": self._candidate_credentials(event),
               "status": "safety_alert" if safety else "pending", "reviewer": None,
               "reviewed_at": None, "evidence": None, "responsible_credential": None}
        self.incidents[event.event_id] = row
        self.security_log.append({"t": event.t, "kind": "camera_event",
                                  "event_id": event.event_id, "car_id": event.car_id})
        if safety:
            try:
                floor = self.ctx.get("controllers").status(event.car_id).floor
            except Exception:
                floor = None
            self.ctx.bus.publish(AlertRaised(event.t, "critical", "camera_safety",
                                             ["security"],
                                             f"Camera safety event {event.kind} in car {event.car_id}",
                                             event.car_id, floor))
        if event.kind == "bulky_item" and event.event_id not in self._bulky_voice_events:
            self._bulky_voice_events.add(event.event_id)
            self.ctx.bus.publish(PanelMessage(event.t, f"car:{event.car_id}",
                "Heavy items must use the service car",
                "Hàng nặng phải sử dụng thang máy dịch vụ", True))

    def review_incident(self, event_id: str, decision: str, reviewer: str,
                        evidence: str, responsible_credential: str | None = None) -> dict:
        if decision not in {"confirmed", "dismissed"}:
            raise ValueError("decision must be confirmed or dismissed")
        row = self.incidents[event_id]
        if row["status"] not in {"pending", "safety_alert"}:
            raise ValueError("incident has already been reviewed")
        if responsible_credential and responsible_credential not in row["candidate_credentials"]:
            raise ValueError("responsible credential must be a candidate from the same trip")
        row.update(status=decision, reviewer=reviewer, reviewed_at=self.ctx.clock.now(),
                   evidence=evidence, responsible_credential=responsible_credential)
        self.security_log.append({"t": self.ctx.clock.now(), "kind": "camera_review",
                                  "event_id": event_id, "decision": decision,
                                  "reviewer": reviewer,
                                  "attributed": responsible_credential is not None})
        self.audit(reviewer, Role.SECURITY, "camera.review", event_id, "pending", decision)
        return dict(row)

    def audit(self, user: str, role: Role, action: str, target: str,
              old: Any, new: Any) -> None:
        self.audit_log.append({"t": self.ctx.clock.now(), "user": user, "role": role.value,
                               "action": action, "target": target, "old": old, "new": new})

    def update_configuration(self, key: str, value: Any, user: str, role: Role) -> dict:
        if key not in {"public_floors", "panels", "tenants", "house_rule_limits",
                       "confirmed_clip_days"}:
            raise ValueError("configuration key is not editable")
        if key == "confirmed_clip_days" and not (1 <= int(value) <= 365):
            raise ValueError("confirmed clip retention must be 1..365 days")
        old = self.configuration.get(key)
        self.configuration[key] = value
        self.audit(user, role, "configuration.update", key, old, value)
        return {"key": key, "old": old, "new": value}

    def purge_expired(self) -> dict[str, int]:
        now = self.ctx.clock.now()
        before = {"trip": len(self.trips), "security": len(self.security_log),
                  "audit": len(self.audit_log), "fault": len(self.fault_history),
                  "camera": len(self.incidents)}
        self.trips[:] = [x for x in self.trips if now - x["t"] < RETENTION["trip"]]
        self.security_log[:] = [x for x in self.security_log
                                if now - x["t"] < RETENTION["security"]]
        self.audit_log[:] = [x for x in self.audit_log if now - x["t"] < RETENTION["audit"]]
        self.fault_history[:] = [x for x in self.fault_history
                                 if now - x["t"] < RETENTION["fault"]]
        confirmed_days = min(365, int(self.configuration["confirmed_clip_days"]))
        for event_id, row in list(self.incidents.items()):
            limit = confirmed_days * DAY if row["status"] == "confirmed" else 30 * DAY
            if now - row["t"] >= limit:
                del self.incidents[event_id]
        after = {"trip": len(self.trips), "security": len(self.security_log),
                 "audit": len(self.audit_log), "fault": len(self.fault_history),
                 "camera": len(self.incidents)}
        return {k: before[k] - after[k] for k in before}

    def _kpis_for(self, rows: list[dict[str, Any]], slices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        waits, rides = [r["wait_s"] for r in rows], [r["ride_s"] for r in rows]
        slices = self.run_slices if slices is None else slices
        run_by_car: dict[str, float] = {}
        for item in slices:
            run_by_car[item["car_id"]] = run_by_car.get(item["car_id"], 0.0) + item["seconds"]
        total_run = sum(run_by_car.values())
        return {"trips": len(rows),
                "average_wait_s": sum(waits) / len(waits) if waits else 0.0,
                "p95_wait_s": _nearest_rank(waits, .95),
                "average_ride_s": sum(rides) / len(rides) if rides else 0.0,
                "run_time_s": total_run, "run_time_by_car_s": run_by_car,
                "estimated_energy_kwh": total_run / 3600 * ENERGY_KWH_PER_RUN_HOUR,
                "energy_model": f"{ENERGY_KWH_PER_RUN_HOUR:g} kWh per car run-hour",
                "ceiling_breaches": sum(1 for r in rows
                    if r["wait_s"] > config.MODE_TARGETS[Mode(r["mode"])][1]),
                "vip_trips": sum(1 for r in rows if r["priority"] == CallPriority.VIP.name),
                "degraded_trips": sum(1 for r in rows if r["degraded"])}

    def kpis(self, *, mode: str | None = None) -> dict[str, Any]:
        rows = [r for r in self.trips if mode is None or r["mode"] == mode]
        slices = [s for s in self.run_slices if mode is None or s["mode"] == mode]
        return self._kpis_for(rows, slices)

    def period_report(self, period: str = "month", mode: str | None = None) -> dict[str, Any]:
        if period not in {"hour", "day", "month"}:
            raise ValueError("period must be hour, day or month")
        now = self.ctx.clock.now_dt()
        def same_period(t: float) -> bool:
            dt = self._dt_at(t)
            if period == "hour":
                return (dt.year, dt.month, dt.day, dt.hour) == (now.year, now.month, now.day, now.hour)
            if period == "day":
                return dt.date() == now.date()
            return (dt.year, dt.month) == (now.year, now.month)
        rows = [r for r in self.trips if same_period(r["t"])
                and (mode is None or r["mode"] == mode)]
        slices = [s for s in self.run_slices if same_period(s["t"])
                  and (mode is None or s["mode"] == mode)]
        return {"period": period, "mode": mode, "wait_and_run_time": self._kpis_for(rows, slices)}

    def monthly_report(self) -> dict[str, Any]:
        current = self.ctx.clock.now_dt()
        previous = current.replace(day=1) - timedelta(days=1)
        this_rows, previous_rows = [], []
        for row in self.trips:
            dt = self._dt_at(row["t"])
            if (dt.year, dt.month) == (current.year, current.month):
                this_rows.append(row)
            elif (dt.year, dt.month) == (previous.year, previous.month):
                previous_rows.append(row)
        return {"period": current.strftime("%Y-%m"),
                "wait_and_run_time": self._kpis_for(this_rows,
                    [s for s in self.run_slices if (self._dt_at(s["t"]).year,
                     self._dt_at(s["t"]).month) == (current.year, current.month)]),
                "previous_month": self._kpis_for(previous_rows,
                    [s for s in self.run_slices if (self._dt_at(s["t"]).year,
                     self._dt_at(s["t"]).month) == (previous.year, previous.month)]),
                "targets": {m.value: {"mean_wait_s": v[0], "ceiling_s": v[1]}
                            for m, v in config.MODE_TARGETS.items()}}
