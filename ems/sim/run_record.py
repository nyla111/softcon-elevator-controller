"""Run records: what a live run did, saved as one JSON file so runs can be compared later.

Owned by: Part 1. A RunRecorder is attached to every live run (live.start_live). It samples the
building once per simulated minute and listens to mode changes and alerts; save() writes the
summary and the time series to runs/live/. The server lists and returns saved runs
(/api/runs) for the run history page (Part 6).

Saved fields:
  meta         version tag / commit, seed, profile, rate, passenger list, faults, start/end
  overall      the harness metrics (wait, ride, run time, delivered, refused, cancelled)
  by_group     per group: wait avg/p50/p95/p99/max, ride avg, journey avg, served/refused/...
  by_mode      the same wait figures per operating mode in force when the passenger arrived
  cars         per car: floors travelled, run time, standby time, door cycles
  events       mode changes (with Eco step-ups), alerts by kind, dispatch fallback uses
  series       one row per simulated minute: waiting per group, rolling 5-min average wait,
               cars in service, cumulative floors per car
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from ems.contracts.events import AlertRaised, CarStatusUpdated, DoorsOpened, ModeChanged

from .harness import RUNS_DIR, _git, _metrics
from .passengers import PassengerState

SAMPLE_S = 60.0
ROLLING_S = 300.0
LIVE_RUNS_DIR = RUNS_DIR / "live"
_SAFE = re.compile(r"[^A-Za-z0-9_.-]+")


def _pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile; 0.0 for no values."""
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round(q * len(ordered) + 0.5)) - 1))
    return ordered[k]


def _wait_stats(people) -> dict:
    served = [p for p in people if not p.evacuated]
    waits = [p.wait_s for p in served if p.wait_s is not None]
    rides = [p.ride_s for p in served if p.ride_s is not None]
    journeys = [p.alighted_at - p.arrived_at for p in served if p.alighted_at is not None]
    return {
        "calls": len(people),
        "delivered": sum(1 for p in served if p.alighted_at is not None),
        "refused": sum(1 for p in people if p.state is PassengerState.REFUSED),
        "cancelled": sum(1 for p in people if p.state is PassengerState.CANCELLED),
        "evacuated": sum(1 for p in people if p.evacuated),
        "not_cleared": sum(1 for p in people if p.alighted_at is None
                           and p.state not in (PassengerState.REFUSED, PassengerState.CANCELLED)
                           and not p.evacuated),
        "wait_avg_s": sum(waits) / len(waits) if waits else 0.0,
        "wait_p50_s": _pct(waits, 0.50), "wait_p95_s": _pct(waits, 0.95),
        "wait_p99_s": _pct(waits, 0.99), "wait_max_s": max(waits, default=0.0),
        "ride_avg_s": sum(rides) / len(rides) if rides else 0.0,
        "journey_avg_s": sum(journeys) / len(journeys) if journeys else 0.0,
    }


class RunRecorder:
    def __init__(self, ctx, meta: dict) -> None:
        self.ctx = ctx
        self.meta = dict(meta)
        self.meta["started_wall"] = datetime.now().isoformat(timespec="seconds")
        self.meta["sim_start"] = ctx.clock.now_dt().isoformat()
        self.faults: list[dict] = []
        self.mode_changes: list[dict] = []
        self.alerts: Counter = Counter()
        self.series: list[dict] = []
        self.floors: dict[str, int] = {}
        self.door_cycles: Counter = Counter()
        self.standby_s: Counter = Counter()
        self._last_floor: dict[str, int] = {}
        self._mode_at: list[tuple[float, dict]] = []     # (t, {group: mode}) when modes change
        self._standby_since: dict[str, float] = {}
        bus = ctx.bus
        bus.subscribe(CarStatusUpdated, self._on_status)
        bus.subscribe(DoorsOpened, self._on_doors)
        bus.subscribe(ModeChanged, self._on_mode)
        bus.subscribe(AlertRaised, self._on_alert)
        self._mode_at.append((ctx.clock.now(), self._modes()))
        ctx.clock.call_later(SAMPLE_S, self._sample)

    # ------------------------------------------------------------------ listeners
    def note_fault(self, kind: str, car_id: Optional[str]) -> None:
        self.faults.append({"t": self.ctx.clock.now(), "kind": kind, "car_id": car_id})

    def _on_status(self, ev: CarStatusUpdated) -> None:
        s = ev.status
        car = s.car_id
        last = self._last_floor.get(car)
        self._last_floor[car] = s.floor
        if last is not None and s.floor != last:
            # B1 = -1 and L1 = 1 are adjacent floors
            a = last if last < 0 else last - 1
            b = s.floor if s.floor < 0 else s.floor - 1
            self.floors[car] = self.floors.get(car, 0) + abs(a - b)
        standby = bool(getattr(self.ctx.services["controllers"].cars.get(car), "standby", False))
        if standby and car not in self._standby_since:
            self._standby_since[car] = ev.t
        elif not standby and car in self._standby_since:
            self.standby_s[car] += ev.t - self._standby_since.pop(car)

    def _on_doors(self, ev: DoorsOpened) -> None:
        self.door_cycles[ev.car_id] += 1

    def _on_mode(self, ev: ModeChanged) -> None:
        self.mode_changes.append({"t": ev.t, "group": ev.group, "old": ev.old.value,
                                  "new": ev.new.value, "cause": ev.cause})
        self._mode_at.append((ev.t, self._modes()))

    def _on_alert(self, ev: AlertRaised) -> None:
        self.alerts[ev.kind] += 1

    def _modes(self) -> dict:
        dispatcher = self.ctx.services.get("dispatcher")
        building = self.ctx.services["building"]
        out = {}
        for g in building.groups:
            try:
                out[g.name] = dispatcher.current_mode(g.name).value
            except Exception:                       # stub dispatcher
                out[g.name] = "unknown"
        return out

    def _mode_when(self, group: str, t: float) -> str:
        mode = "unknown"
        for at, modes in self._mode_at:
            if at > t:
                break
            mode = modes.get(group, mode)
        return mode

    def _sample(self) -> None:
        now = self.ctx.clock.now()
        sim = self.ctx.services["controllers"]
        waiting: Counter = Counter(p.group for p in sim.waiting.values())
        recent = [p.wait_s for p in sim.passengers.values()
                  if p.boarded_at is not None and p.boarded_at >= now - ROLLING_S
                  and not p.evacuated]
        in_service = sum(1 for c in sim.cars.values()
                         if not (c.standby or c.fault_code or c.link_lost or c.fire
                                 or c.power_down or c.independent))
        self.series.append({
            "t": now, "time": self.ctx.clock.now_dt().isoformat(timespec="seconds"),
            "waiting": dict(waiting),
            "rolling_wait_avg_s": sum(recent) / len(recent) if recent else 0.0,
            "cars_in_service": in_service,
            "floors": dict(self.floors),
        })
        self.ctx.clock.call_later(SAMPLE_S, self._sample)

    # ------------------------------------------------------------------ output
    def summary(self) -> dict:
        ctx = self.ctx
        sim = ctx.services["controllers"]
        now = ctx.clock.now()
        people = list(sim.passengers.values())
        groups = sorted({p.group for p in people})
        by_mode: dict[str, list] = {}
        for p in people:
            by_mode.setdefault(self._mode_when(p.group, p.arrived_at), []).append(p)
        guard = ctx.services.get("dispatch_guard")
        compute = sorted(getattr(guard, "compute_s", []) or [])
        standby = Counter(self.standby_s)
        for car, since in self._standby_since.items():
            standby[car] += now - since
        meta = dict(self.meta)
        meta.update(sim_end=ctx.clock.now_dt().isoformat(), sim_seconds=now,
                    git_commit=_git("rev-parse", "HEAD"),
                    version=_git("describe", "--tags", "--always"),
                    faults=self.faults)
        return {
            "meta": meta,
            "overall": _metrics(sim, 0.0, now + 1.0),
            "by_group": {g: _wait_stats([p for p in people if p.group == g]) for g in groups},
            "by_mode": {m: _wait_stats(ps) for m, ps in sorted(by_mode.items())},
            "cars": {c: {"group": car.group, "floors": self.floors.get(c, 0),
                         "run_time_s": car.run_time_s, "standby_s": standby.get(c, 0.0),
                         "door_cycles": self.door_cycles.get(c, 0)}
                     for c, car in sorted(sim.cars.items())},
            "events": {
                "mode_changes": self.mode_changes,
                "eco_step_ups": sum(1 for m in self.mode_changes if m["cause"] == "eco_step_up"),
                "alerts_by_kind": dict(self.alerts),
                "dispatch_fallback_uses": int(sum(1 for e in getattr(guard, "log", []) or []
                                                  if len(e) > 3 and e[3])),
                "dispatch_decision_p99_s": _pct(compute, 0.99),
            },
            "series": self.series,
        }

    def save(self, name: Optional[str] = None, out_dir: Optional[Path] = None) -> Path:
        out_dir = out_dir or LIVE_RUNS_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        data = self.summary()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        label = name or f"{self.meta.get('profile', 'run')}_seed{self.meta.get('seed', 1)}"
        path = out_dir / f"{stamp}_{_SAFE.sub('-', label)[:60]}.json"
        data["meta"]["run_id"] = path.stem
        data["meta"]["name"] = name or label
        path.write_text(json.dumps(data, indent=1, default=str))
        return path


def list_runs(out_dir: Optional[Path] = None) -> list[dict]:
    """Saved runs, newest first: run id, name, profile, seed, version, sim time covered."""
    out_dir = out_dir or LIVE_RUNS_DIR
    rows = []
    for path in sorted(out_dir.glob("*.json"), reverse=True):
        try:
            meta = json.loads(path.read_text()).get("meta", {})
        except (OSError, ValueError):
            continue
        rows.append({k: meta.get(k) for k in ("run_id", "name", "profile", "rate", "seed",
                                              "script", "version", "sim_seconds",
                                              "started_wall")})
    return rows


def load_run(run_id: str, out_dir: Optional[Path] = None) -> dict:
    """One saved run. Raises FileNotFoundError for an unknown or unsafe id."""
    out_dir = out_dir or LIVE_RUNS_DIR
    if not run_id or _SAFE.search(run_id) or run_id.startswith("."):
        raise FileNotFoundError(run_id)
    path = out_dir / f"{run_id}.json"
    if not path.is_file():
        raise FileNotFoundError(run_id)
    return json.loads(path.read_text())
