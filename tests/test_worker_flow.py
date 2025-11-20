import pytest
import json
from unittest.mock import MagicMock, AsyncMock, patch
from worker import WhatsappWorker, QUEUE_OUTBOUND

@pytest.mark.asyncio
async def test_worker_process_outbound_success():
    """Testira da worker uzima zadatak i šalje HTTP zahtjev."""
    
    # 1. Setup Workera s mockovima
    worker = WhatsappWorker()
    worker.redis = MagicMock()
    worker.http = AsyncMock()
    worker.queue = MagicMock()
    
    # Simulacija dohvaćanja poruke iz Redis reda
    payload = {"to": "38599", "text": "Hello", "attempts": 0}
    worker.redis.blpop = AsyncMock(return_value=[QUEUE_OUTBOUND, json.dumps(payload)])
    
    # Mock Infobip odgovora
    worker.http.post.return_value.status_code = 200
    
    # 2. Izvrši jednu iteraciju obrade
    await worker._process_outbound()
    
    # 3. Provjera
    # Mora pozvati Infobip API
    worker.http.post.assert_called_once()
    call_args = worker.http.post.call_args[1]['json']
    assert call_args['to'] == "38599"
    assert call_args['content']['text'] == "Hello"

@pytest.mark.asyncio
async def test_worker_schedules_retry_on_failure():
    """Ako slanje ne uspije, poruka mora ići u retry schedule."""
    
    worker = WhatsappWorker()
    worker.redis = MagicMock()
    worker.http = AsyncMock()
    worker.queue = AsyncMock() # Mockamo QueueService
    
    payload = {"to": "38599", "text": "Fail", "attempts": 0}
    worker.redis.blpop = AsyncMock(return_value=[QUEUE_OUTBOUND, json.dumps(payload)])
    
    # Simulacija greške na mreži
    worker.http.post.side_effect = Exception("Network error")
    
    await worker._process_outbound()
    
    # Mora pozvati schedule_retry
    worker.queue.schedule_retry.assert_called_once_with(payload)