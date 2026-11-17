from ems.core.clock import SimClock
from ems.safety.call_policy import ActionLimiter, duplicate_removable


def test_sixth_ordinary_action_and_window_boundary():
    clock = SimClock()
    limiter = ActionLimiter(clock)
    assert [limiter.allow("credential") for _ in range(6)] == [True] * 5 + [False]
    assert limiter.allow("another")
    clock.run_until(59.999)
    assert not limiter.allow("credential")
    clock.run_until(60)
    assert limiter.allow("credential")


def test_exits_assistance_and_fire_do_not_consume_or_obey_the_limit():
    limiter = ActionLimiter(SimClock())
    for _ in range(20):
        assert limiter.allow("credential", exit=True)
        assert limiter.allow("credential", assistance=True)
        assert limiter.allow("credential", fire=True)
    assert all(limiter.allow("credential") for _ in range(5))
    assert not limiter.allow("credential")
    assert limiter.allow("credential", exit=True)
    assert limiter.allow("credential", assistance=True)
    assert limiter.allow(None)


def test_only_proven_waiting_duplicates_can_be_removed():
    assert duplicate_removable(proven_same_action=True, waiting=True, boarded=False, uncertain=False)
    for proof, waiting, boarded, uncertain in [
        (False, True, False, False), (True, False, False, False),
        (True, True, True, False), (True, True, False, True),
    ]:
        assert not duplicate_removable(proven_same_action=proof, waiting=waiting,
                                       boarded=boarded, uncertain=uncertain)
