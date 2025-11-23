import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from services.cache import CacheService
import json

@pytest.mark.asyncio
async def test_cache_redis_failure_read(redis_client):
    """Ako Redis pukne kod čitanja, funkcija se svejedno mora izvršiti."""
    mock_redis = MagicMock()
    # Simuliramo grešku kod GET
    mock_redis.get = AsyncMock(side_effect=Exception("Redis down"))
    mock_redis.setex = AsyncMock()
    
    cache = CacheService(mock_redis)
    
    async def my_func():
        return "data"
        
    # Ne smije se srušiti, mora vratiti "data"
    result = await cache.get_or_compute("key", my_func)
    assert result == "data"

@pytest.mark.asyncio
async def test_cache_redis_failure_write(redis_client):
    """Ako Redis pukne kod pisanja, vraćamo podatak bez rušenja."""
    mock_redis = MagicMock()
    mock_redis.get = AsyncMock(return_value=None)
    # Simuliramo grešku kod SETEX
    mock_redis.setex = AsyncMock(side_effect=Exception("Redis readonly"))
    
    cache = CacheService(mock_redis)
    
    async def my_func():
        return "new_data"
        
    result = await cache.get_or_compute("key", my_func)
    assert result == "new_data"