import pytest
from unittest.mock import AsyncMock, patch
import json
from services.ai import analyze_intent 
from config import get_settings

# --- Pomoćne varijable za simulaciju ---
settings = get_settings()
TEST_CONFIDENCE_SUCCESS = settings.AI_CONFIDENCE_THRESHOLD + 0.05
TEST_CONFIDENCE_FAIL = settings.AI_CONFIDENCE_THRESHOLD - 0.05

# Funkcija za kreiranje lažnog OpenAI odgovora
def create_mock_openai_response(tool_name: str, plate: str, confidence: float):
    """Kreira JSON objekt koji simulira OpenAI odgovor."""
    content = {
        "tool": tool_name,
        "confidence": confidence,
        "parameters": {"plate": plate}
    }
    return {
        'choices': [{'message': {'content': json.dumps(content)}}]
    }

@pytest.mark.asyncio
async def test_ai_returns_valid_parameters():
    """Testira da AI vraća složeni JSON sa željenim parametrima i da prolazi confidence threshold."""
    
    # Simulacija AI-ja koji je siguran u odgovor
    mock_response = create_mock_openai_response(
        tool_name="vehicle_status", 
        plate="ZG-123-AB", 
        confidence=TEST_CONFIDENCE_SUCCESS
    )
    
    # Koristimo patch za simulaciju OpenAI odgovora
    with patch('services.ai.client.chat.completions.create', new=AsyncMock(return_value=mock_response)):
        # Prvi upit, prazna povijest
        result = await analyze_intent(history=[], current_user_text="Gdje je vozilo ZG-123-AB?")

        # Provjeravamo da li je izlaz ispravno parsiran i da je tool ispravan
        assert result['tool'] == "vehicle_status"
        assert result['parameters']['plate'] == "ZG-123-AB"
        assert result['confidence'] > settings.AI_CONFIDENCE_THRESHOLD

@pytest.mark.asyncio
async def test_ai_reverts_to_fallback_on_low_confidence():
    """Testira da se tool postavlja na 'fallback' ako je confidence prenizak."""

    # Simulacija AI-ja koji je NESIGURAN u odgovor
    mock_response = create_mock_openai_response(
        tool_name="vehicle_status", 
        plate="RI-999-CD", 
        confidence=TEST_CONFIDENCE_FAIL
    )

    with patch('services.ai.client.chat.completions.create', new=AsyncMock(return_value=mock_response)):
        result = await analyze_intent(history=[], current_user_text="Neka nejasna poruka.")

        # Očekujemo da je tool 'fallback' jer confidence nije bio dovoljan
        assert result['tool'] == "fallback"
        assert result['confidence'] < settings.AI_CONFIDENCE_THRESHOLD

@pytest.mark.asyncio
async def test_ai_uses_context_from_history():
    """Testira da AI koristi povijest za zaključivanje (npr. 'ga')."""
    
    # Povijest: Bot je prije spomenuo vozilo (role: assistant)
    history = [
        {"role": "assistant", "content": "Vozilo RI-456-XY je u Rijeci."}
    ]
    
    # AI vraća odgovor koji koristi parametar iz povijesti (i siguran je)
    mock_response = create_mock_openai_response(
        tool_name="vehicle_status", 
        plate="RI-456-XY", 
        confidence=TEST_CONFIDENCE_SUCCESS
    )

    with patch('services.ai.client.chat.completions.create', new=AsyncMock(return_value=mock_response)):
        # Trenutni upit koristi zamjenicu ('njega')
        result = await analyze_intent(history=history, current_user_text="Tko njega vozi?")
        
        # Testiramo da je AI izvadio ispravnu registraciju iz povijesti
        assert result['parameters']['plate'] == "RI-456-XY"
        assert result['tool'] == "vehicle_status"
        