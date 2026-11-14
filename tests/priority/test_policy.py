"""FR-76 / BR-10 order, FR-08 capacity and door time, FR-71 car preference (pure functions)."""
from ems.contracts.types import (Call, CallPriority, CallSource, CarStatus, Direction, DoorState)
from ems.priority import policy


def _call(cid, prio, t=0.0, party=1):
    return Call(cid, 1, 10, "LOW", t, CallSource.DESTINATION_PANEL, prio, party_size=party)


def _car(cid, pos, passengers=0, doors=DoorState.CLOSED):
    return CarStatus(cid, "LOW", int(pos), float(pos), Direction.IDLE, doors, 0.0, passengers)


def test_priority_order_medical_accessible_vip_ordinary_then_oldest_first():
    calls = [_call("n-old", CallPriority.NORMAL, 0), _call("vip", CallPriority.VIP, 5),
             _call("acc", CallPriority.ACCESSIBLE, 9), _call("med", CallPriority.MEDICAL, 10),
             _call("n-new", CallPriority.NORMAL, 3)]
    assert [c.call_id for c in policy.order(calls)] == ["med", "acc", "vip", "n-old", "n-new"]
    assert policy.rank(calls[3]) < policy.rank(calls[2]) < policy.rank(calls[1]) \
        < policy.rank(calls[0])


def test_accessible_door_time_is_10_s_others_5_s():
    assert policy.door_dwell_s(_call("a", CallPriority.ACCESSIBLE)) == 10.0
    assert policy.door_dwell_s(_call("n", CallPriority.NORMAL)) == 5.0


def test_accessible_needs_room_for_the_whole_party():
    acc = _call("a", CallPriority.ACCESSIBLE, party=2)
    assert policy.accessible_car_ok(acc, _car("A", 1, passengers=19), 21)
    assert not policy.accessible_car_ok(acc, _car("A", 1, passengers=20), 21)
    assert policy.accessible_car_ok(_call("n", CallPriority.NORMAL), _car("A", 1, 21), 21)


def test_eta_counts_travel_doors_and_closing():
    assert policy.eta_s(_car("A", 5), 1) == 4 * 1.5 + 2.0
    assert policy.eta_s(_car("A", 1, doors=DoorState.OPEN), 1) == 2.0 + 2.0


def test_vip_prefers_an_empty_car_within_30_s_over_a_faster_busy_car():
    busy_close = _car("A", 1, passengers=5)
    empty_near = _car("B", 10)                 # 9 floors: 13.5 + 2 = 15.5 s
    empty_far = _car("C", 30)                  # 29 floors: 45.5 s > 30 s
    order = [s.car_id for s in policy.vip_candidates([empty_far, busy_close, empty_near], 1)]
    assert order == ["B", "A", "C"]


def test_vip_takes_the_fastest_when_no_empty_car_is_within_30_s():
    order = [s.car_id for s in policy.vip_candidates(
        [_car("C", 30), _car("A", 3, passengers=4)], 1)]
    assert order == ["A", "C"]


def test_medical_prefers_cars_without_riders_then_fastest():
    order = [s.car_id for s in policy.medical_candidates(
        [_car("A", 1, passengers=3), _car("B", 12), _car("C", 6)], 1)]
    assert order == ["C", "B", "A"]
