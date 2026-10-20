from aiogram import Router, F, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _
from middleware.user import _language_locale
from .states import LanguageState

router = Router(name='language')


@router.callback_query(F.data == 'language', StateFilter(None))
async def change_language(callback_query: types.CallbackQuery, state: FSMContext):
    await state.set_state(LanguageState.language)
    await callback_query.message.edit_text(text=_('Выберите язык'), reply_markup=buttons.BUTTON_LANGUAGE)


@router.callback_query(F.data == 'en', LanguageState.language)
async def language_en(callback_query: types.CallbackQuery, state: FSMContext):
    await _language_locale(user_id=callback_query.from_user.id, lang='en')
    await callback_query.message.edit_text(text=_('Ваш язык был изменен', locale='en'))
    await callback_query.message.answer(text=_('Изменения вступили в силу', locale='en'), reply_markup=await buttons.get_button_manage_money(lang='en'))
    await state.clear()


@router.callback_query(F.data == 'ru', LanguageState.language)
async def language_ru(callback_query: types.CallbackQuery, state: FSMContext):
    await _language_locale(user_id=callback_query.from_user.id, lang='ru')
    await callback_query.message.edit_text(text=_('Ваш язык был изменен', locale='ru'))
    await callback_query.message.answer(text=_('Изменения вступили в силу', locale='ru'), reply_markup=await buttons.get_button_manage_money(lang='ru'))
    await state.clear()


@router.callback_query(F.data == 'be', LanguageState.language)
async def language_be(callback_query: types.CallbackQuery, state: FSMContext):
    await _language_locale(user_id=callback_query.from_user.id, lang='be')
    await callback_query.message.edit_text(text=_('Ваш язык был изменен', locale='be'))
    await callback_query.message.answer(text=_('Изменения вступили в силу', locale='be'), reply_markup=await buttons.get_button_manage_money(lang='be'))
    await state.clear()
