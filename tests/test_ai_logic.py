import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from services.ai import analyze_intent
from config import get_settings

settings = get_settings()

@pytest.mark.asyncio
async def test_ai_parsing_success():
    # 1. Pripremi JSON string koji simulira odgovor
    mock_content_str = json.dumps({
        "tool": "ina_info",
        "confidence": 0.95,
        "parameters": {"card_id": "12345"}
    })
    
    # 2. Kreiraj strukturu Response objekta
    # Bitno: .message i .content NE SMIJU biti AsyncMock jer su to atributi, ne awaitables!
    mock_message = MagicMock()
    mock_message.content = mock_content_str
    
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    
    # 3. Patchaj create metodu da vrati taj objekt
    with patch("services.ai.client.chat.completions.create", new=AsyncMock(return_value=mock_response)):
        result = await analyze_intent([], "Stanje kartice 12345")
        
        assert result["tool"] == "ina_info"
        assert result["parameters"]["card_id"] == "12345"

@pytest.mark.asyncio
async def test_ai_low_confidence_fallback():
    # Simuliramo nizak confidence
    mock_content_str = json.dumps({
        "tool": "vehicle_status",
        "confidence": 0.10, 
        "parameters": {}
    })
    
    mock_message = MagicMock()
    mock_message.content = mock_content_str
    mock_choice = MagicMock()
    mock_choice.message = mock_message
    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    
    with patch("services.ai.client.chat.completions.create", new=AsyncMock(return_value=mock_response)):
        result = await analyze_intent([], "Neka glupost")
        
        # Zbog niskog confidence-a, očekujemo fallback
        assert result["tool"] == "fallback"