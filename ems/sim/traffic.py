"""Traffic generator: passengers arrive at random (exponential gaps, rate per minute).

Each arrival publishes PassengerArrived. All randomness comes from ctx.rng.
Passengers use tenant floors only (the lobby L1 is the origin of "up" trips).
Every passenger is an employee drawn from the Population (population.py), who works on the
trip's tenant floor (destination of "up", origin of "down" and inter-floor trips). The employee
supplies accessible and vip. credential_id is the employee's card, or, for visits to another
tenant, a visitor QR pass issued through ctx.services["acs"] (valid that day, that floor only).
Employees can make several trips in one run.

Basements (D-08), with basements=True (live runs and long runs): some "up" trips start in the
basement and some "down" trips end there (PARKING_SHARE at the B2 car park, DOCK_SHARE at the
B1 loading dock). The Appendix B scenario profiles keep their lobby-only mix (basements=False).
Both passenger groups serve B2/B1 (D-10), so a basement trip is one ride in the group of its
tenant floor. A building where only some groups serve the basement (SIM-10's zoned layout)
still makes the trip two rides with a change at the lobby: the second ride starts
TRANSFER_WALK_S after the first one ends.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta

from ems.contracts import config
from ems.contracts.events import PassengerAlighted, PassengerArrived
from ems.contracts.types import Mode, floor_label

from .population import ALL_TENANT_FLOORS, PASSENGER_GROUPS, TENANT_FLOORS, Population  # noqa: F401

LOBBY = config.LOBBY
PARKING = min(config.PARKING_FLOORS)          # B2, the car park
LOADING_DOCK = config.LOADING_DOCK            # B1
# D-08 trip mix: an office tower in Hanoi where most staff ride motorbikes. One parking level
# (B2) cannot hold 2,500 people's vehicles, so most park outside or are dropped off and walk in
# at the lobby; about 30% start and end their day in B2. The B1 loading dock is used by staff
# handling deliveries and waste only. Couriers use the service car (FR-85) and are not modelled.
PARKING_SHARE = 0.30
DOCK_SHARE = 0.02
TRANSFER_WALK_S = 20.0
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
                 population: Population | None = None, basements: bool = False) -> None:
        self.ctx = ctx
        self.basements = basements
        self.segments = segments
        self.population = population       # default: ctx.services["population"]
        self.count = 0
        self.transfers = 0                  # second rides started after a change at the lobby
        self._transfers: dict = {}          # first-ride passenger id -> (second ride, person, card)

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
        self.ctx.bus.subscribe(PassengerAlighted, self._on_alighted)
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
        origin, destination = self._with_basement(origin, destination)
        self._publish(origin, destination, person, credential)
        self._schedule_next(self.ctx.clock.now())

    def _with_basement(self, origin, destination):
        """Move the lobby end of an up or down trip to B2 or B1 for a share of the trips."""
        if not self.basements or (origin != LOBBY and destination != LOBBY) \
                or self._basement_group() is None:
            return origin, destination
        r = self.ctx.rng.random()
        if r < PARKING_SHARE:
            level = PARKING
        elif r < PARKING_SHARE + DOCK_SHARE:
            level = LOADING_DOCK
        else:
            return origin, destination
        return (level, destination) if origin == LOBBY else (origin, level)

    def _basement_group(self):
        """The passenger group whose cars serve the car park (LOW; zone 1 in SIM-10)."""
        if not hasattr(self, "_bgroup"):
            groups = self.population.building.parking_floors.get(PARKING, [])
            self._bgroup = groups[0] if groups else None
        return self._bgroup

    def _group_of(self, origin, destination):
        tenant = max(origin, destination)
        if tenant > LOBBY:
            return self.population.building.group_of_floor[tenant]
        return self._basement_group()

    def _publish(self, origin, destination, person, credential) -> None:
        """One ride. A basement trip whose tenant floor's group does not serve that basement
        rides between the basement and the lobby first and changes there (_on_alighted)."""
        self.count += 1
        pid = f"P{self.count:06d}"
        basement = min(origin, destination)
        if basement < LOBBY and self._group_of(origin, destination) not in \
                self.population.building.parking_floors.get(basement, ()):
            self._transfers[pid] = ((LOBBY, destination), person, credential)
            destination = LOBBY
        self.ctx.bus.publish(PassengerArrived(
            t=self.ctx.clock.now(), passenger_id=pid, panel_id=f"panel-{floor_label(origin)}",
            origin=origin, destination=destination, group=self._group_of(origin, destination),
            credential_id=credential, accessible=person.accessible, vip=person.vip))

    def _on_alighted(self, ev: PassengerAlighted) -> None:
        pending = self._transfers.pop(ev.call_id, None)
        if pending is None or ev.floor != LOBBY:
            return
        (origin, destination), person, credential = pending
        self.transfers += 1
        self.ctx.clock.call_later(TRANSFER_WALK_S,
                                  lambda: self._publish(origin, destination, person, credential))

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
                      until: float | None = None, weekends: bool = False) -> list[Segment]:
    """Weekday-schedule blocks as Segments in sim seconds, covering [now, until). The time of
    day at sim second t is ctx.clock.now_dt() + (t - now), so day_start (the sim second of
    midnight) follows from it. Blocks wrap over midnight. weekends=True follows
    config.WEEKEND_SCHEDULE on Saturdays and Sundays (multi-day runs, SIM-19)."""
    now = ctx.clock.now()
    dt = ctx.clock.now_dt()
    if day_start is None:
        day_start = now - (dt.hour * 3600.0 + dt.minute * 60.0 + dt.second)
    until = now + 86400.0 if until is None else until
    segments: list[Segment] = []
    day = day_start
    while day < until:
        weekday = (dt + timedelta(seconds=day + 43200.0 - now)).weekday()
        schedule = (config.WEEKEND_SCHEDULE if weekends and weekday >= 5
                    else config.WEEKDAY_SCHEDULE)
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
