import os
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from analytics import handlers as a
from report import files as report_files
from telegram.states import ReportState
from timeutils import LOCAL_TZ

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal
NOW = datetime(2026, 10, 21, 12, 0, tzinfo=LOCAL_TZ)  # day 21 of 31


class FakeAnalytics:
    def __init__(self):
        self.months = {
            (2026, 10): {'income': D('100000.00'), 'by_cat': {'кафе': D('9000.00'), 'продукты': D('12000.00'), 'такси': D('500.00')}},
            (2026, 9): {'income': D('90000.00'), 'by_cat': {'кафе': D('7000.00'), 'продукты': D('12000.00'), 'спорт': D('2000.00')}},
        }
        self.calls = []

    def _month(self, since):
        local = since.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)
        return self.months.get((local.year, local.month), {'income': D('0'), 'by_cat': {}})

    async def totals(self, user_id, since, until):
        assert user_id == USER_ID
        m = self._month(since)
        return m['income'], sum(m['by_cat'].values(), D('0'))

    async def expenses_by_category(self, user_id, since, until):
        return list(self._month(since)['by_cat'].items())

    async def largest_expenses(self, user_id, since, until, limit=5):
        if not self._month(since)['by_cat']:
            return []
        return [SimpleNamespace(created_at=datetime(2026, 10, 5, 7, 0), quantity=D('8000.00'), comment='ноутбук <б/у>', category='техника'),
                SimpleNamespace(created_at=datetime(2026, 10, 6, 7, 0), quantity=D('3000.00'), comment=None, category='продукты')]

    async def monthly_totals(self, user_id, since, until):
        self.calls.append((since, until))
        return {(2026, 9): (D('90000'), D('21000')), (2026, 10): (D('100000'), D('21500'))}


@pytest.fixture
def fake(monkeypatch):
    f = FakeAnalytics()
    for name in ('totals', 'expenses_by_category', 'largest_expenses', 'monthly_totals'):
        monkeypatch.setattr(a, name, getattr(f, name))
    monkeypatch.setattr(a, 'local_now', lambda: NOW)

    async def no_budget(user_id):
        return None

    monkeypatch.setattr(a, '_overall_budget_left', no_budget)
    return f


def last(harness):
    return harness.session.requests[-1]


def buttons_of(method):
    return [(b.text, b.callback_data) for row in method.reply_markup.inline_keyboard for b in row]


async def test_current_month_screen(harness, fake):
    await harness.feed(make_message('/stats'))

    text = last(harness).text
    assert text.startswith('📈 Аналитика · 10.2026')
    assert '💰 Доходы: 100000.00' in text and '💸 Расходы: 21500.00' in text
    assert '🏦 Норма сбережений: 78.5%' in text
    assert '📆 Средний расход в день: 1023.81' in text            # 21500 / 21
    assert '🔮 Прогноз расходов на конец месяца: 31738.10' in text  # 21500 / 21 * 31
    assert '✅ Можно тратить в день: 7136.36 (из остатка доходов)' in text  # 78500 / 11
    assert '📊 Сравнение с 09.2026:' in text
    assert 'Продукты: <b>12000.00</b> (было 12000.00, 0.00, 0.0%)' in text
    assert 'Кафе: <b>9000.00</b> (было 7000.00, +2000.00, +28.6%)' in text
    assert 'Такси: <b>500.00</b> (было 0.00, +500.00, новая)' in text
    assert 'Спорт: <b>0.00</b> (было 2000.00, -2000.00, -100.0%)' in text
    assert 'Итого: 21500.00 (было 21000.00, +2.4%)' in text
    assert '1. Продукты — <b>12000.00</b> (55.8%)' in text
    assert '💥 Самые крупные траты:' in text and 'ноутбук &lt;б/у&gt; — <b>8000.00</b>' in text
    assert [d for _, d in buttons_of(last(harness))] == ['a:m:2026-09', 'a:dyn:2026-10']


async def test_menu_button_opens_analytics(harness, fake):
    await harness.feed(make_message('📈 Аналитика'))

    assert last(harness).text.startswith('📈 Аналитика · 10.2026')


async def test_budget_is_used_for_daily_allowance(harness, fake, monkeypatch):
    async def budget_left(user_id):
        return D('5500.00')

    monkeypatch.setattr(a, '_overall_budget_left', budget_left)

    await harness.feed(make_message('/stats'))

    assert '✅ Можно тратить в день: 500.00 (по общему бюджету)' in last(harness).text


async def test_nothing_left_to_spend(harness, fake, monkeypatch):
    async def overspent(user_id):
        return D('-10')

    monkeypatch.setattr(a, '_overall_budget_left', overspent)

    await harness.feed(make_message('/stats'))

    assert '⚠️ Тратить больше нечего (по общему бюджету)' in last(harness).text


async def test_past_month_has_no_forecast_and_can_go_forward(harness, fake):
    await harness.feed(make_callback('a:m:2026-09'))

    edit = harness.session.requests[-2]
    assert edit.text.startswith('📈 Аналитика · 09.2026')
    assert '🔮' not in edit.text and 'Можно тратить' not in edit.text
    assert '📆 Средний расход в день: 700.00' in edit.text  # 21000 / 30 days
    assert [d for _, d in buttons_of(edit)] == ['a:m:2026-08', 'a:m:2026-10', 'a:dyn:2026-09']


async def test_empty_month(harness, fake):
    await harness.feed(make_callback('a:m:2025-01'))

    assert 'Операций нет.' in harness.session.requests[-2].text
    assert '🏦 Норма сбережений: нет доходов' in harness.session.requests[-2].text


@pytest.mark.parametrize('data', ['a:m:2026-11', 'a:m:2026-13', 'a:m:1999-01', 'a:m:x', 'a:dyn:2030-01'])
async def test_future_or_bad_month_is_ignored(harness, fake, data):
    await harness.feed(make_callback(data))

    assert [type(m).__name__ for m in harness.session.requests] == ['AnswerCallbackQuery']


async def test_dynamics_chart_is_sent_and_removed(harness, fake):
    await harness.feed(make_callback('a:dyn:2026-10'))

    photo = harness.session.requests[-2]
    assert type(photo).__name__ == 'SendPhoto'
    assert photo.caption == '📊 Доходы и расходы: 11.2025 — 10.2026'
    path, existed = harness.session.sent_files[0]
    assert existed and not os.path.exists(path)
    since, until = fake.calls[0]
    assert (since, until) == (datetime(2025, 10, 31, 19, 0), datetime(2026, 10, 31, 19, 0))


async def test_stranger_ignored(harness, fake):
    await harness.feed(make_message('/stats', user=make_user(user_id=2002)))

    assert harness.session.requests == []


# --- /report periods ---------------------------------------------------------

async def test_report_custom_range_flow(harness):
    await harness.feed(make_message('/report'))
    await harness.feed(make_callback('time_custom'))
    assert await harness.state() == ReportState.custom_range.state

    await harness.feed(make_message('31.09-01.10'))
    assert await harness.state() == ReportState.custom_range.state
    assert 'Не понял период' in last(harness).text

    await harness.feed(make_message('01.09.2026-30.09.2026'))

    assert await harness.state() == ReportState.send_place.state
    data = await harness.data()
    assert (data['since'], data['until']) == ('2026-08-31T19:00:00', '2026-09-30T19:00:00')


@pytest.mark.parametrize('button, expect_since', [('time_today', True), ('time_year', True), ('time_365', True)])
async def test_report_period_buttons(harness, button, expect_since):
    await harness.feed(make_message('/report'))
    await harness.feed(make_callback(button))

    data = await harness.data()
    assert await harness.state() == ReportState.send_place.state
    assert (data['since'] is not None) is expect_since and data['until'] is None


async def test_report_unknown_period_ignored(harness):
    await harness.set_state(ReportState.time_interval)

    await harness.feed(make_callback('time_forever'))

    assert await harness.state() == ReportState.time_interval.state


def test_multi_month_report_gets_dynamics_chart(tmp_path, monkeypatch):
    monkeypatch.setattr(report_files, 'GENERATED_DIR', str(tmp_path))
    rows = [{'category': 'кафе', 'date': 'x', 'month': month, 'quantity': '100.00', 'operation_type': op, 'comment': ''}
            for month, op in [('2026-08', 'Expenditure'), ('2026-10', 'Income'), ('2026-10', 'Expenditure')]]

    files = report_files.build_report_files(rows, 'ru')
    try:
        assert files.dynamics_diagram and os.path.getsize(files.dynamics_diagram) > 0
    finally:
        report_files.remove_report_files(files)

    single = report_files.build_report_files(rows[1:], 'ru')
    try:
        assert single.dynamics_diagram is None
    finally:
        report_files.remove_report_files(single)


def test_report_rows_use_local_time():
    row = SimpleNamespace(categories='кафе', created_at=datetime(2026, 9, 30, 20, 0), quantity=D('1.00'),
                          operation_type=OperationType.income, comment=None)

    data = report_files.serialize_accounts([row])[0]

    assert data['date'] == '01.10.2026 01:00' and data['month'] == '2026-10'  # Tashkent is UTC+5
