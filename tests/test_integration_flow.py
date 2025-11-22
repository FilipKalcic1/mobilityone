import pytest
from unittest.mock import patch
import json

@pytest.mark.asyncio
async def test_webhook_end_to_end(async_client, redis_client):
    """
    Simulira puni flow: Webhook -> Context -> AI -> Tool -> Queue
    """
    payload = {
        "results": [{
            "from": "38591234567",
            "text": "Gdje je vozilo?",
            "messageId": "msg-1"
        }]
    }
    

    mock_ai_response = {
        "tool": "vehicle_status",
        "confidence": 0.95,
        "parameters": {"plate": "ZG-1234"}
    }

    with patch("services.ai.analyze_intent", return_value=mock_ai_response), \
         patch("routers.webhook.validate_infobip_signature", return_value=None):
         
        response = await async_client.post("/webhook/whatsapp", json=payload)
        

        assert response.status_code == 200
        data = response.json()
        

        assert data["status"] == "queued"
        assert "req_id" in data  

        assert await redis_client.llen("whatsapp_outbound") == 1