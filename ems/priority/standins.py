"""SCENARIO / TEST DOUBLES ONLY. Never registered by ems.priority.register().

At the base commit Part 2 (dispatcher), Part 3 (access) and Part 4 (car_states) are still the
contract stubs, and Part 1's mock ACS knows no staff cards. To run Part 5's scenarios at all,
the Part 5 scenario setups install these doubles, the same way Part 1 installs its nearest-car
stand-in (ems/sim/baseline.py) while Part 2 is a stub:

* ScheduleModeDispatcher  Dispatcher double: current_mode() follows config.WEEKDAY_SCHEDULE at
                          ctx.clock.now_dt(); everything else is delegated to the contract stub
                          (would_breach_ceiling_without -> False, reassign_calls_of -> []).
* PriorityAwareNearestCar Part 1's nearest-car stand-in plus the two calls Part 2 is asked to
                          make (docs/priority/INTEGRATION.md): claim_call() for VIP calls and the
                          FR-08 free-capacity filter for accessible calls. It is the reference for
                          Part 2's integration, not an implementation of Part 2.
* StaffDirectory          Authorizer double: fixed staff cards (security, FM, technician,
                          tenant admin); any other credential goes to the real Access service.
* RecordingCarStates      evidence only: wraps car_states (stub or real) and logs every
                          request / release Part 5 makes.
* VipReplacementPopulation SIM-12 traffic: the first ordinary arrival of one group in each listed
                          measured minute becomes a VIP arrival (same person, same trip, same rng
                          draws: no traffic is added).

integration_gaps(ctx) lists which of these doubles / stubs a run used, so every scenario check
can report the run as BLOCKED instead of passing on stand-ins.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Optional

from ems.contracts import config
from ems.contracts.events import CallAssigned, CallRequested, PassengerArrived
from ems.contracts.types import Call, CallPriority, CredentialId, Mode, Role
from ems.sim.baseline import NearestCar

from .auth import AccessAuthorizer, Actor

# Fake staff cards for scenario operators. Not people of the population, never in trip records.
STAFF = {
    "staff-security-1": ({Role.SECURITY}, None),
    "staff-fm-1": ({Role.FM}, None),
    "staff-tech-1": ({Role.TECHNICIAN}, None),
    "staff-tenant-admin-T01": ({Role.TENANT_ADMIN}, "T01"),
    "staff-tenant-admin-T02": ({Role.TENANT_ADMIN}, "T02"),
}


class StaffDirectory:
    """Authorizer double (see auth.Authorizer)."""

    def __init__(self, ctx, staff: Optional[dict] = None) -> None:
        self.staff = STAFF if staff is None else staff
        self.fallback = AccessAuthorizer(ctx)

    def actor(self, credential_id: CredentialId) -> Actor:
        if credential_id in self.staff:
            roles, tenant = self.staff[credential_id]
            return Actor(credential_id, frozenset(roles), tenant)
        return self.fallback.actor(credential_id)


def mode_at(dt: datetime) -> Mode:
    schedule = config.WEEKDAY_SCHEDULE if dt.weekday() < 5 else config.WEEKEND_SCHEDULE
    hhmm = dt.strftime("%H:%M")
    mode = schedule[0][1]
    for start, m in schedule:
        if start <= hhmm:
            mode = m
    return mode


class ScheduleModeDispatcher:
    """Dispatcher double (Part 2 is a stub). Only current_mode() differs from the stub."""

    def __init__(self, ctx, stub) -> None:
        self.ctx = ctx
        self.stub = stub

    def current_mode(self, group) -> Mode:
        return mode_at(self.ctx.clock.now_dt())

    def predicted_wait(self, call_id):
        return self.stub.predicted_wait(call_id)

    def reassign_calls_of(self, car_id, reason):
        return self.stub.reassign_calls_of(car_id, reason)

    def pending_calls(self, group):
        return self.stub.pending_calls(group)

    def would_breach_ceiling_without(self, car_id) -> bool:
        return self.stub.would_breach_ceiling_without(car_id)


class PriorityAwareNearestCar(NearestCar):
    """Part 1's nearest-car stand-in + the Part 5 hooks Part 2 is asked to call."""

    def on_call(self, ev: CallRequested) -> None:
        call: Call = ev.call
        priority = self.ctx.services["priority"]
        car = priority.claim_call(call)
        if car is not None:            # Part 5 already sent the car (express): just assign it
            status = self.ctx.services["controllers"].status(car)
            eta = abs(status.position - call.origin) * config.SIM["s_per_floor"]
            self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car, eta))
            return
        if call.priority is not CallPriority.ACCESSIBLE:
            super().on_call(ev)
            return
        controllers = self.ctx.services["controllers"]
        guard = self.ctx.services["dispatch_guard"]
        mode = self.ctx.services["dispatcher"].current_mode(call.group)
        cars = [s for s in controllers.all_status(call.group)
                if s.fault_code is None and not s.disconnected and priority.car_ok_for(call, s)]
        if not cars:                   # no car with room right now: plain nearest car
            super().on_call(ev)
            return
        while cars:
            car_id, _ = guard.choose_car(self.engine, call, cars, mode)
            if car_id is None:
                break
            if controllers.register_stop(car_id, call.origin, call.call_id):
                status = next(s for s in cars if s.car_id == car_id)
                eta = abs(status.position - call.origin) * config.SIM["s_per_floor"]
                self.ctx.bus.publish(CallAssigned(ev.t, call.call_id, car_id, eta))
                return
            cars = [s for s in cars if s.car_id != car_id]
        super().on_call(ev)


class RecordingCarStates:
    """Evidence recorder around ctx.services['car_states'] (stub or Part 4's real service):
    every request / release is delegated unchanged and written to `log` as
    (t, car_id, "request" | "release", state, by, answer)."""

    def __init__(self, ctx, inner) -> None:
        self.ctx = ctx
        self.inner = inner
        self.log: list[tuple] = []

    def state(self, car_id):
        return self.inner.state(car_id)

    def request(self, car_id, new, by) -> bool:
        ok = self.inner.request(car_id, new, by)
        self.log.append((self.ctx.clock.now(), car_id, "request", new, by, ok))
        return ok

    def release(self, car_id, from_state, by) -> bool:
        ok = self.inner.release(car_id, from_state, by)
        self.log.append((self.ctx.clock.now(), car_id, "release", from_state, by, ok))
        return ok

    def dispatchable(self, group):
        return self.inner.dispatchable(group)

    def safety_active(self) -> bool:
        return self.inner.safety_active()


def install_scenario_standins(ctx, staff: Optional[dict] = None) -> list[str]:
    """Install the doubles a Part 5 scenario needs, only where the real part is missing.
    Returns the names of what was installed (also kept in ctx.services['priority_standins'])."""
    installed = []
    if type(ctx.services["dispatcher"]).__name__ == "StubDispatcher":
        ctx.services["dispatcher"] = ScheduleModeDispatcher(ctx, ctx.services["dispatcher"])
        PriorityAwareNearestCar(ctx)
        installed += ["ScheduleModeDispatcher", "PriorityAwareNearestCar"]
    ctx.services["priority"].use_authorizer(StaffDirectory(ctx, staff))
    installed.append("StaffDirectory")
    ctx.services["car_states"] = RecordingCarStates(ctx, ctx.services["car_states"])
    ctx.services["priority_standins"] = installed
    return installed


def integration_gaps(ctx) -> list[str]:
    """Which contract services of this run are still stubs or Part 5 doubles."""
    gaps = []
    real = {k: getattr(ctx.services.get(k), "inner", ctx.services.get(k))
            for k in ("dispatcher", "access", "car_states")}
    names = {k: type(v).__name__ for k, v in real.items()}
    if names["dispatcher"] in ("StubDispatcher", "ScheduleModeDispatcher"):
        gaps.append(f"Part 2 dispatcher = {names['dispatcher']}")
    if names["access"] == "StubAccess":
        gaps.append("Part 3 access = StubAccess (calls come from Part 1's temporary bridge)")
    if names["car_states"] in ("StubCarStates", "SpecCarStates"):
        gaps.append(f"Part 4 car_states = {names['car_states']} (grants every request)")
    if "StaffDirectory" in ctx.services.get("priority_standins", []):
        gaps.append("staff cards from StaffDirectory (mock ACS has no staff)")
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
    """SIM-12: wraps Part 1's Population. In each listed measured minute, the FIRST arrival of
    `group` whose person is ordinary (not VIP, not accessible) is turned into a VIP arrival:
    same person, same trip, same random draws, so no arrival is added or removed."""

    def __init__(self, ctx, base, group: str, minutes_s: list[tuple[float, float]]) -> None:
        self.ctx = ctx
        self.base = base
        self.group = group
        self.windows = list(minutes_s)                 # [(start, end)] in run seconds
        self.done: dict[int, str] = {}                 # window index -> passenger id
        self.last_pick = None
        self._flipped: set[int] = set()                # id() of the VIP copies handed out
        ctx.bus.subscribe(PassengerArrived, self._on_arrived)

    def __getattr__(self, name):
        return getattr(self.base, name)

    def _window(self) -> Optional[int]:
        now = self.ctx.clock.now()
        for i, (a, b) in enumerate(self.windows):
            if a <= now < b and i not in self.done:
                return i
        return None

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
