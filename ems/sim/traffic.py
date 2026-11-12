"""Traffic generator: passengers arrive at random (exponential gaps, rate per minute).

Each arrival publishes PassengerArrived. All randomness comes from ctx.rng.
Passengers use tenant floors only (the lobby L1 is the origin of "up" trips).
Every passenger is an employee drawn from the Population (population.py), who works on the
trip's tenant floor (destination of "up", origin of "down" and inter-floor trips). The employee
supplies accessible and vip. credential_id is the employee's card, or, for visits to another
tenant, a visitor QR pass issued through ctx.services["acs"] (valid that day, that floor only).
Employees can make several trips in one run.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from ems.contracts import config
from ems.contracts.events import PassengerArrived
from ems.contracts.types import Mode

from .population import ALL_TENANT_FLOORS, PASSENGER_GROUPS, TENANT_FLOORS, Population  # noqa: F401

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


# Decision D-01 (docs/DECISIONS.md): the three peak profiles default to 35 persons/min until the
# stakeholders answer about the SRS rate of 75/min. The rate can be overridden for every run.
SRS_ORIGINAL_PEAK_RATE = 75
PEAK_PROFILES = ("up_peak", "lunch", "evening")
DEFAULT_PEAK_RATE = 35

PROFILES = {p.name: p for p in [
    Profile("up_peak", DEFAULT_PEAK_RATE, 0.90, 0.00, 0.10),
    Profile("lunch", DEFAULT_PEAK_RATE, 0.45, 0.45, 0.10),
    Profile("evening", DEFAULT_PEAK_RATE, 0.00, 0.90, 0.10),
    Profile("balanced", 20, 0.40, 0.40, 0.20),
    Profile("eco", 2, 0.40, 0.40, 0.20),            # quiet, balanced mix
    Profile("eco_quiet", 2, 0.40, 0.40, 0.20),      # alias of "eco": the Eco-period traffic
]}


def with_rate(profile: Profile | str, rate_per_min: float) -> Profile:
    """Same mix, different rate (the defaults in PROFILES are never changed; the name stays the same)."""
    base = PROFILES[profile] if isinstance(profile, str) else profile
    return replace(base, rate_per_min=rate_per_min)


def profile_for_block(start_hhmm: str, mode: Mode) -> Profile:
    """Profile for one block of config.WEEKDAY_SCHEDULE (RUSH blocks depend on the hour)."""
    if mode is Mode.ECO:
        return PROFILES["eco_quiet"]
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
    label: str = ""


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
    def weekday(cls, ctx, day_start: float | None = None, until: float | None = None,
                **kw) -> "TrafficGenerator":
        """Traffic that follows config.WEEKDAY_SCHEDULE, one profile per block. Time of day comes
        from ctx.clock.now_dt(): the run may start at any time. day_start = sim second of the
        last midnight (default: derived from the clock); until = sim second to stop (default:
        24 h after now)."""
        return cls(ctx, schedule_segments(ctx, day_start, until), **kw)

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
        origin, destination, person, credential = self._pick_trip(seg.profile)
        self.count += 1
        self.ctx.bus.publish(PassengerArrived(
            t=self.ctx.clock.now(), passenger_id=f"P{self.count:06d}", panel_id=f"panel-L{origin}",
            origin=origin, destination=destination, group=self.population.building.group_of_floor[max(origin, destination)],
            credential_id=credential, accessible=person.accessible, vip=person.vip))
        self._schedule_next(self.ctx.clock.now())

    def _pick_trip(self, prof: Profile):
        """(origin, destination, person, credential id)

        The tenant floor is drawn uniformly over all tenant floors, then a person who works
        there. Up/down trips use that person's card. Inter-floor trips: if the person's tenant
        has another floor, half of them go there (card); all the others visit a floor of
        another tenant in the same group with a visitor QR pass issued by the host tenant.
        """
        rng = self.ctx.rng
        pop = self.population
        kind = rng.random()
        building = pop.building
        floor = rng.choice(building.all_tenant_floors)
        person = rng.choice(pop.on_floor(floor))
        if kind < prof.up:
            return LOBBY, floor, person, person.credential_id
        if kind < prof.up + prof.down:
            return floor, LOBBY, person, person.credential_id
        own_floors = pop.tenants[person.tenant_id]
        own_others = [f for f in own_floors if f != floor]
        if own_others and rng.random() < 0.5:
            return floor, rng.choice(own_others), person, person.credential_id
        group_of = building.group_of_floor
        others = [f for f in building.tenant_floors[group_of[floor]] if f not in own_floors]
        destination = rng.choice(others)
        now = self.ctx.clock.now()
        day_start = now // 86400 * 86400
        qr = self.ctx.services["acs"].issue_visitor_pass(
            pop.tenant_of_floor[destination], {destination}, day_start, day_start + 86400)
        return floor, destination, person, qr


def schedule_segments(ctx, day_start: float | None = None,
                      until: float | None = None) -> list[Segment]:
    """Weekday-schedule blocks as Segments in sim seconds, covering [now, until). The time of
    day at sim second t is ctx.clock.now_dt() + (t - now), so day_start (the sim second of
    midnight) follows from it. Blocks wrap over midnight."""
    now = ctx.clock.now()
    dt = ctx.clock.now_dt()
    if day_start is None:
        day_start = now - (dt.hour * 3600.0 + dt.minute * 60.0 + dt.second)
    until = now + 86400.0 if until is None else until
    schedule = config.WEEKDAY_SCHEDULE
    segments: list[Segment] = []
    day = day_start
    while day < until:
        for i, (hhmm, mode) in enumerate(schedule):
            end_hhmm = schedule[i + 1][0] if i + 1 < len(schedule) else "24:00"
            seg = Segment(day + _seconds(hhmm), day + _seconds(end_hhmm),
                          profile_for_block(hhmm, mode), f"{hhmm} {mode.value}")
            if seg.end > now and seg.start < until:
                segments.append(seg)
        day += 86400.0
    return segments


def _seconds(hhmm: str) -> float:
    h, m = hhmm.split(":")
    return int(h) * 3600.0 + int(m) * 60.0
