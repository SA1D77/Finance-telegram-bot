"""One-time login of the owner's Telegram account for the Click import:

    python -m clicksync.login

The phone number, the login code and the two-step password are typed here, in this terminal, and go straight
to Telegram. They are not stored or printed; only the session file in secrets/clicksync/ is kept.
"""
import asyncio
import os
import sys
from getpass import getpass

from config import OWNER_ID
from .settings import ConfigError, SESSION_PATH
from .telethon_port import make_client


async def main() -> int:
    try:
        client = make_client()
    except ConfigError as error:
        print(error)
        return 1
    print('Вход в Telegram для синхронизации с Click.')
    print('Код придёт в приложение Telegram. Вводите его только здесь, никому не пересылайте.')
    await client.start(
        phone=lambda: input('Номер телефона (с +998...): ').strip(),
        code_callback=lambda: input('Код из Telegram: ').strip(),
        password=lambda: getpass('Пароль двухэтапной проверки (не отображается): '),
    )
    me = await client.get_me()
    await client.disconnect()
    session_file = f'{SESSION_PATH}.session'
    try:
        os.chmod(session_file, 0o600)
    except OSError:     # Windows
        pass
    if me.id != OWNER_ID:
        os.remove(session_file)
        print(f'Это не аккаунт владельца бота (OWNER_ID={OWNER_ID}). Сессия удалена, войдите под своим аккаунтом.')
        return 1
    print('Готово: вход выполнен, сессия сохранена. Отключить её можно в Telegram: Настройки → Устройства.')
    return 0


if __name__ == '__main__':
    sys.exit(asyncio.run(main()))
