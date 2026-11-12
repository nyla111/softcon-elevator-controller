from ems.sim.scenarios import SCENARIOS, describe, run_scenario
from ems.sim.traffic import SRS_ORIGINAL_PEAK_RATE

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
            expected = ("Part 1 design-limit setup + Part 2 check" if s.id == "SIM-10"
                        else f"Part {s.owner_part} check")
            assert s.check is None and s.waiting_for == expected
        else:
            assert s.check is not None and s.waiting_for is None


def test_not_ready_scenario_reports_what_it_waits_for():
    r = run_scenario("SIM-01", 1)
    assert r["ready"] is False and r["passed"] is None and r["criteria"] == []
    assert r["message"] == "not ready: waiting for Part 2 check"


def test_describe_resolves_the_default_rate():
    d = describe(SCENARIOS["SIM-09"])
    assert d["rate"] == 35 and d["owner_part"] == 1 and d["faults"] == ["ems_crash at 1800 s"]
    assert describe(SCENARIOS["SIM-04"])["rate"] == 2


def test_every_scenario_has_title_criteria_and_requirement_ids():
    for s in SCENARIOS.values():
        assert s.title and s.srs_criteria and s.verifies, s.id
        d = describe(s)
        assert d["srs_criteria"] == s.srs_criteria and d["verifies"] == s.verifies
    assert SCENARIOS["SIM-01"].title == "Morning up-peak, all passengers with cards"
    assert SCENARIOS["SIM-09"].verifies == "FR-54, NFR-R2"
    assert SCENARIOS["SIM-16"].srs_criteria.startswith("Car marked Disconnected in 3 s")


def test_fault_notes_only_where_appendix_b_gives_them():
    with_notes = sorted(s.id for s in SCENARIOS.values() if s.notes)
    assert with_notes == ["SIM-07", "SIM-08", "SIM-12", "SIM-13", "SIM-14", "SIM-18"]


# Appendix B traffic setup: scenario -> [(profile name, mode)] in run order
SETUP = {
    "SIM-01": [("up_peak", "fixed")], "SIM-02": [("lunch", "fixed"), ("evening", "fixed")],
    "SIM-03": [("balanced", "fixed")],
    "SIM-04": [("eco_quiet", "fixed"), ("balanced", "fixed"), ("up_peak", "fixed")],
    "SIM-05": [("weekday", "weekday")],
    "SIM-06": [("up_peak", "fixed")], "SIM-07": [("up_peak", "fixed")],
    "SIM-08": [("up_peak", "fixed")], "SIM-09": [("up_peak", "fixed")],
    "SIM-10": [], "SIM-11": [("up_peak", "fixed")], "SIM-12": [("up_peak", "fixed")],
    "SIM-13": [("up_peak", "fixed")], "SIM-14": [("up_peak", "fixed")],
    "SIM-15": [("up_peak", "fixed")], "SIM-16": [("up_peak", "fixed")],
    "SIM-17": [("balanced", "fixed")], "SIM-18": [("up_peak", "fixed")],
}


def test_every_scenario_setup_matches_appendix_b():
    assert set(SETUP) == set(SCENARIOS)
    for sid, want in SETUP.items():
        got = [(r.profile_name, r.mode) for r in SCENARIOS[sid].runs]
        assert got == want, sid


def test_sim_10_is_unsupported_for_now():
    s = SCENARIOS["SIM-10"]
    assert s.profile is None and s.runs == [] and not s.ready
    assert s.waiting_for == "Part 1 design-limit setup + Part 2 check"
    d = describe(s)
    assert d["runs"] == [] and d["rate"] is None


def test_sim_04_pair_and_surge():
    eco, bal, surge = SCENARIOS["SIM-04"].runs
    assert eco.pair == bal.pair == "quiet" and surge.pair is None
    assert (eco.warmup_s, eco.measure_s) == (bal.warmup_s, bal.measure_s)
    assert surge.rate == SRS_ORIGINAL_PEAK_RATE == 75 and surge.measure_s == 900.0
    assert describe(SCENARIOS["SIM-04"])["runs"][2]["rate"] == 75


def test_sim_05_is_the_24h_schedule_and_sim_08_alarm_is_at_the_rush_transition():
    (wk,) = SCENARIOS["SIM-05"].runs
    assert wk.mode == "weekday" and wk.measure_s == 86400.0 and wk.warmup_s == 0.0
    (r,) = SCENARIOS["SIM-08"].runs
    alarm = [f for f in r.faults if f.kind == "fire_alarm"]
    assert len(alarm) == 1 and r.day_start_hhmm == "16:30"
    hh, mm = map(int, r.day_start_hhmm.split(":"))
    assert hh * 3600 + mm * 60 + alarm[0].at_s == 17 * 3600      # 17:00 Rush begins


def test_sim_02_runs_are_separate():
    assert [r.label for r in SCENARIOS["SIM-02"].runs] == ["lunch", "evening"]
    assert all(r.pair is None for r in SCENARIOS["SIM-02"].runs)
