"""Part 4 partial acceptance checks; imported only when a Safety scenario runs."""
from __future__ import annotations

from ems.contracts import events as safety_events
from ems.contracts.events import AlertRaised, CallAssigned, CallRequested, CarStatusUpdated
from ems.contracts.types import CarState
from ems.sim.baseline import install_dispatch_standin
from ems.sim.faults import measured
from ems.sim.harness import RunResult
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip

from . import scenario_power

Criteria = list  # of (criterion, passed, detail)


def setup_sim_14(ctx):
    return scenario_power.setup_sim_14(ctx)


def check_sim_14(result, ctx):
    return scenario_power.check_sim_14(result, ctx)


# ---------------------------------------------------------------- Part 4 checks
def _waiting_on(ctx, incident):
    """Reconstruct waiting assignments at the incident; boarded riders never qualify."""
    waiting, assigned = set(), {}
    for event in ctx.bus.log:
        if event is incident:
            break
        if isinstance(event, CallRequested):
            waiting.add(event.call.call_id)
        elif isinstance(event, CallAssigned):
            assigned[event.call_id] = event.car_id
        elif isinstance(event, safety_events.CallReassigned):
            assigned[event.call_id] = event.to_car
        elif isinstance(event, (safety_events.PassengerBoarded, safety_events.CallCancelled)):
            waiting.discard(event.call_id)
    return {call for call in waiting if assigned.get(call) == incident.car_id}


def _reassignment_check(ctx, incidents):
    affected = set()
    delivered = set()
    for incident in incidents:
        waiting = _waiting_on(ctx, incident)
        affected |= waiting
        delivered |= {e.call_id for e in ctx.bus.of_type(safety_events.CallReassigned)
                      if e.call_id in waiting and e.from_car == incident.car_id
                      and e.to_car != incident.car_id and incident.t <= e.t <= incident.t + 10}
    boarded = set()
    moved_boarded = set()
    for event in ctx.bus.log:
        if isinstance(event, safety_events.PassengerBoarded):
            boarded.add(event.call_id)
        elif isinstance(event, safety_events.CallReassigned) and event.call_id in boarded:
            moved_boarded.add(event.call_id)
    return ("waiting calls reassigned without reassigning boarded passengers",
            bool(affected) and affected <= delivered and not moved_boarded,
            (f"{len(delivered & affected)}/{len(affected)} waiting calls; "
             f"{len(moved_boarded)} boarded calls reassigned; requires Part 2"))


def _setup_safety_fire(ctx):
    install_dispatch_standin(ctx)
    states = ctx.services["car_states"]
    states.request("B", CarState.MAINTENANCE, "scenario:maintenance")
    states.request("C", CarState.INDEPENDENT_SERVICE, "scenario:independent")


def _setup_safety_brake(ctx):
    ctx.services["safety_brake_evidence"] = {}

    def inject():
        sim = ctx.services["controllers"]
        dispatcher = ctx.get("dispatcher")
        occupied = [s for s in sim.all_status()
                    if s.passengers > 0 and not s.fault_code and s.position != s.floor
                    and s.car_id in ctx.get("car_states").dispatchable(s.group)
                    and s.passengers < ctx.get("building").group(s.group).rated_persons]
        if not occupied:
            if ctx.clock.now() < measured(21):
                ctx.clock.call_later(0.25, inject)
            return  # Full checker fails without real occupied/waiting preconditions.
        car = occupied[0].car_id
        if not dispatcher.waiting.get(car):
            spec = ctx.get("building").group(occupied[0].group)
            states = ctx.get("car_states")
            held = [other for other in spec.cars if other != car
                    and states.request(other, CarState.MAINTENANCE, "scenario:brake_precondition")]
            origin = max(spec.floors) if occupied[0].position < max(spec.floors) - 1 else min(spec.floors)
            destination = min(spec.floors) if origin == max(spec.floors) else max(spec.floors)
            ScriptedTraffic(ctx, [ScriptedTrip(ctx.clock.now(), origin, destination,
                card="staff-fm-1", accessible=False, vip=False,
                passenger_id="sim15-waiting-precondition")]).start()

            def after_admission():
                for other in held:
                    states.release(other, CarState.MAINTENANCE, "scenario:brake_precondition")
                complete(car)
            ctx.clock.call_later(0.001, after_admission)
            return
        complete(car)

    def complete(car):
        sim, dispatcher = ctx.get("controllers"), ctx.get("dispatcher")
        status = sim.status(car)
        if not (status.passengers > 0 and status.position != status.floor
                and dispatcher.waiting.get(car)):
            return  # The checker rejects incomplete actual preconditions.
        ctx.services["safety_brake_evidence"].update(
            t=ctx.clock.now(), car=car, position=status.position,
            passengers=status.passengers,
            waits={cid: dispatcher.calls[cid].call.created_at
                   for cid in dispatcher.waiting[car]},
        )
        sim.inject_fault(car, "brake", between_floors=True)
        ctx.clock.call_later(300, lambda: sim.clear_fault(car))
    ctx.clock.call_later(measured(20), inject)


def _fire_goods_check(ctx, alarm) -> tuple[str, bool, str]:
    """Part 4 consumes the approved Part 5 terminal event; it never clears owners' holds."""
    active = [e for e in ctx.bus.of_type(safety_events.GoodsMoveScheduled)
              if e.t <= alarm.t and e.start <= alarm.t < e.end]
    ended = ctx.bus.of_type(safety_events.GoodsMoveEnded)
    cancelled = []
    for move in active:
        terminal = [e for e in ended if e.move_id == move.move_id and e.car_id == move.car_id]
        restarts = [c for c in ctx.services["controllers"].command_log
                    if c[1] == move.car_id and c[2] == "set_independent_service"
                    and c[3] == (True,) and c[0] >= alarm.t]
        reentries = [e for e in ctx.bus.of_type(safety_events.CarStateChanged)
                     if e.car_id == move.car_id and e.new is CarState.INDEPENDENT_SERVICE
                     and e.t > alarm.t]
        cancelled.append(len(terminal) == 1 and terminal[0].cancelled
                         and terminal[0].reason == "emergency:fire" and terminal[0].t == alarm.t
                         and not restarts and not reentries)
    return ("active goods move cancelled without automatic restart",
            bool(active) and all(cancelled),
            f"{sum(cancelled)}/{len(active)} active moves verified using GoodsMoveEnded"
            if active else "BLOCKED: no active goods move exercised by this scenario setup")


def check_sim_08(result: RunResult, ctx) -> Criteria:
    alarms = ctx.bus.of_type(safety_events.FireAlarm)
    clears = ctx.bus.of_type(safety_events.FireAlarmCleared)
    states = ctx.services["car_states"]
    sim = ctx.services["controllers"]
    recovery = states.recovery_log
    start = alarms[0].t if alarms else float("inf")
    end = recovery[0][0] if recovery else ctx.clock.now() + 1
    commands = [c for c in sim.command_log if start <= c[0] < end]
    return [
        ("fire at 17:00 Rush transition", bool(alarms) and start == measured(20)
         and result.start.endswith("16:30:00"), f"alarm t={start}"),
        ("zero EMS controller commands throughout fire handling", bool(alarms) and not commands,
         f"{len(commands)} attempted (including refused)"),
        ("healthy recovery requires explicit FM confirmation", bool(clears) and bool(recovery)
         and end >= clears[-1].t, "FM UI/controller-normal integration absent" if not recovery
         else f"confirmed at {end}"),
        ("isolated cars remain excluded", states.state("B") is not CarState.GROUP_SERVICE
         and states.state("C") is not CarState.GROUP_SERVICE
         and not ({"B", "C"} & set(states.dispatchable("LOW"))), "Maintenance B; Independent C"),
        (_fire_goods_check(ctx, alarms[0]) if alarms else
         ("active goods move cancelled without automatic restart", False, "no fire exercised")),
    ]


def check_sim_13(result: RunResult, ctx) -> Criteria:
    overloads = ctx.bus.of_type(safety_events.OverloadDetected)
    messages = ctx.bus.of_type(safety_events.PanelMessage)
    prompt = all(any(m.target == f"car:{o.car_id}" and o.t <= m.t <= o.t + 1
                         and "step" in m.text_en.lower() for m in messages) for o in overloads)
    bulky = [e for e in ctx.bus.of_type(safety_events.CameraEvent) if e.kind == "bulky_item"]
    reports = ctx.services.get("reports")
    incidents = getattr(reports, "incidents", {})
    flagged = len(bulky) == 2 and all(e.event_id in incidents for e in bulky)
    return [
        ("at least three overload bursts exercised", len(overloads) >= 3,
         f"{len(overloads)} overloads"),
        ("step-out message within 1 s", bool(overloads) and prompt,
         "Part 2 owns overload messages and next-car selection"),
        _reassignment_check(ctx, overloads),
        ("both bulky items flagged by Monitor", flagged, f"{len(bulky)} bulky events"),
    ]


def check_sim_15(result: RunResult, ctx) -> Criteria:
    faults = [e for e in ctx.bus.of_type(safety_events.ControllerFault)
              if e.code == "brake" and e.between_floors]
    alerts = ctx.bus.of_type(AlertRaised)
    statuses = ctx.bus.of_type(CarStatusUpdated)
    qualified = []
    for fault in faults:
        before = [s.status for s in statuses if s.status.car_id == fault.car_id and s.t <= fault.t]
        if before and before[-1].passengers > 0:
            qualified.append(fault)
    timely = all(any(a.car_id == f.car_id and a.kind == "entrapment"
                     and a.severity == "critical" and "contractor" in a.recipients
                     and f.t <= a.t <= f.t + 10 for a in alerts) for f in qualified)
    commands = []
    for fault in faults:
        clear = next((e.t for e in ctx.bus.of_type(safety_events.ControllerFaultCleared)
                      if e.car_id == fault.car_id and e.t >= fault.t), ctx.clock.now() + 1)
        commands += [c for c in ctx.services["controllers"].command_log
                     if c[1] == fault.car_id and fault.t <= c[0] < clear]
    proof = ctx.services.get("safety_brake_evidence", {})
    waits = proof.get("waits", {})
    moved = {e.call_id for e in ctx.bus.of_type(safety_events.CallReassigned)
             if e.from_car == proof.get("car") and e.t >= proof.get("t", float("inf"))
             and e.to_car != proof.get("car")}
    boarded = set()
    invalid = []
    for event in ctx.bus.log:
        if isinstance(event, safety_events.PassengerBoarded):
            boarded.add(event.call_id)
        elif isinstance(event, safety_events.CallReassigned) and event.call_id in boarded:
            invalid.append(event.call_id)
    retained = bool(waits) and waits.keys() <= moved and not invalid and all(
        ctx.get("dispatcher").calls[cid].call.created_at == t for cid, t in waits.items())
    inbox = ctx.get("notifier").inbox
    delivered = bool(qualified) and all(any(
        f"entrapment car {f.car_id}" in n[2] and f.t <= n[0] <= f.t + 10
        and {"security", "contractor"} <= set(n[1]) for n in inbox) for f in qualified)
    return [
        ("occupied between-floor brake fault exercised", bool(qualified), f"{len(qualified)} faults"),
        ("urgent entrapment alert and contractor call-out within 10 s", bool(qualified) and timely,
         f"{len(qualified)} occupied faults checked"),
        ("waiting calls reassigned without reassigning boarded passengers", retained,
         f"{len(set(waits) & moved)}/{len(waits)} original waits; boarded moved={invalid}"),
        ("actual moving occupied precondition and delivered urgent notification",
         proof.get("passengers", 0) > 0 and proof.get("position", 0) % 1 != 0 and delivered,
         f"car={proof.get('car')}; delivered={delivered}"),
        ("no EMS command to the faulted car", bool(faults) and not commands,
         f"{len(commands)} attempted commands"),
    ]


def setup_sim_17(ctx):
    from .scenario_admission import setup_sim_17 as setup
    return setup(ctx)


def check_sim_17(result, ctx):
    from .scenario_admission import check_sim_17 as check
    return check(result, ctx)
