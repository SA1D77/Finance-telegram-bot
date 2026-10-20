"""Pure date arithmetic for recurring operations."""
import calendar
from datetime import date, timedelta
from typing import List, Optional, Tuple

MAX_CATCH_UP = 62  # occurrences created at once after downtime; more is surely a mistake


def _monthly(year: int, month: int, anchor_day: int) -> date:
    return date(year, month, min(anchor_day, calendar.monthrange(year, month)[1]))


def advance(current: date, frequency: str, anchor_day: int) -> date:
    """The occurrence after `current`."""
    if frequency == 'daily':
        return current + timedelta(days=1)
    if frequency == 'weekly':
        return current + timedelta(days=7)
    if frequency == 'monthly':
        year, month = (current.year + 1, 1) if current.month == 12 else (current.year, current.month + 1)
        return _monthly(year, month, anchor_day)
    raise ValueError(f'unknown frequency {frequency!r}')


def first_date(start: date, frequency: str) -> Tuple[date, int]:
    """(first occurrence, anchor_day) for a schedule starting on `start`."""
    anchor = start.weekday() if frequency == 'weekly' else start.day
    return start, anchor


def due_dates(next_date: date, today: date, frequency: str, anchor_day: int) -> Tuple[List[date], date]:
    """Occurrences up to today (inclusive) and the following next_date."""
    dates: List[date] = []
    current = next_date
    while current <= today and len(dates) < MAX_CATCH_UP:
        dates.append(current)
        current = advance(current, frequency, anchor_day)
    while current <= today:  # skip whatever exceeded the cap
        current = advance(current, frequency, anchor_day)
    return dates, current


def reminder_due(next_date: date, remind_days: int, today: date, reminded_for: Optional[date]) -> bool:
    """True once per occurrence, within remind_days before it."""
    if remind_days <= 0 or reminded_for == next_date:
        return False
    return next_date - timedelta(days=remind_days) <= today < next_date
