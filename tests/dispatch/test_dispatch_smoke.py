from ems.core.app import build


def test_dispatch_registers():
    ctx = build(seed=1, parts=["dispatch"])
    assert ctx.services  # stubs fill anything not yet provided
