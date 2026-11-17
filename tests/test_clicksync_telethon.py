"""The Telethon glue against the real TL classes of the installed Telethon (no network)."""
import pytest

telethon = pytest.importorskip('telethon')
from telethon.tl import types  # noqa: E402

from clicksync.telethon_port import _kind  # noqa: E402


def button(kind_type):
    return types.KeyboardButton(text='x', type=kind_type)


def test_callback_button():
    assert _kind(button(types.InlineButtonTypeCallback(data=b'1'))) == 'callback'


def test_reply_text_button():
    assert _kind(button(types.ButtonTypeDefault())) == 'text'


def test_url_button():
    assert _kind(button(types.InlineButtonTypeUrl(url='https://example.com'))) == 'url'


def test_other_buttons_are_never_pressed():
    assert _kind(button(types.ButtonTypeRequestPhone())) == 'other'
