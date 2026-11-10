"""Shared value types. Owned by: Tech lead. Change only with team approval.

Floors are ints: B2 = -2, B1 = -1, L1 = 1 ... L30 = 30 (no floor 0).
Times are simulated seconds (float) from SimClock.now().
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum
from typing import Optional

CarId = str        # "A".."H", "S"
GroupId = str      # "LOW", "HIGH", "SERVICE"
Floor = int
CredentialId = str
CallId = str


def floor_label(f: Floor) -> str:
    return f"B{-f}" if f < 0 else f"L{f}"


class Direction(Enum):
    UP = "up"
    DOWN = "down"
    IDLE = "idle"


class DoorState(Enum):
    OPEN = "open"
    OPENING = "opening"
    CLOSING = "closing"
    CLOSED = "closed"


class CarState(IntEnum):
    """Car service states (SRS Figure 4). Lower value = higher precedence (BR-06)."""
    FIRE_RECALL = 1
    EMERGENCY_POWER = 2
    FAULTED = 3          # includes Disconnected (see CarStatus.disconnected)
    MAINTENANCE = 4
    INDEPENDENT_SERVICE = 5
    VIP_EXCLUSIVE = 6
    STANDBY = 7
    GROUP_SERVICE = 8

    def outranks(self, other: "CarState") -> bool:
        return self.value < other.value

    @property
    def dispatchable(self) -> bool:
        """May the dispatcher assign ordinary hall calls to a car in this state?"""
        return self is CarState.GROUP_SERVICE


class Mode(Enum):
    RUSH = "rush"
    BALANCED = "balanced"
    ECO = "eco"


class CallPriority(IntEnum):
    """Passenger priority order (BR-10). Lower value = served first."""
    MEDICAL = 1
    ACCESSIBLE = 2
    VIP = 3
    NORMAL = 4


class Role(Enum):
    EMPLOYEE = "employee"
    VISITOR = "visitor"
    COURIER = "courier"
    VIP = "vip"
    ACCESSIBLE = "accessible"
    SECURITY = "security"
    TECHNICIAN = "technician"
    FM = "facility_manager"
    TENANT_ADMIN = "tenant_admin"
    OWNER = "owner"
    SYSADMIN = "system_admin"


class CredentialKind(Enum):
    CARD = "card"
    QR = "qr"
    GROUP_QR = "group_qr"
    BUTTON = "button"    # landing/car buttons, no credential


class CallSource(Enum):
    DESTINATION_PANEL = "destination_panel"
    LANDING_BUTTON = "landing_button"
    CAR_PANEL = "car_panel"
    SECURITY_CONSOLE = "security_console"   # medical priority, send-to-lobby


@dataclass(frozen=True)
class Permission:
    """Answer from the access service for one credential."""
    credential_id: CredentialId
    allowed_floors: frozenset[Floor]
    default_floor: Optional[Floor]
    roles: frozenset[Role]
    valid: bool                  # False = unknown / expired / revoked
    tenant_id: Optional[str] = None
    from_cache: bool = False     # True when served under ACS outage (FR-49)


@dataclass
class Call:
    """One passenger request. Created by Part 3 (panels) or Part 5 (priority)."""
    call_id: CallId
    origin: Floor
    destination: Optional[Floor]          # None for up/down button calls
    group: GroupId
    created_at: float                     # wait clock starts here (SRS 4.3)
    source: CallSource
    priority: CallPriority = CallPriority.NORMAL
    credential_id: Optional[CredentialId] = None   # never copied into trip records
    party_size: int = 1                   # group QR passes (FR-126)
    panel_id: Optional[str] = None


@dataclass
class CarStatus:
    """Latest telemetry for one car, as reported by its controller adapter."""
    car_id: CarId
    group: GroupId
    floor: Floor
    position: float                       # e.g. 7.4 = between L7 and L8
    direction: Direction
    doors: DoorState
    load_pct: float                       # % of rated load
    passengers: int                       # simulator truth; real system estimates
    fault_code: Optional[str] = None
    rescue_status: Optional[str] = None   # "rescued_at_floor" | "stuck" | None
    disconnected: bool = False
    pending_stops: list[Floor] = field(default_factory=list)
