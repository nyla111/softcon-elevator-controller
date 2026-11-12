"""Trip database with buffering (FR-107). Implements the Database protocol: ctx.services["db"].

Model:
* write() never raises and never blocks. Every record goes into an in-memory buffer first.
* While the database is up the buffer is flushed to "disk" every flush_interval_s (30 s), so
  records() lags by up to 30 s. A crash of the EMS server loses what is still in the buffer
  (lose_unflushed), so at most 30 s of records are lost (failover.py).
* While the database is down nothing is flushed: the buffer grows up to capacity (default
  24 h x 75/min x 1.2 = 129,600 records; the oldest records are dropped beyond that and counted
  in `dropped`). When the buffer passes 80% of capacity ONE AlertRaised(severity "high",
  kind "db_buffer", recipients ["fm"]) is published. When the database is back, the whole
  buffer is flushed at once, in order.
* records() returns the persisted records only.
"""
from __future__ import annotations

from ems.contracts.events import AlertRaised

DEFAULT_CAPACITY = int(24 * 60 * 75 * 1.2)
ALERT_FRACTION = 0.8


class TripDatabase:
    def __init__(self, ctx, capacity: int = DEFAULT_CAPACITY, flush_interval_s: float = 30.0):
        self.ctx = ctx
        self.capacity = capacity
        self.flush_interval_s = flush_interval_s
        self.down = False
        self.disk: list[dict] = []
        self.buffer: list[tuple[float, dict]] = []     # (time written, record)
        self.lost: list[tuple[float, dict]] = []       # records lost in a crash
        self.dropped = 0
        self.alerted = False
        ctx.clock.every(flush_interval_s, self._flush_if_up)

    # ---------- Database protocol ----------
    def write(self, record: dict) -> None:
        try:
            self.buffer.append((self.ctx.clock.now(), dict(record)))
            if len(self.buffer) > self.capacity:
                self.buffer.pop(0)
                self.dropped += 1
            if (self.down and not self.alerted
                    and len(self.buffer) > ALERT_FRACTION * self.capacity):
                self.alerted = True
                self.ctx.bus.publish(AlertRaised(
                    self.ctx.clock.now(), "high", "db_buffer", ["fm"],
                    f"Database is down: {len(self.buffer)} of {self.capacity} records buffered"))
        except Exception:                               # never raise into the caller
            self.dropped += 1

    def records(self) -> list[dict]:
        return list(self.disk)

    def available(self) -> bool:
        return not self.down

    # ---------- used by faults.py / failover.py ----------
    def set_down(self, down: bool) -> None:
        self.down = down
        if not down:
            self._flush()

    def lose_unflushed(self) -> list[dict]:
        """The EMS server crashed: whatever was only in memory is gone."""
        gone = list(self.buffer)
        self.lost += gone
        self.buffer = []
        self.alerted = False
        return [rec for _, rec in gone]

    def _flush_if_up(self) -> None:
        if not self.down:
            self._flush()

    def _flush(self) -> None:
        self.disk += [rec for _, rec in self.buffer]
        self.buffer = []
        self.alerted = False
