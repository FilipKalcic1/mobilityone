import pytest
import json
import numpy as np
from unittest.mock import MagicMock, AsyncMock, patch, mock_open
from services.tool_registry import ToolRegistry

# Sample Swagger za testiranje
SAMPLE_SWAGGER = {
    "paths": {
        "/vehicle/{id}": {
            "get": {
                "operationId": "get_vehicle",
                "summary": "Dohvati vozilo",
                "description": "Vraća detalje.",
                "parameters": [
                    {"name": "id", "in": "path", "required": True, "schema": {"type": "string"}}
                ]
            }
        },
        "/ignore_me": {
            "head": {} # Ovo treba ignorirati
        }
    }
}

@pytest.mark.asyncio
async def test_load_swagger_file_not_found(redis_client):
    """Testira da se sustav ne ruši (samo logira) ako fali file."""
    registry = ToolRegistry(redis_client)
    
    # Simuliramo da file ne postoji
    with patch("builtins.open", side_effect=FileNotFoundError):
        await registry.load_swagger("nepostojeci.json")
    
    assert registry.is_ready is False
    assert len(registry.tools_map) == 0

@pytest.mark.asyncio
async def test_load_swagger_invalid_json(redis_client):
    """Testira loš JSON format."""
    registry = ToolRegistry(redis_client)
    
    with patch("builtins.open", mock_open(read_data="{neispravan_json")):
        await registry.load_swagger("bad.json")
        
    assert registry.is_ready is False

@pytest.mark.asyncio
async def test_load_swagger_success_and_caching(redis_client):
    """
    Testira:
    1. Učitavanje validnog swaggera.
    2. Generiranje embeddinga (mockano).
    3. Spremanje u Redis (caching).
    """
    registry = ToolRegistry(redis_client)
    
    # Mockamo OpenAI response
    mock_embedding = [0.1, 0.2, 0.3]
    mock_create = AsyncMock()
    mock_create.return_value.data = [MagicMock(embedding=mock_embedding)]
    registry.client.embeddings.create = mock_create

    # 1. Prvi prolaz - Nema u cacheu, zove OpenAI
    with patch("builtins.open", mock_open(read_data=json.dumps(SAMPLE_SWAGGER))):
        await registry.load_swagger("swagger.json")

    assert registry.is_ready is True
    assert "get_vehicle" in registry.tools_map
    assert len(registry.tools_vectors) == 1
    
    # Provjeri je li spremio u Redis
    keys = await redis_client.keys("tool_embedding:*")
    assert len(keys) > 0

    # 2. Drugi prolaz - Čita iz cachea (ne zove OpenAI)
    registry.client.embeddings.create = AsyncMock() # Reset mocka
    
    # Resetiramo registry state ali Redis je pun
    registry_2 = ToolRegistry(redis_client)
    registry_2.client = registry.client # Da ne kreira novi pravi klijent
    
    with patch("builtins.open", mock_open(read_data=json.dumps(SAMPLE_SWAGGER))):
        await registry_2.load_swagger("swagger.json")
    
    # OpenAI create NIJE trebao biti pozvan jer je u Redisu
    registry_2.client.embeddings.create.assert_not_called()
    assert registry_2.is_ready is True

@pytest.mark.asyncio
async def test_find_relevant_tools(redis_client):
    """Testira cosine similarity logiku."""
    registry = ToolRegistry(redis_client)
    registry.is_ready = True
    
    # Postavimo 2 vektora
    registry.tools_names = ["tool_A", "tool_B"]
    # Tool A je sličan queryju (dot product 1.0), Tool B je suprotan
    registry.tools_vectors = [
        [1.0, 0.0], 
        [0.0, 1.0]
    ]
    
    # Mock query embedding da bude [1.0, 0.0]
    mock_create = AsyncMock()
    mock_create.return_value.data = [MagicMock(embedding=[1.0, 0.0])]
    registry.client.embeddings.create = mock_create
    
    # Mock tool definicije
    registry.tools_map = {
        "tool_A": {"openai_schema": {"name": "tool_A"}},
        "tool_B": {"openai_schema": {"name": "tool_B"}}
    }

    results = await registry.find_relevant_tools("query", top_k=1)
    
    assert len(results) == 1
    assert results[0]["name"] == "tool_A"

@pytest.mark.asyncio
async def test_find_relevant_tools_not_ready(redis_client):
    registry = ToolRegistry(redis_client)
    registry.is_ready = False
    assert await registry.find_relevant_tools("test") == []

@pytest.mark.asyncio
async def test_find_relevant_tools_error(redis_client):
    registry = ToolRegistry(redis_client)
    registry.is_ready = True
    registry.tools_vectors = [[1]]
    # Force error
    registry.client.embeddings.create = AsyncMock(side_effect=Exception("OpenAI Down"))
    
    res = await registry.find_relevant_tools("test")
    assert res == []