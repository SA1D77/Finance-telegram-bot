"""Exchange rates by date from the free fawazahmed0 currency-api (no key), cached in Redis.

Rates for past days never change, so they are cached for a month; today's for a few hours.
"""
import logging
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Optional

import aiohttp

from config import REDIS_HOST, REDIS_PASSWORD, REDIS_PORT

logger = logging.getLogger(__name__)

SOURCES = (
    'https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@{day}/v1/currencies/{code}.json',
    'https://{day}.currency-api.pages.dev/v1/currencies/{code}.json',
)
CACHE_DB = 5
PAST_TTL = 30 * 24 * 3600
TODAY_TTL = 6 * 3600
TIMEOUT = aiohttp.ClientTimeout(total=10)


class RateUnavailable(Exception):
    pass


def convert(amount: Decimal, rate: Decimal) -> Decimal:
    return (amount * rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _cache():
    from redis.asyncio import Redis
    return Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db=CACHE_DB)


async def _cached(key: str) -> Optional[Decimal]:
    try:
        redis = _cache()
        try:
            value = await redis.get(key)
        finally:
            await redis.aclose()
        return Decimal(value.decode()) if value else None
    except Exception:  # the cache is an optimisation; never fail because of it
        return None


async def _store(key: str, rate: Decimal, ttl: int) -> None:
    try:
        redis = _cache()
        try:
            await redis.set(key, str(rate), ex=ttl)
        finally:
            await redis.aclose()
    except Exception:
        pass


async def _download(code: str, base: str, day_label: str) -> Decimal:
    async with aiohttp.ClientSession(timeout=TIMEOUT) as session:
        for template in SOURCES:
            url = template.format(day=day_label, code=code.lower())
            try:
                async with session.get(url) as response:
                    if response.status != 200:
                        continue
                    data = await response.json(content_type=None)
                value = data[code.lower()][base.lower()]
                rate = Decimal(str(value))
                if rate > 0:
                    return rate
            except Exception:
                logger.warning('Rate source failed for %s/%s', code, base, exc_info=False)
    raise RateUnavailable(f'{code}/{base}')


async def get_rate(code: str, base: str, day: date, today: date) -> Decimal:
    """How many units of `base` one unit of `code` cost on `day`."""
    if code == base:
        return Decimal('1')
    key = f'rate:{day.isoformat()}:{code}:{base}'
    cached = await _cached(key)
    if cached is not None:
        return cached
    rate = await _download(code, base, 'latest' if day >= today else day.isoformat())
    await _store(key, rate, TODAY_TTL if day >= today else PAST_TTL)
    return rate
