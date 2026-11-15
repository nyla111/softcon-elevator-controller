"""SIM-11 Accessible calls mixed into up-peak (FR-08, BR-10)."""
from __future__ import annotations

from collections import defaultdict

from ems.contracts import config
from ems.contracts.events import (CallAssigned, CallRequested, CarStatusUpdated, DoorsOpened, PanelMessage,
                                  PassengerAlighted, PassengerBoarded, PriorityTripStarted)
from ems.contracts.types import CallPriority, DoorState

from ..standins import install_scenario_standins
from .common import (RUSH_AVG_S, RUSH_CEILING_S, _service, _integration, _dispatcher_is_double,
                      _cleared, _measured, _waits, _wait_summary, _replay_assignments)

def setup_sim_11(ctx) -> None:
    install_scenario_standins(ctx)


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

    # FR-08 free capacity when the car opens at the call's floor: riders who stay + calls
    # already assigned to the car that do not end there (replayed from the bus)
    rated = {c: g.rated_persons for g in ctx.services["building"].groups for c in g.cars}
    riders: dict[str, dict] = defaultdict(dict)
    no_room, checked = [], 0
    first_open, first_cycle, later = {}, set(), []
    vip_over_acc = []
    for e, assigned in _replay_assignments(ctx):
        if isinstance(e, PassengerBoarded) and e.call_id in calls:
            riders[e.car_id][e.call_id] = calls[e.call_id].destination
        elif isinstance(e, PassengerAlighted):
            riders[e.car_id].pop(e.call_id, None)
        if isinstance(e, CallAssigned) and e.call_id in acc:
            call = calls[e.call_id]
            taken = sum(1 for d in riders[e.car_id].values() if d != call.origin) + sum(
                calls[c].party_size for c, car in assigned.items()
                if car == e.car_id and c in calls and calls[c].destination != call.origin)
            checked += 1
            if rated[e.car_id] - taken < call.party_size:
                no_room.append(e.call_id)
        elif isinstance(e, DoorsOpened):
            for c, car in assigned.items():
                if c in acc and car == e.car_id and calls[c].origin == e.floor:
                    if c in first_open:
                        later.append(c)                   # a second opening: left behind
                    first_open.setdefault(c, e.t)
        elif isinstance(e, PassengerBoarded) and e.call_id in acc and e.call_id in first_open \
                and e.call_id not in later:
            first_cycle.add(e.call_id)
        elif isinstance(e, PriorityTripStarted) and e.kind == "vip":
            vip_over_acc += [c for c, car in assigned.items() if car == e.car_id and c in acc]
    out.append(("accessible calls assigned to a car with room when it opens at their floor "
                "(riders who stay + calls already assigned)", checked > 0 and not no_room,
                f"{checked} assignments checked, {len(no_room)} without room"))
    out.append(("every accessible passenger boarded at the first door opening of their car",
                bool(acc) and first_cycle == acc,
                f"{len(first_cycle)} of {len(acc)}; {len(set(later))} left behind once"))
    out.append(("accessible above VIP: no exclusive VIP trip took a car an accessible "
                "passenger was waiting for", not vip_over_acc,
                f"{len(vip_over_acc)} case(s); "
                f"{sum(1 for e in bus.of_type(PriorityTripStarted) if e.kind == 'vip')} VIP trips"))
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
    # #10: register_stop(..., min_dwell_s) is logged by the simulator as (floor, call, dwell)
    asked = {c[3][1]: c[3][2] for c in sim.command_log
             if c[2] == "register_stop" and c[4] and len(c[3]) == 3}
    short = [cid for cid in acc if asked.get(cid, 0.0) < config.SIM["accessible_dwell_s"]]
    sender = " (sent by the Part 2 stand-in)" if _dispatcher_is_double(ctx) else ""
    out.append(("EMS requests the >= 10 s door time for every accessible pick-up "
                "(register_stop min_dwell_s in the command log)", bool(acc) and not short,
                f"{len(acc) - len(short)} of {len(acc)} requested{sender}"))
    out.append(("priority order medical > accessible > VIP > ordinary applied by the dispatcher",
                not _dispatcher_is_double(ctx),
                "BLOCKED: Part 2 dispatcher is a stub/double (Part 5 provides rank()/order())"
                if _dispatcher_is_double(ctx) else "real Part 2 dispatcher"))

    measured_people = _measured(result, sim)
    end = ctx.clock.now()
    waits = _waits(measured_people, end)
    acc_people = [q for q in measured_people if q.passenger_id in acc]
    acc_waits = _waits(acc_people, end)
    open_all = sum(1 for q in measured_people if q.boarded_at is None)
    open_acc = sum(1 for q in acc_people if q.boarded_at is None)
    avg = sum(waits) / len(waits) if waits else 0.0
    out.append((f"Rush wait targets hold (average <= {RUSH_AVG_S} s, none over "
                f"{RUSH_CEILING_S} s), every measured call counted",
                bool(waits) and avg <= RUSH_AVG_S and max(waits) <= RUSH_CEILING_S,
                f"all: {_wait_summary(waits, open_all)}; accessible: "
                f"{_wait_summary(acc_waits, open_acc)}"))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out
