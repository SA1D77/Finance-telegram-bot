"""Feedback to the bot owner: users send a bug report or an idea, the owner can answer from Telegram."""
import time
from html import escape
from typing import Dict

from aiogram import F, Router, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

from config import OWNER_ID
from i18n import _
from telegram import buttons
from telegram.validation import parse_category_id

router = Router(name='feedback')

MAX_LENGTH = 2000
COOLDOWN_SECONDS = 60
REPLY_KEY = 'feedback_reply_to'
_last_sent: Dict[int, float] = {}


class FeedbackState(StatesGroup):
    message = State()
    reply = State()


def _author(user: types.User) -> str:
    name = escape(user.full_name or '')
    login = f' @{escape(user.username)}' if user.username else ''
    return f'{name}{login} (id <code>{user.id}</code>, {escape(user.language_code or "?")})'


@router.message(Command('feedback'), StateFilter(None))
async def feedback_command(message: types.Message, state: FSMContext):
    await _ask(message, state)


@router.callback_query(F.data == 'feedback')
async def feedback_button(callback_query: types.CallbackQuery, state: FSMContext):
    await _ask(callback_query.message, state)
    await callback_query.answer()


async def _ask(message: types.Message, state: FSMContext) -> None:
    await state.set_state(FeedbackState.message)
    await message.answer(text=_('✉️ Напишите, что не работает или что добавить. Можно приложить скриншот.'),
                         reply_markup=await buttons.get_button_cancel())


@router.message(FeedbackState.message)
async def feedback_message(message: types.Message, state: FSMContext):
    user = message.from_user
    text = message.text or message.caption or ''
    if not text.strip() and not message.photo:
        await message.answer(text=_('Пришлите текст или скриншот.'), reply_markup=await buttons.get_button_cancel())
        return
    if len(text) > MAX_LENGTH:
        await message.answer(text=_('Слишком длинно: до {count} символов.').format(count=MAX_LENGTH),
                             reply_markup=await buttons.get_button_cancel())
        return
    now = time.monotonic()
    if user.id != OWNER_ID and now - _last_sent.get(user.id, -COOLDOWN_SECONDS) < COOLDOWN_SECONDS:
        await message.answer(text=_('Подождите минуту перед следующим сообщением.'))
        return
    _last_sent[user.id] = now

    reply = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text='↩️ Ответить', callback_data=f'fb:reply:{user.id}')]])
    await message.bot.send_message(chat_id=OWNER_ID, text=f'📩 Сообщение от {_author(user)}:')
    # Copy keeps the text, photo or file exactly as sent.
    await message.bot.copy_message(chat_id=OWNER_ID, from_chat_id=message.chat.id, message_id=message.message_id,
                                   reply_markup=reply)
    await state.clear()
    await message.answer(text=_('Спасибо! Сообщение отправлено разработчику.'),
                         reply_markup=await buttons.get_button_manage_money())


@router.callback_query(F.data.startswith('fb:reply:'))
async def reply_start(callback_query: types.CallbackQuery, state: FSMContext):
    user_id = parse_category_id(callback_query.data, prefix='fb:reply:')
    if callback_query.from_user.id != OWNER_ID or user_id is None:
        await callback_query.answer()
        return
    await state.set_state(FeedbackState.reply)
    await state.update_data(**{REPLY_KEY: user_id})
    await callback_query.message.answer(text=f'Ответ для id {user_id}:', reply_markup=await buttons.get_button_cancel())
    await callback_query.answer()


@router.message(FeedbackState.reply)
async def reply_message(message: types.Message, state: FSMContext):
    user_id = (await state.get_data()).get(REPLY_KEY)
    await state.clear()
    if message.from_user.id != OWNER_ID or not isinstance(user_id, int):
        return
    try:
        await message.bot.send_message(chat_id=user_id, text='📬 ' + _('Ответ разработчика:'))
        await message.bot.copy_message(chat_id=user_id, from_chat_id=message.chat.id, message_id=message.message_id)
    except Exception:
        await message.answer(text='Не удалось доставить: пользователь мог заблокировать бота.')
        return
    await message.answer(text='✅ Ответ отправлен.', reply_markup=await buttons.get_button_manage_money())
