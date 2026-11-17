import itertools

import pytest

from ems.contracts.events import CarStateChanged
from ems.contracts.types import CarState as S
from ems.core.app import build
from ems.sim.building import BuildingSpec


def test_real_service_in_safety_only_build():
    ctx = build(parts=["safety"])
    service = ctx.services["car_states"]
    assert type(service).__name__ == "SafetyService"
    assert service.dispatchable("LOW") == list("ABCD")


@pytest.mark.parametrize("low,high", list(itertools.combinations(reversed(list(S)), 2)))
def test_every_precedence_pair(low, high):
    ctx = build(parts=["safety"])
    service = ctx.services["car_states"]
    assert service.request("A", low, "low")
    assert service.request("A", high, "high")
    assert service.state("A") is high
    assert not service.request("A", low, "refused")
    assert service.release("A", high, "high")
    assert service.state("A") is low


def test_owners_are_independent_and_only_effective_changes_emit_events():
    ctx = build(parts=["safety"])
    service = ctx.services["car_states"]
    assert service.request("A", S.MAINTENANCE, "technician:1")
    assert service.request("A", S.MAINTENANCE, "technician:2")
    assert not service.release("A", S.MAINTENANCE, "other")
    assert service.release("A", S.MAINTENANCE, "technician:1")
    assert service.state("A") is S.MAINTENANCE
    assert service.release("A", S.MAINTENANCE, "technician:2")
    assert service.state("A") is S.GROUP_SERVICE
    assert [(e.old, e.new) for e in ctx.bus.of_type(CarStateChanged)] == [
        (S.GROUP_SERVICE, S.MAINTENANCE), (S.MAINTENANCE, S.GROUP_SERVICE)]


def test_unknown_car_and_reserved_owner_cannot_create_or_release_holds():
    service = build(parts=["safety"]).services["car_states"]
    with pytest.raises(KeyError):
        service.request("unknown", S.FAULTED, "test")
    assert not service.request("A", S.FAULTED, "safety:fire")
    assert not service.release("A", S.FIRE_RECALL, "safety:fire")


def test_dynamic_layout_uses_all_64_cars():
    ctx = build(parts=["sim", "safety"], services={"building": BuildingSpec.design_limit()})
    service = ctx.services["car_states"]
    assert len(service.table.cars()) == 64
    assert service.request("G8-8", S.MAINTENANCE, "maintenance")
    assert service.dispatchable("G8") == [f"G8-{n}" for n in range(1, 8)]
