import os
import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from currency import rates
from currency.codes import normalize, split_currency
from quick_input import handlers as qi

from conftest import make_callback, make_message

D = Decimal


@pytest.mark.parametrize('token, code', [
    ('usd', 'USD'), ('USD', 'USD'), ('$', 'USD'), ('евро', 'EUR'), ('€', 'EUR'), ('руб', 'RUB'), ('₽', 'RUB'),
    ('сум', 'UZS'), ('uzs', 'UZS'), ('тенге', 'KZT'), ('usd.', 'USD'),
    ('кафе', None), ('abc', None), ('', None), (None, None), ('us', None),
])
def test_normalize(token, code):
    assert normalize(token) == code


def test_split_currency():
    assert split_currency(['usd', 'кафе', 'обед']) == ('USD', ['кафе', 'обед'])
    assert split_currency(['кафе']) == (None, ['кафе'])
    assert split_currency([]) == (None, [])


def test_convert():
    assert rates.convert(D('50.00'), D('12650.123456')) == D('632506.17')
    assert rates.convert(D('0.01'), D('0.004')) == D('0.00')


@pytest.fixture
def no_cache(monkeypatch):
    """An in-memory stand-in for the Redis cache: {key: (rate, ttl)}."""
    stored = {}

    async def cached(key):
        return stored[key][0] if key in stored else None

    async def store(key, rate, ttl):
        stored[key] = (rate, ttl)

    monkeypatch.setattr(rates, '_cached', cached)
    monkeypatch.setattr(rates, '_store', store)
    return stored


async def test_get_rate_same_currency_needs_nothing(monkeypatch):
    async def boom(*args):
        raise AssertionError('no download for the same currency')

    monkeypatch.setattr(rates, '_download', boom)
    assert await rates.get_rate('UZS', 'UZS', date(2026, 10, 1), date(2026, 10, 22)) == D('1')


async def test_get_rate_downloads_and_caches(monkeypatch, no_cache):
    calls = []

    async def download(code, base, day_label):
        calls.append((code, base, day_label))
        return D('11781.34174931')

    monkeypatch.setattr(rates, '_download', download)

    past = await rates.get_rate('USD', 'UZS', date(2026, 10, 1), date(2026, 10, 22))
    again = await rates.get_rate('USD', 'UZS', date(2026, 10, 1), date(2026, 10, 22))
    today = await rates.get_rate('USD', 'UZS', date(2026, 10, 22), date(2026, 10, 22))

    assert past == again == today == D('11781.34174931')
    assert calls == [('USD', 'UZS', '2026-10-01'), ('USD', 'UZS', 'latest')]  # the past day came from the cache
    assert no_cache['rate:2026-10-01:USD:UZS'][1] == rates.PAST_TTL
    assert no_cache['rate:2026-10-22:USD:UZS'][1] == rates.TODAY_TTL


async def test_download_falls_back_to_second_source(monkeypatch):
    async def broken(request):
        return web.Response(status=503)

    async def good(request):
        assert request.match_info['day'] == '2026-10-01'
        return web.json_response({'date': '2026-10-01', 'usd': {'uzs': 11781.34174931, 'rub': 83.4}})

    app = web.Application()
    app.router.add_get('/a/{day}/{code}.json', broken)
    app.router.add_get('/b/{day}/{code}.json', good)
    async with TestServer(app) as server:
        base = str(server.make_url('/'))
        monkeypatch.setattr(rates, 'SOURCES', (base + 'a/{day}/{code}.json', base + 'b/{day}/{code}.json'))
        assert await rates._download('USD', 'UZS', '2026-10-01') == D('11781.34174931')
        with pytest.raises(rates.RateUnavailable):
            await rates._download('USD', 'XYZ', '2026-10-01')


@pytest.mark.skipif(not os.environ.get('TEST_NETWORK'), reason='TEST_NETWORK is not set')
async def test_real_rates_source():
    assert D('5000') < await rates._download('USD', 'UZS', '2026-10-01') < D('50000')


# --- Quick entry in another currency ------------------------------------------------

@pytest.fixture
def multi(monkeypatch, harness):
    saved = []

    async def categories(user_id, kind=None):
        return [SimpleNamespace(id=1, name='отель')]

    async def add_operation(**kwargs):
        saved.append(kwargs)
        return uuid.uuid4()

    async def get_category(category_id, user_id):
        return SimpleNamespace(id=1, name='отель') if category_id == 1 else None

    async def rate(code, base, day, today):
        assert (code, base) == ('USD', 'UZS')
        return D('12650.12')

    monkeypatch.setattr(qi, '_show_all_categories', categories)
    monkeypatch.setattr(qi, '_add_operation', add_operation)
    monkeypatch.setattr(qi, '_get_category', get_category)
    monkeypatch.setattr(qi, 'get_rate', rate)
    monkeypatch.setattr(qi, 'currencies_active', lambda: True)
    harness.settings['currency'] = 'UZS'  # the user's main currency
    return saved


async def test_foreign_amount_is_converted_after_confirmation(harness, multi):
    await harness.feed(make_message('50 usd отель Стамбул'))

    assert multi == []
    text = harness.session.requests[-1].text
    assert 'РАСХОД <b>632\xa0506\xa0сум отель</b>' in text and '💱 $50 × 12650.12 = 632\xa0506\xa0сум' in text

    await harness.feed(make_callback('qi:save'))

    assert multi[0]['quantity'] == D('632506.00') and multi[0]['comment'] == 'Стамбул'
    assert (multi[0]['original_amount'], multi[0]['original_currency'], multi[0]['rate']) == (D('50.00'), 'USD', D('12650.12'))


async def test_symbol_after_amount(harness, multi):
    await harness.feed(make_message('50$ отель'))
    await harness.feed(make_callback('qi:save'))

    assert multi[0]['original_currency'] == 'USD'


async def test_base_currency_word_is_just_dropped(harness, multi):
    await harness.feed(make_message('500 сум отель'))

    assert multi[0]['quantity'] == D('500.00') and 'original_currency' not in multi[0]


async def test_rate_unavailable(harness, multi, monkeypatch):
    async def down(*args):
        raise rates.RateUnavailable('USD/UZS')

    monkeypatch.setattr(qi, 'get_rate', down)

    await harness.feed(make_message('50 usd отель'))

    assert multi == [] and 'Не удалось получить курс USD' in harness.session.requests[-1].text


async def test_currencies_off_keeps_words(harness, multi, monkeypatch):
    monkeypatch.setattr(qi, 'currencies_active', lambda: False)

    await harness.feed(make_message('50 usd отель'))

    assert 'Категории «usd» нет.' in harness.session.requests[-1].text


def test_currencies_active_needs_base(monkeypatch):
    import config
    monkeypatch.setattr(config, 'FEATURE_CURRENCIES', True)
    monkeypatch.setattr(config, 'BASE_CURRENCY', '')
    assert config.currencies_active() is False
    monkeypatch.setattr(config, 'BASE_CURRENCY', 'UZS')
    assert config.currencies_active() is True
    monkeypatch.setattr(config, 'BASE_CURRENCY', 'UZ1')
    assert config.currencies_active() is False
