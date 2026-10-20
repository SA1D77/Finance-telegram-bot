from datetime import date, datetime
from decimal import Decimal

import pytest

from goals import handlers as g
from goals.handlers import GoalState, goal_text, parse_deadline
from goals.service import Goal, monthly_needed, months_left
from i18n import i18n
from timeutils import LOCAL_TZ

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal
TODAY = date(2026, 10, 22)


def goal(saved='0', target='100000.00', deadline=None):
    return Goal(id=1, name='Отпуск', target=D(target), deadline=deadline, saved=D(saved))


def test_goal_numbers():
    assert goal('45000').percent == 45 and goal('45000').left == D('55000.00')
    assert goal('120000').reached and goal('120000').left == D('0.00')


@pytest.mark.parametrize('deadline, months', [
    (date(2026, 12, 31), 3), (date(2026, 10, 31), 1), (date(2026, 11, 1), 1), (date(2026, 11, 22), 2),
    (date(2027, 10, 21), 12), (date(2026, 10, 21), None),
])
def test_months_left(deadline, months):
    assert months_left(deadline, TODAY) == months


def test_monthly_needed():
    assert monthly_needed(goal('10000', deadline=date(2026, 12, 31)), TODAY) == D('30000.00')
    assert monthly_needed(goal('0', target='100.00', deadline=date(2026, 12, 31)), TODAY) == D('33.34')  # rounded up
    assert monthly_needed(goal('0'), TODAY) is None                             # no deadline
    assert monthly_needed(goal('100000', deadline=date(2026, 12, 31)), TODAY) is None  # reached
    assert monthly_needed(goal('0', deadline=date(2026, 1, 1)), TODAY) is None  # passed


def test_goal_text():
    with i18n.context(), i18n.use_locale('ru'):
        text = goal_text(goal('45000', deadline=date(2026, 12, 31)), TODAY)
        passed = goal_text(goal('1', deadline=date(2026, 1, 1)), TODAY)
        reached = goal_text(goal('100000'), TODAY)
    assert '▓▓▓▓░░░░░░ 45%' in text and '45\xa0000 из 100\xa0000, осталось 55\xa0000' in text
    assert 'до 31.12.2026 · откладывать 18\xa0333.34 в месяц' in text
    assert 'срок прошёл' in passed
    assert '🎉 Цель достигнута!' in reached


@pytest.mark.parametrize('text, expected', [('31.12.2026', date(2026, 12, 31)), ('31.12', date(2026, 12, 31)),
                                            ('01.03', date(2027, 3, 1)), ('22.10.2026', None), ('abc', None), ('', None)])
def test_parse_deadline(text, expected, monkeypatch):
    monkeypatch.setattr(g, 'local_now', lambda: datetime(2026, 10, 22, 10, 0, tzinfo=LOCAL_TZ))
    assert parse_deadline(text, TODAY) == expected


class FakeGoals:
    def __init__(self):
        self.goals = {}
        self.contributions = []

    async def create(self, user_id, name, target, deadline):
        goal_id = len(self.goals) + 1
        self.goals[goal_id] = Goal(id=goal_id, name=name, target=target, deadline=deadline, saved=D('0.00'))
        return goal_id

    async def list_for(self, user_id):
        return list(self.goals.values())

    async def get(self, user_id, goal_id):
        return self.goals.get(goal_id) if user_id == USER_ID else None

    async def contribute(self, user_id, goal_id, amount):
        self.goals[goal_id].saved += amount
        self.contributions.append(amount)
        return True

    async def update_fields(self, user_id, goal_id, **values):
        for key, value in values.items():
            setattr(self.goals[goal_id], key, value)
        return True

    async def remove(self, user_id, goal_id):
        return self.goals.pop(goal_id, None) is not None


@pytest.fixture
def fake(monkeypatch):
    f = FakeGoals()
    for name in ('create', 'list_for', 'get', 'contribute', 'update_fields', 'remove'):
        monkeypatch.setattr(g.service, name, getattr(f, name))
    monkeypatch.setattr(g, 'local_now', lambda: datetime(2026, 10, 22, 10, 0, tzinfo=LOCAL_TZ))
    return f


def last(harness):
    return harness.session.requests[-1]


async def test_create_goal_and_top_up(harness, fake):
    await harness.feed(make_message('⭐ Цели'))
    assert 'Целей пока нет' in last(harness).text

    await harness.feed(make_callback('g:new'))
    await harness.feed(make_message('Отпуск в горах'))
    await harness.feed(make_message('100000'))
    await harness.feed(make_message('вчера'))
    assert await harness.state() == GoalState.deadline.state
    await harness.feed(make_message('31.12.2026'))

    created = fake.goals[1]
    assert (created.name, created.target, created.deadline) == ('Отпуск в горах', D('100000.00'), date(2026, 12, 31))
    assert 'откладывать 33\xa0333.34 в месяц' in last(harness).text

    await harness.feed(make_callback('g:add:1'))
    await harness.feed(make_message('60000+40000'))
    assert fake.contributions == [D('100000.00')]
    assert last(harness).text.startswith('🎉 Цель достигнута!')


async def test_goal_without_deadline(harness, fake):
    await harness.feed(make_callback('g:new'))
    await harness.feed(make_message('Подушка'))
    await harness.feed(make_message('50000'))
    await harness.feed(make_message('-'))

    assert fake.goals[1].deadline is None


async def test_withdraw_limited_to_saved(harness, fake):
    goal_id = await fake.create(USER_ID, 'Ноутбук', D('80000'), None)
    await fake.contribute(USER_ID, goal_id, D('1000'))

    await harness.feed(make_callback(f'g:take:{goal_id}'))
    await harness.feed(make_message('5000'))
    assert await harness.state() == GoalState.withdraw.state
    await harness.feed(make_message('400'))

    assert fake.goals[goal_id].saved == D('600')


async def test_edit_target_deadline_and_delete(harness, fake):
    goal_id = await fake.create(USER_ID, 'Ноутбук', D('80000'), None)
    await harness.feed(make_callback(f'g:target:{goal_id}'))
    await harness.feed(make_message('90000'))
    await harness.feed(make_callback(f'g:deadline:{goal_id}'))
    await harness.feed(make_message('01.03'))
    assert (fake.goals[goal_id].target, fake.goals[goal_id].deadline) == (D('90000.00'), date(2027, 3, 1))

    await harness.feed(make_callback(f'g:del:{goal_id}'))
    assert goal_id in fake.goals
    await harness.feed(make_callback(f'g:delok:{goal_id}'))
    assert fake.goals == {}


@pytest.mark.parametrize('data', ['g:card:9', 'g:add:x', 'g:delok:'])
async def test_unknown_goal(harness, fake, data):
    await harness.feed(make_callback(data))

    assert last(harness).show_alert is True


async def test_bad_name(harness, fake):
    await harness.feed(make_callback('g:new'))
    await harness.feed(make_message('я' * 65))

    assert await harness.state() == GoalState.name.state


async def test_stranger_ignored(harness, fake):
    goal_id = await fake.create(USER_ID, 'Ноутбук', D('80000'), None)
    await harness.feed(make_callback(f'g:delok:{goal_id}', user=make_user(user_id=2002)))

    assert goal_id in fake.goals and harness.session.requests == []
