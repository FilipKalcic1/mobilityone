import json
import redis.asyncio as redis
from typing import Callable, Any
import structlog

logger = structlog.get_logger("cache")

class CacheService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def get_or_compute(self, key: str, func: Callable, *args, ttl: int = 60) -> Any:
        # 1. Pokušaj dohvat iz Redisa
        try:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        except Exception as e:
            logger.warning("Redis unavailable, skipping cache read", error=str(e))

        # 2. Izračunaj vrijednost (skupa operacija)
        result = await func(*args)

        # 3. Spremi u Redis
        try:
            if result: # Ne keširaj prazne rezultate/greške
                await self.redis.setex(key, ttl, json.dumps(result))
        except Exception as e:
            logger.warning("Redis unavailable, skipping cache write", error=str(e))

        return result