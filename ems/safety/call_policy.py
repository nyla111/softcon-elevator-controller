"""Pure Part 4 spam decisions for the future Access integration; never cancels calls.

CallRequested is already accepted, so filtering that event would be too late. The owning
panel must consult this policy before publication. Do not infer duplicate trips from a
shared credential, destination, uncertain occupancy, or camera count.
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from ems.contracts.config import SPAM_ACTIONS_PER_60S


@dataclass(frozen=True)
class ActionDecision:
    allowed: bool
    message_en: str = ""
    message_vi: str = ""
    retry_at: float | None = None


class ActionLimiter:
    def __init__(self, clock, on_daily_limit: Callable[[str], None] | None = None) -> None:
        self.clock = clock
        self.actions: dict[str, deque[float]] = {}
        self.blocked_until: dict[str, float] = {}
        self.episodes: dict[str, tuple[date, int]] = {}
        self.on_daily_limit = on_daily_limit

    def allow(self, credential_id: str | None, *, exit: bool = False,
              assistance: bool = False, fire: bool = False) -> bool:
        return self.evaluate(credential_id, exit=exit, assistance=assistance, fire=fire).allowed

    def evaluate(self, credential_id: str | None, *, exit: bool = False,
                 assistance: bool = False, fire: bool = False) -> ActionDecision:
        """P3 consumes the decision before acceptance and displays its message.

        The optional daily callback supplies the credential to the security-log owner;
        callers must not put it in public alerts/trip records. Denied repeats do not
        start another episode, count as ordinary actions, or extend the sixth's block.
        """
        if exit or assistance or fire or credential_id is None:
            return ActionDecision(True)
        now = self.clock.now()
        for key in list(self.actions):
            times = self.actions[key]
            while times and times[0] <= now - 60:
                times.popleft()
            if not times:
                del self.actions[key]
        for key, until in list(self.blocked_until.items()):
            if until <= now:
                del self.blocked_until[key]
        until = self.blocked_until.get(credential_id)
        if until is not None:
            return self._refusal(until)
        times = self.actions.setdefault(credential_id, deque())
        if len(times) >= SPAM_ACTIONS_PER_60S:
            until = now + 60
            self.blocked_until[credential_id] = until
            self.actions.pop(credential_id, None)
            today = self.clock.now_dt().date()
            day, count = self.episodes.get(credential_id, (today, 0))
            count = count + 1 if day == today else 1
            self.episodes[credential_id] = (today, count)
            if count == 3 and self.on_daily_limit is not None:
                self.on_daily_limit(credential_id)
            return self._refusal(until)
        times.append(now)
        return ActionDecision(True)

    @staticmethod
    def _refusal(until: float) -> ActionDecision:
        return ActionDecision(False, "Too many requests; please wait before trying again.",
                              "Quá nhiều yêu cầu; vui lòng chờ trước khi thử lại.", until)


def duplicate_removable(*, proven_same_action: bool, waiting: bool,
                        boarded: bool, uncertain: bool) -> bool:
    """Requires explicit proof from the owner, never just matching request attributes."""
    return proven_same_action and waiting and not boarded and not uncertain
