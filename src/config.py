import os
from typing import Optional
from urllib.parse import quote

from dotenv import load_dotenv


load_dotenv()


def _parse_owner_id(value: Optional[str]) -> Optional[int]:
    """Numeric Telegram id of the only user allowed to use the bot."""
    if value is None or not value.strip().isdigit():
        return None
    return int(value.strip())


TELEGRAM = os.getenv('TELEGRAM')

OWNER_ID = _parse_owner_id(os.environ.get('OWNER_ID'))

HOST = os.environ.get('HOST')
POSTGRES_DB = os.environ.get('POSTGRES_DB')
POSTGRES_USER = os.environ.get('POSTGRES_USER')
POSTGRES_PASSWORD = os.environ.get('POSTGRES_PASSWORD')
PORT = os.environ.get('PORT')
POSTGRES_DB_TEST = os.environ.get('POSTGRES_DB_TEST')

DB_URL = os.environ.get('DB_URL')

REDIS_HOST = os.environ.get('REDIS_HOST')
REDIS_PORT = os.environ.get('REDIS_PORT')
REDIS_PASSWORD = os.environ.get('REDIS_PASSWORD') or None

DEBUG = int(os.environ.get('DEBUG', '0'))

SMTP_HOST = os.environ.get('SMTP_HOST')
SMTP_PORT = os.environ.get('SMTP_PORT')

SMTP_USER = os.environ.get('SMTP_USER')
SMTP_PASSWORD = os.environ.get('SMTP_PASSWORD')


def _flag(name: str) -> bool:
    """Feature flags: on by default, FEATURE_<NAME>=0 turns one off."""
    return os.environ.get(name, '1').strip() != '0'


FEATURE_QUICK_INPUT = _flag('FEATURE_QUICK_INPUT')
FEATURE_HISTORY = _flag('FEATURE_HISTORY')
FEATURE_BUDGETS = _flag('FEATURE_BUDGETS')
FEATURE_ANALYTICS = _flag('FEATURE_ANALYTICS')
FEATURE_RECURRING = _flag('FEATURE_RECURRING')

# IANA zone used to show and type dates, e.g. Asia/Tashkent or Europe/Moscow.
TIMEZONE = (os.environ.get('TIMEZONE') or 'UTC').strip()

CELERY_HOST = os.environ.get('CELERY_HOST')
CELERY_PORT = os.environ.get('CELERY_PORT')


def celery_broker_url() -> str:
    auth = f':{quote(REDIS_PASSWORD, safe="")}@' if REDIS_PASSWORD else ''
    return f'redis://{auth}{CELERY_HOST}:{CELERY_PORT}'
