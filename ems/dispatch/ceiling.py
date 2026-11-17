"""Wait ceiling (FR-13, BR-01) and Eco step-up (FR-14, BR-05).

Mixin of Dispatcher (service.py).
"""
from __future__ import annotations

from collections import Counter

from ems.contracts import config
from ems.contracts.events import AlertRaised, WaitCeilingRisk
from ems.contracts.types import Mode

from .model import replay
from .records import Rec


class CeilingMixin:
    def _check_risk(self) -> None:
        for car_id, ids in list(self.waiting.items()):
            if not ids:
                continue
            status = self._status(car_id)
            if status is None:
                continue
            plan = replay(status, self.riders_of(car_id), self.waiting_of(car_id),
                          self.capacity_of(status), self.extra_load_of(status))
            for cid in sorted(ids):
                rec = self.calls[cid]
                eta = plan.pickup.get(cid)
                if eta is not None:
                    rec.eta = eta
                self._risk(rec, self.elapsed(cid) + (eta if eta is not None else 0.0))
        for rec in [self.calls[c] for c in sorted(self.open)]:
            if rec.state == "pending":
                self._risk(rec, self.elapsed(rec.call.call_id))

    def _risk(self, rec: Rec, predicted: float) -> None:
        ceiling = self._ceiling(rec.call.group)
        if rec.risk_sent or predicted < config.CEILING_WARN_FRACTION * ceiling:
            return
        rec.risk_sent = True
        self.risk_log.append((self._now(), rec.call.call_id, predicted, ceiling))
        self.ctx.bus.publish(WaitCeilingRisk(self._now(), rec.call.call_id, predicted, ceiling))
        if self._wake_standby(rec.call.group, "wait_ceiling"):   # FR-13: Standby capacity
            self._dispatch(rec, reason="wait_ceiling")

    def _log_breach(self, rec: Rec, wait: float, ceiling: float) -> None:
        group = rec.call.group
        cause = ("degraded" if (self.fire_hold or self.power_hold or self.ems_down)
                 else "capacity")
        self.breaches.append({"t": self._now(), "call_id": rec.call.call_id, "group": group,
                              "mode": self.current_mode(group).value, "wait_s": round(wait, 1),
                              "ceiling_s": ceiling, "cause": cause})
        last = self._last_breach_alert.get(group, -1e9)
        if self._now() - last >= 300.0:                  # one alert per group per 5 min
            self._last_breach_alert[group] = self._now()
            self.ctx.bus.publish(AlertRaised(
                self._now(), "high", "wait_ceiling", ["fm"],
                f"Group {group}: a call waited {wait:.0f} s, over the {ceiling} s ceiling "
                f"({cause}); see the breach log"))

    def _check_step_up(self) -> None:
        now = self._now()
        while self.pickups and self.pickups[0][0] < now - config.ECO_STEP_UP["window_s"]:
            self.pickups.popleft()
        open_by_group = Counter(self.calls[c].call.group for c in self.open)
        for group in self.modes.groups:
            if self.current_mode(group) is not Mode.ECO:
                self.over_since[group] = None
                continue
            waiting = open_by_group.get(group, 0)
            if waiting > config.ECO_STEP_UP["pending_calls"]:
                since = self.over_since.get(group)
                if since is None:
                    self.over_since[group] = since = now
            else:
                self.over_since[group] = since = None
            waits = [w for _, g, w in self.pickups if g == group]
            avg = sum(waits) / len(waits) if waits else 0.0
            cause = None
            if since is not None and now - since >= config.ECO_STEP_UP["for_s"]:
                cause = f"{waiting} pending calls for {now - since:.0f} s"
            elif waits and avg > config.ECO_STEP_UP["avg_wait_s"]:
                cause = f"average wait {avg:.0f} s over the last 5 min"
            if cause:
                self.modes.step_up(group)
                self.ctx.bus.publish(AlertRaised(
                    now, "info", "eco_step_up", ["fm"],
                    f"Group {group}: Eco stepped up to Balanced ({cause})"))
