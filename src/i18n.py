from pathlib import Path
from typing import Any, Dict, Optional

from aiogram.types import TelegramObject, User
from aiogram.utils.i18n import I18n, I18nMiddleware
from aiogram.utils.i18n import gettext as _  # noqa: F401
from aiogram.utils.i18n import lazy_gettext as __  # noqa: F401

from middleware.user import _get_user_lang

I18N_DOMAIN = 'finance'
BASE_DIR = Path(__file__).parent.parent
LOCALES_DIR = BASE_DIR / 'locales'

# Source strings are Russian, so 'ru' is the natural fallback.
i18n = I18n(path=LOCALES_DIR, default_locale='ru', domain=I18N_DOMAIN)


async def get_lang(username: Optional[str]) -> Optional[str]:
    if not username:
        return None
    user = await _get_user_lang(username=username)
    if user:
        return user
    return None


class ACLMiddleware(I18nMiddleware):
    async def get_locale(self, event: TelegramObject, data: Dict[str, Any]) -> str:
        user: Optional[User] = data.get('event_from_user')
        if user is None:
            return self.i18n.default_locale

        return await get_lang(user.username) or user.language_code or self.i18n.default_locale


def setup_middleware(dp) -> ACLMiddleware:
    middleware = ACLMiddleware(i18n=i18n)
    middleware.setup(dp)
    return middleware
