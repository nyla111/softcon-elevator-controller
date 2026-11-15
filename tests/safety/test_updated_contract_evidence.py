"""Safety scenario consumers must use the newly approved terminal goods event conservatively."""
from types import SimpleNamespace

import pytest

from ems.contracts import events as E
from ems.core.app import build
from ems.sim.scenarios import _fire_goods_check


@pytest.mark.parametrize("reason,cancelled,car,when", [
    ("completed", False, "S", 10), ("emergency:power", True, "S", 10),
    ("emergency:fire", True, "A", 10), ("emergency:fire", True, "S", 11),
])
def test_wrong_goods_terminal_evidence_cannot_pass_fire_cancellation(reason, cancelled, car, when):
    ctx = build(parts=[])
    ctx.services["controllers"] = SimpleNamespace(command_log=[])
    ctx.bus.publish(E.GoodsMoveScheduled(0, "move", "S", 0, 20))
    ctx.bus.publish(E.GoodsMoveEnded(when, "move", car, cancelled, reason))
    assert not _fire_goods_check(ctx, E.FireAlarm(10))[1]


def test_terminal_event_alone_does_not_prove_active_move_or_prevent_restart():
    ctx = build(parts=[])
    commands = []
    ctx.services["controllers"] = SimpleNamespace(command_log=commands)
    ctx.bus.publish(E.GoodsMoveEnded(10, "move", "S", True, "emergency:fire"))
    assert not _fire_goods_check(ctx, E.FireAlarm(10))[1]
    ctx.bus.publish(E.GoodsMoveScheduled(0, "move", "S", 0, 20))
    assert _fire_goods_check(ctx, E.FireAlarm(10))[1]
    commands.append((11, "S", "set_independent_service", (True,), True))
    assert not _fire_goods_check(ctx, E.FireAlarm(10))[1]
