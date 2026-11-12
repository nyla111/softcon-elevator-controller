from ems.sim.scenarios import SCENARIOS, describe, run_scenario

OWNERS = {2: ["SIM-01", "SIM-02", "SIM-03", "SIM-04", "SIM-05", "SIM-10"], 3: ["SIM-07"],
          4: ["SIM-08", "SIM-13", "SIM-14", "SIM-15", "SIM-17"],
          5: ["SIM-06", "SIM-11", "SIM-12", "SIM-18"], 1: ["SIM-09", "SIM-16"]}


def test_all_18_scenarios_with_the_right_owner_part():
    assert list(SCENARIOS) == [f"SIM-{i:02d}" for i in range(1, 19)]
    for part, ids in OWNERS.items():
        for sid in ids:
            assert SCENARIOS[sid].owner_part == part, sid
    assert sum(len(v) for v in OWNERS.values()) == 18


def test_only_sim_09_and_16_are_ready():
    ready = [s.id for s in SCENARIOS.values() if s.ready]
    assert ready == ["SIM-09", "SIM-16"]
    for s in SCENARIOS.values():
        if not s.ready:
            assert s.check is None and s.waiting_for == f"Part {s.owner_part} check"
        else:
            assert s.check is not None and s.waiting_for is None


def test_not_ready_scenario_reports_what_it_waits_for():
    r = run_scenario("SIM-01", 1)
    assert r["ready"] is False and r["passed"] is None and r["criteria"] == []
    assert r["message"] == "not ready: waiting for Part 2 check"


def test_describe_resolves_the_default_rate():
    d = describe(SCENARIOS["SIM-09"])
    assert d["rate"] == 35 and d["owner_part"] == 1 and d["faults"] == ["ems_crash at 1800 s"]
    assert describe(SCENARIOS["SIM-04"])["rate"] == 20
