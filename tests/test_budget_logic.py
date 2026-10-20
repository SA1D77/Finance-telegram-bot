from datetime import datetime
from decimal import Decimal

import pytest

from budgets.service import BudgetStatus, days_left, month_bounds
from budgets.texts import alert_text, bar, status_emoji
from i18n import i18n
from timeutils import LOCAL_TZ


def status(spent, limit='1000.00', category_id=1, name='кафе'):
    return BudgetStatus(budget_id=1, category_id=category_id, name=name, limit=Decimal(limit), spent=Decimal(spent))


@pytest.mark.parametrize('spent, percent', [('0', 0), ('799.99', 79), ('800', 80), ('999.99', 99), ('1000', 100), ('2500', 250)])
def test_percent_is_floored(spent, percent):
    assert status(spent).percent == percent


def test_left_and_overspend():
    assert status('300').left == Decimal('700.00')
    assert status('1300').left == Decimal('-300.00')


@pytest.mark.parametrize('percent, expected', [(0, '░' * 10), (45, '▓▓▓▓░░░░░░'), (100, '▓' * 10), (250, '▓' * 10)])
def test_bar(percent, expected):
    assert bar(percent) == expected


@pytest.mark.parametrize('percent, emoji', [(10, '🟢'), (79, '🟢'), (80, '🟡'), (99, '🟡'), (100, '🔴'), (180, '🔴')])
def test_status_emoji(percent, emoji):
    assert status_emoji(percent) == emoji


def test_month_bounds_in_local_time():
    now = datetime(2026, 10, 21, 3, 0, tzinfo=LOCAL_TZ)  # Tashkent, UTC+5
    assert month_bounds(now) == ('2026-10', datetime(2026, 9, 30, 19, 0), datetime(2026, 10, 31, 19, 0))


def test_month_bounds_december_wraps_year():
    now = datetime(2026, 12, 31, 23, 59, tzinfo=LOCAL_TZ)
    period, _, until = month_bounds(now)
    assert period == '2026-12' and until == datetime(2026, 12, 31, 19, 0)


@pytest.mark.parametrize('now, left', [
    (datetime(2026, 10, 1, tzinfo=LOCAL_TZ), 31),
    (datetime(2026, 10, 21, tzinfo=LOCAL_TZ), 11),
    (datetime(2026, 10, 31, 23, 0, tzinfo=LOCAL_TZ), 1),
    (datetime(2028, 2, 29, tzinfo=LOCAL_TZ), 1),
])
def test_days_left(now, left):
    assert days_left(now) == left


def ru(message):
    return message


def test_alert_texts_ru():
    assert alert_text(status('820'), 80, ru) == '🟡 Кафе: потрачено 820 из 1000.00 (82%). Осталось 180.00.'
    assert alert_text(status('1300', category_id=None, name=None), 100, ru) == (
        '🔴 Общий бюджет: лимит исчерпан. Потрачено 1300 из 1000.00 (130%), перерасход 300.00.')


def test_alert_text_translated_and_escaped():
    with i18n.context():
        text = alert_text(status('900', name='<кафе>'), 80, lambda m: i18n.gettext(m, locale='en'))
    assert text == '🟡 &lt;кафе&gt;: spent 900 of 1000.00 (90%). 100.00 left.'
