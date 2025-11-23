import pytest
import orjson
from unittest.mock import MagicMock, AsyncMock, patch
from worker import WhatsappWorker, QUEUE_INBOUND, QUEUE_OUTBOUND

@pytest.mark.asyncio
async def test_worker_full_processing_cycle():
    worker = WhatsappWorker()
    
    # Mock Redis
    mock_redis = MagicMock()
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock()
    mock_redis.aclose = AsyncMock()
    mock_redis.setex = AsyncMock()
    
    inbound_payload = {"sender": "38591", "text": "Lokacija?", "message_id": "msg_1"}
    mock_redis.blpop = AsyncMock(side_effect=[
        [QUEUE_INBOUND, orjson.dumps(inbound_payload).decode('utf-8')],
        None
    ])
    worker.redis = mock_redis

    # Mock Dependencies
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
    worker.gateway.execute_tool = AsyncMock(return_value={"lat": 45})

    # Mock AI
    tool_call = MagicMock()
    tool_call.model_dump.return_value = {"id": "1", "function": {}}
    
    decision_tool = {"tool": "get_loc", "parameters": {}, "tool_call_id": "1", "raw_tool_calls": [tool_call], "response_text": None}
    decision_final = {"tool": None, "parameters": {}, "response_text": "Evo."}
    
    with patch("worker.analyze_intent", side_effect=[decision_tool, decision_final]):
        await worker._process_inbound()
        
        worker.gateway.execute_tool.assert_called_once()
        worker.queue.enqueue.assert_called_with("38591", "Evo.")

@pytest.mark.asyncio
async def test_worker_outbound_success():
    worker = WhatsappWorker()
    worker.redis = MagicMock()
    worker.http = MagicMock()
    worker.http.post = AsyncMock()
    worker.http.post.return_value.status_code = 200
    
    payload = {"to": "123", "text": "msg", "attempts": 0}
    worker.redis.blpop = AsyncMock(return_value=[QUEUE_OUTBOUND, orjson.dumps(payload).decode('utf-8')])
    
    await worker._process_outbound()
    worker.http.post.assert_called_once()