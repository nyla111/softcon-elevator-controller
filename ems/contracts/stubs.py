"""Placeholder implementations so every part can run before the others exist.
Owned by: Tech lead. Replace nothing here; real parts register over these.
"""
from __future__ import annotations

from typing import Optional

from .config import GROUPS, LOBBY
from .types import (Call, CallId, CarId, CarState, CarStatus, CredentialId,
                    Direction, DoorState, Floor, GroupId, Mode, Permission, Role)


class StubControllers:
    def __init__(self) -> None:
        self._s = {c: CarStatus(c, g, LOBBY, float(LOBBY), Direction.IDLE,
                                DoorState.CLOSED, 0.0, 0)
                   for g, d in GROUPS.items() for c in d["cars"]}

    def status(self, car_id: CarId) -> CarStatus: return self._s[car_id]
    def all_status(self, group: Optional[GroupId] = None) -> list[CarStatus]:
        return [s for s in self._s.values() if group is None or s.group == group]
    def register_stop(self, car_id, floor, call_id) -> bool: return True
    def cancel_stop(self, car_id, call_id) -> bool: return True
    def park(self, car_id, floor) -> bool: return True
    def set_standby(self, car_id, on) -> bool: return True
    def set_independent_service(self, car_id, on) -> bool: return True
    def close_to_new_calls(self, car_id, on) -> bool: return True
    def express(self, car_id, floor, hold_doors_open=False) -> bool: return True


class StubAcs:
    def lookup(self, credential_id: CredentialId) -> Permission:
        return Permission(credential_id, frozenset(range(1, 31)), 18,
                          frozenset({Role.EMPLOYEE}), True, "tenant-1")
    def issue_visitor_pass(self, tenant_id, floors, valid_from, valid_to, group_size=1):
        return f"QR-{tenant_id}-{int(valid_from)}"


class StubAccess:
    def __init__(self) -> None: self._acs = StubAcs()
    def permission(self, credential_id): return self._acs.lookup(credential_id)
    def public_floors(self, at_time: float) -> set[Floor]: return {LOBBY}


class StubCarStates:
    def state(self, car_id: CarId) -> CarState: return CarState.GROUP_SERVICE
    def request(self, car_id, new, by) -> bool: return True
    def release(self, car_id, from_state, by) -> bool: return True
    def dispatchable(self, group: GroupId) -> list[CarId]: return list(GROUPS[group]["cars"])
    def safety_active(self) -> bool: return False


class StubDispatcher:
    def current_mode(self, group: GroupId) -> Mode: return Mode.BALANCED
    def predicted_wait(self, call_id: CallId) -> float: return 20.0
    def reassign_calls_of(self, car_id, reason) -> list[CallId]: return []
    def pending_calls(self, group: GroupId) -> list[Call]: return []
    def would_breach_ceiling_without(self, car_id: CarId) -> bool: return False


class StubNotifier:
    def __init__(self) -> None: self.inbox: list[tuple[list[str], str, str]] = []
    def send(self, recipients, subject, body) -> None:
        self.inbox.append((list(recipients), subject, body))


STUBS = {
    "controllers": StubControllers, "acs": StubAcs, "notifier": StubNotifier,
    "access": StubAccess, "car_states": StubCarStates, "dispatcher": StubDispatcher,
}
