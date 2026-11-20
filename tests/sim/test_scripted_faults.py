"""Passenger lists: fault parameters, bounded alert checks and the start time (#55, Part 1)."""
import pytest

from ems.contracts.events import AlertRaised
from ems.sim import live
from ems.sim.scripted import loads, run_list


def test_params_are_parsed_typed_and_checked():
    trips = loads("t,event,car,params\n"
                  "10,mains_loss,,generator_failed=true;failed_rescue_cars=C|D;generator_delay=5\n"
                  "20,car_fault,B,code=F-BRAKE;between_floors=yes\n")
    assert dict(trips[0].params) == {"generator_failed": True, "failed_rescue_cars": ("C", "D"),
                                     "generator_delay": 5.0}
    assert dict(trips[1].params) == {"code": "F-BRAKE", "between_floors": True}
    with pytest.raises(ValueError, match="row 1: car_fault does not take generator_failed"):
        loads("t,event,car,params\n0,car_fault,B,generator_failed=true\n")
    with pytest.raises(ValueError, match="key=value"):
        loads("t,event,params\n0,mains_loss,generator_failed\n")
    with pytest.raises(ValueError, match="within_s needs"):
        loads("t,event,within_s\n0,fire_alarm,10\n")


def test_start_time_comment_and_json_form():
    trips = loads("# start=07:00\nt,origin,destination\n0,1,5\n")
    assert trips.start_hhmm == "07:00" and trips[0].destination == 5
    assert loads('{"start": "7:5", "rows": [{"t": 0, "origin": 1, "destination": 5}]}'
                 ).start_hhmm == "07:05"
    assert loads("t,origin,destination\n0,1,5\n").start_hhmm is None
    with pytest.raises(ValueError, match="HH:MM"):
        loads("# start=25:00\nt,origin,destination\n0,1,5\n")
    ctx, _ = run_list(loads("# start=07:00\nt,origin,destination\n0,1,5\n"))
    assert ctx.clock.now_dt().hour >= 7


def test_unknown_param_car_is_refused_when_applied():
    ctx, _ = run_list(loads("t,origin,destination\n0,1,5\n"))
    with pytest.raises(ValueError, match="unknown car 'Z'"):
        live.apply_fault(ctx, "mains_loss", params={"failed_rescue_cars": ("Z",)})


def test_brake_fault_between_floors_bounded_entrapment_alert():
    # an occupied car moving up, a brake fault between floors: entrapment within 10 s (FR-112)
    text = ("t,origin,destination,card,event,car,params,expect,within_s\n"
            "0,1,12,card-L12-001,,,,delivered,\n"
            "28,,,,car_fault,{car},code=F-BRAKE;between_floors=true,alert:entrapment,10\n"
            "28,,,,check,{car},,no_alert:entrapment,0.5\n")
    # find the car the passenger rides, then fault it while it is moving
    ctx, _ = run_list(loads(text.replace("{car}", "A").replace(
        "alert:entrapment,10", ",").replace("no_alert:entrapment,0.5", ",")))
    car = next(e.car_id for e in ctx.bus.log if type(e).__name__ == "PassengerBoarded")
    ctx, checks = run_list(loads(text.replace("{car}", car)))
    bounded = [c for c in checks if c.subject in ("car_fault", "check")]
    assert bounded[0].ok, bounded
    alert = next(a for a in ctx.bus.of_type(AlertRaised) if a.kind == "entrapment")
    assert alert.car_id == car


def test_generator_failure_and_silent_rescue_devices_bounded_per_car():
    # FR-110: no generator and rescue devices that report nothing: alert within 60 s, per car
    text = ("t,origin,destination,card,event,car,params,expect,within_s\n"
            "0,1,12,card-L12-001,,,,,\n"
            "20,,,,mains_loss,,generator_failed=true;failed_rescue_cars=A|B|C|D|E|F|G|H|S,"
            "alert:entrapment,61\n"
            "20,,,,check,A,,alert:entrapment,61\n"
            "20,,,,check,A,,no_alert:entrapment,59\n")
    _, checks = run_list(loads(text))
    assert [c.ok for c in checks] == [True, True, True], checks


def test_live_run_of_a_list_starts_at_its_start_time():
    ctx = live.start_live(script=loads("# start=07:00\nt,origin,destination\n0,1,5\n"))
    assert ctx.clock.now_dt().strftime("%H:%M") == "07:00"
    ctx = live.start_live(script=loads("t,origin,destination\n0,1,5\n"))
    assert ctx.clock.now_dt().strftime("%H:%M") == "00:00"
