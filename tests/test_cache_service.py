# tests/test_cache_service.py
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from services.cache import CacheService
from services.queue import QueueService

# --- MOCK FUNKCIJA ZA SIMULACIJU BAZE (Skupi poziv) ---
# Globalni brojač
EXPENSIVE_CALL_COUNT = 0

async def mock_expensive_fetch(data_id):
    """Simulira spori poziv prema bazi i broji izvršenja."""
    global EXPENSIVE_CALL_COUNT
    EXPENSIVE_CALL_COUNT += 1
    await asyncio.sleep(0.01) # Mali delay
    return f"Data for {data_id} - {EXPENSIVE_CALL_COUNT}"

@pytest.mark.asyncio
async def test_cache_l2_and_l1_persistence(redis_client):
    """
    Testira: 
    1. Da se skupi poziv izvršava samo jednom.
    2. Da Redis (L2) preuzima poziv nakon što se L1 (Python Memory) očisti.
    """
    global EXPENSIVE_CALL_COUNT
    EXPENSIVE_CALL_COUNT = 0 # Resetiramo brojač
    
    cache_service = CacheService(redis_client)
    test_key = "test_data_123"
    
    # Prvo, očistimo Redis od prethodnih testova i L1 cache
    await redis_client.delete(test_key)
    cache_service._get_from_l1.cache_clear() # Čišćenje Python Cache

    # --- 1. Puni MISS (Pozovi bazu) ---
    result_1 = await cache_service.get_or_compute(test_key, mock_expensive_fetch, "A")
    assert EXPENSIVE_CALL_COUNT == 1
    assert "Data for A - 1" in result_1
    
    # --- 2. L1 HIT (Provjera Python memorije) ---
    # Ne bi smio zvati bazu niti Redis
    result_2 = await cache_service.get_or_compute(test_key, mock_expensive_fetch, "A")
    assert EXPENSIVE_CALL_COUNT == 1 # Poziv je presretnut u L1 (Python @alru_cache)
    assert result_1 == result_2

    # --- 3. L1 CLEAR (Simulacija restarta servera) ---
    # Brišemo lokalnu memoriju (Python je zaboravio, Redis i dalje pamti)
    cache_service._get_from_l1.cache_clear()
    
    # --- 4. L2 HIT (Provjera Redisa) ---
    # Ovaj put bi Redis trebao presresti poziv (L1 Miss, L2 Hit)
    result_3 = await cache_service.get_or_compute(test_key, mock_expensive_fetch, "A")
    assert EXPENSIVE_CALL_COUNT == 1 # Brojač je i dalje 1
    assert "Data for A - 1" in result_3 # Dobivamo prvi rezultat, ali iz Redisa

    # --- 5. L2 MISS (Istek TTL-a i ponovni poziv baze) ---
    # Simuliramo da je TTL istekao (ručno brišemo Redis ključ)
    await redis_client.delete(test_key)
    
    # Sada mora pozvati skupu bazu
    result_4 = await cache_service.get_or_compute(test_key, mock_expensive_fetch, "A")
    assert EXPENSIVE_CALL_COUNT == 2 # Brojač se povećao!
    assert "Data for A - 2" in result_4

    # FINALNA PROVJERA: Cache se ponovno puni
    result_5 = await cache_service.get_or_compute(test_key, mock_expensive_fetch, "A")
    assert EXPENSIVE_CALL_COUNT == 2 # Opet L1/L2 HIT