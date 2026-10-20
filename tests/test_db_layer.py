"""Data layer against a real, freshly migrated PostgreSQL database (needs TEST_PG_URL)."""
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from exception import EmailAlreadyExist
from middleware import accounting, service, user

psycopg2 = pytest.importorskip('psycopg2')

TEST_PG_URL = os.environ.get('TEST_PG_URL')
pytestmark = pytest.mark.skipif(not TEST_PG_URL, reason='TEST_PG_URL is not set')

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / 'alembic'
OWNER, OTHER = 1001, 2002


@pytest.fixture
async def db(monkeypatch):
    url = urlparse(TEST_PG_URL)
    name = f'db_layer_test_{uuid.uuid4().hex[:8]}'
    admin = psycopg2.connect(TEST_PG_URL)
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE {name}')
    password = url.password or 'unused'
    env = dict(os.environ, HOST=url.hostname, PORT=str(url.port or 5432), POSTGRES_USER=url.username,
               POSTGRES_PASSWORD=password, POSTGRES_DB=name, OWNER_ID=str(OWNER))
    subprocess.run([sys.executable, '-m', 'alembic', 'upgrade', 'head'], cwd=ALEMBIC_DIR, env=env,
                   check=True, capture_output=True)

    engine = create_async_engine(f'postgresql+asyncpg://{url.username}:{password}@{url.hostname}:{url.port or 5432}/{name}')
    monkeypatch.setattr(service, 'async_sessioin_maker', sessionmaker(engine, class_=AsyncSession, expire_on_commit=False))
    try:
        yield
    finally:
        await engine.dispose()
        admin.cursor().execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        admin.close()


async def test_ensure_user_creates_once_and_tracks_username(db):
    assert await user.ensure_user(OWNER, 'old_name') is True
    assert await user.ensure_user(OWNER, 'new_name') is False
    assert await user.ensure_user(OWNER, None) is False  # username removed in Telegram: data stays


async def test_language_and_email(db):
    await user.ensure_user(OWNER, None)
    assert await user._get_user_lang(OWNER) is None

    await user._language_locale(OWNER, 'en')
    await user._insert_user_email(OWNER, 'me@example.com')

    assert await user._get_user_lang(OWNER) == 'en'
    assert await user._get_users_email(OWNER) == 'me@example.com'
    with pytest.raises(EmailAlreadyExist):
        await user._insert_user_email(OWNER, 'other@example.com')


async def test_categories_are_isolated_between_users(db):
    for uid in (OWNER, OTHER):
        await user.ensure_user(uid, None)
    await accounting._add_new_category('еда', OWNER)
    await accounting._add_new_category('еда', OTHER)  # same name, different user: allowed now

    own = await accounting._show_all_categories(OWNER)
    other = await accounting._show_all_categories(OTHER)

    assert [c.name for c in own] == ['еда'] and [c.name for c in other] == ['еда']
    assert own[0].id != other[0].id
    assert await accounting._get_category(other[0].id, OWNER) is None  # cannot use someone else's id
    assert (await accounting._get_category(own[0].id, OWNER)).name == 'еда'


async def test_deleted_category_is_restored_with_same_id(db):
    await user.ensure_user(OWNER, None)
    await accounting._add_new_category('кафе', OWNER)
    category = (await accounting._show_all_categories(OWNER))[0]

    await accounting._delete_category(OWNER, category.id)
    assert await accounting._show_all_categories(OWNER) == []
    assert await accounting._get_category(category.id, OWNER) is None
    assert await accounting._find_active_category('кафе', OWNER) is None

    await accounting._add_new_category('кафе', OWNER)
    assert [c.id for c in await accounting._show_all_categories(OWNER)] == [category.id]


async def test_balance_is_exact_and_per_user(db):
    for uid in (OWNER, OTHER):
        await user.ensure_user(uid, None)
        await accounting._add_new_category('всё', uid)
    own_cat = (await accounting._show_all_categories(OWNER))[0].id
    other_cat = (await accounting._show_all_categories(OTHER))[0].id

    await accounting._add_income(OWNER, own_cat, Decimal('0.10'))
    await accounting._add_income(OWNER, own_cat, Decimal('0.20'))
    await accounting._add_expenditure(OWNER, own_cat, Decimal('0.05'))
    await accounting._add_income(OTHER, other_cat, Decimal('1000000.00'))

    assert await accounting._get_balance(OWNER) == Decimal('0.25')
    assert await accounting._get_balance(OTHER) == Decimal('1000000.00')
    assert await accounting._get_balance(3003) == Decimal('0')


async def test_accounts_are_per_user_and_last_is_limited(db):
    for uid in (OWNER, OTHER):
        await user.ensure_user(uid, None)
        await accounting._add_new_category('еда', uid)
    own_cat = (await accounting._show_all_categories(OWNER))[0].id
    other_cat = (await accounting._show_all_categories(OTHER))[0].id
    for i in range(12):
        await accounting._add_expenditure(OWNER, own_cat, Decimal(i + 1))
    await accounting._add_expenditure(OTHER, other_cat, Decimal('999'))

    last = await accounting._get_accounts(OWNER, 'last')
    month = await accounting._get_accounts(OWNER, '30')

    assert len(last) == 10
    assert len(month) == 12
    assert {row.categories for row in month} == {'еда'}
    assert Decimal('999') not in {row.quantity for row in last + month}


async def test_comment_is_stored_and_returned(db):
    await user.ensure_user(OWNER, None)
    await accounting._add_new_category('кафе', OWNER)
    category = (await accounting._show_all_categories(OWNER))[0]

    await accounting._add_operation(OWNER, category.id, Decimal('500.00'), accounting.OperationType.expenditure, comment='обед')
    await accounting._add_expenditure(OWNER, category.id, Decimal('1.00'))

    rows = await accounting._get_accounts(OWNER, '30')
    assert sorted((row.quantity, row.comment) for row in rows) == [(Decimal('1.00'), None), (Decimal('500.00'), 'обед')]


async def _two_users_with_operations():
    from history import queries  # noqa: F401  (imported lazily: needs the patched session)
    ids = {}
    for uid in (OWNER, OTHER):
        await user.ensure_user(uid, None)
        await accounting._add_new_category('еда', uid)
        await accounting._add_new_category('кафе', uid)
    own = {c.name: c.id for c in await accounting._show_all_categories(OWNER)}
    other = {c.name: c.id for c in await accounting._show_all_categories(OTHER)}
    for i in range(7):
        ids[i] = await accounting._add_operation(OWNER, own['еда' if i % 2 else 'кафе'], Decimal(i + 1),
                                                 accounting.OperationType.expenditure, comment=f'#{i}')
    foreign = await accounting._add_expenditure(OTHER, other['еда'], Decimal('999'))
    return ids, own, other, foreign


async def test_history_paging_filters_and_isolation(db):
    from history import queries
    ids, own, other, foreign = await _two_users_with_operations()

    page, total = await queries.list_operations(OWNER, offset=0, limit=5)
    assert total == 7 and len(page) == 5
    assert [row.comment for row in page] == ['#6', '#5', '#4', '#3', '#2']  # newest first
    page2, _ = await queries.list_operations(OWNER, offset=5, limit=5)
    assert [row.comment for row in page2] == ['#1', '#0']

    food, food_total = await queries.list_operations(OWNER, 0, 10, category_id=own['еда'])
    assert food_total == 3 and {row.category for row in food} == {'еда'}
    # Filtering by someone else's category id yields nothing, not their rows.
    assert await queries.list_operations(OWNER, 0, 10, category_id=other['еда']) == ([], 0)

    assert [c.name for c in await queries.used_categories(OWNER)] == ['еда', 'кафе']
    assert (await queries.last_operation(OWNER)).comment == '#6'


async def test_history_edit_and_delete_are_scoped_to_user(db):
    from datetime import datetime
    from history import queries
    ids, own, other, foreign = await _two_users_with_operations()

    assert await queries.get_operation(OWNER, foreign) is None
    assert await queries.update_operation(OWNER, foreign, quantity=Decimal('1')) is False
    assert await queries.delete_operation(OWNER, foreign) is False
    assert (await queries.get_operation(OTHER, foreign)).quantity == Decimal('999.00')

    when = datetime(2026, 1, 2, 3, 4)
    assert await queries.update_operation(OWNER, ids[0], quantity=Decimal('12.34'), comment=None,
                                          created_at=when, category_id=own['еда']) is True
    row = await queries.get_operation(OWNER, ids[0])
    assert (row.quantity, row.comment, row.created_at, row.category) == (Decimal('12.34'), None, when, 'еда')

    assert await queries.delete_operation(OWNER, ids[0]) is True
    assert await queries.delete_operation(OWNER, ids[0]) is False
    assert (await queries.list_operations(OWNER, 0, 10))[1] == 6
    assert await accounting._get_balance(OWNER) == -sum(Decimal(i + 1) for i in range(1, 7))


async def test_update_rejects_non_editable_fields(db):
    from history import queries
    ids, *_ = await _two_users_with_operations()

    with pytest.raises(ValueError):
        await queries.update_operation(OWNER, ids[0], operation_type=accounting.OperationType.income)


async def _budget_setup():
    from budgets import service as budgets
    await user.ensure_user(OWNER, None)
    await accounting._add_new_category('кафе', OWNER)
    await accounting._add_new_category('зарплата', OWNER)
    cats = {c.name: c.id for c in await accounting._show_all_categories(OWNER)}
    await budgets.set_budget(OWNER, cats['кафе'], Decimal('1000.00'))
    await budgets.set_budget(OWNER, None, Decimal('5000.00'))
    return budgets, cats


async def _spend(category_id, amount, when=None):
    from history import queries
    op_id = await accounting._add_expenditure(OWNER, category_id, Decimal(amount))
    if when is not None:
        await queries.update_operation(OWNER, op_id, created_at=when)
    return op_id


async def test_budget_upsert_and_single_overall_limit(db):
    budgets, cats = await _budget_setup()
    await budgets.set_budget(OWNER, None, Decimal('6000.00'))  # replaces, does not duplicate
    await budgets.set_budget(OWNER, cats['кафе'], Decimal('1200.00'))

    statuses = await budgets.budget_statuses(OWNER)
    assert [(s.category_id, s.limit) for s in statuses] == [(None, Decimal('6000.00')), (cats['кафе'], Decimal('1200.00'))]

    assert await budgets.delete_budget(OWNER, None) is True
    assert await budgets.delete_budget(OWNER, None) is False
    assert [s.category_id for s in await budgets.budget_statuses(OWNER)] == [cats['кафе']]


async def test_spending_counts_only_this_months_expenses(db):
    from datetime import datetime, timedelta
    budgets, cats = await _budget_setup()
    _, since, _ = budgets.month_bounds()
    await _spend(cats['кафе'], '300')
    await _spend(cats['кафе'], '999', when=since - timedelta(minutes=1))  # last month
    await accounting._add_income(OWNER, cats['зарплата'], Decimal('70000'))   # income is not spending

    by_category = {s.category_id: s.spent for s in await budgets.budget_statuses(OWNER)}
    assert by_category == {None: Decimal('300.00'), cats['кафе']: Decimal('300.00')}
    assert datetime(2000, 1, 1) < since


async def test_alerts_once_per_threshold_and_month(db):
    from datetime import timedelta
    budgets, cats = await _budget_setup()

    await _spend(cats['кафе'], '790')
    assert await budgets.new_alerts(OWNER) == []                      # 79%

    await _spend(cats['кафе'], '10')
    alerts = await budgets.new_alerts(OWNER)
    assert [(s.category_id, t) for s, t in alerts] == [(cats['кафе'], 80)]
    assert await budgets.new_alerts(OWNER) == []                      # no repeat

    await _spend(cats['кафе'], '250')
    assert [(s.category_id, t) for s, t in await budgets.new_alerts(OWNER)] == [(cats['кафе'], 100)]
    assert await budgets.new_alerts(OWNER) == []

    # Next month starts clean.
    _, _, until = budgets.month_bounds()
    from timeutils import to_local
    next_month = to_local(until + timedelta(hours=1))
    assert await budgets.new_alerts(OWNER, next_month) == []
    await _spend(cats['кафе'], '1000', when=until + timedelta(hours=1))
    assert [(s.category_id, t) for s, t in await budgets.new_alerts(OWNER, next_month)] == [(cats['кафе'], 100)]


async def test_jump_past_100_sends_one_alert(db):
    budgets, cats = await _budget_setup()
    await _spend(cats['кафе'], '1500')

    alerts = await budgets.new_alerts(OWNER)

    assert [(s.category_id, t) for s, t in alerts] == [(cats['кафе'], 100)]
    period, _, _ = budgets.month_bounds()
    assert await budgets.record_alert(alerts[0][0].budget_id, period, 80) is False  # 80 recorded too


async def test_deleting_category_removes_its_budget(db):
    from sqlalchemy import delete
    from accounting.models import Categories
    from middleware.service import _execute_insert_update_delete_command
    budgets, cats = await _budget_setup()
    await _execute_insert_update_delete_command(delete(Categories).where(Categories.id == cats['зарплата']))
    await budgets.set_budget(OWNER, cats['кафе'], Decimal('10'))

    assert len(await budgets.budget_statuses(OWNER)) == 2


async def test_check_and_notify_sends_once_in_user_language(db):
    from budgets.tasks import check_and_notify
    budgets, cats = await _budget_setup()
    await user._language_locale(OWNER, 'en')
    await _spend(cats['кафе'], '850')
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append((chat_id, text))

    assert await check_and_notify(OWNER, bot=FakeBot()) == 1
    assert await check_and_notify(OWNER, bot=FakeBot()) == 0
    assert sent == [(OWNER, '🟡 Кафе: spent 850.00 of 1000.00 (85%). 150.00 left.')]


async def test_analytics_queries(db):
    from datetime import datetime
    from analytics import service as analytics
    from history import queries
    from timeutils import month_range
    for uid in (OWNER, OTHER):
        await user.ensure_user(uid, None)
        for name in ('кафе', 'зарплата', 'техника'):
            await accounting._add_new_category(name, uid)
    cats = {c.name: c.id for c in await accounting._show_all_categories(OWNER)}
    other = {c.name: c.id for c in await accounting._show_all_categories(OTHER)}

    async def op(kind, category, amount, when, comment=None):
        op_id = await accounting._add_operation(OWNER, cats[category], Decimal(amount), kind, comment=comment)
        await queries.update_operation(OWNER, op_id, created_at=when)

    EXP, INC = accounting.OperationType.expenditure, accounting.OperationType.income
    await op(INC, 'зарплата', '100000', datetime(2026, 10, 1, 6, 0))
    await op(EXP, 'кафе', '300', datetime(2026, 10, 2, 6, 0))
    await op(EXP, 'техника', '50000', datetime(2026, 10, 3, 6, 0), comment='ноутбук')
    # 20:00 UTC on 30 Sep is already 01:00 on 1 Oct in Tashkent: October.
    await op(EXP, 'кафе', '200', datetime(2026, 9, 30, 20, 0))
    # 18:00 UTC on 30 Sep is 23:00 on 30 Sep in Tashkent: September.
    await op(EXP, 'кафе', '700', datetime(2026, 9, 30, 18, 0))
    foreign = await accounting._add_expenditure(OTHER, other['кафе'], Decimal('999999'))
    await queries.update_operation(OTHER, foreign, created_at=datetime(2026, 10, 5))

    since, until = month_range(2026, 10)
    assert await analytics.totals(OWNER, since, until) == (Decimal('100000.00'), Decimal('50500.00'))
    assert sorted(await analytics.expenses_by_category(OWNER, since, until)) == [
        ('кафе', Decimal('500.00')), ('техника', Decimal('50000.00'))]
    largest = await analytics.largest_expenses(OWNER, since, until, limit=2)
    assert [(r.quantity, r.comment, r.category) for r in largest] == [
        (Decimal('50000.00'), 'ноутбук', 'техника'), (Decimal('300.00'), None, 'кафе')]

    first, _ = month_range(2026, 9)
    by_month = await analytics.monthly_totals(OWNER, first, until)
    assert by_month == {(2026, 9): (Decimal('0'), Decimal('700.00')), (2026, 10): (Decimal('100000.00'), Decimal('50500.00'))}


async def test_report_rows_between(db):
    from datetime import datetime
    from history import queries
    await user.ensure_user(OWNER, None)
    await accounting._add_new_category('кафе', OWNER)
    category = (await accounting._show_all_categories(OWNER))[0].id
    for day in (1, 15, 30):
        op_id = await accounting._add_expenditure(OWNER, category, Decimal(day))
        await queries.update_operation(OWNER, op_id, created_at=datetime(2026, 9, day, 12, 0))

    rows = await accounting._get_accounts_between(OWNER, datetime(2026, 9, 10), datetime(2026, 9, 30))
    assert [r.quantity for r in rows] == [Decimal('15.00')]
    assert len(await accounting._get_accounts_between(OWNER, None, None)) == 3
    assert len(await accounting._get_accounts_between(OTHER, None, None)) == 0


async def _recurring_setup(next_date, remind_days=0, frequency='daily'):
    from recurring import service as recurring
    await user.ensure_user(OWNER, None)
    await accounting._add_new_category('интернет', OWNER)
    category = (await accounting._show_all_categories(OWNER))[0].id
    template_id = await recurring.create(OWNER, category, accounting.OperationType.expenditure, Decimal('5000.00'), 'тариф',
                                         frequency, next_date.day, next_date, remind_days)
    return recurring, template_id


async def test_recurring_run_creates_due_operations_once(db):
    from datetime import date, datetime
    recurring, template_id = await _recurring_setup(date(2026, 10, 20))

    created = await recurring.run_due(date(2026, 10, 22))
    again = await recurring.run_due(date(2026, 10, 22))

    assert [c.day for c in created] == [date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)]
    assert again == []
    rows = await accounting._get_accounts_between(OWNER, None, None)
    assert len(rows) == 3 and {r.comment for r in rows} == {'тариф'}
    assert rows[0].created_at == datetime(2026, 10, 20, 7, 0)  # local noon in Tashkent
    assert (await recurring.get(OWNER, template_id)).next_date == date(2026, 10, 23)
    assert await accounting._get_balance(OWNER) == Decimal('-15000.00')


async def test_recurring_reminder_once_and_skip(db):
    from datetime import date
    recurring, template_id = await _recurring_setup(date(2026, 11, 5), remind_days=3, frequency='monthly')

    assert await recurring.reminders_due(date(2026, 11, 1)) == []
    assert [r.id for r in await recurring.reminders_due(date(2026, 11, 2))] == [template_id]
    assert await recurring.reminders_due(date(2026, 11, 3)) == []

    assert await recurring.skip_next(OWNER, template_id) == date(2026, 12, 5)
    assert await recurring.skip_next(OTHER, template_id) is None
    assert await recurring.run_due(date(2026, 11, 5)) == []          # skipped occurrence is not created
    assert [r.id for r in await recurring.reminders_due(date(2026, 12, 3))] == [template_id]


async def test_recurring_edit_and_remove_scoped(db):
    from datetime import date
    recurring, template_id = await _recurring_setup(date(2026, 11, 5))

    assert await recurring.update_fields(OTHER, template_id, amount=Decimal('1')) is False
    assert await recurring.update_fields(OWNER, template_id, amount=Decimal('6000'), next_date=date(2026, 11, 7)) is True
    row = await recurring.get(OWNER, template_id)
    assert (row.amount, row.next_date, row.category) == (Decimal('6000.00'), date(2026, 11, 7), 'интернет')
    with pytest.raises(ValueError):
        await recurring.update_fields(OWNER, template_id, frequency='daily')
    assert await recurring.remove(OTHER, template_id) is False
    assert await recurring.remove(OWNER, template_id) is True


async def test_recurring_worker_sends_messages(db):
    from datetime import date
    from recurring import tasks
    recurring, template_id = await _recurring_setup(date(2026, 10, 22))
    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text, reply_markup=None):
            sent.append((chat_id, text, reply_markup))

    assert await tasks.run(bot=FakeBot(), today=date(2026, 10, 22)) == 1
    chat_id, text, markup = sent[0]
    assert chat_id == OWNER and 'Добавлено по расписанию (22.10.2026)' in text and '5000.00 интернет' in text
    assert markup.inline_keyboard[0][0].callback_data.startswith('h:undo:')
    assert await tasks.run(bot=FakeBot(), today=date(2026, 10, 22)) == 0
