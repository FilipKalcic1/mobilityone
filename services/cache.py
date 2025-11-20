import json
import redis.asyncio as redis
from async_lru import alru_cache
from typing import Any, Callable

class CacheService:
    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client

    # L1 Cache (Memorija procesa) - Ultra brzo za česte iste upite
    @alru_cache(maxsize=1000)
    async def _dummy_l1(self): pass

    async def get_or_compute(self, key: str, func: Callable, *args) -> Any:
        """
        Strategija: Redis (L2) -> Compute (Baza) -> Save to Redis
        """
        # 1. Pokušaj Redis
        try:
            cached = await self.redis.get(key)
            if cached:
                return json.loads(cached)
        except Exception:
            pass # Ako Redis ne radi, nastavi na izračun

        # 2. Izračunaj (skupi poziv)
        result = await func(*args)
        
        # 3. Spremi u Redis (TTL 60s)
        try:
            await self.redis.setex(key, 60, json.dumps(result))
        except Exception:
            pass

        return result