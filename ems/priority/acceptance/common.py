"""Shared helpers of the Part 5 scenario checks (SIM-06, 11, 12, 18).

Each check returns [(criterion, passed, detail)] computed from observable evidence of the
finished run (bus log, the simulator's passengers and command audit, car_states requests). Every
check also reports "integration with real Part 2/3/4 services": while those parts are stubs the
setups install the doubles of ems/priority/standins.py and that criterion FAILS as BLOCKED, so a
scenario cannot pass on stand-ins. Operator actions (technician, FM, security) go through the
public Part 5 API with the mock ACS staff cards at fixed measured minutes (scenario choices).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (CallAssigned, CallCancelled, CallReassigned,
                                  PassengerBoarded, PriorityTripEnded,
                                  PriorityTripStarted)
from ems.contracts.types import Mode
from ems.sim.passengers import PassengerState

from ..standins import integration_gaps

RUSH_AVG_S, RUSH_CEILING_S = config.MODE_TARGETS[Mode.RUSH]
MOVE_COMMANDS = ("register_stop", "express", "park")
TECH, FM, SECURITY = "staff-technician-1", "staff-fm-1", "staff-security-1"


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


def _waits(people, end_t: float) -> list[float]:
    """Wait of every call: boarding - arrival; a call never picked up counts with
    end_t - arrival (a lower bound, it is still waiting). Evacuated riders (fire / rescue) are
    left out as in the harness metrics; cancelled calls have no wait."""
    out = []
    for p in people:
        if p.evacuated or p.state is PassengerState.CANCELLED:
            continue
        out.append(p.wait_s if p.wait_s is not None else end_t - p.arrived_at)
    return out


def _wait_summary(waits: list[float], uncleared: int = 0) -> str:
    if not waits:
        return "no calls"
    over = sum(1 for w in waits if w > RUSH_CEILING_S)
    return (f"n={len(waits)} avg={sum(waits) / len(waits):.1f} s max={max(waits):.1f} s, "
            f"{over} over {RUSH_CEILING_S} s, {uncleared} never picked up")


def _replay_assignments(ctx):
    """Yield (event, assigned) in bus order; `assigned` maps call -> car for calls assigned and
    not yet on board, as it was just BEFORE the event."""
    assigned: dict[str, str] = {}
    for e in ctx.bus.log:
        yield e, assigned
        if isinstance(e, CallAssigned):
            assigned[e.call_id] = e.car_id
        elif isinstance(e, CallReassigned):
            assigned[e.call_id] = e.to_car
        elif isinstance(e, (PassengerBoarded, CallCancelled)):
            assigned.pop(e.call_id, None)


def _group_of(ctx, car_id) -> Optional[str]:
    return _service(ctx).group_of_car(car_id)


def _replay_exclusive(ctx):
    """Yield (event, active) in bus order; `active` maps group -> {car: trip kind} for the
    exclusive trips running just AFTER the event (from PriorityTripStarted / Ended)."""
    active: dict[str, dict] = defaultdict(dict)
    for ev in ctx.bus.log:
        if isinstance(ev, PriorityTripStarted):
            active[_group_of(ctx, ev.car_id)][ev.car_id] = ev.kind
        elif isinstance(ev, PriorityTripEnded):
            active[_group_of(ctx, ev.car_id)].pop(ev.car_id, None)
        yield ev, active


def _max_exclusive_per_group(ctx) -> tuple[int, str]:
    """Most exclusive cars at once in one group."""
    worst, where = 0, "-"
    for ev, active in _replay_exclusive(ctx):
        if isinstance(ev, PriorityTripStarted):
            g = _group_of(ctx, ev.car_id)
            if len(active[g]) > worst:
                worst, where = len(active[g]), f"group {g} at t={ev.t:.0f}"
    return worst, where


def _max_vip_starts_per_rolling_hour(ctx) -> int:
    starts = [e.t for e in ctx.bus.of_type(PriorityTripStarted) if e.kind == "vip"]
    return max((sum(1 for s in starts if t - 3600.0 < s <= t) for t in starts), default=0)
