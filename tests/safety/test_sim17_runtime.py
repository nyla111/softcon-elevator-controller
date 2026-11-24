import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from ems.safety.scenario_admission import check_sim_17, setup_sim_17
from ems.sim.scenarios import SCENARIOS, run_setup


@pytest.mark.parametrize("seed", range(1, 11))
def test_full_runtime_admission_acceptance(seed):
    result = run_setup(replace(SCENARIOS["SIM-17"].runs[0], setup=setup_sim_17), seed)
    criteria = check_sim_17(result, result.ctx)
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
                "event_digest": result.event_digest,
                "observations": ctx.get("admission_acceptance"),
                "commands": ctx.get("controllers").command_log,
                "inbox": ctx.get("notifier").inbox,
                "events": [{"type": type(e).__name__, **clean(asdict(e))} for e in ctx.bus.log]}
        path = Path(directory) / f"sim17-seed{seed}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, default=str), encoding="utf-8")
    assert criteria and all(passed for _, passed, _ in criteria), criteria
    assert result.metrics["calls_not_cleared"] == 0
