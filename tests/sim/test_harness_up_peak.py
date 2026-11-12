"""Harness check (not an acceptance criterion): up-peak at the default 35/min with the
nearest-car stand-in clears every call on every seed. SIM-01 itself belongs to Part 2."""
import pytest

from ems.sim.baseline import install_nearest_car
from ems.sim.harness import run


@pytest.mark.slow
@pytest.mark.parametrize("seed", range(1, 11))
def test_up_peak_35_clears_all_calls(seed, tmp_path):
    r = run("up_peak", seed, setup=install_nearest_car, out_dir=tmp_path)
    assert r.params["profile"]["rate_per_min"] == 35
    assert r.metrics["calls_not_cleared"] == 0 and r.passed
    assert r.metrics["avg_wait_s"] > 0
