from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _
from middleware.user import _language_locale
from .states import LanguageState

router = Router(name='language')

LANGUAGE_CODES = ('ru', 'uz', 'en', 'be')


@router.callback_query(F.data == 'language', StateFilter(None))
async def change_language(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(LanguageState.language)
    await callback_query.message.edit_text(text=_('Выберите язык'), reply_markup=buttons.BUTTON_LANGUAGE)


@router.callback_query(F.data.in_(LANGUAGE_CODES), LanguageState.language)
async def language_chosen(callback_query: types.CallbackQuery, state: FSMContext):
    lang = callback_query.data
    await _language_locale(user_id=callback_query.from_user.id, lang=lang)
    await callback_query.message.edit_text(text=_('Ваш язык был изменен', locale=lang))
    await callback_query.message.answer(text=_('Изменения вступили в силу', locale=lang),
                                        reply_markup=await buttons.get_button_manage_money(lang=lang))
    await state.clear()
