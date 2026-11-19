"""Part 2 scenario setups and checks: SIM-01, 02, 03, 04, 05, 10 (Appendix B).

Wired into ems/sim/scenarios.py through _part2(name) (imported only when the scenario runs).
A check returns [(criterion, passed, detail)]; Part 1 runs them for seeds 1-10, every seed on
its own (no averaging).

Wait = boarding time - arrival time, measured-window passengers delivered to their own floor
(harness metrics). VIP exclusive trips are part of normal service (4.3) and are counted.
"Car shown within 1.5 s" (NFR-P5): time from the card tap (PassengerArrived) to CallAssigned in
simulated time, plus the dispatch engine's real compute time for that call.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from ems.contracts import config
from ems.contracts.events import (AlertRaised, CallAssigned, CallReassigned, ModeChanged,
                                  PassengerArrived)
from ems.contracts.types import Mode

Criteria = list

SHOWN_WITHIN_S = 1.5        # NFR-P5
SHOWN_SHARE = 0.95
ASSIGN_WITHIN_S = 1.0       # NFR-P1
ASSIGN_SHARE = 0.99
ECO_SAVING = 0.15           # Figure 6 / SIM-04
FAR_FUTURE = datetime(2100, 1, 1)


# ================================================================ setups
def force_mode(mode: Mode):
    """Setup for SIM-04 pair runs: the FM pins one mode for the whole run (FR-12)."""
    def setup(ctx) -> None:
        ctx.services["dispatcher"].set_manual_mode(mode, None, FAR_FUTURE)
    setup.__name__ = f"force_{mode.value}"
    return setup


def setup_eco(ctx) -> None:
    force_mode(Mode.ECO)(ctx)


def setup_balanced(ctx) -> None:
    force_mode(Mode.BALANCED)(ctx)


# ================================================================ helpers
def _waits(result, ceiling: float, target: float, label: str = "") -> Criteria:
    m = result.metrics
    p = f"{label}: " if label else ""
    return [
        (f"{p}average wait <= {target:g} s", m["avg_wait_s"] <= target,
         f"{m['avg_wait_s']:.1f} s over {m['passengers_measured']} passengers"),
        (f"{p}no wait over {ceiling:g} s", m["max_wait_s"] <= ceiling,
         f"max {m['max_wait_s']:.1f} s; {_over(result, ceiling)} over"),
        (f"{p}all calls cleared", m["calls_not_cleared"] == 0,
         f"{m['calls_not_cleared']} not cleared"),
    ]


def _over(result, limit: float) -> int:
    ctx = result.ctx
    if ctx is None:
        return -1
    lo, hi = _window(result)
    return sum(1 for p in ctx.services["controllers"].passengers.values()
               if lo <= p.arrived_at < hi and not p.evacuated and p.wait_s is not None
               and p.wait_s > limit)


def _window(result) -> tuple[float, float]:
    lo = result.params["warmup_s"]
    return lo, lo + result.params["measure_s"]


def _shown(result, ctx, label: str = "") -> Criteria:
    """NFR-P5: the assigned car is known within 1.5 s of the tap for 95% of calls."""
    lo, hi = _window(result)
    tapped = {e.passenger_id: e.t for e in ctx.bus.of_type(PassengerArrived) if lo <= e.t < hi}
    first: dict[str, float] = {}
    for e in ctx.bus.log:
        if isinstance(e, (CallAssigned, CallReassigned)) and e.call_id in tapped:
            first.setdefault(e.call_id, e.t)
    guard = ctx.services["dispatch_guard"]
    compute = max(guard.compute_s, default=0.0)
    ok = sum(1 for cid, t in tapped.items()
             if cid in first and first[cid] - t + compute <= SHOWN_WITHIN_S)
    share = ok / len(tapped) if tapped else 0.0
    p = f"{label}: " if label else ""
    return [(f"{p}car shown within {SHOWN_WITHIN_S} s for >= 95% of calls", share >= SHOWN_SHARE,
             f"{share:.1%} of {len(tapped)} (engine compute max {compute * 1000:.1f} ms)")]


def _rush(result, ctx, label: str = "") -> Criteria:
    target, ceiling = config.MODE_TARGETS[Mode.RUSH]
    return _waits(result, ceiling, target, label) + _shown(result, ctx, label)


def _no_fallback(ctx, label: str = "") -> Criteria:
    guard = ctx.services["dispatch_guard"]
    p = f"{label}: " if label else ""
    return [(f"{p}dispatch plug-in answered every call (no FR-105 fallback)",
             guard.fallbacks == 0, f"{guard.fallbacks} fallbacks of {guard.calls}")]


def _integration(ctx) -> Criteria:
    """Not a pass criterion of the SRS: the run must use the real Part 2 dispatcher."""
    name = type(ctx.services["dispatcher"]).__name__
    return [("real Part 2 dispatcher in the run", name == "Dispatcher", name)]


# ================================================================ checks
def check_sim_01(result, ctx) -> Criteria:
    return _integration(ctx) + _rush(result, ctx) + _no_fallback(ctx)


def check_sim_02(results, ctxs) -> Criteria:
    out = _integration(ctxs[0])
    for label, r, c in zip(("lunch", "evening"), results, ctxs):
        out += _rush(r, c, label) + _no_fallback(c, label)
    return out


def check_sim_03(result, ctx) -> Criteria:
    target, ceiling = config.MODE_TARGETS[Mode.BALANCED]
    modes = {ctx.services["dispatcher"].current_mode(g) for g in ("LOW", "HIGH")}
    return (_integration(ctx) + _waits(result, ceiling, target)
            + [("Balanced mode during the run", modes == {Mode.BALANCED},
                ", ".join(sorted(m.value for m in modes)))]
            + _no_fallback(ctx))


def check_sim_04(results, ctxs) -> Criteria:
    eco, bal, surge = results
    ce, cb, cs = ctxs
    target, ceiling = config.MODE_TARGETS[Mode.ECO]
    out = _integration(ce)
    run_e = eco.metrics["total_car_run_time_s"]
    run_b = bal.metrics["total_car_run_time_s"]
    saving = (run_b - run_e) / run_b if run_b else 0.0
    out.append(("quiet pair: Eco saves >= 15% run time vs Balanced", saving >= ECO_SAVING,
                f"Balanced {run_b:.0f} s, Eco {run_e:.0f} s, saving {saving:.1%}"))
    out.append(("quiet pair: same arrivals in both runs",
                eco.metrics["passengers_measured"] == bal.metrics["passengers_measured"],
                f"{eco.metrics['passengers_measured']} vs {bal.metrics['passengers_measured']}"))
    out += _waits(eco, ceiling, target, "quiet Eco")
    pinned = [e for e in ce.bus.of_type(ModeChanged) if e.new is not Mode.ECO]
    out.append(("quiet Eco run stays in Eco", not pinned, f"{len(pinned)} changes away from Eco"))
    # surge: FR-14 step-up, logged, FM notified; the run is degraded service (outside envelope)
    lo, _ = _window(surge)
    ups = [e for e in cs.bus.of_type(ModeChanged)
           if e.cause == "eco_step_up" and e.old is Mode.ECO and e.new is Mode.BALANCED]
    alerts = [a for a in cs.bus.of_type(AlertRaised) if a.kind == "eco_step_up"]
    first = min((e.t for e in ups), default=None)
    out.append(("surge: Eco steps up to Balanced (FR-14)", bool(ups) and first >= lo,
                f"{len(ups)} step-up(s), first {first - lo:.0f} s into the surge"
                if first is not None else "no step-up"))
    out.append(("surge: FM notified of the step-up", bool(alerts) and
                all("fm" in a.recipients for a in alerts), f"{len(alerts)} alert(s)"))
    stays = [e for e in cs.bus.of_type(ModeChanged)
             if first is not None and e.t > first and e.old is Mode.BALANCED
             and e.cause != "schedule"]
    out.append(("surge: stays in Balanced until the next scheduled change", not stays,
                f"{len(stays)} early change(s)"))
    out.append(("surge: all calls cleared", surge.metrics["calls_not_cleared"] == 0,
                f"{surge.metrics['calls_not_cleared']} not cleared (degraded period, D-01)"))
    return out


def check_sim_05(result, ctx) -> Criteria:
    out = _integration(ctx)
    d = ctx.services["dispatcher"]
    # FR-11: every scheduled boundary of the day produced a ModeChanged with cause "schedule"
    changes = [(e.t, e.group, e.new) for e in ctx.bus.of_type(ModeChanged)
               if e.cause == "schedule" and e.group in ("LOW", "HIGH")]
    expected = [(start, mode) for start, mode in config.WEEKDAY_SCHEDULE[1:]]
    seen = {(f"{int(t // 3600):02d}:{int(t % 3600 // 60):02d}", m) for t, _, m in changes}
    missing = [s for s, m in expected if (s, m) not in seen]
    out.append(("mode follows the weekday schedule (FR-11)", not missing,
                f"missing {missing}" if missing else f"{len(changes)} scheduled changes"))
    for b in result.blocks:
        mode = Mode(b["mode"]) if b["mode"] else None
        if mode is None:
            continue
        target, ceiling = config.MODE_TARGETS[mode]
        ok = b["avg_wait_s"] <= target and b["max_wait_s"] <= ceiling
        out.append((f"{b['label']}: {mode.value} targets (avg <= {target}, max <= {ceiling})",
                    ok, f"avg {b['avg_wait_s']:.1f} s, max {b['max_wait_s']:.1f} s, "
                        f"{b['calls']} calls"))
        out.append((f"{b['label']}: nobody stranded at the mode change (FR-15)",
                    b["stranded"] == 0, f"{b['stranded']} stranded"))
    out.append(("all calls cleared", result.metrics["calls_not_cleared"] == 0,
                f"{result.metrics['calls_not_cleared']} not cleared"))
    out.append(("ceiling breaches logged with cause (FR-13)",
                all("cause" in b for b in d.breaches), f"{len(d.breaches)} logged"))
    return out


def check_sim_10(result, ctx) -> Criteria:
    lat = result.latency or {}
    assign = (lat.get("assignment_s") or {})
    guard = ctx.services["dispatch_guard"]
    xs = guard.compute_s
    within = sum(1 for x in xs if x <= ASSIGN_WITHIN_S) / len(xs) if xs else 0.0
    out = _integration(ctx)
    out.append(("at least 99% of calls assigned <= 1 s (NFR-P1)", bool(xs) and
                within >= ASSIGN_SHARE,
                f"{within:.2%} of {len(xs)}; p99 {_ms(assign.get('p99'))}, "
                f"max {_ms(assign.get('max'))}"))
    dash = (lat.get("dashboard") or {}).get("response_s") or {}
    out.append(("all dashboard changes <= 1 s (NFR-P2)",
                dash.get("max") is not None and dash["max"] <= 1.0,
                f"max {_ms(dash.get('max'))} over {dash.get('n', 0)} requests"))
    look = lat.get("lookup_s") or {}
    out.append(("at least 95% of credential responses <= 1.5 s (NFR-P5)",
                look.get("p95") is not None and look["p95"] <= 1.5,
                f"p95 {_ms(look.get('p95'))} over {look.get('n', 0)} lookups"))
    return out


def _seed_wear_history(ctx, floors: int) -> None:
    """Compress the equal-distance history before SIM-19's measured final slice."""
    usage = ctx.services["usage"]
    cars = [car for group in ctx.services["building"].groups for car in group.cars]
    usage.period.update({car: floors for car in cars})
    month = ctx.clock.now_dt().strftime("%Y-%m")
    usage.monthly[month] = {car: floors for car in cars}


def setup_sim_19_month(ctx) -> None:
    _seed_wear_history(ctx, 100_000)       # first 29 compressed days


def setup_sim_19_period(ctx) -> None:
    _seed_wear_history(ctx, 500_000)       # first five compressed months


def check_sim_19(results, ctxs) -> Criteria:
    month = ctxs[0].services["usage"].wear_report()
    period = ctxs[1].services["usage"].wear_report()
    passenger_groups = ctxs[0].services["building"].passenger_groups
    monthly = {g: month["groups"][g]["monthly_imbalance"] for g in passenger_groups}
    six_month = {g: period["groups"][g]["period_imbalance"] for g in passenger_groups}
    return [
        ("every passenger group is under 2% for the compressed month",
         all(value < month["monthly_limit"] for value in monthly.values()), repr(monthly)),
        ("every passenger group is at most 5% for the compressed six-month period",
         all(value <= period["period_limit"] for value in six_month.values()),
         repr(six_month)),
        ("wait targets retain priority and every call clears",
         all(result.metrics["calls_not_cleared"] == 0 for result in results),
         repr([result.metrics["calls_not_cleared"] for result in results])),
    ]


def _ms(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 1000:.1f} ms"
