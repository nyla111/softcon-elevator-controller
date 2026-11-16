"""SIM-18 Medical priority starts during an occupied VIP trip (FR-127, BR-10)."""
from __future__ import annotations


from ems.contracts.events import (CarStatusUpdated, ControllerFault,
                                  DoorsOpened, PassengerAlighted, PassengerBoarded, PriorityTripEnded,
                                  PriorityTripStarted)
from ems.contracts.types import DoorState
from ems.sim.faults import measured
from ems.sim.passengers import PassengerState

from ..standins import install_scenario_standins
from .common import (SECURITY, _service, _log, _act, _action, _integration, _cleared,
                      _max_exclusive_per_group)

# Appendix B note: "Medical priority starts after the VIP has boarded" (this replaces the default
# "20 minutes into the measured window"): the first exclusive VIP boarding of the measured window.
SIM18_AFTER = measured(0)
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
        def request() -> None:
            _act(ctx, "medical", lambda: p.request_medical(SECURITY, origin, lobby))
            out = _action(ctx, "medical")[2]
            trip = p.trips.get(out.ref) if out.ok else None
            if trip is not None and trip.phase == "at_origin":   # car was already open there
                log["departed"] = True
                ctx.clock.call_later(SIM18_HANDLING_S, lambda: _act(
                    ctx, "depart", lambda: p.medical_depart(SECURITY, trip.trip_id)))
        ctx.clock.call_later(0.0, request)

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


def _open_at(ctx, car, floor, t) -> bool:
    """Telemetry: the car's last DoorsOpened before t was at `floor` and no status between it
    and t shows the doors closing / closed or another floor."""
    last = None
    for e in ctx.bus.log:
        if e.t > t:
            break
        if isinstance(e, DoorsOpened) and e.car_id == car:
            last = e
        elif last is not None and isinstance(e, CarStatusUpdated) and e.status.car_id == car \
                and e.t >= last.t and (e.status.doors in (DoorState.CLOSING, DoorState.CLOSED)
                                       or e.status.floor != last.floor):
            last = None
    return last is not None and last.floor == floor


def check_sim_18(result, ctx) -> list:
    bus, sim, p, log = ctx.bus, ctx.services["controllers"], _service(ctx), _log(ctx)
    vb, med = log.get("vip_boarded"), _action(ctx, "medical")
    if vb is None or med is None:
        return [("an occupied VIP trip exists in the measured window to start the medical trip",
                 False, "no exclusive VIP boarding in the measured window of this run"),
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
    if trip is not None and _open_at(ctx, car, trip.origin, t_med):
        at_origin = t_med                    # already standing there with open doors
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
    # event order: VIP boards -> VIP trip ends = medical starts -> doors at origin -> doors at
    # destination -> medical ends
    med_end = next((e.t for e in bus.of_type(PriorityTripEnded)
                    if e.kind == "medical" and e.car_id == car and e.t >= t_med), None)
    seq = [t_board, vip_end[0].t if vip_end else None, t_med, at_origin, at_dest, med_end]
    ordered = None not in seq and all(a <= b for a, b in zip(seq, seq[1:]))
    out.append(("event order: VIP boarded <= VIP yields = medical start <= origin < "
                "destination < medical end", ordered and seq[1] == seq[2]
                and at_origin is not None and at_origin >= t_med,
                " -> ".join("?" if x is None else f"{x:.1f}" for x in seq)))
    states = ctx.services["car_states"].log
    granted = [x for x in states if x[0] == t_med and x[1] == car and x[2] == "request"
               and x[4] == "priority:medical" and x[5]]
    converted = vip_car == car
    released = [x for x in states if x[1] == car and x[2] == "release" and x[0] == med_end]
    out.append(("medical car held through car_states (requested or converted VIP hold) and "
                "released at the end", (bool(granted) or converted) and bool(released),
                f"request {'converted VIP hold' if converted else len(granted)}, "
                f"release at end {len(released)}"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out
