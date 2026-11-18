"""Long runs: weeks or months of simulated traffic, for CR-01 / NFR-W1 (SIM-19). Owned by: Part 1.

One run keeps one context for the whole period. Traffic follows config.WEEKDAY_SCHEDULE on
weekdays and config.WEEKEND_SCHEDULE on Saturdays and Sundays. To keep memory flat, the bus
keeps no event log, finished passengers are summarised per day and then dropped, and Part 1's
own histories (command log, ACS lookups, dispatch guard, trip database) keep the last
RETAIN_S only (one counting month and a day, enough for the monthly report). The wear figures
come from Part 5's ctx.services["usage"].wear_report().

    python -m ems.sim.long_run --days 7 --seed 1
    python -m ems.sim.long_run --months 1 --seed 1
    python -m ems.sim.long_run --months 6 --seed 1

One simulated day takes about 30 s with the dispatch stand-in, so a month is about 15 min and
six months about 1.5 h. The evidence manifest is written to runs/ like every other run.
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from ems.contracts import config
from ems.contracts.types import WearReport
from ems.core.app import Context, build

from .baseline import install_dispatch_standin
from .harness import RUNS_DIR, START_DATE, _git
from .passengers import PassengerState
from .traffic import TrafficGenerator, schedule_segments

DAY_S = 86400.0
RETAIN_S = config.WEAR_MONTH_S + DAY_S
FINISHED = (PassengerState.DONE, PassengerState.REFUSED, PassengerState.CANCELLED)


@dataclass
class DaySummary:
    day: int                       # 0 = first day of the run
    date: str
    weekend: bool
    passengers: int                # arrived that day
    delivered: int
    refused: int
    cancelled: int
    avg_wait_s: float
    max_wait_s: float


@dataclass
class LongRunResult:
    seed: int
    days: int
    start: str
    summaries: list[DaySummary]
    wear: WearReport
    imbalance: list[dict]          # one row per group and window, see wear_imbalance()
    wall_s: float
    manifest_path: Optional[str] = None
    ctx: Optional[Context] = field(default=None, repr=False)


def imbalance(values) -> float:
    """(max - min) / max of the cars' distances; 0.0 when nothing has moved yet."""
    values = list(values)
    if len(values) < 2 or max(values) <= 0:
        return 0.0
    return (max(values) - min(values)) / max(values)


def wear_imbalance(report: WearReport, building) -> list[dict]:
    """Per passenger group: the imbalance of every month and of the whole period, with the
    NFR-W1 limit for each. Groups with one car (service car S) are balanced by definition."""
    rows: list[dict] = []
    if not report.period:
        return rows                       # Part 5 not registered yet (StubUsage)
    for group in building.groups:
        if len(group.cars) < 2:
            continue
        windows = [(f"month {i + 1}", month, config.WEAR_MONTH_LIMIT)
                   for i, month in enumerate(report.months)]
        windows.append(("period", report.period, config.WEAR_PERIOD_LIMIT))
        for label, counts, limit in windows:
            floors = {car: counts.get(car, 0.0) for car in group.cars}
            value = imbalance(floors.values())
            rows.append({"group": group.name, "window": label, "floors": floors,
                         "imbalance": value, "limit": limit, "ok": value < limit})
    return rows


def run_long(days: int, seed: int = 1, *, start: datetime = START_DATE,
             setup: Optional[Callable[[Context], None]] = install_dispatch_standin,
             out_dir: Optional[Path] = RUNS_DIR, keep_ctx: bool = False,
             progress: Optional[Callable[[DaySummary], None]] = None) -> LongRunResult:
    """Run `days` simulated days from `start` (00:00) and return daily summaries + wear."""
    began = time.monotonic()
    ctx = build(seed=seed, start=start)
    ctx.bus.keep_log = False
    if setup is not None:
        setup(ctx)
    sim = ctx.services["controllers"]
    until = days * DAY_S
    TrafficGenerator(ctx, schedule_segments(ctx, day_start=0.0, until=until,
                                            weekends=True)).start()
    summaries = []
    for day in range(days):
        ctx.clock.run_until((day + 1) * DAY_S)
        summary = _summarise_day(sim, day, start)
        _trim_part1(ctx, (day + 1) * DAY_S - RETAIN_S)
        summaries.append(summary)
        if progress is not None:
            progress(summary)
    report = ctx.services["usage"].wear_report()
    result = LongRunResult(seed, days, start.isoformat(), summaries, report,
                           wear_imbalance(report, ctx.services["building"]),
                           time.monotonic() - began, ctx=ctx if keep_ctx else None)
    if out_dir is not None:
        result.manifest_path = str(_write_manifest(result, Path(out_dir)))
    return result


def _summarise_day(sim, day: int, start: datetime) -> DaySummary:
    """Summarise the passengers who arrived on `day`, then drop every finished passenger."""
    lo, hi = day * DAY_S, (day + 1) * DAY_S
    arrived = [p for p in sim.passengers.values() if lo <= p.arrived_at < hi]
    waits = [p.wait_s for p in arrived if p.wait_s is not None and not p.evacuated]
    date = start + timedelta(days=day)
    summary = DaySummary(
        day, date.date().isoformat(), date.weekday() >= 5, len(arrived),
        sum(1 for p in arrived if p.state is PassengerState.DONE and not p.evacuated),
        sum(1 for p in arrived if p.state is PassengerState.REFUSED),
        sum(1 for p in arrived if p.state is PassengerState.CANCELLED),
        sum(waits) / len(waits) if waits else 0.0, max(waits, default=0.0))
    for pid in [pid for pid, p in sim.passengers.items()
                if p.state in FINISHED and p.arrived_at < hi]:
        del sim.passengers[pid]
    return summary


def _trim_part1(ctx, keep_from: float) -> None:
    """Drop Part 1 history older than keep_from (sim seconds). Entries are in time order."""
    if keep_from <= 0:
        return
    sim, acs, guard, db = (ctx.services.get(k) for k in ("controllers", "acs", "dispatch_guard",
                                                          "db"))
    for owner, name, time_of in ((sim, "command_log", lambda e: e[0]),
                                 (acs, "lookup_log", lambda e: e[0]),
                                 (guard, "log", lambda e: e[0]),
                                 (db, "disk", lambda e: e["t"])):
        entries = getattr(owner, name, None)
        if not entries:
            continue
        cut = next((i for i, e in enumerate(entries) if time_of(e) >= keep_from), len(entries))
        del entries[:cut]
    if guard is not None and hasattr(guard, "compute_s"):
        del guard.compute_s[:max(0, len(guard.compute_s) - len(guard.log))]


def _write_manifest(result: LongRunResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    commit = _git("rev-parse", "HEAD")
    manifest = {
        "run": "long", "seed": result.seed, "days": result.days, "start": result.start,
        "git_commit": commit, "git_dirty": bool(_git("status", "--porcelain")),
        "sim_config": config.SIM, "wall_s": round(result.wall_s, 1),
        "wear": asdict(result.wear), "imbalance": result.imbalance,
        "days_summary": [asdict(s) for s in result.summaries],
    }
    path = out_dir / f"long_{result.days}d_seed{result.seed}_{commit[:8]}.json"
    path.write_text(json.dumps(manifest, indent=2, default=str))
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run weeks or months of simulated traffic "
                                                 "and report wear balance (CR-01, SIM-19).")
    length = parser.add_mutually_exclusive_group(required=True)
    length.add_argument("--days", type=int)
    length.add_argument("--months", type=int, help="counting months of 30 days")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, default=RUNS_DIR)
    args = parser.parse_args(argv)
    days = args.days if args.days is not None else round(args.months * config.WEAR_MONTH_S / DAY_S)

    def show(s: DaySummary) -> None:
        print(f"{s.date} {'weekend' if s.weekend else 'weekday'}: {s.passengers} passengers, "
              f"avg wait {s.avg_wait_s:.1f} s, max {s.max_wait_s:.0f} s", flush=True)

    result = run_long(days, args.seed, out_dir=args.out, progress=show)
    print(f"\n{days} days in {result.wall_s / 60:.1f} min")
    for row in result.imbalance:
        print(f"{row['group']:5} {row['window']:9} imbalance {row['imbalance']:.2%} "
              f"(limit {row['limit']:.0%}) {'ok' if row['ok'] else 'FAIL'}")
    if not result.imbalance:
        print("no wear figures: ctx.services['usage'] is still the stub (Part 5)")
    print(f"manifest: {result.manifest_path}")
    return 0 if all(r["ok"] for r in result.imbalance) else 1


if __name__ == "__main__":
    raise SystemExit(main())
