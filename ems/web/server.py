"""Web server for the demo. Owned by: Tech lead (routes); pages by part owners.

Run:  uvicorn ems.web.server:app --reload   then open http://localhost:8000

Pages (static HTML/JS, each polls /api/state):
  /dashboard  Part 6     /panel  Part 3     /building, /runner  Part 1
Add your own JSON routes in your part as an APIRouter and include it below.

Part 1 routes: /api/live/start, /api/live/fault, /api/scenarios, /api/scenarios/{id}/run.
/api/state only ever gains fields (other parts' pages poll it). Every access to the shared
context is under app.state.lock (FastAPI runs these sync routes in a thread pool).
Car states come from the car_states service (Part 4); this file never computes them.
"""
from __future__ import annotations

import threading
from collections import Counter
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ems.contracts.config import GROUPS
from ems.contracts.events import AlertRaised, CarStatusUpdated
from ems.core.app import build
from ems.sim import live, scenarios

STATIC = Path(__file__).parent / "static"
MAX_ADVANCE_S = 600.0
EVENT_LINES = 30
app = FastAPI(title="Elevator Management System")
app.state.ctx = build(seed=1)
app.state.lock = threading.RLock()
app.state.live = None                     # {"profile", "rate", "seed"} after /api/live/start


# ---------------------------------------------------------------- state
def _event_line(ev) -> str:
    """One short text line: sim time, event name, its small fields."""
    bits = []
    for f in fields(ev):
        if f.name == "t":
            continue
        value = getattr(ev, f.name)
        if is_dataclass(value):                       # Call -> its id
            value = getattr(value, "call_id", type(value).__name__)
        if value is None or value == []:
            continue
        bits.append(f"{f.name}={value.name if hasattr(value, 'name') else value}")
    return f"{ev.t:8.1f}s {type(ev).__name__} {' '.join(bits)}".strip()


def _recent_events(ctx, n: int = EVENT_LINES) -> list[str]:
    lines = []
    for ev in reversed(ctx.bus.log):                  # newest first, skip telemetry noise
        if isinstance(ev, CarStatusUpdated):
            continue
        lines.append(_event_line(ev))
        if len(lines) == n:
            break
    return lines[::-1]


def _waiting(sim) -> dict:
    """{group: {floor: count}} of passengers waiting for a car."""
    per_group: dict[str, Counter] = {g: Counter() for g in GROUPS}
    for p in sim.waiting.values():
        per_group.setdefault(p.group, Counter())[p.origin] += 1
    return {g: {str(f): c for f, c in sorted(cnt.items())} for g, cnt in per_group.items()}


def _active_faults(ctx, sim) -> list[str]:
    out = []
    for car in sim.cars.values():
        if car.fault_code:
            out.append(f"car {car.car_id} fault {car.fault_code}")
        if car.link_lost:
            out.append(f"car {car.car_id} link lost")
    if sim.fire_alarm_active:
        out.append("fire alarm")
    elif sim.fire_needs_reset:
        out.append("fire alarm cleared, reset needed")
    if sim.mains_lost:
        out.append("mains lost" + (" (generator running)" if sim.generator_ready else ""))
    if ctx.get("acs").__class__.__name__ == "MockAcs" and ctx.get("acs").down:
        out.append("ACS down")
    if not ctx.get("db").available():
        out.append("database down")
    if sim.ems_down:
        out.append("EMS server down")
    guard = ctx.services.get("dispatch_guard")
    if guard is not None and guard.crashed:
        out.append("dispatch plug-in crashed")
    return out


@app.get("/api/state")
def state() -> dict:
    with app.state.lock:
        ctx = app.state.ctx
        sim = ctx.get("controllers")
        cars = []
        for s in sim.all_status():
            car = sim.cars[s.car_id]
            cars.append(asdict(s) | {
                "state": ctx.get("car_states").state(s.car_id).name,
                "capacity": car.capacity,
                "link_lost": car.link_lost,
            })
        guard = ctx.services.get("dispatch_guard")
        return {
            "t": ctx.clock.now(),
            "time": ctx.clock.now_dt().isoformat(),
            "modes": {g: ctx.get("dispatcher").current_mode(g).value for g in GROUPS},
            "cars": cars,
            "alerts": [asdict(a) for a in ctx.bus.of_type(AlertRaised)[-20:]],
            # ---- fields added by Part 1 (building page) ----
            "alerts_total": len(ctx.bus.of_type(AlertRaised)),
            "waiting": _waiting(sim),
            "active_faults": _active_faults(ctx, sim),
            "ems": {"status": "down" if sim.ems_down else "up",
                    "controllers_fallback": sim.fallback_active},
            "dispatch_fallback_active": bool(guard and guard.in_outage),
            "dispatch_guard": ({"calls": guard.calls, "fallbacks": guard.fallbacks}
                               if guard else None),
            "events": _recent_events(ctx),
            "live": app.state.live,
        }


# ---------------------------------------------------------------- time and live control
@app.post("/api/advance")
def advance(seconds: float = 1.0) -> dict:
    """Advance simulated time (the building page drives this)."""
    with app.state.lock:
        ctx = app.state.ctx
        ctx.clock.run_until(ctx.clock.now() + max(0.0, min(seconds, MAX_ADVANCE_S)))
        return {"t": ctx.clock.now()}


@app.post("/api/reset")
def reset(seed: int = 1) -> dict:
    with app.state.lock:
        app.state.ctx = build(seed=seed)
        app.state.live = None
    return {"seed": seed}


class LiveStart(BaseModel):
    profile: str = "up_peak"
    rate: Optional[float] = None          # persons/min; None = the profile's default
    seed: int = 1


class LiveFault(BaseModel):
    kind: str
    car_id: Optional[str] = None
    duration: Optional[float] = None      # seconds, for faults that end by themselves


@app.post("/api/live/start")
def live_start(body: LiveStart) -> dict:
    try:
        ctx = live.start_live(body.profile, body.rate, body.seed)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with app.state.lock:
        app.state.ctx = ctx
        app.state.live = body.model_dump()
    return {"started": body.model_dump()}


@app.post("/api/live/fault")
def live_fault(body: LiveFault) -> dict:
    with app.state.lock:
        try:
            done = live.apply_fault(app.state.ctx, body.kind, body.car_id, body.duration)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        return {"injected": done, "t": app.state.ctx.clock.now()}


# ---------------------------------------------------------------- scenarios
@app.get("/api/scenarios")
def scenario_list() -> list[dict]:
    return [scenarios.describe(s) for s in scenarios.SCENARIOS.values()]


@app.post("/api/scenarios/{scenario_id}/run")
def scenario_run(scenario_id: str, seed: int = 1) -> dict:
    """Run ONE seed headless on its own context (the live view is not touched)."""
    if scenario_id not in scenarios.SCENARIOS:
        raise HTTPException(404, f"unknown scenario {scenario_id}")
    return scenarios.run_scenario(scenario_id, seed)


for page in ("dashboard", "panel", "building", "runner"):
    app.mount(f"/{page}", StaticFiles(directory=STATIC / page, html=True), name=page)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
