from ems.sim.capacity import analytic_round_trip, format_tables, measure, queue_length
from ems.sim.passengers import Passenger


def test_analytic_round_trip_one_passenger_by_hand():
    # LOW: mean distance (1..14) = 7.5 floors; lobby 2+5+2 = 9; travel 2x7.5x1.5 = 22.5; 1 stop of 9 s
    low = analytic_round_trip("LOW", 1)
    assert abs(low["rtt_s"] - (9 + 22.5 + 9)) < 1e-9 and abs(low["stops"] - 1) < 1e-9
    # HIGH: distances 15..29, mean 22
    assert abs(analytic_round_trip("HIGH", 1)["rtt_s"] - (9 + 66 + 9)) < 1e-9


def test_capacity_grows_with_load():
    assert (analytic_round_trip("LOW", 21)["capacity_per_min"]
            > analytic_round_trip("LOW", 10)["capacity_per_min"])


def test_queue_length():
    a = Passenger("a", 1, 5, "LOW", False, False, arrived_at=0.0, boarded_at=10.0)
    b = Passenger("b", 1, 5, "LOW", False, False, arrived_at=5.0)       # never boards
    assert [queue_length([a, b], t) for t in (1, 6, 10, 20)] == [1, 2, 1, 1]


def test_short_measurement_runs_and_prints():
    rows = measure(rates=(30,), seeds=(1,), warmup_s=60, measure_s=600, clear_s=1200)
    assert [r.group for r in rows] == ["LOW", "HIGH"]
    assert all(r.arrived > 0 and r.cleared <= r.arrived for r in rows)
    text = format_tables(rows)
    assert "capacity_pax/min" in text and "cleared/arrived" in text
