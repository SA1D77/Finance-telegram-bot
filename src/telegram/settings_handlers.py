from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _, __
from .messages import HELP_MESSAGE

router = Router(name='settings')


@router.message(F.text == __('⚙️ Настройки'), StateFilter(None))
async def settings(message: types.Message, state: FSMContext):
    await state.update_data(username=message.from_user.username)
    await message.answer(text=_('⚙️ Настройки'), reply_markup=await buttons.settings_button())


@router.callback_query(F.data == 'help', StateFilter(None))
async def help_handler(callback_query: types.CallbackQuery, state: FSMContext):
    await callback_query.message.edit_text(text=_(HELP_MESSAGE))
    await callback_query.answer()
    await state.clear()
