# Acceptance scenarios (Appendix B)

One file per scenario: `test_sim_01_up_peak.py`, `test_ver_01_access.py`, ...
Each runs seeds 1-10 (`@pytest.mark.parametrize("seed", range(1, 11))`) and
every seed must pass (no averaging). Owner of each scenario = owner of the
part listed in the assignment table; the tech lead runs the full set.

Mark scenarios that need parts not merged yet with `@pytest.mark.skip(reason="waiting for part N")`.
