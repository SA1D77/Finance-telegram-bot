"""Pure calculations for the analytics screen (no database, easy to test)."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Dict, List, Optional, Tuple

CENT = Decimal('0.01')
ZERO = Decimal('0.00')  # shown as money, so keep the cents


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def percent(part: Decimal, whole: Decimal) -> Optional[Decimal]:
    """part / whole * 100 with one decimal, None when whole is zero."""
    if not whole:
        return None
    return (part * 100 / whole).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def savings_rate(income: Decimal, expense: Decimal) -> Optional[Decimal]:
    """Share of income left after expenses, %; negative when spending exceeds income."""
    return percent(income - expense, income)


def average_per_day(expense: Decimal, days: int) -> Decimal:
    return money(expense / days) if days > 0 else ZERO


def forecast(expense: Decimal, days_elapsed: int, days_total: int) -> Decimal:
    """Linear projection of this month's expenses to the end of the month."""
    if days_elapsed <= 0:
        return money(expense)
    return money(expense / days_elapsed * days_total)


def daily_allowance(available: Decimal, days_left: int) -> Decimal:
    """How much can be spent per day for the rest of the month; rounded down, never negative."""
    if days_left <= 0 or available <= 0:
        return ZERO
    return (available / days_left).quantize(CENT, rounding=ROUND_DOWN)


@dataclass
class Comparison:
    name: str
    current: Decimal
    previous: Decimal

    @property
    def diff(self) -> Decimal:
        return self.current - self.previous

    @property
    def change(self) -> Optional[Decimal]:
        """Change in %, None for a category that is new this month."""
        return percent(self.diff, self.previous)


def compare(current: Dict[str, Decimal], previous: Dict[str, Decimal], limit: int = 10) -> List[Comparison]:
    """Per category, sorted by this month's spending, then by last month's."""
    names = set(current) | set(previous)
    rows = [Comparison(name, current.get(name, ZERO), previous.get(name, ZERO)) for name in names]
    rows.sort(key=lambda row: (-row.current, -row.previous, row.name))
    return rows[:limit]


def shares(totals: List[Tuple[str, Decimal]], limit: int = 5) -> List[Tuple[str, Decimal, Optional[Decimal]]]:
    """Top categories with their share of all expenses, %."""
    whole = sum((amount for _, amount in totals), ZERO)
    ordered = sorted(totals, key=lambda item: -item[1])
    return [(name, amount, percent(amount, whole)) for name, amount in ordered[:limit]]
