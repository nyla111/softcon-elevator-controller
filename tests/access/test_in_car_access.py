"""FR-48: Test in-car card authorization and floor selection."""

from types import SimpleNamespace

from ems.access.in_car_access import InCarAccess
from ems.access.refused_panel import RefusedPanel
from ems.contracts.events import AccessRefused, PanelMessage
from ems.contracts.types import Permission, Role


# ==========================================
# STEP 1: Fake dependencies
# ==========================================

class FakeClock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t


class FakeBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)

    def of_type(self, event_type):
        return [
            event for event in self.events
            if isinstance(event, event_type)
        ]


class FakeControllers:
    def __init__(self):
        self.stops = []
        self.can_register = True

        self.groups = {
            "A": "LOW",
            "B": "HIGH",
            "S": "SERVICE",
        }

    def status(self, car_id):
        return SimpleNamespace(
            group=self.groups[car_id]
        )

    def register_stop(self, car_id, floor, call_id):
        if not self.can_register:
            return False

        self.stops.append(
            (car_id, floor, call_id)
        )
        return True


class FakeLockdown:
    def __init__(self):
        self.locked_floors = set()

    def can_visit_floor(self, floor, permission):
        if floor == 1:
            return True

        if floor not in self.locked_floors:
            return True

        return (
            permission is not None
            and Role.SECURITY in permission.roles
        )


class FakeFloorAccess:
    def __init__(self):
        self.revoked = set()
        self.public = {1}
        self.lockdown = FakeLockdown()

    def invalid(self, card_id):
        return Permission(
            credential_id=card_id,
            allowed_floors=frozenset(),
            default_floor=None,
            roles=frozenset(),
            valid=False,
        )

    def permission(self, card_id):

        if card_id in self.revoked:
            return self.invalid(card_id)

        if card_id == "employee-1":
            return Permission(
                credential_id=card_id,
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.EMPLOYEE}),
                valid=True,
            )

        if card_id == "security-1":
            return Permission(
                credential_id=card_id,
                allowed_floors=frozenset({5, 7}),
                default_floor=5,
                roles=frozenset({Role.SECURITY}),
                valid=True,
            )

        if card_id == "courier-1":
            return Permission(
                credential_id=card_id,
                allowed_floors=frozenset({-1, 1}),
                default_floor=-1,
                roles=frozenset({Role.COURIER}),
                valid=True,
            )

        return self.invalid(card_id)

    def public_floors(self, at_time):
        return set(self.public)


class FakeAccessLogging:
    def __init__(self):
        self.logs = []

    def add_log(self, **log):
        self.logs.append(log)


# ==========================================
# STEP 2: Create a fresh test system
# ==========================================

def make_system():
    clock = FakeClock()
    bus = FakeBus()
    controllers = FakeControllers()
    floor_access = FakeFloorAccess()
    logger = FakeAccessLogging()

    ctx = SimpleNamespace(
        clock=clock,
        bus=bus,
        services={
            "controllers": controllers
        },
    )

    floor_access.refused_panel = RefusedPanel(
        ctx, floor_access
    )

    access = InCarAccess(
        ctx, floor_access, logger
    )

    return ctx, access, floor_access, controllers, logger


# ==========================================
# STEP 3: Test 10-second access window
# ==========================================

def test_01_public_floor_needs_no_card():
    # L1 is public, so no card is needed.

    ctx, access, floors, controllers, logs = make_system()

    assert access.press_floor("A", 1) is True
    assert controllers.stops[0][:2] == ("A", 1)


def test_02_secured_floor_without_card_is_refused():
    # L5 is secured. No card was tapped.

    ctx, access, floors, controllers, logs = make_system()

    assert access.press_floor("A", 5) is False
    assert controllers.stops == []
    assert len(ctx.bus.of_type(AccessRefused)) == 1


def test_03_valid_card_allows_secured_floor_within_10_seconds():
    # Employee taps card at t=0.
    # At t=9.9, the card is still valid for L5.

    ctx, access, floors, controllers, logs = make_system()

    assert access.tap_card("A", "employee-1") is True

    ctx.clock.t = 9.9

    assert access.press_floor("A", 5) is True
    assert controllers.stops[0][:2] == ("A", 5)


def test_04_exactly_10_seconds_is_too_late():
    # At t=10.0, the temporary authorization has expired.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")
    ctx.clock.t = 10.0

    assert access.press_floor("A", 5) is False
    assert controllers.stops == []


def test_05_invalid_card_cannot_unlock_secured_floor():
    # An unknown credential cannot authorize L5.

    ctx, access, floors, controllers, logs = make_system()

    assert access.tap_card("A", "unknown-card") is False
    assert access.press_floor("A", 5) is False
    assert controllers.stops == []


def test_06_card_does_not_unlock_other_car():
    # Card tapped in cabin A cannot unlock cabin B.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")

    assert access.press_floor("B", 5) is False
    assert controllers.stops == []


def test_07_card_cannot_open_floor_outside_its_permission():
    # Employee can access L5 and L7, but not L18.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")

    assert access.press_floor("A", 18) is False
    assert controllers.stops == []


def test_08_revoked_card_is_rechecked_before_pressing_floor():
    # The card was valid at tap time.
    # It is revoked before the floor button is pressed.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")
    floors.revoked.add("employee-1")

    assert access.press_floor("A", 5) is False
    assert controllers.stops == []


# ==========================================
# STEP 4: Test Lockdown and courier restrictions
# ==========================================

def test_09_lockdown_blocks_regular_employee():
    # Employee has L5 permission, but L5 is locked.

    ctx, access, floors, controllers, logs = make_system()

    floors.lockdown.locked_floors.add(5)
    access.tap_card("A", "employee-1")

    assert access.press_floor("A", 5) is False


def test_10_security_can_use_authorized_locked_floor():
    # Security can access L5 during Lockdown.

    ctx, access, floors, controllers, logs = make_system()

    floors.lockdown.locked_floors.add(5)
    access.tap_card("A", "security-1")

    assert access.press_floor("A", 5) is True


def test_11_courier_cannot_use_passenger_car_for_loading_dock():
    # Couriers must use SERVICE elevators for B1.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "courier-1")

    assert access.press_floor("A", -1) is False


def test_12_courier_can_use_service_car_for_loading_dock():
    # Courier in service cabin S can access B1.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("S", "courier-1")

    assert access.press_floor("S", -1) is True
    assert controllers.stops[0][:2] == ("S", -1)


# ==========================================
# STEP 5: Test controller and refusal messages
# ==========================================

def test_13_controller_can_refuse_a_stop():
    # Even if access is allowed, the controller
    # may refuse to register a stop.

    ctx, access, floors, controllers, logs = make_system()

    controllers.can_register = False
    access.tap_card("A", "employee-1")

    assert access.press_floor("A", 5) is False
    assert controllers.stops == []


def test_14_public_floor_rule_respects_lockdown():
    # L7 is public but currently locked down.

    ctx, access, floors, controllers, logs = make_system()

    floors.public.add(7)
    floors.lockdown.locked_floors.add(7)

    assert access.press_floor("A", 7) is False


def test_15_refusal_does_not_reveal_secured_floor_reason():
    # The panel must use the generic FR-46 message.

    ctx, access, floors, controllers, logs = make_system()

    assert access.press_floor("A", 5) is False

    message = ctx.bus.of_type(PanelMessage)[0]

    assert "Not recognised" in message.text_en
    assert "unauthorized" not in message.text_en
    assert "5" not in message.text_en


def test_16_successful_card_use_is_logged():
    # FR-43 records both the card tap
    # and the accepted secured stop.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")
    access.press_floor("A", 5)

    assert logs.logs[0]["action"] == "CARD_TAP"
    assert logs.logs[1]["action"] == "CAR_FLOOR_ACCEPTED"
    assert logs.logs[1]["destination"] == 5


def test_17_invalid_floor_number_is_rejected():
    # Floor 0 does not exist.

    ctx, access, floors, controllers, logs = make_system()

    assert access.press_floor("A", 0) is False
    assert controllers.stops == []


def test_18_expired_or_revoked_card_can_still_go_to_lobby():
    # BR-07: Access to Lobby must remain available.

    ctx, access, floors, controllers, logs = make_system()

    access.tap_card("A", "employee-1")
    floors.revoked.add("employee-1")

    assert access.press_floor("A", 1) is True
    assert controllers.stops[0][:2] == ("A", 1)
