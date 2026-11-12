"""EMS server failover (NFR-R2 / FR-54): a primary/standby pair in a single-process simulation.

There is only one Python process and the event bus never stops. "The EMS server is down" is
therefore enforced at the boundary the real system has, the controllers:
  crash                 EmsServerDown("primary") is published. The simulator refuses every EMS
                        command (ems_down). Records still in the database buffer (written less
                        than flush_interval_s = 30 s ago) are lost, so loss <= 1 minute.
  crash + 3 s           the controllers notice the silence (config.HEARTBEAT_TIMEOUT_S) and
                        switch to BUILT-IN GROUP LOGIC: they assign every unassigned waiting
                        passenger, and every new one, to the nearest car themselves (the same
                        ranking as baseline.py) and register the stop directly.
  crash + takeover_s    the standby takes over: EmsServerUp("standby", gap_s=takeover_s) is
                        published, commands are accepted again, built-in logic stops (one last
                        sweep for passengers nobody assigned). Passengers keep the car the
                        built-in logic gave them until they are served.
Assumptions: the dispatcher (Part 2) must stop assigning on EmsServerDown and start again on
EmsServerUp; here its commands are refused anyway. A second crash while down is ignored.
The primary/standby identity is only a label in the events.
"""
from __future__ import annotations

from ems.contracts import config
from ems.contracts.events import EmsServerDown, EmsServerUp


class Failover:
    def __init__(self, ctx, simulator, database) -> None:
        self.ctx = ctx
        self.sim = simulator
        self.db = database
        self.down_since: float | None = None
        self.lost_records: list[dict] = []

    def crash(self, takeover_s: float = 8.0) -> None:
        if self.down_since is not None:
            return
        now = self.ctx.clock.now()
        self.down_since = now
        self.sim.ems_down = True
        self.lost_records += self.db.lose_unflushed()
        self.ctx.bus.publish(EmsServerDown(now, "primary"))
        self.ctx.clock.call_later(config.HEARTBEAT_TIMEOUT_S, self._controllers_detect)
        self.ctx.clock.call_later(takeover_s, self._takeover)

    def _controllers_detect(self) -> None:
        if self.sim.ems_down:
            self.sim.start_fallback()

    def _takeover(self) -> None:
        now = self.ctx.clock.now()
        gap = now - self.down_since
        self.sim.ems_down = False
        self.sim.stop_fallback()
        self.down_since = None
        self.ctx.bus.publish(EmsServerUp(now, "standby", gap))
