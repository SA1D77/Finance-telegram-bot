from redis.asyncio import Redis

from config import REDIS_HOST, REDIS_PORT, REDIS_PASSWORD

redis = Redis(host=REDIS_HOST, port=REDIS_PORT, password=REDIS_PASSWORD, db='3')
