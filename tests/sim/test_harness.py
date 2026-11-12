import json

from ems.sim.baseline import install_nearest_car
from ems.sim.harness import Fault, run, run_seeds

SHORT = dict(warmup_s=60, measure_s=300, clear_s=900)


def test_run_clears_all_calls_and_writes_manifest(tmp_path):
    r = run("balanced", 1, setup=install_nearest_car, out_dir=tmp_path, **SHORT)
    assert r.passed and r.metrics["calls_not_cleared"] == 0
    for key in ("avg_wait_s", "max_wait_s", "avg_ride_s", "total_car_run_time_s"):
        assert r.metrics[key] > 0
    m = json.loads(open(r.manifest_path).read())
    assert m["seed"] == 1 and m["profile"] == "balanced" and m["metrics"] == r.metrics
    assert m["git_commit"] and "params" in m and m["event_digest"] == r.event_digest
    assert r.manifest_path.startswith(str(tmp_path))


def test_no_dispatcher_means_calls_not_cleared():
    r = run("balanced", 1, out_dir=None, **SHORT)          # stub: nobody assigns cars
    assert not r.passed and r.metrics["calls_not_cleared"] > 0


def test_run_seeds_returns_one_result_per_seed():
    results = run_seeds("eco", seeds=[1, 2, 3], setup=install_nearest_car, out_dir=None, **SHORT)
    assert [r.seed for r in results] == [1, 2, 3]


def test_fault_is_scheduled_and_run_still_ends():
    faults = [Fault(at_s=100, car_id="A", duration_s=120), Fault(at_s=150, car_id="E",
                                                                  kind="disconnect")]
    r = run("balanced", 1, faults, setup=install_nearest_car, out_dir=None, keep_ctx=True,
            **SHORT)
    from ems.contracts.events import ControllerFault, ControllerFaultCleared
    assert [e.t for e in r.ctx.bus.of_type(ControllerFault)] == [100.0]
    assert [e.t for e in r.ctx.bus.of_type(ControllerFaultCleared)] == [220.0]
    assert r.params["faults"][0]["car_id"] == "A"
