"""Settings of the @clickuz import, read from src/.env (see src/.env.example).

The button labels default to what a real @clickuz session showed (python -m clicksync.inspect); the time zone
of the report has no default.
"""
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _number(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name) or default)
    except ValueError:
        return default


API_ID = (os.environ.get('TG_API_ID') or '').strip()
API_HASH = (os.environ.get('TG_API_HASH') or '').strip()
BOT = (os.environ.get('CLICK_BOT') or 'clickuz').strip().lstrip('@')
# Zone the times in the Click reports are printed in. Required: it is never assumed.
TIMEZONE = (os.environ.get('CLICK_TIMEZONE') or '').strip()
INTERVAL_MINUTES = max(int(_number('CLICK_SYNC_INTERVAL_MIN', 180)), 5)
OVERLAP_DAYS = max(int(_number('CLICK_OVERLAP_DAYS', 7)), 0)
STEP_DELAY = max(_number('CLICK_STEP_DELAY', 2.0), 0.5)     # pause between button presses, seconds
REPLY_TIMEOUT = max(_number('CLICK_REPLY_TIMEOUT', 25.0), 5.0)
# Click prepares the monthly file for a few minutes ("Пожалуйста, подождите..."): about 3 minutes seen.
FILE_TIMEOUT = max(_number('CLICK_FILE_TIMEOUT', 600.0), 30.0)
# Session file and downloaded reports: outside git (see .gitignore), readable only by the owner.
DATA_DIR = Path(os.environ.get('CLICK_DATA_DIR') or PROJECT_ROOT / 'secrets' / 'clicksync')
SESSION_PATH = DATA_DIR / 'telegram'    # Telethon adds .session


class ConfigError(Exception):
    """Message in Russian for the person setting the import up."""


@dataclass(frozen=True)
class Profile:
    """What to send and which buttons may be pressed. A button is matched by a part of its label.

    Defaults are the labels of the real @clickuz flow (seen 17.11.2026): /reports -> "💸 Платежи" -> the weekly
    report with "◀️ 1️⃣ ▶️" pages and "⬇️ Скачать" -> "Выберите месяц" with "2026.11"-style buttons -> .xlsx.
    """
    reports_command: str = '/reports'
    payments: str = 'Платежи'
    download: str = 'Скачать'
    next_page: str = '▶'
    currency: str = ''      # fallback when the weekly report shows no payment of a card


def profile() -> Profile:
    return Profile(
        reports_command=(os.environ.get('CLICK_CMD_REPORTS') or '/reports').strip(),
        payments=(os.environ.get('CLICK_BTN_PAYMENTS') or 'Платежи').strip(),
        download=(os.environ.get('CLICK_BTN_DOWNLOAD') or 'Скачать').strip(),
        next_page=(os.environ.get('CLICK_BTN_NEXT_PAGE') or '▶').strip(),
        currency=(os.environ.get('CLICK_CURRENCY') or '').strip().upper(),
    )


def zone() -> ZoneInfo:
    if not TIMEZONE:
        raise ConfigError('Не задан CLICK_TIMEZONE в src/.env: часовой пояс, в котором Click печатает время в отчётах '
                          '(например Asia/Tashkent).')
    try:
        return ZoneInfo(TIMEZONE)
    except (ZoneInfoNotFoundError, ValueError):
        raise ConfigError(f'Неизвестный часовой пояс в CLICK_TIMEZONE: {TIMEZONE}') from None


def to_utc(printed: datetime, tz: Optional[ZoneInfo] = None) -> datetime:
    """Time as printed in the report -> naive UTC, the format of accounting.created_at."""
    return printed.replace(tzinfo=tz or zone()).astimezone(timezone.utc).replace(tzinfo=None)


def from_utc(utc_naive: datetime, tz: Optional[ZoneInfo] = None) -> datetime:
    return utc_naive.replace(tzinfo=timezone.utc).astimezone(tz or zone()).replace(tzinfo=None)


def api_credentials() -> tuple:
    if not API_ID.isdigit() or not API_HASH:
        raise ConfigError('Не заданы TG_API_ID и TG_API_HASH в src/.env (получить: https://my.telegram.org → API development tools).')
    return int(API_ID), API_HASH


def prepare_data_dir() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(DATA_DIR, 0o700)
    except OSError:     # Windows
        pass
    return DATA_DIR
