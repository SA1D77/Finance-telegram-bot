"""Card balance checks without a database or Telegram: the /balance reply, the reconciliation of an interval,
pairing of snapshots, matching of explanations, the adapter on a fake chat and the worker's schedule."""
from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from clicksync import worker
from clicksync.balance import BalanceRejected, parse_amount, parse_balance_reply
from clicksync.balance_recon import (IntervalFacts, Movement, Snap, card_problem, explanation_effect, match_candidates,
                                     pair_snapshots, reconcile)
from clicksync.balance_texts import question, review_text, span
from clicksync.clickuz import ClickAdapter
from clicksync.engine import SyncError
from clicksync.settings import Profile

D = Decimal
REPLY = "Общий баланс:\n2'244'986.91 сум\n\nВаши карты:\n💳 HAMK***6104 ⭐️\n      2'244'986.91 сум"
TWO = ("Общий баланс:\n1 500 000 сум\n\nВаши карты:\n💳 HAMK***6104\n1 000 000 сум\n"
       "💳 UZCARD 8600 **** **** 1234\n500 000 сум")


# --- The /balance reply ------------------------------------------------------------------------------

@pytest.mark.parametrize('raw, value', [
    ("2'244'986.91", D('2244986.91')), ('2 244 986,91', D('2244986.91')), ('2 244 986', D('2244986')),
    ('1.234.567,5', D('1234567.5')), ('1,234,567.50', D('1234567.50')), ('0', D('0')), ('-1 500', D('-1500')),
    ('950', D('950')), ('12.5', D('12.5')),
])
def test_amounts(raw, value):
    assert parse_amount(raw) == value


@pytest.mark.parametrize('raw', ['1,000', '1.000', '***', '•••• ', 'скрыт', '12.345', '1.2.3', '12 34', '1,234.567', '', 'abc',
                                 '1.234,567'])
def test_ambiguous_or_hidden_amounts_are_rejected(raw):
    with pytest.raises(BalanceRejected):
        parse_amount(raw)


def test_reply_with_one_card():
    reply = parse_balance_reply(REPLY)
    assert reply.total == {'UZS': D('2244986.91')}
    [card] = reply.cards
    assert (card.label, card.last4, card.amount, card.currency) == ('HAMK***6104', '6104', D('2244986.91'), 'UZS')


def test_reply_with_several_cards_keeps_the_total_apart():
    reply = parse_balance_reply(TWO)
    assert [c.last4 for c in reply.cards] == ['6104', '1234'] and reply.total == {'UZS': D('1500000')}
    assert sum(c.amount for c in reply.cards) == reply.total['UZS']


def test_full_card_number_is_masked():
    reply = parse_balance_reply("Общий баланс:\n100 сум\nВаши карты:\nКарта 8600123412341234\n100 сум")
    assert '12341234' not in reply.cards[0].label and reply.cards[0].last4 == '1234'


def test_same_last_digits_are_marked_ambiguous():
    reply = parse_balance_reply("Общий баланс:\n300 сум\nВаши карты:\nA ***6104\n100 сум\nB ***6104\n200 сум")
    assert reply.ambiguous_last4 == ['6104']


@pytest.mark.parametrize('text', [
    None, 'Неизвестная команда',
    "Общий баланс:\n*** сум\nВаши карты:\n💳 HAMK***6104\n*** сум",              # hidden balance
    "Общий баланс:\n100 сум\nВаши карты:\n💳 HAMK***6104",                        # card without an amount
    "Общий баланс:\n100 сум\nВаши карты:",                                        # no card
    "Общий баланс:\nВаши карты:\n💳 HAMK***6104\n100 сум",                        # no total
    "Общий баланс:\n150 сум\nВаши карты:\n💳 HAMK***6104\n100 сум",               # cards do not add up
    "Общий баланс:\n100 сум\nВаши карты:\n💳 HAMK без номера\n100 сум",           # no masked number
    "Общий баланс:\n100 тугриков\nВаши карты:\n💳 HAMK***6104\n100 тугриков",     # unknown currency
    "Общий баланс:\n1,000 сум\nВаши карты:\n💳 HAMK***6104\n1,000 сум",           # ambiguous amount
])
def test_bad_replies_are_rejected_never_zero(text):
    with pytest.raises(BalanceRejected):
        parse_balance_reply(text)


# --- Reconciliation of one interval ------------------------------------------------------------------

T0 = datetime(2026, 11, 15, 16, 0)
T1 = datetime(2026, 11, 17, 4, 0)
GRACE = timedelta(hours=24)
BOUNDARY = timedelta(minutes=10)
LATER = T1 + timedelta(days=3)


def mv(id, amount, direction='out', status='completed', at=T0 + timedelta(hours=5), fee=None, fee_included=None):
    return Movement(id=id, amount=D(amount), direction=direction, status=status, at=at, fee=None if fee is None else D(fee),
                    fee_included=fee_included)


def facts(closing, movements=(), opening='1000000', explained='0', read_at=LATER, **kw):
    return IntervalFacts(start_at=T0, end_at=T1, opening=D(opening), closing=D(closing), movements=list(movements),
                         explained=D(explained), report_read_at=read_at, **kw)


def run(f, now=LATER):
    return reconcile(f, now, GRACE, BOUNDARY)


def test_exact_match_is_ok():
    r = run(facts('949000', [mv(1, '50000'), mv(2, '1000', 'in'), mv(3, '2000')]))
    assert (r.status, r.expected, r.remaining, sorted(r.used)) == ('ok', D('949000'), D('0'), [1, 2, 3])


def test_unexplained_decrease_and_increase():
    down = run(facts('950000'))
    assert (down.status, down.remaining) == ('unexplained', D('-50000'))
    up = run(facts('1020000'))
    assert (up.status, up.remaining) == ('unexplained', D('20000'))


def test_fee_on_top_included_and_unknown():
    assert run(facts('948500', [mv(1, '50000', fee='1500', fee_included=False)])).status == 'ok'
    assert run(facts('950000', [mv(1, '50000', fee='1500', fee_included=True)])).status == 'ok'
    unknown = run(facts('950000', [mv(1, '50000', fee='1500', fee_included=None)]))
    assert unknown.status == 'ok' and 'комиссия уже входит' in unknown.reason


def test_failed_never_counts_and_pending_is_tried_both_ways():
    assert run(facts('1000000', [mv(1, '50000', status='failed')])).status == 'ok'
    not_yet = run(facts('1000000', [mv(1, '50000', status='pending')]))
    assert not_yet.status == 'ok' and 'в обработке' in not_yet.reason
    assert run(facts('950000', [mv(1, '50000', status='pending')])).status == 'ok'
    other = run(facts('900000', [mv(1, '50000', status='pending')]))
    assert other.status == 'uncertain' and 'в обработке' in other.reason


def test_reversed_is_tried_as_not_moved_and_moved():
    assert run(facts('1000000', [mv(1, '50000', status='reversed')])).status == 'ok'
    moved = run(facts('950000', [mv(1, '50000', status='reversed')]))
    assert moved.status == 'ok' and 'отменённый' in moved.reason


def test_unknown_direction_makes_it_uncertain():
    r = run(facts('950000', [mv(1, '50000', direction='unknown')]))
    assert r.status == 'uncertain' and 'направление' in r.reason


def test_own_transfer_counts_as_a_movement_of_this_card():
    assert run(facts('500000', [mv(1, '500000')])).status == 'ok'


def test_waiting_inside_the_grace_period_and_until_a_report_after_it():
    assert run(facts('950000'), now=T1 + timedelta(hours=2)).status == 'waiting'
    assert run(facts('950000', read_at=T1 + timedelta(hours=1)), now=T1 + timedelta(hours=30)).status == 'waiting'
    stale = run(facts('950000', read_at=None), now=T1 + timedelta(days=5))
    assert stale.status == 'uncertain' and 'не был прочитан' in stale.reason


def test_incomplete_report_is_uncertain_not_unexplained():
    r = run(facts('950000', report_problem='отчёт Click за 01.11–30.11.2026 прочитан не полностью'), now=T1 + timedelta(days=5))
    assert r.status == 'uncertain' and 'не полностью' in r.reason


def test_delayed_payment_closes_the_interval_on_recompute():
    assert run(facts('950000')).status == 'unexplained'
    assert run(facts('950000', [mv(7, '50000')])).status == 'ok'


def test_boundary_payment_makes_it_uncertain():
    near_end = mv(1, '50000', at=T1 + timedelta(minutes=3))       # printed just after the check
    r = run(facts('950000', [near_end]))
    assert r.status == 'uncertain' and 'соседний промежуток' in r.reason
    near_start = mv(2, '50000', at=T0 + timedelta(minutes=2))     # counted, but the opening may include it
    assert run(facts('1000000', [near_start])).status == 'uncertain'


def test_card_problems_come_first():
    r = run(facts('949000', [mv(1, '51000')], card_problem='в ответе /balance несколько карт оканчиваются на 6104'))
    assert r.status == 'uncertain'
    assert card_problem(None, False, []) and card_problem('6104', True, [])
    assert 'разные карты' in card_problem('6104', False, ['561468******6104', '860012******6104'])
    assert card_problem('6104', False, ['561468******6104', None])
    assert card_problem('6104', False, ['561468******6104', '561468******6104']) is None


def test_explanations_partly_and_fully():
    part = run(facts('950000', explained='-20000'))
    assert (part.status, part.remaining) == ('unexplained', D('-30000'))
    full = run(facts('950000', explained='-50000'))
    assert (full.status, full.remaining) == ('explained', D('0'))


def test_explained_payment_appearing_later_is_not_counted_twice():
    # explained by the owner, then the payment shows up in the report but is not linked yet
    r = run(facts('950000', [mv(7, '50000')], explained='-50000'))
    assert r.status == 'uncertain' and 'появился в отчёте' in r.reason
    # once linked, the matched part of the explanation no longer counts
    effect = explanation_effect('out', D('50000'), matched=D('50000'))
    assert effect == 0 and run(facts('950000', [mv(7, '50000')], explained=effect)).status == 'ok'
    assert explanation_effect('out', D('50000'), matched=D('20000')) == D('-30000')
    assert explanation_effect('in', D('50000'), matched=D('0')) == D('50000')


# --- Matching explanations to payments -----------------------------------------------------------------

def test_only_unique_exact_pairs_are_linked_automatically():
    assert match_candidates([(1, 'out', D('50000'))], [(7, 'out', D('50000'))]) == [(1, 7)]
    assert match_candidates([(1, 'out', D('50000'))], [(7, 'in', D('50000'))]) == []            # direction
    assert match_candidates([(1, 'out', D('50000'))], [(7, 'out', D('20000'))]) == []           # partial: owner decides
    assert match_candidates([(1, 'out', D('50000'))], [(7, 'out', D('50000')), (8, 'out', D('50000'))]) == []
    assert match_candidates([(1, 'out', D('50000')), (2, 'out', D('50000'))], [(7, 'out', D('50000'))]) == []
    assert match_candidates([(1, 'out', D('0'))], [(7, 'out', D('0'))]) == []


# --- Pairing snapshots into intervals --------------------------------------------------------------------

def snap(id, observation, card, at, last4='6104', currency='UZS'):
    return Snap(id=id, observation_id=observation, card=card, last4=last4, currency=currency, observed_at=at)


def test_consecutive_snapshots_of_the_same_card_pair_up_and_the_first_is_a_baseline():
    t = [T0 + timedelta(hours=h) for h in range(4)]
    snaps = [snap(1, 1, 'A***6104', t[0]), snap(2, 1, 'B***1234', t[0], '1234'),
             snap(3, 2, 'A***6104', t[1]), snap(4, 3, 'A***6104', t[2]), snap(5, 3, 'B***1234', t[2], '1234'),
             snap(6, 3, 'C***9999', t[2], '9999')]
    assert sorted(pair_snapshots(snaps)) == [(1, 3), (2, 5), (3, 4)]     # C is new: baseline only


def test_renamed_card_is_followed_by_its_unique_last_digits_only():
    t = [T0, T0 + timedelta(hours=1)]
    assert pair_snapshots([snap(1, 1, 'OLD***6104', t[0]), snap(2, 2, 'NEW***6104', t[1])]) == [(1, 2)]
    two = [snap(1, 1, 'A***6104', t[0]), snap(2, 1, 'B***6104', t[0]), snap(3, 2, 'C***6104', t[1])]
    assert pair_snapshots(two) == []                                        # ambiguous: new baseline
    other_currency = [snap(1, 1, 'A***6104', t[0], currency='USD'), snap(2, 2, 'A***6104', t[1])]
    assert pair_snapshots(other_currency) == []                             # never mixes currencies


# --- Texts -------------------------------------------------------------------------------------------------

def test_question_describes_the_real_interval():
    interval = SimpleNamespace(remaining=D('-50000'), last4='6104', card='HAMK***6104', currency='UZS',
                               start_at=datetime(2026, 11, 15, 21, 0), end_at=datetime(2026, 11, 17, 9, 0))
    text = question(interval, lambda value: value).replace(' ', ' ').replace(' ', ' ')
    assert text == ('По карте ••••6104 с 15.11.2026 21:00 до 17.11.2026 09:00 осталось на 50 000 сум меньше, '
                    'чем получается по операциям. На что ушли деньги?')
    assert span(datetime(2026, 11, 17, 9, 0), datetime(2026, 11, 17, 12, 0)) == '17.11.2026 с 09:00 до 12:00'
    interval.remaining = D('20000')
    assert 'Откуда пришли деньги?' in question(interval, lambda value: value)


def test_review_groups_by_card():
    a = SimpleNamespace(remaining=D('-50000'), last4='6104', card='', currency='UZS', start_at=T0, end_at=T1, status='unexplained')
    b = SimpleNamespace(remaining=D('1000'), last4='1234', card='', currency='UZS', start_at=T0, end_at=T1, status='unexplained')
    c = SimpleNamespace(remaining=None, last4='6104', card='', currency='UZS', start_at=T0, end_at=T1, status='waiting')
    text = review_text([a, b], [c], lambda value: value)
    assert text.count('💳 ••••6104') == 1 and '💳 ••••1234' in text and 'жду отчёт: 1' in text


# --- The adapter reads a fresh reply ------------------------------------------------------------------------

class FakeChat:
    """Telegram as a list of messages; /balance gets the scripted reply, optionally edited once."""

    def __init__(self, reply, edited=None, old=REPLY):
        self.messages = [SimpleNamespace(id=1, text=old, buttons=[])] if old else []
        self.reply, self.edited, self.sent = reply, edited, []

    async def send(self, text):
        self.sent.append(text)
        if self.reply is not None:
            self.messages.append(SimpleNamespace(id=len(self.messages) + 10, text=self.reply, buttons=[]))

    async def recent(self, limit=8):
        if self.edited is not None and self.messages and self.messages[-1].text == self.reply:
            self.messages[-1] = SimpleNamespace(id=self.messages[-1].id, text=self.edited, buttons=[])
        return list(reversed(self.messages))[:limit]

    async def press(self, message_id, index):
        raise AssertionError('no button is pressed for /balance')

    async def download(self, message_id, directory):
        raise AssertionError('nothing is downloaded for /balance')


async def no_sleep(_seconds):
    return None


def adapter(chat, tmp_path):
    return ClickAdapter(chat, Profile(), download_dir=tmp_path, step_delay=0, reply_timeout=1, file_timeout=1, sleep=no_sleep)


async def test_adapter_reads_only_the_reply_to_this_request(tmp_path):
    chat = FakeChat(TWO)
    reply, message_id = await adapter(chat, tmp_path).read_balance()
    assert chat.sent == ['/balance'] and message_id != 1 and reply.total == {'UZS': D('1500000')}


async def test_adapter_rereads_an_edited_reply(tmp_path):
    chat = FakeChat("Общий баланс:\n0 сум\nВаши карты:\n💳 HAMK***6104\n0 сум", edited=TWO)
    reply, _ = await adapter(chat, tmp_path).read_balance()
    assert reply.total == {'UZS': D('1500000')}


async def test_old_reply_is_never_reused(tmp_path):
    with pytest.raises(SyncError):
        await adapter(FakeChat(None), tmp_path).read_balance()


# --- The worker's schedule ----------------------------------------------------------------------------------

NOW = datetime(2026, 11, 17, 12, 0)


def state(**kw):
    values = dict(balance_requested=False, paused=False, last_balance_at=None, last_balance_error=None, last_review_on=None)
    values.update(kw)
    return SimpleNamespace(**values)


@pytest.mark.parametrize('values, enabled, due', [
    ({}, True, True),
    ({}, False, False),
    ({'paused': True}, True, False),
    ({'paused': True, 'balance_requested': True}, False, True),          # the manual button always works
    ({'last_balance_at': NOW - timedelta(minutes=100)}, True, False),
    ({'last_balance_at': NOW - timedelta(minutes=180)}, True, True),
    ({'last_balance_at': NOW - timedelta(minutes=16), 'last_balance_error': 'x'}, True, True),
])
def test_balance_due(values, enabled, due):
    assert worker.balance_due(state(**values), NOW, 180, enabled) is due


def test_review_due_once_a_day_after_the_local_time():
    at = (21, 0)
    assert worker.review_due(state(), datetime(2026, 11, 17, 20, 59), at) is None
    assert worker.review_due(state(), datetime(2026, 11, 17, 21, 0), at) == date(2026, 11, 17)
    assert worker.review_due(state(last_review_on=date(2026, 11, 17)), datetime(2026, 11, 17, 23, 0), at) is None
    assert worker.review_due(state(last_review_on=date(2026, 11, 16)), datetime(2026, 11, 17, 23, 0), at) == date(2026, 11, 17)


def fake_review_db(monkeypatch, asked):
    log = {'claimed': None, 'released': [], 'notified': []}

    async def claim(user_id, today):
        if log['claimed'] == today:
            return None
        previous, log['claimed'] = log['claimed'] or date.min, today
        return previous

    async def release(user_id, today, previous):
        log['released'].append(today)
        log['claimed'] = None if previous == date.min else previous

    async def intervals(user_id, now):
        return asked

    async def others(user_id, now):
        return []

    async def mark(user_id, ids):
        log['notified'] += ids

    for name, value in dict(claim_review=claim, release_review=release, open_intervals=intervals, other_intervals=others,
                            mark_intervals_notified=mark).items():
        monkeypatch.setattr(worker.balance_queries, name, value)
    monkeypatch.setattr(worker.settings, 'from_utc', lambda value: value)
    return log


async def test_daily_review_is_sent_once_and_given_back_when_telegram_fails(monkeypatch):
    asked = [SimpleNamespace(id=4, remaining=D('-50000'), last4='6104', card='', currency='UZS', start_at=T0, end_at=T1,
                             status='unexplained')]
    log = fake_review_db(monkeypatch, asked)
    day = date(2026, 11, 17)

    async def down(text, markup):
        return False

    assert await worker.send_review(1001, down, day) is False
    assert log['released'] == [day] and log['notified'] == []

    sent = []

    async def up(text, markup):
        sent.append((text, markup))
        return True

    assert await worker.send_review(1001, up, day) is True
    assert await worker.send_review(1001, up, day) is False         # not twice the same day
    assert len(sent) == 1 and log['notified'] == [4]
    assert sent[0][1]['inline_keyboard'][0][0]['callback_data'] == 'cb:v:4'


def fake_balance_db(monkeypatch, previous_error=None, manual=False):
    log = {'saved': [], 'rejected': [], 'marked': [], 'refreshed': 0}

    async def get_state(user_id):
        return state(last_balance_error=previous_error, balance_requested=manual)

    async def save(user_id, requested_at, observed_at, message_id, reply):
        log['saved'].append(reply)

    async def reject(user_id, requested_at, observed_at, error):
        log['rejected'].append(error)

    async def mark(user_id, error):
        log['marked'].append(error)

    async def refresh(user_id):
        log['refreshed'] += 1
        return {'waiting': 1}

    async def latest(user_id):
        return None, []

    async def asked(user_id, now):
        return []

    monkeypatch.setattr(worker.queries, 'get_state', get_state)
    for name, value in dict(save_observation=save, save_rejection=reject, mark_balance=mark, latest_balances=latest,
                            open_intervals=asked).items():
        monkeypatch.setattr(worker.balance_queries, name, value)
    monkeypatch.setattr(worker, 'refresh_intervals', refresh)
    return log


class Port:
    def __init__(self, reply):
        self.reply = reply

    async def read_balance(self):
        if isinstance(self.reply, Exception):
            raise self.reply
        return parse_balance_reply(self.reply), 42


async def test_balance_check_stores_a_snapshot_and_refreshes(monkeypatch):
    log = fake_balance_db(monkeypatch, manual=True)
    sent = []

    async def notify(text, markup):
        sent.append(text)
        return True

    async def factory():
        return Port(REPLY)

    assert await worker.check_balance(1001, factory, notify) is True
    assert len(log['saved']) == 1 and log['marked'] == [None] and log['refreshed'] == 1
    assert any('Неясных изменений нет' in text for text in sent)     # a manual check answers


async def test_rejected_reply_keeps_snapshots_and_is_reported_once(monkeypatch):
    sent = []

    async def notify(text, markup):
        sent.append(text)
        return True

    async def factory():
        return Port(BalanceRejected('баланс скрыт в Click'))

    log = fake_balance_db(monkeypatch)
    assert await worker.check_balance(1001, factory, notify) is False
    assert log['saved'] == [] and log['refreshed'] == 0 and 'скрыт' in log['rejected'][0]
    assert len(sent) == 1 and '2244986' not in sent[0]

    log = fake_balance_db(monkeypatch, previous_error=log['marked'][0])
    sent.clear()
    await worker.check_balance(1001, factory, notify)
    assert sent == []           # the same failure is not announced again
