# tests/test_integration_flow.py
import pytest
import httpx
import json
from unittest.mock import patch
from services.queue import QueueService

# Budući da koristimo fixture 'async_client', 'redis_client', 
# i 'mock_settings', oni se automatski ubacuju iz conftest.py

@pytest.mark.asyncio
async def test_inbound_webhook_queues_message_successfully(async_client, redis_client):
    """
    Testira da li POST zahtjev na webhook:
    1. Vraća 200 OK.
    2. Stavlja poruku u Redis Listu (Queue) s ispravnim sadržajem.
    """
    
    test_sender = "447700900000"
    queue_name = "whatsapp_outbound_queue"
    
    webhook_payload = {
        "results": [{
            "type": "TEXT",
            "text": "Gdje je ZG-123?",
            "from": test_sender,
            "to": "447800000000",
            "messageId": "test-id-123"
        }],
        "messageCount": 1,
        "pendingMessageCount": 0
    }
    
    # 1. ARRANGE (Mock AI and Security)
    # Mockiramo AI da vrati kontrolirani JSON output
    mock_ai_output = {"tool": "vehicle_status", "parameters": {"plate": "ZG-123"}}
    
    with patch('security.validate_infobip_signature', return_value=None):
        with patch('services.ai.analyze_intent', return_value=mock_ai_output):
            
            # 2. ACT (Izvršenje API poziva)
            response = await async_client.post(
                "/webhook/whatsapp",
                json=webhook_payload
            )

    # 3. ASSERT (Provjera)
    
    # A. Provjera HTTP statusa
    assert response.status_code == 200
    assert response.json() == {"status": "queued"}
    
    # B. Provjera Redisa (Najvažnije!)
    
    # Provjeravamo da li se točno 1 poruka pojavila u redu
    queue_length = await redis_client.llen(queue_name)
    assert queue_length == 1
    
    # Dohvaćamo poruku iz reda (LPOP)
    queued_item = await redis_client.lpop(queue_name)
    
    # Parsiramo JSON iz reda
    queued_data = json.loads(queued_item)
    
    # Provjeravamo da li je Worker dobio ispravne podatke
    assert queued_data['to'] == test_sender
    # Provjeravamo da li odgovor sadrži podatke iz našeg mocka
    assert "Vozilo ZG-123 je Na terenu" in queued_data['text']