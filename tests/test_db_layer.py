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
