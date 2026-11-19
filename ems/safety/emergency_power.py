"""Internal FR-52 parking sequence; not connected to SafetyService or controllers.

The caller supplies the order and remains responsible for readiness and authorization.
Request IDs are opaque, in-memory identity tokens, not cross-module identifiers or
shared power-episode IDs. They must not be serialized or reconstructed.
There are no destinations, capacity decisions, timers or automatic retries here.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from ems.contracts.types import CarId


@dataclass(frozen=True)
class ParkingRequest:
    request_id: object
    car_id: CarId


@dataclass(frozen=True)
class ParkingResult:
    request: ParkingRequest
    outcome: Literal["completed", "refused"]


@dataclass(frozen=True)
class ParkingProgress:
    pending: tuple[CarId, ...]
    outstanding: ParkingRequest | None
    results: tuple[ParkingResult, ...]
    revoked: bool


class ParkingSequence:
    """Reserve one request at a time until completed/refused, or permanently revoked.

    Reservation is bookkeeping, not controller acceptance or a movement command.
    Explicit repeated car IDs are preserved; each reservation gets a fresh token.
    The caller must return the exact token from reserve() to finish().
    There is deliberately no reset/rearm API. On a new power episode, the future
    runtime owner must revoke the old instance and create a new sequence; this
    local primitive cannot detect an episode change itself.
    """

    def __init__(self, cars: Sequence[CarId]) -> None:
        if isinstance(cars, (str, bytes)) or not isinstance(cars, Sequence):
            raise TypeError("cars must be an explicitly ordered sequence of car IDs")
        self._cars = tuple(cars)
        if any(not isinstance(car, str) or not car for car in self._cars):
            raise ValueError("car IDs must be non-empty strings")
        self._next = 0
        self._outstanding: ParkingRequest | None = None
        self._results: list[ParkingResult] = []
        self._revoked = False

    def reserve(self) -> ParkingRequest | None:
        """Reserve the next supplied car; return None while blocked or exhausted."""
        if self._revoked or self._outstanding is not None or self._next == len(self._cars):
            return None
        request = ParkingRequest(object(), self._cars[self._next])
        self._outstanding = request
        self._next += 1
        return request

    def finish(self, request_id: object, car_id: CarId, outcome: str) -> bool:
        """Record a matching terminal report; ignore all other reports unchanged.

        A late matching report after revocation may record what happened physically,
        but never clears revocation or permits another reservation. 'accepted' is
        not terminal. Missing reports leave the request outstanding indefinitely.
        """
        request = self._outstanding
        if (request is None or request.request_id is not request_id
                or request.car_id != car_id or outcome not in ("completed", "refused")):
            return False
        self._results.append(ParkingResult(request, outcome))
        self._outstanding = None
        return True

    def revoke(self) -> None:
        """Permanently block new reservations without claiming physical termination."""
        self._revoked = True

    def snapshot(self) -> ParkingProgress:
        """Return immutable progress; pending excludes the outstanding request."""
        return ParkingProgress(self._cars[self._next:], self._outstanding,
                               tuple(self._results), self._revoked)
