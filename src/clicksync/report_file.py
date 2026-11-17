"""The monthly report file of @clickuz: "⬇️ Скачать" -> a month -> click-payments-<id>-<YYYYMM>.xlsx.

Layout seen in a real file (November 2026), one sheet, a header row and one row per payment:

    № платежа | Сумма | Время | Карта | Сервис | Комиссия | Параметры оплаты | Статус платежа

* Сумма is text with digits only and NO sign: a salary and a taxi ride look the same. The direction is not in
  the file; the adapter takes it from the weekly report message (➕ / ➖) where it can, otherwise it stays
  'unknown' and the owner says which way when classifying.
* There is no currency column either; the adapter supplies it.
* Время is "DD.MM.YYYY HH:MM:SS" as printed by Click (local time of the report).
* № платежа is mostly empty; when present it is used as the payment's own id.
* "Параметры оплаты" may hold phone or account numbers and is never read.

Columns are found by their header text, not by position, so a reordered file still reads. Read with the
standard library only (an .xlsx is a zip of XML parts).
"""
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Dict, List, Optional
from xml.etree import ElementTree

from .core import ParsedTx, mask_account

_NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_REL_NS = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'

# Header text (lower case, a part of it) -> field. Checked in this order.
COLUMNS = (
    ('№', 'source_id'), ('номер', 'source_id'),
    ('комисс', 'fee'),
    ('сумма', 'amount'),
    ('время', 'time'), ('дата', 'time'),
    ('карта', 'card'),
    ('сервис', 'description'), ('получател', 'description'),
    ('статус', 'status'),
)
REQUIRED = ('amount', 'time')


class UnknownReportFormat(Exception):
    """The file is not the report layout described above."""


@dataclass
class ReportFile:
    transactions: List[ParsedTx] = field(default_factory=list)
    unreadable: int = 0     # payment rows that could not be read: the period is then not complete


def status_of(text: str) -> str:
    value = (text or '').casefold()
    if any(word in value for word in ('отмен', 'возврат', 'reverse', 'refund')):
        return 'reversed'
    if any(word in value for word in ('не проведен', 'неуспеш', 'ошибк', 'отклон', 'failed', 'error')):
        return 'failed'
    if any(word in value for word in ('обработ', 'ожида', 'pending')):
        return 'pending'
    if any(word in value for word in ('успешно', 'проведен', 'success')):
        return 'completed'
    return 'unknown'


def amount_of(text: str) -> Optional[Decimal]:
    """'60000', "2'100'000", '1 200 000,50', '-2500.00' -> Decimal (sign kept); None when not a number."""
    raw = re.sub(r"[\s'  `]", '', text or '')
    if not raw:
        return None
    if ',' in raw and '.' in raw:
        raw = raw.replace(',', '')
    else:
        raw = raw.replace(',', '.')
    if not re.fullmatch(r'[+-]?\d+(\.\d+)?', raw):
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def time_of(text: str, numeric: bool) -> Optional[datetime]:
    value = (text or '').strip()
    if not value:
        return None
    if numeric:     # an Excel date serial
        try:
            return datetime(1899, 12, 30) + timedelta(days=float(value))
        except (ValueError, OverflowError):
            return None
    for pattern in ('%d.%m.%Y %H:%M:%S', '%d.%m.%Y %H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S'):
        try:
            return datetime.strptime(value, pattern)
        except ValueError:
            continue
    return None


def _column(reference: str) -> str:
    return re.sub(r'\d', '', reference or '')


def _first_sheet(archive: zipfile.ZipFile) -> str:
    names = archive.namelist()
    try:
        workbook = ElementTree.fromstring(archive.read('xl/workbook.xml'))
        rels = ElementTree.fromstring(archive.read('xl/_rels/workbook.xml.rels'))
        first = workbook.find(f'{_NS}sheets/{_NS}sheet')
        target = next(r.get('Target') for r in rels if r.get('Id') == first.get(f'{_REL_NS}id'))
        path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        if path in names:
            return path
    except (KeyError, AttributeError, StopIteration, ElementTree.ParseError):
        pass
    sheets = sorted(n for n in names if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', n))
    if not sheets:
        raise UnknownReportFormat('в файле нет листов')
    return sheets[0]


def read_rows(path: Path) -> List[Dict[str, tuple]]:
    """Rows of the first sheet as {column letter: (text, is_number)}."""
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as error:
        raise UnknownReportFormat(f'не xlsx: {type(error).__name__}') from None
    with archive:
        shared: List[str] = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            for item in ElementTree.fromstring(archive.read('xl/sharedStrings.xml')).findall(f'{_NS}si'):
                shared.append(''.join(t.text or '' for t in item.iter(f'{_NS}t')))
        sheet = ElementTree.fromstring(archive.read(_first_sheet(archive)))
    rows = []
    for row in sheet.iter(f'{_NS}row'):
        cells: Dict[str, tuple] = {}
        for cell in row.findall(f'{_NS}c'):
            kind = cell.get('t', 'n')
            value = cell.find(f'{_NS}v')
            if kind == 'inlineStr':
                text = ''.join(t.text or '' for t in cell.iter(f'{_NS}t'))
            else:
                text = value.text if value is not None and value.text is not None else ''
            if kind == 's' and text:
                try:
                    text = shared[int(text)]
                except (ValueError, IndexError):
                    raise UnknownReportFormat('битая таблица строк') from None
            cells[_column(cell.get('r'))] = (text, kind == 'n')
        rows.append(cells)
    return rows


def _header(rows: List[Dict[str, tuple]]):
    """(index of the header row, {field: column letter})."""
    for index, row in enumerate(rows[:10]):
        mapping: Dict[str, str] = {}
        for letter, (text, _numeric) in row.items():
            label = (text or '').strip().casefold()
            if not label or 'параметр' in label:
                continue
            for part, name in COLUMNS:
                if part in label:
                    mapping.setdefault(name, letter)
                    break
        if all(name in mapping for name in REQUIRED):
            return index, mapping
    raise UnknownReportFormat('не найдена строка заголовков со столбцами «Сумма» и «Время»')


def parse_report_file(path: Path) -> ReportFile:
    rows = read_rows(path)
    start, columns = _header(rows)
    result = ReportFile()

    def cell(row, name):
        letter = columns.get(name)
        return row.get(letter, ('', False)) if letter else ('', False)

    for row in rows[start + 1:]:
        if not any((text or '').strip() for text, _ in row.values()):
            continue
        amount = amount_of(cell(row, 'amount')[0])
        when = time_of(*cell(row, 'time'))
        if amount is None or when is None or amount == 0:
            result.unreadable += 1
            continue
        fee = amount_of(cell(row, 'fee')[0])
        source_id = (cell(row, 'source_id')[0] or '').strip() or None
        description = ' '.join((cell(row, 'description')[0] or '').split()) or None
        result.transactions.append(ParsedTx(
            amount=abs(amount), direction='out' if amount < 0 else 'unknown',
            status=status_of(cell(row, 'status')[0]), occurred_at=when, currency=None,
            description=description, account=mask_account(cell(row, 'card')[0]),
            source_id=source_id, fee=abs(fee) if fee else None, fee_included=None))
    return result
