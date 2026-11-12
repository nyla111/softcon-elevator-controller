"""Scenario harness: run one traffic profile with one seed and report metrics.

Timeline (sim seconds): arrivals run for warm-up (10 min) + measured period (60 min);
then up to 30 more minutes are allowed for every passenger to be delivered.
Only passengers who arrive in the measured period count in the wait/ride metrics.
If anybody is still not delivered at the end, the run FAILS (calls_not_cleared > 0).

Assumption: calls_not_cleared counts EVERY passenger still waiting or riding at the end,
including those who arrived during the warm-up.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from ems.contracts import config
from ems.core.app import Context, build

from .building import BuildingSpec
from .faults import Fault, schedule_fault
from .passengers import PassengerState
from .population import Population
from .traffic import (PEAK_PROFILES, PROFILES, SRS_ORIGINAL_PEAK_RATE, Profile, Segment,
                      TrafficGenerator, schedule_segments)

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"          # gitignored
WARMUP_S = 600.0
MEASURE_S = 3600.0
CLEAR_S = 1800.0
CHECK_EVERY_S = 5.0
START_DATE = datetime(2026, 11, 16)    # Monday; a run starts at START_DATE + start_hhmm
STRANDED_AFTER_S = 1800.0              # SIM-05: a call still open 30 min after a block change


@dataclass
class RunResult:
    profile: str
    seed: int
    passed: bool                         # True when every call was cleared
    metrics: dict
    params: dict
    n_evacuated: int = 0                 # riders who left early (fire recall, power rescue)
    manifest_path: Optional[str] = None
    event_digest: str = ""
    blocks: list = field(default_factory=list)       # weekday runs: per-block metrics
    latency: dict = field(default_factory=dict)      # design-limit runs: latency summaries
    start: str = ""                                   # simulated date and time at t=0
    ctx: Optional[Context] = field(default=None, repr=False)   # only if keep_ctx=True


def _start_datetime(start_hhmm: Optional[str]) -> Optional[datetime]:
    if start_hhmm is None:
        return None
    h, m = start_hhmm.split(":")
    return START_DATE.replace(hour=int(h), minute=int(m))


def run(profile: Union[str, Profile], seed: int, faults: Sequence[Fault] = (), *,
        warmup_s: float = WARMUP_S, measure_s: float = MEASURE_S, clear_s: float = CLEAR_S,
        setup: Optional[Callable[[Context], None]] = None,
        population: Optional[Population] = None, out_dir: Optional[Path] = RUNS_DIR,
        keep_ctx: bool = False, start_hhmm: Optional[str] = None, traffic: str = "fixed",
        warmup_profile: Union[None, str, Profile] = None,
        building: Optional[BuildingSpec] = None, workload: Optional[dict] = None,
        manifest_extra: Optional[dict] = None) -> RunResult:
    """Run one scenario. `setup(ctx)` runs after build (e.g. to install a dispatcher).
    out_dir=None skips writing the evidence manifest.

    start_hhmm     time of day at t=0, "HH:MM" on START_DATE (default 00:00); applied through
                   build(start=...), so ctx.clock.now_dt() gives the time of day.
    traffic        "fixed": one profile; "weekday": follow config.WEEKDAY_SCHEDULE (the profile
                   argument is only a label). Weekday runs report per-block metrics.
    warmup_profile profile used during the warm-up only (SIM-04: quiet Eco traffic before a surge).
    building       layout of the run (default: the reference building).
    workload       SIM-10 design-limit hooks: {"lookups_per_min", "camera_per_day",
                   "dashboard_users", "dashboard_requests"}.
    manifest_extra extra keys for the evidence manifest."""
    weekday = traffic == "weekday"
    prof = None if weekday else (PROFILES[profile] if isinstance(profile, str) else profile)
    label = profile if isinstance(profile, str) else profile.name
    ctx = build(seed=seed, start=_start_datetime(start_hhmm),
                services={"building": building} if building is not None else None)
    sim = ctx.services["controllers"]
    if setup is not None:
        setup(ctx)
    arrivals_end = warmup_s + measure_s
    if weekday:
        segments = schedule_segments(ctx, until=arrivals_end)
        TrafficGenerator(ctx, segments, population=population).start()
    elif warmup_profile is not None and warmup_s > 0:
        lead = PROFILES[warmup_profile] if isinstance(warmup_profile, str) else warmup_profile
        TrafficGenerator(ctx, [Segment(0.0, warmup_s, lead, "warm-up"),
                               Segment(warmup_s, arrivals_end, prof, "measured")],
                         population=population).start()
    else:
        TrafficGenerator.fixed(ctx, prof, 0.0, arrivals_end, population=population).start()
    load = None
    if workload:
        from .workload import Workload
        load = Workload(ctx, workload.get("lookups_per_min", 400.0),
                        workload.get("camera_per_day", 2000.0))
        load.start()
    pop = population or ctx.services["population"]
    for f in faults:
        schedule_fault(ctx, f)

    ctx.clock.run_until(arrivals_end)
    if load is not None:
        load.stop()
    dashboard = None
    if workload and workload.get("dashboard_users"):
        from . import dashboard_load      # burst at the end of the arrivals: queues are longest
        dashboard = dashboard_load.run(ctx, workload["dashboard_users"],
                                       workload.get("dashboard_requests", 5))
    t, deadline = arrivals_end, arrivals_end + clear_s
    while sim.undelivered() > 0 and t < deadline:
        t = min(t + CHECK_EVERY_S, deadline)
        ctx.clock.run_until(t)

    metrics = _metrics(sim, warmup_s, arrivals_end)
    pdict = ({"name": label, "rate_per_min": None, "schedule": "weekday"} if weekday
             else asdict(prof))
    params = {"profile": pdict, "warmup_s": warmup_s, "measure_s": measure_s,
              "clear_s": clear_s, "population": pop.params, "faults": [asdict(f) for f in faults],
              "ended_at_s": ctx.clock.now(), "start": ctx.clock.now_dt().isoformat(),
              "building": ctx.services["building"].as_dict()}
    if warmup_profile is not None:
        params["warmup_profile"] = (warmup_profile if isinstance(warmup_profile, str)
                                    else warmup_profile.name)
    params["start"] = (_start_datetime(start_hhmm) or START_DATE).isoformat()
    latency = {}
    if load is not None:
        latency = load.summary() | {"dashboard": dashboard}
    result = RunResult(label, seed, metrics["calls_not_cleared"] == 0, metrics, params,
                       n_evacuated=metrics["n_evacuated"], event_digest=_digest(ctx),
                       blocks=_block_metrics(sim, segments, warmup_s, arrivals_end) if weekday else [],
                       latency=latency, start=params["start"], ctx=ctx if keep_ctx else None)
    if out_dir is not None:
        result.manifest_path = str(_write_manifest(result, Path(out_dir), manifest_extra or {}))
    return result


def run_seeds(profile: Union[str, Profile], faults: Sequence[Fault] = (),
              seeds: Sequence[int] = range(1, 11), **kwargs) -> list[RunResult]:
    """One result per seed. No averaging: every seed must pass on its own."""
    return [run(profile, seed, faults, **kwargs) for seed in seeds]


# ---------- internals ----------
def _metrics(sim, measure_from: float, measure_to: float) -> dict:
    """Wait/ride metrics cover measured-window passengers who were delivered to their own
    destination. Evacuated riders (fire recall, power rescue) are counted separately and are
    excluded from wait and ride: their trip did not end where they wanted to go."""
    everyone = list(sim.passengers.values())
    measured = [p for p in everyone if measure_from <= p.arrived_at < measure_to]
    served = [p for p in measured if not p.evacuated]
    waits = [p.wait_s for p in served if p.wait_s is not None]
    rides = [p.ride_s for p in served if p.ride_s is not None]
    return {
        "passengers_measured": len(measured),
        "n_delivered": sum(1 for p in everyone if p.alighted_at is not None and not p.evacuated),
        "n_evacuated": sum(1 for p in everyone if p.evacuated),
        "avg_wait_s": sum(waits) / len(waits) if waits else 0.0,
        "max_wait_s": max(waits, default=0.0),
        "avg_ride_s": sum(rides) / len(rides) if rides else 0.0,
        "max_ride_s": max(rides, default=0.0),
        "total_car_run_time_s": sum(c.run_time_s for c in sim.cars.values()),
        # every passenger still waiting/riding at the end, warm-up arrivals included
        "calls_not_cleared": sim.undelivered(),
        "calls_cancelled": sum(1 for p in everyone if p.state is PassengerState.CANCELLED),
    }


def _block_metrics(sim, segments, measure_from: float, measure_to: float) -> list[dict]:
    """One entry per schedule block that overlaps [measure_from, measure_to).

    calls = passengers who arrived in the block; wait metrics cover those who were delivered to
    their own destination. stranded = calls created in the block and still not delivered
    STRANDED_AFTER_S (30 min) after the block ended (the run's end for the last block)."""
    out = []
    people = list(sim.passengers.values())
    for seg in segments:
        start, end = max(seg.start, measure_from), min(seg.end, measure_to)
        if end <= start:
            continue
        mine = [p for p in people if start <= p.arrived_at < end]
        waits = [p.wait_s for p in mine if not p.evacuated and p.wait_s is not None]
        stranded = [p for p in mine if p.state is not PassengerState.CANCELLED
                    and not p.evacuated and (p.alighted_at is None
                                             or p.alighted_at > end + STRANDED_AFTER_S)]
        mode = seg.label.split(" ", 1)[1] if " " in seg.label else ""
        out.append({"label": seg.label, "mode": mode, "profile": seg.profile.name,
                    "start_s": start, "end_s": end,
                    "start_hhmm": seg.label.split(" ", 1)[0],
                    "calls": len(mine), "delivered": sum(1 for p in mine if p.alighted_at is not None),
                    "avg_wait_s": sum(waits) / len(waits) if waits else 0.0,
                    "max_wait_s": max(waits, default=0.0), "stranded": len(stranded)})
    return out


def _digest(ctx: Context) -> str:
    """Hash of the whole event log: equal digests = identical runs."""
    h = hashlib.sha256()
    for event in ctx.bus.log:
        h.update(repr(event).encode())
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True,
                             timeout=10)
        return out.stdout.strip() if out.returncode == 0 else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _write_manifest(result: RunResult, out_dir: Path, extra: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _git("rev-parse", "HEAD")
    rate = result.params["profile"]["rate_per_min"]
    manifest = {
        "profile": result.profile, "seed": result.seed, "passed": result.passed,
        "rate_per_min": rate,
        # D-01: peak rate differs from the SRS value of 75/min (see docs/DECISIONS.md)
        "decision": "D-01" if result.profile in PEAK_PROFILES and rate not in (
            None, SRS_ORIGINAL_PEAK_RATE) else None,
        "start": result.start,
        "git_commit": commit, "git_dirty": bool(_git("status", "--porcelain")),
        "sim_config": config.SIM, "params": result.params, "metrics": result.metrics,
        "event_digest": result.event_digest,
    } | ({"blocks": result.blocks} if result.blocks else {}) | (
        {"latency": result.latency} if result.latency else {}) | extra
    rate_part = "" if rate is None else f"_r{rate:g}"
    path = out_dir / f"{result.profile}{rate_part}_seed{result.seed}_{commit[:8]}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str))
    return path
