"""History periods and dates typed by the user, both in the local time zone."""
import re
from datetime import date, datetime, time, timedelta
from typing import Optional, Tuple

from timeutils import LOCAL_TZ, local_now, to_local, to_utc_naive

PERIODS = ('all', 'today', 'week', 'month', 'year')

_DATE_RE = re.compile(r'^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?(?:\s+(\d{1,2})[:.](\d{2}))?$')
_TODAY = {'сегодня', 'today', 'сёння'}
_YESTERDAY = {'вчера', 'yesterday', 'учора'}
_EARLIEST_YEAR = 2000


def period_range(period: str, now: Optional[datetime] = None) -> Tuple[Optional[datetime], Optional[datetime]]:
    """(since, until) as naive UTC for the query; None means open-ended."""
    now = now or local_now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == 'today':
        since = midnight
    elif period == 'week':
        since = midnight - timedelta(days=6)  # today and the six days before
    elif period == 'month':
        since = midnight.replace(day=1)
    elif period == 'year':
        since = midnight.replace(month=1, day=1)
    else:
        return None, None
    return to_utc_naive(since), None


def parse_user_date(text: Optional[str], original_utc: datetime, now: Optional[datetime] = None) -> Optional[datetime]:
    """A new timestamp (naive UTC) from "21.10", "21.10.2026 14:30", "вчера"...; keeps the old time if none typed."""
    if not text:
        return None
    now = now or local_now()
    original = to_local(original_utc)
    value = ' '.join(text.split()).lower()

    if value in _TODAY or value in _YESTERDAY:
        day = now.date() - timedelta(days=1 if value in _YESTERDAY else 0)
        local = datetime.combine(day, original.timetz().replace(tzinfo=None), tzinfo=LOCAL_TZ)
        return to_utc_naive(local)

    match = _DATE_RE.match(value)
    if match is None:
        return None
    day, month, year, hour, minute = match.groups()
    year = now.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
    try:
        local = datetime(year, int(month), int(day),
                         int(hour) if hour is not None else original.hour,
                         int(minute) if minute is not None else original.minute,
                         tzinfo=LOCAL_TZ)
    except ValueError:  # 31.02, 25:00 and the like
        return None

    # Up to tomorrow by calendar date (a typo guard, not a time check).
    if local.year < _EARLIEST_YEAR or local.date() > now.date() + timedelta(days=1):
        return None
    return to_utc_naive(local)


_DAY_RE = re.compile(r'^(\d{1,2})\.(\d{1,2})(?:\.(\d{2}|\d{4}))?$')
_RANGE_SPLIT_RE = re.compile(r'\s*[-–—]\s*')
MAX_RANGE_DAYS = 3660  # about ten years


def _parse_day(text: str, now: datetime) -> Optional[date]:
    match = _DAY_RE.match(text)
    if match is None:
        return None
    day, month, year = match.groups()
    year = now.year if year is None else int(year) + (2000 if len(year) == 2 else 0)
    try:
        return date(year, int(month), int(day))
    except ValueError:
        return None


def parse_date_range(text: Optional[str], now: Optional[datetime] = None) -> Optional[Tuple[datetime, datetime]]:
    """"01.09.2026-30.09.2026", "01.09-30.09" or one date -> [since, until) as naive UTC, whole local days."""
    if not text:
        return None
    now = now or local_now()
    parts = _RANGE_SPLIT_RE.split(text.strip())
    if len(parts) not in (1, 2):
        return None
    days = [_parse_day(part, now) for part in parts]
    if None in days:
        return None
    start, end = days[0], days[-1]
    if start > end or start.year < _EARLIEST_YEAR or end > now.date() + timedelta(days=1):
        return None
    if (end - start).days > MAX_RANGE_DAYS:
        return None
    since = datetime.combine(start, time(), tzinfo=LOCAL_TZ)
    until = datetime.combine(end + timedelta(days=1), time(), tzinfo=LOCAL_TZ)
    return to_utc_naive(since), to_utc_naive(until)
