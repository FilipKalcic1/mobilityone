import pytest
import asyncio
from services.cache import CacheService

# Globalni brojač za testiranje
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
    
    # --- 1. Cache MISS ---
    # Redis je prazan (FakeRedis starta prazan).
    # Očekujemo da se mock_db_call izvrši.
    res1 = await cache.get_or_compute(key, mock_db_call, "A")
    
    assert res1 == "result_A"
    assert CALL_COUNT == 1
    
    # Provjera je li FakeRedis zapamtio vrijednost.
    # CacheService interno radi JSON dump, pa očekujemo string.
    saved_value = await redis_client.get(key)
    assert saved_value is not None
    
    # --- 2. Cache HIT ---
    # Sada zovemo opet. Budući da je FakeRedis zapamtio vrijednost u koraku 1,
    # CacheService bi trebao vratiti rezultat BEZ pozivanja mock_db_call.
    res2 = await cache.get_or_compute(key, mock_db_call, "A")
    
    assert res2 == "result_A"
    assert CALL_COUNT == 1 # Brojač mora ostati isti!