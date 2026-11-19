from ems.sim.scenarios import run_scenario


def test_sim_19_compressed_wear_balance():
    result = run_scenario("SIM-19", 1, out_dir=None)
    assert result["ready"] and result["passed"], result["criteria"]
    assert len(result["runs"]) == 2
