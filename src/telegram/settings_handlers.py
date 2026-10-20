from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _, __
from config import FEATURE_QUICK_INPUT
from .messages import HELP_MESSAGE, QUICK_INPUT_HELP

router = Router(name='settings')


@router.message(F.text == __('⚙️ Настройки'), StateFilter(None))
async def settings(message: types.Message):
    await message.answer(text=_('⚙️ Настройки'), reply_markup=await buttons.settings_button())


@router.callback_query(F.data == 'help', StateFilter(None))
async def help_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(text=_(HELP_MESSAGE))
    if FEATURE_QUICK_INPUT:
        await callback_query.message.answer(text=_(QUICK_INPUT_HELP))
    await callback_query.answer()
    await state.clear()
