"""Scripted traffic: a fixed, hand-written list of passengers and events instead of random traffic.

Used to follow one passenger through the system, to reproduce a bug, to give a demo the same way
every time, and as an acceptance check (expected outcome per row). A list is CSV (with a header
row) or JSON (a list of objects). Every column except t is optional:

    t            time in seconds from the start of the run
    origin       passenger rows: floor number; the lobby is 1
    destination  passenger rows: floor number
    card         credential id; "visitor:<floor>" issues a QR pass for that floor from its
                 tenant; empty = the first employee who works on the trip's tenant floor
    accessible   true/false; empty = taken from the employee (false for other cards)
    vip          true/false; empty = taken from the employee (false for other cards)
    passenger_id empty = S0001, S0002, ... in arrival order
    event        an event row instead of a passenger: a live fault kind (fire_alarm, mains_loss,
                 car_fault, link_loss, acs_outage, ...; see live.FAULT_KINDS), "check" for a
                 check that needs no event, or a medical trip event (FR-127, done by security):
                 medical (uses origin and destination), medical_depart (the patient is in the
                 car), medical_finish (the car returns to service); the last two act on the
                 trip of the latest medical row
                 or fm_confirm: the facility manager confirms recovery after a fire or power
                 loss (FR-55); refused while recovery_eligible() is False
    car          car id, for events that need one
    duration     seconds, for events that end by themselves (link_loss, acs_outage, plugin_crash)
    params       event rows: fault parameters as key=value pairs separated by ";" (live.FAULT_PARAMS)
                 mains_loss: generator_failed, generator_delay, failed_rescue_cars, stuck_cars
                 car_fault:  code, between_floors, doors_stuck
                 lists of cars are written A|B, e.g. "generator_failed=true;failed_rescue_cars=C"
    within_s     event rows with alert:/no_alert: the window after t in which the alert must
                 (or must not) be raised; empty = until the end of the run. With car set, only
                 alerts about that car count (use a "check" row for a per-car check)
    expect       passenger rows: delivered | refused | cancelled | evacuated | waiting
                 event rows: alert:<kind> (an alert of that kind is raised at or after t)
                 or no_alert:<kind>
    max_wait_s   passenger rows: the passenger boards within this many seconds
    note         free text, ignored

A CSV list may start with a comment line "# start=HH:MM": the run starts at that time of day
(default 00:00), e.g. "# start=07:00" for a fault during the morning peak.

Example:
    t,origin,destination,card,event,car,expect,note
    0,1,5,card-L5-001,,,delivered,employee to their own floor
    20,1,18,card-L5-001,,,refused,no access to L18
    30,,,,link_loss,B,alert:disconnected,controller B goes silent

Passenger trips use the lobby and tenant floors, like the random traffic, and stay in one group.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from ems.contracts.events import AlertRaised, PassengerArrived
from ems.contracts.types import Floor, Role, floor_label

from .passengers import PassengerState

_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n", ""}
OUTCOMES = ("delivered", "refused", "cancelled", "evacuated", "waiting")
CLEAR_S = 1800.0                     # time allowed after the last row for everyone to arrive
MEDICAL_EVENTS = ("medical", "medical_depart", "medical_finish")
OTHER_EVENTS = ("check", "fm_confirm")
SECURITY_CARD = "staff-security-1"


@dataclass(frozen=True)
class ScriptedTrip:
    t: float
    origin: Optional[Floor] = None
    destination: Optional[Floor] = None
    card: Optional[str] = None
    accessible: Optional[bool] = None
    vip: Optional[bool] = None
    passenger_id: Optional[str] = None
    event: Optional[str] = None
    car: Optional[str] = None
    duration: Optional[float] = None
    expect: Optional[str] = None
    max_wait_s: Optional[float] = None
    params: tuple = ()                               # ((key, value), ...) for the fault
    within_s: Optional[float] = None
    row: int = field(default=0, compare=False)      # line in the list, for messages


@dataclass(frozen=True)
class Check:
    row: int
    subject: str                     # passenger id or event name
    expected: str
    actual: str
    ok: bool


def _text(value) -> Optional[str]:
    text = "" if value is None else str(value).strip()
    return text or None


def _number(value, name: str, row: int) -> Optional[float]:
    text = _text(value)
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"row {row}: {name} must be a number, not {value!r}")


def _params(value, event: str, row: int) -> tuple:
    """ "a=1;b=true;cars=A|B" -> (("a", 1.0), ("b", True), ("cars", ("A", "B")))."""
    text = _text(value)
    if text is None:
        return ()
    from .live import FAULT_PARAMS, check_fault_params
    out = {}
    for part in text.split(";"):
        if not part.strip():
            continue
        key, sep, raw = part.partition("=")
        key, raw = key.strip(), raw.strip()
        if not sep or not key:
            raise ValueError(f"row {row}: write params as key=value;key=value, not {part!r}")
        default = FAULT_PARAMS.get(event, {}).get(key)
        if isinstance(default, bool):
            flag = _flag(raw, key, row)
            out[key] = bool(flag)
        elif isinstance(default, (int, float)):
            out[key] = _number(raw, key, row)
        elif isinstance(default, tuple):
            out[key] = tuple(c.strip() for c in raw.split("|") if c.strip())
        else:
            out[key] = raw
    try:
        check_fault_params(event, out)
    except ValueError as e:
        raise ValueError(f"row {row}: {e}")
    return tuple(out.items())


def _flag(value, name: str, row: int) -> Optional[bool]:
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return None if text == "" else False
    raise ValueError(f"row {row}: {name} must be true or false, not {value!r}")


def _check_expect(expect: Optional[str], is_event: bool, row: int) -> None:
    if expect is None:
        return
    if is_event:
        want, _, kind = expect.partition(":")
        if want not in ("alert", "no_alert") or not kind:
            raise ValueError(f"row {row}: an event row expects alert:<kind> or no_alert:<kind>")
    elif expect not in OUTCOMES:
        raise ValueError(f"row {row}: expect must be one of {', '.join(OUTCOMES)}")


def parse(rows: Iterable[dict]) -> list[ScriptedTrip]:
    """Rows (dicts with the fields above) -> trips and events sorted by time. Raises ValueError."""
    from .live import FAULT_KINDS                     # live imports this module
    trips = []
    for i, row in enumerate(rows, start=1):
        t = _number(row.get("t"), "t", i)
        if t is None or t < 0:
            raise ValueError(f"row {i}: t is required and must be >= 0")
        event, expect = _text(row.get("event")), _text(row.get("expect"))
        if event is not None:
            if event not in OTHER_EVENTS and event not in FAULT_KINDS and event not in MEDICAL_EVENTS:
                raise ValueError(f"row {i}: unknown event {event!r}")
            _check_expect(expect, True, i)
            origin = destination = None
            if event == "medical":
                origin = _number(row.get("origin"), "origin", i)
                destination = _number(row.get("destination"), "destination", i)
                if origin is None or destination is None:
                    raise ValueError(f"row {i}: a medical row needs origin and destination")
                origin, destination = int(origin), int(destination)
            within = _number(row.get("within_s"), "within_s", i)
            if within is not None and not expect:
                raise ValueError(f"row {i}: within_s needs an alert:/no_alert: expect")
            trips.append(ScriptedTrip(t, origin, destination, event=event,
                                      car=_text(row.get("car")),
                                      duration=_number(row.get("duration"), "duration", i),
                                      expect=expect, params=_params(row.get("params"), event, i),
                                      within_s=within, row=i))
            continue
        origin = _number(row.get("origin"), "origin", i)
        destination = _number(row.get("destination"), "destination", i)
        if origin is None or destination is None:
            raise ValueError(f"row {i}: a passenger row needs origin and destination")
        _check_expect(expect, False, i)
        trips.append(ScriptedTrip(
            t, int(origin), int(destination), _text(row.get("card")),
            _flag(row.get("accessible"), "accessible", i), _flag(row.get("vip"), "vip", i),
            _text(row.get("passenger_id")), expect=expect,
            max_wait_s=_number(row.get("max_wait_s"), "max_wait_s", i), row=i))
    return sorted(trips, key=lambda trip: trip.t)


class ScriptList(list):
    """The trips of one list, plus the time of day the run starts at ("HH:MM" or None)."""
    start_hhmm: Optional[str] = None


def loads(text: str) -> ScriptList:
    """CSV or JSON text -> trips. JSON is a list of rows, or {"start": "HH:MM", "rows": [...]}."""
    stripped = text.lstrip()
    start = None
    if stripped.startswith("{"):
        data = json.loads(stripped)
        trips, start = parse(data.get("rows", [])), data.get("start")
    elif stripped.startswith("["):
        trips = parse(json.loads(stripped))
    else:
        lines = text.splitlines()
        while lines and lines[0].lstrip().startswith("#"):
            key, _, value = lines.pop(0).lstrip("# ").partition("=")
            if key.strip() == "start":
                start = value.strip()
        trips = parse(csv.DictReader(io.StringIO("\n".join(lines))))
    out = ScriptList(trips)
    if start is not None:
        h, _, m = start.partition(":")
        if not (h.isdigit() and m.isdigit() and int(h) < 24 and int(m) < 60):
            raise ValueError(f"start must be HH:MM, not {start!r}")
        out.start_hhmm = f"{int(h):02d}:{int(m):02d}"
    return out


def load(path: str | Path) -> list[ScriptedTrip]:
    return loads(Path(path).read_text(encoding="utf-8"))


class ScriptedTraffic:
    """Publishes one PassengerArrived per passenger row and applies each event row at its time.
    Draws nothing from ctx.rng."""

    def __init__(self, ctx, trips: list[ScriptedTrip], start: float = 0.0,
                 population=None) -> None:
        self.ctx = ctx
        self.start_at = start
        self.population = population or ctx.services["population"]
        self.building = self.population.building
        self.trips = list(trips)
        people = [trip for trip in self.trips if trip.event is None]
        self.arrivals = [self._resolve(n, trip) for n, trip in enumerate(people, start=1)]
        self._arrival_of = {id(trip): a for trip, a in zip(people, self.arrivals)}
        self.count = 0
        self.medical_trip: Optional[str] = None     # trip id of the latest medical row

    def start(self) -> None:
        for trip in self.trips:
            if trip.event is None:
                action = (lambda a=self._arrival_of[id(trip)]: self._arrive(a))
            else:
                action = (lambda e=trip: self._apply(e))
            self.ctx.clock.call_at(self.start_at + trip.t, action)

    def _resolve(self, n: int, trip: ScriptedTrip) -> dict:
        """Check the trip against the building and fill the defaults (fails before the run)."""
        lobby, group_of = self.building.lobby, self.building.group_of_floor
        parking = self.building.parking_floors
        where = f"row {trip.row}" if trip.row else f"trip {n}"
        for floor in (trip.origin, trip.destination):
            if floor != lobby and floor not in group_of and floor not in parking:
                raise ValueError(f"{where}: floor {floor} is not the lobby, a tenant floor "
                                 "or a parking floor")
        if trip.origin == trip.destination:
            raise ValueError(f"{where}: origin and destination are the same floor")
        floors = (trip.origin, trip.destination)

        def serving(f: Floor) -> set:
            if f == lobby:
                return set(self.building.passenger_groups)
            return {group_of[f]} if f in group_of else set(parking[f])

        groups = serving(trip.origin) & serving(trip.destination)
        if not groups:
            raise ValueError(f"{where}: floors {trip.origin} and {trip.destination} are in "
                             "different groups (no transfers)")
        group = sorted(groups)[0]
        tenant_floors = [f for f in floors if f in group_of]
        tenant_floor = (tenant_floors[0] if tenant_floors
                        else self.building.tenant_floors[group][0])   # lobby <-> parking
        card, person = trip.card, None
        if card and card.startswith("visitor:"):
            card = self._visitor_pass(card, where)
        elif card:
            person = self.population.lookup(card)
        else:
            person = self.population.on_floor(tenant_floor)[0]
            card = person.credential_id

        def pick(value, attribute):
            return value if value is not None else bool(person and getattr(person, attribute))

        return {"t": trip.t, "origin": trip.origin, "destination": trip.destination,
                "group": group, "card": card, "passenger_id": trip.passenger_id,
                "accessible": pick(trip.accessible, "accessible"), "vip": pick(trip.vip, "vip")}

    def _visitor_pass(self, card: str, where: str) -> str:
        """visitor:<floor> -> a QR pass for that floor from its tenant, valid that day."""
        try:
            floor = int(card.split(":", 1)[1])
        except ValueError:
            raise ValueError(f"{where}: write a visitor card as visitor:<floor>")
        tenant = self.population.tenant_of_floor.get(floor)
        if tenant is None:
            raise ValueError(f"{where}: floor {floor} has no tenant to host a visitor")
        day_start = self.ctx.clock.now() // 86400 * 86400
        return self.ctx.services["acs"].issue_visitor_pass(tenant, {floor}, day_start,
                                                           day_start + 86400)

    def _arrive(self, a: dict) -> None:
        self.count += 1
        if a["passenger_id"] is None:
            a["passenger_id"] = f"S{self.count:04d}"
        self.ctx.bus.publish(PassengerArrived(
            t=self.ctx.clock.now(), passenger_id=a["passenger_id"],
            panel_id=f"panel-{floor_label(a['origin'])}", origin=a["origin"], destination=a["destination"],
            group=a["group"], credential_id=a["card"], accessible=a["accessible"], vip=a["vip"]))

    def _apply(self, trip: ScriptedTrip) -> None:
        if trip.event in MEDICAL_EVENTS:
            self._medical(trip)
        elif trip.event == "fm_confirm":
            self.ctx.get("car_states").confirm_recovery(Role.FM)
        elif trip.event != "check":
            from .live import apply_fault
            apply_fault(self.ctx, trip.event, trip.car, trip.duration, dict(trip.params))

    def _medical(self, trip: ScriptedTrip) -> None:
        """FR-127 through Part 5, as security would do it. A refused request raises the
        medical_no_car alert, which an expect column can check."""
        priority = self.ctx.services["priority"]
        if trip.event == "medical":
            out = priority.request_medical(SECURITY_CARD, trip.origin, trip.destination)
            self.medical_trip = out.ref if out.ok else None
        elif self.medical_trip is not None:
            if trip.event == "medical_depart":
                priority.medical_depart(SECURITY_CARD, self.medical_trip)
            else:
                priority.finish_trip(SECURITY_CARD, self.medical_trip)
                self.medical_trip = None

    # ---------- expected outcomes ----------
    def evaluate(self) -> list[Check]:
        """One Check per expect and per max_wait_s, in list order."""
        sim = self.ctx.services["controllers"]
        alerts = self.ctx.bus.of_type(AlertRaised)
        checks = []
        for trip in sorted(self.trips, key=lambda t: t.row):
            if trip.event is not None:
                if trip.expect:
                    want, _, kind = trip.expect.partition(":")
                    t0 = self.start_at + trip.t
                    t1 = None if trip.within_s is None else t0 + trip.within_s
                    hits = [a for a in alerts if a.kind == kind and a.t >= t0
                            and (t1 is None or a.t <= t1)
                            and (trip.car is None or trip.within_s is None
                                 or a.car_id == trip.car)]
                    seen = bool(hits)
                    expected = trip.expect + (f" within {trip.within_s:g} s" if t1 else "") + \
                        (f" car {trip.car}" if t1 and trip.car else "")
                    actual = ("alert:" if seen else "no_alert:") + kind + \
                        (f" at +{hits[0].t - t0:.1f} s" if seen else "")
                    checks.append(Check(trip.row, trip.event, expected, actual,
                                        seen == (want == "alert")))
                continue
            pid = self._arrival_of[id(trip)]["passenger_id"] or "not arrived"
            p = sim.passengers.get(pid)
            if trip.expect:
                outcome = _outcome(p)
                checks.append(Check(trip.row, pid, trip.expect, outcome, outcome == trip.expect))
            if trip.max_wait_s is not None:
                wait = p.wait_s if p is not None else None
                checks.append(Check(trip.row, pid, f"wait <= {trip.max_wait_s:g} s",
                                    "not boarded" if wait is None else f"wait {wait:.1f} s",
                                    wait is not None and wait <= trip.max_wait_s))
        return checks


def _outcome(p) -> str:
    if p is None:
        return "not arrived"
    if p.evacuated:
        return "evacuated"
    return {PassengerState.DONE: "delivered", PassengerState.REFUSED: "refused",
            PassengerState.CANCELLED: "cancelled"}.get(p.state, "waiting")


def run_list(trips: list[ScriptedTrip], seed: int = 1, clear_s: float = CLEAR_S,
             start_hhmm: Optional[str] = None):
    """Run a list on a fresh context (as the live demo would) until every passenger has arrived
    or clear_s after the last row. The run starts at start_hhmm, else the list's own
    "# start=HH:MM", else 00:00 (Monday 2026-11-16). Returns (ctx, checks)."""
    from ems.core.app import build

    from .baseline import install_dispatch_standin
    from .harness import START_DATE
    hhmm = start_hhmm or getattr(trips, "start_hhmm", None) or "00:00"
    h, m = (int(x) for x in hhmm.split(":"))
    ctx = build(seed=seed, start=START_DATE.replace(hour=h, minute=m))
    install_dispatch_standin(ctx)
    traffic = ScriptedTraffic(ctx, trips)
    traffic.start()
    sim = ctx.services["controllers"]
    last = max((trip.t for trip in trips), default=0.0)
    ctx.clock.run_until(last)
    deadline = last + clear_s
    while sim.undelivered() > 0 and ctx.clock.now() < deadline:
        ctx.clock.run_until(min(ctx.clock.now() + 30.0, deadline))
    return ctx, traffic.evaluate()
