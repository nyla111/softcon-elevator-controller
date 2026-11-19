"""Shared by the Part 5 scenario tests (SIM-06, 11, 12, 18).

Every seed runs once (cached). Two tests per seed:
* test_<sim>_part5_criteria: every criterion passes on its own seed, except the ones listed as
  KNOWN_GAPS (with the reason). Parts 2, 3 and 4 are real on main, so the integration criterion
  must pass.
* test_<sim>_full_acceptance: plain assertion once every seed passes; xfail (not strict) while a
  known gap remains on some seeds.
"""
from __future__ import annotations

from functools import lru_cache

from ems.sim.scenarios import run_scenario

INTEGRATION = "integration with real Part 2/3/4 services (not stand-ins)"
RUSH_MAX_GAP = ("Part 2: Rush max wait over 60 s at 35/min in the high-rise group "
                "(docs/dispatch.md, proposed D-07)")


@lru_cache(maxsize=None)
def result(sid: str, seed: int) -> dict:
    return run_scenario(sid, seed)


def assert_part5_criteria(sid: str, seed: int, known_gaps: dict[str, str]) -> None:
    r = result(sid, seed)
    assert r["ready"] is True
    by_name = {c["criterion"]: c for c in r["criteria"]}
    unexpected = [c for c in r["criteria"] if not c["passed"] and c["criterion"] not in known_gaps]
    assert not unexpected, unexpected
    for name in known_gaps:
        assert name in by_name, f"criterion missing: {name}"
    assert by_name[INTEGRATION]["passed"], by_name[INTEGRATION]
