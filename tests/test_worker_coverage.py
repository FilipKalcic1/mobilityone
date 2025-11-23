import pytest
import orjson
from unittest.mock import MagicMock, AsyncMock, patch
from worker import WhatsappWorker, QUEUE_INBOUND, QUEUE_OUTBOUND

@pytest.mark.asyncio
async def test_worker_full_processing_cycle():
    """
    Integracijski test logike workera (bez prave mreže).
    Simulira: 
    1. Worker nalazi poruku u QUEUE_INBOUND.
    2. AI odlučuje pozvati alat 'get_loc'.
    3. Worker izvršava alat preko Gatewaya.
    4. AI nakon toga daje finalni odgovor.
    5. Worker šalje odgovor u QUEUE_OUTBOUND.
    """
    worker = WhatsappWorker()
    
    # --- 1. MOCKOVI ---
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True) # Lock acquired
    mock_redis.eval = AsyncMock() # Lock release
    mock_redis.aclose = AsyncMock()
    
    # Simuliramo 'blpop': Prvi put vrati poruku, drugi put None (da ne uđe u beskonačnu petlju ako se test vrti)
    inbound_payload = {
        "sender": "38591", 
        "text": "Gdje je auto?", 
        "message_id": "msg_123"
    }
    mock_redis.blpop = AsyncMock(return_value=[QUEUE_INBOUND, orjson.dumps(inbound_payload).decode('utf-8')])
    worker.redis = mock_redis

    # Mock Servisi
    worker.queue = MagicMock()
    worker.queue.enqueue = AsyncMock()
    
    worker.context = MagicMock()
    worker.context.add_message = AsyncMock()
    worker.context.get_history = AsyncMock(return_value=[])
    
    worker.registry = MagicMock()
    worker.registry.is_ready = True
    worker.registry.tools_map = {"get_loc": {"path": "/loc", "method": "GET"}} 
    worker.registry.find_relevant_tools = AsyncMock(return_value=[])

    worker.gateway = MagicMock()
    worker.gateway.execute_tool = AsyncMock(return_value={"lat": 45.0})

    # --- 2. MOCK AI LOGIKE (Analyze Intent) ---
    
    # Mockiramo pydantic objekte za tool_calls
    mock_tool_call = MagicMock()
    mock_tool_call.model_dump.return_value = {"id": "call_123", "function": {}}

    # Prvi poziv: AI kaže "Zovi alat get_loc"
    decision_tool = {
        "tool": "get_loc",
        "parameters": {},
        "tool_call_id": "call_123",
        "raw_tool_calls": [mock_tool_call], 
        "response_text": None
    }
    # Drugi poziv: AI kaže "Auto je na 45.0"
    decision_final = {
        "tool": None,
        "parameters": {},
        "response_text": "Auto je na 45.0"
    }
    
    # Patchamo analyze_intent da vrati sekvencu odluka
    with patch("worker.analyze_intent", side_effect=[decision_tool, decision_final]):
        
        # --- 3. POKRETANJE ---
        # Pozivamo direktno _process_inbound za jednu poruku
        await worker._process_inbound()
        
        # --- 4. PROVJERE ---
        
        # A. Je li lock zatražen?
        mock_redis.set.assert_called() 
        
        # B. Je li alat izvršen?
        worker.gateway.execute_tool.assert_called_once()
        
        # C. Je li spremljena Assistant poruka s tool_calls? (Prije izvršenja)
        assert worker.context.add_message.call_count >= 3
        
        # Provjeri spremanje rezultata alata
        tool_msg_call = [c for c in worker.context.add_message.call_args_list if c[0][1] == "tool"]
        assert tool_msg_call, "Nije spremljen rezultat alata"
        
        # D. Je li finalni odgovor poslan korisniku?
        worker.queue.enqueue.assert_called_with("38591", "Auto je na 45.0")

@pytest.mark.asyncio
async def test_worker_outbound_processing():
    """Testira _process_outbound logiku."""
    worker = WhatsappWorker()
    worker.redis = MagicMock()
    worker.http = MagicMock()
    worker.http.post = AsyncMock()
    worker.queue = MagicMock()
    worker.queue.schedule_retry = AsyncMock()

    # 1. Uspješan scenarij
    payload = {"to": "123", "text": "msg", "attempts": 0}
    worker.redis.blpop = AsyncMock(return_value=[QUEUE_OUTBOUND, orjson.dumps(payload).decode('utf-8')])
    worker.http.post.return_value.status_code = 200
    
    await worker._process_outbound()
    worker.http.post.assert_called()

    # 2. Neuspješan scenarij (Exception)
    worker.redis.blpop = AsyncMock(return_value=[QUEUE_OUTBOUND, orjson.dumps(payload).decode('utf-8')])
    worker.http.post.side_effect = Exception("Net error")
    
    await worker._process_outbound()
    worker.queue.schedule_retry.assert_called()