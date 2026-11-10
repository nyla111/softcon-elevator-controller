"""Wiring. Owned by: Tech lead.

Every part exposes  register(ctx: Context) -> None  in its package
(e.g. ems/dispatch/__init__.py). register() subscribes to events and puts the
object implementing its Protocol into ctx.services[<key>]. Any key a part has
not provided yet is filled with a stub from contracts/stubs.py, so the app
always runs, even on day 1.
"""
from __future__ import annotations

import importlib
import random
from dataclasses import dataclass, field
from typing import Any

from ems.contracts.stubs import STUBS
from ems.core.bus import EventBus
from ems.core.clock import SimClock

PARTS = ["sim", "safety", "access", "dispatch", "priority", "monitor"]  # load order


@dataclass
class Context:
    clock: SimClock
    bus: EventBus
    rng: random.Random                       # the ONLY source of randomness
    services: dict[str, Any] = field(default_factory=dict)
    # keys: "controllers", "acs", "notifier", "access", "car_states",
    #       "dispatcher", "priority", "reports"

    def get(self, name: str) -> Any:
        return self.services[name]           # look up lazily, inside handlers


def build(seed: int = 1, parts: list[str] | None = None) -> Context:
    """Create a context and register the given parts (default: all)."""
    ctx = Context(clock=SimClock(), bus=EventBus(), rng=random.Random(seed))
    for name in (PARTS if parts is None else parts):
        importlib.import_module(f"ems.{name}").register(ctx)
    for key, cls in STUBS.items():
        ctx.services.setdefault(key, cls())
    return ctx
