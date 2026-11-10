from ems.core.app import build


def test_monitor_registers():
    ctx = build(seed=1, parts=["monitor"])
    assert ctx.services  # stubs fill anything not yet provided
