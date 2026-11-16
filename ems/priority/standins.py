"""SCENARIO / TEST DOUBLES ONLY. Never registered by ems.priority.register().

Part 2 (dispatcher), Part 3 (access) and Part 4 (car_states) are still contract stubs, so the
Part 5 scenario setups install these doubles where the real part is missing (as Part 1 installs
its nearest-car stand-in while Part 2 is a stub):

* ScheduleModeDispatcher   Dispatcher: current_mode() follows config.WEEKDAY_SCHEDULE; the rest
                           is the contract stub (would_breach_ceiling_without -> False,
                           reassign_calls_of -> []).
* PriorityAwareNearestCar  Part 1's nearest-car stand-in plus the PriorityService calls Part 2
                           makes: claim_call() for VIP calls, car_ok_for() for accessible calls,
                           register_stop(..., min_dwell_s=door_dwell_s(call)).
* AcsStaffAuthorizer       while Part 3's access service is the stub (which answers EMPLOYEE for
                           every card), staff roles are read from Part 1's mock ACS (staff cards).
* RecordingCarStates       evidence only: wraps car_states and logs every request / release.
* VipReplacementPopulation SIM-12 traffic: the first ordinary arrival of one group in each listed
                           minute becomes a VIP arrival (same person and trip, same rng draws).

integration_gaps(ctx) names the doubles a run used, so a scenario cannot pass on them.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from ems.contracts import config
from ems.contracts.events import CallAssigned, CallRequested, PassengerArrived
from ems.contracts.types import Call, CallPriority, CredentialId, Mode
from ems.sim.baseline import NearestCar

from .auth import Actor


class AcsStaffAuthorizer:
    """Authorizer double: roles from Part 1's mock ACS (ctx.services['acs'])."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx

    def actor(self, credential_id: CredentialId) -> Actor:
        try:
            perm = self.ctx.get("acs").lookup(credential_id)
        except (TimeoutError, KeyError):
            return Actor(credential_id, frozenset())
        if not perm.valid:
            return Actor(credential_id, frozenset())
        return Actor(credential_id, frozenset(perm.roles), perm.tenant_id)


def mode_at(dt: datetime) -> Mode:
    schedule = config.WEEKDAY_SCHEDULE if dt.weekday() < 5 else config.WEEKEND_SCHEDULE
    mode = schedule[0][1]
    for start, m in schedule:
        if start <= dt.strftime("%H:%M"):
            mode = m
    return mode


class ScheduleModeDispatcher:
    """Dispatcher double: only current_mode() differs from the contract stub."""

    def __init__(self, ctx, stub) -> None:
        self.ctx = ctx
        self.stub = stub

    def current_mode(self, group) -> Mode:
        return mode_at(self.ctx.clock.now_dt())

    def __getattr__(self, name):               # predicted_wait, reassign_calls_of, ...
        return getattr(self.stub, name)


class PriorityAwareNearestCar(NearestCar):
    """Nearest-car assignment with the PriorityService calls Part 2 makes."""

    def on_call(self, ev: CallRequested) -> None:
        call: Call = ev.call
        priority = self.ctx.services["priority"]
        controllers = self.ctx.services["controllers"]
        car = priority.claim_call(call)
        if car is not None:            # Part 5 already sent the car (express): just assign it
            status = controllers.status(car)
            eta = abs(status.position - call.origin) * config.SIM["s_per_floor"]
            self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car, eta))
            return
        cars = [s for s in controllers.all_status(call.group)
                if s.fault_code is None and not s.disconnected]
        if call.priority is CallPriority.ACCESSIBLE:
            cars = [s for s in cars if priority.car_ok_for(call, s)] or cars
        dwell = priority.door_dwell_s(call) if call.priority is CallPriority.ACCESSIBLE else None
        guard = self.ctx.services["dispatch_guard"]
        mode = self.ctx.services["dispatcher"].current_mode(call.group)
        while cars:
            car_id, _ = guard.choose_car(self.engine, call, cars, mode)
            if car_id is None:
                return
            if controllers.register_stop(car_id, call.origin, call.call_id, dwell):
                status = next(s for s in cars if s.car_id == car_id)
                eta = abs(status.position - call.origin) * config.SIM["s_per_floor"]
                self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car_id, eta))
                return
            cars = [s for s in cars if s.car_id != car_id]


class RecordingCarStates:
    """Evidence recorder around car_states (stub or Part 4's service): delegates unchanged and
    logs (t, car_id, "request" | "release", state, by, answer)."""

    def __init__(self, ctx, inner) -> None:
        self.ctx = ctx
        self.inner = inner
        self.log: list[tuple] = []

    def request(self, car_id, new, by) -> bool:
        ok = self.inner.request(car_id, new, by)
        self.log.append((self.ctx.clock.now(), car_id, "request", new, by, ok))
        return ok

    def release(self, car_id, from_state, by) -> bool:
        ok = self.inner.release(car_id, from_state, by)
        self.log.append((self.ctx.clock.now(), car_id, "release", from_state, by, ok))
        return ok

    def __getattr__(self, name):               # state, dispatchable, safety_active
        return getattr(self.inner, name)


def install_scenario_standins(ctx) -> list[str]:
    """Install the doubles a Part 5 scenario needs, only where the real part is missing."""
    installed = []
    if type(ctx.services["dispatcher"]).__name__ == "StubDispatcher":
        ctx.services["dispatcher"] = ScheduleModeDispatcher(ctx, ctx.services["dispatcher"])
        PriorityAwareNearestCar(ctx)
        installed += ["ScheduleModeDispatcher", "PriorityAwareNearestCar"]
    if type(ctx.services["access"]).__name__ == "StubAccess":
        ctx.services["priority"].use_authorizer(AcsStaffAuthorizer(ctx))
        installed.append("AcsStaffAuthorizer")
    ctx.services["car_states"] = RecordingCarStates(ctx, ctx.services["car_states"])
    ctx.services["priority_standins"] = installed
    return installed


def integration_gaps(ctx) -> list[str]:
    """Which contract services of this run are still stubs or Part 5 doubles."""
    real = {k: getattr(ctx.services.get(k), "inner", ctx.services.get(k))
            for k in ("dispatcher", "access", "car_states")}
    names = {k: type(v).__name__ for k, v in real.items()}
    gaps = []
    if names["dispatcher"] in ("StubDispatcher", "ScheduleModeDispatcher"):
        gaps.append(f"Part 2 dispatcher = {names['dispatcher']}")
    if names["access"] == "StubAccess":
        gaps.append("Part 3 access = StubAccess (staff roles read from the mock ACS)")
    if names["car_states"] in ("StubCarStates", "SpecCarStates"):
        gaps.append(f"Part 4 car_states = {names['car_states']} (grants every request)")
    return gaps


class _Picks(list):
    """List that remembers which element rng.choice() took (random.choice indexes it once)."""

    def __init__(self, items, owner) -> None:
        super().__init__(items)
        self.owner = owner

    def __getitem__(self, i):
        item = super().__getitem__(i)
        self.owner.last_pick = item
        return item


class VipReplacementPopulation:
    """SIM-12: wraps Part 1's Population. In each listed window the FIRST arrival of `group`
    whose person is ordinary (not VIP, not accessible) becomes a VIP arrival."""

    def __init__(self, ctx, base, group: str, windows: list[tuple[float, float]]) -> None:
        self.ctx = ctx
        self.base = base
        self.group = group
        self.windows = list(windows)                   # [(start, end)] in run seconds
        self.done: dict[int, str] = {}                 # window index -> passenger id
        self.last_pick = None
        self._flipped: set[int] = set()                # id() of the VIP copies handed out
        ctx.bus.subscribe(PassengerArrived, self._on_arrived)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _window(self) -> Optional[int]:
        now = self.ctx.clock.now()
        return next((i for i, (a, b) in enumerate(self.windows)
                     if a <= now < b and i not in self.done), None)

    def on_floor(self, floor):
        people = self.base.on_floor(floor)
        if self._window() is None or self.base.building.group_of_floor.get(floor) != self.group:
            return people
        copies = []
        for e in people:
            if not e.vip and not e.accessible:
                e = replace(e, vip=True)
                self._flipped.add(id(e))
            copies.append(e)
        self.last_pick = None
        return _Picks(copies, self)

    def _on_arrived(self, ev: PassengerArrived) -> None:
        i = self._window()
        if i is None or ev.group != self.group:
            return
        if self.last_pick is not None and id(self.last_pick) in self._flipped:
            self.done[i] = ev.passenger_id
        self.last_pick = None
        self._flipped.clear()

    @property
    def replaced(self) -> list[str]:
        return [self.done[i] for i in sorted(self.done)]


__all__ = ["AcsStaffAuthorizer", "PriorityAwareNearestCar",
           "RecordingCarStates", "ScheduleModeDispatcher", "VipReplacementPopulation",
           "install_scenario_standins", "integration_gaps", "mode_at"]
