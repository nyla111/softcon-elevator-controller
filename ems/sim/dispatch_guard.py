"""FR-105: if the dispatch plug-in fails or is slower than 1 s, a built-in nearest-car rule decides.

Part 2 owns the plug-in (DispatchEngine.choose_car(call, cars, mode) -> car id or None) and must
call it THROUGH this guard: ctx.services["dispatch_guard"].choose_car(engine, call, cars, mode).
Part 1 only provides the safety net and the nearest-car fallback (baseline.rank_cars).

The engine's answer is used unless
  * it raises an exception, or
  * it returns a car that is not in `cars`, not in the call's group, or not dispatchable
    (car_states.dispatchable(group), read from Part 4's service), or
  * it took longer than timeout_s (1.0 s), or the plug-in was marked crashed (set_crashed).
None from the engine is a valid answer ("no car for this call now") and is passed on.
A slow answer is thrown away even though it arrived: the guard cannot interrupt the engine,
it only measures afterwards.

Real time: the compute time is measured with time.perf_counter(). This is the ONLY real-time
use in the EMS: FR-105 is about how long the computation takes on the machine, not about
simulated time, so ctx.clock cannot measure it. It never changes simulated time, but a slow
machine could in principle turn a normal answer into a fallback.

Alerts: the first fallback of an outage publishes AlertRaised(high, "dispatch_fallback", ["fm"]);
more fallbacks in the same outage are silent. When the engine answers normally again, an info
AlertRaised "dispatch_recovered" is published. Counters: calls, fallbacks. log has one entry per
call: (t, call_id, car_id, used_fallback, reason).
"""
from __future__ import annotations

import time
from typing import Optional

from ems.contracts.events import AlertRaised
from ems.contracts.types import Call, CarId, CarStatus, Mode

from .baseline import rank_cars

TIMEOUT_S = 1.0


class DispatchGuard:
    def __init__(self, ctx, timeout_s: float = TIMEOUT_S) -> None:
        self.ctx = ctx
        self.timeout_s = timeout_s
        self.crashed = False                   # fault injection: the plug-in is dead
        self.in_outage = False
        self.calls = 0
        self.fallbacks = 0
        self.log: list[tuple] = []
        self.compute_s: list[float] = []       # real compute time of each engine call (SIM-10)

    def set_crashed(self, crashed: bool) -> None:
        self.crashed = crashed

    def choose_car(self, engine, call: Call, cars: list[CarStatus],
                   mode: Mode) -> tuple[Optional[CarId], bool]:
        """Returns (car_id, used_fallback)."""
        self.calls += 1
        answer, reason = self._ask_engine(engine, call, cars, mode)
        if reason is None:
            if self.in_outage:
                self._recovered()
            self.log.append((self.ctx.clock.now(), call.call_id, answer, False, "engine"))
            return answer, False
        car_id = self._fallback(call, cars)
        self.fallbacks += 1
        if not self.in_outage:
            self._alert_fallback(reason)
        self.log.append((self.ctx.clock.now(), call.call_id, car_id, True, reason))
        return car_id, True

    # ---------- internals ----------
    def _ask_engine(self, engine, call, cars, mode) -> tuple[Optional[CarId], Optional[str]]:
        """(answer, None) when the engine answered well, else (None, reason)."""
        if self.crashed:
            return None, "plug-in crashed"
        started = time.perf_counter()          # real time on purpose, see module docstring
        try:
            answer = engine.choose_car(call, cars, mode)
        except Exception as exc:               # any plug-in failure counts
            self.compute_s.append(time.perf_counter() - started)
            return None, f"plug-in raised {type(exc).__name__}"
        elapsed = time.perf_counter() - started
        self.compute_s.append(elapsed)
        if elapsed > self.timeout_s:
            return None, f"plug-in too slow ({elapsed:.2f} s)"
        if answer is not None and answer not in self._valid_ids(call, cars):
            return None, f"plug-in returned invalid car {answer!r}"
        return answer, None

    def _valid_ids(self, call: Call, cars: list[CarStatus]) -> set[CarId]:
        dispatchable = set(self.ctx.get("car_states").dispatchable(call.group))
        return {c.car_id for c in cars if c.group == call.group and c.car_id in dispatchable}

    def _fallback(self, call: Call, cars: list[CarStatus]) -> Optional[CarId]:
        usable = [c for c in cars if c.car_id in self._valid_ids(call, cars)
                  and c.fault_code is None and not c.disconnected]
        return rank_cars(usable, call.origin)[0].car_id if usable else None

    def _alert_fallback(self, reason: str) -> None:
        self.in_outage = True
        self.ctx.bus.publish(AlertRaised(
            self.ctx.clock.now(), "high", "dispatch_fallback", ["fm"],
            f"Dispatch plug-in failed ({reason}); built-in nearest-car rule is assigning calls"))

    def _recovered(self) -> None:
        self.in_outage = False
        self.ctx.bus.publish(AlertRaised(
            self.ctx.clock.now(), "info", "dispatch_recovered", ["fm"],
            "Dispatch plug-in answers normally again"))
