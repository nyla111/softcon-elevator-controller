"""Reference building and fixed parameters (SRS 2.4, 4.3, Appendix B).
Owned by: Tech lead. Values here are defaults; FR-62 says they live in config.
"""
from __future__ import annotations

from .types import Mode

# --- Building (SRS 2.4) ---
LOW_FLOORS = [1] + list(range(2, 16))                 # L1..L15
HIGH_FLOORS = [1] + list(range(16, 31))               # L1, L16..L30
SERVICE_FLOORS = [-2, -1] + list(range(1, 31))        # B2..L30
LOBBY = 1
LOADING_DOCK = -1

GROUPS = {
    "LOW": {"cars": ["A", "B", "C", "D"], "floors": LOW_FLOORS},
    "HIGH": {"cars": ["E", "F", "G", "H"], "floors": HIGH_FLOORS},
    "SERVICE": {"cars": ["S"], "floors": SERVICE_FLOORS},
}
RATED = {"passenger": {"kg": 1600, "persons": 21}, "S": {"kg": 2000, "persons": 26}}

# --- Modes (SRS 4.3 table) ---
MODE_TARGETS = {          # (average wait target s, wait ceiling s)
    Mode.RUSH: (25, 60),
    Mode.BALANCED: (30, 90),
    Mode.ECO: (45, 120),
}
CEILING_WARN_FRACTION = 0.9          # FR-13
ECO_STEP_UP = {"pending_calls": 10, "for_s": 30, "avg_wait_s": 45, "window_s": 300}  # FR-14
ASSIGN_LOAD_LIMIT_PCT = 80           # FR-05

# Default weekday schedule (Figure 6), "HH:MM" -> mode
WEEKDAY_SCHEDULE = [
    ("00:00", Mode.ECO), ("07:00", Mode.RUSH), ("09:30", Mode.BALANCED),
    ("11:30", Mode.RUSH), ("13:30", Mode.BALANCED), ("17:00", Mode.RUSH),
    ("18:30", Mode.BALANCED), ("20:00", Mode.ECO),
]
WEEKEND_SCHEDULE = [("00:00", Mode.ECO)]

# --- Timeouts and limits used across parts ---
OTHER_FLOOR_WINDOW_S = 5            # FR-45
ACS_TIMEOUT_S = 1.0                 # FR-49
CACHE_MAX_AGE_S = 24 * 3600         # FR-49
HEARTBEAT_TIMEOUT_S = 3             # FR-104
RESCUE_REPORT_DEADLINE_S = 60       # FR-110
GENERATOR_WAIT_S = 15               # FR-110
MAINS_STABLE_S = 120                # FR-111
VIP_MAX_PER_HOUR = 6                # FR-72
VIP_EMPTY_CAR_ETA_S = 30            # FR-71
SPAM_ACTIONS_PER_60S = 5            # FR-121
SECOND_CAR_APPROVAL_TIMEOUT_S = 600 # FR-33

# --- Simulator physics (Appendix B) ---
SIM = {
    "s_per_floor": 1.5, "door_open_s": 2.0, "door_close_s": 2.0,
    "min_dwell_s": 5.0, "accessible_dwell_s": 10.0, "s_per_passenger": 1.0,
    "acs_reply_s": 0.1, "status_interval_s": 1.0,
}
