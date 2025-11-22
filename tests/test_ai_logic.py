import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from services.ai import analyze_intent

@pytest.mark.asyncio
async def test_ai_tool_selection():

    mock_tool_call = MagicMock()
    mock_tool_call.function.name = "vehicle_status"
    mock_tool_call.function.arguments = '{"plate": "ZG-123"}'
    
    mock_message = MagicMock()
    mock_message.tool_calls = [mock_tool_call]
    mock_message.content = None
    
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    

    fake_tools = [{"type": "function", "function": {"name": "vehicle_status"}}]
    
    with patch("services.ai.client.chat.completions.create", new=AsyncMock(return_value=mock_response)):
        result = await analyze_intent([], "Gdje je auto?", tools=fake_tools)
        
        assert result["tool"] == "vehicle_status"
        assert result["parameters"]["plate"] == "ZG-123"
        assert result["response_text"] is None

@pytest.mark.asyncio
async def test_ai_conversation_no_tool():

    mock_message = MagicMock()
    mock_message.tool_calls = None
    mock_message.content = "Pozdrav! Kako vam mogu pomoći?"
    
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    
    with patch("services.ai.client.chat.completions.create", new=AsyncMock(return_value=mock_response)):
        result = await analyze_intent([], "Bok")
        
        assert result["tool"] is None
        assert result["response_text"] == "Pozdrav! Kako vam mogu pomoći?"