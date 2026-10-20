from aiogram import Router, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile

import asyncio
from html import escape

from telegram import buttons
from i18n import _, __
from telegram.states import (
    ReportState,
    EmailState
)
from middleware.user import _insert_user_email, _get_users_email, _get_user_lang
from middleware.accounting import _get_accounts
from exception import EmailAlreadyExist
from report.email_send import email_report, is_valid_email
from report.files import build_report_files, remove_report_files, serialize_accounts

router = Router(name='report')

REPORT_PERIODS = {'1', '30', '365'}


async def _report_rows(state: FSMContext, user_id: int):
    data = await state.get_data()
    accounts = await _get_accounts(user_id=user_id, time=data.get('time'))
    return serialize_accounts(accounts)


@router.message(Command('report'), StateFilter(None))
async def run_report(message: types.Message, state: FSMContext):
    await state.set_state(ReportState.time_interval)
    await message.answer(text=_('Выберите промежуток времени'), reply_markup=await buttons.time_interval_buttons())


@router.callback_query(F.data.startswith('time'), ReportState.time_interval)
async def report(callback_query: types.CallbackQuery, state: FSMContext):
    text = callback_query.data.replace('time_', '').lower()
    if text not in REPORT_PERIODS:
        await callback_query.answer()
        return

    # Files are built right before sending, so there is no race with a background task
    # and nothing is left on disk afterwards.
    await state.update_data(time=text)
    await state.set_state(ReportState.send_place)
    await callback_query.message.edit_text(text=_('Отчет сформирован!'))
    await callback_query.message.answer(text=_('Выберите место для отправки отчета'), reply_markup=await buttons.get_report_buttons())


@router.message(ReportState.send_place)
async def report_send_place(message: types.Message, state: FSMContext):
    user_input = message.text
    user_id = message.from_user.id

    if user_input == _('📧 Почта'):
        response = await _get_users_email(user_id=user_id)
        if response:
            await state.set_state(EmailState.reuse_email)
            await message.answer(text=_('У вас есть сохранненая почта, использовать её?'), reply_markup=await buttons.get_email_remember_buttons())
        else:
            await state.set_state(ReportState.email)
            await message.answer(text=_('Введите вашу почту'), reply_markup=await buttons.get_button_cancel())
    elif user_input == _('📌 Чат'):
        rows = await _report_rows(state, user_id)
        lang = await _get_user_lang(user_id=user_id)
        files = await asyncio.to_thread(build_report_files, rows, lang or 'ru')
        try:
            await message.answer_document(document=FSInputFile(files.table, filename='report.xlsx'),
                                          reply_markup=await buttons.get_button_manage_money())
            await message.answer_photo(photo=FSInputFile(files.general_diagram))
            await message.answer(text=_('РАСХОД'), reply_markup=await buttons.get_button_manage_money())
            await message.answer_photo(photo=FSInputFile(files.expenditure_diagram))
            await message.answer(text=_('ДОХОД'), reply_markup=await buttons.get_button_manage_money())
            await message.answer_photo(photo=FSInputFile(files.income_diagram))
        finally:
            remove_report_files(files)
        await state.clear()
    else:
        await message.answer(text=_('Отменено'), reply_markup=await buttons.get_button_manage_money())
        await state.clear()


@router.message(ReportState.email)
async def send_report(message: types.Message, state: FSMContext):
    user_email = (message.text or '').strip()
    if not is_valid_email(user_email):
        await message.answer(text=_('Похоже, это не адрес почты. Попробуйте ещё раз'), reply_markup=await buttons.get_button_cancel())
        return

    await state.set_state(ReportState.remember_email)
    rows = await _report_rows(state, message.from_user.id)
    lang = await _get_user_lang(user_id=message.from_user.id)
    email_report.delay(user_email=user_email, accounts=rows, lang=lang)
    await message.answer(text=_('Сохранить почту?'), reply_markup=await buttons.get_email_remember_buttons())
    await state.update_data(email=user_email)


@router.message(ReportState.remember_email)
async def remember_email(message: types.Message, state: FSMContext):
    user_input = message.text
    data = await state.get_data()
    email = data.get('email')

    if user_input == _('⭕️ Да'):
        try:
            await _insert_user_email(user_id=message.from_user.id, email=email)
        except EmailAlreadyExist:
            await message.answer(text=_('Похоже такая почта уже существует! Выберите другую'), reply_markup=await buttons.get_button_manage_money())
        else:
            await message.answer(text=_('Проверьте свою почту'), reply_markup=await buttons.get_button_manage_money())

    await state.clear()


@router.message(EmailState.reuse_email)
async def reuse_email(message: types.Message, state: FSMContext):
    user_input = message.text

    if user_input == _('⭕️ Да'):
        rows = await _report_rows(state, message.from_user.id)
        await state.clear()
        email = await _get_users_email(user_id=message.from_user.id)
        lang = await _get_user_lang(user_id=message.from_user.id)
        email_report.delay(user_email=email, accounts=rows, lang=lang)
        await message.answer(text=_('Проверьте свою почту'), reply_markup=await buttons.get_button_manage_money())
    else:
        await state.set_state(ReportState.email)
        await message.answer(text=_('Введите вашу почту'), reply_markup=await buttons.get_button_cancel())


@router.message(F.text == __('🕒 Последние 10 операций'), StateFilter(None))
async def last_ten_operations(message: types.Message):
    accounts = await _get_accounts(user_id=message.from_user.id, time='last')
    msg = str()
    for i in accounts:
        msg += '\n<b>📌' + i.created_at.strftime("%d/%m/%Y, %H:%M:%S") + '  ' + str(i.quantity) + ' => ' + escape(i.categories) + '</b>' + (f' 💬 {escape(i.comment)}' if i.comment else '') + '\n'

    await message.answer(text=_('🕒 Последние 10 операций') + ':\n' + msg, reply_markup=await buttons.get_button_manage_money())
