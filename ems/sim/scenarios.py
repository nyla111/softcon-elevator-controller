"""Scenario registry: SIM-01 .. SIM-18 (Appendix B) in one place, shared by tests and the runner.

Each Scenario says what to run (profile, rate, faults, run lengths) and, once an owner has
written it, how to judge the run (`check`). Ownership:
    Part 1: SIM-09, SIM-16 (checks below)
    Part 2: SIM-01 02 03 04 05 10     Part 3: SIM-07
    Part 4: SIM-08 13 14 15 17        Part 5: SIM-06 11 12 18
HOW A PART ADDS ITS SCENARIO: edit the entry in SCENARIOS (below), set `faults`, write a function
  check(result, ctx) -> list[(criterion, passed, detail)]
and set check=..., ready=True, waiting_for=None. Nothing else changes: tests/scenarios and the
runner page pick it up. Part 1 only runs the scenarios; it does not write the other criteria.

Titles of the scenarios other than SIM-09 and SIM-16 are working titles: the repo does not contain
Appendix B, so each owner should correct the title when adding the check.
rate=None means "the profile's default rate" (D-01). A check gets the RunResult and the finished
Context (bus log, services).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from ems.contracts.events import (AlertRaised, CallAssigned, CallRequested, CarStatusUpdated,
                                  EmsServerDown, EmsServerUp, HeartbeatLost, HeartbeatRestored,
                                  PassengerAlighted)

from .baseline import install_dispatch_standin
from .faults import Fault, ems_server_crash, link_loss, measured, plugin_crash
from .harness import CLEAR_S, MEASURE_S, WARMUP_S, RunResult, run
from .traffic import with_rate

Criteria = list  # of (criterion, passed, detail)


@dataclass
class Scenario:
    id: str
    title: str
    owner_part: int
    profile: str
    rate: Optional[float] = None
    faults: list[Fault] = field(default_factory=list)
    warmup_s: float = WARMUP_S
    measure_s: float = MEASURE_S
    clear_s: float = CLEAR_S
    setup: Optional[Callable] = None            # runs after build(), before traffic starts
    check: Optional[Callable] = None            # check(result, ctx) -> criteria or None
    ready: bool = False
    waiting_for: Optional[str] = None


def _setup_with_recorder(ctx) -> None:
    """Dispatch stand-in while Part 2 is a stub + a stand-in for Part 6: one DB record per
    delivered passenger (SIM-09 checks how much of that is lost in the crash)."""
    install_dispatch_standin(ctx)
    db = ctx.services["db"]
    ctx.bus.subscribe(PassengerAlighted, lambda e: db.write({"t": e.t, "call_id": e.call_id}))


# ---------------------------------------------------------------- SIM-09 (Part 1)
CRASH_AT = measured(20)
TAKEOVER_S = 8.0


def check_sim_09(result: RunResult, ctx) -> Criteria:
    bus, sim = ctx.bus, ctx.services["controllers"]
    downs, ups = bus.of_type(EmsServerDown), bus.of_type(EmsServerUp)
    if len(downs) != 1 or len(ups) != 1:
        return [("one EmsServerDown and one EmsServerUp", False,
                 f"{len(downs)} down, {len(ups)} up")]
    down, up = downs[0], ups[0]
    in_gap = [e for e in bus.of_type(PassengerAlighted) if down.t <= e.t <= up.t]
    arrived = [p for p in sim.passengers.values() if down.t <= p.arrived_at <= up.t]
    refused = [c for c in sim.command_log if down.t <= c[0] < up.t and c[4]]
    lost = ctx.services["failover"].lost_records
    oldest = max((down.t - r["t"] for r in lost), default=0.0)
    return [
        ("failover gap <= 10 s", up.gap_s <= 10, f"{up.gap_s:.1f} s"),
        ("crash happens at the injected time", down.t == CRASH_AT, f"t={down.t:.1f}"),
        ("calls still delivered during the gap", len(in_gap) > 0, f"{len(in_gap)} delivered"),
        ("every passenger arriving in the gap got a car",
         bool(arrived) and all(p.car_id for p in arrived), f"{len(arrived)} arrived"),
        ("no EMS command accepted while the server is down", not refused,
         f"{len(refused)} accepted"),
        ("at most 30 s of records lost", oldest <= 30.0,
         f"{len(lost)} records, oldest {oldest:.1f} s"),
        ("all calls cleared", result.metrics["calls_not_cleared"] == 0,
         f"{result.metrics['calls_not_cleared']} not cleared"),
    ]


# ---------------------------------------------------------------- SIM-16 (Part 1)
LINK_CAR, LINK_AT, LINK_S = "B", measured(20), 300.0          # half 1: link loss
CRASH_PLUGIN_AT, CRASH_PLUGIN_S = measured(40), 300.0         # half 2: dispatch plug-in crash


def check_sim_16(result: RunResult, ctx) -> Criteria:
    bus = ctx.bus
    out: Criteria = []
    # --- half 1: link loss
    lost = [e for e in bus.of_type(HeartbeatLost) if e.car_id == LINK_CAR]
    if lost:
        silent_since = max((e.t for e in bus.of_type(CarStatusUpdated)
                            if e.status.car_id == LINK_CAR and e.t < lost[0].t), default=0.0)
        out.append(("HeartbeatLost within 3 s of the last status",
                    lost[0].t - silent_since <= 3.0 + 1e-9,
                    f"{lost[0].t - silent_since:.1f} s after last status, "
                    f"{lost[0].t - LINK_AT:.1f} s after the link dropped"))
    else:
        out.append(("HeartbeatLost within 3 s of the last status", False, "never published"))
    back = [e for e in bus.of_type(HeartbeatRestored) if e.car_id == LINK_CAR]
    out.append(("HeartbeatRestored when the link returns",
                bool(back) and back[0].t >= LINK_AT + LINK_S,
                f"t={back[0].t:.0f}" if back else "never"))
    out.append(("all calls delivered", result.metrics["calls_not_cleared"] == 0,
                f"{result.metrics['calls_not_cleared']} not cleared"))
    # --- half 2: plug-in crash
    guard = ctx.services["dispatch_guard"]
    start, end = CRASH_PLUGIN_AT, CRASH_PLUGIN_AT + CRASH_PLUGIN_S
    calls = {e.call.call_id for e in bus.of_type(CallRequested) if start <= e.t < end}
    fallback_ids = {entry[1] for entry in guard.log if entry[3] and start <= entry[0] < end}
    assigned = {e.call_id for e in bus.of_type(CallAssigned) if start <= e.t < end}
    out.append(("every call during the plug-in crash assigned via fallback",
                bool(calls) and calls <= fallback_ids and calls <= assigned,
                f"{len(calls & fallback_ids & assigned)} of {len(calls)} calls"))
    alerts = [a for a in bus.of_type(AlertRaised) if a.kind == "dispatch_fallback"]
    out.append(("one dispatch_fallback alert (high, to fm) for the outage",
                len(alerts) == 1 and alerts[0].severity == "high" and alerts[0].recipients == ["fm"]
                and start <= alerts[0].t < end, f"{len(alerts)} alert(s)"))
    recovered = [a for a in bus.of_type(AlertRaised) if a.kind == "dispatch_recovered"]
    out.append(("info alert when the plug-in answers again",
                len(recovered) == 1 and recovered[0].severity == "info" and recovered[0].t >= end,
                f"{len(recovered)} alert(s)"))
    return out


# ---------------------------------------------------------------- the registry
def _todo(sid: str, title: str, part: int, profile: str) -> Scenario:
    return Scenario(sid, title, part, profile, waiting_for=f"Part {part} check")


SCENARIOS: dict[str, Scenario] = {s.id: s for s in [
    _todo("SIM-01", "Up-peak traffic", 2, "up_peak"),
    _todo("SIM-02", "Lunch peak traffic", 2, "lunch"),
    _todo("SIM-03", "Evening down-peak traffic", 2, "evening"),
    _todo("SIM-04", "Balanced traffic", 2, "balanced"),
    _todo("SIM-05", "Eco quiet period", 2, "eco"),
    _todo("SIM-06", "VIP trips under load", 5, "balanced"),
    _todo("SIM-07", "ACS outage", 3, "balanced"),
    _todo("SIM-08", "Fire alarm recall", 4, "balanced"),
    Scenario("SIM-09", "EMS server failover", 1, "up_peak", faults=[
        ems_server_crash(CRASH_AT, TAKEOVER_S)], setup=_setup_with_recorder,
        check=check_sim_09, ready=True),
    _todo("SIM-10", "Mode schedule over a day", 2, "balanced"),
    _todo("SIM-11", "Medical priority express", 5, "balanced"),
    _todo("SIM-12", "Goods move with the service car", 5, "balanced"),
    _todo("SIM-13", "Mains loss and rescue", 4, "balanced"),
    _todo("SIM-14", "Car fault with passengers inside", 4, "balanced"),
    _todo("SIM-15", "Excess calls and spam limits", 4, "balanced"),
    Scenario("SIM-16", "Link loss and dispatch plug-in crash", 1, "up_peak", faults=[
        link_loss(LINK_CAR, LINK_AT, LINK_S), plugin_crash(CRASH_PLUGIN_AT, CRASH_PLUGIN_S)],
        setup=install_dispatch_standin, check=check_sim_16, ready=True),
    _todo("SIM-17", "Lost fire signal and generator limits", 4, "balanced"),
    _todo("SIM-18", "Send car to a collapsed person", 5, "balanced"),
]}


def get(scenario_id: str) -> Scenario:
    return SCENARIOS[scenario_id]


def describe(s: Scenario) -> dict:
    """Plain data for the web API."""
    from .traffic import PROFILES
    rate = s.rate if s.rate is not None else PROFILES[s.profile].rate_per_min
    return {"id": s.id, "title": s.title, "owner_part": s.owner_part, "profile": s.profile,
            "rate": rate, "ready": s.ready, "waiting_for": s.waiting_for,
            "faults": [f"{f.kind} at {f.at_s:.0f} s" for f in s.faults]}


def run_scenario(scenario_id: str, seed: int, out_dir=None) -> dict:
    """Run ONE seed headless. Returns plain data: metrics + criteria, or 'not ready'."""
    s = get(scenario_id)
    base = {"id": s.id, "title": s.title, "seed": seed, "owner_part": s.owner_part,
            "profile": s.profile}
    if not s.ready or s.check is None:
        return base | {"ready": False, "passed": None, "metrics": None, "criteria": [],
                       "message": f"not ready: waiting for {s.waiting_for}"}
    profile = with_rate(s.profile, s.rate) if s.rate is not None else s.profile
    result = run(profile, seed, s.faults, warmup_s=s.warmup_s, measure_s=s.measure_s,
                 clear_s=s.clear_s, setup=s.setup, out_dir=out_dir, keep_ctx=True)
    criteria = [{"criterion": c, "passed": bool(ok), "detail": str(d)}
                for c, ok, d in (s.check(result, result.ctx) or [])]
    passed = bool(criteria) and all(c["passed"] for c in criteria)
    return base | {"ready": True, "passed": passed, "metrics": result.metrics,
                   "criteria": criteria, "message": "PASS" if passed else "FAIL"}


def run_all_seeds(scenario_id: str, seeds=range(1, 11), out_dir=None) -> list[dict]:
    """Every seed on its own. The scenario passes only if every seed passes (no averaging)."""
    return [run_scenario(scenario_id, seed, out_dir) for seed in seeds]
