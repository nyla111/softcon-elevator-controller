"""In-process event bus. Owned by: Tech lead.

publish() never calls handlers re-entrantly: events published inside a handler
are queued and delivered after it returns, in FIFO order. Every event is kept
in bus.log, which Part 6 (reports) and the test harness read.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Callable, TypeVar

from ems.contracts.events import Event

E = TypeVar("E", bound=Event)


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[type, list[Callable[[Event], None]]] = defaultdict(list)
        self._pending: deque[Event] = deque()
        self._draining = False
        self.log: list[Event] = []

    def subscribe(self, event_type: type[E], handler: Callable[[E], None]) -> None:
        self._subs[event_type].append(handler)  # type: ignore[arg-type]

    def publish(self, event: Event) -> None:
        self._pending.append(event)
        if self._draining:
            return
        self._draining = True
        try:
            while self._pending:
                ev = self._pending.popleft()
                self.log.append(ev)
                for cls in type(ev).__mro__:          # subscribers to Event get all
                    for h in list(self._subs.get(cls, ())):
                        h(ev)
        finally:
            self._draining = False

    def of_type(self, event_type: type[E]) -> list[E]:
        return [e for e in self.log if isinstance(e, event_type)]  # type: ignore[misc]
