import csv
import gzip
import io
import os
import zipfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from accounting.models import OperationType
from backup import dump as backup_dump
from backup import tasks as backup_tasks
from export import handlers as e
from export.exporters import rows_for_export, to_csv, to_xlsx

from conftest import USER_ID, make_callback, make_message, make_user

D = Decimal


def accounts():
    return [
        SimpleNamespace(created_at=datetime(2026, 10, 1, 7, 0), operation_type=OperationType.income, categories='зарплата',
                        quantity=D('100000.00'), comment=None),
        SimpleNamespace(created_at=datetime(2026, 10, 2, 19, 30), operation_type=OperationType.expenditure, categories='кафе',
                        quantity=D('1250.50'), comment='ужин; "с друзьями"'),
    ]


def test_rows_use_local_time_and_language():
    rows = rows_for_export(accounts(), 'en')

    assert rows[0] == {'date': '2026-10-01 12:00', 'type': 'Income', 'category': 'зарплата', 'amount': D('100000.00'), 'comment': ''}
    assert rows[1]['date'] == '2026-10-03 00:30'  # 19:30 UTC is past midnight in Tashkent
    assert rows_for_export(accounts(), 'xx')[0]['type'] == 'Доход'


def test_csv_is_excel_friendly_and_escaped():
    content = to_csv(rows_for_export(accounts(), 'ru'), 'ru')

    assert content.startswith('﻿'.encode('utf-8'))
    parsed = list(csv.reader(io.StringIO(content.decode('utf-8-sig')), delimiter=';'))
    assert parsed[0] == ['Дата', 'Тип', 'Категория', 'Сумма', 'Комментарий']
    assert parsed[2] == ['2026-10-03 00:30', 'Расход', 'кафе', '1250.50', 'ужин; "с друзьями"']


def test_xlsx_is_a_valid_workbook():
    content = to_xlsx(rows_for_export(accounts(), 'ru'), 'ru')

    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        sheet = archive.read('xl/worksheets/sheet1.xml').decode()
        shared = archive.read('xl/sharedStrings.xml').decode()
    assert '<v>1250.5</v>' in sheet          # amounts are numbers, not text
    assert 'кафе' in shared and 'Категория' in shared


def test_empty_export():
    assert to_csv([], 'ru').decode('utf-8-sig').count('\r\n') == 1
    assert zipfile.is_zipfile(io.BytesIO(to_xlsx([], 'ru')))


@pytest.fixture
def export_db(monkeypatch):
    calls = []

    async def between(user_id, since, until):
        calls.append((user_id, since, until))
        return accounts()

    monkeypatch.setattr(e, '_get_accounts_between', between)
    return calls


def last(harness):
    return harness.session.requests[-1]


@pytest.mark.parametrize('fmt, suffix', [('csv', '.csv'), ('xlsx', '.xlsx')])
async def test_export_flow(harness, export_db, fmt, suffix):
    await harness.feed(make_message('/export'))
    assert [b.callback_data for row in last(harness).reply_markup.inline_keyboard for b in row] == [
        'e:p:today', 'e:p:week', 'e:p:month', 'e:p:year', 'e:p:all', 'e:p:custom']

    await harness.feed(make_callback('e:p:all'))
    await harness.feed(make_callback(f'e:f:{fmt}'))

    document = harness.session.requests[-2]
    assert type(document).__name__ == 'SendDocument'
    assert document.document.filename.endswith(suffix) and document.caption == 'Операций: 2'
    assert export_db == [(USER_ID, None, None)]


async def test_export_custom_range(harness, export_db):
    await harness.feed(make_callback('e:p:custom'))
    await harness.feed(make_message('сентябрь'))
    assert 'Не понял период' in last(harness).text
    await harness.feed(make_message('01.09.2026-30.09.2026'))
    await harness.feed(make_callback('e:f:csv'))

    assert export_db[0][1:] == (datetime(2026, 8, 31, 19, 0), datetime(2026, 9, 30, 19, 0))


async def test_export_stale_format_button(harness, export_db):
    await harness.feed(make_callback('e:f:csv'))

    assert last(harness).show_alert is True and export_db == []


async def test_backup_command_queues_task(harness, monkeypatch):
    queued = []

    async def queue(chat_id):
        queued.append(chat_id)
        return True

    monkeypatch.setattr(backup_tasks, 'queue_backup', queue)

    await harness.feed(make_message('/backup'))

    assert queued == [USER_ID] and 'Готовлю резервную копию' in last(harness).text


async def test_stranger_cannot_export_or_backup(harness, export_db):
    await harness.feed(make_message('/export', user=make_user(user_id=2002)))
    await harness.feed(make_message('/backup', user=make_user(user_id=2002)))

    assert harness.session.requests == []


# --- Backup files ----------------------------------------------------------------------

def test_dump_command_keeps_password_out_of_argv():
    params = backup_dump.DbParams(host='db', port='5432', user='finance', password='s3cret', database='finance')

    command = backup_dump.dump_command(params)

    assert 's3cret' not in ' '.join(command)
    assert '--clean' in command and '--if-exists' in command
    assert backup_dump.dump_env(params)['PGPASSWORD'] == 's3cret'


def test_rotate_keeps_newest(tmp_path):
    for day in range(1, 11):
        (tmp_path / f'finance-2026-10-{day:02d}_030000.sql.gz').write_bytes(b'x')
    (tmp_path / 'notes.txt').write_text('keep me')

    removed = backup_dump.rotate(str(tmp_path), keep=7)

    assert len(removed) == 3
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left[0] == 'finance-2026-10-04_030000.sql.gz' and 'notes.txt' in left and len(left) == 8


def test_failed_dump_leaves_nothing(tmp_path):
    params = backup_dump.DbParams(host='127.0.0.1', port='1', user='x', password=None, database='x')

    with pytest.raises(Exception):
        backup_dump.create_backup(params, str(tmp_path), datetime(2026, 10, 22, 3, 0), pg_dump='definitely-not-pg-dump')

    assert list(tmp_path.iterdir()) == []


async def test_backup_task_sends_file(tmp_path, monkeypatch):
    def fake_create(params, directory, now, pg_dump):
        path = Path(directory) / backup_dump.backup_name(now)
        with gzip.open(path, 'wb') as out:
            out.write(b'-- dump')
        return path

    sent = []

    class FakeBot:
        async def send_document(self, chat_id, document, caption):
            sent.append((chat_id, os.path.basename(str(document.path)), caption))

        async def send_message(self, chat_id, text):
            sent.append((chat_id, None, text))

    monkeypatch.setattr(backup_dump, 'create_backup', fake_create)
    monkeypatch.setattr('config.BACKUP_DIR', str(tmp_path))

    path = await backup_tasks.run_backup(chat_id=USER_ID, bot=FakeBot(), now=datetime(2026, 10, 22, 3, 0))

    assert path.endswith('finance-2026-10-22_030000.sql.gz')
    assert sent[0][0] == USER_ID and sent[0][1] == 'finance-2026-10-22_030000.sql.gz' and 'restore.bat' in sent[0][2]


async def test_backup_task_reports_failure(tmp_path, monkeypatch):
    def broken(*args):
        raise RuntimeError('pg_dump failed')

    sent = []

    class FakeBot:
        async def send_message(self, chat_id, text):
            sent.append(text)

    monkeypatch.setattr(backup_dump, 'create_backup', broken)
    monkeypatch.setattr('config.BACKUP_DIR', str(tmp_path))

    assert await backup_tasks.run_backup(chat_id=USER_ID, bot=FakeBot()) is None
    assert sent and sent[0].startswith('⚠️ Резервная копия не создана')
