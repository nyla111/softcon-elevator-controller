"""Fault injection: Fault objects, helper constructors, and the scheduler used by the harness.

A Fault is plain data (it goes into the run manifest): kind, time, optional car, optional
duration, and a params dict. Times are seconds from the start of the run; measured(minute) gives
the time `minute` minutes into the measured window (after the 600 s warm-up), because Appendix B
injects faults "20 minutes into the measured window".

    run("up_peak", 1, faults=[car_fault("A", "F-07", measured(20), 300)])

schedule_fault(ctx, fault) books the start (and the end, when there is a duration) on ctx.clock.
Kinds: fault, link_loss (alias disconnect), acs_outage, fire_alarm, mains_loss, overload_burst,
camera_event, alarm_button, db_outage, ems_crash.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Fault:
    at_s: float
    car_id: Optional[str] = None
    duration_s: Optional[float] = None       # None = never ends / not applicable
    kind: str = "fault"
    code: str = "F-01"
    params: dict = field(default_factory=dict)


def measured(minute: float) -> float:
    """Run time of `minute` minutes into the measured window (after the warm-up)."""
    from .harness import WARMUP_S
    return WARMUP_S + minute * 60.0


# ---------- helpers that return Fault objects ----------
def car_fault(car: str, code: str, at: float, duration: Optional[float],
              between_floors: bool = False, doors_stuck: bool = False) -> Fault:
    return Fault(at, car, duration, "fault", code,
                 {"between_floors": between_floors, "doors_stuck": doors_stuck})


def link_loss(car: str, at: float, duration: Optional[float]) -> Fault:
    """The car keeps serving its registered stops but sends no status and refuses commands."""
    return Fault(at, car, duration, "link_loss")


def acs_outage(at: float, duration: float) -> Fault:
    return Fault(at, None, duration, "acs_outage")


def fire_alarm(at: float, clear_after: Optional[float] = None, io_signal_lost: bool = False,
               reset_after: Optional[float] = None) -> Fault:
    """reset_after = seconds after the alarm is cleared until reset_fire() is called."""
    return Fault(at, None, clear_after, "fire_alarm",
                 params={"source": "io_signal_lost" if io_signal_lost else "panel",
                         "reset_after": reset_after})


def mains_loss(at: float, restore_after: float, generator_failed: bool = False,
               generator_delay: float = 15.0, failed_rescue_cars=(), stuck_cars=()) -> Fault:
    return Fault(at, None, restore_after, "mains_loss",
                 params={"generator_failed": generator_failed,
                         "generator_delay": generator_delay,
                         "failed_rescue_cars": list(failed_rescue_cars),
                         "stuck_cars": list(stuck_cars)})


def overload_burst(car: str, at: float) -> Fault:
    """Phantom load: at the car's next door opening nobody can board for one door cycle."""
    return Fault(at, car, None, "overload_burst")


def camera_event(kind: str, car: str, at: float, people_count: int = 1) -> Fault:
    return Fault(at, car, None, "camera_event", params={"event": kind,
                                                        "people_count": people_count})


def alarm_button(car: str, at: float) -> Fault:
    return Fault(at, car, None, "alarm_button")


def db_outage(at: float, duration: float) -> Fault:
    return Fault(at, None, duration, "db_outage")


def ems_server_crash(at: float, takeover_s: float = 8.0) -> Fault:
    return Fault(at, None, None, "ems_crash", params={"takeover_s": takeover_s})


# ---------- scheduler ----------
def schedule_fault(ctx, f: Fault) -> None:
    handler = _HANDLERS.get(f.kind)
    if handler is None:
        raise ValueError(f"unknown fault kind {f.kind!r}")
    handler(ctx, f)


def _start_end(ctx, f: Fault, start, end) -> None:
    ctx.clock.call_at(f.at_s, start)
    if f.duration_s is not None and end is not None:
        ctx.clock.call_at(f.at_s + f.duration_s, end)


def _car_fault(ctx, f):
    sim = ctx.services["controllers"]
    _start_end(ctx, f,
               lambda: sim.inject_fault(f.car_id, f.code, f.params.get("between_floors", False),
                                        f.params.get("doors_stuck", False)),
               lambda: sim.clear_fault(f.car_id))


def _link_loss(ctx, f):
    sim = ctx.services["controllers"]
    _start_end(ctx, f, lambda: sim.set_disconnected(f.car_id, True),
               lambda: sim.set_disconnected(f.car_id, False))


def _acs_outage(ctx, f):
    _start_end(ctx, f, lambda: ctx.services["acs"].set_down(True),
               lambda: ctx.services["acs"].set_down(False))


def _fire_alarm(ctx, f):
    fire = ctx.services["devices"].fire
    ctx.clock.call_at(f.at_s, lambda: fire.alarm(f.params.get("source", "panel")))
    if f.duration_s is not None:
        ctx.clock.call_at(f.at_s + f.duration_s, fire.clear)
        if f.params.get("reset_after") is not None:
            ctx.clock.call_at(f.at_s + f.duration_s + f.params["reset_after"],
                              ctx.services["controllers"].reset_fire)


def _mains_loss(ctx, f):
    power = ctx.services["devices"].power
    p = f.params
    _start_end(ctx, f,
               lambda: power.lose_mains(p.get("generator_failed", False),
                                        p.get("generator_delay", 15.0),
                                        p.get("failed_rescue_cars", ()), p.get("stuck_cars", ())),
               power.restore_mains)


def _overload_burst(ctx, f):
    ctx.clock.call_at(f.at_s, lambda: ctx.services["controllers"].overload_burst(f.car_id))


def _camera_event(ctx, f):
    ctx.clock.call_at(f.at_s, lambda: ctx.services["devices"].camera.event(
        f.car_id, f.params["event"], f.params.get("people_count", 1)))


def _alarm_button(ctx, f):
    ctx.clock.call_at(f.at_s, lambda: ctx.services["devices"].alarm_button(f.car_id))


def _db_outage(ctx, f):
    _start_end(ctx, f, lambda: ctx.services["db"].set_down(True),
               lambda: ctx.services["db"].set_down(False))


def _ems_crash(ctx, f):
    ctx.clock.call_at(f.at_s, lambda: ctx.services["failover"].crash(f.params.get("takeover_s", 8.0)))


_HANDLERS = {
    "fault": _car_fault, "link_loss": _link_loss, "disconnect": _link_loss,
    "acs_outage": _acs_outage, "fire_alarm": _fire_alarm, "mains_loss": _mains_loss,
    "overload_burst": _overload_burst, "camera_event": _camera_event,
    "alarm_button": _alarm_button, "db_outage": _db_outage, "ems_crash": _ems_crash,
}
