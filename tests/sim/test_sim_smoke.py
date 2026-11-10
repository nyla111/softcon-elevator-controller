from ems.core.app import build


def test_sim_registers():
    ctx = build(seed=1, parts=["sim"])
    assert ctx.services  # stubs fill anything not yet provided
