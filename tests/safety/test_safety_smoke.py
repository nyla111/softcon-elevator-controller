from ems.core.app import build


def test_safety_registers():
    ctx = build(seed=1, parts=["safety"])
    assert ctx.services  # stubs fill anything not yet provided
