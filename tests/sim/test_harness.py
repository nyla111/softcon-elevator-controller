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


def test_manifest_records_rate_and_decision_d01(tmp_path):
    import json

    from ems.sim.traffic import with_rate
    short = dict(warmup_s=30, measure_s=60, clear_s=300, setup=install_nearest_car,
                 out_dir=tmp_path)
    m35 = json.load(open(run("up_peak", 1, **short).manifest_path))
    assert m35["rate_per_min"] == 35 and m35["decision"] == "D-01"
    m75 = json.load(open(run(with_rate("up_peak", 75), 1, **short).manifest_path))
    assert m75["rate_per_min"] == 75 and m75["decision"] is None
    mbal = json.load(open(run("balanced", 1, **short).manifest_path))
    assert mbal["rate_per_min"] == 20 and mbal["decision"] is None


def test_evacuated_riders_are_counted_separately_and_excluded_from_wait_and_ride():
    from ems.sim.faults import fire_alarm
    r = run("balanced", 1, [fire_alarm(150)], warmup_s=60, measure_s=240, clear_s=600,
            setup=install_nearest_car, out_dir=None, keep_ctx=True)
    sim = r.ctx.services["controllers"]
    evacuated = [p for p in sim.passengers.values() if p.evacuated]
    assert r.n_evacuated == len(evacuated) == r.metrics["n_evacuated"] > 0
    assert r.metrics["n_delivered"] == sum(1 for p in sim.passengers.values()
                                           if p.alighted_at is not None and not p.evacuated)
    served = [p for p in sim.passengers.values()
              if 60 <= p.arrived_at < 300 and not p.evacuated and p.wait_s is not None]
    assert abs(r.metrics["avg_wait_s"] - sum(p.wait_s for p in served) / len(served)) < 1e-9
    assert not any(p.evacuated for p in served)
