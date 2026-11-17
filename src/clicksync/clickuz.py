"""Adapter for the payment reports of the @clickuz bot. Everything specific to that bot lives here.

The real flow (inspected on 17.11.2026 with python -m clicksync.inspect)
-------------------------------------------------------------------------
1. "/reports" -> "✅ Мониторинг карт активен" with the inline buttons "💸 Платежи" and "📊 Мониторинг карт".
2. "💸 Платежи" -> the weekly report "🗓 Платежи c 10.11.2026 до 17.11.2026", five payments per page
   (➖/➕ amount, 🔹 merchant, 💳 card, 🕘 time, ✅ status) and the buttons "◀️ 1️⃣ ▶️" (pages of this week,
   not other periods), "💳", "⬇️ Скачать", "⬅️ Назад".
3. "⬇️ Скачать" -> "Выберите месяц 👇" with one button per month ("2026.11" ... "2025.12", a year back).
4. A month -> "⏳ Пожалуйста, подождите..." and some minutes later a file click-payments-<id>-<YYYYMM>.xlsx
   with the caption "🗓 История платежей с 01.11.2026 по 30.11.2026" (read by report_file).

So a period here is a calendar month, read from its file. The file has no sign and no currency: both are
taken from the weekly report (all its pages are read first) for the payments it shows; older payments keep
the direction 'unknown' and the owner picks it when classifying. A month missing from the list is the
beginning of the history Click keeps.

Safety: the adapter sends exactly one command (the reports command) and presses only "Платежи", the
next-page arrow, "Скачать" and month buttons ("YYYY.MM"). Payments, transfers and anything else on the
bot's keyboards cannot be reached from here.
"""
import asyncio
import logging
import re
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional, Protocol, Set, Tuple

from statements.parser import parse_statement
from .balance import BalanceReply, is_balance_reply, parse_balance_reply
from .core import ParsedTx, PeriodReport, mask_account, parse_period
from .engine import SyncError
from .report_file import UnknownReportFormat, parse_report_file
from .settings import Profile

logger = logging.getLogger(__name__)

PRESSABLE = ('callback', 'text')    # an inline callback button, or a reply-keyboard button (sends its label)
MAX_PAGES = 100
MONTH_LABEL = re.compile(r'^\s*(\d{4})\.(\d{2})\s*$')


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


def _match_key(tx: ParsedTx) -> tuple:
    """What a payment of the weekly message and the same row of the file have in common."""
    minute = tx.occurred_at.replace(second=0, microsecond=0) if tx.occurred_at else None
    return (tx.account or '', minute, tx.amount, ' '.join((tx.description or '').casefold().split()))


def previous_month(month: Tuple[int, int]) -> Tuple[int, int]:
    year, number = month
    return (year - 1, 12) if number == 1 else (year, number - 1)


class ClickAdapter:
    def __init__(self, port: Port, profile: Profile, download_dir: Optional[Path] = None, step_delay: float = 2.0,
                 reply_timeout: float = 25.0, file_timeout: float = 600.0,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.port = port
        self.profile = profile
        self.download_dir = download_dir
        self.step_delay = step_delay
        self.reply_timeout = reply_timeout
        self.file_timeout = file_timeout
        self.sleep = sleep
        self.view: Optional[BotMessage] = None
        self.month: Optional[Tuple[int, int]] = None
        self.directions: Dict[tuple, Set[str]] = {}
        self.currencies: Dict[str, Set[str]] = {}
        self.view_note: Optional[str] = None

    # --- The only two ways this adapter acts in the chat -----------------------------------

    def _find(self, message: BotMessage, label: str) -> Optional[int]:
        """Index of the single button whose label contains `label`; None when there is none."""
        if not label:
            return None
        matches = [i for i, button in enumerate(message.buttons) if label.casefold() in button.text.casefold()]
        if len(matches) > 1:
            raise SyncError(f'Подпись «{label}» подходит сразу к нескольким кнопкам. Уточните её в src/.env.')
        return matches[0] if matches else None

    def _allowed(self, label: str) -> bool:
        named = {self.profile.payments, self.profile.download, self.profile.next_page} - {''}
        return label in named or MONTH_LABEL.match(label) is not None

    async def _press(self, message: BotMessage, label: str) -> None:
        index = self._find(message, label)
        if not self._allowed(label) or index is None:
            raise SyncError(f'Кнопка «{label}» не найдена или не разрешена.')
        button = message.buttons[index]
        if button.kind not in PRESSABLE:
            raise SyncError(f'Кнопка «{label}» не является обычной кнопкой бота, нажимать её нельзя.')
        if MONTH_LABEL.match(label) and not MONTH_LABEL.match(button.text):
            raise SyncError(f'Кнопка «{button.text}» не похожа на кнопку месяца.')
        await self.sleep(self.step_delay)
        if button.kind == 'text':
            await self.port.send(button.text)
        else:
            await self.port.press(message.id, index)

    # --- Waiting for the bot ---------------------------------------------------------------

    async def _wait(self, wanted: Callable[[BotMessage], bool], timeout: Optional[float] = None) -> Optional[BotMessage]:
        limit = self.reply_timeout if timeout is None else timeout
        waited = 0.0
        while True:
            for message in await self.port.recent():
                if wanted(message):
                    return message
            if waited >= limit:
                return None
            pause = 1.0 if waited < 20 else 5.0     # a long wait (the file) asks Telegram less often
            await self.sleep(pause)
            waited += pause

    async def _latest_id(self) -> int:
        messages = await self.port.recent(limit=1)
        return messages[0].id if messages else 0

    # --- Balances --------------------------------------------------------------------------

    async def read_balance(self) -> Tuple[BalanceReply, int]:
        """Sends the balance command and reads the reply to THIS request.

        Only a message newer than the last one before the request counts, so an old /balance reply is never
        reused. If the bot edits its reply (a "loading" text first, or the numbers updated), the text is read
        again until it stops changing. Raises BalanceRejected for a reply that cannot be read safely.
        """
        before = await self._latest_id()
        await self.sleep(self.step_delay)
        await self.port.send(self.profile.balance_command)
        message = await self._wait(lambda m: m.id > before and is_balance_reply(m.text))
        if message is None:
            raise SyncError(f'Click не ответил на {self.profile.balance_command}.')
        settled = message
        for _attempt in range(3):
            await self.sleep(1.0)
            again = next((m for m in await self.port.recent() if m.id == message.id), None)
            if again is None or again.text == settled.text:
                break
            settled = again
        return parse_balance_reply(settled.text), settled.id

    # --- Reports ---------------------------------------------------------------------------

    async def open(self) -> PeriodReport:
        before = await self._latest_id()
        await self.sleep(self.step_delay)
        await self.port.send(self.profile.reports_command)
        menu = await self._wait(lambda m: m.id > before and self._find(m, self.profile.payments) is not None)
        if menu is None:
            raise SyncError(f'Бот не показал кнопку «{self.profile.payments}» в ответ на {self.profile.reports_command}.')
        await self._press(menu, self.profile.payments)
        view = await self._wait(lambda m: m.id >= menu.id and parse_period(m.text) is not None)
        if view is None:
            raise SyncError(f'После нажатия «{self.profile.payments}» не пришёл отчёт с периодом (формат сообщения другой?).')
        self.view = view
        await self._learn_from_view()
        period_end = parse_period(view.text)[1]
        self.month = (period_end.year, period_end.month)
        report = await self._month(self.month)
        if report is None:
            raise SyncError(f'В списке месяцев для скачивания нет текущего ({period_end:%m.%Y}).')
        return report

    async def older(self) -> Optional[PeriodReport]:
        if self.view is None or self.month is None:
            raise SyncError('Отчёт не открыт.')
        month = previous_month(self.month)
        report = await self._month(month)
        if report is not None:
            self.month = month
        return report

    async def _learn_from_view(self) -> None:
        """Reads every page of the weekly report: the signs and currencies the monthly file does not have."""
        view = self.view
        period = parse_period(view.text)
        texts = [view.text]
        while self._find(view, self.profile.next_page) is not None and len(texts) < MAX_PAGES:
            anchor = view
            await self._press(anchor, self.profile.next_page)
            page = await self._wait(lambda m: m.id >= anchor.id and parse_period(m.text) == period and m.text not in texts)
            if page is None:    # nothing new: that was the last page
                break
            texts.append(page.text)
            view = page
        self.view = view
        unreadable = False
        for text in texts:
            found = to_transactions(text)
            if not found and len([line for line in text.splitlines() if line.strip()]) > 3:
                unreadable = True
            for tx in found:
                self.directions.setdefault(_match_key(tx), set()).add(tx.direction)
                if tx.currency:
                    self.currencies.setdefault(tx.account or '', set()).add(tx.currency)
        self.view_note = 'не удалось прочитать недельный отчёт: знаки платежей неизвестны' if unreadable else None

    def _currency(self, account: Optional[str]) -> str:
        own = self.currencies.get(account or '', set())
        every = set().union(*self.currencies.values()) if self.currencies else set()
        for candidates in (own, every):
            if len(candidates) == 1:
                return next(iter(candidates))
        if self.profile.currency:
            return self.profile.currency
        raise SyncError('В файле Click нет валюты, а в недельном отчёте не нашлось платежей, по которым её узнать. '
                        'Укажите её в src/.env: CLICK_CURRENCY=UZS')

    def _complete(self, tx: ParsedTx) -> ParsedTx:
        tx = replace(tx, currency=self._currency(tx.account))
        if tx.direction == 'unknown':
            seen = self.directions.get(_match_key(tx), set())
            if len(seen) == 1:
                tx = replace(tx, direction=next(iter(seen)))
        return tx

    async def _month(self, month: Tuple[int, int]) -> Optional[PeriodReport]:
        """Downloads and reads the file of one month; None when Click does not offer that month."""
        view = self.view
        if self._find(view, self.profile.download) is None:
            raise SyncError(f'Под отчётом нет кнопки «{self.profile.download}».')
        before = await self._latest_id()
        await self._press(view, self.profile.download)
        months = await self._wait(lambda m: m.id > before and any(MONTH_LABEL.match(b.text) for b in m.buttons))
        if months is None:
            raise SyncError(f'После «{self.profile.download}» бот не предложил выбрать месяц.')
        label = f'{month[0]}.{month[1]:02d}'
        if self._find(months, label) is None:
            return None     # Click keeps no older months
        await self._press(months, label)
        first_day = date(month[0], month[1], 1)

        def is_the_file(m: BotMessage) -> bool:
            return m.id > months.id and m.has_file and (parse_period(m.text) or (None,))[0] == first_day

        message = await self._wait(is_the_file, timeout=self.file_timeout)
        if message is None:
            raise SyncError(f'Click не прислал файл за {month[1]:02d}.{month[0]} (ждал {int(self.file_timeout)} с).')
        if self.download_dir is None:
            raise SyncError('Не задана папка для файлов отчётов.')
        path = await self.port.download(message.id, self.download_dir)
        try:
            parsed = parse_report_file(path)
        except UnknownReportFormat as error:
            raise SyncError(f'Файл Click за {month[1]:02d}.{month[0]} другого формата: {error}') from None
        finally:
            try:
                Path(path).unlink()     # the rows are in the ledger now; no second copy of the statement on disk
            except OSError:
                pass
        start, end = parse_period(message.text)
        transactions = [self._complete(tx) for tx in parsed.transactions]
        notes = [f'не прочитано строк файла: {parsed.unreadable}'] if parsed.unreadable else []
        if self.view_note:
            notes.append(self.view_note)
        logger.info('Click month %s: %d rows, %d with a known direction', label, len(transactions),
                    sum(tx.direction != 'unknown' for tx in transactions))
        return PeriodReport(start=start, end=end, transactions=transactions, pages=1, complete=not parsed.unreadable,
                            note='; '.join(notes)[:255] or None)
