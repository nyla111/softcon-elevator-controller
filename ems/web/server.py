"""Web server for the demo. Owned by: Tech lead (routes); pages by part owners.

Run:  uvicorn ems.web.server:app --reload   then open http://localhost:8000

Pages (static HTML/JS, each polls /api/state):
  /dashboard  Part 6     /panel  Part 3     /building, /runner  Part 1
Add your own JSON routes in your part as an APIRouter and include it below.
"""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ems.contracts.config import GROUPS
from ems.contracts.events import AlertRaised
from ems.core.app import build

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Elevator Management System")
app.state.ctx = build(seed=1)


@app.get("/api/state")
def state() -> dict:
    ctx = app.state.ctx
    cars = [asdict(s) | {"state": ctx.get("car_states").state(s.car_id).name}
            for s in ctx.get("controllers").all_status()]
    return {
        "t": ctx.clock.now(),
        "time": ctx.clock.now_dt().isoformat(),
        "modes": {g: ctx.get("dispatcher").current_mode(g).value for g in GROUPS},
        "cars": cars,
        "alerts": [asdict(a) for a in ctx.bus.of_type(AlertRaised)[-20:]],
    }


@app.post("/api/advance")
def advance(seconds: float = 1.0) -> dict:
    """Advance simulated time (the runner page drives this)."""
    ctx = app.state.ctx
    ctx.clock.run_until(ctx.clock.now() + seconds)
    return {"t": ctx.clock.now()}


@app.post("/api/reset")
def reset(seed: int = 1) -> dict:
    app.state.ctx = build(seed=seed)
    return {"seed": seed}


for page in ("dashboard", "panel", "building", "runner"):
    app.mount(f"/{page}", StaticFiles(directory=STATIC / page, html=True), name=page)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")
