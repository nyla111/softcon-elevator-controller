"""Every event on the bus. Owned by: Tech lead. Add new events only with team approval.

Rule: each event has ONE publisher (the "pub" note). Anyone may subscribe.
All events carry t = simulated time when published.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .types import (Call, CallId, CallPriority, CarId, CarState, CarStatus,
                    CredentialId, Floor, GroupId, Mode)


@dataclass
class Event:
    t: float


# ---------- Panels and passengers (pub: Part 3 Access & panels) ----------
@dataclass
class CallRequested(Event):          # accepted, authorised request -> dispatcher
    call: Call

@dataclass
class CallReplaced(Event):           # "Other floor" within 5 s (FR-45); keeps created_at
    old_call_id: CallId
    new_call: Call

@dataclass
class CallCancelled(Event):          # explicit cancel, no-show, spam rule
    call_id: CallId
    reason: str                      # "user" | "no_show" | "duplicate" | "fire" ...

@dataclass
class AccessRefused(Event):          # FR-46; credential id goes to security log only
    panel_id: str
    credential_id: Optional[CredentialId]
    reason: str


# ---------- Dispatch (pub: Part 2 Dispatch & modes) ----------
@dataclass
class CallAssigned(Event):
    call_id: CallId
    car_id: CarId
    eta_s: float

@dataclass
class CallReassigned(Event):         # FR-04; original wait clock kept
    call_id: CallId
    from_car: CarId
    to_car: CarId
    reason: str

@dataclass
class ModeChanged(Event):
    group: Optional[GroupId]         # None = all groups
    old: Mode
    new: Mode
    cause: str                       # "schedule" | "manual" | "eco_step_up"

@dataclass
class WaitCeilingRisk(Event):        # FR-13: predicted wait >= 90% of ceiling
    call_id: CallId
    predicted_wait_s: float
    ceiling_s: float


# ---------- Car telemetry (pub: Part 1 Simulator / controller adapter) ----------
@dataclass
class CarStatusUpdated(Event):       # every 1 s per car
    status: CarStatus

@dataclass
class DoorsOpened(Event):            # ends the wait clock for calls at this floor
    car_id: CarId
    floor: Floor

@dataclass
class PassengerBoarded(Event):
    call_id: CallId
    car_id: CarId

@dataclass
class PassengerAlighted(Event):
    call_id: CallId
    car_id: CarId
    floor: Floor

@dataclass
class OverloadDetected(Event):
    car_id: CarId
    floor: Floor
    load_pct: float

@dataclass
class ControllerFault(Event):
    car_id: CarId
    code: str
    between_floors: bool
    doors_stuck: bool

@dataclass
class ControllerFaultCleared(Event):
    car_id: CarId

@dataclass
class HeartbeatLost(Event):          # 3 s without status (FR-104)
    car_id: CarId

@dataclass
class HeartbeatRestored(Event):
    car_id: CarId

@dataclass
class RescueReport(Event):           # FR-110
    car_id: CarId
    status: str                      # "rescued_at_floor" | "stuck"

@dataclass
class AlarmButtonPressed(Event):
    car_id: CarId


# ---------- Building signals (pub: Part 1 Simulator, fire panel / power room) ----------
@dataclass
class FireAlarm(Event):
    source: str = "panel"            # "panel" | "io_signal_lost" (NFR-SF3)

@dataclass
class FireAlarmCleared(Event):
    pass

@dataclass
class FirefighterService(Event):
    car_id: CarId
    active: bool

@dataclass
class MainsLost(Event):
    pass

@dataclass
class GeneratorReady(Event):         # generator running AND controller EP-ready
    pass

@dataclass
class MainsRestored(Event):
    pass


# ---------- Car states (pub: Part 4 Safety, the only writer of car state) ----------
@dataclass
class CarStateChanged(Event):
    car_id: CarId
    old: CarState
    new: CarState
    requested_by: str                # module or user that asked


# ---------- Priority services (pub: Part 5 Priority & service car) ----------
@dataclass
class PriorityTripStarted(Event):
    kind: str                        # "vip" | "medical" | "send_to_lobby"
    car_id: CarId
    origin: Floor
    destination: Floor

@dataclass
class PriorityTripEnded(Event):
    kind: str
    car_id: CarId
    cancelled: bool

@dataclass
class GoodsMoveScheduled(Event):
    move_id: str
    car_id: CarId
    start: float
    end: float
    credential_ids: list[CredentialId] = field(default_factory=list)


# ---------- Camera (pub: Part 1 Simulator camera device) ----------
@dataclass
class CameraEvent(Event):
    event_id: str
    car_id: CarId
    kind: str            # "door_held" | "bulky_item" | "repeated_overload" | "smoking"
                         # | "vandalism" | "lying_still" | "tailgating"
    people_count: int
    clip_ref: str        # reference only, no video in Release 1


# ---------- Outputs to people (pub: any module) ----------
@dataclass
class PanelMessage(Event):           # panel / car display + voice
    target: str                      # panel_id or "car:<id>"
    text_en: str
    text_vi: str
    voice: bool = True

@dataclass
class AlertRaised(Event):            # delivered by Part 1 notifier to test inbox
    severity: str                    # "critical" | "high" | "info"
    kind: str                        # "entrapment" | "fault" | "acs_down" | ...
    recipients: list[str]            # roles: "security", "fm", "contractor"
    message: str
    car_id: Optional[CarId] = None
    floor: Optional[Floor] = None


# ---------- Records (pub: Part 6 builds trips from the events above) ----------
@dataclass
class TripCompleted(Event):          # NO credential or person id (NFR-SE2)
    call_id: CallId
    car_id: CarId
    group: GroupId
    origin: Floor
    destination: Optional[Floor]
    priority: CallPriority
    mode: Mode
    created_at: float
    picked_up_at: float
    arrived_at: float
    degraded: bool
