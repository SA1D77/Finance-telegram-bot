"""clickuz.Port on Telethon: the chat with the source bot through the owner's own Telegram session.

Only this chat is ever read. Nothing from it is logged.
"""
import asyncio
from pathlib import Path
from typing import List

from telethon import TelegramClient
from telethon.tl import types
from telethon.errors import BotResponseTimeoutError

from .clickuz import BotMessage, Button
from .settings import SESSION_PATH, api_credentials, prepare_data_dir


def make_client() -> TelegramClient:
    api_id, api_hash = api_credentials()
    prepare_data_dir()
    return TelegramClient(str(SESSION_PATH), api_id, api_hash)


# Since Telethon 1.43 (layer 2xx) a button is one KeyboardButton(text, type) and its kind lives in `type`;
# older layers had a class per kind (KeyboardButtonCallback...). Names are looked up so that both work.
_KINDS = (('InlineButtonTypeCallback', 'callback'), ('ButtonTypeDefault', 'text'), ('InlineButtonTypeUrl', 'url'),
          ('KeyboardButtonCallback', 'callback'), ('KeyboardButtonUrl', 'url'))


def _kind(button) -> str:
    """'callback', 'text', 'url' or 'other' for a raw TL button (MessageButton.button)."""
    for candidate in (getattr(button, 'type', None), button):
        for name, kind in _KINDS:
            cls = getattr(types, name, None)
            if cls is not None and candidate is not None and isinstance(candidate, cls):
                return kind
    if type(button).__name__ == 'KeyboardButton' and getattr(button, 'type', None) is None:
        return 'text'   # old layer: a plain reply button
    return 'other'


def _flat_buttons(message) -> list:
    return [button for row in (message.buttons or []) for button in row]


def to_bot_message(message) -> BotMessage:
    buttons = [Button(text=button.text or '', kind=_kind(button.button)) for button in _flat_buttons(message)]
    return BotMessage(id=message.id, text=message.raw_text or '', buttons=buttons, has_file=message.document is not None)


class TelethonPort:
    def __init__(self, client: TelegramClient, bot_username: str) -> None:
        self.client = client
        self.bot_username = bot_username
        self.entity = None

    async def start(self) -> None:
        self.entity = await self.client.get_input_entity(self.bot_username)

    async def send(self, text: str) -> None:
        await self.client.send_message(self.entity, text)

    async def recent(self, limit: int = 8) -> List[BotMessage]:
        messages = await self.client.get_messages(self.entity, limit=limit)
        return [to_bot_message(message) for message in messages if not message.out]

    async def press(self, message_id: int, index: int) -> None:
        message = await self.client.get_messages(self.entity, ids=message_id)
        button = _flat_buttons(message)[index]
        if _kind(button.button) != 'callback':
            raise ValueError('not a callback button')
        try:
            await button.click()
        except (BotResponseTimeoutError, asyncio.TimeoutError):
            pass    # the bot did not answer the callback itself; its reaction is awaited in the chat

    async def download(self, message_id: int, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        message = await self.client.get_messages(self.entity, ids=message_id)
        return Path(await message.download_media(file=str(directory)))
