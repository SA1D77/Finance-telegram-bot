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
FEATURE_GOALS = _flag('FEATURE_GOALS')
FEATURE_EXPORT = _flag('FEATURE_EXPORT')
FEATURE_BACKUP = _flag('FEATURE_BACKUP')
FEATURE_CURRENCIES = _flag('FEATURE_CURRENCIES')


def _off_flag(name: str) -> bool:
    """Off by default: FEATURE_<NAME>=1 turns it on."""
    return os.environ.get(name, '0').strip() == '1'


# Offline recognition, off by default because it adds large components to the image.
FEATURE_VOICE = _off_flag('FEATURE_VOICE')
FEATURE_RECEIPTS = _off_flag('FEATURE_RECEIPTS')
VOSK_MODEL_PATH = os.environ.get('VOSK_MODEL_PATH') or '/opt/vosk-model'
TESSERACT_LANG = os.environ.get('TESSERACT_LANG') or 'rus+eng'

# Main currency (ISO code such as UZS, RUB, USD). Amounts in other currencies are converted into it;
# leave empty to keep the bot single-currency.
BASE_CURRENCY = (os.environ.get('BASE_CURRENCY') or '').strip().upper()


def currencies_active() -> bool:
    return FEATURE_CURRENCIES and len(BASE_CURRENCY) == 3 and BASE_CURRENCY.isalpha()


# Database backups (pg_dump in the celery container, scheduled by beat).
BACKUP_DIR = os.environ.get('BACKUP_DIR') or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backups')
BACKUP_HOUR = int(os.environ.get('BACKUP_HOUR') or 3)
BACKUP_KEEP = int(os.environ.get('BACKUP_KEEP') or 7)
BACKUP_TO_TELEGRAM = _flag('BACKUP_TO_TELEGRAM')
BACKUP_PG_DUMP = os.environ.get('BACKUP_PG_DUMP') or 'pg_dump'

# IANA zone used to show and type dates, e.g. Asia/Tashkent or Europe/Moscow.
TIMEZONE = (os.environ.get('TIMEZONE') or 'UTC').strip()

CELERY_HOST = os.environ.get('CELERY_HOST')
CELERY_PORT = os.environ.get('CELERY_PORT')


def celery_broker_url() -> str:
    auth = f':{quote(REDIS_PASSWORD, safe="")}@' if REDIS_PASSWORD else ''
    return f'redis://{auth}{CELERY_HOST}:{CELERY_PORT}'
