"""Operations as CSV or XLSX, built in memory (nothing is written to disk)."""
import csv
import io
from decimal import Decimal
from typing import Any, Dict, Iterable, List

import xlsxwriter

from accounting.models import OperationType
from timeutils import to_local

HEADERS = {
    'ru': ['Дата', 'Тип', 'Категория', 'Сумма', 'Комментарий'],
    'en': ['Date', 'Type', 'Category', 'Amount', 'Comment'],
    'be': ['Дата', 'Тып', 'Катэгорыя', 'Сума', 'Каментарый'],
}
TYPES = {
    'ru': {OperationType.income: 'Доход', OperationType.expenditure: 'Расход'},
    'en': {OperationType.income: 'Income', OperationType.expenditure: 'Expense'},
    'be': {OperationType.income: 'Даход', OperationType.expenditure: 'Расход'},
}


def _lang(lang: str) -> str:
    return lang if lang in HEADERS else 'ru'


def rows_for_export(accounts: Iterable[Any], lang: str) -> List[Dict[str, Any]]:
    lang = _lang(lang)
    return [{
        'date': to_local(account.created_at).strftime('%Y-%m-%d %H:%M'),
        'type': TYPES[lang][account.operation_type],
        'category': account.categories,
        'amount': Decimal(account.quantity),
        'comment': account.comment or '',
    } for account in accounts]


def to_csv(rows: List[Dict[str, Any]], lang: str) -> bytes:
    """UTF-8 with BOM and ";" so Excel opens Cyrillic text and decimal commas correctly."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(HEADERS[_lang(lang)])
    for row in rows:
        writer.writerow([row['date'], row['type'], row['category'], str(row['amount']), row['comment']])
    return buffer.getvalue().encode('utf-8-sig')


def to_xlsx(rows: List[Dict[str, Any]], lang: str) -> bytes:
    buffer = io.BytesIO()
    workbook = xlsxwriter.Workbook(buffer, {'in_memory': True})
    sheet = workbook.add_worksheet('Operations')
    bold = workbook.add_format({'bold': True})
    money = workbook.add_format({'num_format': '#,##0.00'})
    for column, title in enumerate(HEADERS[_lang(lang)]):
        sheet.write(0, column, title, bold)
    for index, row in enumerate(rows, start=1):
        sheet.write(index, 0, row['date'])
        sheet.write(index, 1, row['type'])
        sheet.write(index, 2, row['category'])
        sheet.write_number(index, 3, float(row['amount']), money)  # a number cell, so Excel can sum it
        sheet.write(index, 4, row['comment'])
    sheet.set_column(0, 0, 17)
    sheet.set_column(1, 2, 16)
    sheet.set_column(3, 3, 14)
    sheet.set_column(4, 4, 40)
    sheet.freeze_panes(1, 0)
    sheet.autofilter(0, 0, max(len(rows), 1), 4)
    workbook.close()
    return buffer.getvalue()
