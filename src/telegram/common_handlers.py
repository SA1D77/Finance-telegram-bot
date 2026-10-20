from aiogram import Router, F, types
from aiogram.filters import CommandStart, StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _
from .messages import START_MESSAGE

router = Router(name='common')

WELCOME_STICKER = 'CAACAgIAAxkBAAIxgmR_WduWkzBmN4xogt4TSPMCiukoAAI2FgACcmugS6XaTV2HP2QpLwQ'


@router.message(CommandStart(), StateFilter(None))
async def start_bot(message: types.Message, user_created: bool = False):
    # The user row itself is created by EnsureUserMiddleware.
    if user_created:
        await message.answer_sticker(sticker=WELCOME_STICKER)
    await message.answer(text=_(START_MESSAGE), reply_markup=await buttons.get_button_manage_money())


@router.callback_query(F.data == 'cancel')
async def cancel_input(callback_query: types.CallbackQuery, state: FSMContext):
    await state.clear()

    await callback_query.message.edit_text(text=_('Отменено'))
    await callback_query.message.answer(text=_('Гланое меню'), reply_markup=await buttons.get_button_manage_money())
    await callback_query.answer()
