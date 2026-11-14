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
    assert [r["id"] for r in rows if r["ready"]] == ["SIM-06", "SIM-09", "SIM-11", "SIM-12",
                                                     "SIM-16", "SIM-18"]
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
    for path in ("/", "/building/", "/runner/", "/dashboard/"):
        assert client.get(path).status_code == 200
    assert "tower" in client.get("/building/").text and "SIM-" not in client.get("/runner/").text
    assert "Live cars" in client.get("/dashboard/").text


def test_monitor_role_permissions_and_audit(client):
    owner = {"X-Role": "owner", "X-User": "owner-1"}
    security = {"X-Role": "security", "X-User": "guard-1"}
    assert client.get("/api/monitor/report", headers=owner).status_code == 200
    assert client.get("/api/monitor/incidents", headers=owner).status_code == 403
    assert client.get("/api/monitor/incidents", headers=security).status_code == 200
    r = client.put("/api/monitor/configuration/public_floors", json={"value": [1, 2]},
                   headers={"X-Role": "facility_manager", "X-User": "fm-1"})
    assert r.status_code == 200 and r.json()["old"] == [1]
    assert client.put("/api/monitor/configuration/public_floors", json={"value": [1]},
                      headers=owner).status_code == 403
    audit = client.get("/api/monitor/audit", headers=owner).json()
    assert audit[-1]["user"] == "fm-1" and audit[-1]["old"] == [1]


def test_scenarios_list_has_runs_with_start_times(client):
    rows = {s["id"]: s for s in client.get("/api/scenarios").json()}
    assert rows["SIM-08"]["runs"][0]["start_hhmm"] == "16:30"
    assert rows["SIM-10"]["runs"][0]["rate"] == 400.0 and rows["SIM-10"]["waiting_for"] == "Part 2 check"
    assert [r["label"] for r in rows["SIM-04"]["runs"]] == ["quiet eco", "quiet balanced", "surge"]


def test_state_snapshot_is_cached_and_refreshed_on_change(client):
    from ems.web import server
    client.post("/api/live/start", json={"profile": "balanced", "seed": 1})
    first = client.get("/api/state")
    assert client.get("/api/state").content == first.content          # same pre-serialised bytes
    client.post("/api/advance?seconds=10")
    assert client.get("/api/state").json()["t"] == 10.0
    client.post("/api/live/fault", json={"kind": "fire_alarm"})        # same sim time, new events
    assert "fire alarm" in client.get("/api/state").json()["active_faults"]
    assert server.app.state.snapshot is not None


def test_50_concurrent_state_requests_return_within_1_s(client):
    import threading
    import time

    from fastapi.testclient import TestClient

    from ems.web import server
    client.post("/api/live/start", json={"profile": "up_peak", "rate": 35, "seed": 1})
    client.post("/api/advance?seconds=600")
    times, bad = [], []
    barrier = threading.Barrier(50)

    def user():
        c = TestClient(server.app)
        barrier.wait()
        for _ in range(3):
            t0 = time.perf_counter()
            r = c.get("/api/state")
            times.append(time.perf_counter() - t0)
            if r.status_code != 200 or "cars" not in r.json():
                bad.append(r.status_code)
    threads = [threading.Thread(target=user) for _ in range(50)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not bad and len(times) == 150
    assert max(times) < 1.0, max(times)
