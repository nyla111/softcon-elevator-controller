"""Fault helpers + the harness scheduling every kind (short runs, smoke level)."""
import json

import pytest

from ems.contracts.events import (AlarmButtonPressed, AlertRaised, CameraEvent, ControllerFault,
                                  EmsServerDown, FireAlarm, HeartbeatLost, MainsLost,
                                  OverloadDetected)
from ems.sim.baseline import install_nearest_car
from ems.sim.faults import (Fault, acs_outage, alarm_button, camera_event, car_fault, db_outage,
                            ems_server_crash, fire_alarm, link_loss, mains_loss, measured,
                            overload_burst, schedule_fault)
from ems.sim.harness import run

SHORT = dict(warmup_s=60, measure_s=240, clear_s=900, setup=install_nearest_car)


def test_measured_is_warmup_plus_minutes():
    assert measured(0) == 600.0 and measured(20) == 1800.0


def test_helpers_return_plain_fault_objects():
    assert car_fault("A", "F-7", 10, 300, between_floors=True) == Fault(
        10, "A", 300, "fault", "F-7", {"between_floors": True, "doors_stuck": False})
    assert link_loss("B", 5, 60).kind == "link_loss"
    assert acs_outage(1, 900).duration_s == 900
    assert fire_alarm(3, clear_after=60, io_signal_lost=True).params["source"] == "io_signal_lost"
    m = mains_loss(4, 120, generator_failed=True, failed_rescue_cars=["A"])
    assert m.duration_s == 120 and m.params["failed_rescue_cars"] == ["A"]
    assert ems_server_crash(7).params == {"takeover_s": 8.0}
    json.dumps([Fault(1).__dict__, db_outage(1, 2).__dict__, overload_burst("A", 1).__dict__])


def test_unknown_kind_is_rejected():
    from ems.core.app import build
    with pytest.raises(ValueError):
        schedule_fault(build(seed=1, parts=["sim"]), Fault(1, kind="meteor"))


def test_every_kind_can_be_scheduled_in_a_run(tmp_path):
    faults = [
        car_fault("A", "F-1", 70, 30, between_floors=True),
        link_loss("B", 80, 20),
        acs_outage(90, 30),
        camera_event("smoking", "C", 100, people_count=2),
        alarm_button("D", 101),
        overload_burst("E", 102),
        db_outage(110, 20),
        mains_loss(130, 60, failed_rescue_cars=["F"]),
        fire_alarm(300, clear_after=30, reset_after=5),
        ems_server_crash(400, takeover_s=6),
    ]
    r = run("balanced", 1, faults, out_dir=tmp_path, keep_ctx=True, **SHORT)
    log = r.ctx.bus
    assert [e.car_id for e in log.of_type(ControllerFault)] == ["A"]
    assert [e.car_id for e in log.of_type(HeartbeatLost)] == ["B"]
    assert log.of_type(CameraEvent) and log.of_type(AlarmButtonPressed)
    assert log.of_type(MainsLost) and log.of_type(FireAlarm) and log.of_type(EmsServerDown)
    assert any(e.load_pct > 100 for e in log.of_type(OverloadDetected))
    assert not r.ctx.services["acs"].down and r.ctx.services["db"].available()
    assert [f["kind"] for f in json.load(open(r.manifest_path))["params"]["faults"]][:3] == [
        "fault", "link_loss", "acs_outage"]


def test_acs_outage_makes_lookups_time_out_only_inside_the_window():
    from ems.core.app import build
    ctx = build(seed=1, parts=["sim"])
    schedule_fault(ctx, acs_outage(10, 20))
    acs = ctx.services["acs"]
    ctx.clock.run_until(9)
    acs.lookup("card-L5-001")
    ctx.clock.run_until(15)
    with pytest.raises(TimeoutError):
        acs.lookup("card-L5-001")
    ctx.clock.run_until(31)
    acs.lookup("card-L5-001")
    assert not ctx.bus.of_type(AlertRaised)
