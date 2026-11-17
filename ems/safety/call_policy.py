"""Pure Part 4 spam decisions for the future Access integration; never cancels calls.

CallRequested is already accepted, so filtering that event would be too late. The owning
panel must consult this policy before publication. Do not infer duplicate trips from a
shared credential, destination, uncertain occupancy, or camera count.
"""
from __future__ import annotations

from collections import deque

from ems.contracts.config import SPAM_ACTIONS_PER_60S


class ActionLimiter:
    def __init__(self, clock) -> None:
        self.clock = clock
        self.actions: dict[str, deque[float]] = {}

    def allow(self, credential_id: str | None, *, exit: bool = False,
              assistance: bool = False, fire: bool = False) -> bool:
        if exit or assistance or fire or credential_id is None:
            return True
        now = self.clock.now()
        for key in list(self.actions):
            times = self.actions[key]
            while times and times[0] <= now - 60:
                times.popleft()
            if not times:
                del self.actions[key]
        times = self.actions.setdefault(credential_id, deque())
        if len(times) >= SPAM_ACTIONS_PER_60S:
            return False
        times.append(now)
        return True


def duplicate_removable(*, proven_same_action: bool, waiting: bool,
                        boarded: bool, uncertain: bool) -> bool:
    """Requires explicit proof from the owner, never just matching request attributes."""
    return proven_same_action and waiting and not boarded and not uncertain
