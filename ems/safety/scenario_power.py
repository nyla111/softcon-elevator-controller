"""Full SIM-14 setup/check using real services and observable recovery outcomes."""
from __future__ import annotations

from ems.contracts import events as E
from ems.contracts.types import CarState, Role
from ems.sim.faults import measured
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip

LOSS = measured(20)
RESTORE = LOSS + 300
CONFIRM = RESTORE + 121


def setup_sim_14(ctx):
    sim, safety = ctx.get("controllers"), ctx.get("car_states")
    evidence = ctx.services.setdefault("safety_acceptance", {})["SIM-14"] = {"attempts": []}
    sim.inject_fault("B", "scenario:isolation")

    def lose():
        occupied = [s for s in sim.all_status() if s.passengers > 0 and s.fault_code is None]
        car = occupied[0].car_id if occupied else None
        evidence["car"] = car
        evidence["occupied_at_loss"] = bool(occupied)
        evidence["original_waits"] = {
            cid: rec.call.created_at for cid, rec in ctx.get("dispatcher").calls.items()
            if rec.state in ("pending", "assigned")
        }
        ctx.get("devices").power.lose_mains(
            generator_failed=True, failed_rescue_cars=(car,) if car else (),
        )

    def trapped():
        car = evidence.get("car")
        evidence["occupied_before_restore"] = bool(car and sim.status(car).passengers > 0)

    def confirm():
        evidence["attempts"].append((ctx.clock.now(), safety.confirm_recovery(Role.FM)))

    ctx.clock.call_at(LOSS, lose)
    ctx.clock.call_at(RESTORE - 0.001, trapped)
    ctx.clock.call_at(RESTORE, ctx.get("devices").power.restore_mains)
    ctx.clock.call_at(RESTORE + 1, confirm)
    ctx.clock.call_at(RESTORE + 119, confirm)
    ctx.clock.call_at(RESTORE + 120, lambda: evidence.update(
        stable_boundary=safety.recovery_eligible() and safety.safety_active(),
        recovery_status=safety.recovery_status(),
    ))
    ctx.clock.call_at(CONFIRM, confirm)
    ScriptedTraffic(ctx, [ScriptedTrip(CONFIRM + 1, 1, 5, card="card-L5-001",
                                      accessible=False, vip=False,
                                      passenger_id="sim14-post-confirm")]).start()


def check_sim_14(result, ctx):
    safety, sim = ctx.get("car_states"), ctx.get("controllers")
    evidence = ctx.services.get("safety_acceptance", {}).get("SIM-14", {})
    losses, restores = ctx.bus.of_type(E.MainsLost), ctx.bus.of_type(E.MainsRestored)
    loss = losses[0].t if losses else float("inf")
    rescued = {r.car_id for r in ctx.bus.of_type(E.RescueReport)
               if loss <= r.t <= loss + 60 and r.status == "rescued_at_floor"}
    missing = set(safety.table.cars()) - rescued
    warnings = [a for a in ctx.bus.of_type(E.AlertRaised)
                if a.kind == "entrapment" and "POSSIBLE ENTRAPMENT" in a.message
                and loss <= a.t <= loss + 60]
    warned = {a.car_id for a in warnings}
    recovery = safety.recovery_log
    end = recovery[0][0] if recovery else ctx.clock.now() + 1
    commands = [c for c in sim.command_log if loss <= c[0] < end]
    assignments = [e for e in ctx.bus.of_type(E.CallAssigned) if loss <= e.t < end]
    received = [n for n in ctx.get("notifier").inbox
                if loss <= n[0] <= loss + 60 and "entrapment" in n[2]]
    inbox_ok = all(any(f"car {car}" in n[2] and {"security", "fm", "contractor"} <= set(n[1])
                      and all(key in n[3] for key in ("floor=", "load=", "telemetry_t="))
                      for n in received) for car in missing)
    attempts = evidence.get("attempts", [])
    expected = [(RESTORE + 1, False), (RESTORE + 119, False), (CONFIRM, True)]
    status = evidence.get("recovery_status", {})
    post = sim.passengers.get("sim14-post-confirm")
    original = evidence.get("original_waits", {})
    preserved = bool(original) and all(ctx.get("dispatcher").calls[c].call.created_at == t
                                        for c, t in original.items())
    return [
        ("failed generator and exactly one rescue report missing",
         len(losses) == 1 and missing == {evidence.get("car")}
         and not ctx.bus.of_type(E.GeneratorReady), f"missing={sorted(missing)}"),
        ("occupied silent-rescue car remains trapped until restoration",
         evidence.get("occupied_at_loss", False) and evidence.get("occupied_before_restore", False),
         f"car={evidence.get('car')}"),
        ("POSSIBLE ENTRAPMENT for every missing rescue by 60 s",
         bool(missing) and missing <= warned and inbox_ok,
         f"warned={sorted(warned)}; delivered context={inbox_ok}"),
        ("no automatic healthy recovery", bool(restores) and not commands and not assignments,
         f"{len(commands)} attempted commands; {len(assignments)} assignments"),
        ("FM-confirmed recovery after 120 s continuous mains",
         attempts == expected and evidence.get("stable_boundary", False)
         and status.get("safety_inputs_clear") is True
         and not status.get("missing_controller_reports", ("missing",))
         and recovery == [(CONFIRM, "fm_confirmed")], str(attempts)),
        ("faulted car remains isolated after recovery",
         safety.state("B") is CarState.FAULTED and "B" not in safety.dispatchable("LOW")
         and not [c for c in sim.command_log if c[1] == "B"], "B isolated throughout"),
        ("original waiting clocks retained and post-confirm traffic delivered",
         preserved and post is not None and post.alighted_at is not None
         and not post.evacuated and post.boarded_at >= CONFIRM,
         f"{len(original)} original waits; post-confirm={post is not None}"),
    ]
