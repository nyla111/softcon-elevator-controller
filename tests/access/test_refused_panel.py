
"""FR-46: Unit tests for Refused Credential UI."""

from types import SimpleNamespace

from ems.access.refused_panel import RefusedPanel
from ems.contracts.events import (
    AccessRefused,
    CallRequested,
    PanelMessage,
)
from ems.contracts.config import LOBBY


# ==========================================
# STEP 1: Fake dependencies
# ==========================================

class FakeClock:
    def __init__(self):
        self.t = 0

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


class FakeLockdown:
    def __init__(self):
        self.locked_floors = set()

    def can_visit_floor(self, floor, permission):
        if floor == LOBBY:
            return True

        return floor not in self.locked_floors


class FakeFloorAccess:
    def __init__(self):
        self.available_floors = {LOBBY}
        self.lockdown = FakeLockdown()

    def public_floors(self, at_time):
        return set(self.available_floors)


# ==========================================
# STEP 2: Create test system
# ==========================================

def make_system():
    clock = FakeClock()
    bus = FakeBus()
    floor_access = FakeFloorAccess()

    ctx = SimpleNamespace(
        clock=clock,
        bus=bus,
    )

    panel = RefusedPanel(ctx, floor_access)

    return ctx, panel, floor_access


# ==========================================
# STEP 3: Test refusal messages
# ==========================================

def test_01_refusal_creates_one_access_refused_event():
    # An invalid card must create one AccessRefused event.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", "bad-card")

    events = ctx.bus.of_type(AccessRefused)

    assert len(events) == 1
    assert events[0].credential_id == "bad-card"


def test_02_refusal_shows_message_on_correct_panel():
    # The message must appear on the correct panel.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", "bad-card")

    messages = ctx.bus.of_type(PanelMessage)

    assert len(messages) == 1
    assert messages[0].target == "panel-L1"

    assert messages[0].text_en.startswith(
        "Not recognised, please see reception"
    )


def test_03_refusal_message_has_vietnamese_translation():
    # The panel must also display Vietnamese text.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", "bad-card")

    text = ctx.bus.of_type(PanelMessage)[0].text_vi

    assert "lễ tân" in text


# ==========================================
# STEP 4: Test public floor display
# ==========================================

def test_04_only_public_floors_are_displayed():
    # L1 and L5 are public. L18 is secured.
    # The panel must not reveal L18.

    ctx, panel, access = make_system()

    access.available_floors = {1, 5}

    panel.refuse("panel-L1", "bad-card")

    text = ctx.bus.of_type(PanelMessage)[0].text_en

    assert "1, 5" in text
    assert "18" not in text


def test_05_locked_public_floor_is_not_offered():
    # L5 is normally public but currently locked.
    # The panel must offer only L1.

    ctx, panel, access = make_system()

    access.available_floors = {1, 5}
    access.lockdown.locked_floors.add(5)

    panel.refuse("panel-L1", "bad-card")

    text = ctx.bus.of_type(PanelMessage)[0].text_en

    assert "Public floors: 1" in text
    assert "5" not in text


def test_06_private_refusal_reason_is_not_shown():
    # Security receives the real reason: lockdown.
    # The passenger does not see that reason.

    ctx, panel, access = make_system()

    panel.refuse(
        "panel-L1",
        "bad-card",
        "lockdown"
    )

    refusal = ctx.bus.of_type(AccessRefused)[0]
    message = ctx.bus.of_type(PanelMessage)[0]

    assert refusal.reason == "lockdown"

    assert "lockdown" not in message.text_en.lower()
    assert "lockdown" not in message.text_vi.lower()


def test_07_credential_id_is_not_shown():
    # The panel must not reveal a credential ID.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", "secret-card-123")

    message = ctx.bus.of_type(PanelMessage)[0]

    assert "secret-card-123" not in message.text_en
    assert "secret-card-123" not in message.text_vi


def test_08_public_schedule_changes_are_reflected():
    # FR-44 makes L7 public.
    # The refusal message must show L7.

    ctx, panel, access = make_system()

    access.available_floors = {1, 7}

    panel.refuse("panel-L1", "bad-card")

    text = ctx.bus.of_type(PanelMessage)[0].text_en

    assert "Public floors: 1, 7" in text


def test_09_lobby_is_available_even_if_schedule_is_empty():
    # BR-07: Lobby is always available.

    ctx, panel, access = make_system()

    access.available_floors = set()

    panel.refuse("panel-L1", None)

    text = ctx.bus.of_type(PanelMessage)[0].text_en

    assert f"Public floors: {LOBBY}" in text


# ==========================================
# STEP 5: Test privacy and safety
# ==========================================

def test_10_different_refusal_reasons_show_same_message():
    # Invalid credentials and lockdown
    # must show the same generic message.

    ctx, panel, access = make_system()

    panel.refuse(
        "panel-A", "first-card", "invalid_credential"
    )

    panel.refuse(
        "panel-A", "second-card", "lockdown"
    )

    messages = ctx.bus.of_type(PanelMessage)

    assert messages[0].text_en == messages[1].text_en
    assert messages[0].text_vi == messages[1].text_vi


def test_11_refusal_never_creates_an_elevator_call():
    # Refusing a credential must not generate a call.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", "bad-card")

    assert ctx.bus.of_type(CallRequested) == []


def test_12_refused_anonymous_attempt_is_logged():
    # Even without a credential ID,
    # an attempted unauthorized access is recorded.

    ctx, panel, access = make_system()

    panel.refuse("panel-L1", None)

    refusals = ctx.bus.of_type(AccessRefused)

    assert len(refusals) == 1
    assert refusals[0].credential_id is None
