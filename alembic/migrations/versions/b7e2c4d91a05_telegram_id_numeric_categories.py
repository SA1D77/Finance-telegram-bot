"""telegram id, numeric money, per-user categories

Revision ID: b7e2c4d91a05
Revises: 8ca044247220
Create Date: 2026-10-20 21:30:00

Moves the schema from Telegram usernames to numeric Telegram ids, from
float to numeric(14, 2) and from one global category namespace to
categories owned by a user. Data is copied into new tables, the row counts
are checked and only then are the old tables dropped; everything runs in
one transaction, so a failed check leaves the database untouched.

Old rows are mapped to Telegram ids like this:
  * OWNER_ID (environment) is the owner's numeric id;
  * the owner's old row is the user whose username equals OWNER_USERNAME,
    or the only user when there is just one;
  * any other old users get negative placeholder ids, so their rows are
    kept (but nobody can reach them).

The user's stored balance is dropped: it is computed from the operations.
Downgrade restores the previous schema with the same data.
"""
import os

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7e2c4d91a05'
down_revision = '8ca044247220'
branch_labels = None
depends_on = None

NO_CATEGORY = 'без категории'


def _owner_mapping(conn) -> dict:
    usernames = [row[0] for row in conn.execute(sa.text('SELECT username FROM "user" ORDER BY registered_at, username'))]
    if not usernames:
        return {}

    raw_owner_id = (os.environ.get('OWNER_ID') or '').strip()
    if not raw_owner_id.isdigit():
        raise RuntimeError('Set OWNER_ID (your numeric Telegram id) in src/.env before running this migration.')
    owner_id = int(raw_owner_id)

    owner_username = (os.environ.get('OWNER_USERNAME') or '').strip().lstrip('@').lower()
    if owner_username:
        matches = [name for name in usernames if name.lower() == owner_username]
        if not matches:
            raise RuntimeError(f'OWNER_USERNAME={owner_username} not found among {len(usernames)} users in the database.')
        owner = matches[0]
    elif len(usernames) == 1:
        owner = usernames[0]
    else:
        raise RuntimeError(
            f'The database has {len(usernames)} users. Set OWNER_USERNAME (your Telegram username) '
            'in src/.env so the migration knows which data is yours.'
        )

    mapping = {owner: owner_id}
    placeholder = -1
    for name in usernames:
        if name not in mapping:
            mapping[name] = placeholder
            placeholder -= 1
    return mapping


def upgrade() -> None:
    conn = op.get_bind()

    null_users = conn.execute(sa.text('SELECT count(*) FROM accounting WHERE user_id IS NULL')).scalar()
    if null_users:
        raise RuntimeError(f'{null_users} operations have no user; fix them before migrating.')

    old_operations = conn.execute(sa.text('SELECT count(*) FROM accounting')).scalar()

    op.execute('CREATE TEMPORARY TABLE user_map (username varchar PRIMARY KEY, tg_id bigint NOT NULL UNIQUE) ON COMMIT DROP')
    mapping = _owner_mapping(conn)
    if mapping:
        conn.execute(sa.text('INSERT INTO user_map (username, tg_id) VALUES (:username, :tg_id)'),
                     [{'username': name, 'tg_id': tg_id} for name, tg_id in mapping.items()])

    op.execute('''
        CREATE TABLE user_new (
            id bigint NOT NULL,
            username varchar,
            registered_at timestamp,
            email varchar,
            locale varchar,
            CONSTRAINT user_pkey_new PRIMARY KEY (id),
            CONSTRAINT user_email_key_new UNIQUE (email)
        )
    ''')
    op.execute('''
        INSERT INTO user_new (id, username, registered_at, email, locale)
        SELECT m.tg_id, u.username, u.registered_at, u.email, u.locale
        FROM "user" u JOIN user_map m ON m.username = u.username
    ''')

    op.execute('''
        CREATE TABLE categories_new (
            id serial NOT NULL,
            user_id bigint NOT NULL,
            name varchar(64) NOT NULL,
            is_active boolean NOT NULL DEFAULT true,
            CONSTRAINT categories_pkey_new PRIMARY KEY (id),
            CONSTRAINT categories_user_id_fkey_new FOREIGN KEY (user_id) REFERENCES user_new (id),
            CONSTRAINT uq_categories_user_name_new UNIQUE (user_id, name)
        )
    ''')
    # Categories each user created...
    op.execute('''
        INSERT INTO categories_new (user_id, name, is_active)
        SELECT m.tg_id, left(c.name, 64), c.is_active
        FROM categories c JOIN user_map m ON m.username = c.user_id
        ORDER BY c.name
        ON CONFLICT (user_id, name) DO NOTHING
    ''')
    # ...plus names a user booked operations under but did not create (categories used to be shared).
    op.execute(f'''
        INSERT INTO categories_new (user_id, name, is_active)
        SELECT DISTINCT m.tg_id, left(coalesce(a.categories, '{NO_CATEGORY}'), 64), true
        FROM accounting a JOIN user_map m ON m.username = a.user_id
        ON CONFLICT (user_id, name) DO NOTHING
    ''')

    op.execute('''
        CREATE TABLE accounting_new (
            id uuid NOT NULL,
            user_id bigint NOT NULL,
            created_at timestamp,
            quantity numeric(14, 2) NOT NULL,
            category_id integer NOT NULL,
            operation_type operationtype NOT NULL,
            CONSTRAINT accounting_pkey_new PRIMARY KEY (id),
            CONSTRAINT accounting_user_id_fkey_new FOREIGN KEY (user_id) REFERENCES user_new (id),
            CONSTRAINT accounting_category_id_fkey_new FOREIGN KEY (category_id) REFERENCES categories_new (id)
        )
    ''')
    op.execute(f'''
        INSERT INTO accounting_new (id, user_id, created_at, quantity, category_id, operation_type)
        SELECT a.id, m.tg_id, a.created_at, round(a.quantity::numeric, 2), c.id, a.operation_type
        FROM accounting a
        JOIN user_map m ON m.username = a.user_id
        JOIN categories_new c ON c.user_id = m.tg_id AND c.name = left(coalesce(a.categories, '{NO_CATEGORY}'), 64)
    ''')

    new_operations = conn.execute(sa.text('SELECT count(*) FROM accounting_new')).scalar()
    if new_operations != old_operations:
        raise RuntimeError(f'Operation count mismatch after copy: {old_operations} -> {new_operations}. Nothing was changed.')

    op.execute('DROP TABLE accounting')
    op.execute('DROP TABLE categories')
    op.execute('DROP TABLE "user"')

    op.execute('ALTER TABLE user_new RENAME TO "user"')
    op.execute('ALTER TABLE "user" RENAME CONSTRAINT user_pkey_new TO user_pkey')
    op.execute('ALTER TABLE "user" RENAME CONSTRAINT user_email_key_new TO user_email_key')

    op.execute('ALTER TABLE categories_new RENAME TO categories')
    op.execute('ALTER SEQUENCE categories_new_id_seq RENAME TO categories_id_seq')
    op.execute('ALTER TABLE categories RENAME CONSTRAINT categories_pkey_new TO categories_pkey')
    op.execute('ALTER TABLE categories RENAME CONSTRAINT categories_user_id_fkey_new TO categories_user_id_fkey')
    op.execute('ALTER TABLE categories RENAME CONSTRAINT uq_categories_user_name_new TO uq_categories_user_name')

    op.execute('ALTER TABLE accounting_new RENAME TO accounting')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_pkey_new TO accounting_pkey')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_user_id_fkey_new TO accounting_user_id_fkey')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_category_id_fkey_new TO accounting_category_id_fkey')
    op.create_index('ix_accounting_user_id', 'accounting', ['user_id'])


def downgrade() -> None:
    conn = op.get_bind()
    old_operations = conn.execute(sa.text('SELECT count(*) FROM accounting')).scalar()

    # Old schema: username is the key, so users without one get a stable synthetic name.
    op.execute('''
        CREATE TEMPORARY TABLE user_map ON COMMIT DROP AS
        SELECT id AS tg_id, coalesce(username, 'tg' || id::text) AS username FROM "user"
    ''')

    op.execute('''
        CREATE TABLE user_old (
            username varchar NOT NULL,
            registered_at timestamp,
            balance double precision,
            email varchar,
            locale varchar,
            CONSTRAINT user_pkey_old PRIMARY KEY (username),
            CONSTRAINT user_email_key_old UNIQUE (email)
        )
    ''')
    op.execute('''
        INSERT INTO user_old (username, registered_at, balance, email, locale)
        SELECT m.username, u.registered_at,
               coalesce((SELECT sum(CASE WHEN a.operation_type = 'income' THEN a.quantity ELSE -a.quantity END)
                         FROM accounting a WHERE a.user_id = u.id), 0)::double precision,
               u.email, u.locale
        FROM "user" u JOIN user_map m ON m.tg_id = u.id
    ''')

    # Category names were global: the earliest category with a name owns it.
    op.execute('''
        CREATE TABLE categories_old (
            name varchar NOT NULL,
            user_id varchar,
            is_active boolean NOT NULL,
            CONSTRAINT categories_pkey_old PRIMARY KEY (name),
            CONSTRAINT categories_user_id_fkey_old FOREIGN KEY (user_id) REFERENCES user_old (username)
        )
    ''')
    op.execute('''
        INSERT INTO categories_old (name, user_id, is_active)
        SELECT DISTINCT ON (c.name) c.name, m.username, c.is_active
        FROM categories c JOIN user_map m ON m.tg_id = c.user_id
        ORDER BY c.name, c.id
    ''')

    op.execute('''
        CREATE TABLE accounting_old (
            id uuid NOT NULL,
            user_id varchar,
            created_at timestamp,
            quantity double precision NOT NULL,
            categories varchar,
            operation_type operationtype NOT NULL,
            CONSTRAINT accounting_pkey_old PRIMARY KEY (id),
            CONSTRAINT accounting_user_id_fkey_old FOREIGN KEY (user_id) REFERENCES user_old (username),
            CONSTRAINT accounting_categories_fkey_old FOREIGN KEY (categories) REFERENCES categories_old (name)
        )
    ''')
    op.execute('''
        INSERT INTO accounting_old (id, user_id, created_at, quantity, categories, operation_type)
        SELECT a.id, m.username, a.created_at, a.quantity::double precision, c.name, a.operation_type
        FROM accounting a
        JOIN user_map m ON m.tg_id = a.user_id
        JOIN categories c ON c.id = a.category_id
    ''')

    new_operations = conn.execute(sa.text('SELECT count(*) FROM accounting_old')).scalar()
    if new_operations != old_operations:
        raise RuntimeError(f'Operation count mismatch on downgrade: {old_operations} -> {new_operations}. Nothing was changed.')

    op.execute('DROP TABLE accounting')
    op.execute('DROP TABLE categories')
    op.execute('DROP TABLE "user"')

    op.execute('ALTER TABLE user_old RENAME TO "user"')
    op.execute('ALTER TABLE "user" RENAME CONSTRAINT user_pkey_old TO user_pkey')
    op.execute('ALTER TABLE "user" RENAME CONSTRAINT user_email_key_old TO user_email_key')
    op.execute('ALTER TABLE categories_old RENAME TO categories')
    op.execute('ALTER TABLE categories RENAME CONSTRAINT categories_pkey_old TO categories_pkey')
    op.execute('ALTER TABLE categories RENAME CONSTRAINT categories_user_id_fkey_old TO categories_user_id_fkey')
    op.execute('ALTER TABLE accounting_old RENAME TO accounting')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_pkey_old TO accounting_pkey')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_user_id_fkey_old TO accounting_user_id_fkey')
    op.execute('ALTER TABLE accounting RENAME CONSTRAINT accounting_categories_fkey_old TO accounting_categories_fkey')
