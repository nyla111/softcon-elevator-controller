import pytest
from fastapi.testclient import TestClient

from ems.web.server import app


@pytest.fixture()
def client():
    c = TestClient(app)
    c.post("/api/reset?seed=1")
    yield c
    c.post("/api/reset?seed=1")


def test_old_state_fields_still_there(client):
    s = client.get("/api/state").json()
    for key in ("t", "time", "modes", "cars", "alerts"):
        assert key in s
    assert len(s["cars"]) == 9 and {"state", "doors", "direction", "load_pct", "pending_stops",
                                    "fault_code", "disconnected"} <= set(s["cars"][0])


def test_live_start_runs_traffic_and_state_has_the_new_fields(client):
    r = client.post("/api/live/start", json={"profile": "up_peak", "rate": 35, "seed": 2})
    assert r.status_code == 200 and r.json()["started"]["seed"] == 2
    assert client.post("/api/advance?seconds=120").json()["t"] == 120.0
    s = client.get("/api/state").json()
    assert s["live"] == {"profile": "up_peak", "rate": 35.0, "seed": 2}
    assert set(s["waiting"]) == {"LOW", "HIGH", "SERVICE"} and sum(s["waiting"]["LOW"].values()) > 0
    assert s["ems"] == {"status": "up", "controllers_fallback": False}
    assert s["dispatch_fallback_active"] is False and s["active_faults"] == []
    assert s["dispatch_guard"]["calls"] > 0 and s["alerts_total"] == 0
    assert 0 < len(s["events"]) <= 30 and all("s " in line for line in s["events"])
    assert not any("CarStatusUpdated" in line for line in s["events"])
    assert {c["state"] for c in s["cars"]} == {"GROUP_SERVICE"}      # read from car_states


def test_live_start_rejects_unknown_profile(client):
    assert client.post("/api/live/start", json={"profile": "nope"}).status_code == 400


def test_live_faults_show_up_in_state(client):
    client.post("/api/live/start", json={"profile": "balanced", "seed": 1})
    client.post("/api/advance?seconds=30")
    assert client.post("/api/live/fault", json={"kind": "car_fault", "car_id": "C"}).status_code == 200
    s = client.get("/api/state").json()
    assert "car C fault F-LIVE" in s["active_faults"]
    assert next(c for c in s["cars"] if c["car_id"] == "C")["fault_code"] == "F-LIVE"
    client.post("/api/live/fault", json={"kind": "clear_fault", "car_id": "C"})
    client.post("/api/live/fault", json={"kind": "ems_crash"})
    s = client.get("/api/state").json()
    assert s["ems"]["status"] == "down" and "EMS server down" in s["active_faults"]
    client.post("/api/advance?seconds=10")
    s = client.get("/api/state").json()
    assert s["ems"]["status"] == "up" and any("EmsServerUp" in e for e in s["events"])
    client.post("/api/live/fault", json={"kind": "plugin_crash", "duration": 60})
    client.post("/api/advance?seconds=30")
    s = client.get("/api/state").json()
    assert s["dispatch_fallback_active"] and "dispatch plug-in crashed" in s["active_faults"]
    assert s["alerts_total"] >= 1


@pytest.mark.parametrize("kind", ["fire_alarm", "fire_clear", "mains_loss", "mains_restore",
                                  "acs_outage"])
def test_building_wide_faults_need_no_car(client, kind):
    client.post("/api/live/start", json={"profile": "eco", "seed": 1})
    assert client.post("/api/live/fault", json={"kind": kind}).status_code == 200


@pytest.mark.parametrize("kind", ["car_fault", "link_loss", "overload_burst", "bulky_item"])
def test_car_faults_need_a_car(client, kind):
    client.post("/api/live/start", json={"profile": "eco", "seed": 1})
    assert client.post("/api/live/fault", json={"kind": kind}).status_code == 400
    assert client.post("/api/live/fault", json={"kind": kind, "car_id": "D"}).status_code == 200


def test_unknown_fault_kind_is_400(client):
    assert client.post("/api/live/fault", json={"kind": "meteor"}).status_code == 400


def test_fire_alarm_then_reset_through_the_api(client):
    client.post("/api/live/start", json={"profile": "eco", "seed": 1})
    client.post("/api/live/fault", json={"kind": "fire_alarm"})
    assert "fire alarm" in client.get("/api/state").json()["active_faults"]
    client.post("/api/live/fault", json={"kind": "fire_clear"})
    assert client.get("/api/state").json()["active_faults"] == []


def test_scenario_list(client):
    rows = client.get("/api/scenarios").json()
    assert [r["id"] for r in rows] == [f"SIM-{i:02d}" for i in range(1, 19)]
    assert [r["id"] for r in rows if r["ready"]] == ["SIM-09", "SIM-16"]
    assert next(r for r in rows if r["id"] == "SIM-01")["waiting_for"] == "Part 2 check"


def test_run_not_ready_and_unknown_scenario(client):
    r = client.post("/api/scenarios/SIM-02/run?seed=1").json()
    assert r["ready"] is False and r["message"] == "not ready: waiting for Part 2 check"
    assert client.post("/api/scenarios/SIM-99/run").status_code == 404


def test_run_sim_09_one_seed(client):
    r = client.post("/api/scenarios/SIM-09/run?seed=1").json()
    assert r["ready"] and r["passed"] and r["seed"] == 1
    assert len(r["criteria"]) == 7 and all(c["passed"] for c in r["criteria"])
    assert r["metrics"]["calls_not_cleared"] == 0
    assert client.get("/api/state").json()["t"] == 0.0          # the live context is untouched


def test_pages_are_served(client):
    for path in ("/", "/building/", "/runner/"):
        assert client.get(path).status_code == 200
    assert "tower" in client.get("/building/").text and "SIM-" not in client.get("/runner/").text
