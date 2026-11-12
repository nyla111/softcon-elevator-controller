"""Traffic generator: passengers arrive at random (exponential gaps, rate per minute).

Each arrival publishes PassengerArrived. All randomness comes from ctx.rng.
Passengers use tenant floors only (the lobby L1 is the origin of "up" trips).
Every passenger is an employee drawn from the Population (population.py): the employee who
works on the trip's tenant floor (destination of "up", origin of "down" and inter-floor trips).
The employee supplies credential_id, accessible and vip.
"""
from __future__ import annotations

from dataclasses import dataclass

from ems.contracts import config
from ems.contracts.events import PassengerArrived
from ems.contracts.types import Mode

from .population import ALL_TENANT_FLOORS, PASSENGER_GROUPS, TENANT_FLOORS, Population

LOBBY = config.LOBBY
# Tenant floors per group come from population.py (the people live there).
GROUP_OF_FLOOR = {f: g for g in PASSENGER_GROUPS for f in TENANT_FLOORS[g]}


@dataclass(frozen=True)
class Profile:
    name: str
    rate_per_min: float
    up: float          # share of arrivals going lobby -> tenant floor
    down: float        # tenant floor -> lobby
    inter: float       # tenant floor -> another floor in the same group


PROFILES = {p.name: p for p in [
    Profile("up_peak", 75, 0.90, 0.00, 0.10),
    Profile("lunch", 75, 0.45, 0.45, 0.10),
    Profile("evening", 75, 0.00, 0.90, 0.10),
    Profile("balanced", 20, 0.40, 0.40, 0.20),
    Profile("eco", 2, 0.40, 0.40, 0.20),            # quiet, balanced mix
]}


def profile_for_block(start_hhmm: str, mode: Mode) -> Profile:
    """Profile for one block of config.WEEKDAY_SCHEDULE (RUSH blocks depend on the hour)."""
    if mode is Mode.ECO:
        return PROFILES["eco"]
    if mode is Mode.BALANCED:
        return PROFILES["balanced"]
    hour = int(start_hhmm.split(":")[0])
    if hour < 10:
        return PROFILES["up_peak"]
    return PROFILES["lunch"] if hour < 15 else PROFILES["evening"]


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    profile: Profile


class TrafficGenerator:
    def __init__(self, ctx, segments: list[Segment],
                 population: Population | None = None) -> None:
        self.ctx = ctx
        self.segments = segments
        self.population = population       # default: ctx.services["population"]
        self.count = 0

    @classmethod
    def fixed(cls, ctx, profile: Profile, start: float, end: float, **kw) -> "TrafficGenerator":
        """One profile from start to end (sim seconds)."""
        return cls(ctx, [Segment(start, end, profile)], **kw)

    @classmethod
    def weekday(cls, ctx, day_start: float = 0.0, **kw) -> "TrafficGenerator":
        """24 hours following config.WEEKDAY_SCHEDULE, one profile per block."""
        schedule = config.WEEKDAY_SCHEDULE
        segments = []
        for i, (hhmm, mode) in enumerate(schedule):
            end_hhmm = schedule[i + 1][0] if i + 1 < len(schedule) else "24:00"
            segments.append(Segment(day_start + _seconds(hhmm), day_start + _seconds(end_hhmm),
                                    profile_for_block(hhmm, mode)))
        return cls(ctx, segments, **kw)

    def start(self) -> None:
        if self.population is None:
            self.population = self.ctx.services.get("population") or Population.build(
                self.ctx.rng)
        self._schedule_next(self.segments[0].start)

    # ---------- internals ----------
    def _schedule_next(self, t: float) -> None:
        """Find the next arrival time at or after t and schedule it."""
        for seg in self.segments:
            if t >= seg.end:
                continue
            t = max(t, seg.start)
            t += self.ctx.rng.expovariate(seg.profile.rate_per_min / 60.0)
            if t < seg.end:
                self.ctx.clock.call_at(t, lambda s=seg: self._arrive(s))
                return
            # The gap ran past the end of this block: restart at the next block (memoryless).
            t = seg.end

    def _arrive(self, seg: Segment) -> None:
        rng = self.ctx.rng
        origin, destination, owner_floor = self._pick_trip(seg.profile)
        person = rng.choice(self.population.on_floor(owner_floor))
        self.count += 1
        self.ctx.bus.publish(PassengerArrived(
            t=self.ctx.clock.now(), passenger_id=f"P{self.count:06d}", panel_id=f"panel-L{origin}",
            origin=origin, destination=destination, group=GROUP_OF_FLOOR[max(origin, destination)],
            credential_id=person.credential_id, accessible=person.accessible,
            vip=person.vip))
        self._schedule_next(self.ctx.clock.now())

    def _pick_trip(self, prof: Profile) -> tuple[int, int, int]:
        """(origin, destination, tenant floor of the person making the trip)"""
        rng = self.ctx.rng
        kind = rng.random()
        floor = rng.choice(ALL_TENANT_FLOORS)         # uniform over tenant floors
        if kind < prof.up:
            return LOBBY, floor, floor
        if kind < prof.up + prof.down:
            return floor, LOBBY, floor
        others = [f for f in TENANT_FLOORS[GROUP_OF_FLOOR[floor]] if f != floor]
        return floor, rng.choice(others), floor               # same group as the first floor


def _seconds(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return int(h) * 3600.0 + int(m) * 60.0
