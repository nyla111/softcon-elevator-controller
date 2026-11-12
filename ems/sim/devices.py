"""Mock devices around the elevators: ACS, notifier, fire panel, power room, camera.

Assumptions:
* MockAcs answers from the Population (population.py). A lookup is a plain function call, so the
  0.1 s reply time (config.SIM["acs_reply_s"]) is not waited for: every lookup is written to
  lookup_log as (t, credential_id, latency_s, outcome) so a test can check it. When the ACS is
  down, lookup raises TimeoutError and the logged latency is config.ACS_TIMEOUT_S.
* Visitor passes are issued by the host tenant's portal, so issue_visitor_pass also works while
  the ACS is down. A pass is valid from valid_from (inclusive) to valid_to (exclusive).
  Visitor permission = the pass floors + the public floors, role VISITOR.
* MockNotifier delivers every AlertRaised into its inbox, one entry per alert.
* FirePanel / PowerRoom / Camera only publish the building events; how the cars react is in
  cars.py (the controllers) so the same events work for any scenario.
* The helpers are reachable as ctx.services["devices"] (.fire, .power, .camera).
"""
from __future__ import annotations

from ems.contracts import config
from ems.contracts.events import (AlarmButtonPressed, AlertRaised, CameraEvent, FireAlarm,
                                  FireAlarmCleared, GeneratorReady, MainsLost, MainsRestored)
from ems.contracts.types import CredentialId, Permission, Role

from .population import PUBLIC_FLOORS, Population


class MockAcs:
    def __init__(self, ctx, population: Population) -> None:
        self.ctx = ctx
        self.population = population
        self.down = False
        self.revoked: set[CredentialId] = set()
        self.passes: dict[CredentialId, dict] = {}
        self.lookup_log: list[tuple] = []        # (t, credential_id, latency_s, outcome)

    def set_down(self, down: bool) -> None:
        self.down = down

    def revoke(self, credential_id: CredentialId) -> None:
        self.revoked.add(credential_id)

    def issue_visitor_pass(self, tenant_id: str, floors: set[int], valid_from: float,
                           valid_to: float, group_size: int = 1) -> CredentialId:
        pass_id = f"QR-{tenant_id}-{len(self.passes) + 1:06d}"
        self.passes[pass_id] = {"tenant_id": tenant_id, "floors": frozenset(floors),
                                "valid_from": valid_from, "valid_to": valid_to,
                                "group_size": group_size}
        return pass_id

    def lookup(self, credential_id: CredentialId) -> Permission:
        now = self.ctx.clock.now()
        if self.down:
            self.lookup_log.append((now, credential_id, config.ACS_TIMEOUT_S, "timeout"))
            raise TimeoutError("ACS is down")
        permission = self._answer(credential_id, now)
        self.lookup_log.append((now, credential_id, config.SIM["acs_reply_s"],
                                "ok" if permission.valid else "invalid"))
        return permission

    def _answer(self, credential_id: CredentialId, now: float) -> Permission:
        invalid = Permission(credential_id, frozenset(), None, frozenset(), False)
        if credential_id in self.revoked:
            return invalid
        employee = self.population.lookup(credential_id)
        if employee is not None:
            roles = {Role.EMPLOYEE}
            if employee.accessible:
                roles.add(Role.ACCESSIBLE)
            if employee.vip:
                roles.add(Role.VIP)
            return Permission(credential_id, employee.allowed_floors, employee.default_floor,
                              frozenset(roles), True, employee.tenant_id)
        visitor = self.passes.get(credential_id)
        if visitor is None or not visitor["valid_from"] <= now < visitor["valid_to"]:
            return invalid
        return Permission(credential_id, visitor["floors"] | PUBLIC_FLOORS,
                          min(visitor["floors"]), frozenset({Role.VISITOR}), True,
                          visitor["tenant_id"])


class MockNotifier:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.inbox: list[tuple[float, list[str], str, str]] = []   # (t, recipients, subject, body)
        ctx.bus.subscribe(AlertRaised, self.on_alert)

    def send(self, recipients: list[str], subject: str, body: str) -> None:
        self.inbox.append((self.ctx.clock.now(), list(recipients), subject, body))

    def on_alert(self, ev: AlertRaised) -> None:
        where = "" if ev.car_id is None else f" car {ev.car_id}"
        self.send(ev.recipients, f"[{ev.severity}] {ev.kind}{where}", ev.message)


class FirePanel:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def alarm(self, source: str = "panel") -> None:
        self.ctx.bus.publish(FireAlarm(self.ctx.clock.now(), source))

    def clear(self) -> None:
        self.ctx.bus.publish(FireAlarmCleared(self.ctx.clock.now()))


class PowerRoom:
    def __init__(self, ctx, simulator) -> None:
        self.ctx = ctx
        self.sim = simulator

    def lose_mains(self, generator_failed: bool = False, generator_delay: float = 15.0,
                   failed_rescue_cars=(), stuck_cars=()) -> None:
        """Mains lost now. GeneratorReady follows after generator_delay unless it failed."""
        self.sim.rescue_failed = set(failed_rescue_cars)
        self.sim.rescue_stuck = set(stuck_cars)
        self.ctx.bus.publish(MainsLost(self.ctx.clock.now()))
        if not generator_failed:
            self.ctx.clock.call_later(
                generator_delay, lambda: self.ctx.bus.publish(GeneratorReady(self.ctx.clock.now())))

    def restore_mains(self) -> None:
        self.ctx.bus.publish(MainsRestored(self.ctx.clock.now()))


class Camera:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.count = 0

    def event(self, car_id: str, kind: str, people_count: int = 1) -> None:
        self.count += 1
        event_id = f"cam-{self.count:05d}"
        self.ctx.bus.publish(CameraEvent(self.ctx.clock.now(), event_id, car_id, kind,
                                         people_count, f"clip://{event_id}"))


class Devices:
    """Bundle of the building-side helpers: ctx.services["devices"]."""

    def __init__(self, ctx, simulator) -> None:
        self.ctx = ctx
        self.fire = FirePanel(ctx)
        self.power = PowerRoom(ctx, simulator)
        self.camera = Camera(ctx)

    def alarm_button(self, car_id: str) -> None:
        self.ctx.bus.publish(AlarmButtonPressed(self.ctx.clock.now(), car_id))
