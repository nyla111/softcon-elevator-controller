"""SIM-12 Ten VIP calls during up-peak in one group (FR-71, FR-72, FR-76)."""
from __future__ import annotations

from ems.contracts import config
from ems.contracts.events import CallRequested, PassengerBoarded, PriorityTripStarted
from ems.contracts.types import CallPriority
from ems.sim.faults import measured

from ..standins import VipReplacementPopulation, install_scenario_standins
from .common import (RUSH_CEILING_S, _service, _integration, _dispatcher_is_double, _cleared,
                      _measured, _waits, _wait_summary, _group_of, _max_exclusive_per_group,
                      _max_vip_starts_per_rolling_hour, _replay_exclusive)

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
                bool(starts) and per_hour <= config.VIP_MAX_PER_HOUR,
                f"max {per_hour} in any hour, {len(starts)} starts in the run"))
    # the caps were really exercised, and every cap refusal was right at that moment
    times = [e.t for e in starts]
    hourly = [d for d in p.decisions if d["reason"] == "hourly_cap"]
    wrong_hourly = [d for d in hourly
                    if sum(1 for t in times if d["t"] - 3600.0 < t <= d["t"]) <
                    config.VIP_MAX_PER_HOUR]
    excl = [d for d in p.decisions if d["reason"] == "group_exclusive_cap"]
    busy_at: dict[str, int] = {}
    intruders = []
    for e, active in _replay_exclusive(ctx):
        if isinstance(e, CallRequested) and e.call.priority is CallPriority.VIP:
            busy_at[e.call.call_id] = len(active[e.call.group])
        elif isinstance(e, PassengerBoarded) and not sim.passengers[e.call_id].vip and \
                active[_group_of(ctx, e.car_id)].get(e.car_id) == "vip":
            intruders.append(e.call_id)
    wrong_excl = [d for d in excl if busy_at.get(d["call_id"], 0) == 0]
    out.append(("caps exercised and every cap refusal justified at its time",
                bool(hourly) and not wrong_hourly and not wrong_excl,
                f"{len(hourly)} hourly_cap refusals ({len(wrong_hourly)} unjustified), "
                f"{len(excl)} group_exclusive_cap ({len(wrong_excl)} unjustified)"))
    out.append(("exclusive: no other passenger boards a car during its VIP trip",
                bool(starts) and not intruders, f"{len(intruders)} other boarding(s)"))
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
    waits = _waits(ordinary, ctx.clock.now())
    out.append((f"ordinary calls of group {group} within the {RUSH_CEILING_S} s ceiling",
                bool(waits) and max(waits) <= RUSH_CEILING_S,
                _wait_summary(waits, sum(1 for q in ordinary if q.boarded_at is None))))
    out.append(_cleared(result))
    out.append(_integration(ctx))
    return out
