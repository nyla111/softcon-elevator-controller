"""Shared by the Part 5 scenario tests (SIM-06, 11, 12, 18).

Every seed runs once (cached). Two tests per seed:
* test_<sim>_part5_criteria: every criterion passes on its own seed, except the ones listed as
  NOT_PART5 (evidence that only a missing part can produce, or the stand-in dispatcher's waits);
  those must be reported as failed (never as PASS) and the run must not pass overall.
* test_<sim>_full_acceptance: strict xfail. The scenario can only pass once Part 2/3/4 are real;
  when it starts passing, strict xfail turns red and the marker must be removed.
"""
from __future__ import annotations

from functools import lru_cache

from ems.sim.scenarios import run_scenario

INTEGRATION = "integration with real Part 2/3/4 services (not stand-ins)"
BLOCKED_REASON = ("BLOCKED: Part 2 dispatcher, Part 3 access and Part 4 car_states are contract "
                  "stubs at the base commit; see docs/priority/VERIFICATION.md")


@lru_cache(maxsize=None)
def result(sid: str, seed: int) -> dict:
    return run_scenario(sid, seed)


def assert_part5_criteria(sid: str, seed: int, not_part5: dict[str, str]) -> None:
    r = result(sid, seed)
    assert r["ready"] is True
    by_name = {c["criterion"]: c for c in r["criteria"]}
    unexpected = [c for c in r["criteria"] if not c["passed"] and c["criterion"] not in not_part5
                  and c["criterion"] != INTEGRATION]
    assert not unexpected, unexpected
    for name in [*not_part5, INTEGRATION]:
        assert name in by_name, f"criterion missing: {name}"
        assert not by_name[name]["passed"], f"{name} passed on stand-ins: {by_name[name]}"
    assert by_name[INTEGRATION]["detail"].startswith("BLOCKED")
    assert r["passed"] is False and r["message"] == "FAIL"
