"""Telegram edits shared by the Click controls and balance follow-up screens."""
from aiogram import types
from aiogram.exceptions import TelegramBadRequest


async def edit_text(message: types.Message, **kwargs):
    """A repeated callback may display the same screen. Only that harmless error is ignored."""
    try:
        return await message.edit_text(**kwargs)
    except TelegramBadRequest as error:
        if 'message is not modified' not in error.message.casefold():
            raise
        return None
