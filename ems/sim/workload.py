"""Design-limit workload hooks (SIM-10, DECISIONS D-03). Measurement only: no pass check here.

SIM-10 checks software latency under the SRS design limits, not passenger capacity:
400 calls and 400 credential lookups per minute, 2,000 camera events per day (equivalent).
* Calls come from the traffic generator (rate 400/min).
* Lookups: every call's credential lookup counts. While Part 3's access service is not there
  (StubAccess) this workload performs the lookup for each arrival; it then tops up with extra
  lookups of random employees (ctx.rng) so that the total keeps at `lookups_per_min`.
* Camera events: a Poisson stream at `camera_per_day / 86400` per second, random car and kind.
* Latencies: assignment = DispatchGuard.compute_s (real compute time per engine call, the one
  place that uses perf_counter); lookup = MockAcs.lookup_log (the mock's modelled reply time,
  config.SIM["acs_reply_s"]; a real wall-time figure needs Part 3's access path); dashboard =
  dashboard_load.py.
"""
from __future__ import annotations

import math

from ems.contracts.events import PassengerArrived

CAMERA_KINDS = ("door_held", "smoking", "vandalism", "tailgating", "lying_still")


def summarize(values) -> dict:
    """n, p50, p95, p99, max (nearest-rank percentiles); None when there are no values."""
    xs = sorted(values)
    if not xs:
        return {"n": 0, "p50": None, "p95": None, "p99": None, "max": None}

    def pct(q: float) -> float:
        return xs[max(0, math.ceil(q / 100 * len(xs)) - 1)]
    return {"n": len(xs), "p50": pct(50), "p95": pct(95), "p99": pct(99), "max": xs[-1]}


class Workload:
    def __init__(self, ctx, lookups_per_min: float = 400.0, camera_per_day: float = 2000.0) -> None:
        self.ctx = ctx
        self.lookups_per_min = lookups_per_min
        self.camera_per_day = camera_per_day
        self.call_lookups = 0
        self.topup_lookups = 0
        self.timeouts = 0
        self.stopped = False
        ctx.bus.subscribe(PassengerArrived, self.on_arrived)

    def start(self) -> None:
        self.ctx.clock.every(1.0, self._top_up)
        if self.camera_per_day > 0:
            self._schedule_camera()

    # ---------- lookups ----------
    def _lookup(self, credential_id: str) -> None:
        try:
            self.ctx.get("acs").lookup(credential_id)
        except TimeoutError:
            self.timeouts += 1

    def on_arrived(self, ev: PassengerArrived) -> None:
        if type(self.ctx.services.get("access")).__name__ == "StubAccess":
            self.call_lookups += 1
            self._lookup(ev.credential_id)

    def stop(self) -> None:
        """End of the arrivals: no more top-up lookups or camera events."""
        self.stopped = True

    def _top_up(self) -> None:
        if self.stopped:
            return
        acs = self.ctx.get("acs")
        target = self.lookups_per_min / 60.0 * self.ctx.clock.now()
        employees = self.ctx.get("population").employees
        while len(acs.lookup_log) < target:
            self.topup_lookups += 1
            self._lookup(self.ctx.rng.choice(employees).credential_id)

    # ---------- camera ----------
    def _schedule_camera(self) -> None:
        gap = self.ctx.rng.expovariate(self.camera_per_day / 86400.0)
        self.ctx.clock.call_later(gap, self._camera)

    def _camera(self) -> None:
        if self.stopped:
            return
        rng = self.ctx.rng
        car = rng.choice(sorted(self.ctx.get("controllers").cars))
        self.ctx.get("devices").camera.event(car, rng.choice(CAMERA_KINDS), rng.randint(1, 5))
        self._schedule_camera()

    def summary(self) -> dict:
        ctx = self.ctx
        log = ctx.get("acs").lookup_log
        guard = ctx.services.get("dispatch_guard")
        return {
            "assignment_s": summarize(guard.compute_s if guard else []),
            "lookup_s": summarize(l for _, _, l, _ in log),
            "lookups_total": len(log), "lookups_per_call": self.call_lookups,
            "lookups_topped_up": self.topup_lookups, "lookup_timeouts": self.timeouts,
            "camera_events": ctx.get("devices").camera.count,
            "lookup_latency_is": "modelled reply time of the mock ACS (config.SIM acs_reply_s)",
            "assignment_latency_is": "real compute time of the dispatch engine per call",
        }
