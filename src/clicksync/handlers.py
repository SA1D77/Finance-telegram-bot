"""/click: controls of the @clickuz import and the queue of imported payments. Owner only, Russian only.

Nothing is recorded into accounting without a decision here: a payment is either given a category (a new
operation), linked to an operation that already exists, marked as a transfer between own accounts, or skipped.
"""
import csv
import io
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from html import escape
from typing import Optional

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile

from accounting.models import OperationType
from budgets.notify import schedule_budget_check
from config import OWNER_ID, currencies_active
from currency.rates import RateUnavailable, convert, get_rate
from middleware.accounting import _get_category, _show_all_categories, kind_of
from money import current_currency
from telegram import buttons
from telegram.validation import checked_amount
from timeutils import format_local, utc_now
from . import queries, settings
from .reconcile import balance_check, consolidated, totals_by_account
from .texts import amount, discrepancy_text, tx_card, tx_line

router = Router(name='clicksync')
router.message.filter(F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

ALIVE = timedelta(minutes=3)


class ClickState(StatesGroup):
    start_date = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def _markup(*rows) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows])


def _int(data: str, prefix: str) -> Optional[int]:
    raw = data[len(prefix):]
    return int(raw) if raw.isdigit() else None


# --- Status and controls ----------------------------------------------------------------------

async def _status() -> tuple:
    user_id = OWNER_ID
    await queries.ensure_state(user_id)
    state = await queries.get_state(user_id)
    summary = await queries.period_summary(user_id)
    pending = await queries.pending_count(user_id)
    questions = len(await queries.open_discrepancies(user_id))
    counts = summary['counts']

    lines = ['<b>Синхронизация с Click</b>']
    lines.append('📅 Импорт с: ' + (f'{state.start_date:%d.%m.%Y}' if state.start_date else 'не выбрано'))
    alive = state.heartbeat_at is not None and utc_now() - state.heartbeat_at < ALIVE
    lines.append('🤖 Помощник: ' + ('работает' if alive else 'не запущен (см. CLICK.md)'))
    lines.append('⏯ Автосинхронизация: ' + ('на паузе' if state.paused else f'каждые {settings.INTERVAL_MINUTES} мин'))
    if state.sync_requested:
        lines.append('🔄 Запрошена синхронизация, ждёт помощника')
    if state.last_ok_at:
        lines.append(f'✅ Последняя успешная: {format_local(state.last_ok_at)}')
    if state.last_error:
        lines.append(f'⚠️ Последняя ошибка: {escape(state.last_error)}')
    last = summary['last_ok']
    if last is not None:
        lines.append(f'🗓 Последний полный период: {last.period_start:%d.%m.%Y}–{last.period_end:%d.%m.%Y}')
    if summary['earliest']:
        lines.append(f'⏮ Загружено с: {summary["earliest"]:%d.%m.%Y}' + ('' if state.history_complete else ' (история ещё загружается)'))
    lines.append(f'Периодов: готово {counts.get("completed", 0)}, неполных {counts.get("incomplete", 0)}, '
                 f'с ошибкой {counts.get("failed", 0)}')
    for period in summary['problems']:
        lines.append(f'• {period.period_start:%d.%m}–{period.period_end:%d.%m.%Y}: {escape(period.note or period.status)}')

    rows = [
        [_button(f'🧾 Разобрать новые ({pending})', 'cs:q')] if pending else [],
        [_button('🔄 Синхронизировать сейчас', 'cs:now'), _button('📅 Дата начала', 'cs:start')],
        [_button('▶️ Продолжить' if state.paused else '⏸ Пауза', 'cs:resume' if state.paused else 'cs:pause'),
         _button('⚖️ Сверка', 'cs:rec')],
        [_button(f'❗ Вопросы ({questions})', 'cs:d'), _button('📤 Экспорт', 'cs:x')],
    ]
    return '\n'.join(lines), _markup(*[row for row in rows if row])


@router.message(Command('click'), StateFilter(None))
async def click_command(message: types.Message):
    text, markup = await _status()
    await message.answer(text=text, reply_markup=markup)


@router.callback_query(F.data == 'cs:home')
async def home(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    text, markup = await _status()
    await callback_query.message.edit_text(text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data == 'cs:now')
async def sync_now(callback_query: types.CallbackQuery):
    state = await queries.get_state(OWNER_ID)
    if state is None or state.start_date is None:
        await callback_query.answer('Сначала выберите дату начала импорта.', show_alert=True)
        return
    await queries.request_sync(OWNER_ID)
    await callback_query.answer('Запрос отправлен помощнику. Пришлю сообщение, когда появятся новые платежи.', show_alert=True)
    text, markup = await _status()
    await callback_query.message.edit_text(text=text, reply_markup=markup)


@router.callback_query(F.data.in_({'cs:pause', 'cs:resume'}))
async def pause(callback_query: types.CallbackQuery):
    paused = callback_query.data == 'cs:pause'
    await queries.set_paused(OWNER_ID, paused)
    await callback_query.answer('Автосинхронизация на паузе' if paused else 'Автосинхронизация включена')
    text, markup = await _status()
    await callback_query.message.edit_text(text=text, reply_markup=markup)


# --- Start date -----------------------------------------------------------------------------

def parse_start_date(text: Optional[str], today: date) -> Optional[date]:
    try:
        day, month, year = (int(part) for part in (text or '').strip().split('.'))
        value = date(year if year > 99 else 2000 + year, month, day)
    except ValueError:
        return None
    return value if date(2000, 1, 1) <= value <= today else None


@router.callback_query(F.data == 'cs:start')
async def start_date_ask(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(ClickState.start_date)
    hint = ''
    last = await queries.last_accounting_date(OWNER_ID)
    if last is not None:
        hint = (f'\n\nПоследняя операция в учёте: {format_local(last)}. Если раньше вы записывали платежи вручную, '
                'можно начать с этой даты: совпадающие платежи (та же сумма, минута и продавец) свяжутся сами, '
                'остальные я покажу вам, а не запишу молча.')
    await callback_query.message.answer(text='С какой даты загружать платежи из Click? Например 01.10.2026' + hint,
                                        reply_markup=_markup([_button('⬅️ Отмена', 'cs:home')]))
    await callback_query.answer()


@router.message(ClickState.start_date)
async def start_date_input(message: types.Message, state: FSMContext):
    value = parse_start_date(message.text, datetime.now(settings.zone() if settings.TIMEZONE else None).date())
    if value is None:
        await message.answer(text='Не понял дату. Формат: 01.10.2026 (не позже сегодняшнего дня).')
        return
    await state.set_state(None)
    await queries.set_start_date(OWNER_ID, value)
    text = f'Импорт будет начинаться с {value:%d.%m.%Y}. История загрузится при следующей синхронизации.'
    last = await queries.last_accounting_date(OWNER_ID)
    if last is not None and last.date() >= value:
        text += ('\n\n⚠️ В учёте уже есть операции после этой даты. Совпадающие точно я свяжу сами, а про остальные '
                 'спрошу: при разборе есть кнопка «🔗 Уже записано».')
    markup = _markup([_button('🔄 Синхронизировать сейчас', 'cs:now')], [_button('⬅️ К синхронизации', 'cs:home')])
    await message.answer(text=text, reply_markup=markup)


# --- Classification queue -------------------------------------------------------------------

def _operation_type(tx) -> OperationType:
    return OperationType.income if tx.direction == 'in' else OperationType.expenditure


async def _card(tx):
    total = await queries.pending_count(OWNER_ID)
    text = tx_card(tx, 1, total) + '\n\nВыберите категорию:'
    rows = []
    if tx.direction in ('in', 'out'):
        kind = kind_of(_operation_type(tx))
        categories = await _show_all_categories(OWNER_ID, kind)
        remembered = await queries.remembered_category(OWNER_ID, tx.description, kind)
        name = next((c.name for c in categories if c.id == remembered), None)
        if name:
            rows.append([_button(f'✅ {name.capitalize()} (как в прошлый раз)', f'cs:c:{tx.id}:{remembered}')])
        rows += buttons.category_grid(categories, f'cs:c:{tx.id}:')
    else:
        text = tx_card(tx, 1, total) + '\n\nНаправление неизвестно: такой платёж можно только связать или пропустить.'
    rows.append([_button('🔗 Уже записано', f'cs:l:{tx.id}'), _button('🔁 Перевод между своими', f'cs:o:{tx.id}')])
    rows.append([_button('⏭ Не записывать', f'cs:i:{tx.id}'), _button('⏹ Позже', 'cs:home')])
    return text, _markup(*rows)


async def _show_next(callback_query: types.CallbackQuery, note: Optional[str] = None) -> None:
    tx = await queries.next_pending(OWNER_ID)
    if tx is None:
        text, markup = await _status()
        text = (note + '\n\n' if note else '') + '✅ Все платежи разобраны.\n\n' + text
    else:
        text, markup = await _card(tx)
        if note:
            text = note + '\n\n' + text
    await callback_query.message.edit_text(text=text, reply_markup=markup)


@router.callback_query(F.data == 'cs:q')
async def queue(callback_query: types.CallbackQuery):
    await _show_next(callback_query)
    await callback_query.answer()


async def _amounts(tx):
    """(quantity in the user's currency, original amount, original currency, rate) or None without a rate."""
    base = current_currency()
    if tx.currency and base and tx.currency != base and currencies_active():
        try:
            today = datetime.now(settings.zone()).date()
            day = tx.occurred_at.date() if tx.occurred_at else today
            rate = await get_rate(tx.currency, base, day, today)
        except RateUnavailable:
            return None
        return convert(tx.amount, rate), tx.amount, tx.currency, rate
    quantity = checked_amount(tx.amount)
    exact = quantity == tx.amount
    # Units finer than cents are kept as the original amount instead of being rounded away silently.
    return quantity, (None if exact else tx.amount), (None if exact else tx.currency), None


@router.callback_query(F.data.startswith('cs:c:'))
async def classify(callback_query: types.CallbackQuery):
    parts = callback_query.data.split(':')
    if len(parts) != 4 or not parts[2].isdigit() or not parts[3].isdigit():
        await callback_query.answer()
        return
    tx = await queries.get_transaction(OWNER_ID, int(parts[2]))
    category = await _get_category(category_id=int(parts[3]), user_id=OWNER_ID)
    if tx is None or tx.delivery != 'new':
        await callback_query.answer('Этот платёж уже разобран.', show_alert=True)
        await _show_next(callback_query)
        return
    if category is None or tx.direction not in ('in', 'out'):
        await callback_query.answer('Такой категории нет.', show_alert=True)
        return
    amounts = await _amounts(tx)
    if amounts is None or amounts[0] is None:
        await callback_query.answer('Не удалось получить курс или сумма вне допустимого диапазона. Попробуйте позже.', show_alert=True)
        return
    quantity, original_amount, original_currency, rate = amounts
    created_at = settings.to_utc(tx.occurred_at) if tx.occurred_at else utc_now()
    result = await queries.deliver(OWNER_ID, tx.id, category.id, quantity, _operation_type(tx), tx.description,
                                   created_at, original_amount, original_currency, rate)
    if result == 'conflict':
        # The operation with this payment's own id exists: it was written before, only the mark was lost.
        recovered = await queries.link(OWNER_ID, tx.id, queries.delivery_id(OWNER_ID, tx.id), reason='recovered_delivery')
        note = ('ℹ️ Этот платёж уже был записан раньше, повторно не записываю.' if recovered
                else '⚠️ Не удалось подтвердить запись. Ничего не изменено, проверьте /history.')
    elif result == 'stale':
        note = 'ℹ️ Этот платёж уже разобран.'
    else:
        note = f'✅ Записано: {tx_line(tx)} → {category.name.capitalize()}'
        if tx.direction == 'out':
            await schedule_budget_check(OWNER_ID)
    await _show_next(callback_query, note)
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:i:') | F.data.startswith('cs:o:'))
async def skip(callback_query: types.CallbackQuery):
    transaction_id = _int(callback_query.data, callback_query.data[:5])
    own = callback_query.data.startswith('cs:o:')
    done = transaction_id is not None and await queries.ignore(OWNER_ID, transaction_id, own_transfer=own)
    note = ('🔁 Отмечено как перевод между своими счетами: не доход и не расход.' if own else '⏭ Не записываю.') if done \
        else 'ℹ️ Этот платёж уже разобран.'
    await _show_next(callback_query, note)
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:l:'))
async def link_menu(callback_query: types.CallbackQuery):
    tx = await queries.get_transaction(OWNER_ID, _int(callback_query.data, 'cs:l:') or 0)
    if tx is None:
        await callback_query.answer()
        return
    around = settings.to_utc(tx.occurred_at) if tx.occurred_at else None
    found = await queries.candidates(OWNER_ID, tx.amount, tx.currency, current_currency(), around)
    rows = [[_button(f'{format_local(op.created_at)} · {op.comment or "без комментария"}'[:60], f'cs:lk:{tx.id}:{op.id.hex}')]
            for op in found]
    rows.append([_button('⬅️ Назад', 'cs:q')])
    text = tx_card(tx) + '\n\n' + ('С какой записью в учёте это тот же платёж?' if found else
                                   'В учёте нет свободных записей с такой суммой рядом по времени (±3 дня).')
    await callback_query.message.edit_text(text=text, reply_markup=_markup(*rows))
    await callback_query.answer()


def _link_args(data: str, prefix: str):
    parts = data[len(prefix):].split(':')
    if len(parts) != 2 or not parts[0].isdigit():
        return None
    try:
        return int(parts[0]), uuid.UUID(hex=parts[1])
    except ValueError:
        return None


@router.callback_query(F.data.startswith('cs:lk:'))
async def link_chosen(callback_query: types.CallbackQuery):
    args = _link_args(callback_query.data, 'cs:lk:')
    done = args is not None and await queries.link(OWNER_ID, *args)
    await _show_next(callback_query, '🔗 Связано с записью в учёте.' if done else 'ℹ️ Не удалось связать: платёж или запись уже заняты.')
    await callback_query.answer()


# --- Discrepancies ----------------------------------------------------------------------------

@router.callback_query(F.data == 'cs:d')
async def discrepancies(callback_query: types.CallbackQuery):
    items = await queries.open_discrepancies(OWNER_ID)
    from .texts import KIND
    rows = [[_button(('🕓 ' if d.status == 'unresolved' else '❗ ') + KIND.get(d.kind, d.kind)[:50], f'cs:dv:{d.id}')] for d in items]
    rows.append([_button('⬅️ Назад', 'cs:home')])
    text = 'Открытые вопросы (🕓 — оставлены нерешёнными):' if items else 'Открытых вопросов нет.'
    await callback_query.message.edit_text(text=text, reply_markup=_markup(*rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:dv:'))
async def discrepancy_view(callback_query: types.CallbackQuery):
    d = await queries.get_discrepancy(OWNER_ID, _int(callback_query.data, 'cs:dv:') or 0)
    if d is None:
        await callback_query.answer('Вопрос не найден.', show_alert=True)
        return
    period = await queries.get_period(OWNER_ID, d.period_id)
    transactions = await queries.period_transactions(OWNER_ID, d.period_id) if period is not None else []
    tx = await queries.get_transaction(OWNER_ID, d.transaction_id) if d.transaction_id else None
    rows = []
    if tx is not None and tx.accounting_id is None and tx.delivery in ('delivered', 'linked', 'ignored'):
        rows.append([_button('🔗 Связать с записью', f'cs:dl:{d.id}'), _button('🧾 Разобрать заново', f'cs:dq:{d.id}')])
    if d.kind == 'balance_mismatch':
        rows.append([_button('➕ Добавить операцию', f'cs:da:{d.id}')])
    if d.status == 'open' or d.status == 'unresolved':
        rows.append([_button('✅ Решено', f'cs:dr:{d.id}'), _button('🕓 Оставить нерешённым', f'cs:du:{d.id}')])
    rows.append([_button('⬅️ К вопросам', 'cs:d')])
    await callback_query.message.edit_text(text=discrepancy_text(d, period, transactions, tx), reply_markup=_markup(*rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:dr:') | F.data.startswith('cs:du:'))
async def discrepancy_close(callback_query: types.CallbackQuery):
    resolved = callback_query.data.startswith('cs:dr:')
    discrepancy_id = _int(callback_query.data, callback_query.data[:6]) or 0
    await queries.close_discrepancy(OWNER_ID, discrepancy_id, 'resolved' if resolved else 'unresolved',
                                    'user_resolved' if resolved else 'left_unresolved')
    await callback_query.answer('Отмечено как решённое' if resolved else 'Оставлено нерешённым, повторно напоминать не буду')
    await discrepancies(callback_query)


@router.callback_query(F.data.startswith('cs:da:'))
async def discrepancy_add(callback_query: types.CallbackQuery):
    await callback_query.message.answer(
        text='Отправьте недостающую операцию обычным сообщением, например «-15 000 комиссия» или «+200 000 возврат». '
             'Бот ничего не добавляет сам. Потом откройте вопрос снова и нажмите «✅ Решено».')
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:dq:'))
async def discrepancy_requeue(callback_query: types.CallbackQuery):
    d = await queries.get_discrepancy(OWNER_ID, _int(callback_query.data, 'cs:dq:') or 0)
    if d is None or d.transaction_id is None or not await queries.requeue(OWNER_ID, d.transaction_id):
        await callback_query.answer('Не получилось: платёж уже связан с записью.', show_alert=True)
        return
    await queries.close_discrepancy(OWNER_ID, d.id, 'resolved', 'requeued')
    await _show_next(callback_query, '🧾 Платёж возвращён в разбор.')
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:dl:'))
async def discrepancy_link_menu(callback_query: types.CallbackQuery):
    d = await queries.get_discrepancy(OWNER_ID, _int(callback_query.data, 'cs:dl:') or 0)
    tx = await queries.get_transaction(OWNER_ID, d.transaction_id) if d and d.transaction_id else None
    if tx is None:
        await callback_query.answer()
        return
    around = settings.to_utc(tx.occurred_at) if tx.occurred_at else None
    found = await queries.candidates(OWNER_ID, tx.amount, tx.currency, current_currency(), around, days=7)
    rows = [[_button(f'{format_local(op.created_at)} · {op.comment or "без комментария"}'[:60], f'cs:dk:{d.id}:{op.id.hex}')]
            for op in found]
    rows.append([_button('⬅️ Назад', f'cs:dv:{d.id}')])
    await callback_query.message.edit_text(
        text=tx_card(tx) + '\n\n' + ('Выберите запись в учёте:' if found else 'Свободных записей с такой суммой рядом (±7 дней) нет.'),
        reply_markup=_markup(*rows))
    await callback_query.answer()


@router.callback_query(F.data.startswith('cs:dk:'))
async def discrepancy_link(callback_query: types.CallbackQuery):
    args = _link_args(callback_query.data, 'cs:dk:')
    d = await queries.get_discrepancy(OWNER_ID, args[0]) if args else None
    if d is None or d.transaction_id is None or not await queries.relink_recorded(OWNER_ID, d.transaction_id, args[1]):
        await callback_query.answer('Не удалось связать: запись уже занята.', show_alert=True)
        return
    await queries.close_discrepancy(OWNER_ID, d.id, 'resolved', 'linked')
    await callback_query.answer('Связано')
    await discrepancies(callback_query)


# --- Reconciliation and export --------------------------------------------------------------

def reconciliation_text(transactions, periods_with_balance: list, deliveries: dict) -> str:
    lines = ['<b>⚖️ Сверка</b>']
    if not periods_with_balance:
        lines.append('Сверку баланса выполнить нельзя: отчёты Click не показывают начальный и конечный баланс '
                     'за период, а придумывать его я не буду.')
    for period, scoped in periods_with_balance:
        result = balance_check(period.opening_balance, period.closing_balance, scoped)
        label = f'{period.period_start:%d.%m}–{period.period_end:%d.%m.%Y}'
        verdict = {'ok': '✅ сходится', 'mismatch': '❌ не сходится', 'indeterminate': '❔ не определить',
                   'unavailable': '— нет данных'}[result.status]
        difference = f', разница {amount(result.difference, period.balance_currency)}' if result.difference else ''
        lines.append(f'{label} {escape(period.balance_account or "")}: {verdict}{difference}')

    lines.append('')
    lines.append('<b>Платежи Click и учёт</b>')
    lines.append(f'Записано: {deliveries.get("delivered", 0)}, связано с вашими записями: {deliveries.get("linked", 0)}, '
                 f'не записывать: {deliveries.get("ignored", 0)}, ждут решения: {deliveries.get("new", 0)}')
    per_account = totals_by_account(transactions)
    if per_account:
        lines.append('')
        lines.append('<b>По картам (успешные платежи)</b>')
        for (account, currency), totals in sorted(per_account.items(), key=lambda item: (item[0][0] or '', item[0][1] or '')):
            line = (f'{escape(account or "карта не указана")}: приход {amount(totals.income, currency)}, '
                    f'расход {amount(totals.expense, currency)}')
            if totals.fees:
                line += f', комиссии {amount(totals.fees, currency)}'
            if totals.transfers_in or totals.transfers_out:
                line += f', переводы между своими {amount(totals.transfers_in + totals.transfers_out, currency)}'
            lines.append(line)
        lines.append('')
        lines.append('<b>Итого без переводов между своими счетами</b>')
        for currency, totals in consolidated(per_account).items():
            lines.append(f'{currency or "валюта не указана"}: доход {amount(totals.income, currency)}, '
                         f'расход {amount(totals.expense + totals.fees, currency)}')
    return '\n'.join(lines)


@router.callback_query(F.data == 'cs:rec')
async def reconciliation(callback_query: types.CallbackQuery):
    raised = await queries.check_ledger(OWNER_ID, current_currency())
    transactions = await queries.all_transactions(OWNER_ID)
    periods = []
    if await queries.periods_with_balances(OWNER_ID):
        by_period = {}
        for tx in transactions:
            by_period.setdefault(tx.period_id, []).append(tx)
        for period_id in by_period:
            period = await queries.get_period(OWNER_ID, period_id)
            if period is not None and period.opening_balance is not None and period.closing_balance is not None:
                scoped = [t for t in by_period[period_id]
                          if period.balance_account in (None, t.account) and period.balance_currency in (None, t.currency)]
                periods.append((period, scoped))
    text = reconciliation_text(transactions, periods, await queries.delivery_counts(OWNER_ID))
    if raised:
        text += f'\n\n❗ Найдено новых расхождений с учётом: {raised}. Смотрите «Вопросы».'
    await callback_query.message.edit_text(text=text, reply_markup=_markup([_button('⬅️ Назад', 'cs:home')]))
    await callback_query.answer()


EXPORT_HEADERS = ['Дата и время (Click)', 'Направление', 'Сумма', 'Валюта', 'Комиссия', 'Комиссия в сумме', 'Статус',
                  'Описание', 'Карта', 'ID в Click', 'В учёте', 'Перевод между своими', 'Неоднозначный', 'Загружен (UTC)']
DELIVERY = {'new': 'ждёт решения', 'delivered': 'записан', 'linked': 'связан', 'ignored': 'не записывать'}


def ledger_csv(transactions) -> bytes:
    """Exact amounts as text (never float): UTF-8 with BOM and ";" like /export."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=';', lineterminator='\r\n')
    writer.writerow(EXPORT_HEADERS)
    for tx in transactions:
        def plain(value: Optional[Decimal]) -> str:
            return '' if value is None else format(value.normalize(), 'f').replace('.', ',')
        writer.writerow([
            tx.occurred_at.strftime('%Y-%m-%d %H:%M') if tx.occurred_at else '', tx.direction, plain(tx.amount),
            tx.currency or '', plain(tx.fee), {True: 'да', False: 'нет', None: ''}[tx.fee_included], tx.status,
            tx.description or '', tx.account or '', tx.source_id or '', DELIVERY.get(tx.delivery, tx.delivery),
            'да' if tx.own_transfer else '', 'да' if tx.ambiguous else '', tx.first_seen_at.strftime('%Y-%m-%d %H:%M'),
        ])
    return buffer.getvalue().encode('utf-8-sig')


@router.callback_query(F.data == 'cs:x')
async def export(callback_query: types.CallbackQuery):
    transactions = await queries.all_transactions(OWNER_ID)
    if not transactions:
        await callback_query.answer('Журнал пока пуст.', show_alert=True)
        return
    name = f'click-ledger-{datetime.now():%Y-%m-%d}.csv'
    await callback_query.message.answer_document(BufferedInputFile(ledger_csv(transactions), filename=name),
                                                 caption=f'Журнал платежей Click: {len(transactions)} строк.')
    await callback_query.answer()
