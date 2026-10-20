import io
import shutil
import uuid
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiogram.types import Chat, Message, PhotoSize, Update, Voice

from media import engines
from media import handlers as m
from media.numbers_ru import words_to_digits
from media.receipt import extract_total
from quick_input import handlers as qi

from conftest import USER_ID, make_callback, make_user

D = Decimal


@pytest.mark.parametrize('heard, text', [
    ('пятьсот кафе', '500 кафе'),
    ('плюс пятьдесят тысяч зарплата', '+50000 зарплата'),
    ('две тысячи триста сорок продукты', '2340 продукты'),
    ('полторы тысячи такси', '1500 такси'),
    ('5 тысяч кафе обед', '5000 кафе обед'),
    ('сто двадцать пять кафе', '125 кафе'),
    ('тысяча рублей отель', '1000 рублей отель'),
    ('один миллион двести тысяч машина', '1200000 машина'),
    ('двадцать одна тысяча кафе', '21000 кафе'),
    ('минус триста кафе', '-300 кафе'),
    ('Пятьсот Кафе', '500 кафе'),
    ('кафе', 'кафе'),
    ('', ''),
    ('плюс', '+'),
])
def test_words_to_digits(heard, text):
    assert words_to_digits(heard) == text


@pytest.mark.parametrize('ocr_text, total', [
    ('ООО Ромашка\nХлеб 45,00\nМолоко 89,90\nИТОГО: 134,90\nНаличные 200,00', '134.90'),
    ('Кофе 3.50\nTOTAL 12.40\nCash 20.00', '12.40'),
    ('Jami: 125 000\nNaqd 130 000', '125000.00'),
    ('Сумма к оплате 1 234,56', '1234.56'),
    ('позиция 12.30\nещё 45.67', '45.67'),        # no total word: the largest amount with cents
    ('ИТОГ =2 499.00 ₽', '2499.00'),
])
def test_extract_total(ocr_text, total):
    assert extract_total(ocr_text) == D(total)


@pytest.mark.parametrize('ocr_text', ['', 'nothing here', 'ИНН 7701234567', None])
def test_extract_total_none(ocr_text):
    assert extract_total(ocr_text) is None


# --- Bot flows ---------------------------------------------------------------------------

def voice_update(duration=3):
    return Update(update_id=5, message=Message(
        message_id=5, date=datetime.now(), chat=Chat(id=USER_ID, type='private'), from_user=make_user(),
        voice=Voice(file_id='v1', file_unique_id='u1', duration=duration)))


def photo_update(caption=None, size=1000):
    return Update(update_id=6, message=Message(
        message_id=6, date=datetime.now(), chat=Chat(id=USER_ID, type='private'), from_user=make_user(), caption=caption,
        photo=[PhotoSize(file_id='small', file_unique_id='s', width=90, height=90),
               PhotoSize(file_id='big', file_unique_id='b', width=1280, height=1600, file_size=size)]))


class SavedList(list):
    """Saved operations, plus the file ids that were downloaded."""
    downloads: list


@pytest.fixture
def saved(monkeypatch):
    saved = SavedList()
    downloads = []

    async def download(message, file_id):
        downloads.append(file_id)
        return b'media-bytes'

    async def categories(user_id):
        return [SimpleNamespace(id=1, name='кафе'), SimpleNamespace(id=2, name='продукты')]

    async def get_category(category_id, user_id):
        return {1: SimpleNamespace(id=1, name='кафе'), 2: SimpleNamespace(id=2, name='продукты')}.get(category_id)

    async def add_operation(**kwargs):
        saved.append(kwargs)
        return uuid.uuid4()

    monkeypatch.setattr(m, '_download', download)
    monkeypatch.setattr(qi, '_show_all_categories', categories)
    monkeypatch.setattr(qi, '_get_category', get_category)
    monkeypatch.setattr(qi, '_add_operation', add_operation)
    saved.downloads = downloads
    return saved


def texts(harness):
    return [getattr(r, 'text', None) for r in harness.session.requests]


async def test_voice_becomes_confirmed_entry(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'transcribe', lambda data, path: 'пятьсот кафе обед')

    await harness.feed(voice_update())

    assert '🎙 Распознано: «500 кафе обед»' in texts(harness)
    assert saved == []  # even an exact category waits for confirmation
    await harness.feed(make_callback('qi:save'))
    assert saved[0]['quantity'] == D('500.00') and saved[0]['comment'] == 'обед'


async def test_unclear_voice(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'transcribe', lambda data, path: 'привет как дела')

    await harness.feed(voice_update())

    assert 'Не разобрал: «привет как дела»' in texts(harness)[-1] and saved == []


async def test_long_voice_is_refused(harness, saved):
    await harness.feed(voice_update(duration=120))

    assert 'Слишком длинное' in texts(harness)[-1] and saved.downloads == []


async def test_voice_without_engine(harness, saved, monkeypatch):
    def missing(data, path):
        raise engines.EngineUnavailable('vosk')

    monkeypatch.setattr(engines, 'transcribe', missing)

    await harness.feed(voice_update())

    assert 'Распознавание не установлено' in texts(harness)[-1]


async def test_receipt_total_then_category(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'ocr', lambda data, lang: 'Хлеб 45,00\nИТОГО 134,90')

    await harness.feed(photo_update())

    assert saved.downloads == ['big']
    assert '🧾 Итог по чеку: 134.90' in texts(harness)
    await harness.feed(make_callback('qi:cat:2'))
    assert saved[0]['quantity'] == D('134.90') and saved[0]['category_id'] == 2


async def test_receipt_caption_names_category(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'ocr', lambda data, lang: 'TOTAL 12.40')

    await harness.feed(photo_update(caption='продукты магнит'))
    await harness.feed(make_callback('qi:save'))

    assert saved[0]['category_id'] == 2 and saved[0]['comment'] == 'магнит'


async def test_receipt_without_total(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'ocr', lambda data, lang: 'размыто')

    await harness.feed(photo_update())

    assert 'Не нашёл сумму' in texts(harness)[-1] and saved == []


async def test_huge_photo_refused(harness, saved):
    await harness.feed(photo_update(size=50 * 1024 * 1024))

    assert 'слишком большое' in texts(harness)[-1] and saved.downloads == []


async def test_stranger_voice_ignored(harness, saved, monkeypatch):
    monkeypatch.setattr(engines, 'transcribe', lambda data, path: 'пятьсот кафе')
    stranger = Update(update_id=7, message=Message(
        message_id=7, date=datetime.now(), chat=Chat(id=2002, type='private'), from_user=make_user(user_id=2002),
        voice=Voice(file_id='v', file_unique_id='u', duration=2)))

    await harness.feed(stranger)

    assert harness.session.requests == [] and saved.downloads == []


@pytest.mark.skipif(shutil.which('tesseract') is None, reason='tesseract is not installed')
def test_real_ocr_reads_digits():
    from PIL import Image, ImageDraw
    image = Image.new('RGB', (600, 120), 'white')
    ImageDraw.Draw(image).text((20, 40), 'TOTAL 123.45', fill='black')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    assert '123' in engines.ocr(buffer.getvalue(), 'eng')


def test_engines_report_missing_tools(monkeypatch):
    monkeypatch.setattr(shutil, 'which', lambda name: None)
    with pytest.raises(engines.EngineUnavailable):
        engines.ocr(b'', 'eng')
    with pytest.raises(engines.EngineUnavailable):
        engines.ogg_to_pcm(b'')
