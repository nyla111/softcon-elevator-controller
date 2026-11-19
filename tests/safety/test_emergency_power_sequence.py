from dataclasses import FrozenInstanceError

import pytest

from ems.safety.emergency_power import ParkingSequence


def test_empty_sequence_is_exhausted_without_a_request():
    sequence = ParkingSequence(())
    assert sequence.reserve() is None
    progress = sequence.snapshot()
    assert progress.pending == progress.results == ()
    assert progress.outstanding is None and not progress.revoked


def test_first_reservation_blocks_second_until_completion():
    sequence = ParkingSequence(("car-z", "car-a"))
    first = sequence.reserve()
    assert first.car_id == "car-z"
    before = sequence.snapshot()
    assert before.outstanding is first and before.pending == ("car-a",)
    assert sequence.reserve() is None
    assert sequence.snapshot() == before
    assert sequence.finish(first.request_id, first.car_id, "completed")
    progress = sequence.snapshot()
    assert progress.outstanding is None
    assert progress.results[0].request is first
    assert progress.results[0].outcome == "completed"
    second = sequence.reserve()
    assert second.car_id == "car-a" and second.request_id is not first.request_id
    assert sequence.finish(second.request_id, second.car_id, "completed")
    assert sequence.reserve() is None
    assert sequence.snapshot().pending == ()


def test_refusal_allows_next_request_without_recording_completion():
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    assert sequence.finish(first.request_id, first.car_id, "refused")
    assert [result.outcome for result in sequence.snapshot().results] == ["refused"]
    assert sequence.reserve().car_id == "second"


@pytest.mark.parametrize("outcome", ["accepted", "pending", "timeout", "cancelled", "", "COMPLETED"])
def test_nonterminal_or_invalid_outcomes_do_not_advance(outcome):
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    before = sequence.snapshot()
    assert not sequence.finish(first.request_id, first.car_id, outcome)
    assert sequence.reserve() is None
    assert sequence.snapshot() == before


@pytest.mark.parametrize("wrong_id,wrong_car", [(True, False), (False, True), (True, True)])
def test_mismatched_response_does_not_release_outstanding_request(wrong_id, wrong_car):
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    before = sequence.snapshot()
    assert not sequence.finish(object() if wrong_id else first.request_id,
                               "second" if wrong_car else first.car_id, "completed")
    assert sequence.snapshot() == before
    assert sequence.reserve() is None


def test_outcome_without_any_reservation_is_ignored():
    sequence = ParkingSequence(("first",))
    before = sequence.snapshot()
    assert not sequence.finish(object(), "first", "completed")
    assert sequence.snapshot() == before


@pytest.mark.parametrize("duplicate", ["completed", "refused"])
def test_duplicate_and_stale_outcomes_cannot_finish_a_later_request(duplicate):
    # Repeated car IDs make correlation, rather than car identity, decisive.
    sequence = ParkingSequence(("same", "same"))
    first = sequence.reserve()
    assert sequence.finish(first.request_id, first.car_id, "completed")
    before = sequence.snapshot()
    assert not sequence.finish(first.request_id, first.car_id, duplicate)
    assert sequence.snapshot() == before
    second = sequence.reserve()
    before = sequence.snapshot()
    assert not sequence.finish(first.request_id, first.car_id, duplicate)
    assert sequence.snapshot() == before
    assert sequence.finish(second.request_id, second.car_id, "refused")


def test_response_from_another_sequence_cannot_match_even_for_the_same_car():
    old = ParkingSequence(("same",)).reserve()
    sequence = ParkingSequence(("same",))
    current = sequence.reserve()
    before = sequence.snapshot()
    assert old.request_id is not current.request_id
    assert not sequence.finish(old.request_id, old.car_id, "completed")
    assert sequence.snapshot() == before


def test_missing_acknowledgement_keeps_request_blocked_without_automatic_timeout():
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    before = sequence.snapshot()
    # No timer/clock dependency: only an explicit matching terminal report can release it.
    for _ in range(100):
        assert sequence.reserve() is None
        assert sequence.snapshot() == before
    assert sequence.snapshot().outstanding is first


def test_revocation_before_reservation_preserves_the_unissued_queue():
    sequence = ParkingSequence(("first", "second"))
    sequence.revoke()
    sequence.revoke()
    assert sequence.reserve() is None
    progress = sequence.snapshot()
    assert progress.revoked and progress.pending == ("first", "second")
    assert progress.outstanding is None and progress.results == ()


def test_revocation_preserves_an_outstanding_physical_operation():
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    sequence.revoke()
    sequence.revoke()
    progress = sequence.snapshot()
    assert progress.revoked and progress.outstanding is first
    assert progress.pending == ("second",) and progress.results == ()
    assert sequence.reserve() is None
    assert not sequence.finish(first.request_id, first.car_id, "stopped")
    assert sequence.snapshot() == progress


@pytest.mark.parametrize("outcome", ["completed", "refused"])
def test_late_terminal_after_revocation_records_result_without_reactivation(outcome):
    sequence = ParkingSequence(("first", "second"))
    first = sequence.reserve()
    sequence.revoke()
    replacement = ParkingSequence(("first", "second"))
    current = replacement.reserve()
    replacement_before = replacement.snapshot()
    assert sequence.finish(first.request_id, first.car_id, outcome)
    progress = sequence.snapshot()
    assert progress.revoked and progress.outstanding is None
    assert progress.pending == ("second",)
    assert [result.outcome for result in progress.results] == [outcome]
    assert sequence.reserve() is None
    assert not sequence.finish(first.request_id, first.car_id, outcome)
    assert sequence.snapshot() == progress
    assert not replacement.finish(first.request_id, first.car_id, outcome)
    assert replacement.snapshot() == replacement_before
    assert current.request_id is not first.request_id
    assert replacement.reserve() is None


def test_inspection_is_immutable_detached_and_does_not_mutate_progress():
    cars = ["z", "a"]
    sequence = ParkingSequence(cars)
    cars.reverse()
    initial = sequence.snapshot()
    first = sequence.reserve()
    waiting = sequence.snapshot()
    assert initial.pending == ("z", "a") and initial.outstanding is None
    assert first.car_id == "z"
    for _ in range(3):
        assert sequence.snapshot() == waiting
    with pytest.raises(FrozenInstanceError):
        waiting.revoked = True
    with pytest.raises(FrozenInstanceError):
        first.car_id = "other"
    assert sequence.finish(first.request_id, first.car_id, "completed")
    finished = sequence.snapshot()
    with pytest.raises(FrozenInstanceError):
        finished.results[0].outcome = "refused"
    assert waiting.results == () and waiting.outstanding is first
    assert sequence.snapshot() == finished


def test_order_is_required_and_has_no_destination_or_capacity_policy():
    with pytest.raises(TypeError):
        ParkingSequence()
    sequence = ParkingSequence(("custom-9", "custom-2", "custom-7"))
    requests = []
    while (request := sequence.reserve()) is not None:
        requests.append(request)
        assert sequence.finish(request.request_id, request.car_id, "completed")
    assert [request.car_id for request in requests] == ["custom-9", "custom-2", "custom-7"]
    assert all(set(vars(request)) == {"request_id", "car_id"} for request in requests)


@pytest.mark.parametrize("cars", [{"a", "b"}, {"a": 1}, "ab", b"ab"])
def test_unordered_collections_and_bare_strings_are_not_a_parking_order(cars):
    with pytest.raises(TypeError):
        ParkingSequence(cars)


@pytest.mark.parametrize("cars", [("",), (None,), (1,)])
def test_invalid_car_ids_are_rejected(cars):
    with pytest.raises(ValueError):
        ParkingSequence(cars)
