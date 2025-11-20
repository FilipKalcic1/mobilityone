import pytest
import json
from unittest.mock import MagicMock, AsyncMock
# Zbog conftest.py, i ovaj import radi
from services.queue import QueueService

@pytest.mark.asyncio
async def test_enqueue_adds_to_redis():
    """Testira dodaje li se poruka u Redis listu."""
    
    # 1. Mockamo Redis (da ne mora biti upaljen Docker)
    mock_redis = MagicMock()
    mock_redis.rpush = AsyncMock()
    
    # 2. Inicijalizacija
    queue = QueueService(mock_redis)
    
    # 3. Akcija
    await queue.enqueue("38591", "Test")
    
    # 4. Provjera poziva
    expected_payload = json.dumps({"to": "38591", "text": "Test", "attempts": 0})
    mock_redis.rpush.assert_called_once_with("whatsapp_outbound", expected_payload)

@pytest.mark.asyncio
async def test_schedule_retry_logic():
    """Testira retry logiku (ZSET)."""
    
    mock_redis = MagicMock()
    mock_redis.zadd = AsyncMock()
    
    queue = QueueService(mock_redis)
    payload = {"to": "38591", "text": "Fail", "attempts": 0}
    
    # Akcija
    status = await queue.schedule_retry(payload)
    
    assert status == "SCHEDULED"
    # Provjera je li pozvan zadd (Sorted Set)
    mock_redis.zadd.assert_called_once()