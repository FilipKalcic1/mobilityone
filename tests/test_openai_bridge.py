import pytest
from unittest.mock import MagicMock, AsyncMock
import httpx
from services.openapi_bridge import OpenAPIGateway

@pytest.mark.asyncio
async def test_execute_tool_success():
    gateway = OpenAPIGateway("http://api.test")
    
    # Mock httpx client unutar gatewaya
    gateway.client.request = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": "ok"}
    gateway.client.request.return_value = mock_response

    tool_def = {
        "path": "/users/{id}",
        "method": "GET",
        "description": "Get user",
        "openai_schema": {},
        "operationId": "get_user"
    }
    params = {"id": 123, "filter": "active"}

    result = await gateway.execute_tool(tool_def, params)

    # Provjera URL zamjene i query paramsa
    gateway.client.request.assert_called_once()
    args, kwargs = gateway.client.request.call_args
    
    assert args[0] == "GET"
    assert args[1] == "http://api.test/users/123"
    assert kwargs["params"] == {"filter": "active"}
    assert result == {"data": "ok"}

@pytest.mark.asyncio
async def test_execute_tool_http_error():
    gateway = OpenAPIGateway("http://api.test")
    
    gateway.client.request = AsyncMock()
    # Simuliramo 404
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.text = "Not Found"
    
    # raise_for_status mora baciti grešku koju hvatamo
    error = httpx.HTTPStatusError("404", request=MagicMock(), response=mock_response)
    gateway.client.request.side_effect = error

    tool_def = {"path": "/test", "method": "POST", "description": "", "openai_schema": {}, "operationId": "test"}
    
    result = await gateway.execute_tool(tool_def, {})
    
    assert result["error"] is True
    assert result["status"] == 404

@pytest.mark.asyncio
async def test_execute_tool_network_error():
    gateway = OpenAPIGateway("http://api.test")
    gateway.client.request = AsyncMock(side_effect=httpx.RequestError("DNS failure"))

    tool_def = {"path": "/test", "method": "GET", "description": "", "openai_schema": {}, "operationId": "test"}
    result = await gateway.execute_tool(tool_def, {})
    
    assert result["error"] is True
    assert "Nisam uspio kontaktirati sustav" in result["message"]

@pytest.mark.asyncio
async def test_execute_tool_unexpected_error():
    gateway = OpenAPIGateway("http://api.test")
    gateway.client.request = AsyncMock(side_effect=Exception("Boom"))

    tool_def = {"path": "/test", "method": "GET", "description": "", "openai_schema": {}, "operationId": "test"}
    result = await gateway.execute_tool(tool_def, {})
    
    assert result["error"] is True
    assert result["message"] == "Interna greška sustava."

@pytest.mark.asyncio
async def test_gateway_close():
    gateway = OpenAPIGateway("http://api.test")
    gateway.client.aclose = AsyncMock()
    await gateway.close()
    gateway.client.aclose.assert_called_once()