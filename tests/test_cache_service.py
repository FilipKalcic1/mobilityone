import pytest
import asyncio
from services.cache import CacheService


CALL_COUNT = 0

async def mock_db_call(arg):
    """Glumi spori poziv bazi."""
    global CALL_COUNT
    CALL_COUNT += 1
    return f"result_{arg}"

@pytest.mark.asyncio
async def test_cache_miss_and_hit(redis_client):
    """
    Testira:
    1. Prvi poziv izvršava funkciju i sprema u Redis.
    2. Drugi poziv čita iz Redisa (brojač se ne povećava).
    """
    global CALL_COUNT
    CALL_COUNT = 0
    
    cache = CacheService(redis_client)
    key = "test_key_1"
    

    res1 = await cache.get_or_compute(key, mock_db_call, "A")
    
    assert res1 == "result_A"
    assert CALL_COUNT == 1
    

    saved_value = await redis_client.get(key)
    assert saved_value is not None
    

    res2 = await cache.get_or_compute(key, mock_db_call, "A")
    
    assert res2 == "result_A"
    assert CALL_COUNT == 1 