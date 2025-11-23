import pytest
import json
import orjson  # Koristimo za provjeru formata ako želimo biti precizni, ili json.loads za logiku
from unittest.mock import MagicMock, AsyncMock, patch
from services.queue import QueueService, QUEUE_OUTBOUND, QUEUE_SCHEDULE

@pytest.mark.asyncio
async def test_enqueue_adds_to_redis():
    """Testira dodaje li se poruka u Redis listu s ispravnim CID-om."""
    
    mock_redis = MagicMock()
    mock_redis.rpush = AsyncMock()
    
    queue = QueueService(mock_redis)
    
    # Patchamo uuid da uvijek vrati isti ID radi lakše provjere
    with patch("services.queue.uuid.uuid4", return_value="test-uuid-123"):
        await queue.enqueue("38591", "Test")
    
    # Očekivani podaci (kao dict)
    expected_data = {
        "to": "38591", 
        "text": "Test", 
        "cid": "test-uuid-123", 
        "attempts": 0
    }

    # Provjera poziva
    mock_redis.rpush.assert_called_once()
    args = mock_redis.rpush.call_args[0]
    
    # 1. Provjeri ime reda
    assert args[0] == QUEUE_OUTBOUND
    
    # 2. Provjeri sadržaj poruke (robusnija metoda)
    # Umjesto usporedbe stringova, parsiramo JSON koji je poslan u Redis
    # i uspoređujemo ga s očekivanim rječnikom.
    actual_payload = orjson.loads(args[1])
    assert actual_payload == expected_data

@pytest.mark.asyncio
async def test_schedule_retry_logic():
    """Testira retry logiku (ZSET)."""
    mock_redis = MagicMock()
    mock_redis.zadd = AsyncMock()
    
    queue = QueueService(mock_redis)
    # Ulazni payload
    payload = {"to": "38591", "text": "Fail", "cid": "old-id", "attempts": 0}
    
    await queue.schedule_retry(payload)
    
    mock_redis.zadd.assert_called_once()
    
    call_args = mock_redis.zadd.call_args[0]
    assert call_args[0] == QUEUE_SCHEDULE
    
    # zadd prima {member: score} mapu
    zadd_map = call_args[1]
    assert isinstance(zadd_map, dict)
    
    # Provjerimo ključ (member) iz mape
    member_json = list(zadd_map.keys())[0]
    member_data = orjson.loads(member_json)
    
    assert member_data["attempts"] == 1  # Mora se povećati
    assert member_data["cid"] == "old-id"