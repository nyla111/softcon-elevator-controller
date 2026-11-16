"""Dependency DOUBLES for Part 5 unit tests (they follow the contract Protocols in
ems/contracts/interfaces.py; they are not the real Part 1-4 services).

FakeControllers  ControllerAdapter: settable car status, every command recorded, refusals on demand
FakeCarStates    CarStates: keeps the state per car, refuses chosen requests, logs everything
FakeDispatcher   Dispatcher: settable mode and "would breach" cars, records reassign_calls_of
FakeAccess       AccessService: credential -> Permission table
"""
from __future__ import annotations

import pytest

from ems.contracts.types import (Call, CallPriority, CallSource, CarState, CarStatus, Direction,
                                 DoorState, Mode, Permission, Role)
from ems.core.app import build
from ems.sim.building import BuildingSpec

BUILDING = BuildingSpec.reference()
SECURITY, FM, TECH, EMPLOYEE = "sec-1", "fm-1", "tech-1", "emp-1"
ADMIN_T01, ADMIN_T02 = "adm-T01", "adm-T02"


class FakeControllers:
    def __init__(self) -> None:
        self.s = {c: CarStatus(c, g.name, 1, 1.0, Direction.IDLE, DoorState.CLOSED, 0.0, 0)
                  for g in BUILDING.groups for c in g.cars}
        self.log: list[tuple] = []
        self.refuse: set[tuple] = set()          # (command, car) or (command, "*")

    def put(self, car, **kw):
        st = self.s[car]
        for k, v in kw.items():
            setattr(st, k, v)
        if "floor" in kw and "position" not in kw:
            st.position = float(kw["floor"])

    def status(self, car_id):
        return self.s[car_id]

    def all_status(self, group=None):
        return [s for s in self.s.values() if group is None or s.group == group]

    def _cmd(self, name, car, *args):
        ok = (name, car) not in self.refuse and (name, "*") not in self.refuse \
            and self.s[car].fault_code is None and not self.s[car].disconnected
        self.log.append((name, car, args, ok))
        return ok

    def register_stop(self, car_id, floor, call_id): return self._cmd("register_stop", car_id, floor)
    def cancel_stop(self, car_id, call_id): return self._cmd("cancel_stop", car_id, call_id)
    def park(self, car_id, floor): return self._cmd("park", car_id, floor)
    def set_standby(self, car_id, on): return self._cmd("set_standby", car_id, on)
    def set_independent_service(self, car_id, on):
        return self._cmd("set_independent_service", car_id, on)
    def close_to_new_calls(self, car_id, on): return self._cmd("close_to_new_calls", car_id, on)
    def express(self, car_id, floor, hold_doors_open=False):
        return self._cmd("express", car_id, floor, hold_doors_open)

    def sent(self, name=None, car=None, accepted=True):
        return [x for x in self.log if (name is None or x[0] == name)
                and (car is None or x[1] == car) and (accepted is None or x[3] == accepted)]


class FakeCarStates:
    def __init__(self) -> None:
        self.states = {c: CarState.GROUP_SERVICE for g in BUILDING.groups for c in g.cars}
        self.refuse: set[tuple] = set()          # (car, state)
        self.log: list[tuple] = []
        self.safety = False

    def state(self, car_id): return self.states[car_id]

    def request(self, car_id, new, by):
        ok = (car_id, new) not in self.refuse and (
            new.outranks(self.states[car_id]) or self.states[car_id] is new)
        self.log.append(("request", car_id, new, by, ok))
        if ok:
            self.states[car_id] = new
        return ok

    def release(self, car_id, from_state, by):
        ok = self.states[car_id] is from_state
        self.log.append(("release", car_id, from_state, by, ok))
        if ok:
            self.states[car_id] = CarState.GROUP_SERVICE
        return ok

    def dispatchable(self, group):
        return [c for c in BUILDING.group(group).cars if self.states[c].dispatchable]

    def safety_active(self): return self.safety


class FakeDispatcher:
    def __init__(self) -> None:
        self.mode = Mode.BALANCED
        self.breach: set[str] = set()
        self.reassigned: list[tuple] = []

    def current_mode(self, group): return self.mode
    def predicted_wait(self, call_id): return 20.0
    def reassign_calls_of(self, car_id, reason):
        self.reassigned.append((car_id, reason))
        return []
    def pending_calls(self, group): return []
    def would_breach_ceiling_without(self, car_id): return car_id in self.breach


def _perm(cred, roles, tenant=None, valid=True):
    return Permission(cred, frozenset({1}), 1, frozenset(roles), valid, tenant)


class FakeAccess:
    def __init__(self) -> None:
        self.table = {SECURITY: _perm(SECURITY, {Role.SECURITY}),
                      FM: _perm(FM, {Role.FM}),
                      TECH: _perm(TECH, {Role.TECHNICIAN}),
                      EMPLOYEE: _perm(EMPLOYEE, {Role.EMPLOYEE}, "T01"),
                      ADMIN_T01: _perm(ADMIN_T01, {Role.TENANT_ADMIN}, "T01"),
                      ADMIN_T02: _perm(ADMIN_T02, {Role.TENANT_ADMIN}, "T02")}
        self.down = False

    def permission(self, credential_id):
        if self.down:
            raise TimeoutError("ACS down")
        return self.table.get(credential_id, _perm(credential_id, set(), valid=False))

    def public_floors(self, at_time): return {1}


class Env:
    def __init__(self) -> None:
        self.controllers = FakeControllers()
        self.states = FakeCarStates()
        self.dispatcher = FakeDispatcher()
        self.access = FakeAccess()
        self.ctx = build(seed=1, parts=["priority"], services={
            "building": BUILDING, "controllers": self.controllers, "car_states": self.states,
            "dispatcher": self.dispatcher, "access": self.access})
        self.p = self.ctx.services["priority"]
        self._n = 0

    @property
    def bus(self):
        return self.ctx.bus

    def publish(self, ev):
        self.ctx.bus.publish(ev)

    def run_to(self, t):
        self.ctx.clock.run_until(t)

    def now(self):
        return self.ctx.clock.now()

    def events(self, cls):
        return self.ctx.bus.of_type(cls)

    def call(self, origin=1, destination=20, group="HIGH", priority=CallPriority.VIP,
             credential="vip-card-1", party=1, call_id=None):
        self._n += 1
        return Call(call_id or f"C{self._n}", origin, destination, group, self.now(),
                    CallSource.DESTINATION_PANEL, priority, credential, party,
                    f"panel-L{origin}")


@pytest.fixture
def env():
    return Env()
