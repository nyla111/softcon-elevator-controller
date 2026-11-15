"""SIM-06 Car fault at up-peak, then a second removal requested (FR-04, FR-33)."""
from __future__ import annotations


from ems.contracts import config
from ems.contracts.events import (AlertRaised, CallAssigned, CallCancelled, CallReassigned,
                                  ControllerFault,
                                  ControllerFaultCleared, PassengerBoarded, TripCompleted)
from ems.contracts.types import CarState, Mode
from ems.sim.faults import car_fault, measured, schedule_fault

from ..standins import install_scenario_standins
from .common import (MOVE_COMMANDS, TECH, FM, _service, _log, _act, _action, _integration,
                      _dispatcher_is_double, _cleared, _waits, _wait_summary)

# The faulted car is chosen when the fault happens: the car of the first passenger group with the
# most calls assigned and not yet picked up (so "waiting calls retained" is really exercised).
# The two cars of the second-removal requests are the next free cars of the same group.
SIM06_FAULT_AT, SIM06_FAULT_S, SIM06_CODE = measured(20), 300.0, "F-06"
SIM06_ASK_EXPIRE, SIM06_ASK_APPROVE = measured(22), measured(23)
SIM06_APPROVE, SIM06_EXIT, SIM06_LATE = measured(24), measured(29), measured(33)
SIM06_ASK_DENY, SIM06_DENY = measured(24) + 30.0, measured(24) + 90.0


class _Waiting:
    """Calls assigned to each car and not yet boarded / cancelled (from bus events)."""

    def __init__(self, ctx) -> None:
        self.by_call: dict[str, str] = {}
        ctx.bus.subscribe(CallAssigned, lambda e: self.by_call.__setitem__(e.call_id, e.car_id))
        ctx.bus.subscribe(CallReassigned, lambda e: self.by_call.__setitem__(e.call_id, e.to_car))
        ctx.bus.subscribe(PassengerBoarded, lambda e: self.by_call.pop(e.call_id, None))
        ctx.bus.subscribe(CallCancelled, lambda e: self.by_call.pop(e.call_id, None))

    def count(self, car_id: str) -> int:
        return sum(1 for c in self.by_call.values() if c == car_id)


def setup_sim_06(ctx) -> None:
    install_scenario_standins(ctx)
    p, log = _service(ctx), _log(ctx)
    m = p.maintenance
    waiting = _Waiting(ctx)
    group = ctx.services["building"].group(ctx.services["building"].passenger_groups[0])
    at = ctx.clock.call_at

    def inject() -> None:
        free = [c for c in group.cars if c not in p.holds]
        car = max(free, key=lambda c: (waiting.count(c), c))
        log["fault_car"] = car
        log["waiting_at_fault"] = sorted(cid for cid, c in waiting.by_call.items() if c == car)
        log["mode_at_fault"] = ctx.services["dispatcher"].current_mode(group.name).value
        schedule_fault(ctx, car_fault(car, SIM06_CODE, ctx.clock.now(), SIM06_FAULT_S))

    def ask(name: str) -> None:
        taken = {log["fault_car"]} | {a[3] for a in log["actions"] if len(a) > 3}
        car = next(c for c in group.cars if c not in taken and c not in p.holds)
        out = m.request_maintenance(TECH, car)
        log["actions"].append((ctx.clock.now(), name, out, car))

    def decide(name: str, request: str) -> None:
        req = _action(ctx, request)
        _act(ctx, name, lambda: m.approve_removal(FM, req[2].ref))

    def leave() -> None:
        car = _action(ctx, "request approve-car")[3]
        _act(ctx, "technician exits", lambda: m.exit_maintenance(TECH, car))

    at(SIM06_FAULT_AT, inject)
    at(SIM06_ASK_EXPIRE, lambda: ask("request expire-car"))
    at(SIM06_ASK_APPROVE, lambda: ask("request approve-car"))
    at(SIM06_APPROVE, lambda: decide("fm approves", "request approve-car"))
    at(SIM06_EXIT, leave)
    at(SIM06_LATE, lambda: decide("fm approves late", "request expire-car"))
    at(SIM06_ASK_DENY, lambda: ask("request deny-car"))
    at(SIM06_DENY, lambda: _act(ctx, "fm denies", lambda: m.deny_removal(
        FM, _action(ctx, "request deny-car")[2].ref)))


def check_sim_06(result, ctx) -> list:
    bus, sim, p, log = ctx.bus, ctx.services["controllers"], _service(ctx), _log(ctx)
    car = log.get("fault_car")
    faults = [e for e in bus.of_type(ControllerFault) if e.car_id == car]
    if car is None or not faults:
        return [("car fault injected", False, "no ControllerFault"), _integration(ctx)]
    cleared = [e for e in bus.of_type(ControllerFaultCleared) if e.car_id == car]
    ask_x, ask_a = _action(ctx, "request expire-car"), _action(ctx, "request approve-car")
    expire_car, approve_car = ask_x[3], ask_a[3]
    out = []
    t0 = faults[0].t
    t1 = cleared[0].t if cleared else float("inf")
    out.append(("car fault injected during up-peak Rush",
                t0 == SIM06_FAULT_AT and log["mode_at_fault"] == Mode.RUSH.value,
                f"car {car} at t={t0:.0f} s, cleared t={t1:.0f} s, mode "
                f"{log['mode_at_fault']} (run start {result.start})"))
    alerts = [a for a in bus.of_type(AlertRaised) if a.kind == "fault" and a.car_id == car]
    out.append(("FR-30 one fault alert to contractor and FM",
                len(alerts) == 1 and set(alerts[0].recipients) == {"contractor", "fm"},
                f"{len(alerts)} alert(s)"))
    moved = [c for c in sim.command_log if c[1] == car and t0 <= c[0] < t1
             and c[2] in MOVE_COMMANDS and c[4]]
    out.append(("faulted car not moved: no accepted movement command while faulted",
                not moved, f"{len(moved)} accepted"))
    ours = [c for c in p.commands if c[1] == car and t0 <= c[0] < t1]
    out.append(("Part 5 sent no command to the faulted car", not ours, f"{len(ours)} sent"))

    # FR-33 approval gate
    gate = (ask_x and ask_a and ask_x[2].reason == ask_a[2].reason == "approval_required")
    fm_alerts = [a for a in bus.of_type(AlertRaised) if a.kind == "removal_approval"]
    out.append(("second removal in Rush waits for FM approval (FM alerted)",
                bool(gate) and {a.car_id for a in fm_alerts} == {expire_car, approve_car}
                | ({_action(ctx, "request deny-car")[3]} if _action(ctx, "request deny-car")
                   else set()) and len(fm_alerts) == len({a.car_id for a in fm_alerts})
                and all(a.recipients == ["fm"] for a in fm_alerts),
                f"answers {ask_x and ask_x[2].reason}/{ask_a and ask_a[2].reason}, "
                f"{len(fm_alerts)} FM alert(s)"))
    expired = [a for a in bus.of_type(AlertRaised)
               if a.kind == "removal_expired" and a.car_id == expire_car]
    late = _action(ctx, "fm approves late")
    states = ctx.services["car_states"].log
    mnt = [x for x in states if x[2] == "request" and x[3] is CarState.MAINTENANCE]
    closed_x = [x for x in mnt if x[1] == expire_car]
    out.append(("unapproved request expires after 600 s simulated; late approval refused",
                bool(expired) and expired[0].t - ask_x[0] == config.SECOND_CAR_APPROVAL_TIMEOUT_S
                and late is not None and late[2].reason == "expired" and not closed_x,
                f"expired after {expired[0].t - ask_x[0]:.0f} s, late answer "
                f"{late and late[2].reason}, {len(closed_x)} MAINTENANCE request(s) for car "
                f"{expire_car}"
                if expired and ask_x else "no expiry"))
    approve = _action(ctx, "fm approves")
    closed_a = [x for x in mnt if x[1] == approve_car]
    first = closed_a[0][0] if closed_a else None
    ask_d, deny = _action(ctx, "request deny-car"), _action(ctx, "fm denies")
    deny_car = ask_d[3] if ask_d else None
    denied_mnt = [x for x in mnt if x[1] == deny_car]
    out.append(("denied removal never enters maintenance",
                ask_d is not None and ask_d[2].reason == "approval_required"
                and deny is not None and deny[2].reason == "denied" and not denied_mnt,
                f"car {deny_car}: request {ask_d and ask_d[2].reason}, FM answer "
                f"{deny and deny[2].reason}, {len(denied_mnt)} MAINTENANCE request(s)"))
    exit_ = _action(ctx, "technician exits")
    out.append(("approved removal enters maintenance only after FM approval, then exits",
                approve is not None and approve[2].ok and first is not None
                and first >= approve[0] and exit_ is not None and exit_[2].ok,
                f"approved t={approve and approve[0]:.0f}, MAINTENANCE requested t={first}, "
                f"exit {exit_ and exit_[2].reason}"))

    # waiting calls of the faulted car (FR-04 is Part 2's; SIM-06 checks it)
    waiting = log["waiting_at_fault"]
    delivered = [cid for cid in waiting if sim.passengers[cid].alighted_at is not None]
    trips = {e.call_id: e for e in bus.of_type(TripCompleted)}
    clock_kept = [cid for cid in waiting if cid in trips
                  and trips[cid].created_at == sim.passengers[cid].arrived_at]
    out.append(("waiting calls of the faulted car retained, wait clock (created_at) kept",
                bool(waiting) and len(delivered) == len(waiting)
                and len(clock_kept) == len(waiting),
                f"{len(waiting)} waiting at fault, {len(delivered)} delivered, "
                f"{len(clock_kept)} trip records with original created_at"))
    moved_off = {e.call_id for e in bus.of_type(CallReassigned)
                 if e.from_car == car and e.call_id in waiting}
    blocked = _dispatcher_is_double(ctx)
    out.append(("waiting calls re-assigned away from the faulted car (FR-04, Part 2)",
                bool(waiting) and len(moved_off) == len(waiting) and not blocked,
                ("BLOCKED: Part 2 dispatcher is a stub/double; " if blocked else "")
                + f"{len(moved_off)} of {len(waiting)} re-assigned"))
    degraded = [cid for cid in waiting if cid in trips and trips[cid].degraded]
    out.append(("degraded waits logged for the affected calls",
                bool(waiting) and len(degraded) == len(waiting),
                f"{len(degraded)} of {len(waiting)} trip records degraded"
                + (" (Part 6 marks degraded only on CallReassigned)" if not degraded else "")))
    affected = _waits([sim.passengers[c] for c in waiting], ctx.clock.now())
    in_metrics = all(sim.passengers[c].wait_s is not None for c in waiting) and \
        result.metrics["max_wait_s"] >= max(affected, default=0.0) - 1e-9
    out.append(("affected calls' long waits stay in the run metrics (not dropped)",
                bool(waiting) and in_metrics,
                _wait_summary(affected) + f"; run max wait {result.metrics['max_wait_s']:.1f} s"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out
