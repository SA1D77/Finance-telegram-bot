import json
import os
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, InlineQuery, Message, Update

import config
import i18n
import main
from accounting.models import OperationType
from celery_app import celery
from middleware import accounting, general_handlers
from report import files as report_files
from report.email_send import is_valid_email
from telegram import main_handlers, report_handlers
from telegram.states import IncomeState, ReportState

from conftest import make_callback, make_message, make_user

STRANGER_ID = 2002


def stranger(username='stranger'):
    return make_user(username=username, user_id=STRANGER_ID)


@pytest.fixture
def redis_forbidden(monkeypatch):
    """Fails the test if anything tries to read the language from Redis."""
    async def boom(user_id):
        raise AssertionError('Redis must not be touched for a foreign update')

    monkeypatch.setattr(i18n, '_get_user_lang', boom)


# --- Owner-only middleware -------------------------------------------------

async def test_owner_message_is_handled(harness):
    await harness.feed(make_message('➕ Доход'))

    assert await harness.state() == IncomeState.income_input.state
    assert harness.session.requests


async def test_stranger_message_is_silently_ignored(harness, redis_forbidden):
    await harness.feed(make_message('➕ Доход', user=stranger()))
    await harness.feed(make_message('/start', user=stranger()))

    assert harness.session.requests == []


async def test_stranger_with_owner_username_is_ignored(harness, redis_forbidden):
    # The check is by numeric id, so copying the owner's username gives nothing.
    await harness.feed(make_message('📊 Баланс', user=stranger(username='owner')))

    assert harness.session.requests == []


async def test_stranger_callback_is_ignored(harness, redis_forbidden, monkeypatch):
    async def must_not_save(**kwargs):
        raise AssertionError('foreign callback reached the database')

    monkeypatch.setattr(main_handlers, '_add_income', must_not_save)
    await harness.feed(make_callback('cancel', user=stranger()))
    await harness.feed(make_callback('category_1', user=stranger()))

    assert harness.session.requests == []


async def test_stranger_inline_query_is_ignored(harness, redis_forbidden):
    update = Update(update_id=3, inline_query=InlineQuery(id='q', from_user=stranger(), query='x', offset=''))

    await harness.feed(update)

    assert harness.session.requests == []


async def test_update_without_user_is_ignored(harness, redis_forbidden):
    update = Update(update_id=4, channel_post=Message(
        message_id=1, date=datetime.now(), chat=Chat(id=-100, type='channel'), text='➕ Доход'))

    await harness.feed(update)

    assert harness.session.requests == []


@pytest.mark.parametrize('raw, expected', [
    ('123456', 123456),
    (' 42 ', 42),
    (None, None),
    ('', None),
    ('abc', None),
    ('-5', None),
    ('12.5', None),
])
def test_parse_owner_id(raw, expected):
    assert config._parse_owner_id(raw) == expected


async def test_bot_refuses_to_start_without_owner(monkeypatch):
    monkeypatch.setattr(main, 'OWNER_ID', None)

    with pytest.raises(SystemExit):
        await main.main()


# --- Data isolation --------------------------------------------------------

@pytest.mark.parametrize('time', ['last', '30'])
async def test_operations_are_filtered_by_user(monkeypatch, time):
    captured = {}

    async def capture(statemant):
        captured['sql'] = str(statemant.compile(compile_kwargs={'literal_binds': True}))
        return []

    monkeypatch.setattr(accounting, '_execute_select_rows', capture)

    await accounting._get_accounts(user_id=1001, time=time)

    assert 'accounting.user_id = 1001' in captured['sql']


@pytest.mark.parametrize('callback', ['category_99', 'category_', 'category_Еда', 'category_-1'])
async def test_unknown_category_callback_is_rejected(harness, monkeypatch, callback):
    async def only_own(category_id, user_id):
        return SimpleNamespace(id=1, name='еда') if (category_id, user_id) == (1, 1001) else None

    async def must_not_save(**kwargs):
        raise AssertionError('unknown category was saved')

    monkeypatch.setattr(general_handlers, '_get_category', only_own)
    monkeypatch.setattr(main_handlers, '_add_income', must_not_save)
    await harness.set_state(IncomeState.category_input)
    await harness.storage.set_data(harness.key, {'total': '10.00'})

    await harness.feed(make_callback(callback))

    assert await harness.state() is None
    assert 'Мне кажется или такой категории нет?' in harness.session.texts()


# --- Report files ----------------------------------------------------------

def fake_accounts():
    return [
        SimpleNamespace(categories='еда', created_at=datetime(2026, 10, 1, 12, 0), quantity=Decimal('250.10'),
                        operation_type=OperationType.expenditure),
        SimpleNamespace(categories='зарплата', created_at=datetime(2026, 10, 2, 9, 0), quantity=Decimal('1000.00'),
                        operation_type=OperationType.income),
    ]


@pytest.fixture
def generated_dir(tmp_path, monkeypatch):
    directory = tmp_path / 'generated'
    monkeypatch.setattr(report_files, 'GENERATED_DIR', str(directory))
    return directory


def test_report_files_get_unique_names_and_are_removed(generated_dir):
    rows = report_files.serialize_accounts(fake_accounts())

    first = report_files.build_report_files(rows, 'ru')
    second = report_files.build_report_files(rows, 'ru')

    assert first.directory != second.directory
    for path in (first.table, first.general_diagram, first.expenditure_diagram, first.income_diagram):
        assert os.path.getsize(path) > 0

    report_files.remove_report_files(first)
    report_files.remove_report_files(second)
    assert list(generated_dir.iterdir()) == []


def test_report_with_single_category_and_empty_period(generated_dir):
    single = report_files.serialize_accounts(fake_accounts()[:1])

    for rows in (single, []):
        files = report_files.build_report_files(rows, 'en')
        report_files.remove_report_files(files)


async def test_report_to_chat_sends_files_then_deletes_them(harness, monkeypatch, generated_dir):
    async def accounts(user_id, time):
        assert (user_id, time) == (1001, '30')
        return fake_accounts()

    async def no_lang(user_id):
        return None

    monkeypatch.setattr(report_handlers, '_get_accounts', accounts)
    monkeypatch.setattr(report_handlers, '_get_user_lang', no_lang)

    await harness.feed(make_message('/report'))
    await harness.feed(make_callback('time_30'))
    assert await harness.state() == ReportState.send_place.state

    await harness.feed(make_message('📌 Чат'))

    assert len(harness.session.sent_files) == 4
    assert all(existed for _, existed in harness.session.sent_files)
    assert all(not os.path.exists(path) for path, _ in harness.session.sent_files)
    assert list(generated_dir.iterdir()) == []
    assert await harness.state() is None


async def test_unknown_report_period_is_ignored(harness):
    await harness.set_state(ReportState.time_interval)

    await harness.feed(make_callback('time_9999'))

    assert await harness.state() == ReportState.time_interval.state


# --- Email -----------------------------------------------------------------

@pytest.mark.parametrize('value, ok', [
    ('me@example.com', True),
    ('first.last@mail.example.org', True),
    ('not-an-email', False),
    ('a@b', False),
    ('two@@example.com', False),
    ('with space@example.com', False),
    ('x' * 250 + '@example.com', False),
    (None, False),
])
def test_email_validation(value, ok):
    assert is_valid_email(value) is ok


async def test_invalid_email_is_not_sent(harness, monkeypatch):
    sent = []
    monkeypatch.setattr(report_handlers.email_report, 'delay', lambda **kwargs: sent.append(kwargs))
    await harness.set_state(ReportState.email)

    await harness.feed(make_message('not-an-email'))

    assert sent == []
    assert await harness.state() == ReportState.email.state


async def test_valid_email_gets_json_safe_task_arguments(harness, monkeypatch):
    sent = []

    async def accounts(user_id, time):
        return fake_accounts()

    async def no_lang(user_id):
        return None

    monkeypatch.setattr(report_handlers, '_get_accounts', accounts)
    monkeypatch.setattr(report_handlers, '_get_user_lang', no_lang)
    monkeypatch.setattr(report_handlers.email_report, 'delay', lambda **kwargs: sent.append(kwargs))
    await harness.set_state(ReportState.email)
    await harness.storage.set_data(harness.key, {'time': '30'})

    await harness.feed(make_message(' me@example.com '))

    assert len(sent) == 1
    assert sent[0]['user_email'] == 'me@example.com'
    json.dumps(sent[0])  # would raise if ORM objects were still passed


# --- Celery / Redis --------------------------------------------------------

def test_celery_accepts_json_only():
    assert celery.conf.task_serializer == 'json'
    assert celery.conf.accept_content == ['json']


def test_broker_url_includes_quoted_redis_password(monkeypatch):
    monkeypatch.setattr(config, 'REDIS_PASSWORD', 'p@ss:word/1')
    monkeypatch.setattr(config, 'CELERY_HOST', 'redis')
    monkeypatch.setattr(config, 'CELERY_PORT', '6379/1')

    assert config.celery_broker_url() == 'redis://:p%40ss%3Aword%2F1@redis:6379/1'


def test_broker_url_without_password(monkeypatch):
    monkeypatch.setattr(config, 'REDIS_PASSWORD', None)
    monkeypatch.setattr(config, 'CELERY_HOST', 'redis')
    monkeypatch.setattr(config, 'CELERY_PORT', '6379/1')

    assert config.celery_broker_url() == 'redis://redis:6379/1'
