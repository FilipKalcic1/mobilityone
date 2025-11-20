# tests/test_worker_flow.py
import pytest
import httpx
import json
from unittest.mock import AsyncMock, patch
from worker import run_worker # Importiramo glavnu funkciju workera

# Postavljamo timeout na 1 sekundu jer ne želimo da test čeka vječno
@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setattr("worker.asyncio.sleep", AsyncMock())
    monkeypatch.setattr("worker.redis_client.blpop", AsyncMock(return_value=None))

@pytest.mark.asyncio
async def test_worker_sends_correct_payload_to_infobip(redis_client):
    """
    Testira da li worker ispravno čita iz reda i šalje pravilan HTTP zahtjev 
    Infobipu (mockirajući Infobip API).
    """
    
    # 1. ARRANGE (Priprema Redisa i Mocka)
    test_message = "Test poruka iz reda."
    test_number = "447771234567"
    queue_name = "whatsapp_outbound_queue"
    
    # Poruka koja čeka u redu
    queued_payload = json.dumps({"to": test_number, "text": test_message, "attempts": 0})
    
    # Koristimo BLPOP mock da simuliramo poruku koja dolazi iz reda
    # Workera moramo natjerati da se pokrene samo jednom (zaustavit ćemo ga after_call)
    
    async def blpop_mock(queue, timeout):
        # Prvi put vrati poruku, drugi put vrati None (da se worker zaustavi)
        if not blpop_mock.called:
            blpop_mock.called = True
            return [queue_name, queued_payload]
        return None
    blpop_mock.called = False

    # Mockiramo httpx.AsyncClient.post (lažni Infobip API)
    mock_post = AsyncMock(return_value=httpx.Response(200, json={}))

    with patch('worker.http_client.post', new=mock_post), \
         patch('worker.redis_client.blpop', new=blpop_mock):
        
        # 2. ACT (Pokretanje Workera)
        # Pokrećemo workera i čekamo da obradi BLPOP poziv
        # Radimo BLPOP direktno jer je teško kontrolirati asyncio.run(run_worker())
        await worker.run_worker_once_for_test(redis_client, http_client)

        # 3. ASSERT (Provjera)
        
        # A. Provjeravamo je li HTTP poziv prema Infobipu izvršen
        mock_post.assert_called_once()
        
        # B. Provjeravamo da li je payload ispravno strukturiran za Infobip
        called_args, called_kwargs = mock_post.call_args
        
        # Slanje ide na točan Infobip URL
        assert called_args[0].endswith('/whatsapp/1/message/text')

        # Provjeravamo tijelo poruke (da li je u formatu koji Infobip očekuje)
        assert called_kwargs['json']['to'] == test_number
        assert called_kwargs['json']['content']['text'] == test_message