"""FR-44: Facility Manager configures public floors by time of day"""

from datetime import timedelta

from ..contracts.config import LOBBY
from ..contracts.types import Role


class PublicFloors:
    def __init__(self, ctx, floor_access):
        self.ctx = ctx
        self.floor_access = floor_access

        # Each entry: (start_minute, end_minute, allowed_floors)
        # Example: (540, 1020, {5, 7}) means 09:00-17:00.
        self.daily_windows = []

    def check_fm(self, fm_card):
        """Only a valid, live-verified Facility Manager may change schedules."""

        permission = self.floor_access.permission(fm_card)

        if not permission.valid or permission.from_cache:
            raise PermissionError("A valid live FM card is required")

        if Role.FM not in permission.roles:
            raise PermissionError("Only Facility Manager may change public floors")

    def to_minutes(self, time_text):
        """Convert HH:MM text into minutes after midnight."""

        # STEP 1: Check format: exactly HH:MM
        if not isinstance(time_text, str) or len(time_text) != 5:
            raise ValueError("Time must be HH:MM, e.g. 09:00")

        if time_text[2] != ":":
            raise ValueError("Time must be HH:MM")

        hours_text = time_text[:2]
        minutes_text = time_text[3:]
        if not hours_text.isdigit() or not minutes_text.isdigit():
            raise ValueError("Time must contain digits")

        # STEP 2: Check that hours/minutes are in range
        hours = int(hours_text)
        minutes = int(minutes_text)
        if hours > 23 or minutes > 59:
            raise ValueError("Invalid time of day")

        return hours * 60 + minutes

    def check_floors(self, floors):
        """Check that each configured floor is a real floor number."""

        if not isinstance(floors, (list, tuple, set, frozenset)) or not floors:
            raise ValueError("Choose at least one floor")

        selected = set()
        for floor in floors:
            if type(floor) is not int or floor == 0:
                raise ValueError("Floor numbers must be non-zero integers")
            selected.add(floor)

        # When simulator provides a building layout, check its actual floors.
        building = self.ctx.services.get("building")
        if building is not None and hasattr(building, "groups"):
            existing_floors = set()
            for group in building.groups:
                existing_floors.update(group.floors)
            if not selected.issubset(existing_floors):
                raise ValueError("Floor does not exist in this building")

        return selected

    def set_public_hours(self, fm_card, floors, start_time, end_time):
        """Add or replace a daily public-access time window."""

        # STEP 1: Verify the person configuring the schedule
        self.check_fm(fm_card)

        # STEP 2: Check floors and time (24-hour clock)
        selected = self.check_floors(floors)
        start = self.to_minutes(start_time)
        end = self.to_minutes(end_time)
        if start == end:
            raise ValueError("Start and end cannot be the same")

        # STEP 3: Replace the existing window with the same times, if any
        for index, window in enumerate(self.daily_windows):
            if window[0] == start and window[1] == end:
                self.daily_windows[index] = (start, end, selected)
                return

        # STEP 4: Otherwise, add a new time window
        self.daily_windows.append((start, end, selected))

    def remove_public_hours(self, fm_card, start_time, end_time):
        """Remove one daily time window. Return True if it existed."""

        # STEP 1: Verify FM rights
        self.check_fm(fm_card)

        # STEP 2: Find and remove the window
        start = self.to_minutes(start_time)
        end = self.to_minutes(end_time)

        for index, window in enumerate(self.daily_windows):
            if window[0] == start and window[1] == end:
                self.daily_windows.pop(index)
                return True

        return False

    def get_public_floors(self, at_time):
        """Return floors accessible without a credential at simulated time at_time."""

        # STEP 1: Lobby is ALWAYS public (BR-07)
        building = self.ctx.services.get("building")
        lobby = building.lobby if building is not None else LOBBY
        result = {lobby}

        # STEP 2: Find the requested simulated time of day
        # clock.now() = seconds since simulation started.
        # clock.now_dt() = real date/time inside the simulation.
        seconds_from_now = at_time - self.ctx.clock.now()
        date_and_time = self.ctx.clock.now_dt() + timedelta(seconds=seconds_from_now)
        minute_of_day = date_and_time.hour * 60 + date_and_time.minute

        # STEP 3: Add floors from each active window
        for start, end, floors in self.daily_windows:
            if start < end:
                active = start <= minute_of_day < end
            else:
                # Example: 22:00-02:00 crosses midnight
                active = minute_of_day >= start or minute_of_day < end

            if active:
                result.update(floors)

        return result
