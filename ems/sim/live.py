"""Helpers for the live demo (ems/web/server.py): start a live run, inject faults now.

Everything here is Part 1: it only builds the simulator, traffic and building signals. While
Part 2 is still the stub dispatcher, the nearest-car stand-in (baseline.py) assigns the calls.
Fault buttons use the same helpers as the scenarios (faults.py), scheduled for "now".
"""
from __future__ import annotations

from ems.core.app import Context, build

from . import faults as F
from .baseline import install_dispatch_standin
from .traffic import PROFILES, TrafficGenerator, with_rate

LIVE_HORIZON_S = 7 * 86400.0       # traffic keeps coming for a simulated week

# kind -> needs a car?
FAULT_KINDS = {
    "fire_alarm": False, "fire_clear": False, "mains_loss": False, "mains_restore": False,
    "car_fault": True, "clear_fault": True, "link_loss": True, "acs_outage": False,
    "ems_crash": False, "plugin_crash": False, "overload_burst": True, "bulky_item": True,
}
DEFAULT_DURATION_S = {"link_loss": 300.0, "acs_outage": 900.0, "plugin_crash": 300.0}


def start_live(profile: str = "up_peak", rate: float | None = None, seed: int = 1) -> Context:
    if profile not in PROFILES:
        raise ValueError(f"unknown profile {profile!r}")
    ctx = build(seed=seed)
    install_dispatch_standin(ctx)
    prof = with_rate(profile, rate) if rate else PROFILES[profile]
    TrafficGenerator.fixed(ctx, prof, 0.0, LIVE_HORIZON_S).start()
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
    dur = duration if duration else DEFAULT_DURATION_S.get(kind)
    devices = ctx.services["devices"]
    if kind == "fire_clear":
        devices.fire.clear()
        sim.reset_fire()
    elif kind == "mains_restore":
        devices.power.restore_mains()
    elif kind == "clear_fault":
        sim.clear_fault(car_id)
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
