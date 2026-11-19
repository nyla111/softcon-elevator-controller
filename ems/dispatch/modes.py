"""Operating modes (SRS 4.3): schedule (FR-11), manual switch (FR-12), Eco step-up (FR-14).

The active mode of a group is, in order:
  1. an override for that group (manual FR-12, or Eco step-up FR-14) that has not expired,
  2. an override for all groups (group None) that has not expired,
  3. the schedule for the day (weekday, or weekend / holiday calendar), from config.

An override with no end time lasts until the next scheduled change (the next time the
scheduled mode differs). Every change publishes ModeChanged once per group.
FR-15 / BR-02: a mode change never cancels a call or a trip; new rules apply to new decisions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Callable, Optional

from ems.contracts import config
from ems.contracts.events import ModeChanged
from ems.contracts.types import GroupId, Mode

Schedule = list[tuple[str, Mode]]


@dataclass
class Override:
    mode: Mode
    until: datetime
    cause: str               # "manual" | "eco_step_up"


class ModeManager:
    def __init__(self, ctx, groups: list[GroupId],
                 weekday: Optional[Schedule] = None, weekend: Optional[Schedule] = None,
                 holidays: Optional[set[date]] = None) -> None:
        self.ctx = ctx
        self.groups = list(groups)
        self.weekday: Schedule = list(weekday or config.WEEKDAY_SCHEDULE)
        self.weekend: Schedule = list(weekend or config.WEEKEND_SCHEDULE)
        self.holidays: set[date] = set(holidays or ())
        self.overrides: dict[Optional[GroupId], Override] = {}
        now = ctx.clock.now_dt()
        self.current: dict[GroupId, Mode] = {g: self.scheduled(now) for g in self.groups}
        self.listeners: list[Callable[[GroupId, Mode, Mode, str], None]] = []
        self.history: list[tuple[float, GroupId, Mode, Mode, str]] = []

    # ---------------------------------------------------------------- schedule (FR-11)
    def schedule_for(self, day: date) -> Schedule:
        return self.weekend if day.weekday() >= 5 or day in self.holidays else self.weekday

    def scheduled(self, dt: datetime) -> Mode:
        sched = self.schedule_for(dt.date())
        hhmm = dt.strftime("%H:%M")
        mode = sched[0][1]
        for start, m in sched:
            if start <= hhmm:
                mode = m
        return mode

    def next_change(self, dt: datetime) -> datetime:
        """First schedule boundary after dt where the scheduled mode differs from now."""
        now_mode = self.scheduled(dt)
        for days in range(0, 15):
            day = (dt + timedelta(days=days)).date()
            for start, _ in self.schedule_for(day):
                h, m = map(int, start.split(":"))
                b = datetime(day.year, day.month, day.day, h, m)
                if b > dt and self.scheduled(b) is not now_mode:
                    return b
        return dt + timedelta(days=15)

    # ---------------------------------------------------------------- overrides
    def set_manual(self, mode: Mode, group: Optional[GroupId] = None,
                   until: Optional[datetime] = None) -> None:
        """FR-12: the FM switches mode until `until` or the next scheduled change."""
        now = self.ctx.clock.now_dt()
        if group is None:      # an all-groups switch replaces the per-group ones
            for g in list(self.overrides):
                if g is not None:
                    del self.overrides[g]
        self.overrides[group] = Override(mode, until or self.next_change(now), "manual")
        self.tick()

    def clear_manual(self, group: Optional[GroupId] = None) -> None:
        self.overrides.pop(group, None)
        self.tick()

    def step_up(self, group: GroupId) -> None:
        """FR-14: Eco -> Balanced until the next scheduled change or an FM instruction."""
        now = self.ctx.clock.now_dt()
        self.overrides[group] = Override(Mode.BALANCED, self.next_change(now), "eco_step_up")
        self.tick()

    def set_holidays(self, days: set[date]) -> None:
        self.holidays = set(days)
        self.tick()

    # ---------------------------------------------------------------- state
    def mode(self, group: GroupId) -> Mode:
        return self.current.get(group, self.scheduled(self.ctx.clock.now_dt()))

    def _desired(self, group: GroupId, now: datetime) -> tuple[Mode, str]:
        for key in (group, None):
            o = self.overrides.get(key)
            if o is not None:
                if now < o.until:
                    return o.mode, o.cause
                del self.overrides[key]
        return self.scheduled(now), "schedule"

    def tick(self) -> None:
        now = self.ctx.clock.now_dt()
        for g in self.groups:
            new, cause = self._desired(g, now)
            old = self.current.get(g)
            if new is not old:
                self.current[g] = new
                self.history.append((self.ctx.clock.now(), g, old, new, cause))
                self.ctx.bus.publish(ModeChanged(self.ctx.clock.now(), g, old, new, cause))
                for fn in self.listeners:
                    fn(g, old, new, cause)
