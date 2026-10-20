"""/export: operations for a period as a CSV or XLSX file in the chat; /backup: a database copy now."""
from datetime import datetime
from typing import Optional

from aiogram import F, Router, types
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile

from config import FEATURE_BACKUP
from history.periods import parse_date_range, period_range
from i18n import _, i18n
from middleware.accounting import _get_accounts_between
from telegram import buttons
from timeutils import local_now
from .exporters import rows_for_export, to_csv, to_xlsx

router = Router(name='export')

PERIOD_KEY = 'export_period'
PERIODS = ('today', 'week', 'month', 'year', 'all')


class ExportState(StatesGroup):
    custom_range = State()


def _button(text: str, data: str) -> types.InlineKeyboardButton:
    return types.InlineKeyboardButton(text=text, callback_data=data)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


async def _ask_format(message: types.Message, state: FSMContext, since: Optional[datetime], until: Optional[datetime]):
    await state.set_state(None)
    await state.update_data(**{PERIOD_KEY: {'since': _iso(since), 'until': _iso(until)}})
    markup = types.InlineKeyboardMarkup(inline_keyboard=[[_button('📄 CSV', 'e:f:csv'), _button('📊 XLSX', 'e:f:xlsx')]])
    await message.answer(text=_('В каком формате?'), reply_markup=markup)


@router.message(Command('export'), StateFilter(None))
async def export_command(message: types.Message):
    labels = {'today': _('Сегодня'), 'week': _('Неделя'), 'month': _('Месяц'), 'year': _('Год'), 'all': _('Всё время')}
    rows = [[_button(labels[p], f'e:p:{p}') for p in PERIODS[:2]], [_button(labels[p], f'e:p:{p}') for p in PERIODS[2:4]],
            [_button(labels['all'], 'e:p:all'), _button(_('📅 Свой период'), 'e:p:custom')]]
    await message.answer(text=_('Экспорт операций. За какой период?'), reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows))


@router.callback_query(F.data.startswith('e:'))
async def leave_text_input(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(None)
    raise SkipHandler()


@router.callback_query(F.data.startswith('e:p:'))
async def period_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    period = callback_query.data[len('e:p:'):]
    if period == 'custom':
        await state.set_state(ExportState.custom_range)
        await callback_query.message.answer(text=_('Введите период: 01.09.2026-30.09.2026, 01.09-30.09 или одну дату'),
                                            reply_markup=await buttons.get_button_cancel())
    elif period in PERIODS:
        since, until = period_range(period) if period != 'all' else (None, None)
        await _ask_format(callback_query.message, state, since, until)
    await callback_query.answer()


@router.message(ExportState.custom_range)
async def custom_range_input(message: types.Message, state: FSMContext):
    period = parse_date_range(message.text)
    if period is None:
        await message.answer(text=_('Не понял период. Примеры: 01.09.2026-30.09.2026, 01.09-30.09, 05.10.2026'),
                             reply_markup=await buttons.get_button_cancel())
        return
    await _ask_format(message, state, *period)


@router.callback_query(F.data.startswith('e:f:'))
async def format_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    fmt = callback_query.data[len('e:f:'):]
    period = (await state.get_data()).get(PERIOD_KEY)
    if fmt not in ('csv', 'xlsx') or period is None:
        await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'), show_alert=True)
        return
    since = datetime.fromisoformat(period['since']) if period['since'] else None
    until = datetime.fromisoformat(period['until']) if period['until'] else None
    accounts = await _get_accounts_between(user_id=callback_query.from_user.id, since=since, until=until)
    rows = rows_for_export(accounts, i18n.current_locale)
    content = to_csv(rows, i18n.current_locale) if fmt == 'csv' else to_xlsx(rows, i18n.current_locale)
    filename = f"operations-{local_now().strftime('%Y-%m-%d')}.{fmt}"
    await callback_query.message.answer_document(
        document=BufferedInputFile(content, filename=filename),
        caption=_('Операций: {count}').format(count=len(rows)))
    await state.update_data(**{PERIOD_KEY: None})
    await callback_query.answer()


@router.message(Command('backup'), StateFilter(None))
async def backup_command(message: types.Message, is_owner: bool = False):
    # The copy holds every user's data, so only the owner can ask for it.
    if not FEATURE_BACKUP or not is_owner:
        await message.answer(text=_('Эта команда доступна только владельцу бота.'))
        return
    from backup.tasks import queue_backup
    if await queue_backup(message.chat.id):
        await message.answer(text=_('Готовлю резервную копию, пришлю файл через минуту.'))
    else:
        await message.answer(text=_('Не удалось поставить задачу: проверьте, что контейнер celery запущен.'))
