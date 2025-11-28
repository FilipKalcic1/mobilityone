import orjson
import redis.asyncio as redis
from typing import Callable, Any
import structlog

logger = structlog.get_logger("cache")

class CacheService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    async def get_or_compute(self, key: str, func: Callable, *args, ttl: int = 60) -> Any:
        """
        Dohvaća podatak iz cachea. Ako ne postoji, izvršava funkciju 'func' i sprema rezultat.
        Otporan na pad Redisa (nastavlja raditi bez cachea).
        """
        # 1. Pokušaj čitanja
        try:
            cached = await self.redis.get(key)
            if cached:
                return orjson.loads(cached)
        except Exception as e:
            logger.warning("Cache read failed (skipping)", key=key, error=str(e))

        # 2. Izračun (ako nema u cacheu ili je Redis pao)
        result = await func(*args)

        # 3. Pokušaj spremanja
        try:
            if result: 
                # orjson.dumps vraća bytes, decode u str za Redis
                await self.redis.setex(key, ttl, orjson.dumps(result).decode('utf-8'))
        except Exception as e:
            logger.warning("Cache write failed", key=key, error=str(e))

        return result   