"""Runs the Alembic migrations against a real PostgreSQL server.

Set TEST_PG_URL to a server where the user may create databases, e.g.
postgresql://postgres:postgres@127.0.0.1:5432/postgres. Skipped otherwise.
"""
import os
import subprocess
import sys
import uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import pytest

psycopg2 = pytest.importorskip('psycopg2')

TEST_PG_URL = os.environ.get('TEST_PG_URL')
pytestmark = pytest.mark.skipif(not TEST_PG_URL, reason='TEST_PG_URL is not set')

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / 'alembic'
LEGACY_HEAD = '8ca044247220'
NEW_HEAD = 'b7e2c4d91a05'  # telegram id / numeric / categories


def _head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    config = Config(str(ALEMBIC_DIR / 'alembic.ini'))
    config.set_main_option('script_location', str(ALEMBIC_DIR / 'migrations'))  # relative in the ini
    return ScriptDirectory.from_config(config).get_current_head()


HEAD = _head()  # latest revision, whatever stage added it
OWNER_ID = 777000111


@pytest.fixture
def database():
    url = urlparse(TEST_PG_URL)
    name = f'migration_test_{uuid.uuid4().hex[:8]}'
    admin = psycopg2.connect(TEST_PG_URL)
    admin.autocommit = True
    admin.cursor().execute(f'CREATE DATABASE {name}')

    env = dict(os.environ, HOST=url.hostname, PORT=str(url.port or 5432), POSTGRES_USER=url.username or 'postgres',
               POSTGRES_PASSWORD=url.password or 'unused', POSTGRES_DB=name, OWNER_ID=str(OWNER_ID))
    env.pop('OWNER_USERNAME', None)
    dsn = f"host={url.hostname} port={url.port or 5432} user={env['POSTGRES_USER']} password={env['POSTGRES_PASSWORD']} dbname={name}"
    try:
        yield dsn, env
    finally:
        admin.cursor().execute(f'DROP DATABASE IF EXISTS {name} WITH (FORCE)')
        admin.close()


def alembic(env, *args, check=True):
    return subprocess.run([sys.executable, '-m', 'alembic', *args], cwd=ALEMBIC_DIR, env=env,
                          capture_output=True, text=True, check=check)


def query(dsn, sql, params=None):
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall() if cur.description else None


def seed_legacy(dsn):
    """Data in the shape the old bot wrote it."""
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('''INSERT INTO "user" (username, registered_at, balance, email, locale) VALUES
                       ('owner', '2024-01-01', 1749.9, 'me@example.com', 'en'),
                       ('someone', '2024-02-01', -10, NULL, NULL)''')
        # "еда" was created by owner but also used by someone: categories were global.
        cur.execute('''INSERT INTO categories (name, user_id, is_active) VALUES
                       ('еда', 'owner', true), ('зарплата', 'owner', true), ('старое', 'owner', false)''')
        rows = [
            ('owner', 2000.0, 'зарплата', 'income'),
            ('owner', 250.1, 'еда', 'expenditure'),
            ('owner', 0.1 + 0.2, 'старое', 'expenditure'),  # float noise: 0.30000000000000004
            ('someone', 10.0, 'еда', 'expenditure'),
        ]
        for user, quantity, category, op in rows:
            cur.execute('INSERT INTO accounting (id, user_id, created_at, quantity, categories, operation_type) '
                        'VALUES (%s, %s, now(), %s, %s, %s)', (str(uuid.uuid4()), user, quantity, category, op))


def test_upgrade_moves_data_and_downgrade_restores_it(database):
    dsn, env = database
    alembic(env, 'upgrade', LEGACY_HEAD)
    seed_legacy(dsn)
    env = dict(env, OWNER_USERNAME='owner')

    alembic(env, 'upgrade', NEW_HEAD)

    assert query(dsn, 'SELECT version_num FROM alembic_version') == [(NEW_HEAD,)]
    users = dict(query(dsn, 'SELECT username, id FROM "user"'))
    assert users == {'owner': OWNER_ID, 'someone': -1}
    assert query(dsn, 'SELECT email, locale FROM "user" WHERE id = %s', (OWNER_ID,)) == [('me@example.com', 'en')]

    categories = set(query(dsn, 'SELECT user_id, name, is_active FROM categories'))
    assert categories == {
        (OWNER_ID, 'еда', True), (OWNER_ID, 'зарплата', True), (OWNER_ID, 'старое', False),
        (-1, 'еда', True),  # someone's operations get their own copy of the shared name
    }

    operations = query(dsn, '''SELECT a.user_id, c.name, a.quantity, a.operation_type::text
                               FROM accounting a JOIN categories c ON c.id = a.category_id
                               ORDER BY a.user_id, c.name''')
    assert operations == [
        (-1, 'еда', Decimal('10.00'), 'expenditure'),
        (OWNER_ID, 'еда', Decimal('250.10'), 'expenditure'),
        (OWNER_ID, 'зарплата', Decimal('2000.00'), 'income'),
        (OWNER_ID, 'старое', Decimal('0.30'), 'expenditure'),
    ]
    # Every operation belongs to a category of the same user.
    assert query(dsn, 'SELECT count(*) FROM accounting a JOIN categories c ON c.id = a.category_id '
                      'WHERE c.user_id <> a.user_id') == [(0,)]

    alembic(env, 'downgrade', LEGACY_HEAD)

    assert query(dsn, 'SELECT version_num FROM alembic_version') == [(LEGACY_HEAD,)]
    assert set(query(dsn, 'SELECT username, email FROM "user"')) == {('owner', 'me@example.com'), ('someone', None)}
    balances = dict(query(dsn, 'SELECT username, balance FROM "user"'))
    assert balances == {'owner': pytest.approx(1749.6), 'someone': pytest.approx(-10)}
    assert set(query(dsn, 'SELECT name, user_id, is_active FROM categories')) == {
        ('еда', 'owner', True), ('зарплата', 'owner', True), ('старое', 'owner', False)}
    assert sorted(query(dsn, 'SELECT user_id, categories, quantity FROM accounting')) == [
        ('owner', 'еда', 250.1), ('owner', 'зарплата', 2000.0), ('owner', 'старое', 0.3), ('someone', 'еда', 10.0)]

    # And forward again: the round trip is repeatable.
    alembic(env, 'upgrade', NEW_HEAD)
    assert query(dsn, 'SELECT count(*) FROM accounting') == [(4,)]


def test_fresh_database_upgrades_to_head(database):
    dsn, env = database

    alembic(env, 'upgrade', 'head')

    assert query(dsn, 'SELECT version_num FROM alembic_version') == [(HEAD,)]
    assert query(dsn, 'SELECT count(*) FROM "user"') == [(0,)]


def test_several_users_without_owner_username_aborts_without_changes(database):
    dsn, env = database
    alembic(env, 'upgrade', LEGACY_HEAD)
    seed_legacy(dsn)

    result = alembic(env, 'upgrade', NEW_HEAD, check=False)

    assert result.returncode != 0
    assert 'OWNER_USERNAME' in result.stderr
    assert query(dsn, 'SELECT version_num FROM alembic_version') == [(LEGACY_HEAD,)]
    assert query(dsn, 'SELECT count(*) FROM accounting') == [(4,)]


def test_owner_username_picks_the_owner(database):
    dsn, env = database
    alembic(env, 'upgrade', LEGACY_HEAD)
    seed_legacy(dsn)

    alembic(dict(env, OWNER_USERNAME='@Someone'), 'upgrade', NEW_HEAD)

    assert dict(query(dsn, 'SELECT username, id FROM "user"')) == {'someone': OWNER_ID, 'owner': -1}


def test_comment_column_round_trip(database):
    dsn, env = database
    alembic(dict(env, OWNER_USERNAME='owner'), 'upgrade', LEGACY_HEAD)
    seed_legacy(dsn)
    alembic(dict(env, OWNER_USERNAME='owner'), 'upgrade', 'head')
    query(dsn, "UPDATE accounting SET comment = 'обед' WHERE quantity = 250.10")

    assert query(dsn, 'SELECT count(*) FROM accounting WHERE comment IS NOT NULL') == [(1,)]

    alembic(env, 'downgrade', NEW_HEAD)

    assert query(dsn, 'SELECT count(*) FROM accounting') == [(4,)]
    columns = [row[0] for row in query(dsn, "SELECT column_name FROM information_schema.columns WHERE table_name = 'accounting'")]
    assert 'comment' not in columns
