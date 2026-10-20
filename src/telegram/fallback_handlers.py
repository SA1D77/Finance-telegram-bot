"""Last router: the bot never stays silent when nothing else matched."""
from aiogram import Router, types
from aiogram.fsm.context import FSMContext

from telegram import buttons
from i18n import _

router = Router(name='fallback')


@router.message()
async def unknown_message(message: types.Message, state: FSMContext):
    if await state.get_state() is not None:
        text = _('Сейчас я жду выбор кнопкой выше. Чтобы выйти, нажмите «⬅️ Отмена» или любую кнопку меню.')
    else:
        text = _('Не понял. Нажмите кнопку меню или напишите, например: 500 кафе обед')
    await message.answer(text=text, reply_markup=await buttons.get_button_manage_money())


@router.callback_query()
async def stale_button(callback_query: types.CallbackQuery):
    await callback_query.answer(text=_('Эта кнопка устарела. Отправьте операцию ещё раз.'))
