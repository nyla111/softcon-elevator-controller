from datetime import datetime

import pytest

from ems.core.clock import SimClock
from ems.safety.call_policy import ActionLimiter


def ordinary_episode(limiter):
    assert all(limiter.allow("card") for _ in range(5))
    assert not limiter.allow("card")


def test_sixth_at_59_blocks_until_119_without_extension_from_denied_actions():
    clock = SimClock()
    limiter = ActionLimiter(clock)
    for t in range(5):
        clock.run_until(t)
        assert limiter.allow("card")
    clock.run_until(59)
    assert not limiter.allow("card")
    for t in (60, 61, 118, 118.999):
        clock.run_until(t)
        assert not limiter.allow("card")
    clock.run_until(119)
    assert limiter.allow("card")


def test_refusal_decision_returns_immediate_bilingual_message_and_retry_boundary():
    clock = SimClock()
    limiter = ActionLimiter(clock)
    ordinary_episode(limiter)
    decision = limiter.evaluate("card")
    assert not decision.allowed and decision.retry_at == 60
    assert decision.message_en and decision.message_vi
    assert clock.now() == 0  # P3 can display immediately, with no polling/timer dependency.
    clock.run_until(60)
    assert limiter.evaluate("card").allowed


def test_third_episode_notifies_once_per_calendar_day_not_per_denied_repeat():
    clock = SimClock()
    notices = []
    limiter = ActionLimiter(clock, on_daily_limit=lambda card: notices.append((clock.now(), card)))
    for t in (0, 60, 120, 180):
        clock.run_until(t)
        ordinary_episode(limiter)
        assert not limiter.allow("card")
    assert notices == [(120, "card")]
    for t in (86400, 86460, 86520):
        clock.run_until(t)
        ordinary_episode(limiter)
    assert notices == [(120, "card"), (86520, "card")]


def test_midnight_resets_daily_count_without_shortening_active_cooldown():
    clock = SimClock(datetime(2026, 11, 23, 23, 59, 30))
    notices = []
    limiter = ActionLimiter(clock, on_daily_limit=notices.append)
    ordinary_episode(limiter)
    clock.run_until(30)
    assert not limiter.allow("card")
    clock.run_until(60)
    ordinary_episode(limiter)
    clock.run_until(120)
    ordinary_episode(limiter)
    assert notices == []  # Only two episodes on the new day.
    clock.run_until(180)
    ordinary_episode(limiter)
    assert notices == ["card"]


@pytest.mark.parametrize("exemption", [{"exit": True}, {"assistance": True}, {"fire": True}])
def test_exempt_actions_do_not_consume_extend_or_obey_active_block(exemption):
    clock = SimClock()
    limiter = ActionLimiter(clock)
    ordinary_episode(limiter)
    clock.run_until(59)
    for _ in range(20):
        assert limiter.allow("card", **exemption)
    assert not limiter.allow("card")
    clock.run_until(60)
    assert all(limiter.allow("card") for _ in range(5))
    assert not limiter.allow("card")


def test_credentials_are_independent_and_anonymous_requests_do_not_accumulate():
    clock = SimClock()
    limiter = ActionLimiter(clock)
    ordinary_episode(limiter)
    assert limiter.allow("other")
    assert all(limiter.allow(None) for _ in range(20))
    assert not limiter.allow("card")
