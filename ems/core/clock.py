"""Simulated clock. Owned by: Tech lead.

Never call time.time() or datetime.now() anywhere in the EMS: use clock.now().
Everything is deterministic for a given seed (Appendix B: seeds 1-10).
"""
from __future__ import annotations

import heapq
import itertools
from datetime import datetime, timedelta
from typing import Callable


class SimClock:
    def __init__(self, start: datetime = datetime(2026, 11, 16, 0, 0)):  # a Monday
        self._t = 0.0
        self._start = start
        self._queue: list[tuple[float, int, Callable[[], None]]] = []
        self._seq = itertools.count()      # tie-break -> deterministic order

    def now(self) -> float:
        return self._t

    def now_dt(self) -> datetime:
        return self._start + timedelta(seconds=self._t)

    def call_at(self, t: float, fn: Callable[[], None]) -> None:
        if t < self._t:
            raise ValueError("cannot schedule in the past")
        heapq.heappush(self._queue, (t, next(self._seq), fn))

    def call_later(self, delay: float, fn: Callable[[], None]) -> None:
        self.call_at(self._t + delay, fn)

    def every(self, interval: float, fn: Callable[[], None]) -> None:
        def tick() -> None:
            fn()
            self.call_later(interval, tick)
        self.call_later(interval, tick)

    def run_until(self, t_end: float) -> None:
        while self._queue and self._queue[0][0] <= t_end:
            t, _, fn = heapq.heappop(self._queue)
            self._t = t
            fn()
        self._t = t_end
