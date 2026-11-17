"""Balance reconciliation: opening + incoming - outgoing - fees = closing, per account and currency.

Nothing here invents numbers: without both balances the result is `unavailable`, and when the data leaves
room for more than one reading (unknown direction, fees that may or may not be inside the amounts, payments
that are not final) the result is `indeterminate` rather than a guess.
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

ZERO = Decimal('0')


@dataclass
class BalanceResult:
    status: str                         # ok / mismatch / indeterminate / unavailable
    expected: Optional[Decimal] = None  # closing balance computed from the transactions
    reported: Optional[Decimal] = None
    difference: Optional[Decimal] = None    # reported - expected
    reason: Optional[str] = None
    candidates: List[Any] = field(default_factory=list)   # transactions that may explain a difference
    waiting: bool = False   # payments still in progress: the balance is expected to move, nothing to ask yet


def _movement(transactions: Iterable[Any], subtract_unknown_fees: bool) -> Tuple[Decimal, bool]:
    """Net change of the balance from completed transactions and whether any fee placement is unknown."""
    net, unknown_fee = ZERO, False
    for tx in transactions:
        net += tx.amount if tx.direction == 'in' else -tx.amount
        fee = tx.fee or ZERO
        if not fee:
            continue
        if tx.fee_included is None:
            unknown_fee = True
            if subtract_unknown_fees:
                net -= fee
        elif not tx.fee_included:   # charged on top of the amount; an included fee must not be taken twice
            net -= fee
    return net, unknown_fee


def balance_check(opening: Optional[Decimal], closing: Optional[Decimal], transactions: Iterable[Any]) -> BalanceResult:
    """Checks one account in one currency over one period. `transactions` have ParsedTx-like fields."""
    if opening is None or closing is None:
        return BalanceResult(status='unavailable',
                             reason='источник не сообщает начальный и конечный баланс за период')
    transactions = list(transactions)
    completed = [tx for tx in transactions if tx.status == 'completed']
    open_ended = [tx for tx in transactions if tx.status in ('pending', 'reversed', 'unknown')]
    if any(tx.direction not in ('in', 'out') for tx in completed):
        return BalanceResult(status='indeterminate', reported=closing,
                             reason='у части операций неизвестно направление (приход или расход)')

    with_fees, unknown_fee = _movement(completed, subtract_unknown_fees=True)
    expected = opening + with_fees
    if expected == closing:
        return BalanceResult(status='ok', expected=expected, reported=closing, difference=ZERO)
    if unknown_fee:
        without_fees, _ = _movement(completed, subtract_unknown_fees=False)
        if opening + without_fees == closing:
            return BalanceResult(status='ok', expected=closing, reported=closing, difference=ZERO,
                                 reason='комиссии уже включены в суммы операций')

    difference = closing - expected
    candidates = [tx for tx in transactions if tx.amount == abs(difference) or (tx.fee and tx.fee == abs(difference))]
    candidates += [tx for tx in open_ended if tx not in candidates]
    if open_ended or unknown_fee:
        reason = 'есть незавершённые операции' if open_ended else 'неизвестно, включены ли комиссии в суммы'
        return BalanceResult(status='indeterminate', expected=expected, reported=closing, difference=difference,
                             reason=reason, candidates=candidates, waiting=bool(open_ended))
    return BalanceResult(status='mismatch', expected=expected, reported=closing, difference=difference,
                         candidates=candidates)


@dataclass
class Totals:
    income: Decimal = ZERO
    expense: Decimal = ZERO
    fees: Decimal = ZERO                # only fees charged on top of the amounts
    transfers_in: Decimal = ZERO        # between the user's own accounts: not income
    transfers_out: Decimal = ZERO       # ... and not an expense
    unsigned: Decimal = ZERO            # completed, direction not known yet: shown apart, never guessed
    count: int = 0


def totals_by_account(transactions: Iterable[Any]) -> Dict[Tuple[Optional[str], Optional[str]], Totals]:
    """Completed transactions summed per (account, currency); own transfers are kept out of income/expense."""
    result: Dict[Tuple[Optional[str], Optional[str]], Totals] = {}
    for tx in transactions:
        if tx.status != 'completed':
            continue
        totals = result.setdefault((tx.account, tx.currency), Totals())
        totals.count += 1
        if tx.direction not in ('in', 'out'):
            totals.unsigned += tx.amount
            continue
        incoming = tx.direction == 'in'
        if getattr(tx, 'own_transfer', False):
            if incoming:
                totals.transfers_in += tx.amount
            else:
                totals.transfers_out += tx.amount
        elif incoming:
            totals.income += tx.amount
        else:
            totals.expense += tx.amount
        if tx.fee and tx.fee_included is False:
            totals.fees += tx.fee
    return result


def consolidated(per_account: Dict[Tuple[Optional[str], Optional[str]], Totals]) -> Dict[Optional[str], Totals]:
    """Income and expenses over all accounts, per currency (currencies are never added together)."""
    result: Dict[Optional[str], Totals] = {}
    for (_account, currency), totals in per_account.items():
        total = result.setdefault(currency, Totals())
        total.income += totals.income
        total.expense += totals.expense
        total.fees += totals.fees
        total.transfers_in += totals.transfers_in
        total.transfers_out += totals.transfers_out
        total.unsigned += totals.unsigned
        total.count += totals.count
    return result
