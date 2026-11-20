"""Helpers for the live demo (ems/web/server.py): start a live run, inject faults now.

Everything here is Part 1: it only builds the simulator, traffic and building signals. While
Part 2 is still the stub dispatcher, the nearest-car stand-in (baseline.py) assigns the calls.
Fault buttons use the same helpers as the scenarios (faults.py), scheduled for "now".
Timed faults (link loss, ACS outage, plug-in crash) end on their own; the *_restore kinds end
them early.
"""
from __future__ import annotations

from datetime import datetime

from ems.core.app import Context, build

from . import faults as F
from .baseline import install_dispatch_standin
from .run_record import RunRecorder
from .scripted import ScriptedTraffic, ScriptedTrip
from .traffic import PROFILES, TrafficGenerator, with_rate

LIVE_HORIZON_S = 7 * 86400.0       # traffic keeps coming for a simulated week
LIVE_DAY = datetime(2026, 11, 16)  # a Monday, as in the harness
# Time of day a live run starts at, so the scheduled mode matches the traffic (WEEKDAY_SCHEDULE):
# morning peak in Rush, office hours in Balanced, night in Eco. Passenger lists start at 00:00.
LIVE_START_HHMM = {"up_peak": (7, 30), "lunch": (12, 0), "evening": (17, 15),
                   "balanced": (10, 0), "eco": (22, 0)}

# kind -> needs a car?
FAULT_KINDS = {
    "fire_alarm": False, "fire_clear": False, "mains_loss": False, "mains_restore": False,
    "car_fault": True, "clear_fault": True, "link_loss": True, "acs_outage": False,
    "ems_crash": False, "plugin_crash": False, "overload_burst": True, "bulky_item": True,
    "link_restore": True, "acs_restore": False, "plugin_restore": False,
}
DEFAULT_DURATION_S = {"link_loss": 300.0, "acs_outage": 900.0, "plugin_crash": 300.0}


def start_live(profile: str = "up_peak", rate: float | None = None, seed: int = 1,
               script: list[ScriptedTrip] | None = None) -> Context:
    """A live run with random traffic of one profile, or with a fixed passenger list (script)."""
    if script is None and profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    h, m = (0, 0) if script is not None else LIVE_START_HHMM.get(profile, (0, 0))
    ctx = build(seed=seed, start=LIVE_DAY.replace(hour=h, minute=m))
    install_dispatch_standin(ctx)
    if script is not None:
        ScriptedTraffic(ctx, script).start()
    else:
        prof = with_rate(profile, rate) if rate else PROFILES[profile]
        TrafficGenerator.fixed(ctx, prof, 0.0, LIVE_HORIZON_S).start()
    ctx.services["run_record"] = RunRecorder(ctx, {
        "profile": "scripted" if script is not None else profile, "rate": rate, "seed": seed,
        "script": f"{len(script)} rows" if script is not None else None})
    return ctx


def apply_fault(ctx: Context, kind: str, car_id: str | None = None,
                duration: float | None = None) -> str:
    """Inject a fault now. Raises ValueError for an unknown kind or a missing/unknown car."""
    if kind not in FAULT_KINDS:
        raise ValueError(f"unknown fault kind {kind!r}")
    sim = ctx.services["controllers"]
    if FAULT_KINDS[kind] and car_id not in sim.cars:
        raise ValueError(f"{kind} needs a valid car_id")
    now = ctx.clock.now()
    recorder = ctx.services.get("run_record")
    if recorder is not None:
        recorder.note_fault(kind, car_id if FAULT_KINDS[kind] else None)
    dur = duration if duration else DEFAULT_DURATION_S.get(kind)
    devices = ctx.services["devices"]
    if kind == "fire_clear":
        devices.fire.clear()
        sim.reset_fire()
    elif kind == "mains_restore":
        devices.power.restore_mains()
    elif kind == "clear_fault":
        sim.clear_fault(car_id)
    elif kind == "link_restore":          # end a timed fault early (demo convenience)
        sim.set_disconnected(car_id, False)
    elif kind == "acs_restore":
        ctx.services["acs"].set_down(False)
    elif kind == "plugin_restore":
        ctx.services["dispatch_guard"].set_crashed(False)
    else:
        fault = {
            "fire_alarm": lambda: F.fire_alarm(now),
            "mains_loss": lambda: F.mains_loss(now, None),
            "car_fault": lambda: F.car_fault(car_id, "F-LIVE", now, dur),
            "link_loss": lambda: F.link_loss(car_id, now, dur),
            "acs_outage": lambda: F.acs_outage(now, dur),
            "ems_crash": lambda: F.ems_server_crash(now),
            "plugin_crash": lambda: F.plugin_crash(now, dur),
            "overload_burst": lambda: F.overload_burst(car_id, now),
            "bulky_item": lambda: F.camera_event("bulky_item", car_id, now),
        }[kind]()
        F.schedule_fault(ctx, fault)
        ctx.clock.run_until(now)          # fire it right away
    return f"{kind}" + (f" car {car_id}" if car_id else "") + (f" for {dur:.0f} s" if dur else "")
