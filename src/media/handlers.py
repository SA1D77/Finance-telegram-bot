"""Voice messages and receipt photos become quick entries, always confirmed before saving."""
import asyncio
import io
import logging
from html import escape

from aiogram import F, Router, types
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext

from config import FEATURE_RECEIPTS, FEATURE_VOICE, TESSERACT_LANG, VOSK_MODEL_PATH
from money import fmt
from i18n import _
from quick_input.handlers import handle_quick_text
from quick_input.parser import looks_like_quick_entry
from . import engines
from .numbers_ru import words_to_digits
from .receipt import extract_total

router = Router(name='media')
logger = logging.getLogger(__name__)

MAX_VOICE_SECONDS = 60
MAX_PHOTO_BYTES = 10 * 1024 * 1024


async def _download(message: types.Message, file_id: str) -> bytes:
    buffer = io.BytesIO()
    await message.bot.download(file_id, destination=buffer)
    return buffer.getvalue()


async def _not_installed(message: types.Message) -> None:
    await message.answer(text=_('Распознавание не установлено в этой сборке. Включите его в src/.env и перезапустите start.bat.'))


@router.message(StateFilter(None), F.voice)
async def voice_handler(message: types.Message, state: FSMContext):
    if not FEATURE_VOICE:
        return
    if message.voice.duration > MAX_VOICE_SECONDS:
        await message.answer(text=_('Слишком длинное сообщение: до {seconds} секунд.').format(seconds=MAX_VOICE_SECONDS))
        return
    data = await _download(message, message.voice.file_id)
    try:
        heard = await asyncio.to_thread(engines.transcribe, data, VOSK_MODEL_PATH)
    except engines.EngineUnavailable:
        await _not_installed(message)
        return
    except Exception:
        logger.warning('Speech recognition failed', exc_info=False)
        heard = ''
    text = words_to_digits(heard)
    if not looks_like_quick_entry(text):
        await message.answer(text=_('Не разобрал: «{text}». Скажите, например: «пятьсот кафе обед».').format(text=escape(heard or '…')))
        return
    await message.answer(text=_('🎙 Распознано: «{text}»').format(text=escape(text)))
    await handle_quick_text(message, state, text, confirm=True)


@router.message(StateFilter(None), F.photo)
async def receipt_handler(message: types.Message, state: FSMContext):
    if not FEATURE_RECEIPTS:
        return
    photo = message.photo[-1]  # the largest size
    if photo.file_size and photo.file_size > MAX_PHOTO_BYTES:
        await message.answer(text=_('Фото слишком большое.'))
        return
    data = await _download(message, photo.file_id)
    try:
        text = await asyncio.to_thread(engines.ocr, data, TESSERACT_LANG)
    except engines.EngineUnavailable:
        await _not_installed(message)
        return
    except Exception:
        logger.warning('Receipt recognition failed', exc_info=False)
        text = ''
    total = extract_total(text)
    if total is None:
        await message.answer(text=_('Не нашёл сумму на чеке. Попробуйте сфотографировать ровнее или введите сумму вручную.'))
        return
    await message.answer(text=_('🧾 Итог по чеку: {amount}').format(amount=fmt(total)))
    caption = ' '.join((message.caption or '').split())
    await handle_quick_text(message, state, f'{total} {caption}'.strip(), confirm=True)
