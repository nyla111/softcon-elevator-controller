"""Fast compressed diagnostic and opt-in real-traffic wear acceptance."""
import os

import pytest

from ems.sim.long_run import run_long
from ems.sim.scenarios import run_scenario


def test_sim_19_compressed_wear_balance():
    result = run_scenario("SIM-19", 1, out_dir=None)
    assert result["ready"] and result["passed"], result["criteria"]
    assert len(result["runs"]) == 2


@pytest.mark.skipif(os.getenv("EMS_LONG") != "1", reason="set EMS_LONG=1 for 24h traffic")
def test_sim_19_real_weekday_wear_balance():
    result = run_long(1, seed=1, out_dir=None)
    assert result.summaries[0].avg_wait_s <= 25.0
    assert all(row["ok"] for row in result.imbalance), result.imbalance
