"""NFR-W1 travel-distance periods and wear-balance reporting.

Part 5 owns the counters.  Consumers only receive detached snapshots, so the additive
``usage`` service can become a formal contract after CCR #53 is approved without changing
the implementation.
"""
from __future__ import annotations

from datetime import datetime

from ems.contracts.events import CarStatusUpdated

MONTH_LIMIT = 0.02
PERIOD_LIMIT = 0.05
PERIOD_MONTHS = 6


def _floor_index(floor: int) -> int:
    return floor if floor < 0 else floor - 1


def _month_index(value: datetime) -> int:
    return value.year * 12 + value.month - 1


def _month_start(index: int) -> datetime:
    year, month = divmod(index, 12)
    return datetime(year, month + 1, 1)


class UsageService:
    def __init__(self, ctx) -> None:
        self.ctx = ctx
        now = ctx.clock.now_dt()
        self.period_index = _month_index(now)
        self.period_started_at = _month_start(self.period_index)
        self.period: dict[str, int] = {}
        self.monthly: dict[str, dict[str, int]] = {}
        self._last: dict[str, int] = {}

    def _rollover(self) -> None:
        now_index = _month_index(self.ctx.clock.now_dt())
        periods = (now_index - self.period_index) // PERIOD_MONTHS
        if periods > 0:
            self.period_index += periods * PERIOD_MONTHS
            self.period_started_at = _month_start(self.period_index)
            self.period.clear()
            # A report needs only months in the active six-month counter period.
            self.monthly = {key: value for key, value in self.monthly.items()
                            if _month_index(datetime.strptime(key, "%Y-%m")) >= self.period_index}

    def on_status(self, event: CarStatusUpdated) -> None:
        self._rollover()
        car = event.status.car_id
        current = _floor_index(event.status.floor)
        previous = self._last.get(car)
        self._last[car] = current
        if previous is None:
            return
        distance = abs(current - previous)
        if distance <= 0:
            return
        month = self.ctx.clock.now_dt().strftime("%Y-%m")
        self.period[car] = self.period.get(car, 0) + distance
        row = self.monthly.setdefault(month, {})
        row[car] = row.get(car, 0) + distance

    def distance(self, car_id: str) -> int:
        self._rollover()
        return self.period.get(car_id, 0)

    @staticmethod
    def _imbalance(distances: dict[str, int]) -> float:
        high = max(distances.values(), default=0)
        return 0.0 if high == 0 else (high - min(distances.values())) / high

    def wear_report(self) -> dict:
        self._rollover()
        building = self.ctx.get("building")
        current_month = self.ctx.clock.now_dt().strftime("%Y-%m")
        month_row = self.monthly.get(current_month, {})
        groups = {}
        for group in building.groups:
            month = {car: month_row.get(car, 0) for car in group.cars}
            period = {car: self.period.get(car, 0) for car in group.cars}
            monthly_imbalance = self._imbalance(month)
            period_imbalance = self._imbalance(period)
            groups[group.name] = {
                "cars": {car: {"month_floors": month[car],
                                "period_floors": period[car]} for car in group.cars},
                "monthly_imbalance": monthly_imbalance,
                "period_imbalance": period_imbalance,
                "monthly_pass": monthly_imbalance < MONTH_LIMIT,
                "period_pass": period_imbalance <= PERIOD_LIMIT,
            }
        return {
            "month": current_month,
            "period_start": self.period_started_at.date().isoformat(),
            "period_months": PERIOD_MONTHS,
            "monthly_limit": MONTH_LIMIT,
            "period_limit": PERIOD_LIMIT,
            "groups": groups,
        }


def subscribe(ctx, usage: UsageService) -> None:
    ctx.bus.subscribe(CarStatusUpdated, usage.on_status)
