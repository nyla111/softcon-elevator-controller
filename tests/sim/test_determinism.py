"""Same seed => identical event log. Different seed => different run."""
from ems.sim.baseline import install_nearest_car
from ems.sim.harness import run

SHORT = dict(warmup_s=60, measure_s=300, clear_s=600, setup=install_nearest_car, out_dir=None,
             keep_ctx=True)


def test_same_seed_gives_identical_event_log():
    a = run("balanced", 7, **SHORT)
    b = run("balanced", 7, **SHORT)
    assert len(a.ctx.bus.log) > 500
    assert a.ctx.bus.log == b.ctx.bus.log                  # event by event
    assert a.event_digest == b.event_digest and a.metrics == b.metrics


def test_different_seed_differs():
    a = run("balanced", 1, **SHORT)
    b = run("balanced", 2, **SHORT)
    assert a.event_digest != b.event_digest
