import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE

@pytest.mark.asyncio
async def test_enqueue_adds_to_redis():
    """Testira dodaje li se poruka u Redis listu s ispravnim CID-om."""
    
    mock_redis = MagicMock()
    mock_redis.rpush = AsyncMock()
    
    queue = QueueService(mock_redis)
    
    # --- FIX START ---
    # Patchamo uuid da uvijek vrati isti ID, tako da možemo testirati JSON string
    with patch("services.queue.uuid.uuid4", return_value="test-uuid-123"):
        await queue.enqueue("38591", "Test")
    
    # Sada očekujemo JSON koji sadrži i 'cid'
    expected_payload = json.dumps({
        "to": "38591", 
        "text": "Test", 
        "cid": "test-uuid-123", # Ovo smo dodali
        "attempts": 0
    })
    # --- FIX END ---
    
    mock_redis.rpush.assert_called_once()
    args = mock_redis.rpush.call_args[0]
    
    assert args[0] == QUEUE_OUTBOUND
    assert args[1] == expected_payload

@pytest.mark.asyncio
async def test_schedule_retry_logic():
    """Testira retry logiku (ZSET)."""
    mock_redis = MagicMock()
    mock_redis.zadd = AsyncMock()
    
    queue = QueueService(mock_redis)
    # Payload već ima cid jer dolazi iz workera koji ga je pročitao
    payload = {"to": "38591", "text": "Fail", "cid": "old-id", "attempts": 0}
    
    await queue.schedule_retry(payload)
    
    mock_redis.zadd.assert_called_once()
    
    call_args = mock_redis.zadd.call_args[0]
    assert call_args[0] == QUEUE_SCHEDULE
    assert isinstance(call_args[1], dict)