"""FR-105: the guard uses the plug-in's answer unless it fails, is invalid or is too slow."""
import time

import pytest

from ems.contracts.events import AlertRaised, CallAssigned
from ems.contracts.types import Call, CallSource, Mode
from ems.sim.baseline import NearestCar, NearestEngine
from ems.sim.dispatch_guard import DispatchGuard

from .helpers import make


class Good:
    def __init__(self, car="B"):
        self.car, self.calls = car, 0

    def choose_car(self, call, cars, mode):
        self.calls += 1
        return self.car


class Crashing:
    def choose_car(self, call, cars, mode):
        raise RuntimeError("plug-in bug")


class Slow:
    def __init__(self, seconds):
        self.seconds = seconds

    def choose_car(self, call, cars, mode):
        time.sleep(self.seconds)                 # test-only: real time on purpose
        return "B"


def call(origin=5, group="LOW", cid="c1"):
    return Call(cid, origin, 9, group, 0.0, CallSource.DESTINATION_PANEL)


def setup():
    ctx, sim = make()
    sim.cars["A"].floor = 5                      # A is the nearest car to L5
    return ctx, ctx.services["dispatch_guard"], sim.all_status("LOW")


def alerts(ctx, kind):
    return [a for a in ctx.bus.of_type(AlertRaised) if a.kind == kind]


def test_engine_answer_is_used_when_valid():
    ctx, guard, cars = setup()
    assert guard.choose_car(Good("C"), call(), cars, Mode.BALANCED) == ("C", False)
    assert (guard.calls, guard.fallbacks) == (1, 0) and not ctx.bus.of_type(AlertRaised)


def test_none_from_the_engine_is_a_valid_answer():
    ctx, guard, cars = setup()

    class Nobody:
        def choose_car(self, call, cars, mode):
            return None
    assert guard.choose_car(Nobody(), call(), cars, Mode.BALANCED) == (None, False)


def test_crashing_engine_uses_nearest_car_and_alerts_once_per_outage():
    ctx, guard, cars = setup()
    assert guard.choose_car(Crashing(), call(), cars, Mode.BALANCED) == ("A", True)
    assert guard.choose_car(Crashing(), call(cid="c2"), cars, Mode.BALANCED) == ("A", True)
    (alert,) = alerts(ctx, "dispatch_fallback")
    assert (alert.severity, alert.recipients) == ("high", ["fm"])
    assert "RuntimeError" in alert.message
    assert (guard.calls, guard.fallbacks) == (2, 2)
    assert ctx.services["notifier"].inbox[-1][1] == ["fm"]


def test_invalid_answers_use_fallback():
    ctx, guard, cars = setup()
    for bad in ("Z", "E", "S"):                  # unknown car / other group / not in the list
        assert guard.choose_car(Good(bad), call(), cars, Mode.BALANCED) == ("A", True)
    assert guard.fallbacks == 3 and len(alerts(ctx, "dispatch_fallback")) == 1


def test_car_not_dispatchable_per_car_states_is_invalid():
    ctx, guard, cars = setup()

    class OnlyC:
        def dispatchable(self, group):
            return ["C", "D"]
    ctx.services["car_states"] = OnlyC()
    assert guard.choose_car(Good("B"), call(), cars, Mode.BALANCED) == ("C", True)


def test_recovery_alert_and_new_outage_alerts_again():
    ctx, guard, cars = setup()
    guard.choose_car(Crashing(), call(), cars, Mode.BALANCED)
    assert guard.in_outage
    assert guard.choose_car(Good(), call(cid="c2"), cars, Mode.BALANCED) == ("B", False)
    (info,) = alerts(ctx, "dispatch_recovered")
    assert info.severity == "info" and not guard.in_outage
    guard.choose_car(Crashing(), call(cid="c3"), cars, Mode.BALANCED)
    assert len(alerts(ctx, "dispatch_fallback")) == 2


def test_set_crashed_never_calls_the_engine():
    ctx, guard, cars = setup()
    engine = Good()
    guard.set_crashed(True)
    assert guard.choose_car(engine, call(), cars, Mode.BALANCED) == ("A", True)
    assert engine.calls == 0
    guard.set_crashed(False)
    assert guard.choose_car(engine, call(), cars, Mode.BALANCED) == ("B", False)


def test_slow_engine_is_discarded_with_a_small_timeout():
    ctx, _, cars = setup()
    guard = DispatchGuard(ctx, timeout_s=0.01)
    assert guard.choose_car(Slow(0.05), call(), cars, Mode.BALANCED) == ("A", True)
    assert "too slow" in alerts(ctx, "dispatch_fallback")[0].message


@pytest.mark.slow
def test_engine_slower_than_one_second_uses_fallback():
    ctx, guard, cars = setup()
    assert guard.timeout_s == 1.0
    assert guard.choose_car(Slow(1.1), call(), cars, Mode.BALANCED) == ("A", True)
    assert len(alerts(ctx, "dispatch_fallback")) == 1


def test_no_usable_car_returns_none_with_fallback_flag():
    ctx, guard, cars = setup()
    assert guard.choose_car(Crashing(), call(), [], Mode.BALANCED) == (None, True)


def test_baseline_dispatcher_assigns_through_the_guard():
    ctx, sim = make()
    NearestCar(ctx, engine=Crashing())
    ctx.bus.log.clear()
    ctx.services["dispatch_guard"].set_crashed(False)
    from ems.contracts.events import CallRequested
    ctx.bus.publish(CallRequested(0.0, call(origin=1, group="LOW", cid="x")))
    (assigned,) = ctx.bus.of_type(CallAssigned)
    assert assigned.call_id == "x" and sim.status(assigned.car_id).pending_stops == [1]
    assert alerts(ctx, "dispatch_fallback")
    assert NearestEngine().choose_car(call(), sim.all_status("LOW"), Mode.BALANCED) in "ABCD"
