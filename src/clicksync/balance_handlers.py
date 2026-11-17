"""/click → card balances: manual check, the unexplained intervals and the owner's answers. Owner only.

Nothing is recorded without a confirmation; the confirmation carries a one-time token, so a repeated click
(or a click after a restart) never records twice.
"""
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Optional

from aiogram import F, Router, types
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from accounting.models import OperationType
from budgets.notify import schedule_budget_check
from config import OWNER_ID, currencies_active
from currency.rates import RateUnavailable, convert, get_rate
from middleware.accounting import _get_category, _show_all_categories, kind_of
from money import current_currency
from telegram import buttons
from telegram.validation import checked_amount, normalize_expression
from timeutils import utc_now
from . import balance_queries, queries, settings
from .balance_texts import balances_text, card_name, confirm_text, interval_text, money, span
from .ui import edit_text

router = Router(name='clicksync_balance')
router.message.filter(F.from_user.id == OWNER_ID)
router.callback_query.filter(F.from_user.id == OWNER_ID)

SNOOZE = timedelta(hours=24)
# Which way the money went for each kind; None: the sign of the unexplained part decides.
DIRECTION = {'expense': 'out', 'fee': 'out', 'income': 'in', 'refund': 'in', 'own_transfer': None, 'adjustment': None}
# Kinds recorded into accounting (with a category of this type).
RECORDED = {'expense': OperationType.expenditure, 'fee': OperationType.expenditure,
            'income': OperationType.income, 'refund': OperationType.income}
SHORT = {'expense': 'ex', 'fee': 'fe', 'income': 'in', 'refund': 'rf', 'own_transfer': 'ow', 'adjustment': 'ad'}
LONG = {v: k for k, v in SHORT.items()}


class BalanceState(StatesGroup):
    amount = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def _markup(*rows) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[list(row) for row in rows if row])


def _local(value: datetime) -> datetime:
    return settings.from_utc(value)


def _ids(data: str, count: int) -> Optional[list]:
    parts = data.split(':')[2:]
    if len(parts) < count or not all(p.isdigit() for p in parts[:count]):
        return None
    return [int(p) for p in parts[:count]] + parts[count:]


def parse_custom_amount(text: Optional[str]) -> Optional[Decimal]:
    """A positive amount typed by the owner ("50 000", "50'000", "12,5"); None if it is not one."""
    raw = normalize_expression((text or '').strip()).replace(',', '.')
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def kinds_for(remaining: Decimal) -> list:
    """The answers that fit the sign of the unexplained part."""
    if remaining < 0:
        return [('expense', '➖ Расход'), ('fee', '💸 Комиссия'), ('own_transfer', '🔁 Перевод на свою карту'),
                ('adjustment', '✏️ Корректировка')]
    return [('income', '➕ Доход'), ('refund', '↩️ Возврат'), ('own_transfer', '🔁 Перевод со своей карты'),
            ('adjustment', '✏️ Корректировка')]


def direction_of(kind: str, remaining: Decimal) -> str:
    return DIRECTION.get(kind) or ('in' if remaining > 0 else 'out')


# --- Overview ------------------------------------------------------------------------------------------

async def overview():
    now = utc_now()
    observed_at, snapshots = await balance_queries.latest_balances(OWNER_ID)
    asked = await balance_queries.open_intervals(OWNER_ID, now)
    others = await balance_queries.other_intervals(OWNER_ID, now)
    state = await queries.get_state(OWNER_ID)
    lines = [balances_text(observed_at, snapshots, _local)]
    if state is not None and state.last_balance_error:
        lines.append(f'⚠️ Последняя проверка не удалась: {state.last_balance_error}')
    if state is not None and state.balance_requested:
        lines.append('🔄 Проверка запрошена, ждёт помощника.')
    if not settings.BALANCE_ENABLED:
        lines.append('ℹ️ Автоматическая проверка выключена (CLICK_BALANCE_CHECK=0).')
    lines.append('')
    lines.append(f'Вопросов: {len(asked)}' + (f', других открытых промежутков: {len(others)}' if others else ''))
    rows = []
    for interval in (asked + others)[:12]:
        sign = '+' if (interval.remaining or 0) > 0 else '−'
        mark = '❓' if interval in asked else ('⚠️' if interval.status == 'uncertain' else '🕓')
        label = f'{mark} {card_name(interval.last4, interval.card)} {_local(interval.end_at):%d.%m}'
        if interval.remaining:
            label += f' {sign}{money(abs(interval.remaining), interval.currency)}'
        rows.append([_button(label[:60], f'cb:v:{interval.id}')])
    rows.append([_button('💳 Проверить баланс', 'cb:now')])
    rows.append([_button('⬅️ К синхронизации', 'cs:home')])
    return '\n'.join(lines), _markup(*rows)


@router.callback_query(F.data == 'cb:list')
async def balances(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    text, markup = await overview()
    await edit_text(callback_query.message, text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data == 'cb:now')
async def check_now(callback_query: types.CallbackQuery):
    await balance_queries.request_balance(OWNER_ID)
    await callback_query.answer('Запрос отправлен помощнику: он проверит баланс в Click и пришлёт результат.', show_alert=True)
    text, markup = await overview()
    await edit_text(callback_query.message, text=text, reply_markup=markup)


# --- One interval ------------------------------------------------------------------------------------

async def _interval_view(interval_id: int, note: Optional[str] = None):
    interval = await balance_queries.get_interval(OWNER_ID, interval_id)
    if interval is None:
        return (note + '\n\n' if note else '') + 'Этот промежуток не найден.', _markup([_button('⬅️ Назад', 'cb:list')])
    explanations = await balance_queries.interval_explanations(OWNER_ID, interval_id)
    text = interval_text(interval, _local, explanations)
    if note:
        text = note + '\n\n' + text
    rows = []
    if interval.status in ('unexplained', 'uncertain') and interval.remaining:
        kinds = kinds_for(interval.remaining)
        rows += [[_button(label, f'cb:k:{interval.id}:{SHORT[kind]}') for kind, label in kinds[i:i + 2]]
                 for i in range(0, len(kinds), 2)]
    if interval.status in balance_queries.ACTIVE:
        rows.append([_button('🤷 Не помню', f'cb:u:{interval.id}'), _button('⏰ Проверить позже', f'cb:s:{interval.id}')])
    rows.append([_button('⬅️ Назад', 'cb:list')])
    return text, _markup(*rows)


@router.callback_query(F.data.startswith('cb:v:'))
async def view(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    ids = _ids(callback_query.data, 1)
    if ids is None:
        await callback_query.answer()
        return
    text, markup = await _interval_view(ids[0])
    await edit_text(callback_query.message, text=text, reply_markup=markup)
    await callback_query.answer()


@router.callback_query(F.data.startswith('cb:u:') | F.data.startswith('cb:s:'))
async def set_aside(callback_query: types.CallbackQuery):
    ids = _ids(callback_query.data, 1)
    if ids is None:
        await callback_query.answer()
        return
    if callback_query.data.startswith('cb:u:'):
        done = await balance_queries.mark_unresolved(OWNER_ID, ids[0])
        note = '🤷 Отмечено «Не помню». Больше не спрашиваю, в учёт ничего не записано.'
    else:
        done = await balance_queries.snooze(OWNER_ID, ids[0], utc_now() + SNOOZE)
        note = '⏰ Напомню завтра.'
    text, markup = await _interval_view(ids[0], note if done else 'ℹ️ Этот промежуток уже закрыт.')
    await edit_text(callback_query.message, text=text, reply_markup=markup)
    await callback_query.answer()


# --- Answer: kind → (category) → amount → confirmation ---------------------------------------------------

@router.callback_query(F.data.startswith('cb:k:'))
async def choose_kind(callback_query: types.CallbackQuery, state: FSMContext):
    ids = _ids(callback_query.data, 1)
    interval = await balance_queries.get_interval(OWNER_ID, ids[0]) if ids else None
    kind = LONG.get(ids[1]) if ids and len(ids) > 1 else None
    if interval is None or kind is None or not interval.remaining:
        await callback_query.answer('Этот промежуток уже закрыт.', show_alert=True)
        return
    if kind in RECORDED:
        categories = await _show_all_categories(OWNER_ID, kind_of(RECORDED[kind]))
        rows = buttons.category_grid(categories, f'cb:c:{interval.id}:{SHORT[kind]}:')
        rows.append([_button('⬅️ Назад', f'cb:v:{interval.id}')])
        await edit_text(callback_query.message, text='Выберите категорию:', reply_markup=_markup(*rows))
    else:
        await _ask_amount(callback_query.message, state, interval, kind, None, None)
    await callback_query.answer()


@router.callback_query(F.data.startswith('cb:c:'))
async def choose_category(callback_query: types.CallbackQuery, state: FSMContext):
    parts = callback_query.data.split(':')
    if len(parts) != 5 or not parts[2].isdigit() or not parts[4].isdigit() or parts[3] not in LONG:
        await callback_query.answer()
        return
    interval = await balance_queries.get_interval(OWNER_ID, int(parts[2]))
    kind = LONG[parts[3]]
    category = await _get_category(category_id=int(parts[4]), user_id=OWNER_ID)
    if interval is None or not interval.remaining or category is None or kind not in RECORDED:
        await callback_query.answer('Не получилось: промежуток закрыт или категории нет.', show_alert=True)
        return
    await _ask_amount(callback_query.message, state, interval, kind, category.id, category.name)
    await callback_query.answer()


async def _ask_amount(message: types.Message, state: FSMContext, interval, kind: str, category_id, category_name):
    await state.set_state(BalanceState.amount)
    await state.update_data(balance={'interval': interval.id, 'kind': kind, 'category': category_id,
                                     'category_name': category_name})
    whole = money(abs(interval.remaining), interval.currency)
    await edit_text(
        message,
        text=f'Какая сумма? Не объяснено {whole}. Можно всю сумму или часть: остальное останется вопросом.\n'
             'Чтобы указать часть, напишите сумму сообщением.',
        reply_markup=_markup([_button(f'Вся сумма: {whole}', f'cb:a:{interval.id}')],
                             [_button('⬅️ Назад', f'cb:v:{interval.id}')]))


async def _confirm(state: FSMContext, interval, value: Decimal):
    """The confirmation screen; the token is made here and only here, once per screen."""
    data = (await state.get_data()).get('balance') or {}
    if data.get('interval') != interval.id:
        return None
    direction = direction_of(data['kind'], interval.remaining)
    token = secrets.token_hex(8)
    data.update(amount=str(value), direction=direction, token=token)
    await state.update_data(balance=data)
    text = confirm_text(interval, data['kind'], direction, value, data.get('category_name'))
    return text, _markup([_button('✅ Подтвердить', f'cb:ok:{token}'), _button('Отмена', f'cb:v:{interval.id}')])


@router.callback_query(F.data.startswith('cb:a:'))
async def whole_amount(callback_query: types.CallbackQuery, state: FSMContext):
    ids = _ids(callback_query.data, 1)
    interval = await balance_queries.get_interval(OWNER_ID, ids[0]) if ids else None
    shown = await _confirm(state, interval, abs(interval.remaining)) if interval is not None and interval.remaining else None
    if shown is None:
        await callback_query.answer('Начните заново: откройте промежуток ещё раз.', show_alert=True)
        return
    await edit_text(callback_query.message, text=shown[0], reply_markup=shown[1])
    await callback_query.answer()


@router.message(BalanceState.amount)
async def custom_amount(message: types.Message, state: FSMContext):
    data = (await state.get_data()).get('balance') or {}
    interval = await balance_queries.get_interval(OWNER_ID, data.get('interval') or 0)
    value = parse_custom_amount(message.text)
    if interval is None or not interval.remaining:
        await state.set_state(None)
        await message.answer('Этот промежуток уже закрыт.')
        return
    if value is None or value > abs(interval.remaining):
        await message.answer(f'Нужна сумма больше нуля и не больше {money(abs(interval.remaining), interval.currency)}.')
        return
    shown = await _confirm(state, interval, value)
    await message.answer(text=shown[0], reply_markup=shown[1])


async def _operation(interval, kind: str, value: Decimal) -> Optional[dict]:
    """The accounting operation for a recorded kind, in the user's currency (converted at the day's rate)."""
    base = current_currency()
    end = _local(interval.end_at)
    original_amount = original_currency = rate = None
    quantity = checked_amount(value)
    if interval.currency and base and interval.currency != base and currencies_active():
        try:
            rate = await get_rate(interval.currency, base, end.date(), datetime.now(settings.zone()).date())
        except RateUnavailable:
            return None
        quantity, original_amount, original_currency = convert(value, rate), value, interval.currency
    elif quantity is not None and quantity != value:
        original_amount, original_currency = value, interval.currency
    if quantity is None:
        return None
    comment = f'Сверка баланса {card_name(interval.last4, interval.card)}, {span(_local(interval.start_at), end)}'
    return balance_queries.operation_values(quantity, RECORDED[kind], comment, interval.end_at, original_amount,
                                            original_currency, rate)


@router.callback_query(F.data.startswith('cb:ok:'))
async def confirm(callback_query: types.CallbackQuery, state: FSMContext):
    token = callback_query.data[len('cb:ok:'):]
    data = (await state.get_data()).get('balance') or {}
    if not token or data.get('token') != token:
        # A second click, or the screen is from before a restart: nothing to do twice.
        await callback_query.answer('Уже обработано или устарело. Откройте промежуток заново.', show_alert=True)
        return
    interval = await balance_queries.get_interval(OWNER_ID, data['interval'])
    if interval is None:
        await callback_query.answer('Этот промежуток не найден.', show_alert=True)
        return
    kind, value = data['kind'], Decimal(data['amount'])
    operation = None
    if kind in RECORDED:
        operation = await _operation(interval, kind, value)
        if operation is None:
            await callback_query.answer('Не удалось получить курс или сумма вне диапазона. Попробуйте позже.', show_alert=True)
            return
    result = await balance_queries.explain(OWNER_ID, interval.id, token, kind, data['direction'], value,
                                           data.get('category'), operation)
    await state.set_state(None)
    await state.update_data(balance=None)
    note = {'ok': '✅ Записано.' if operation else '✅ Отмечено.',
            'duplicate': 'ℹ️ Это уже было записано, повторно не записываю.',
            'too_much': '⚠️ Сумма больше, чем осталось объяснить. Ничего не записано.',
            'stale': 'ℹ️ Этот промежуток уже закрыт. Ничего не записано.'}[result]
    if result == 'ok' and operation and RECORDED[kind] == OperationType.expenditure:
        await schedule_budget_check(OWNER_ID)
    text, markup = await _interval_view(interval.id, note)
    await edit_text(callback_query.message, text=text, reply_markup=markup)
    await callback_query.answer()


# --- A payment that may be a part of an explanation ---------------------------------------------------

async def candidates_for(tx) -> list:
    if tx.direction not in ('in', 'out') or not settings.TIMEZONE:
        return []
    return await balance_queries.explanation_candidates(
        OWNER_ID, tx.id, timedelta(minutes=settings.BALANCE_BOUNDARY_MINUTES), settings.to_utc)


@router.callback_query(F.data.startswith('cb:m:'))
async def match(callback_query: types.CallbackQuery):
    ids = _ids(callback_query.data, 2)
    done = ids is not None and await balance_queries.apply_match(
        OWNER_ID, ids[1], ids[0], 'owner', timedelta(minutes=settings.BALANCE_BOUNDARY_MINUTES), settings.to_utc)
    from .handlers import _show_next
    await _show_next(callback_query, '🧩 Связано с вашим объяснением из сверки баланса: повторно не записывается.' if done
                     else 'ℹ️ Не удалось связать: платёж или объяснение уже заняты.')
    await callback_query.answer()
