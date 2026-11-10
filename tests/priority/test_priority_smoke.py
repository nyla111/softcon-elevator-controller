from ems.core.app import build


def test_priority_registers():
    ctx = build(seed=1, parts=["priority"])
    assert ctx.services  # stubs fill anything not yet provided
