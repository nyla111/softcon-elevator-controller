"""Safety supervision. Physical recall/rescue and command interlocks stay in controllers.

No ControllerAdapter command is sent here. Recovery attestations are internal integration
entry points, not authenticated UI endpoints (see docs/safety.md).
"""
from __future__ import annotations

from ems.contracts import config
from ems.contracts import events as ev
from ems.contracts.types import CarId, CarState, CarStatus, GroupId, Role

from .states import StateTable


class SafetyService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.table = StateTable(ctx)
        self.fire = False
        self.firefighters: set[CarId] = set()
        self.mains_lost = False
        self.generator_ready = False
        self.recovery_required = False
        self.signals_clear = False
        self.normal: set[CarId] = set()
        self.mains_since: float | None = None
        self.power_epoch = 0
        self.power_lost_at: float | None = None
        self.rescued: set[CarId] = set()
        self.rescue_alerted: set[CarId] = set()
        self.hazards: dict[CarId, tuple[bool, bool]] = {}
        self.fault_alerted: set[CarId] = set()
        self.recovery_log: list[tuple[float, str]] = []
        self._signal_times: dict[tuple[str, CarId | None], float] = {}
        # Scalar snapshots: bus payloads/adapter status objects may be mutable.
        self.telemetry: dict[CarId, tuple[float, int, float, int]] = {}

    def subscribe(self) -> None:
        for event, handler in {
            ev.FireAlarm: self.on_fire, ev.FireAlarmCleared: self.on_fire_cleared,
            ev.FirefighterService: self.on_firefighter,
            ev.MainsLost: self.on_mains_lost, ev.GeneratorReady: self.on_generator_ready,
            ev.MainsRestored: self.on_mains_restored, ev.RescueReport: self.on_rescue,
            ev.ControllerFault: self.on_fault, ev.ControllerFaultCleared: self.on_fault_cleared,
            ev.HeartbeatLost: self.on_link_lost, ev.HeartbeatRestored: self.on_link_restored,
            ev.CarStatusUpdated: self.on_status, ev.AlarmButtonPressed: self.on_alarm,
        }.items():
            self.ctx.bus.subscribe(event, handler)

    def state(self, car_id: CarId) -> CarState:
        return self.table.state(car_id)

    @staticmethod
    def _owner(by: str) -> bool:
        # Internal holds cannot be removed by a public request/release.
        return bool(by) and not by.startswith("safety:")

    def request(self, car_id: CarId, new: CarState, by: str) -> bool:
        current = self.state(car_id)
        if not isinstance(new, CarState) or not self._owner(by):
            return False
        if new > current or (self.safety_active() and new > CarState.EMERGENCY_POWER):
            return False
        if new != CarState.GROUP_SERVICE:
            self.table.set(car_id, new, by, True)
        if new <= CarState.EMERGENCY_POWER:
            self.recovery_required = True
            self.signals_clear = False
            self.normal.clear()
        return True

    def release(self, car_id: CarId, from_state: CarState, by: str) -> bool:
        self.table.check_car(car_id)
        if not self._owner(by):
            return False
        return self.table.set(car_id, from_state, by, False)

    def dispatchable(self, group: GroupId) -> list[CarId]:
        cars = self.table.cars(group)
        if self.safety_active():
            return []
        controllers = self.ctx.get("controllers")
        return [car for car in cars if self.state(car).dispatchable
                and self._healthy(controllers.status(car))]

    def safety_active(self) -> bool:
        return (self.fire or bool(self.firefighters) or self.mains_lost or self.recovery_required
                or any(s <= CarState.EMERGENCY_POWER
                       for holds in self.table.holds.values() for s, _ in holds))

    @staticmethod
    def _healthy(status: CarStatus) -> bool:
        return not status.disconnected and status.fault_code is None

    def _alert(self, severity: str, kind: str, message: str, car_id=None) -> None:
        floor = None
        if car_id is not None:
            sample = self.telemetry.get(car_id)
            if sample is None:
                context = "floor=unknown; load=unknown; passengers=unknown; telemetry_t=unknown"
            else:
                t, floor, load, passengers = sample
                context = (f"last reported floor={floor}; load={load:g}%; passengers={passengers}; "
                           f"telemetry_t={t:g}s; age={self.ctx.clock.now() - t:g}s")
            message = f"{message}; car={car_id}; {context}"
        self.ctx.bus.publish(ev.AlertRaised(self.ctx.clock.now(), severity, kind,
                                           ["security", "fm", "contractor"], message, car_id, floor))

    def _display(self, en: str, vi: str) -> None:
        for car in self.table.cars():
            self.ctx.bus.publish(ev.PanelMessage(self.ctx.clock.now(), f"car:{car}", en, vi))

    def _accept_signal(self, kind: str, event: ev.Event, car_id: CarId | None = None) -> bool:
        """Reject older assertions/clears and invalid timestamps within each physical signal.

        Equal timestamps retain bus order: the contracts have no sequence/episode ID with
        which to distinguish same-time transitions from replays.
        """
        key = (kind, car_id)
        if (not 0 <= event.t <= self.ctx.clock.now()
                or event.t < self._signal_times.get(key, -1)):
            return False
        self._signal_times[key] = event.t
        return True

    def _begin_emergency(self, state: CarState, owner: str) -> None:
        self.recovery_required = True
        self.signals_clear = False
        self.normal.clear()
        # Block every car before delivering any downstream reassignment request.
        for car in self.table.cars():
            self.table.set(car, state, owner, True)
            # An interrupted exclusive hold must never reappear after recovery.
            # Priority still owns trip/goods cancellation via the original building event.
            for held, by in list(self.table.holds.get(car, ())):
                if held is CarState.VIP_EXCLUSIVE:
                    self.table.set(car, held, by, False)
        # The owning dispatcher must retain waiting calls and exclude boarded riders.
        for car in self.table.cars():
            self.ctx.get("dispatcher").reassign_calls_of(car, owner)

    def on_fire(self, event: ev.FireAlarm) -> None:
        if not self._accept_signal("fire", event) or self.fire:
            return
        self.fire = True  # includes io_signal_lost; never command controller recall
        self._begin_emergency(CarState.FIRE_RECALL, "safety:fire")
        self._display("FIRE RECALL — use the stairs", "BÁO CHÁY — sử dụng cầu thang bộ")

    def on_fire_cleared(self, event: ev.FireAlarmCleared) -> None:
        if not self._accept_signal("fire", event):
            return
        self.fire = False  # holds remain until the full recovery gate succeeds

    def on_firefighter(self, event: ev.FirefighterService) -> None:
        self.table.check_car(event.car_id)
        if not self._accept_signal("firefighter", event, event.car_id):
            return
        if event.active:
            if event.car_id in self.firefighters:
                return
            self.firefighters.add(event.car_id)
            if not self.fire or not any((CarState.FIRE_RECALL, "safety:fire") in holds
                                       for holds in self.table.holds.values()):
                self._begin_emergency(CarState.FIRE_RECALL, "safety:fire")
        else:
            self.firefighters.discard(event.car_id)
        self.normal.discard(event.car_id)

    def on_mains_lost(self, event: ev.MainsLost) -> None:
        if not self._accept_signal("power", event) or self.mains_lost:
            return
        self.mains_lost = True
        self.mains_since = None
        self.generator_ready = False
        self.power_epoch += 1
        self.power_lost_at = self.ctx.clock.now()
        self.rescued.clear()
        self.rescue_alerted.clear()
        self._begin_emergency(CarState.EMERGENCY_POWER, "safety:power")
        epoch = self.power_epoch
        self.ctx.clock.call_later(config.RESCUE_REPORT_DEADLINE_S,
                                  lambda: self._rescue_deadline(epoch))
        self._display("POWER FAILURE — await assistance", "MẤT ĐIỆN — chờ hỗ trợ")

    def on_generator_ready(self, event: ev.GeneratorReady) -> None:
        if not self._accept_signal("power", event):
            return
        if self.mains_lost:
            self.generator_ready = True  # event already confirms generator AND controller readiness
        # No capacity / per-car EP authorization contract: fail closed on EMS movement.

    def on_mains_restored(self, event: ev.MainsRestored) -> None:
        if not self._accept_signal("power", event) or not self.mains_lost:
            return  # repeated notifications must not restart a valid stability interval
        self.mains_lost = False
        self.generator_ready = False
        self.mains_since = self.ctx.clock.now()
        self.normal.clear()
        self.signals_clear = False

    def on_rescue(self, event: ev.RescueReport) -> None:
        if (self.power_lost_at is None or event.car_id not in self.table.cars()
                or not self.power_lost_at <= event.t <= self.ctx.clock.now()):
            return
        if event.status == "rescued_at_floor":
            self.rescued.add(event.car_id)
        elif event.status == "stuck":
            self.rescued.discard(event.car_id)
            self._rescue_alert(event.car_id)

    def _rescue_alert(self, car: CarId) -> None:
        if car not in self.rescue_alerted:
            self.rescue_alerted.add(car)
            self._alert("critical", "entrapment", "POSSIBLE ENTRAPMENT — rescue not confirmed", car)

    def _rescue_deadline(self, epoch: int) -> None:
        # Restored power/readiness is not per-car rescue proof (FR-110). Only a
        # newer outage supersedes this timer; current reports decide which cars alert.
        if epoch != self.power_epoch:
            return
        for car in self.table.cars():
            if car not in self.rescued:
                self._rescue_alert(car)

    def report_safety_clear(self, clear: bool) -> None:
        """Trusted adapter acknowledgement of ALL safety inputs; no shared event exists yet."""
        self.signals_clear = clear

    def report_controller_normal(self, car_id: CarId, normal: bool) -> None:
        """Trusted adapter acknowledgement after physical locks/reset; telemetry alone is insufficient."""
        self.table.check_car(car_id)
        if normal and not self.fire and not self.mains_lost and car_id not in self.firefighters:
            self.normal.add(car_id)
        else:
            self.normal.discard(car_id)

    def _recovery_details(self) -> dict[str, object]:
        required = tuple(car for car in self.table.cars() if not self.table.isolated(car))
        missing = tuple(car for car in required if car not in self.normal)
        unhealthy = tuple(car for car in required
                          if not self._healthy(self.ctx.get("controllers").status(car)))
        external_holds = tuple(sorted(
            (car, state.name, owner)
            for car, holds in self.table.holds.items()
            for state, owner in holds
            if state <= CarState.EMERGENCY_POWER and not owner.startswith("safety:")
        ))
        mains_stable = (self.power_lost_at is None or
                        (self.mains_since is not None and
                         self.ctx.clock.now() - self.mains_since >= config.MAINS_STABLE_S))
        blockers = tuple(reason for reason, blocked in (
            ("recovery_not_required", not self.recovery_required),
            ("fire_active", self.fire),
            ("firefighter_service_active", bool(self.firefighters)),
            ("mains_lost", self.mains_lost),
            ("safety_inputs_not_clear", not self.signals_clear),
            ("external_emergency_holds", bool(external_holds)),
            ("mains_not_stable", not mains_stable),
            ("controller_reports_missing", bool(missing)),
            ("controllers_unhealthy", bool(unhealthy)),
        ) if blocked)
        return {
            "recovery_required": self.recovery_required,
            "fire_active": self.fire,
            "safety_inputs_clear": self.signals_clear,
            "required_controllers": required,
            "missing_controller_reports": missing,
            "unhealthy_controllers": unhealthy,
            "firefighter_cars": tuple(sorted(self.firefighters)),
            "mains_lost": self.mains_lost,
            "mains_stable": mains_stable,
            "external_emergency_holds": external_holds,
            "blockers": blockers,
            "eligible": not blockers,
        }

    def recovery_eligible(self) -> bool:
        return bool(self._recovery_details()["eligible"])

    def recovery_status(self) -> dict[str, object]:
        """Internal detached snapshot. Eligibility permits FM confirmation only.

        Tuple values never expose live collections. Blockers explain recovery eligibility,
        including missing reports when recovery is not required during normal service.
        Read under the same context lock as recovery actions when used by Monitor/Admin.
        """
        return self._recovery_details()

    def confirm_recovery(self, role: Role) -> bool:
        """Internal FM entry point. Caller must authenticate the role before invoking this."""
        if role is not Role.FM or not self.recovery_eligible():
            return False  # early confirmation is never saved for automatic recovery later
        self.recovery_required = False
        for car in self.table.cars():
            self.table.set(car, CarState.FIRE_RECALL, "safety:fire", False)
            self.table.set(car, CarState.EMERGENCY_POWER, "safety:power", False)
        self.recovery_log.append((self.ctx.clock.now(), "fm_confirmed"))
        self._display("Safety recovery confirmed", "Đã xác nhận khôi phục an toàn")
        return True

    def _fault_hold(self, car: CarId, owner: str, active: bool) -> None:
        changed = self.table.set(car, CarState.FAULTED, owner, active)
        self.normal.discard(car)
        if active and changed:
            self.ctx.get("dispatcher").reassign_calls_of(car, owner)
            # FR-30 mechanical fault notifications belong to Part 5 Maintenance.
            if owner == "safety:disconnected":
                self._alert("high", "disconnected", "Car controller heartbeat lost", car)

    def on_fault(self, event: ev.ControllerFault) -> None:
        if not self._accept_signal("fault", event, event.car_id):
            return
        between, stuck = self.hazards.get(event.car_id, (False, False))
        self.hazards[event.car_id] = (between or event.between_floors, stuck or event.doors_stuck)
        self._fault_hold(event.car_id, "safety:fault", True)
        self._check_entrapment(event.car_id, self.ctx.get("controllers").status(event.car_id))

    def on_fault_cleared(self, event: ev.ControllerFaultCleared) -> None:
        if not self._accept_signal("fault", event, event.car_id):
            return
        self._fault_hold(event.car_id, "safety:fault", False)
        self.hazards.pop(event.car_id, None)
        self.fault_alerted.discard(event.car_id)

    def on_link_lost(self, event: ev.HeartbeatLost) -> None:
        if not self._accept_signal("link", event, event.car_id):
            return
        self._fault_hold(event.car_id, "safety:disconnected", True)

    def on_link_restored(self, event: ev.HeartbeatRestored) -> None:
        if not self._accept_signal("link", event, event.car_id):
            return
        self._fault_hold(event.car_id, "safety:disconnected", False)

    def on_status(self, event: ev.CarStatusUpdated) -> None:
        s = event.status
        previous = self.telemetry.get(s.car_id)
        if (s.car_id in self.table.cars() and not s.disconnected
                and 0 <= event.t <= self.ctx.clock.now()
                and (previous is None or event.t >= previous[0])):
            self.telemetry[s.car_id] = (event.t, s.floor, s.load_pct, s.passengers)
        fault_current = self._accept_signal("fault", event, s.car_id) if s.fault_code else False
        if fault_current:
            self._fault_hold(s.car_id, "safety:fault", True)
        if s.disconnected and self._accept_signal("link", event, s.car_id):
            self._fault_hold(s.car_id, "safety:disconnected", True)
        # Clean telemetry never releases a fault or a disconnection condition.
        if s.car_id in self.hazards or fault_current:
            self._check_entrapment(s.car_id, s)

    def _check_entrapment(self, car: CarId, status: CarStatus) -> None:
        between, stuck = self.hazards.get(car, (False, False))
        between = between or (status.fault_code is not None and status.position != status.floor)
        if status.passengers > 0 and (between or stuck) and car not in self.fault_alerted:
            self.fault_alerted.add(car)
            self._alert("critical", "entrapment", "Passengers trapped — urgent contractor call-out", car)

    def on_alarm(self, event: ev.AlarmButtonPressed) -> None:
        self.table.check_car(event.car_id)
        self._alert("critical", "passenger_alarm", "Passenger requests urgent assistance", event.car_id)
