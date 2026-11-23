"""FR-113 independent mechanical episodes; P5 retains FR-30 fault alerts."""
from collections import defaultdict, deque
from dataclasses import dataclass, field

WINDOW_S = 30 * 86400


@dataclass
class FaultReviews:
    history: dict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))

    def started(self, car_id: str, now: float) -> bool:
        """Called only on acquisition of a new Safety mechanical-fault hold.

        The trailing window includes its exact 30-day boundary. Repeated telemetry,
        link loss, fault assertions before clear, and further episodes while already
        above threshold do not generate another review notice.
        """
        times = self.history[car_id]
        while times and times[0] < now - WINDOW_S:
            times.popleft()
        before = len(times)
        times.append(now)
        return before < 3 <= len(times)
