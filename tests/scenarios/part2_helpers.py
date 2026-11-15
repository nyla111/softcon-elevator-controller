"""Shared helpers for Part 2 scenario tests (SIM-01..05, 10)."""
import os

from ems.sim.scenarios import run_scenario

SLOW = os.environ.get("EMS_SLOW") == "1"     # SIM-04 / SIM-05 on every seed, SIM-10


def run(sid: str, seed: int) -> dict:
    return run_scenario(sid, seed)


def failed(result: dict, skip_prefixes=()) -> list[str]:
    """Criteria that failed, except those whose text contains one of skip_prefixes."""
    return [f"{c['criterion']}: {c['detail']}" for c in result["criteria"]
            if not c["passed"] and not any(p in c["criterion"] for p in skip_prefixes)]
