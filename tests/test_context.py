import pytest
import json
from services.context import ContextService

@pytest.mark.asyncio
async def test_context_add_and_retrieve(redis_client):
    service = ContextService(redis_client)
    sender = "user_1"
    
    # 1. Dodaj poruku
    await service.add_message(sender, "user", "Pozdrav")
    
    # 2. Dohvati povijest
    history = await service.get_history(sender)
    
    assert len(history) == 1
    assert history[0]["role"] == "user"
    assert history[0]["content"] == "Pozdrav"

@pytest.mark.asyncio
async def test_context_limit_size(redis_client):
    service = ContextService(redis_client)
    sender = "user_2"
    
    # Dodaj 15 poruka (Limit je 10)
    for i in range(15):
        await service.add_message(sender, "user", f"msg_{i}")
        
    history = await service.get_history(sender)
    
    # Mora ih biti samo 10
    assert len(history) == 10
    # Prva poruka u povijesti mora biti "msg_5" (najstarije su obrisane)
    assert history[0]["content"] == "msg_5"
    assert history[-1]["content"] == "msg_14"