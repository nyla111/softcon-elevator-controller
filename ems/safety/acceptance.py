"""Part 4 partial acceptance checks; imported only when a Safety scenario runs."""
from __future__ import annotations

from ems.contracts import events as safety_events
from ems.contracts.events import AlertRaised, CallAssigned, CallRequested, CarStatusUpdated
from ems.contracts.types import CarState
from ems.sim.baseline import install_dispatch_standin
from ems.sim.faults import measured
from ems.sim.harness import RunResult

Criteria = list  # of (criterion, passed, detail)


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
    install_dispatch_standin(ctx)

    def inject():
        sim = ctx.services["controllers"]
        occupied = [s for s in sim.all_status() if s.passengers > 0 and not s.fault_code]
        if not occupied:
            return  # the check fails without an occupied fault; never invent passengers
        car = occupied[0].car_id
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


def setup_sim_13(ctx):
    from .scenario_overload import setup_sim_13 as setup
    return setup(ctx)


def check_sim_13(result, ctx):
    from .scenario_overload import check_sim_13 as check
    return check(result, ctx)


def check_sim_14(result: RunResult, ctx) -> Criteria:
    losses = ctx.bus.of_type(safety_events.MainsLost)
    restores = ctx.bus.of_type(safety_events.MainsRestored)
    reports = ctx.bus.of_type(safety_events.RescueReport)
    alerts = ctx.bus.of_type(AlertRaised)
    lost_at = losses[0].t if losses else float("inf")
    valid = {r.car_id for r in reports if r.status == "rescued_at_floor"
             and lost_at <= r.t <= lost_at + 60}
    cars = set(ctx.services["car_states"].table.cars())
    missing = cars - valid
    warned = {a.car_id for a in alerts if a.kind == "entrapment" and a.severity == "critical"
              and "POSSIBLE ENTRAPMENT" in a.message and lost_at <= a.t <= lost_at + 60}
    service = ctx.services["car_states"]
    recovery = service.recovery_log
    premature = [e for e in ctx.bus.of_type(safety_events.CarStateChanged)
                 if losses and e.t >= lost_at and e.new is CarState.GROUP_SERVICE
                 and (not recovery or e.t < recovery[0][0])]
    return [
        ("failed generator and exactly one rescue report missing", bool(losses) and len(missing) == 1
         and not ctx.bus.of_type(safety_events.GeneratorReady), f"missing={sorted(missing)}"),
        ("POSSIBLE ENTRAPMENT for every missing rescue by 60 s", bool(missing) and missing <= warned,
         f"warned={sorted(warned)}"),
        ("no automatic healthy recovery", bool(restores) and not premature,
         f"{len(premature)} premature state changes"),
        ("FM-confirmed recovery after 120 s continuous mains", bool(recovery) and bool(restores)
         and recovery[0][0] >= restores[-1].t + 120,
         "BLOCKED: no FM UI/controller-normal integration" if not recovery else str(recovery)),
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
    return [
        ("occupied between-floor brake fault exercised", bool(qualified), f"{len(qualified)} faults"),
        ("urgent entrapment alert and contractor call-out within 10 s", bool(qualified) and timely,
         f"{len(qualified)} occupied faults checked"),
        _reassignment_check(ctx, faults),
        ("no EMS command to the faulted car", bool(faults) and not commands,
         f"{len(commands)} attempted commands"),
    ]


def check_sim_17(result: RunResult, ctx) -> Criteria:
    # CallRequested is post-acceptance and has no action id / exemption / duplicate proof.
    # Local policy unit tests are not evidence that real panels enforce this scenario.
    return [("proven duplicates, uncertain retention, sixth-action limit and exemptions", False,
             ("BLOCKED: Access action admission and duplicate-proof contract absent; "
              "ems.safety.call_policy is independently tested but not connected"))]
