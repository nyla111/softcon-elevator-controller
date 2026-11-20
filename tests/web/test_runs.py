"""Saved live runs: /api/runs/save, /api/runs, /api/runs/{run_id} (Part 1)."""
from fastapi.testclient import TestClient

from ems.sim import run_record
from ems.web.server import app


def test_save_list_and_load_a_live_run(tmp_path, monkeypatch):
    monkeypatch.setattr(run_record, "LIVE_RUNS_DIR", tmp_path)
    client = TestClient(app)
    assert client.post("/api/live/start", json={"profile": "balanced", "seed": 2}).status_code == 200
    client.post("/api/live/fault", json={"kind": "car_fault", "car_id": "B"})
    client.post("/api/advance", params={"seconds": 600})
    saved = client.post("/api/runs/save", json={"name": "balanced test"})
    assert saved.status_code == 200
    run_id = saved.json()["run_id"]

    rows = run_record.list_runs(tmp_path)
    assert [r["run_id"] for r in rows] == [run_id]
    data = run_record.load_run(run_id, tmp_path)
    assert data["meta"]["profile"] == "balanced" and data["meta"]["seed"] == 2
    assert data["meta"]["faults"][0]["kind"] == "car_fault"
    assert {"LOW", "HIGH"} <= set(data["by_group"])
    assert data["by_group"]["LOW"]["calls"] > 0
    assert data["by_mode"] and all("wait_p95_s" in v for v in data["by_mode"].values())
    assert sum(c["floors"] for c in data["cars"].values()) > 0
    assert len(data["series"]) == 10                       # one sample per simulated minute
    assert set(data["series"][0]) >= {"waiting", "rolling_wait_avg_s", "cars_in_service", "floors"}


def test_unknown_or_unsafe_run_id_is_404(tmp_path):
    client = TestClient(app)
    assert client.get("/api/runs/../../etc/passwd").status_code == 404
    assert client.get("/api/runs/nope").status_code == 404


def test_live_run_starts_in_the_mode_that_fits_its_profile():
    client = TestClient(app)
    expected = {"up_peak": "rush", "lunch": "rush", "evening": "rush", "balanced": "balanced",
                "eco": "eco"}
    for profile, mode in expected.items():
        client.post("/api/live/start", json={"profile": profile, "seed": 1})
        client.post("/api/advance", params={"seconds": 2})
        modes = client.get("/api/state").json()["modes"]
        assert modes["LOW"] == mode and modes["HIGH"] == mode, (profile, modes)
