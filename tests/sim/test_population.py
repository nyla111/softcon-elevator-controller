from ems.core.app import build
from ems.sim.population import ALL_TENANT_FLOORS, PUBLIC_FLOORS, Population


def pop(seed=1, **kw):
    return Population.build(build(seed=seed, parts=[]).rng, **kw)


def test_ids_floors_and_allowed_floors():
    p = pop()
    assert len(p.employees) == 29 * 30
    e = p.lookup("card-L18-007")
    assert (e.tenant_floor, e.default_floor) == (18, 18)
    assert e.allowed_floors == frozenset({18}) | PUBLIC_FLOORS
    assert all(len(p.on_floor(f)) == 30 for f in ALL_TENANT_FLOORS)
    assert p.lookup("card-L1-001") is None


def test_exact_accessible_and_vip_counts():
    p = pop()
    assert sum(e.accessible for e in p.employees) == round(0.05 * 870)
    assert sum(e.vip for e in p.employees) == round(0.01 * 870)


def test_same_seed_same_population_and_registered_by_sim():
    assert pop(3).employees == pop(3).employees
    assert pop(3).employees != pop(4).employees
    ctx = build(seed=3, parts=["sim"])
    assert ctx.services["population"].employees == pop(3).employees
