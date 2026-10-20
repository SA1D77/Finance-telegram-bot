from datetime import datetime
from decimal import Decimal

import pytest

from analytics import metrics
from analytics.charts import month_labels, months_between
from history.periods import parse_date_range
from timeutils import LOCAL_TZ, shift_month, to_utc_naive

D = Decimal


@pytest.mark.parametrize('income, expense, rate', [
    ('100000', '75000', '25.0'), ('1000', '1000', '0.0'), ('1000', '1500', '-50.0'), ('3', '1', '66.7'),
])
def test_savings_rate(income, expense, rate):
    assert metrics.savings_rate(D(income), D(expense)) == D(rate)


def test_savings_rate_without_income():
    assert metrics.savings_rate(D('0'), D('500')) is None


def test_average_forecast_allowance():
    assert metrics.average_per_day(D('3100'), 10) == D('310.00')
    assert metrics.average_per_day(D('100'), 0) == D('0')  # Decimal equality ignores 0 vs 0.00
    assert metrics.forecast(D('3100'), 10, 31) == D('9610.00')
    assert metrics.forecast(D('1'), 3, 31) == D('10.33')
    assert metrics.daily_allowance(D('1000'), 3) == D('333.33')   # rounded down
    assert metrics.daily_allowance(D('-5'), 3) == D('0')
    assert metrics.daily_allowance(D('100'), 0) == D('0')


def test_compare_orders_and_marks_new_categories():
    rows = metrics.compare({'кафе': D('9000'), 'такси': D('500')}, {'кафе': D('7000'), 'спорт': D('2000')})

    assert [(r.name, r.current, r.previous) for r in rows] == [
        ('кафе', D('9000'), D('7000')), ('такси', D('500'), D('0')), ('спорт', D('0'), D('2000'))]
    assert rows[0].diff == D('2000') and rows[0].change == D('28.6')
    assert rows[1].change is None          # new this month
    assert rows[2].change == D('-100.0')   # nothing spent this month


def test_compare_limit():
    assert len(metrics.compare({str(i): D(i) for i in range(20)}, {}, limit=10)) == 10


def test_shares():
    assert metrics.shares([('a', D('50')), ('b', D('30')), ('c', D('20'))], limit=2) == [
        ('a', D('50'), D('50.0')), ('b', D('30'), D('30.0'))]
    assert metrics.shares([]) == []


def test_months_helpers():
    assert months_between((2025, 11), (2026, 2)) == [(2025, 11), (2025, 12), (2026, 1), (2026, 2)]
    assert month_labels([(2025, 12), (2026, 1)]) == ['12.25', '01.26']
    assert shift_month(2026, 1, -1) == (2025, 12)
    assert shift_month(2026, 10, -11) == (2025, 11)
    assert shift_month(2025, 12, 1) == (2026, 1)


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=LOCAL_TZ)


def local(*args):
    return to_utc_naive(datetime(*args))


@pytest.mark.parametrize('text, since, until', [
    ('01.09.2026-30.09.2026', local(2026, 9, 1), local(2026, 10, 1)),
    ('01.09 - 30.09', local(2026, 9, 1), local(2026, 10, 1)),
    ('1.9.26—15.9.26', local(2026, 9, 1), local(2026, 9, 16)),
    ('05.10.2026', local(2026, 10, 5), local(2026, 10, 6)),
    ('20.12.2025-21.10.2026', local(2025, 12, 20), local(2026, 10, 22)),
])
def test_parse_date_range(text, since, until):
    assert parse_date_range(text, NOW) == (since, until)


@pytest.mark.parametrize('text', [
    None, '', '30.09-01.09', '01.09-', '31.02-01.03', '01.01.1999-01.02.1999', '01.09-25.10', '01.09 14:00-02.09',
    '01.01.2000-01.01.2015', 'сентябрь', '01.09-02.09-03.09',
])
def test_parse_date_range_rejects(text):
    assert parse_date_range(text, NOW) is None
