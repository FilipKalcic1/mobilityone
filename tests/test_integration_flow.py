import pytest
from unittest.mock import patch
from main import app
from routers.webhook import get_gateway, get_registry 

@pytest.mark.asyncio
async def test_webhook_queues_message_correctly(async_client, redis_client):
    """
    Testira da webhook endpoint samo zaprima poruku i sprema je u 'whatsapp_inbound'.
    Ne provjerava izvršavanje alata jer to radi Worker proces.
    """
    payload = {
        "results": [{
            "from": "38591234567",
            "text": "Gdje je vozilo?",
            "messageId": "msg-1"
        }]
    }

    # Mockamo sigurnosnu provjeru da ne moramo računati HMAC
    with patch("routers.webhook.validate_infobip_signature", return_value=None):
         
        response = await async_client.post("/webhook/whatsapp", json=payload)

        # 1. Provjeri HTTP odgovor
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "queued"

        # 2. Provjeri je li poruka u INBOUND redu (za Workera)
        assert await redis_client.llen("whatsapp_inbound") == 1
        
        # 3. Provjeri da OUTBOUND red (odgovori) JOŠ NIJE pun (jer worker nije radio)
        assert await redis_client.llen("whatsapp_outbound") == 0

        # 4. Provjeri sadržaj u Redisu
        item = await redis_client.lrange("whatsapp_inbound", 0, 0)
        
        # POPRAVAK: item[0] je string (JSON), pa tražimo string "..." (bez b prefiksa)
        assert "Gdje je vozilo?" in item[0]