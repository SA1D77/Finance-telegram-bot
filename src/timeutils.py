"""The database stores naive UTC timestamps; the user sees and types local time."""
import calendar
import logging
from datetime import datetime, timezone
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import TIMEZONE


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logging.warning('Unknown TIMEZONE %r, falling back to UTC', name)
        return ZoneInfo('UTC')


LOCAL_TZ = _zone(TIMEZONE)
TIMEZONE_NAME = LOCAL_TZ.key  # validated name, safe to pass to PostgreSQL's timezone()


def utc_now() -> datetime:
    """Naive UTC, the format of accounting.created_at."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ)


def to_local(utc_naive: datetime) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)


def to_utc_naive(local: datetime) -> datetime:
    if local.tzinfo is None:
        local = local.replace(tzinfo=LOCAL_TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def format_local(utc_naive: datetime, with_year: bool = True) -> str:
    return to_local(utc_naive).strftime('%d.%m.%Y %H:%M' if with_year else '%d.%m %H:%M')


def month_range(year: int, month: int) -> Tuple[datetime, datetime]:
    """[start, end) of a local calendar month as naive UTC."""
    start = datetime(year, month, 1, tzinfo=LOCAL_TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=LOCAL_TZ)
    return to_utc_naive(start), to_utc_naive(end)


def days_in_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def shift_month(year: int, month: int, delta: int) -> Tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1
