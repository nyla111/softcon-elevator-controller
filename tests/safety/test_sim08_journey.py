import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from ems.safety.scenario_fire import check_sim_08
from ems.sim.scenarios import SCENARIOS, run_setup


@pytest.mark.parametrize("seed", range(1, 11))
@pytest.mark.parametrize("occupied_companion", [False, True])
def test_full_fire_acceptance_each_seed(seed, occupied_companion):
    setup = SCENARIOS["SIM-08"].runs[int(occupied_companion)]
    result = run_setup(setup, seed)
    criteria = check_sim_08(result, result.ctx, require_occupied_vip=occupied_companion)
    directory = os.environ.get("EMS_SAFETY_EVIDENCE_DIR")
    if directory:
        def clean(value):
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()
                        if k not in ("credential_id", "credential_ids")}
            if isinstance(value, (list, tuple)):
                return [clean(v) for v in value]
            return value
        ctx = result.ctx
        data = {"seed": seed, "criteria": criteria, "config": result.params,
                "event_digest": result.event_digest, "observations": ctx.get("fire_acceptance"),
                "commands": ctx.get("controllers").command_log,
                "inbox": ctx.get("notifier").inbox,
                "events": [{"type": type(e).__name__, **clean(asdict(e))} for e in ctx.bus.log]}
        path = Path(directory) / f"sim08-{'occupied' if occupied_companion else 'peak'}-seed{seed}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, default=str), encoding="utf-8")
    assert criteria and all(passed for _, passed, _ in criteria), [
        criterion for criterion in criteria if not criterion[1]
    ]
    assert result.metrics["calls_not_cleared"] == 0
