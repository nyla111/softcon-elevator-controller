"""Proposed P3-owned pre-admission/lifecycle wiring for FR-120/121/122/125."""
from __future__ import annotations

from ems.contracts import events as E
from ems.contracts.config import LOBBY
from ems.contracts.types import Call, CallPriority, CallSource, DoorState, Role
from ems.safety.call_policy import ActionLimiter


class AccessActions:
    def __init__(self, ctx, access):
        self.ctx, self.access = ctx, access
        self.limiter = ActionLimiter(ctx.clock, self._daily_notice)
        self.car_calls = {}
        self.tokens = {}
        self.calls = {}
        self.assigned = {}
        self.boarded = set()
        self.uncertain = set()
        self.cycles = {}
        self.last_status = {}
        self.sequence = 0
        for event, handler in ((E.CallRequested, self._requested),
                               (E.CallAssigned, self._assigned),
                               (E.CallReassigned, self._reassigned),
                               (E.PassengerBoarded, self._boarded),
                               (E.CallCancelled, self._cancelled),
                               (E.DoorsOpened, self._opened),
                               (E.CarStatusUpdated, self._status)):
            ctx.bus.subscribe(event, handler)

    def _daily_notice(self, credential):
        now = self.ctx.clock.now()
        self.access.access_logging.add_log(now, credential, None, "ACTION_LIMIT_REVIEW")
        self.ctx.bus.publish(E.AlertRaised(now, "high", "action_limit_review", ["security"],
                                          "Three action-limit episodes today; review security log"))

    def admit(self, credential, panel, origin, destination, permission=None, *, assistance=False):
        building = self.ctx.services.get("building")
        lobby = building.lobby if building else LOBBY  # Unit-only Access has contract stubs.
        exit_trip = destination == lobby and destination < origin
        accessible = assistance or (permission is not None and Role.ACCESSIBLE in permission.roles)
        decision = self.limiter.evaluate(credential, exit=exit_trip, assistance=accessible)
        if not decision.allowed:
            self.ctx.bus.publish(E.PanelMessage(self.ctx.clock.now(), panel,
                                               decision.message_en, decision.message_vi))
            self.ctx.bus.publish(E.AccessRefused(self.ctx.clock.now(), panel, credential, "action_limit"))
        return decision.allowed

    def begin_car_action(self, car):
        token = object()  # Owner-issued identity: arbitrary caller strings are not proof.
        self.tokens[token] = (car, None, None, None)
        return token

    def replay(self, token, car, destination, credential):
        if token is None:
            return None
        action = self.tokens.get(token)
        if action is None or action[0] != car:
            return False
        if action[1] is None:
            return None
        return action[1] == destination and action[2] == credential

    def registered(self, car, destination, credential, stop_id, token=None):
        self.car_calls[stop_id] = (car, destination, credential)
        if token is not None:
            self.tokens[token] = (car, destination, credential, stop_id)
        status = self.ctx.get("controllers").status(car)
        pending = [call for call in self.car_calls.values() if call[0] == car]
        if len(pending) > status.passengers + 2:
            self.ctx.bus.publish(E.AlertRaised(
                self.ctx.clock.now(), "info", "uncertain_car_calls", ["security"],
                "Car calls exceed estimated riders plus two; retained pending explicit proof", car,
            ))

    def cancel(self, credential, ref):
        if ref in self.boarded or self.ctx.get("car_states").safety_active():
            return False
        car_call = self.car_calls.get(ref)
        call = self.calls.get(ref)
        owner = car_call[2] if car_call else (call.credential_id if call else None)
        if owner is None or owner != credential:
            return False
        permission = self.access.permission(credential)
        if not permission.valid:
            return False
        car = car_call[0] if car_call else self.assigned.get(ref)
        origin = self.ctx.get("controllers").status(car).floor if car else call.origin
        destination = car_call[1] if car_call else call.destination
        if not self.admit(credential, f"car:{car}" if car else call.panel_id,
                          origin, destination, permission):
            return False
        if car_call:
            if not self.ctx.get("controllers").cancel_stop(car, ref):
                return False
            self.car_calls.pop(ref)
        else:
            self.ctx.bus.publish(E.CallCancelled(self.ctx.clock.now(), ref, "user"))
        return True

    def request_hall(self, credential, panel, origin, destination, group):
        permission = self.access.permission(credential)
        floors = set(permission.allowed_floors) | self.access.public_floors(self.ctx.clock.now())
        if not permission.valid or destination not in floors:
            return None
        if not self.access.floor_access.lockdown.can_visit_floor(destination, permission):
            return None
        if not self.admit(credential, panel, origin, destination, permission):
            return None
        self.sequence += 1
        ref = f"hall-{self.sequence}"
        priority = CallPriority.ACCESSIBLE if Role.ACCESSIBLE in permission.roles else CallPriority.NORMAL
        call = Call(ref, origin, destination, group, self.ctx.clock.now(), CallSource.LANDING_BUTTON,
                    priority, credential, panel_id=panel)
        self.ctx.bus.publish(E.CallRequested(self.ctx.clock.now(), call))
        return ref

    def mark_uncertain(self, ref):
        self.uncertain.add(ref)

    def _requested(self, event):
        self.calls[event.call.call_id] = event.call

    def _assigned(self, event):
        self.assigned[event.call_id] = event.car_id

    def _reassigned(self, event):
        self.assigned[event.call_id] = event.to_car
        self.cycles.pop(event.call_id, None)

    def _boarded(self, event):
        self.boarded.add(event.call_id)
        self.cycles.pop(event.call_id, None)

    def _cancelled(self, event):
        self.calls.pop(event.call_id, None)
        self.assigned.pop(event.call_id, None)
        self.cycles.pop(event.call_id, None)

    def _opened(self, event):
        for ref, call in list(self.calls.items()):
            if (call.source is CallSource.LANDING_BUTTON and call.origin == event.floor
                    and self.assigned.get(ref) == event.car_id and ref not in self.boarded):
                self.cycles[ref] = (event.car_id, event.floor, event.t, False)
        for ref, (car, floor, _) in list(self.car_calls.items()):
            if (car, floor) == (event.car_id, event.floor):
                self.car_calls.pop(ref)

    def _status(self, event):
        status = event.status
        old = self.last_status.get(status.car_id)
        if event.t != self.ctx.clock.now() or (old is not None and event.t < old):
            return
        gap = old is not None and event.t - old > 1.001
        self.last_status[status.car_id] = event.t
        for ref, (car, floor, opened, closing) in list(self.cycles.items()):
            if car != status.car_id:
                continue
            if gap or status.disconnected or status.fault_code or status.floor != floor:
                self.mark_uncertain(ref)
            if status.doors is DoorState.CLOSING:
                self.cycles[ref] = (car, floor, opened, True)
            elif closing and status.doors is DoorState.CLOSED:
                self.cycles.pop(ref)
                call = self.calls.get(ref)
                if call is None or ref in self.boarded or ref in self.uncertain:
                    continue
                exit_call = call.destination == self.ctx.get("building").lobby and call.destination < call.origin
                if exit_call or call.priority in (CallPriority.ACCESSIBLE, CallPriority.MEDICAL):
                    continue
                # Count the proven lifecycle action even if already blocked; a block cannot
                # erase the no-show evidence or extend the sixth-action cooldown.
                self.limiter.allow(call.credential_id)
                self.ctx.bus.publish(E.CallCancelled(event.t, ref, "no_show"))
