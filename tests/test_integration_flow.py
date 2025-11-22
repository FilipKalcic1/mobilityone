import pytest
from unittest.mock import patch
import json
from main import app
from routers.webhook import get_gateway, get_registry 

@pytest.mark.asyncio
async def test_webhook_end_to_end(async_client, redis_client):
    payload = {
        "results": [{
            "from": "38591234567",
            "text": "Gdje je vozilo?",
            "messageId": "msg-1"
        }]
    }

    mock_ai_response = {
        "tool": "get_vehicle_location",
        "parameters": {"plate": "ZG-1234"},
        "response_text": None
    }

    with patch("services.ai.analyze_intent", return_value=mock_ai_response), \
         patch("routers.webhook.validate_infobip_signature", return_value=None):
         

        registry_mock = app.dependency_overrides[get_registry]()
        

        registry_mock.find_relevant_tools.return_value = [
            {"type": "function", "function": {"name": "get_vehicle_location"}}
        ]
        
        response = await async_client.post("/webhook/whatsapp", json=payload)

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"


        gateway_mock = app.dependency_overrides[get_gateway]()
        

        gateway_mock.execute_tool.assert_called_once()
        
        assert await redis_client.llen("whatsapp_outbound") == 1