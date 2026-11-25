"""SIM-13 real lobby overload cycles with nonempty waiting and camera evidence."""
from ems.contracts import events as E
from ems.contracts.types import CarState
from ems.sim.faults import measured
from ems.sim.scripted import ScriptedTraffic, ScriptedTrip


def setup_sim_13(ctx):
    states, sim = ctx.get("car_states"), ctx.get("controllers")
    proof = ctx.services.setdefault("overload_acceptance", {"arrivals": []})
    states.request("A", CarState.MAINTENANCE, "scenario:lobby_overload")

    def burst(index):
        held = [car for car in "BCD"
                if states.request(car, CarState.MAINTENANCE, "scenario:lobby_precondition")]
        states.release("A", CarState.MAINTENANCE, "scenario:lobby_overload")
        assert sim.close_to_new_calls("A", False)
        ref = f"sim13-waiting-{index}"
        proof["arrivals"].append(ref)
        sim.overload_burst("A")
        ScriptedTraffic(ctx, [ScriptedTrip(ctx.clock.now(), 1, 15, card="staff-fm-1",
                                          accessible=False, vip=False, passenger_id=ref)]).start()
        # After this real pickup is admitted, reserve its controlled overload cycle.
        # Existing controller refusal semantics prevent background queue saturation.
        ctx.clock.call_later(0.0005, lambda: sim.close_to_new_calls("A", True))
        def release():
            for car in held:
                states.release(car, CarState.MAINTENANCE, "scenario:lobby_precondition")
        ctx.clock.call_later(0.001, release)
        ctx.clock.call_later(10, lambda: states.request(
            "A", CarState.MAINTENANCE, "scenario:lobby_overload"))

    for index, t in enumerate((measured(20), measured(22), measured(24))):
        ctx.clock.call_at(t + 0.1, lambda i=index: burst(i))
    ctx.clock.call_at(measured(25), lambda: states.release(
        "A", CarState.MAINTENANCE, "scenario:lobby_overload"))
    ctx.clock.call_at(measured(25), lambda: sim.close_to_new_calls("A", False))


def check_sim_13(result, ctx):
    from .acceptance import _reassignment_check, _waiting_on
    overloads = ctx.bus.of_type(E.OverloadDetected)
    planned = [event for event in overloads if event.car_id == "A"
               and measured(20) <= event.t <= measured(25)]
    waiting = [_waiting_on(ctx, event) for event in planned]
    messages = ctx.bus.of_type(E.PanelMessage)
    timely = all(any(m.target == f"car:{event.car_id}" and event.t <= m.t <= event.t + 1
                     and "step" in m.text_en.lower() and "Next car:" in m.text_en
                     and bool(m.text_vi) and m.voice for m in messages) for event in planned)
    bulky = [e for e in ctx.bus.of_type(E.CameraEvent) if e.kind == "bulky_item"]
    crowd = [e for e in ctx.bus.of_type(E.CameraEvent)
             if e.kind == "repeated_overload" and e.people_count >= 20]
    reports = ctx.get("reports")
    flags = all(e.event_id in reports.incidents for e in bulky)
    alerts = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "bulky_item"]
    supported = all(any(e.car_id == a.car_id and e.t == a.t for e in bulky) for a in alerts)
    no_accused = all(row["responsible_credential"] is None for row in reports.incidents.values())
    originals = {e.call.call_id: e.call.created_at for e in ctx.bus.of_type(E.CallRequested)}
    preserved = all(ctx.get("dispatcher").calls[c].call.created_at == originals[c]
                    for group in waiting for c in group)
    return [
        ("three actual lobby overloads within five minutes, each with waiting calls",
         len(planned) == 3 and planned[-1].t - planned[0].t <= 300
         and all(e.floor == 1 for e in planned) and all(waiting),
         str([(e.t, len(w)) for e, w in zip(planned, waiting)])),
        ("bilingual step-out, next-car and voice within one second", bool(planned) and timely,
         f"timely={timely}"),
        _reassignment_check(ctx, overloads),
        ("original waiting times retained", preserved and bool(waiting), "actual call identities"),
        ("third overload requests observable camera review",
         len(planned) == 3 and any(
             n[0] >= planned[2].t and n[0] <= planned[2].t + 1
             and "overload_camera_review" in n[2] and "security" in n[1]
             for n in ctx.get("notifier").inbox), "actual notifier delivery"),
        ("two bulky camera reports flagged and redirected", len(bulky) == 2 and flags
         and len(alerts) == 2 and supported, f"camera={len(bulky)}; redirects={len(alerts)}"),
        ("crowd-only evidence remains distinct without individual accusation",
         bool(crowd) and all(reports.incidents[e.event_id]["kind"] == "repeated_overload"
                            for e in crowd) and supported and no_accused,
         f"crowd={len(crowd)}; evidence-supported={supported}"),
    ]
