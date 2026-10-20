from datetime import datetime

import pytest

from history.periods import parse_user_date, period_range
from timeutils import LOCAL_TZ, format_local, to_local, to_utc_naive

# 21.10.2026 03:00 in Tashkent (UTC+5) is still 20.10 22:00 in UTC.
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=LOCAL_TZ)
ORIGINAL = datetime(2026, 10, 15, 9, 30)  # naive UTC = 14:30 local


def test_zone_is_tashkent_in_tests():
    assert str(LOCAL_TZ) == 'Asia/Tashkent'


def test_round_trip_local_utc():
    assert to_utc_naive(datetime(2026, 10, 21, 14, 30)) == datetime(2026, 10, 21, 9, 30)
    assert to_local(datetime(2026, 10, 21, 9, 30)).hour == 14
    assert format_local(ORIGINAL) == '15.10.2026 14:30'
    assert format_local(ORIGINAL, with_year=False) == '15.10 14:30'


@pytest.mark.parametrize('period, since_utc', [
    ('today', datetime(2026, 10, 20, 19, 0)),   # local midnight of the 21st
    ('week', datetime(2026, 10, 14, 19, 0)),    # today and six days before
    ('month', datetime(2026, 9, 30, 19, 0)),
    ('year', datetime(2025, 12, 31, 19, 0)),
])
def test_period_range(period, since_utc):
    assert period_range(period, NOW) == (since_utc, None)


@pytest.mark.parametrize('period', ['all', 'unknown'])
def test_open_period(period):
    assert period_range(period, NOW) == (None, None)


@pytest.mark.parametrize('text, expected_local', [
    ('20.10', datetime(2026, 10, 20, 14, 30)),             # keeps the old time
    ('20.10.2026 10:05', datetime(2026, 10, 20, 10, 5)),
    ('1.2.25 7.15', datetime(2025, 2, 1, 7, 15)),
    ('вчера', datetime(2026, 10, 20, 14, 30)),
    ('Сегодня', datetime(2026, 10, 21, 14, 30)),
    ('yesterday', datetime(2026, 10, 20, 14, 30)),
    ('22.10', datetime(2026, 10, 22, 14, 30)),            # up to a day ahead is fine
])
def test_parse_user_date(text, expected_local):
    assert parse_user_date(text, ORIGINAL, NOW) == to_utc_naive(expected_local)


@pytest.mark.parametrize('text', [None, '', 'завтра', '31.02', '10.13', '25.10', '01.01.1999', '20.10 25:00', '20/10', 'abc'])
def test_parse_user_date_rejects(text):
    assert parse_user_date(text, ORIGINAL, NOW) is None
