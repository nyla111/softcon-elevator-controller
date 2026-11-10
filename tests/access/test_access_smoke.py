from ems.core.app import build


def test_access_registers():
    ctx = build(seed=1, parts=["access"])
    assert ctx.services  # stubs fill anything not yet provided
