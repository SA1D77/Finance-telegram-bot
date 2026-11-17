"""Russian texts shared by the userbot (notifications) and the bot (/click). Pure functions."""
from datetime import datetime
from decimal import Decimal
from html import escape
from typing import Any, Iterable, List, Optional

from money import number
from .reconcile import balance_check

DIRECTION = {'in': '➕ поступление', 'out': '➖ списание', 'unknown': '❔ направление неизвестно'}
STATUS = {'completed': 'успешно', 'pending': 'в обработке', 'failed': 'неуспешно', 'reversed': 'возврат / отмена',
          'unknown': 'статус не указан'}
KIND = {
    'balance_mismatch': 'Баланс не сходится',
    'changed_after_delivery': 'Записанная операция изменилась в отчёте',
    'accounting_missing': 'Записанная операция пропала из учёта',
    'amount_differs': 'Сумма в учёте отличается от отчёта',
    'period_incomplete': 'Период прочитан не полностью',
    'period_failed': 'Период не обработан',
    'history_ended_early': 'История в Click короче выбранной',
}
# What the user is asked to decide, per kind.
NEEDED = {
    'balance_mismatch': 'Проверьте, нет ли операции, которой не хватает в отчёте или в учёте. Можно добавить её вручную, '
                        'отметить вопрос решённым или оставить нерешённым.',
    'changed_after_delivery': 'Проверьте запись в учёте (/history) и исправьте её сами, если нужно: '
                              'бот суммы не меняет.',
    'accounting_missing': 'Свяжите платёж с другой записью, верните его в разбор или оставьте как есть.',
    'amount_differs': 'Исправьте сумму в /history, если ошиблись, или отметьте, что так и должно быть.',
    'period_incomplete': 'Ничего делать не нужно: период будет прочитан снова. Если повторяется, проверьте настройки кнопок.',
    'period_failed': 'Ничего делать не нужно: период будет прочитан снова при следующей синхронизации.',
    'history_ended_early': 'Если ранние операции нужны, внесите их вручную или выберите более позднюю дату начала.',
}


def amount(value: Optional[Decimal], currency: Optional[str]) -> str:
    if value is None:
        return '—'
    return f'{number(value)} {currency}' if currency else number(value)


def signed(value: Decimal, currency: Optional[str]) -> str:
    return ('+' if value > 0 else '') + amount(value, currency)


def when(value: Optional[datetime]) -> str:
    return value.strftime('%d.%m.%Y %H:%M') if value else 'время не указано'


def tx_line(tx: Any) -> str:
    sign = '+' if tx.direction == 'in' else ('−' if tx.direction == 'out' else '?')
    parts = [f'{sign}{amount(tx.amount, tx.currency)}', escape(tx.description or 'без описания'), when(tx.occurred_at)]
    if tx.status != 'completed':
        parts.append(STATUS.get(tx.status, tx.status))
    if getattr(tx, 'fee', None):
        parts.append(f'комиссия {amount(tx.fee, tx.currency)}')
    return ' · '.join(parts)


def tx_card(tx: Any, position: Optional[int] = None, total: Optional[int] = None) -> str:
    head = '🧾 Платёж из Click' + (f' ({position} из {total})' if position and total else '')
    lines = [f'<b>{head}</b>',
             f'{DIRECTION.get(tx.direction, tx.direction)}: <b>{amount(tx.amount, tx.currency)}</b>',
             f'🔹 {escape(tx.description or "без описания")}',
             f'🕘 {when(tx.occurred_at)} (время Click)']
    if tx.account:
        lines.append(f'💳 {escape(tx.account)}')
    if tx.fee:
        included = {True: 'входит в сумму', False: 'сверх суммы', None: 'неизвестно, входит ли в сумму'}[tx.fee_included]
        lines.append(f'Комиссия: {amount(tx.fee, tx.currency)} ({included})')
    if tx.status != 'completed':
        lines.append(f'⚠️ Статус: {STATUS.get(tx.status, tx.status)}')
    if tx.ambiguous:
        lines.append('⚠️ В отчёте есть точно такой же платёж в ту же минуту: это могут быть два платежа или повтор строки.')
    return '\n'.join(lines)


def discrepancy_text(d: Any, period: Any = None, transactions: Iterable[Any] = (), tx: Any = None) -> str:
    """The notification about one discrepancy: where, expected vs reported, the difference, what may explain it
    and what is needed from the user."""
    lines = [f'❗ <b>{KIND.get(d.kind, d.kind)}</b>']
    if d.account or d.currency:
        lines.append(f'Счёт: {escape(d.account or "все карты")}' + (f', валюта {d.currency}' if d.currency else ''))
    if period is not None:
        lines.append(f'Период: {period.period_start:%d.%m.%Y}–{period.period_end:%d.%m.%Y}')
    if d.details:
        lines.append(escape(d.details))
    if d.expected is not None or d.reported is not None:
        label = ('Ожидаемый баланс', 'Баланс в отчёте') if d.kind == 'balance_mismatch' else ('В отчёте Click', 'В учёте')
        lines.append(f'{label[0]}: {amount(d.expected, d.currency)}')
        lines.append(f'{label[1]}: {amount(d.reported, d.currency)}')
    if d.difference is not None:
        lines.append(f'Разница: <b>{signed(d.difference, d.currency)}</b>')
    if tx is not None:
        lines.append('Платёж: ' + tx_line(tx))
    explaining: List[Any] = []
    if d.kind == 'balance_mismatch' and period is not None:
        result = balance_check(period.opening_balance, period.closing_balance,
                               [t for t in transactions if d.account in (None, t.account) and d.currency in (None, t.currency)])
        explaining = result.candidates[:5]
    if explaining:
        lines.append('Могут объяснить разницу:')
        lines += [f'• {tx_line(t)}' for t in explaining]
    elif d.kind == 'balance_mismatch':
        lines.append('Операций, которые объясняли бы разницу, в отчёте не найдено.')
    lines.append('')
    lines.append('Что нужно от вас: ' + NEEDED.get(d.kind, 'Посмотрите и решите, что с этим делать.'))
    return '\n'.join(lines)
