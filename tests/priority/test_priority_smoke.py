from ems.contracts.types import Call, CallPriority, CallSource
from ems.core.app import build


def test_priority_registers():
    ctx = build(seed=1, parts=["priority"])
    assert ctx.services  # stubs fill anything not yet provided


def test_priority_stub_lets_dispatch_run_without_part5():
    ctx = build(seed=1, parts=[])
    p = ctx.get("priority")
    panel = CallSource.DESTINATION_PANEL
    vip = Call("c1", 1, 20, "HIGH", 0.0, panel, CallPriority.VIP)
    medical = Call("c2", 1, 20, "HIGH", 5.0, panel, CallPriority.MEDICAL)
    assert p.claim_call(vip) is None
    assert p.order([vip, medical]) == [medical, vip]
    assert p.door_dwell_s(vip) == 5.0
