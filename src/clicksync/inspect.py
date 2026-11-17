"""Shows what the @clickuz reports really look like, so the import is set up from facts:

    python -m clicksync.inspect

You drive it: it prints the bot's last messages with numbered buttons and presses a button only when you
type its number and confirm. Use it to walk /reports → Платежи → previous period → download once.
A transcript is written to secrets/clicksync/inspect/transcript.json with every digit replaced by 0 and
button data hidden; received files are saved next to it. Nothing is sent anywhere: look the transcript
through, blur the merchant names if you wish, and share it to finish the adapter settings.
"""
import asyncio
import json
import re
import sys
from typing import Any, Dict, List

from config import OWNER_ID
from .settings import BOT, ConfigError, DATA_DIR, profile
from .telethon_port import TelethonPort, _flat_buttons, _kind, make_client

INSPECT_DIR = DATA_DIR / 'inspect'


def anonymize(text: str) -> str:
    """Keeps the layout (emoji, separators, words), hides the numbers: every digit becomes 0."""
    return re.sub(r'\d', '0', text or '')


def describe(message) -> Dict[str, Any]:
    document = message.document
    return {
        'id_order': None,   # filled by the caller: position in the transcript, not the Telegram id
        'text': anonymize(message.raw_text),
        'text_length': len(message.raw_text or ''),
        'buttons': [{'label': anonymize(b.text), 'kind': _kind(b.button)} for b in _flat_buttons(message)],
        'file': None if document is None else {
            'mime': document.mime_type, 'size': document.size,
            'name': anonymize(next((a.file_name for a in document.attributes if hasattr(a, 'file_name')), '')),
        },
    }


def _ask(prompt: str) -> str:
    return input(prompt).strip()


async def main() -> int:
    try:
        client = make_client()
    except ConfigError as error:
        print(error)
        return 1
    await client.connect()
    if not await client.is_user_authorized():
        print('Сначала выполните вход: python -m clicksync.login')
        return 1
    if (await client.get_me()).id != OWNER_ID:
        print('Сессия принадлежит не владельцу бота (OWNER_ID). Остановлено.')
        return 1
    port = TelethonPort(client, BOT)
    await port.start()
    INSPECT_DIR.mkdir(parents=True, exist_ok=True)
    transcript: List[Dict[str, Any]] = []
    command = profile().reports_command

    print(f'Чат с @{BOT}. Команды: номер кнопки — нажать; s — отправить {command}; r — обновить; '
          f'f — сохранить файл из последнего сообщения; q — выход.')
    print('Нажимайте только кнопки отчётов. Кнопки оплаты и переводов не трогайте.')
    try:
        await _walk(client, port, transcript, command)
    finally:    # the transcript survives an error or Ctrl+C
        path = INSPECT_DIR / 'transcript.json'
        path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2), encoding='utf-8')
        await client.disconnect()
        print(f'Запись сохранена: {path}')
        print('В ней все цифры заменены на 0. Просмотрите файл перед тем, как кому-либо его показывать.')
    return 0


async def _walk(client, port: TelethonPort, transcript: List[Dict[str, Any]], command: str) -> None:
    while True:
        messages = [m for m in await client.get_messages(port.entity, limit=3) if not m.out]
        if not messages:
            print('(сообщений от бота пока нет)')
        for message in reversed(messages):
            print('─' * 40)
            print(message.raw_text or '(без текста)')
            if message.document is not None:
                print('[к сообщению прикреплён файл]')
        latest = messages[0] if messages else None
        buttons = _flat_buttons(latest) if latest is not None else []
        for number, button in enumerate(buttons, start=1):
            print(f'  [{number}] {button.text}   ({_kind(button.button)})')
        if latest is not None:
            entry = describe(latest)
            entry['id_order'] = len(transcript) + 1
            if not transcript or transcript[-1]['text'] != entry['text'] or transcript[-1]['buttons'] != entry['buttons']:
                transcript.append(entry)

        choice = _ask('> ').lower()
        if choice == 'q':
            break
        if choice == 'r':
            continue
        if choice == 's':
            if _ask(f'Отправить боту «{command}»? (y/n) ').lower() == 'y':
                await port.send(command)
                transcript.append({'action': f'sent {command}'})
                await asyncio.sleep(2)
            continue
        if choice == 'f':
            if latest is not None and latest.document is not None:
                path = await latest.download_media(file=str(INSPECT_DIR))
                print(f'Файл сохранён: {path}')
                transcript.append({'action': 'saved file'})
            else:
                print('В последнем сообщении нет файла.')
            continue
        if choice.isdigit() and 1 <= int(choice) <= len(buttons):
            button = buttons[int(choice) - 1]
            kind = _kind(button.button)
            if kind not in ('callback', 'text'):
                print('Это ссылка или особая кнопка, её нажимать не нужно.')
                continue
            if _ask(f'Нажать «{button.text}»? (y/n) ').lower() != 'y':
                continue
            transcript.append({'action': f'pressed button {choice}', 'label': anonymize(button.text), 'kind': kind})
            if kind == 'text':
                await port.send(button.text)
            else:
                await port.press(latest.id, int(choice) - 1)
            await asyncio.sleep(2)
            continue
        print('Не понял. Введите номер кнопки, s, r, f или q.')


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
