from ems.contracts import events as E
from ems.core.app import build


def repeated_overloads():
    ctx = build(parts=["sim", "safety", "monitor"])
    sim = ctx.get("controllers")
    for t in (0, 60, 120):
        ctx.clock.run_until(t)
        sim.overload_burst("A")
        assert sim.register_stop("A", 1, f"cycle-{t}")
        ctx.clock.run_until(t + 30)
    assert len(ctx.bus.of_type(E.OverloadDetected)) == 3
    return ctx


def test_three_overloads_request_camera_review_without_claiming_bulky_item():
    ctx = repeated_overloads()
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "bulky_item"]
    notices = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "overload_camera_review"]
    assert len(notices) == 1 and "review" in notices[0].message.lower()
    assert not [m for m in ctx.bus.of_type(E.PanelMessage) if "Heavy items" in m.text_en]
    assert all(row["responsible_credential"] is None for row in ctx.get("reports").incidents.values())


def test_crowd_only_report_is_distinct_and_actual_bulky_report_redirects():
    ctx = repeated_overloads()
    ctx.get("devices").camera.event("A", "repeated_overload", people_count=20)
    assert not [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "bulky_item"]
    ctx.get("devices").camera.event("A", "bulky_item", people_count=2)
    bulky = [a for a in ctx.bus.of_type(E.AlertRaised) if a.kind == "bulky_item"]
    assert len(bulky) == 1 and "service car" in bulky[0].message
    assert any(m.voice and m.text_vi and "Heavy items" in m.text_en
               for m in ctx.bus.of_type(E.PanelMessage))
    assert all(row["responsible_credential"] is None for row in ctx.get("reports").incidents.values())
