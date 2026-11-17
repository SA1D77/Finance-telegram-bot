"""Adapter for the payment reports of the @clickuz bot. Everything specific to that bot lives here.

What is known and what is not
-----------------------------
Known from the owner: the reports open with /reports and the "Платежи" button; a report covers a period and
has buttons for the previous / next period and a download button. The text of a report is assumed to be the
format the owner pasted earlier ("🗓 Платежи c 17.10.2026 до 24.10.2026" followed by ➖ amount / 🔹 merchant /
💳 card / 🕘 time / ✅ status blocks), read by statements.parser.
NOT known until a real session is inspected (python -m clicksync.inspect): the labels of the period and
download buttons, whether a long period is split into pages, and the format of the downloaded file. The
labels come from settings.Profile and stay empty until then; the file is saved but not parsed.

Safety: the adapter can send exactly one command (the reports command) and press only the buttons named in
the profile. Anything else on the bot's keyboards (payments, transfers...) cannot be reached from here.
"""
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional, Protocol

from statements.parser import parse_statement
from .core import ParsedTx, PeriodReport, mask_account, parse_period
from .engine import SyncError
from .report_file import UnknownReportFormat, parse_report_file
from .settings import Profile

logger = logging.getLogger(__name__)

PRESSABLE = ('callback', 'text')    # an inline callback button, or a reply-keyboard button (sends its label)
TELEGRAM_TEXT_LIMIT = 4096
MAX_PAGES = 100


@dataclass(frozen=True)
class Button:
    text: str
    kind: str   # callback / text / url / other


@dataclass(frozen=True)
class BotMessage:
    id: int
    text: str
    buttons: List[Button]
    has_file: bool = False


class Port(Protocol):
    """The Telegram side: the chat with the source bot as seen by the owner's account."""

    async def send(self, text: str) -> None:
        ...

    async def recent(self, limit: int = 8) -> List[BotMessage]:
        """Latest messages from the bot, newest first."""

    async def press(self, message_id: int, index: int) -> None:
        ...

    async def download(self, message_id: int, directory: Path) -> Path:
        ...


def to_transactions(text: str) -> List[ParsedTx]:
    """Payment blocks of one report message as source transactions."""
    transactions = []
    for payment in parse_statement(text):
        status = 'failed' if payment.failed else ('completed' if payment.confirmed else 'unknown')
        transactions.append(ParsedTx(
            amount=payment.amount, direction='in' if payment.income else 'out', status=status,
            occurred_at=payment.when, currency=payment.currency, description=payment.merchant,
            account=mask_account(payment.card)))
    return transactions


class ClickAdapter:
    def __init__(self, port: Port, profile: Profile, download_dir: Optional[Path] = None, step_delay: float = 2.0,
                 reply_timeout: float = 25.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.port = port
        self.profile = profile
        self.download_dir = download_dir
        self.step_delay = step_delay
        self.reply_timeout = reply_timeout
        self.sleep = sleep
        self.view: Optional[BotMessage] = None

    # --- The only two ways this adapter acts in the chat -----------------------------------

    def _find(self, message: BotMessage, label: str) -> Optional[int]:
        """Index of the single button whose label contains `label`; None when there is none."""
        if not label:
            return None
        matches = [i for i, button in enumerate(message.buttons) if label.casefold() in button.text.casefold()]
        if len(matches) > 1:
            raise SyncError(f'Подпись «{label}» подходит сразу к нескольким кнопкам. Уточните её в src/.env.')
        return matches[0] if matches else None

    async def _press(self, message: BotMessage, label: str) -> None:
        allowed = {self.profile.payments, self.profile.older, self.profile.download, self.profile.next_page} - {''}
        index = self._find(message, label)
        if label not in allowed or index is None:
            raise SyncError(f'Кнопка «{label}» не найдена или не разрешена.')
        button = message.buttons[index]
        if button.kind not in PRESSABLE:
            raise SyncError(f'Кнопка «{label}» не является обычной кнопкой бота, нажимать её нельзя.')
        await self.sleep(self.step_delay)
        if button.kind == 'text':
            await self.port.send(button.text)
        else:
            await self.port.press(message.id, index)

    # --- Waiting for the bot ---------------------------------------------------------------

    async def _wait(self, wanted: Callable[[BotMessage], bool]) -> Optional[BotMessage]:
        waited = 0.0
        while True:
            for message in await self.port.recent():
                if wanted(message):
                    return message
            if waited >= self.reply_timeout:
                return None
            await self.sleep(1.0)
            waited += 1.0

    async def _latest_id(self) -> int:
        messages = await self.port.recent(limit=1)
        return messages[0].id if messages else 0

    # --- Reports ---------------------------------------------------------------------------

    async def open(self) -> PeriodReport:
        if not self.profile.older:
            raise SyncError('Не заполнена подпись кнопки предыдущего периода (CLICK_BTN_OLDER в src/.env). '
                            'Сначала посмотрите реальные кнопки: python -m clicksync.inspect (см. CLICK.md).')
        before = await self._latest_id()
        await self.sleep(self.step_delay)
        await self.port.send(self.profile.reports_command)
        menu = await self._wait(lambda m: m.id > before and self._find(m, self.profile.payments) is not None)
        if menu is None:
            raise SyncError(f'Бот не показал кнопку «{self.profile.payments}» в ответ на {self.profile.reports_command}.')
        await self._press(menu, self.profile.payments)
        view = await self._wait(lambda m: m.id >= menu.id and parse_period(m.text) is not None)
        if view is None:
            raise SyncError('После нажатия «Платежи» не пришёл отчёт с периодом (формат сообщения другой?).')
        self.view = view
        return await self._read()

    async def older(self) -> Optional[PeriodReport]:
        view = self.view
        if view is None:
            raise SyncError('Отчёт не открыт.')
        current = parse_period(view.text)
        if self._find(view, self.profile.older) is None:
            return None     # no "previous period" button: the beginning of the history
        for _attempt in range(2):
            await self._press(view, self.profile.older)
            moved = await self._wait(lambda m: m.id >= view.id and parse_period(m.text) not in (None, current))
            if moved is not None:
                self.view = moved
                return await self._read()
        logger.info('The previous-period button changed nothing: treating it as the end of the history')
        return None

    async def _read(self) -> PeriodReport:
        """Reads the open period: every page of it, and the downloadable file when that is configured."""
        view = self.view
        period = parse_period(view.text)
        texts = [view.text]
        notes: List[str] = []
        while self._find(view, self.profile.next_page) is not None:
            if len(texts) >= MAX_PAGES:
                notes.append('слишком много страниц')
                break
            anchor = view
            await self._press(anchor, self.profile.next_page)
            page = await self._wait(lambda m: m.id >= anchor.id and parse_period(m.text) == period and m.text not in texts)
            if page is None:    # nothing new: the last page (or the same page again)
                break
            texts.append(page.text)
            view = page
        self.view = view

        transactions: List[ParsedTx] = []
        for text in texts:
            found = to_transactions(text)
            if not found and len([line for line in text.splitlines() if line.strip()]) > 3:
                notes.append('не удалось распознать платежи в сообщении (формат изменился?)')
            transactions.extend(found)
        if not self.profile.next_page and any(len(text) >= TELEGRAM_TEXT_LIMIT - 200 for text in texts):
            notes.append('сообщение на пределе длины Telegram, часть платежей могла не поместиться')

        file_note = await self._download(view, period)
        return PeriodReport(start=period[0], end=period[1], transactions=transactions, pages=len(texts),
                            complete=not notes, note='; '.join(notes + ([file_note] if file_note else []))[:255] or None)

    async def _download(self, view: BotMessage, period) -> Optional[str]:
        """Saves the report file next to the session. Its contents are not used until the format is known."""
        if not self.profile.download or self.download_dir is None or self._find(view, self.profile.download) is None:
            return None
        before = await self._latest_id()
        await self._press(view, self.profile.download)
        message = await self._wait(lambda m: m.id > before and m.has_file)
        if message is None:
            return 'файл отчёта не пришёл'
        path = await self.port.download(message.id, self.download_dir)
        try:
            parse_report_file(path)
        except UnknownReportFormat:
            return 'файл отчёта сохранён, но его формат пока не разбирается'
        return None
