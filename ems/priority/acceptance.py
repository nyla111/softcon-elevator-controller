"""Part 5 scenario setups and checks: SIM-06, SIM-11, SIM-12, SIM-18 (Appendix B).

Wired into the shared registry (ems/sim/scenarios.py). Each check returns
[(criterion, passed, detail)] computed from observable evidence of the finished run: the bus
log, the simulator's passengers and command audit, and Part 5's own audit. Nothing is set by
the setup and then read back as a pass.

Every check also reports whether the run used real Part 2 / 3 / 4 services. At the base commit
they are stubs, so the setups install the doubles of standins.py and the criterion
"integration with real Part 2/3/4 services" FAILS with the reason "BLOCKED: ...". A criterion
whose evidence only Part 2 can produce (e.g. FR-04 reassignment) also fails as BLOCKED while
the stand-in dispatcher is in use. A scenario therefore cannot PASS on stand-ins.

Operator actions (technician, FM, security) are scripted through the public Part 5 API at fixed
measured minutes; the times are scenario choices, not SRS values.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (AlertRaised, CallAssigned, CallCancelled, CallReassigned,
                                  CallRequested,
                                  CarStatusUpdated, ControllerFault, ControllerFaultCleared,
                                  DoorsOpened, PanelMessage, PassengerAlighted,
                                  PassengerBoarded, PriorityTripEnded, PriorityTripStarted,
                                  TripCompleted)
from ems.contracts.types import CallPriority, CarState, DoorState, Mode
from ems.sim.faults import car_fault, measured, schedule_fault
from ems.sim.passengers import PassengerState

from .standins import (VipReplacementPopulation, install_scenario_standins,
                       integration_gaps)

RUSH_AVG_S, RUSH_CEILING_S = config.MODE_TARGETS[Mode.RUSH]
MOVE_COMMANDS = ("register_stop", "express", "park")
TECH, FM, SECURITY = "staff-tech-1", "staff-fm-1", "staff-security-1"


# ---------------------------------------------------------------- shared helpers
def _service(ctx):
    return ctx.services["priority"]


def _log(ctx) -> dict:
    return ctx.services.setdefault("priority_scenario", {"actions": []})


def _act(ctx, name: str, fn) -> None:
    out = fn()
    _log(ctx)["actions"].append((ctx.clock.now(), name, out))


def _action(ctx, name: str):
    return next((a for a in _log(ctx)["actions"] if a[1] == name), None)


def _integration(ctx) -> tuple:
    gaps = integration_gaps(ctx)
    return ("integration with real Part 2/3/4 services (not stand-ins)", not gaps,
            "BLOCKED: " + "; ".join(gaps) if gaps else "real services")


def _dispatcher_is_double(ctx) -> bool:
    return type(ctx.services["dispatcher"]).__name__ in ("StubDispatcher",
                                                         "ScheduleModeDispatcher")


def _cleared(result) -> tuple:
    n = result.metrics["calls_not_cleared"]
    return ("all calls cleared (none lost)", n == 0, f"{n} not cleared")


def _measured(result, sim) -> list:
    a = result.params["warmup_s"]
    b = a + result.params["measure_s"]
    return [p for p in sim.passengers.values() if a <= p.arrived_at < b]


def _waits(people) -> list[float]:
    return [p.wait_s for p in people if p.wait_s is not None and not p.evacuated]


def _wait_summary(waits: list[float]) -> str:
    if not waits:
        return "no delivered calls"
    over = sum(1 for w in waits if w > RUSH_CEILING_S)
    return (f"n={len(waits)} avg={sum(waits) / len(waits):.1f} s max={max(waits):.1f} s, "
            f"{over} over {RUSH_CEILING_S} s")


def _group_of(ctx, car_id) -> Optional[str]:
    return _service(ctx).group_of_car(car_id)


def _max_exclusive_per_group(ctx) -> tuple[int, str]:
    """Sweep PriorityTripStarted / Ended in bus order: most exclusive cars at once per group."""
    active: dict[str, set] = defaultdict(set)
    worst, where = 0, "-"
    for ev in ctx.bus.log:
        if isinstance(ev, PriorityTripStarted):
            g = _group_of(ctx, ev.car_id)
            active[g].add(ev.car_id)
            if len(active[g]) > worst:
                worst, where = len(active[g]), f"group {g} at t={ev.t:.0f}"
        elif isinstance(ev, PriorityTripEnded):
            active[_group_of(ctx, ev.car_id)].discard(ev.car_id)
    return worst, where


def _max_vip_starts_per_rolling_hour(ctx) -> int:
    starts = [e.t for e in ctx.bus.of_type(PriorityTripStarted) if e.kind == "vip"]
    return max((sum(1 for s in starts if t - 3600.0 < s <= t) for t in starts), default=0)


# ================================================================ SIM-06 (FR-04, FR-33)
# The faulted car is chosen when the fault happens: the car of the first passenger group with the
# most calls assigned and not yet picked up (so "waiting calls retained" is really exercised).
# The two cars of the second-removal requests are the next free cars of the same group.
SIM06_FAULT_AT, SIM06_FAULT_S, SIM06_CODE = measured(20), 300.0, "F-06"
SIM06_ASK_EXPIRE, SIM06_ASK_APPROVE = measured(22), measured(23)
SIM06_APPROVE, SIM06_EXIT, SIM06_LATE = measured(24), measured(29), measured(33)


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
                bool(gate) and len(fm_alerts) == 2 and all(a.recipients == ["fm"]
                                                           for a in fm_alerts),
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
    affected = [sim.passengers[c].wait_s for c in waiting
                if sim.passengers[c].wait_s is not None]
    out.append(("degraded waits reported (affected calls)", True,
                _wait_summary(affected) if affected else "no affected calls"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out


# ================================================================ SIM-11 (FR-08, BR-10)
def setup_sim_11(ctx) -> None:
    install_scenario_standins(ctx)


def _statuses_at_assignment(ctx) -> dict:
    """call_id -> CarStatus of the assigned car, as last reported before the assignment."""
    last, out = {}, {}
    for e in ctx.bus.log:
        if isinstance(e, CarStatusUpdated):
            last[e.status.car_id] = e.status
        elif isinstance(e, CallAssigned) and e.call_id not in out:
            out[e.call_id] = last.get(e.car_id)
    return out


def _door_open_s(ctx) -> dict:
    """(car, DoorsOpened t) -> seconds until telemetry first shows the doors not open."""
    opened, out = {}, {}
    for e in ctx.bus.log:
        if isinstance(e, DoorsOpened):
            opened[e.car_id] = e.t
        elif isinstance(e, CarStatusUpdated) and e.status.car_id in opened \
                and e.status.doors in (DoorState.CLOSING, DoorState.CLOSED):
            t0 = opened.pop(e.status.car_id)
            out[(e.status.car_id, t0)] = e.t - t0
    return out


def check_sim_11(result, ctx) -> list:
    bus, sim, p = ctx.bus, ctx.services["controllers"], _service(ctx)
    calls = {e.call.call_id: e.call for e in bus.of_type(CallRequested)}
    acc = {cid for cid, c in calls.items() if c.priority is CallPriority.ACCESSIBLE}
    out = [("accessible calls mixed into up-peak", bool(acc), f"{len(acc)} accessible calls")]

    # FR-08 voice feedback
    assigned = {(e.call_id, e.car_id) for e in bus.of_type(CallAssigned) if e.call_id in acc}
    voice = [m for m in bus.of_type(PanelMessage) if m.voice and "is coming for you" in m.text_en]
    out.append(("voice feedback for every accessible assignment (one per call and car)",
                bool(assigned) and assigned <= p.announced and len(voice) == len(p.announced),
                f"{len(assigned & p.announced)} of {len(assigned)} assignments announced, "
                f"{len(voice)} voice messages"))

    # FR-08 free capacity
    st = _statuses_at_assignment(ctx)
    rated = {c: g.rated_persons for g in ctx.services["building"].groups for c in g.cars}
    full = [cid for cid in acc if st.get(cid) is not None
            and rated[st[cid].car_id] - st[cid].passengers < calls[cid].party_size]
    out.append(("accessible calls assigned to a car with free capacity",
                bool(st) and not full, f"{len(full)} assigned to a car without room"))
    boarded = {e.call_id for e in bus.of_type(PassengerBoarded) if e.call_id in acc}
    out.append(("accessible passengers boarded", len(boarded) == len(acc),
                f"{len(boarded)} of {len(acc)} boarded"))

    # FR-08 door time: simulator behaviour vs. an EMS request
    last_open, acc_cycles = {}, set()
    for e in bus.log:
        if isinstance(e, DoorsOpened):
            last_open[e.car_id] = e.t
        elif isinstance(e, (PassengerBoarded, PassengerAlighted)) and e.call_id in acc \
                and e.car_id in last_open:
            acc_cycles.add((e.car_id, last_open[e.car_id]))
    times = _door_open_s(ctx)
    observed = [times[k] for k in acc_cycles if k in times]
    out.append(("doors open >= 10 s on every door cycle with an accessible passenger "
                "(observed in 1 s telemetry; provided by the simulated controller)",
                bool(observed) and min(observed) >= config.SIM["accessible_dwell_s"],
                f"{len(observed)} cycles, shortest {min(observed, default=0):.1f} s"))
    out.append(("EMS requests the >= 10 s door time", False,
                "BLOCKED: ControllerAdapter has no door-time command (CCR-02); Part 5 exposes "
                "door_dwell_s(call) only"))
    out.append(("priority order medical > accessible > VIP > ordinary applied by the dispatcher",
                not _dispatcher_is_double(ctx),
                "BLOCKED: Part 2 dispatcher is a stub/double (Part 5 provides rank()/order())"
                if _dispatcher_is_double(ctx) else "real Part 2 dispatcher"))

    measured_people = _measured(result, sim)
    waits = _waits(measured_people)
    acc_waits = _waits([q for q in measured_people if q.passenger_id in acc])
    avg = sum(waits) / len(waits) if waits else 0.0
    out.append((f"Rush wait targets hold (average <= {RUSH_AVG_S} s, none over "
                f"{RUSH_CEILING_S} s), every measured call counted",
                bool(waits) and avg <= RUSH_AVG_S and max(waits) <= RUSH_CEILING_S,
                f"all: {_wait_summary(waits)}; accessible: {_wait_summary(acc_waits)}"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out


# ================================================================ SIM-12 (FR-71, FR-72, FR-76)
SIM12_MINUTES = range(20, 30)


def setup_sim_12(ctx) -> None:
    install_scenario_standins(ctx)
    group = ctx.services["building"].passenger_groups[0]
    windows = [(measured(m), measured(m + 1)) for m in SIM12_MINUTES]
    ctx.services["population"] = VipReplacementPopulation(
        ctx, ctx.services["population"], group, windows)


def check_sim_12(result, ctx) -> list:
    bus, sim, p = ctx.bus, ctx.services["controllers"], _service(ctx)
    pop = ctx.services["population"]
    group = pop.group
    out = []
    replaced = pop.replaced
    ok = (len(replaced) == len(SIM12_MINUTES)
          and all(sim.passengers[c].vip and not sim.passengers[c].accessible
                  and sim.passengers[c].group == group for c in replaced))
    out.append((f"10 ordinary arrivals of group {group} replaced by VIP arrivals at minutes "
                "20-29 (no traffic added)", ok, f"{len(replaced)} replaced: {replaced}"))
    decided = {d["call_id"]: d for d in p.decisions}
    out.append(("Part 5 decided every replaced VIP call",
                all(c in decided for c in replaced),
                ", ".join(f"{c}:{decided[c]['reason']}" for c in replaced if c in decided)))
    worst, where = _max_exclusive_per_group(ctx)
    out.append(("at most one exclusive car per group at any time", worst <= 1,
                f"max {worst} ({where})"))
    per_hour = _max_vip_starts_per_rolling_hour(ctx)
    starts = [e for e in bus.of_type(PriorityTripStarted) if e.kind == "vip"]
    out.append(("at most six exclusive VIP starts per rolling hour",
                per_hour <= config.VIP_MAX_PER_HOUR,
                f"max {per_hour} in any hour, {len(starts)} starts in the run"))
    checked = all(any(c[0] == s.t and c[1] == s.car_id and c[2] is False
                      for c in p.ceiling_checks) for s in starts)
    out.append(("FR-72 wait-ceiling check asked before every exclusive start",
                bool(starts) and checked,
                f"{len(p.ceiling_checks)} checks"
                + (" (answers from the Part 2 stub: always False)"
                   if _dispatcher_is_double(ctx) else "")))
    vip_calls = [q for q in sim.passengers.values() if q.vip and not q.accessible]
    lost = [q.passenger_id for q in vip_calls if q.alighted_at is None or q.evacuated]
    out.append(("every VIP call delivered (exclusive or as an ordinary call)", not lost,
                f"{len(vip_calls) - len(lost)} of {len(vip_calls)} delivered"))
    ordinary = [q for q in _measured(result, sim)
                if q.group == group and not q.vip and not q.accessible]
    waits = _waits(ordinary)
    out.append((f"ordinary calls of group {group} within the {RUSH_CEILING_S} s ceiling",
                bool(waits) and max(waits) <= RUSH_CEILING_S, _wait_summary(waits)))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out


# ================================================================ SIM-18 (FR-127, BR-10)
SIM18_AFTER = measured(20)
SIM18_HANDLING_S = 30.0          # operator time to load / hand over the patient (scenario choice)


def setup_sim_18(ctx) -> None:
    install_scenario_standins(ctx)
    p, log = _service(ctx), _log(ctx)

    def on_boarded(ev: PassengerBoarded) -> None:
        trip = p.trip_of_call(ev.call_id)
        if "vip_boarded" in log or ev.t < SIM18_AFTER or trip is None or trip.kind != "vip" \
                or trip.car_id != ev.car_id:
            return
        log["vip_boarded"] = (ev.t, ev.call_id, ev.car_id, trip.trip_id)
        floors = ctx.services["building"].tenant_floors[trip.group]
        origin = floors[len(floors) // 2]
        lobby = ctx.services["building"].lobby
        # after this event's handlers have run (the VIP's express is sent first)
        ctx.clock.call_later(0.0, lambda: _act(
            ctx, "medical", lambda: p.request_medical(SECURITY, origin, lobby)))

    def on_doors(ev: DoorsOpened) -> None:
        med = _action(ctx, "medical")
        if med is None or not med[2].ok or ev.car_id != med[2].car_id:
            return
        trip = p.trips.get(med[2].ref)
        if trip is None:
            return
        if ev.floor == trip.origin and "departed" not in log:
            log["departed"] = True
            ctx.clock.call_later(SIM18_HANDLING_S, lambda: _act(
                ctx, "depart", lambda: p.medical_depart(SECURITY, trip.trip_id)))
        elif ev.floor == trip.destination and trip.boarded and "finished" not in log:
            log["finished"] = True
            ctx.clock.call_later(SIM18_HANDLING_S, lambda: _act(
                ctx, "finish", lambda: p.finish_trip(SECURITY, trip.trip_id)))

    ctx.bus.subscribe(PassengerBoarded, on_boarded)
    ctx.bus.subscribe(DoorsOpened, on_doors)


def check_sim_18(result, ctx) -> list:
    bus, sim, p, log = ctx.bus, ctx.services["controllers"], _service(ctx), _log(ctx)
    vb, med = log.get("vip_boarded"), _action(ctx, "medical")
    if vb is None or med is None:
        return [("an occupied VIP trip exists after minute 20 to start the medical trip",
                 False, "no exclusive VIP boarding after minute 20 in this run"),
                _cleared(result), _integration(ctx)]
    t_board, vip_call, vip_car, vip_trip = vb
    t_med, _, outcome = med
    out = [("medical starts after a real VIP PassengerBoarded, during the VIP trip",
            t_med >= t_board and outcome.ok,
            f"VIP {vip_call} boarded car {vip_car} t={t_board:.1f}; medical t={t_med:.1f} "
            f"-> {outcome.reason}, car {outcome.car_id}")]
    if not outcome.ok:
        return out + [_cleared(result), _integration(ctx)]
    car = outcome.car_id
    ended = [t for t in p.finished if t.trip_id == vip_trip]
    out.append(("VIP exclusivity yields to the medical trip",
                bool(ended) and ended[0].end_reason == "medical_preempt",
                f"VIP trip end: {ended[0].end_reason if ended else 'still active'}"))
    worst, where = _max_exclusive_per_group(ctx)
    out.append(("never more than one exclusive car per group", worst <= 1,
                f"max {worst} ({where})"))
    vip = sim.passengers[vip_call]
    out.append(("boarded VIP delivered to their own destination",
                vip.alighted_at is not None and not vip.evacuated,
                f"alighted t={vip.alighted_at} at destination L{vip.destination}"))
    riders = {e.call_id for e in bus.of_type(PassengerBoarded) if e.car_id == car and e.t < t_med}
    riders -= {e.call_id for e in bus.of_type(PassengerAlighted) if e.t < t_med}
    bad = [c for c in riders if sim.passengers[c].alighted_at is None
           or sim.passengers[c].evacuated
           or sim.passengers[c].state is not PassengerState.DONE]
    out.append(("riders already in the medical car delivered to their own destinations",
                not bad, f"{len(riders)} rider(s) on board at selection, {len(bad)} not delivered"))
    faulted = [e for e in bus.of_type(ControllerFault) if e.car_id == car and e.t >= t_med]
    trip = next((t for t in p.finished if t.car_id == car and t.kind == "medical"), None)
    reached = [e for e in bus.of_type(DoorsOpened) if e.car_id == car and e.t >= t_med]
    at_origin = next((e.t for e in reached if trip and e.floor == trip.origin), None)
    at_dest = next((e.t for e in reached if trip and e.floor == trip.destination
                    and at_origin is not None and e.t > at_origin), None)
    out.append(("a healthy car performs the medical trip to the end",
                not faulted and trip is not None and trip.end_reason == "completed"
                and at_origin is not None and at_dest is not None,
                f"car {car}: doors open at origin after "
                f"{(at_origin - t_med) if at_origin else float('nan'):.1f} s, "
                f"destination t={at_dest}, end {trip.end_reason if trip else 'active'}"))
    vip_end = [e for e in bus.of_type(PriorityTripEnded)
               if e.kind == "vip" and e.car_id == vip_car and e.t >= t_board]
    med_start = [e for e in bus.of_type(PriorityTripStarted)
                 if e.kind == "medical" and e.t == t_med]
    out.append(("medical served above VIP: accepted at once while the VIP trip held the "
                "group's exclusive slot",
                bool(vip_end) and bool(med_start) and vip_end[0].t == t_med,
                f"VIP trip ended t={vip_end[0].t if vip_end else None}, medical started "
                f"t={med_start[0].t if med_start else None} (accessible > VIP ordering in "
                "dispatch is Part 2's, see SIM-11)"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out
