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
from pathlib import Path
from typing import Callable, Optional, Sequence, Union

from ems.contracts import config
from ems.core.app import Context, build

from .passengers import PassengerState
from .population import Population
from .traffic import PROFILES, Profile, TrafficGenerator

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO_ROOT / "runs"          # gitignored
WARMUP_S = 600.0
MEASURE_S = 3600.0
CLEAR_S = 1800.0
CHECK_EVERY_S = 5.0


@dataclass
class Fault:
    """A fault to inject. at_s = sim seconds from the start of the run."""
    at_s: float
    car_id: str
    duration_s: Optional[float] = None   # None = never cleared
    kind: str = "fault"                  # "fault" (car freezes) or "disconnect"
    code: str = "F-01"


@dataclass
class RunResult:
    profile: str
    seed: int
    passed: bool                         # True when every call was cleared
    metrics: dict
    params: dict
    manifest_path: Optional[str] = None
    event_digest: str = ""
    ctx: Optional[Context] = field(default=None, repr=False)   # only if keep_ctx=True


def run(profile: Union[str, Profile], seed: int, faults: Sequence[Fault] = (), *,
        warmup_s: float = WARMUP_S, measure_s: float = MEASURE_S, clear_s: float = CLEAR_S,
        setup: Optional[Callable[[Context], None]] = None,
        population: Optional[Population] = None, out_dir: Optional[Path] = RUNS_DIR,
        keep_ctx: bool = False) -> RunResult:
    """Run one scenario. `setup(ctx)` runs after build (e.g. to install a dispatcher).
    out_dir=None skips writing the evidence manifest."""
    prof = PROFILES[profile] if isinstance(profile, str) else profile
    ctx = build(seed=seed)
    sim = ctx.services["controllers"]
    if setup is not None:
        setup(ctx)
    arrivals_end = warmup_s + measure_s
    TrafficGenerator.fixed(ctx, prof, 0.0, arrivals_end, population=population).start()
    pop = population or ctx.services["population"]
    for f in faults:
        _schedule_fault(ctx, sim, f)

    ctx.clock.run_until(arrivals_end)
    t, deadline = arrivals_end, arrivals_end + clear_s
    while sim.undelivered() > 0 and t < deadline:
        t = min(t + CHECK_EVERY_S, deadline)
        ctx.clock.run_until(t)

    metrics = _metrics(sim, warmup_s, arrivals_end)
    params = {"profile": asdict(prof), "warmup_s": warmup_s, "measure_s": measure_s,
              "clear_s": clear_s, "population": pop.params, "faults": [asdict(f) for f in faults],
              "ended_at_s": ctx.clock.now()}
    result = RunResult(prof.name, seed, metrics["calls_not_cleared"] == 0, metrics, params,
                       event_digest=_digest(ctx), ctx=ctx if keep_ctx else None)
    if out_dir is not None:
        result.manifest_path = str(_write_manifest(result, Path(out_dir)))
    return result


def run_seeds(profile: Union[str, Profile], faults: Sequence[Fault] = (),
              seeds: Sequence[int] = range(1, 11), **kwargs) -> list[RunResult]:
    """One result per seed. No averaging: every seed must pass on its own."""
    return [run(profile, seed, faults, **kwargs) for seed in seeds]


# ---------- internals ----------
def _schedule_fault(ctx, sim, f: Fault) -> None:
    if f.kind == "fault":
        ctx.clock.call_at(f.at_s, lambda: sim.inject_fault(f.car_id, f.code))
        if f.duration_s is not None:
            ctx.clock.call_at(f.at_s + f.duration_s, lambda: sim.clear_fault(f.car_id))
    elif f.kind == "disconnect":
        ctx.clock.call_at(f.at_s, lambda: sim.set_disconnected(f.car_id, True))
        if f.duration_s is not None:
            ctx.clock.call_at(f.at_s + f.duration_s, lambda: sim.set_disconnected(f.car_id, False))
    else:
        raise ValueError(f"unknown fault kind {f.kind!r}")


def _metrics(sim, measure_from: float, measure_to: float) -> dict:
    measured = [p for p in sim.passengers.values() if measure_from <= p.arrived_at < measure_to]
    waits = [p.wait_s for p in measured if p.wait_s is not None]
    rides = [p.ride_s for p in measured if p.ride_s is not None]
    return {
        "passengers_measured": len(measured),
        "avg_wait_s": sum(waits) / len(waits) if waits else 0.0,
        "max_wait_s": max(waits, default=0.0),
        "avg_ride_s": sum(rides) / len(rides) if rides else 0.0,
        "max_ride_s": max(rides, default=0.0),
        "total_car_run_time_s": sum(c.run_time_s for c in sim.cars.values()),
        # every passenger still waiting/riding at the end, warm-up arrivals included
        "calls_not_cleared": sim.undelivered(),
        "calls_cancelled": sum(1 for p in sim.passengers.values()
                               if p.state is PassengerState.CANCELLED),
    }


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


def _write_manifest(result: RunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _git("rev-parse", "HEAD")
    manifest = {
        "profile": result.profile, "seed": result.seed, "passed": result.passed,
        "git_commit": commit, "git_dirty": bool(_git("status", "--porcelain")),
        "sim_config": config.SIM, "params": result.params, "metrics": result.metrics,
        "event_digest": result.event_digest,
    }
    path = out_dir / f"{result.profile}_seed{result.seed}_{commit[:8]}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str))
    return path
