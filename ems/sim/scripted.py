"""Scripted traffic: a fixed, hand-written passenger list instead of random arrivals.

Used to follow one passenger through the system, to reproduce a bug, or to give a demo the
same way every time. A list is CSV (with a header row) or JSON (a list of objects):

    t,origin,destination,card,accessible,vip,passenger_id
    0,1,5,card-L5-001,,,
    20,1,18,card-L5-001,,,refused-demo
    45,12,1,,true,,

    t            arrival time in seconds from the start of the run (required)
    origin       floor number; the lobby is 1 (required)
    destination  floor number (required)
    card         credential id; empty = the first employee who works on the trip's tenant floor
    accessible   true/false; empty = taken from the employee (false for other cards)
    vip          true/false; empty = taken from the employee (false for other cards)
    passenger_id empty = S0001, S0002, ... in list order

Trips use the lobby and tenant floors, like the random traffic, and stay inside one group.
"""
from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from ems.contracts.events import PassengerArrived
from ems.contracts.types import Floor

_TRUE = {"true", "1", "yes", "y"}
_FALSE = {"false", "0", "no", "n", ""}


@dataclass(frozen=True)
class ScriptedTrip:
    t: float
    origin: Floor
    destination: Floor
    card: Optional[str] = None
    accessible: Optional[bool] = None
    vip: Optional[bool] = None
    passenger_id: Optional[str] = None


def _flag(value, field: str, row: int) -> Optional[bool]:
    if value is None or isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return None if text == "" else False
    raise ValueError(f"row {row}: {field} must be true or false, not {value!r}")


def parse(rows: Iterable[dict]) -> list[ScriptedTrip]:
    """Rows (dicts with the fields above) -> trips sorted by time. Raises ValueError."""
    trips = []
    for i, row in enumerate(rows, start=1):
        try:
            t = float(row["t"])
            origin, destination = int(row["origin"]), int(row["destination"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"row {i}: t, origin and destination are required numbers")
        if t < 0:
            raise ValueError(f"row {i}: t must be >= 0")
        card = str(row.get("card") or "").strip() or None
        pid = str(row.get("passenger_id") or "").strip() or None
        trips.append(ScriptedTrip(t, origin, destination, card,
                                  _flag(row.get("accessible"), "accessible", i),
                                  _flag(row.get("vip"), "vip", i), pid))
    return sorted(trips, key=lambda trip: trip.t)


def loads(text: str) -> list[ScriptedTrip]:
    """CSV or JSON text -> trips."""
    stripped = text.lstrip()
    if stripped.startswith("["):
        return parse(json.loads(stripped))
    return parse(csv.DictReader(io.StringIO(text)))


def load(path: str | Path) -> list[ScriptedTrip]:
    return loads(Path(path).read_text(encoding="utf-8"))


class ScriptedTraffic:
    """Publishes one PassengerArrived per trip at its time. Draws nothing from ctx.rng."""

    def __init__(self, ctx, trips: list[ScriptedTrip], start: float = 0.0,
                 population=None) -> None:
        self.ctx = ctx
        self.start_at = start
        self.population = population or ctx.services["population"]
        self.building = self.population.building
        self.arrivals = [self._resolve(n, trip) for n, trip in enumerate(trips, start=1)]
        self.count = 0

    def start(self) -> None:
        for arrival in self.arrivals:
            self.ctx.clock.call_at(self.start_at + arrival["t"],
                                   lambda a=arrival: self._arrive(a))

    def _resolve(self, n: int, trip: ScriptedTrip) -> dict:
        """Check the trip against the building and fill the defaults (fails before the run)."""
        lobby, group_of = self.building.lobby, self.building.group_of_floor
        for floor in (trip.origin, trip.destination):
            if floor != lobby and floor not in group_of:
                raise ValueError(f"trip {n}: floor {floor} is not the lobby or a tenant floor")
        if trip.origin == trip.destination:
            raise ValueError(f"trip {n}: origin and destination are the same floor")
        groups = {group_of[f] for f in (trip.origin, trip.destination) if f != lobby}
        if len(groups) > 1:
            raise ValueError(f"trip {n}: floors {trip.origin} and {trip.destination} are in "
                             "different groups (no transfers)")
        tenant_floor = trip.destination if trip.origin == lobby else trip.origin
        person = (self.population.lookup(trip.card) if trip.card
                  else self.population.on_floor(tenant_floor)[0])
        card = trip.card or person.credential_id

        def pick(value, attribute):
            return value if value is not None else bool(person and getattr(person, attribute))

        return {"t": trip.t, "origin": trip.origin, "destination": trip.destination,
                "group": groups.pop(), "card": card, "passenger_id": trip.passenger_id,
                "accessible": pick(trip.accessible, "accessible"), "vip": pick(trip.vip, "vip")}

    def _arrive(self, a: dict) -> None:
        self.count += 1
        self.ctx.bus.publish(PassengerArrived(
            t=self.ctx.clock.now(), passenger_id=a["passenger_id"] or f"S{self.count:04d}",
            panel_id=f"panel-L{a['origin']}", origin=a["origin"], destination=a["destination"],
            group=a["group"], credential_id=a["card"], accessible=a["accessible"], vip=a["vip"]))
