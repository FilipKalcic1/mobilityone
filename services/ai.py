import json
import logging
from openai import AsyncOpenAI
from config import get_settings
from typing import List, Dict, Any

settings = get_settings()
logger = logging.getLogger("ai_engine")

# Klijent na razini modula (za lakše testiranje/patching)
client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)

SYSTEM_PROMPT = """
Ti si AI dispečer za Fleet Management. Analiziraj upit i vrati JSON.
ALATI:
1. "vehicle_status" (zahtijeva parametar 'plate' npr. ZG-1234-AB).
2. "ina_info" (zahtijeva parametar 'card_id').
3. "fallback" (za sve ostalo).

OBAVEZAN IZLAZNI JSON FORMAT:
{"tool": "ime_alata", "confidence": 0.0-1.0, "parameters": {"plate": "...", "card_id": "..."}}
"""

async def analyze_intent(history: List[Dict[str, str]], current_user_text: str) -> Dict[str, Any]:
    """Šalje povijest OpenAI-ju i vraća strukturirani JSON."""
    
    # Defaultni fallback odgovor u slučaju greške
    fallback_result = {"tool": "fallback", "confidence": 1.0, "parameters": {}}

    if not current_user_text:
        return fallback_result

    # Čišćenje povijesti: OpenAI očekuje samo 'role' i 'content'
    clean_history = [
        {"role": msg.get("role"), "content": msg.get("content")} 
        for msg in history if msg.get("content")
    ]

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(clean_history)
    messages.append({"role": "user", "content": current_user_text})

    try:
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content
        data = json.loads(content)
        
        # Sigurno dohvaćanje polja
        tool = data.get("tool", "fallback")
        confidence = data.get("confidence", 0.0)
        params = data.get("parameters", {})

        if confidence < settings.AI_CONFIDENCE_THRESHOLD:
            logger.info("Low confidence, reverting to fallback", confidence=confidence)
            return fallback_result
            
        return {
            "tool": tool,
            "confidence": confidence,
            "parameters": params
        }

    except Exception as e:
        logger.error(f"AI Error: {e}")
        return fallback_result